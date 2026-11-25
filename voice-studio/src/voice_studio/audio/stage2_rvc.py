# -*- coding: utf-8 -*-
"""Stage 2 Hot-Cached Applio RVC v2 Engine (Gawr Gura default + arbitrary custom RVC models)."""

from __future__ import annotations

import hashlib
import os
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from voice_studio.audio.mastering import apply_clean_speech_mastering
from voice_studio.audio.rvc_assets import (
    configure_torch_runtime,
    default_rvc_dirs,
    ensure_applio_repo,
    ensure_rvc_model_assets,
)
from voice_studio.audio.stage1_tts import read_wav_duration_safe, synthesize_with_edge_tts
from voice_studio.config.models import VoiceProfile
from voice_studio.config.voices import build_voice_profile, profile_to_rvc_dict

_HOT_SESSION: Optional["_HotRvcSession"] = None


def condition_rvc_input_tensor(
    audio: Any,
    sr: int = 16000,
    gate_threshold: float = 0.005,
    target_rms: float = 0.085,
    peak_ceiling: float = 0.72,
    rms_ms: float = 25.0,
    ramp_ms: float = 35.0,
) -> Any:
    """Applies DC removal, RMS-envelope Noise Gate, and speech RMS/peak normalization before RVC."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return arr
    arr = arr - float(np.mean(arr))

    win = max(1, int(round(sr * (max(1.0, float(rms_ms)) / 1000.0))))
    if arr.size >= win:
        sq = np.convolve(arr * arr, np.ones(win, dtype=np.float32) / float(win), mode="same")
        rms_env = np.sqrt(np.maximum(0.0, sq))
    else:
        rms_env = np.abs(arr)

    raw_mask = (rms_env >= float(gate_threshold)).astype(np.float32)
    smooth_w = max(1, int(round(sr * (max(1.0, float(ramp_ms)) / 1000.0))))
    if smooth_w > 1 and arr.size >= smooth_w:
        ramp = np.hanning(smooth_w + 2)[1:-1].astype(np.float32)
        ramp /= max(1e-6, float(np.sum(ramp)))
        gate = np.clip(np.convolve(raw_mask, ramp, mode="same") * 1.15, 0.0, 1.0)
    else:
        gate = raw_mask
    gated = np.where(gate < 0.005, 0.0, arr * gate).astype(np.float32)

    active = gated[np.abs(gated) > float(gate_threshold)]
    if active.size > 0:
        cur_rms = float(np.sqrt(np.mean(active * active)))
        if cur_rms > 1e-6:
            gated = gated * (float(target_rms) / cur_rms)
    peak = float(np.max(np.abs(gated)))
    if peak > float(peak_ceiling) and peak > 1e-6:
        gated = gated * (float(peak_ceiling) / peak)
    return gated.astype(np.float32)


def smooth_voiced_f0(f0_arr: Any) -> Any:
    """Applies voiced-only 3-frame median + Hann smoothing to remove RMVPE pitch warble without flattening tones."""
    import numpy as np

    f0 = np.asarray(f0_arr, dtype=np.float64).copy()
    n = f0.size
    if n < 5:
        return f0
    idx = 0
    while idx < n:
        if f0[idx] <= 0.0:
            idx += 1
            continue
        end = idx
        while end < n and f0[end] > 0.0:
            end += 1
        seg = f0[idx:end]
        if seg.size >= 5:
            med = seg.copy()
            for i in range(1, seg.size - 1):
                med[i] = float(np.median(seg[i - 1 : i + 2]))
            sm = med.copy()
            sm[1:-1] = 0.25 * med[:-2] + 0.50 * med[1:-1] + 0.25 * med[2:]
            f0[idx:end] = sm
        idx = end
    return f0


def _install_f0_stabilizer(vc_pipe: Any, enabled: bool = True) -> None:
    """Wraps Applio pipeline.get_f0 once to stabilize RMVPE micro-tremolo when enabled."""
    if vc_pipe is None:
        return
    vc_pipe._f0_stabilizer_enabled = bool(enabled)
    if getattr(vc_pipe, "_f0_stabilized", False):
        return
    orig_get_f0 = vc_pipe.get_f0

    def _stabilized_get_f0(*args: Any, **kwargs: Any):
        import numpy as np

        res = orig_get_f0(*args, **kwargs)
        if not getattr(vc_pipe, "_f0_stabilizer_enabled", True):
            return res
        if isinstance(res, tuple) and len(res) == 2:
            _, f0_raw = res
            f0_sm = smooth_voiced_f0(f0_raw)
            f0_mel_min = 1127.0 * np.log(1.0 + 50.0 / 700.0)
            f0_mel_max = 1127.0 * np.log(1.0 + 1100.0 / 700.0)
            f0_mel = 1127.0 * np.log(1.0 + f0_sm / 700.0)
            f0_mel[f0_mel > 0] = (f0_mel[f0_mel > 0] - f0_mel_min) * 254.0 / (f0_mel_max - f0_mel_min) + 1.0
            f0_mel[f0_mel <= 1] = 1.0
            f0_mel[f0_mel > 255] = 255.0
            f0_coarse = np.rint(f0_mel).astype(int)
            return f0_coarse, f0_sm
        return res

    vc_pipe.get_f0 = _stabilized_get_f0
    vc_pipe._f0_stabilized = True


class _HotRvcSession:
    """Keeps Applio ContentVec, RMVPE, and RVC net_g pinned in memory across items."""

    def __init__(self, applio_dir: str, rvc_fp32: bool = False) -> None:
        self.applio_dir = ensure_applio_repo(applio_dir)
        self.rvc_fp32 = rvc_fp32
        self.loaded_model_path: Optional[str] = None
        self.fast_path_ok: bool = True

        orig_cwd = os.getcwd()
        os.chdir(self.applio_dir)
        try:
            if self.applio_dir not in sys.path:
                sys.path.insert(0, self.applio_dir)
            from rvc.infer.infer import VoiceConverter
            from rvc.lib.utils import load_audio_infer

            self._vc = VoiceConverter()
            self._load_audio_infer = load_audio_infer
            self.hw_info = configure_torch_runtime(getattr(self._vc, "config", None), rvc_fp32=rvc_fp32)
        finally:
            os.chdir(orig_cwd)

    def _ensure_models_loaded(self, model_path: str, sid: int, embedder_model: str, f0_stabilizer: bool = True) -> None:
        vc = self._vc
        if self.loaded_model_path != model_path or vc.net_g is None or vc.vc is None:
            vc.get_vc(model_path, sid, None, "auto")
            self.loaded_model_path = model_path
        _install_f0_stabilizer(vc.vc, enabled=f0_stabilizer)
        if vc.hubert_model is None or vc.last_embedder_model != embedder_model:
            vc.load_hubert(embedder_model, None)
            vc.last_embedder_model = embedder_model

    def convert_file(
        self,
        src_wav: str,
        out_wav: str,
        model_path: str,
        index_path: str,
        pitch: int,
        f0_method: str,
        index_rate: float,
        volume_envelope: float,
        protect: float,
        hop_length: int,
        sid: int = 0,
        embedder_model: str = "contentvec",
        intermediate_float: bool = False,
        input_gate_threshold: float = 0.008,
        input_target_rms: float = 0.140,
        input_peak_ceiling: float = 0.78,
        output_peak_ceiling: float = 0.89,
        f0_stabilizer: bool = True,
        pre_gate_rms_ms: float = 25.0,
        pre_gate_ramp_ms: float = 35.0,
    ) -> None:
        abs_src = os.path.abspath(src_wav)
        abs_out = os.path.abspath(out_wav)
        abs_model = os.path.abspath(model_path)
        clean_idx_raw = (index_path or "").strip().strip('"').strip("\n").strip()
        abs_idx = os.path.abspath(clean_idx_raw) if clean_idx_raw else ""
        os.makedirs(os.path.dirname(abs_out), exist_ok=True)
        orig_cwd = os.getcwd()
        os.chdir(self.applio_dir)
        try:
            if self.fast_path_ok:
                try:
                    import numpy as np
                    import soundfile as sf

                    self._ensure_models_loaded(abs_model, sid, embedder_model, f0_stabilizer=f0_stabilizer)
                    vc = self._vc
                    audio = condition_rvc_input_tensor(
                        self._load_audio_infer(abs_src, 16000),
                        sr=16000,
                        gate_threshold=float(input_gate_threshold),
                        target_rms=float(input_target_rms),
                        peak_ceiling=float(input_peak_ceiling),
                        rms_ms=float(pre_gate_rms_ms),
                        ramp_ms=float(pre_gate_ramp_ms),
                    )
                    clean_idx = abs_idx.replace("trained", "added")
                    audio_opt = vc.vc.pipeline(
                        model=vc.hubert_model,
                        net_g=vc.net_g,
                        sid=sid,
                        audio=audio,
                        pitch=pitch,
                        f0_method=f0_method,
                        file_index=clean_idx,
                        index_rate=index_rate,
                        pitch_guidance=vc.use_f0,
                        volume_envelope=volume_envelope,
                        version=vc.version,
                        protect=protect,
                        f0_autotune=False,
                        f0_autotune_strength=1.0,
                        proposed_pitch=False,
                        proposed_pitch_threshold=155.0,
                    )
                    out_arr = np.asarray(audio_opt, dtype=np.float32)
                    max_abs = float(np.max(np.abs(out_arr))) if out_arr.size else 0.0
                    out_ceil = max(0.5, min(0.95, float(output_peak_ceiling)))
                    if max_abs > out_ceil and max_abs > 1e-6:
                        out_arr = out_arr * (out_ceil / max_abs)
                    sf.write(abs_out, out_arr, vc.tgt_sr, format="WAV", subtype="FLOAT" if intermediate_float else "PCM_24")
                    return
                except Exception as exc:
                    print(f"[RVC] Fast persistent path fallback to convert_audio ({exc})")
                    self.fast_path_ok = False

            self._vc.convert_audio(
                audio_input_path=abs_src,
                audio_output_path=abs_out,
                model_path=abs_model,
                index_path=abs_idx,
                pitch=pitch,
                f0_method=f0_method,
                index_rate=index_rate,
                volume_envelope=volume_envelope,
                protect=protect,
                hop_length=hop_length,
                split_audio=False,
                f0_autotune=False,
                f0_autotune_strength=1.0,
                clean_audio=True,
                clean_strength=0.5,
                export_format="WAV",
                embedder_model=embedder_model,
                resample_sr=0,
                sid=sid,
            )
            self.loaded_model_path = None
        finally:
            os.chdir(orig_cwd)


def get_hot_rvc_session(applio_dir: str, rvc_fp32: bool = False) -> _HotRvcSession:
    """Returns a reusable GPU/CPU RVC converter bound to applio_dir."""
    global _HOT_SESSION
    abs_applio = os.path.abspath(applio_dir)
    if _HOT_SESSION is None or os.path.abspath(_HOT_SESSION.applio_dir) != abs_applio or _HOT_SESSION.rvc_fp32 != rvc_fp32:
        _HOT_SESSION = _HotRvcSession(abs_applio, rvc_fp32=rvc_fp32)
    return _HOT_SESSION


def convert_wav_with_rvc(
    src_wav: str,
    out_wav: str,
    voice_id: str = "gawr_gura",
    model_dir: Optional[str] = None,
    applio_dir: Optional[str] = None,
    pitch_shift: Optional[int] = None,
    index_rate: Optional[float] = None,
    protect: Optional[float] = None,
    volume_envelope: Optional[float] = None,
    f0_method: Optional[str] = None,
    hop_length: Optional[int] = None,
    rvc_cfg: Optional[Dict[str, Any]] = None,
    assets_root: str = "",
    base_dir: Optional[Path] = None,
) -> str:
    """Runs RVC v2 conversion on an input WAV using persistent model weights."""
    preset = rvc_cfg or profile_to_rvc_dict(build_voice_profile({"profile": voice_id}))
    default_model_dir, default_applio_dir = default_rvc_dirs(
        str(preset.get("default_subdir") or voice_id), assets_root=assets_root
    )
    pth_path, index_path = ensure_rvc_model_assets(
        voice_id,
        model_dir=model_dir or default_model_dir,
        rvc_cfg=preset,
        assets_root=assets_root,
        base_dir=base_dir,
    )
    session = get_hot_rvc_session(applio_dir or default_applio_dir, rvc_fp32=bool(preset.get("rvc_fp32", False)))
    session.convert_file(
        src_wav=src_wav,
        out_wav=out_wav,
        model_path=pth_path,
        index_path=index_path,
        pitch=int(preset["pitch"] if pitch_shift is None else pitch_shift),
        f0_method=str(preset["f0_method"] if f0_method is None else f0_method),
        index_rate=float(preset["index_rate"] if index_rate is None else index_rate),
        volume_envelope=float(preset["volume_envelope"] if volume_envelope is None else volume_envelope),
        protect=float(preset["protect"] if protect is None else protect),
        hop_length=int(preset["hop_length"] if hop_length is None else hop_length),
        intermediate_float=bool(preset.get("intermediate_float", False)),
        input_gate_threshold=float(preset.get("input_gate_threshold", 0.008)),
        input_target_rms=float(preset.get("input_target_rms", 0.140)),
        input_peak_ceiling=float(preset.get("input_peak_ceiling", 0.78)),
        output_peak_ceiling=float(preset.get("output_peak_ceiling", 0.89)),
        f0_stabilizer=bool(preset.get("f0_stabilizer", True)),
        pre_gate_rms_ms=float(preset.get("pre_gate_rms_ms", 25.0)),
        pre_gate_ramp_ms=float(preset.get("pre_gate_ramp_ms", 35.0)),
    )
    return out_wav


def _master_with_preset(src_wav: str, out_wav: str, preset: Dict[str, Any]) -> float:
    return apply_clean_speech_mastering(
        src_wav,
        out_wav,
        sample_rate=int(preset["sample_rate"]),
        target_lufs=float(preset["target_lufs"]),
        denoise=bool(preset["denoise"]),
        noise_gate=bool(preset["noise_gate"]),
        noise_gate_threshold=float(preset.get("noise_gate_threshold", 0.007)),
        bit_depth=int(preset.get("bit_depth", 16)),
        resampler_precision=int(preset.get("resampler_precision", 20)),
        declick=bool(preset.get("declick", False)),
        denoise_nf_db=int(preset.get("denoise_nf_db", -48)),
        denoise_nr_db=int(preset.get("denoise_nr_db", 8)),
        highpass_hz=int(preset.get("highpass_hz", 105)),
        lowpass_hz=int(preset.get("lowpass_hz", 0)),
        eq_preset=str(preset.get("eq_preset", "anime_female")),
        eq_bands=preset.get("eq_bands") or None,
        comp_threshold_db=float(preset.get("comp_threshold_db", -16.0)),
        comp_ratio=float(preset.get("comp_ratio", 1.5)),
        gate_ratio=float(preset.get("gate_ratio", 8.0)),
        gate_release_ms=float(preset.get("gate_release_ms", 110.0)),
        limiter_db=float(preset.get("limiter_db", -1.8)),
    )


def _stage3_master_and_cleanup(
    raw_vc_wav: str,
    stage1_wav: str,
    out_wav: str,
    preset: Dict[str, Any],
    keep_stage1: bool = False,
) -> float:
    try:
        return _master_with_preset(raw_vc_wav, out_wav, preset)
    finally:
        if raw_vc_wav and os.path.exists(raw_vc_wav):
            os.remove(raw_vc_wav)
        if not keep_stage1 and stage1_wav and os.path.exists(stage1_wav):
            os.remove(stage1_wav)


def synthesize_edge_rvc_item(
    text: str,
    out_wav: str,
    voice_id: str = "gawr_gura",
    model_dir: Optional[str] = None,
    applio_dir: Optional[str] = None,
    pitch_shift: Optional[int] = None,
    index_rate: Optional[float] = None,
    protect: Optional[float] = None,
    volume_envelope: Optional[float] = None,
    edge_voice: Optional[str] = None,
    edge_rate: Optional[str] = None,
    edge_pitch: Optional[str] = None,
    rvc_cfg: Optional[Dict[str, Any]] = None,
) -> float:
    """Runs Stage 1 TTS -> Stage 2 RVC v2 -> Stage 3 Noise Gate & Mastering for one item."""
    preset = rvc_cfg or profile_to_rvc_dict(build_voice_profile({"profile": voice_id}))
    out_dir = os.path.dirname(os.path.abspath(out_wav))
    os.makedirs(out_dir, exist_ok=True)
    stem = Path(out_wav).stem
    stage1_wav = os.path.join(out_dir, f".tmp_{stem}_stage1.wav")
    raw_vc_wav = os.path.join(out_dir, f".tmp_{stem}_raw.wav")

    try:
        synthesize_with_edge_tts(
            text=text,
            output_wav=stage1_wav,
            voice=edge_voice or str(preset["edge_voice"]),
            rate=edge_rate or str(preset["edge_rate"]),
            pitch=edge_pitch or str(preset["edge_pitch"]),
            sample_rate=int(preset["sample_rate"]),
            target_lufs=float(preset["target_lufs"]),
            noise_gate_threshold=float(preset.get("noise_gate_threshold", 0.006)),
        )
        convert_wav_with_rvc(
            stage1_wav,
            raw_vc_wav,
            voice_id=voice_id,
            model_dir=model_dir,
            applio_dir=applio_dir,
            pitch_shift=pitch_shift,
            index_rate=index_rate,
            protect=protect,
            volume_envelope=volume_envelope,
            rvc_cfg=preset,
        )
        return _master_with_preset(raw_vc_wav, out_wav, preset)
    finally:
        for tmp_f in (stage1_wav, raw_vc_wav):
            if tmp_f and os.path.exists(tmp_f):
                os.remove(tmp_f)


def convert_stage1_to_rvc_batch(
    tasks: List[Dict[str, str]],
    profile: VoiceProfile,
    assets_root: str = "",
    cpu_workers: Optional[int] = None,
    resume: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
    base_dir: Optional[Path] = None,
) -> Dict[str, float]:
    """Converts existing Stage 1 WAVs through configured VoiceProfile RVC + parallel FFmpeg mastering."""
    rvc_cfg = profile_to_rvc_dict(profile)
    durations: Dict[str, float] = {}
    pending: List[Tuple[str, str, str]] = []

    for task in tasks:
        s1_wav = task["stage1_wav"]
        out_wav = task["out_wav"]
        os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
        existing_dur = read_wav_duration_safe(out_wav) if resume else None
        if existing_dur is not None:
            durations[out_wav] = existing_dur
            continue
        digest = hashlib.sha1(out_wav.encode("utf-8")).hexdigest()[:10]
        raw_vc_wav = os.path.join(os.path.dirname(os.path.abspath(out_wav)), f".tmp_{Path(out_wav).stem}_{digest}_raw.wav")
        pending.append((s1_wav, raw_vc_wav, out_wav))

    total = len(tasks)
    completed = len(durations)
    if on_progress and total > 0:
        on_progress(completed, total)
    if not pending:
        return durations

    n_cpu = cpu_workers or min(8, max(2, os.cpu_count() or 4))
    raw_rvc_cfg = {**rvc_cfg, "intermediate_float": True}
    master_futs: List[Tuple[str, Future[float]]] = []
    with ThreadPoolExecutor(max_workers=n_cpu) as master_pool:
        for s1_wav, raw_wav, out_wav in pending:
            try:
                convert_wav_with_rvc(
                    s1_wav,
                    raw_wav,
                    voice_id=profile.voice_id,
                    rvc_cfg=raw_rvc_cfg,
                    assets_root=assets_root,
                    base_dir=base_dir,
                )
            except Exception:
                if os.path.exists(raw_wav):
                    os.remove(raw_wav)
                raise
            fut = master_pool.submit(_stage3_master_and_cleanup, raw_wav, s1_wav, out_wav, rvc_cfg, True)
            master_futs.append((out_wav, fut))
        for out_wav, fut in master_futs:
            durations[out_wav] = fut.result()
            completed += 1
            if on_progress:
                on_progress(completed, total)
    return durations

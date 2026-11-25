# -*- coding: utf-8 -*-
"""Automatic Speech Recognition (ASR) module supporting faster-whisper, openai-whisper, and transformers Whisper."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List

from voice_studio.core.state import save_json_atomic


def _format_srt_ts(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000.0)))
    hrs = ms_total // 3_600_000
    mins = (ms_total % 3_600_000) // 60_000
    secs = (ms_total % 60_000) // 1000
    ms = ms_total % 1000
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def _render_srt(segments: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for idx, seg in enumerate(segments, start=1):
        start_s = _format_srt_ts(float(seg.get("start", 0.0)))
        end_s = _format_srt_ts(float(seg.get("end", 0.0)))
        txt = str(seg.get("text", "")).strip()
        lines.extend([str(idx), f"{start_s} --> {end_s}", txt, ""])
    return "\n".join(lines).strip() + "\n"


def _extract_asr_wav16k(media_path: Path, dst_wav: Path) -> float:
    """Converts input audio or video file to 16kHz mono PCM WAV and returns duration in seconds."""
    if not shutil.which("ffmpeg"):
        if media_path.suffix.lower() == ".wav":
            shutil.copy2(media_path, dst_wav)
            with wave.open(str(dst_wav), "rb") as wf:
                return wf.getnframes() / float(max(1, wf.getframerate()))
        raise RuntimeError("ffmpeg binary is required to decode audio/video for transcription.")

    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(media_path), "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(dst_wav)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed decoding {media_path}: {proc.stderr.strip()}")
    with wave.open(str(dst_wav), "rb") as wf:
        frames, sr = wf.getnframes(), wf.getframerate()
        if frames <= 0 or sr <= 0:
            raise RuntimeError(f"Extracted audio is empty: {media_path}")
        return frames / float(sr)


def _pick_asr_backend(requested: str) -> str:
    req = (requested or "auto").strip().lower()
    if req != "auto":
        if importlib.util.find_spec("faster_whisper" if req == "faster_whisper" else ("whisper" if req == "openai_whisper" else req)) is not None:
            return req
        print(f"[transcribe] Requested backend '{req}' unavailable locally; falling back via 'auto'.")
    for mod_name, key in (("faster_whisper", "faster_whisper"), ("whisper", "openai_whisper"), ("transformers", "transformers")):
        if importlib.util.find_spec(mod_name) is not None:
            return key
    raise RuntimeError("No ASR library found. Install faster-whisper, openai-whisper, or transformers+torch.")


def _select_torch_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            cc = torch.cuda.get_device_capability(0)
            if cc >= (7, 0):
                return "cuda"
            _ = float((torch.ones(1, device="cuda") + 1.0)[0].item())
            return "cuda"
    except Exception as exc:
        print(f"[transcribe] CUDA probe fallback to CPU: {exc}")
    return "cpu"


def _load_wav16k_float32(wav_path: Path) -> Any:
    """Loads 16kHz mono WAV as float32 numpy array, bypassing PyAV version breaking changes in faster-whisper."""
    import numpy as np

    with wave.open(str(wav_path), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _preload_nvidia_cuda_libs() -> None:
    """Preloads pip-bundled nvidia-cublas/cudnn shared libraries so CTranslate2 finds libcublas.so.12."""
    import ctypes
    import site
    import sys

    search_roots = list(dict.fromkeys([*sys.path, *(site.getsitepackages() if hasattr(site, "getsitepackages") else [])]))
    for mod_dir in ("nvidia/cublas/lib", "nvidia/cudnn/lib", "torch/lib"):
        for base in search_roots:
            cand_dir = Path(base) / mod_dir
            if cand_dir.is_dir():
                so_files = sorted(cand_dir.glob("*.so*"), key=lambda p: (0 if "Lt" in p.name else 1, p.name))
                for so_file in so_files:
                    try:
                        ctypes.CDLL(str(so_file), mode=ctypes.RTLD_GLOBAL)
                    except OSError as exc:
                        _ = exc


def _run_faster_whisper(
    wav_path: Path, model: str, language: str, duration_sec: float, beam_size: int = 5, vad_filter: bool = True
) -> List[Dict[str, Any]]:
    _preload_nvidia_cuda_libs()
    from faster_whisper import WhisperModel

    dev = _select_torch_device()
    ctype = "float16" if dev == "cuda" else "float32"
    clean_model = model.replace("openai/whisper-", "")
    try:
        wm = WhisperModel(clean_model, device=dev, compute_type=ctype)
        kw: Dict[str, Any] = {"beam_size": max(1, int(beam_size)), "vad_filter": bool(vad_filter)}
        if language:
            kw["language"] = language
        audio_arr = _load_wav16k_float32(wav_path)
        segs, _ = wm.transcribe(audio_arr, **kw)
        out = [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()} for s in segs if s.text.strip()]
        return out or [{"start": 0.0, "end": round(duration_sec, 3), "text": ""}]
    except RuntimeError as exc:
        if dev == "cuda" and ("libcublas" in str(exc) or "libcudnn" in str(exc) or "cuda" in str(exc).lower()):
            if importlib.util.find_spec("transformers") is not None and importlib.util.find_spec("torch") is not None:
                print(f"[transcribe] faster_whisper CUDA lib missing ({exc}); falling back to transformers Whisper GPU.")
                return _run_transformers_whisper(wav_path, model, language, 30, duration_sec, beam_size=beam_size)
            print(f"[transcribe] faster_whisper CUDA lib missing ({exc}); falling back to CPU float32.")
            wm_cpu = WhisperModel(clean_model, device="cpu", compute_type="float32")
            kw_cpu: Dict[str, Any] = {"beam_size": max(1, int(beam_size)), "vad_filter": bool(vad_filter)}
            if language:
                kw_cpu["language"] = language
            segs_cpu, _ = wm_cpu.transcribe(_load_wav16k_float32(wav_path), **kw_cpu)
            out_cpu = [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()} for s in segs_cpu if s.text.strip()]
            return out_cpu or [{"start": 0.0, "end": round(duration_sec, 3), "text": ""}]
        raise


def _run_openai_whisper(wav_path: Path, model: str, language: str, duration_sec: float, beam_size: int = 5) -> List[Dict[str, Any]]:
    import whisper

    dev = _select_torch_device()
    wm = whisper.load_model(model.replace("openai/whisper-", ""), device=dev)
    res = wm.transcribe(str(wav_path), language=language or None, beam_size=max(1, int(beam_size)))
    out = [
        {"start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3), "text": str(s["text"]).strip()}
        for s in (res.get("segments") or [])
        if str(s.get("text", "")).strip()
    ]
    if not out and str(res.get("text", "")).strip():
        out = [{"start": 0.0, "end": round(duration_sec, 3), "text": str(res["text"]).strip()}]
    return out


def _run_transformers_whisper(
    wav_path: Path, model: str, language: str, chunk_length_s: int, duration_sec: float, beam_size: int = 1
) -> List[Dict[str, Any]]:
    import torch
    from transformers import pipeline

    dev_str = _select_torch_device()
    device_arg: Any = 0 if dev_str == "cuda" else -1
    dtype = torch.float16 if dev_str == "cuda" else torch.float32
    asr = pipeline("automatic-speech-recognition", model=model, device=device_arg, dtype=dtype)
    gen_kwargs: Dict[str, Any] = {}
    if language:
        gen_kwargs["language"] = language
        gen_kwargs["task"] = "transcribe"
    if beam_size > 1:
        gen_kwargs["num_beams"] = int(beam_size)
    pipe_kwargs: Dict[str, Any] = {"return_timestamps": True}
    if duration_sec > 29.0 and chunk_length_s > 0:
        pipe_kwargs["chunk_length_s"] = chunk_length_s
    if gen_kwargs:
        pipe_kwargs["generate_kwargs"] = gen_kwargs
    res = asr(str(wav_path), **pipe_kwargs)
    chunks = res.get("chunks") or []
    out: List[Dict[str, Any]] = []
    for ch in chunks:
        ts = ch.get("timestamp") or (0.0, duration_sec)
        t0 = float(ts[0]) if ts[0] is not None else 0.0
        t1 = float(ts[1]) if len(ts) > 1 and ts[1] is not None else duration_sec
        txt = str(ch.get("text") or "").strip()
        if txt:
            out.append({"start": round(t0, 3), "end": round(max(t0, t1), 3), "text": txt})
    if not out and str(res.get("text", "")).strip():
        out = [{"start": 0.0, "end": round(duration_sec, 3), "text": str(res["text"]).strip()}]
    return out


def transcribe_audio(
    media_path: Path,
    output_dir: Path,
    backend: str = "auto",
    model: str = "openai/whisper-small",
    language: str = "th",
    chunk_length_s: int = 30,
    beam_size: int = 1,
    vad_filter: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Transcribes audio/video into transcript.txt, transcript.srt, transcript.json, and script_from_audio.json."""
    src = Path(media_path)
    if not src.is_file():
        raise FileNotFoundError(f"Media input not found for transcription: {src}")
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="asr_16k_") as tmp_dir:
        wav16 = Path(tmp_dir) / "audio_16k.wav"
        dur_sec = _extract_asr_wav16k(src, wav16)
        if dry_run:
            used_backend = "dry_run"
            segments = [{"start": 0.0, "end": round(dur_sec, 3), "text": f"[DRY-RUN TRANSCRIPT from {src.name} ({dur_sec:.2f}s)]"}]
        else:
            used_backend = _pick_asr_backend(backend)
            if used_backend == "faster_whisper":
                segments = _run_faster_whisper(wav16, model, language, dur_sec, beam_size=beam_size, vad_filter=vad_filter)
            elif used_backend == "openai_whisper":
                segments = _run_openai_whisper(wav16, model, language, dur_sec, beam_size=beam_size)
            elif used_backend == "transformers":
                segments = _run_transformers_whisper(wav16, model, language, chunk_length_s, dur_sec, beam_size=beam_size)
            else:
                raise ValueError(f"Unsupported ASR backend {used_backend!r}")

    full_text = "\n".join(seg["text"] for seg in segments if seg.get("text")).strip()
    txt_path = output_dir / "transcript.txt"
    srt_path = output_dir / "transcript.srt"
    json_path = output_dir / "transcript.json"
    script_path = output_dir / "script_from_audio.json"

    txt_path.write_text(full_text + "\n", encoding="utf-8")
    srt_path.write_text(_render_srt(segments), encoding="utf-8")
    payload = {
        "source": src.name,
        "duration_sec": round(dur_sec, 3),
        "backend": used_backend,
        "model": model,
        "language": language,
        "text": full_text,
        "segments": segments,
    }
    save_json_atomic(json_path, payload)

    script_doc = {
        "title": f"Transcribed: {src.stem}",
        "slides": [
            {
                "slide_no": idx,
                "title": f"Segment {idx} ({seg['start']:.1f}s - {seg['end']:.1f}s)",
                "duration_est_sec": round(max(0.5, float(seg["end"]) - float(seg["start"])), 3),
                "items": [seg["text"]],
            }
            for idx, seg in enumerate(segments, start=1)
            if seg.get("text")
        ],
    }
    save_json_atomic(script_path, script_doc)
    return {
        "backend": used_backend,
        "model": model,
        "duration_sec": round(dur_sec, 3),
        "segment_count": len(segments),
        "text": full_text,
        "txt_path": str(txt_path),
        "srt_path": str(srt_path),
        "json_path": str(json_path),
        "script_path": str(script_path),
    }

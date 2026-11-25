# -*- coding: utf-8 -*-
"""Modular Stage 1 TTS and Stage 2 RVC Conversion / Mastering step executors."""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from voice_studio import PROJECT_ROOT
from voice_studio.audio.mastering import apply_clean_speech_mastering, concatenate_wav_items
from voice_studio.audio.stage1_tts import read_wav_duration_safe, synthesize_stage1_batch
from voice_studio.audio.stage2_rvc import convert_stage1_to_rvc_batch
from voice_studio.config.models import VoiceProfile
from voice_studio.core.state import save_json_atomic


def run_stage1_tts(
    prepared_slides: List[Dict[str, Any]],
    job_dir: Path,
    profile: VoiceProfile,
    stage1_workers: int = 4,
    dry_run: bool = False,
    resume: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    """Generates Stage 1 base WAV files per item inside job_dir/audio_stage1."""
    s1_dir = job_dir / "audio_stage1"
    s1_dir.mkdir(parents=True, exist_ok=True)
    tasks: List[Dict[str, str]] = []
    for sl in prepared_slides:
        s_no = int(sl["slide_no"])
        tts_items = list(sl.get("tts_items") or sl.get("item_scripts") or [])
        phon_hint = list(sl.get("item_scripts") or tts_items)
        for j, txt in enumerate(tts_items):
            tasks.append({
                "text": str(txt),
                "duration_hint_text": str(phon_hint[j] if j < len(phon_hint) else txt),
                "out_wav": str(s1_dir / f"slide_{s_no:02d}_item_{j:02d}.wav"),
            })

    s1_cfg = profile.stage1
    m_cfg = profile.mastering
    warnings_log: List[Dict[str, str]] = []
    if str(s1_cfg.backend or "edge_tts").lower() != "edge_tts":
        from voice_studio.audio.stage1_hf_tts import synthesize_stage1_hf_batch

        durations = synthesize_stage1_hf_batch(
            tasks,
            backend=str(s1_cfg.backend),
            model_repo=str(getattr(s1_cfg, "hf_model_repo", "kunato/wayu-kokoro-thai-v1")),
            speaker=str(getattr(s1_cfg, "hf_speaker", "f_mid_warm")),
            speed=float(getattr(s1_cfg, "hf_speed", 0.96)),
            sample_rate=int(s1_cfg.sample_rate),
            target_lufs=float(m_cfg.target_lufs),
            noise_gate_threshold=float(m_cfg.noise_gate_threshold),
            dry_run=dry_run,
            resume=resume,
            on_progress=on_progress,
        )
    else:
        durations = synthesize_stage1_batch(
            tasks,
            voice=s1_cfg.voice,
            rate=s1_cfg.rate,
            pitch=s1_cfg.pitch,
            sample_rate=int(s1_cfg.sample_rate),
            workers=stage1_workers,
            pacing_sec=float(s1_cfg.pacing_sec),
            target_lufs=float(m_cfg.target_lufs),
            noise_gate_threshold=float(m_cfg.noise_gate_threshold),
            dry_run=dry_run,
            resume=resume,
            on_progress=on_progress,
            fallback_warnings=warnings_log,
        )
    manifest_path = job_dir / "work" / "stage1_manifest.json"
    save_json_atomic(manifest_path, {"voice_id": profile.voice_id, "items": durations, "fallback_warnings": warnings_log})
    return {
        "stage1_dir": str(s1_dir),
        "item_count": len(durations),
        "fallback_count": len(warnings_log),
        "fallback_warnings": warnings_log,
        "manifest_path": str(manifest_path),
    }


def run_stage2_voice_convert(
    prepared_slides: List[Dict[str, Any]],
    job_dir: Path,
    profile: VoiceProfile,
    assets_root: str = "",
    cpu_workers: Optional[int] = None,
    dry_run: bool = False,
    resume: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
    base_dir: Path = PROJECT_ROOT,
) -> Dict[str, Any]:
    """Runs RVC v2 vocal synthesis (if enabled) + clean mastering + slide WAV/MP3 assembly."""
    s1_dir = job_dir / "audio_stage1"
    raw_items_dir = job_dir / "audio_items_raw"
    slides_wav_dir = job_dir / "audio_slides_wav"
    slides_mp3_dir = job_dir / "audio_slides_mp3"
    for d in (raw_items_dir, slides_wav_dir, slides_mp3_dir):
        d.mkdir(parents=True, exist_ok=True)

    tasks: List[Dict[str, str]] = []
    for sl in prepared_slides:
        s_no = int(sl["slide_no"])
        tts_items = list(sl.get("tts_items") or sl.get("item_scripts") or [])
        for j, _ in enumerate(tts_items):
            s1_wav = s1_dir / f"slide_{s_no:02d}_item_{j:02d}.wav"
            if not s1_wav.is_file():
                raise FileNotFoundError(f"Missing Stage 1 WAV {s1_wav}; run 'tts' step first.")
            tasks.append({"stage1_wav": str(s1_wav), "out_wav": str(raw_items_dir / s1_wav.name)})

    use_rvc = profile.stage2.enabled and profile.stage2.backend == "rvc_v2"
    m_cfg = profile.mastering
    if dry_run:
        durations: Dict[str, float] = {}
        for idx, t in enumerate(tasks, start=1):
            shutil.copy2(t["stage1_wav"], t["out_wav"])
            durations[t["out_wav"]] = read_wav_duration_safe(t["out_wav"]) or 1.5
            if on_progress:
                on_progress(idx, len(tasks))
    elif use_rvc:
        durations = convert_stage1_to_rvc_batch(
            tasks,
            profile=profile,
            assets_root=assets_root,
            cpu_workers=cpu_workers,
            resume=resume,
            on_progress=on_progress,
            base_dir=base_dir,
        )
    else:
        durations = {}
        n_cpu = cpu_workers or min(8, max(2, os.cpu_count() or 4))
        with ThreadPoolExecutor(max_workers=n_cpu) as pool:
            futs = [
                (
                    t["out_wav"],
                    pool.submit(
                        apply_clean_speech_mastering,
                        t["stage1_wav"],
                        t["out_wav"],
                        sample_rate=m_cfg.sample_rate,
                        target_lufs=m_cfg.target_lufs,
                        denoise=m_cfg.denoise,
                        noise_gate=m_cfg.noise_gate,
                        noise_gate_threshold=m_cfg.noise_gate_threshold,
                        bit_depth=m_cfg.bit_depth,
                        resampler_precision=m_cfg.resampler_precision,
                        declick=m_cfg.declick,
                        denoise_nf_db=m_cfg.denoise_nf_db,
                        denoise_nr_db=m_cfg.denoise_nr_db,
                    ),
                )
                for t in tasks
            ]
            for idx, (out_wav, fut) in enumerate(futs, start=1):
                durations[out_wav] = fut.result()
                if on_progress:
                    on_progress(idx, len(tasks))

    silence_sec = float(m_cfg.inter_item_silence)
    has_ffmpeg = shutil.which("ffmpeg") is not None
    manifest_slides: List[Dict[str, Any]] = []
    for sl in prepared_slides:
        s_no = int(sl["slide_no"])
        tts_items = list(sl.get("tts_items") or sl.get("item_scripts") or [])
        item_records: List[Dict[str, Any]] = []
        item_wavs: List[str] = []
        curr_t = 0.0
        for j, txt in enumerate(tts_items):
            item_wav = str(raw_items_dir / f"slide_{s_no:02d}_item_{j:02d}.wav")
            dur = durations[item_wav]
            item_wavs.append(item_wav)
            start_s = round(curr_t, 3)
            end_s = round(start_s + dur, 3)
            item_records.append({
                "item_index": j,
                "text": txt,
                "audio_start_sec": start_s,
                "audio_end_sec": end_s,
                "duration_sec": round(dur, 3),
            })
            curr_t = end_s + (silence_sec if j < len(tts_items) - 1 else 0.0)

        slide_wav = slides_wav_dir / f"slide_{s_no:02d}.wav"
        slide_dur = concatenate_wav_items(
            item_wavs,
            str(slide_wav),
            silence_sec=silence_sec,
            default_sample_rate=m_cfg.sample_rate,
            target_lufs=m_cfg.target_lufs,
            bit_depth=m_cfg.bit_depth,
        )
        slide_mp3 = slides_mp3_dir / f"slide_{s_no:02d}.mp3"
        if not dry_run and has_ffmpeg:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(slide_wav), "-c:a", "libmp3lame", "-b:a", m_cfg.mp3_bitrate, str(slide_mp3)],
                check=True,
            )
        manifest_slides.append({
            "slide_no": s_no,
            "title": sl.get("raw_title") or sl.get("title") or f"Segment {s_no}",
            "total_duration_sec": slide_dur,
            "audio_file": f"audio_slides_wav/slide_{s_no:02d}.wav",
            "items": item_records,
        })

    manifest_path = job_dir / "voice_manifest.json"
    save_json_atomic(
        manifest_path,
        {
            "voice_id": profile.voice_id,
            "display_name": profile.display_name,
            "license": profile.license,
            "attribution": profile.attribution,
            "slides": manifest_slides,
        },
    )
    return {"manifest_path": str(manifest_path), "slides": len(manifest_slides)}

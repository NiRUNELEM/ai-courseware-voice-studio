# -*- coding: utf-8 -*-
"""Modular discrete pipeline steps: transcribe -> prepare -> tts -> convert -> qa -> align -> render."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict

from voice_studio import PROJECT_ROOT
from voice_studio.config.models import PipelineConfig
from voice_studio.core.state import save_json_atomic


def _load_prepared(job_dir: Path) -> Dict[str, Any]:
    prep_path = job_dir / "work" / "prepared_slides.json"
    if not prep_path.is_file():
        raise FileNotFoundError(f"Missing {prep_path}; run 'prepare' step first.")
    return json.loads(prep_path.read_text(encoding="utf-8"))


def step_transcribe(
    cfg: PipelineConfig,
    job_dir: Path,
    base_dir: Path = PROJECT_ROOT,
    on_progress: Callable[[float, str], None] | None = None,
) -> Dict[str, Any]:
    """Step 0 (Optional ASR): Transcribes input audio/video into TXT/SRT/JSON + script_from_audio.json."""
    from voice_studio.audio.transcribe import transcribe_audio
    from voice_studio.config.loader import resolve_repo_path

    if on_progress:
        on_progress(0.1, "Extracting 16kHz audio and running Whisper ASR...")
    in_path = resolve_repo_path(cfg.content.path, base_dir)
    out_dir = job_dir / "transcript"
    res = transcribe_audio(
        media_path=in_path,
        output_dir=out_dir,
        backend=cfg.transcribe.backend,
        model=cfg.transcribe.model,
        language=cfg.transcribe.language,
        chunk_length_s=cfg.transcribe.chunk_length_s,
        beam_size=cfg.transcribe.beam_size,
        vad_filter=cfg.transcribe.vad_filter,
        dry_run=cfg.dry_run,
    )
    msg = f"Transcribed {res['segment_count']} segment(s) ({res['duration_sec']:.1f}s, backend={res['backend']})"
    if on_progress:
        on_progress(1.0, msg)
    return {
        "message": msg,
        "metrics": {"segments": res["segment_count"], "duration_sec": res["duration_sec"]},
        "artifacts": {
            "transcript_txt": res["txt_path"],
            "transcript_srt": res["srt_path"],
            "transcript_json": res["json_path"],
            "script_from_audio": res["script_path"],
        },
    }


def step_prepare(
    cfg: PipelineConfig,
    job_dir: Path,
    base_dir: Path = PROJECT_ROOT,
    on_progress: Callable[[float, str], None] | None = None,
) -> Dict[str, Any]:
    """Step 1: Loads script/transcript, normalizes Thai phonetics + spacing."""
    from voice_studio.content.loader import load_and_prepare_content

    if on_progress:
        on_progress(0.1, "Loading and normalizing script content...")
    res = load_and_prepare_content(cfg, job_dir=job_dir, base_dir=base_dir)
    save_json_atomic(job_dir / "work" / "job_meta.json", {"meta": res["meta"]})
    msg = f"Prepared {len(res['slides'])} segment(s)"
    if on_progress:
        on_progress(1.0, msg)
    return {
        "message": msg,
        "metrics": {"segments": len(res["slides"])},
        "artifacts": {"prepared_slides": res["prepared_path"], "slides_phonetic": res["slides_json_path"]},
    }


def step_tts(
    cfg: PipelineConfig,
    job_dir: Path,
    base_dir: Path = PROJECT_ROOT,
    on_progress: Callable[[float, str], None] | None = None,
) -> Dict[str, Any]:
    """Step 2: Synthesizes Stage 1 base speech WAVs per item via Edge-TTS."""
    from voice_studio.audio.stage_runner import run_stage1_tts

    prep = _load_prepared(job_dir)
    slides = list(prep.get("slides") or [])
    profile = cfg.voice_profile
    assert profile is not None

    def _item_cb(done: int, total: int) -> None:
        if on_progress and total > 0:
            on_progress(done / float(total), f"Stage 1 TTS {done}/{total} item(s)")

    res = run_stage1_tts(
        prepared_slides=slides,
        job_dir=job_dir,
        profile=profile,
        stage1_workers=cfg.runtime.stage1_workers,
        dry_run=cfg.dry_run,
        resume=cfg.resume,
        on_progress=_item_cb,
    )
    fb_n = int(res.get("fallback_count") or 0)
    fb_note = f", {fb_n} fallback-recovered" if fb_n else ""
    s1_label = profile.stage1.voice if str(profile.stage1.backend).lower() == "edge_tts" else (profile.stage1.hf_model_repo or profile.stage1.backend)
    msg = f"Stage 1 TTS complete ({res['item_count']} items, voice={s1_label}{fb_note})"
    return {
        "message": msg,
        "metrics": {
            "stage1_items": res["item_count"],
            "fallback_items": fb_n,
            "fallback_warnings": list(res.get("fallback_warnings") or []),
        },
        "artifacts": {"stage1_dir": res["stage1_dir"], "stage1_manifest": res["manifest_path"]},
    }


def step_convert(
    cfg: PipelineConfig,
    job_dir: Path,
    base_dir: Path = PROJECT_ROOT,
    on_progress: Callable[[float, str], None] | None = None,
) -> Dict[str, Any]:
    """Step 3: Applies RVC v2 vocal synthesis (if enabled) + denoise/noise-gate/-16 LUFS mastering."""
    from voice_studio.audio.stage_runner import run_stage2_voice_convert

    prep = _load_prepared(job_dir)
    slides = list(prep.get("slides") or [])
    profile = cfg.voice_profile
    assert profile is not None

    def _item_cb(done: int, total: int) -> None:
        if on_progress and total > 0:
            on_progress(done / float(total), f"Voice convert ({profile.voice_id}) {done}/{total} item(s)")

    res = run_stage2_voice_convert(
        prepared_slides=slides,
        job_dir=job_dir,
        profile=profile,
        assets_root=cfg.runtime.rvc_assets_root,
        cpu_workers=cfg.runtime.cpu_workers or None,
        dry_run=cfg.dry_run,
        resume=cfg.resume,
        on_progress=_item_cb,
        base_dir=base_dir,
    )
    msg = f"Voice convert & mastering complete ({res['slides']} segment WAVs, voice={profile.voice_id})"
    return {
        "message": msg,
        "metrics": {"voice_segments": res["slides"], "voice_id": profile.voice_id},
        "artifacts": {"voice_manifest": res["manifest_path"], "audio_slides_wav": str(job_dir / "audio_slides_wav")},
    }


def step_qa(
    cfg: PipelineConfig,
    job_dir: Path,
    base_dir: Path = PROJECT_ROOT,
    on_progress: Callable[[float, str], None] | None = None,
) -> Dict[str, Any]:
    """Step 4: Validates WAV integrity, duration budget, SNR dB, and LUFS loudness."""
    from voice_studio.audio.qa_gate import print_gate_report, validate_audio_deliverables

    prep = _load_prepared(job_dir)
    slides = list(prep.get("slides") or [])
    meta = dict(prep.get("meta") or {})
    phonetic_path = str(job_dir / "work" / "slides_phonetic.json")

    res = validate_audio_deliverables(
        job_dir=str(job_dir),
        phonetic_json_path=phonetic_path,
        timeline_json_path=str(meta.get("timeline_json") or ""),
        expected_slides=len(slides),
        duration_tolerance=cfg.audio_qa.duration_tolerance,
        min_snr=cfg.audio_qa.min_snr_db,
        target_lufs=cfg.audio_qa.target_lufs,
        lufs_tolerance=cfg.audio_qa.lufs_tolerance,
        enforce_zero_latin=bool(meta.get("zero_latin_phonetic", cfg.content.zero_latin_phonetic)),
    )
    print_gate_report(cfg.job_id or "job", res)
    if res.exit_code != 0:
        raise RuntimeError(f"Audio QA gate failed: {'; '.join(res.violations[:5])}")
    msg = f"Audio QA PASS ({len(res.measured_metrics)} segments verified)"
    if on_progress:
        on_progress(1.0, msg)
    return {"message": msg, "metrics": {"qa_segments": len(res.measured_metrics)}, "artifacts": {}}


def step_align(
    cfg: PipelineConfig,
    job_dir: Path,
    base_dir: Path = PROJECT_ROOT,
    on_progress: Callable[[float, str], None] | None = None,
) -> Dict[str, Any]:
    """Step 5: Aligns measured WAV item durations with optional PPTX/manifest reveals into timeline_manifest.json."""
    from voice_studio.video.alignment import generate_job_timeline, validate_timeline

    prep = _load_prepared(job_dir)
    slides = list(prep.get("slides") or [])
    meta = dict(prep.get("meta") or {})

    timeline = generate_job_timeline(
        job_dir=str(job_dir),
        prepared_slides=slides,
        silence_gap=float(cfg.voice_profile.mastering.inter_item_silence if cfg.voice_profile else 0.40),
        reveal_manifest_path=str(meta.get("reveal_manifest") or ""),
        reveal_key=str(meta.get("reveal_key") or ""),
        pptx_path=str(meta.get("pptx_path") or ""),
        timeline_json_path=str(meta.get("timeline_json") or ""),
    )
    violations = validate_timeline(timeline, expected_count=len(slides))
    if violations > 0:
        raise RuntimeError(f"Timeline alignment validation failed ({violations} violation(s))")

    tl_path = job_dir / "timeline_manifest.json"
    save_json_atomic(tl_path, timeline)
    total_sec = round(sum(float(v.get("total_duration_sec", 0.0)) for v in timeline.values()), 3)
    msg = f"Aligned {len(timeline)} segment(s), total duration {total_sec:.2f}s"
    if on_progress:
        on_progress(1.0, msg)
    return {
        "message": msg,
        "metrics": {"aligned_segments": len(timeline), "total_duration_sec": total_sec},
        "artifacts": {"timeline_manifest": str(tl_path)},
    }


def step_render(
    cfg: PipelineConfig,
    job_dir: Path,
    base_dir: Path = PROJECT_ROOT,
    on_progress: Callable[[float, str], None] | None = None,
) -> Dict[str, Any]:
    """Step 6: Renders WAV/MP3/1080p MP4 deliverables, HTML5 players, and ZIP archive."""
    from voice_studio.config.loader import resolve_repo_path
    from voice_studio.video.renderer import render_job_video

    prep = _load_prepared(job_dir)
    slides = list(prep.get("slides") or [])
    meta = dict(prep.get("meta") or {})
    tl_path = job_dir / "timeline_manifest.json"
    if not tl_path.is_file():
        raise FileNotFoundError(f"Missing {tl_path}; run 'align' step first.")
    timeline = json.loads(tl_path.read_text(encoding="utf-8"))

    slides_png_dir = str(meta.get("slides_png_dir") or "")
    if cfg.content.slides_png_dir:
        cfg_png_dir = resolve_repo_path(cfg.content.slides_png_dir, base_dir)
        if cfg_png_dir.is_dir() or not slides_png_dir or not Path(slides_png_dir).is_dir():
            slides_png_dir = str(cfg_png_dir)
    pptx_path = str(meta.get("pptx_path") or "")
    if cfg.content.pptx_path and not pptx_path:
        pptx_path = str(resolve_repo_path(cfg.content.pptx_path, base_dir))

    if on_progress:
        on_progress(0.2, "Rendering requested audio/video deliverables...")
    artifacts = render_job_video(
        job_dir=job_dir,
        prepared_slides=slides,
        timeline=timeline,
        slides_png_dir=slides_png_dir,
        pptx_path=pptx_path,
        dry_run=cfg.dry_run,
        workers=cfg.runtime.video_workers,
        title=str(meta.get("title") or cfg.content.title or "Voice Studio Production"),
        output_prefix=cfg.output.prefix or cfg.job_id or "studio",
        output_formats=cfg.output.formats,
        fps=cfg.output.fps,
        aac_bitrate=cfg.output.aac_bitrate,
        mp3_bitrate=cfg.output.mp3_bitrate,
        nvenc_preset=cfg.output.nvenc_preset,
        nvenc_cq=cfg.output.nvenc_cq,
        bit_depth=cfg.voice_profile.mastering.bit_depth if cfg.voice_profile else 16,
    )
    msg = f"Deliverables complete (codec={artifacts.get('codec')}, elapsed={artifacts.get('elapsed_sec')}s)"
    if on_progress:
        on_progress(1.0, msg)
    return {"message": msg, "metrics": {"codec": artifacts.get("codec")}, "artifacts": artifacts}


STEP_RUNNERS = {
    "transcribe": step_transcribe,
    "prepare": step_prepare,
    "tts": step_tts,
    "convert": step_convert,
    "qa": step_qa,
    "align": step_align,
    "render": step_render,
}

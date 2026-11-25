# -*- coding: utf-8 -*-
"""Public Python SDK for Voice & Media Studio (programmatic TTS, RVC v2, Mastering, Video & Colab)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from voice_studio import PROJECT_ROOT
from voice_studio.audio.mastering import EQ_PRESETS
from voice_studio.colab.bundle import build_production_bundle
from voice_studio.colab.client import ColabClient
from voice_studio.config.loader import load_pipeline_config, resolve_repo_path, resolve_step_range
from voice_studio.config.models import PipelineConfig
from voice_studio.config.voices import list_builtin_voices, resolve_voice_profile
from voice_studio.core.models_manager import verify_local_models
from voice_studio.core.runtime import probe_runtime
from voice_studio.core.security import validate_safe_id
from voice_studio.core.state import list_jobs
from voice_studio.pipeline.orchestrator import PipelineOrchestrator


def build_run_overrides(
    *,
    job_id: Optional[str] = None,
    input_path: Optional[str] = None,
    voice: Optional[str] = None,
    target: Optional[str] = None,
    steps: Optional[List[str]] = None,
    slides: Optional[List[int]] = None,
    extra_vocab_path: Optional[str] = None,
    voice_overrides: Optional[Dict[str, Any]] = None,
    output_overrides: Optional[Dict[str, Any]] = None,
    audio_only: bool = False,
    resume: Optional[bool] = None,
    clean_work: bool = False,
    dry_run: Optional[bool] = None,
    colab_url: Optional[str] = None,
    colab_token: Optional[str] = None,
    drive_mount_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Builds a validated nested overrides dict shared by the Python SDK and MCP server."""
    overrides: Dict[str, Any] = {}
    if job_id:
        overrides.setdefault("job", {})["id"] = validate_safe_id(str(job_id), label="job_id")
    if dry_run is not None:
        overrides.setdefault("job", {})["dry_run"] = bool(dry_run)
    if resume is not None:
        overrides.setdefault("job", {})["resume"] = bool(resume)
    if clean_work:
        overrides.setdefault("job", {})["clean_work"] = True
    if input_path:
        overrides.setdefault("input", {})["path"] = str(input_path)
    if slides:
        overrides.setdefault("input", {})["slides"] = [int(s) for s in slides]
    if extra_vocab_path:
        overrides.setdefault("input", {})["extra_vocab_path"] = str(extra_vocab_path)
    if voice_overrides and isinstance(voice_overrides, dict):
        v_dict = dict(voice_overrides)
        if voice and "profile" not in v_dict:
            v_dict["profile"] = str(voice)
        overrides["voice"] = v_dict
    elif voice:
        overrides["voice"] = str(voice)
    if output_overrides and isinstance(output_overrides, dict):
        overrides["output"] = dict(output_overrides)
    if audio_only:
        overrides.setdefault("output", {})["formats"] = ["wav", "mp3"]
    if target:
        overrides.setdefault("runtime", {})["target"] = str(target)
    if steps:
        overrides["steps"] = resolve_step_range(steps=[str(s) for s in steps])
    for src_val, dst_k in ((colab_url, "agent_url"), (colab_token, "agent_token"), (drive_mount_dir, "local_mount_dir")):
        if src_val:
            overrides.setdefault("colab", {})[dst_k] = str(src_val)
    return overrides


class VoiceStudio:
    """High-level Python SDK client for Voice & Media Studio workflows."""

    def __init__(self, config_path: Optional[str | Path] = None, base_dir: Path = PROJECT_ROOT) -> None:
        self.config_path = str(config_path) if config_path else None
        self.base_dir = Path(base_dir)

    def load_config(self, overrides: Optional[Dict[str, Any]] = None) -> PipelineConfig:
        """Loads and validates a typed PipelineConfig with optional overrides."""
        return load_pipeline_config(config_path=self.config_path, overrides=overrides, base_dir=self.base_dir)

    def doctor(self, voice_python: str = "") -> Dict[str, Any]:
        """Inspects Python, CUDA GPU, FFmpeg/NVENC, Whisper ASR, and RVC directories."""
        cfg = self.load_config()
        return probe_runtime(voice_python or cfg.runtime.voice_python or "", cfg.runtime.rvc_assets_root)

    def voices(self, voice_id: str = "") -> Dict[str, Any]:
        """Lists built-in voice profiles and EQ presets, or inspects a single voice profile."""
        vid = (voice_id or "").strip()
        if vid:
            vp = resolve_voice_profile(vid)
            return {
                "voice_id": vp.voice_id,
                "display_name": vp.display_name,
                "stage1": vp.stage1.__dict__,
                "stage2": vp.stage2.__dict__,
                "mastering": vp.mastering.__dict__,
            }
        return {
            "voices": {k: {"display_name": v.display_name, "rvc_enabled": v.stage2.enabled} for k, v in list_builtin_voices().items()},
            "eq_presets": sorted(EQ_PRESETS.keys()),
        }

    def eq_presets(self) -> Dict[str, List[Dict[str, float]]]:
        """Returns all built-in mastering EQ presets."""
        return {k: [dict(b) for b in bands] for k, bands in EQ_PRESETS.items()}

    def preview(
        self,
        input_or_text: str,
        *,
        voice: str = "gawr_gura",
        vocab_path: str = "",
        is_inline_text: bool = False,
    ) -> Dict[str, Any]:
        """Previews Thai phonetic normalization and TTS strings before synthesis."""
        from voice_studio.content.loader import preview_prepared_content

        return preview_prepared_content(
            input_path_or_text=input_or_text,
            voice=voice,
            is_inline_text=is_inline_text,
            base_dir=self.base_dir,
            vocab_path=vocab_path,
        )

    def transcribe(
        self,
        input_path: str | Path,
        *,
        out_dir: Optional[str | Path] = None,
        backend: str = "",
        model: str = "",
        language: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Transcribes audio/video into TXT, SRT, JSON, and Studio script using Whisper ASR."""
        from voice_studio.audio.transcribe import transcribe_audio

        media_path = resolve_repo_path(str(input_path), self.base_dir)
        cfg = self.load_config()
        tcfg = cfg.transcribe
        target_dir = (
            resolve_repo_path(str(out_dir), self.base_dir)
            if out_dir
            else resolve_repo_path(cfg.output_root, self.base_dir) / f"tr_{validate_safe_id(media_path.stem, label='job_id')}"
        )
        return transcribe_audio(
            media_path=media_path,
            output_dir=target_dir,
            backend=backend or tcfg.backend or "auto",
            model=model or tcfg.model or "openai/whisper-small",
            language=language if language is not None else tcfg.language,
            beam_size=tcfg.beam_size,
            vad_filter=tcfg.vad_filter,
            dry_run=dry_run,
        )

    def run(
        self,
        *,
        input_path: Optional[str] = None,
        job_id: Optional[str] = None,
        voice: Optional[str] = None,
        target: Optional[str] = None,
        steps: Optional[List[str]] = None,
        slides: Optional[List[int]] = None,
        extra_vocab_path: Optional[str] = None,
        voice_overrides: Optional[Dict[str, Any]] = None,
        output_overrides: Optional[Dict[str, Any]] = None,
        audio_only: bool = False,
        resume: Optional[bool] = None,
        clean_work: bool = False,
        dry_run: Optional[bool] = None,
        colab_url: Optional[str] = None,
        colab_token: Optional[str] = None,
        drive_mount_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Runs the end-to-end or step-scoped Voice & Media Studio pipeline."""
        overrides = build_run_overrides(
            job_id=job_id,
            input_path=input_path,
            voice=voice,
            target=target,
            steps=steps,
            slides=slides,
            extra_vocab_path=extra_vocab_path,
            voice_overrides=voice_overrides,
            output_overrides=output_overrides,
            audio_only=audio_only,
            resume=resume,
            clean_work=clean_work,
            dry_run=dry_run,
            colab_url=colab_url,
            colab_token=colab_token,
            drive_mount_dir=drive_mount_dir,
        )
        cfg = self.load_config(overrides=overrides or None)
        return PipelineOrchestrator(cfg, base_dir=self.base_dir).run()

    def status(self, job_id: str = "") -> Dict[str, Any]:
        """Returns status for one job ID or lists all jobs under output_root."""
        cfg = self.load_config()
        out_root = resolve_repo_path(cfg.output_root, self.base_dir)
        if job_id.strip():
            safe_id = validate_safe_id(job_id.strip(), label="job_id")
            st_path = out_root / safe_id / "status.json"
            if not st_path.is_file():
                raise FileNotFoundError(f"Job status not found: {st_path}")
            return json.loads(st_path.read_text(encoding="utf-8"))
        return {"jobs": list_jobs(out_root)}

    def verify_models(self, verify_checksums: bool = True) -> Dict[str, Any]:
        """Verifies local model manifest and optional SHA-256 checksums."""
        return verify_local_models(base_dir=self.base_dir, verify_checksums=verify_checksums)

    def build_bundle(self, include_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """Builds exports/voice_studio_colab_bundle.zip for remote Colab execution."""
        rc = build_production_bundle(self.base_dir, include_paths=list(include_paths or []))
        return {"exit_code": rc, "bundle_path": str(self.base_dir / "exports/voice_studio_colab_bundle.zip")}

    def _colab_client(self, target: Optional[str] = None) -> ColabClient:
        overrides = {"runtime": {"target": target}} if target else None
        return ColabClient(self.load_config(overrides=overrides), base_dir=self.base_dir)

    def colab_ping(self, *, target: Optional[str] = None, ensure_runtime: bool = False) -> Dict[str, Any]:
        return self._colab_client(target=target).ping(ensure=ensure_runtime)

    def colab_stop(self, *, session_name: str = "") -> Dict[str, Any]:
        return self._colab_client(target="colab_cli").stop_session(session_name=session_name)

    def colab_logs(self, job_id: str, *, offset: int = 0, target: Optional[str] = None) -> Dict[str, Any]:
        return self._colab_client(target=target).fetch_logs(validate_safe_id(job_id, label="job_id"), offset=offset)

    def colab_download(self, job_id: str, *, target: Optional[str] = None) -> Dict[str, Any]:
        safe_id = validate_safe_id(job_id, label="job_id")
        client = self._colab_client(target=target)
        out_dir = resolve_repo_path(client.cfg.output_root, self.base_dir) / safe_id
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = client.download_outputs(out_dir, safe_id)
        return {"job_id": safe_id, "output_dir": str(dest), "status": client.fetch_status(safe_id)}

    def colab_exec(self, command: str, *, cwd: str = "", timeout_sec: float = 120.0, target: Optional[str] = None) -> Dict[str, Any]:
        return self._colab_client(target=target).exec_command(command=command, cwd=cwd, timeout_sec=timeout_sec)

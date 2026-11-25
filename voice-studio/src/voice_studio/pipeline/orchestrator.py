# -*- coding: utf-8 -*-
"""Master Workflow Orchestrator: runs step-by-step or single-pass locally or via signed-in Colab."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from voice_studio import PROJECT_ROOT
from voice_studio.config.loader import resolve_repo_path, validate_steps
from voice_studio.config.models import PipelineConfig
from voice_studio.core.runtime import has_module, resolve_voice_python
from voice_studio.core.security import collect_checksums, sha256_file, validate_safe_id
from voice_studio.core.state import (
    compute_pipeline_signatures,
    default_job_id,
    format_job_status,
    invalidate_steps_from,
    load_or_init_job_state,
    purge_step_artifacts,
    save_json_atomic,
    update_step_state,
)
from voice_studio.pipeline.steps import STEP_RUNNERS


def _serialize_job_spec(cfg: PipelineConfig, for_remote: bool = False) -> Dict[str, Any]:
    """Exports portable unified studio job spec dict so Colab or worker reproduces exact config."""
    profile = cfg.voice_profile
    voice_block: Dict[str, Any] = {}
    if profile is not None:
        voice_block = {
            "profile": profile.voice_id,
            "display_name": profile.display_name,
            "license": profile.license,
            "license_scope": profile.license_scope,
            "tts_backend": profile.stage1.backend,
            "tts_voice": profile.stage1.voice,
            "tts_rate": profile.stage1.rate,
            "tts_pitch": profile.stage1.pitch,
            "pacing_sec": profile.stage1.pacing_sec,
            "text_mode": profile.stage1.text_mode,
            "clause_spacing": profile.stage1.clause_spacing,
            "rvc": {
                "enabled": profile.stage2.enabled,
                "source_type": profile.stage2.source_type,
                "repo_id": profile.stage2.repo_id,
                "zip_filename": profile.stage2.zip_filename,
                "pth_filename": profile.stage2.pth_filename,
                "index_filename": profile.stage2.index_filename,
                "subdir": profile.stage2.default_subdir,
                "local_pth": profile.stage2.local_pth,
                "local_index": profile.stage2.local_index,
                "pitch_shift": profile.stage2.pitch_shift,
                "f0_method": profile.stage2.f0_method,
                "index_rate": profile.stage2.index_rate,
                "volume_envelope": profile.stage2.volume_envelope,
                "protect": profile.stage2.protect,
                "hop_length": profile.stage2.hop_length,
                "input_gate_threshold": profile.stage2.input_gate_threshold,
                "input_target_rms": profile.stage2.input_target_rms,
                "input_peak_ceiling": profile.stage2.input_peak_ceiling,
                "output_peak_ceiling": profile.stage2.output_peak_ceiling,
                "rvc_fp32": profile.stage2.rvc_fp32,
                "intermediate_float": profile.stage2.intermediate_float,
            },
        }
    return {
        "job": {"id": cfg.job_id, "output_dir": "output" if for_remote else cfg.output_root, "resume": cfg.resume, "dry_run": cfg.dry_run},
        "input": {
            "mode": cfg.content.mode,
            "path": cfg.content.path,
            "slides": list(cfg.content.slides),
            "slides_png_dir": cfg.content.slides_png_dir,
            "pptx_path": cfg.content.pptx_path,
            "timeline_json": cfg.content.timeline_json,
            "reveal_manifest": cfg.content.reveal_manifest,
            "reveal_key": cfg.content.reveal_key,
            "title": cfg.content.title,
            "zero_latin_phonetic": cfg.content.zero_latin_phonetic,
        },
        "transcribe": {
            "backend": cfg.transcribe.backend,
            "model": cfg.transcribe.model,
            "language": cfg.transcribe.language,
            "beam_size": cfg.transcribe.beam_size,
            "vad_filter": cfg.transcribe.vad_filter,
            "chunk_length_s": cfg.transcribe.chunk_length_s,
            "formats": list(cfg.transcribe.formats),
        },
        "voice": voice_block,
        "output": {
            "dir": "output" if for_remote else cfg.output.dir,
            "prefix": cfg.output.prefix,
            "formats": list(cfg.output.formats),
            "sample_rate": profile.mastering.sample_rate if profile else 40000,
            "bit_depth": profile.mastering.bit_depth if profile else 16,
            "resampler_precision": profile.mastering.resampler_precision if profile else 20,
            "target_lufs": cfg.audio_qa.target_lufs,
            "lufs_tolerance": cfg.audio_qa.lufs_tolerance,
            "min_snr_db": cfg.audio_qa.min_snr_db,
            "duration_tolerance": cfg.audio_qa.duration_tolerance,
            "denoise": profile.mastering.denoise if profile else True,
            "denoise_nf_db": profile.mastering.denoise_nf_db if profile else -48,
            "denoise_nr_db": profile.mastering.denoise_nr_db if profile else 8,
            "declick": profile.mastering.declick if profile else False,
            "noise_gate": profile.mastering.noise_gate if profile else True,
            "noise_gate_threshold": profile.mastering.noise_gate_threshold if profile else 0.007,
            "inter_item_silence": profile.mastering.inter_item_silence if profile else 0.40,
            "mp3_bitrate": cfg.output.mp3_bitrate,
            "aac_bitrate": cfg.output.aac_bitrate,
            "nvenc_preset": cfg.output.nvenc_preset,
            "nvenc_cq": cfg.output.nvenc_cq,
            "fps": cfg.output.fps,
        },
        "phonetic_vocab_path": cfg.phonetic_vocab_path,
        "steps": list(cfg.steps),
        "runtime": {
            "target": "local" if for_remote else cfg.target,
            "voice_python": "" if for_remote else cfg.runtime.voice_python,
            "rvc_assets_root": "" if for_remote else cfg.runtime.rvc_assets_root,
            "device": cfg.runtime.device,
            "gpu_tier": cfg.runtime.gpu_tier,
            "high_ram": cfg.runtime.high_ram,
            "precision": cfg.runtime.precision,
            "stage1_workers": cfg.runtime.stage1_workers,
            "cpu_workers": cfg.runtime.cpu_workers,
            "video_workers": cfg.runtime.video_workers,
            "fps": cfg.runtime.fps,
        },
    }


def _needs_external_voice_python(cfg: PipelineConfig, step_name: str) -> bool:
    if cfg.dry_run or step_name not in ("transcribe", "tts", "convert"):
        return False
    profile = cfg.voice_profile
    assert profile is not None
    need_asr = (step_name == "transcribe") and not (has_module("faster_whisper") or has_module("whisper") or (has_module("transformers") and has_module("torch")))
    need_edge = (step_name == "tts") and profile.stage1.backend == "edge_tts" and not has_module("edge_tts")
    need_torch = (step_name == "convert") and profile.stage2.enabled and profile.stage2.backend == "rvc_v2" and not has_module("torch")
    return need_asr or need_edge or need_torch


def _delegate_to_voice_python(cfg: PipelineConfig, job_dir: Path, step_name: str, base_dir: Path) -> Dict[str, Any]:
    voice_py = resolve_voice_python(cfg.runtime.voice_python)
    if not voice_py:
        raise RuntimeError(
            f"Step '{step_name}' requires voice/GPU/ASR packages not present in {sys.executable}. "
            "Pass --voice-python /path/to/venv/bin/python or VOICE_STUDIO_PYTHON, install requirements.txt, "
            "use --dry-run, or run with --target colab_cli / colab_agent / colab_drive."
        )
    spec_path = job_dir / "work" / "job_spec.json"
    save_json_atomic(spec_path, _serialize_job_spec(cfg))
    env = dict(os.environ)
    src_dir = str(base_dir / "src")
    env["PYTHONPATH"] = src_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if cfg.runtime.rvc_assets_root:
        env["RVC_ASSETS_ROOT"] = cfg.runtime.rvc_assets_root
    cmd = [voice_py, "-m", "voice_studio", "internal-step", "--config", str(spec_path), "--job", cfg.job_id, "--step", step_name]
    live_log = job_dir / "logs" / "live_terminal.log"
    live_log.parent.mkdir(parents=True, exist_ok=True)
    header = f"[orchestrator] Delegating '{step_name}' -> {voice_py}"
    print(header, flush=True)
    with open(live_log, "a", encoding="utf-8") as lf:
        lf.write(header + "\n")
        proc = subprocess.Popen(cmd, cwd=str(base_dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert proc.stdout is not None
        for ln in proc.stdout:
            sys.stdout.write(ln)
            sys.stdout.flush()
            lf.write(ln)
            lf.flush()
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Delegated step '{step_name}' failed with exit code {proc.returncode}")
    updated_state = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    return dict(updated_state["steps"].get(step_name) or {})


class PipelineOrchestrator:
    """Controls local and signed-in Colab end-to-end or step-by-step execution."""

    def __init__(
        self,
        cfg: PipelineConfig,
        base_dir: Path = PROJECT_ROOT,
        status_mirror_path: Optional[Path] = None,
    ) -> None:
        self.cfg = cfg
        self.base_dir = base_dir
        self.status_mirror_path = status_mirror_path
        self.output_root = resolve_repo_path(cfg.output_root, base_dir)
        label = Path(cfg.content.path).stem if cfg.content.path else "studio"
        self.job_id = validate_safe_id(cfg.job_id or default_job_id(label, cfg.voice), label="job_id")
        self.cfg.job_id = self.job_id
        self.job_dir = self.output_root / self.job_id
        self.job_dir.mkdir(parents=True, exist_ok=True)
        (self.job_dir / "work").mkdir(parents=True, exist_ok=True)
        (self.job_dir / "logs").mkdir(parents=True, exist_ok=True)
        self.live_log_path = self.job_dir / "logs" / "live_terminal.log"

    def _emit_live(self, text: str) -> None:
        print(text, flush=True)
        with open(self.live_log_path, "a", encoding="utf-8") as lf:
            lf.write(text + "\n")

    def _mirror_status(self, state: Dict[str, Any]) -> None:
        if self.status_mirror_path is not None:
            save_json_atomic(self.status_mirror_path, state)

    def run(self) -> Dict[str, Any]:
        """Executes configured steps either locally or on connected Google Colab."""
        steps = validate_steps(self.cfg.steps)
        content_sha = ""
        if self.cfg.content.path:
            in_file = resolve_repo_path(self.cfg.content.path, self.base_dir)
            if in_file.is_file():
                content_sha = sha256_file(in_file)
        content_sig_payload = {
            "path_name": Path(self.cfg.content.path).name if self.cfg.content.path else "",
            "slides": list(self.cfg.content.slides),
            "input_sha256": content_sha,
        }
        content_summary = {
            **content_sig_payload,
            "path": self.cfg.content.path,
            "slides_png_dir": self.cfg.content.slides_png_dir,
        }
        signatures = compute_pipeline_signatures(content_sig_payload, self.cfg.voice_profile)
        state = load_or_init_job_state(
            job_dir=self.job_dir,
            job_id=self.job_id,
            target=self.cfg.target,
            voice_id=self.cfg.voice,
            requested_steps=steps,
            content_summary=content_summary,
            signatures=signatures,
        )
        save_json_atomic(self.job_dir / "work" / "job_spec.json", _serialize_job_spec(self.cfg))
        self._mirror_status(state)

        if self.cfg.target in ("colab_cli", "colab_agent", "colab_drive"):
            if not self.cfg.resume and steps:
                state = invalidate_steps_from(
                    self.job_dir, state, steps[0], reason="Clean --no-resume Colab run", inclusive=True, purge_artifacts=True
                )
                save_json_atomic(self.job_dir / "status.json", state)
                self._mirror_status(state)
            if self.cfg.target == "colab_cli" and not self.cfg.dry_run:
                s1_backend = str(getattr(getattr(self.cfg.voice_profile, "stage1", None), "backend", "edge_tts")).lower()
                pre_steps = ("prepare",) if s1_backend != "edge_tts" else ("prepare", "tts")
                for local_step in pre_steps:
                    if local_step not in steps:
                        continue
                    rec = state["steps"].get(local_step, {})
                    if self.cfg.resume and rec.get("status") == "completed" and not rec.get("needs_rerun"):
                        continue
                    if not self.cfg.resume:
                        purge_step_artifacts(self.job_dir, local_step)
                    state = update_step_state(
                        self.job_dir, state, local_step, status="running", message=f"Pre-staging {local_step} locally", progress=0.05
                    )
                    self._mirror_status(state)
                    self._emit_live(f"\n[LOCAL PRE-STAGE -> {local_step}] Job={self.job_id} | Voice={self.cfg.voice}")

                    def _local_cb(frac: float, msg: str, _s: str = local_step) -> None:
                        update_step_state(self.job_dir, state, _s, status="running", message=msg, progress=frac)
                        self._mirror_status(state)
                        self._emit_live(f"  [{_s}] {frac * 100:5.1f}% - {msg}")

                    res = STEP_RUNNERS[local_step](self.cfg, self.job_dir, self.base_dir, _local_cb)
                    state = update_step_state(
                        self.job_dir,
                        state,
                        local_step,
                        status="completed",
                        message=str(res.get("message", "Done")),
                        progress=1.0,
                        metrics=res.get("metrics"),
                        artifacts=res.get("artifacts"),
                    )
                    self._mirror_status(state)
            import importlib
            import voice_studio.colab.client as _client_mod
            import voice_studio.colab.colab_cli_bridge as _cli_mod

            importlib.reload(_cli_mod)
            importlib.reload(_client_mod)
            return _client_mod.ColabClient(self.cfg, base_dir=self.base_dir).run_remote_job(
                job_dir=self.job_dir, job_id=self.job_id, state=state
            )

        for step_name in steps:
            step_rec = state["steps"].get(step_name, {})
            if self.cfg.resume and step_rec.get("status") == "completed" and not step_rec.get("needs_rerun"):
                print(f"[SKIP] {step_name} already completed (resume=True)")
                continue

            if not self.cfg.resume:
                purge_step_artifacts(self.job_dir, step_name)
            invalidate_steps_from(
                self.job_dir, state, step_name, reason=f"Invalidated by re-run of {step_name}", inclusive=False, purge_artifacts=False
            )
            state = update_step_state(self.job_dir, state, step_name, status="running", message=f"Starting {step_name}", progress=0.05)
            self._mirror_status(state)
            self._emit_live(f"\n[STEP -> {step_name}] Job={self.job_id} | Voice={self.cfg.voice} | DryRun={self.cfg.dry_run}")
            try:
                if _needs_external_voice_python(self.cfg, step_name):
                    _delegate_to_voice_python(self.cfg, self.job_dir, step_name, self.base_dir)
                    state = json.loads((self.job_dir / "status.json").read_text(encoding="utf-8"))
                    self._mirror_status(state)
                else:
                    runner = STEP_RUNNERS[step_name]

                    def _progress_cb(frac: float, msg: str) -> None:
                        update_step_state(self.job_dir, state, step_name, status="running", message=msg, progress=frac)
                        self._mirror_status(state)
                        self._emit_live(f"  [{step_name}] {frac * 100:5.1f}% - {msg}")

                    res = runner(self.cfg, self.job_dir, self.base_dir, _progress_cb)
                    state = update_step_state(
                        self.job_dir,
                        state,
                        step_name,
                        status="completed",
                        message=str(res.get("message", "Done")),
                        progress=1.0,
                        metrics=res.get("metrics"),
                        artifacts=res.get("artifacts"),
                    )
                    self._mirror_status(state)
            except Exception as exc:
                update_step_state(self.job_dir, state, step_name, status="failed", message=f"Failed: {exc}", error=str(exc))
                self._mirror_status(state)
                raise

        deliv_dir = self.job_dir / "deliverables"
        if deliv_dir.is_dir():
            state["checksums"] = collect_checksums(deliv_dir)
            save_json_atomic(self.job_dir / "status.json", state)
            self._mirror_status(state)

        if getattr(self.cfg, "clean_work", False) and deliv_dir.is_dir():
            from voice_studio.core.state import cleanup_intermediate_job_dirs

            cleaned = cleanup_intermediate_job_dirs(self.job_dir)
            if cleaned:
                print(f"[clean] Removed intermediate staging dirs: {', '.join(cleaned)}")

        print("\n" + format_job_status(state))
        if deliv_dir.is_dir():
            print(f"[FINAL DELIVERABLES] Ready inside: {deliv_dir}")
            for fp in sorted(deliv_dir.iterdir()):
                if fp.is_file():
                    print(f"  - {fp.name} ({fp.stat().st_size:,} bytes)")
        return state

    def run_single_internal_step(self, step_name: str) -> Dict[str, Any]:
        """Executes one delegated step in-process without altering outer requested_steps."""
        if step_name not in STEP_RUNNERS:
            raise ValueError(f"Unknown step {step_name!r}")
        state = json.loads((self.job_dir / "status.json").read_text(encoding="utf-8"))
        runner = STEP_RUNNERS[step_name]

        def _progress_cb(frac: float, msg: str) -> None:
            update_step_state(self.job_dir, state, step_name, status="running", message=msg, progress=frac)
            print(f"  [{step_name}] {frac * 100:5.1f}% - {msg}")

        res = runner(self.cfg, self.job_dir, self.base_dir, _progress_cb)
        return update_step_state(
            self.job_dir,
            state,
            step_name,
            status="completed",
            message=str(res.get("message", "Done")),
            progress=1.0,
            metrics=res.get("metrics"),
            artifacts=res.get("artifacts"),
        )

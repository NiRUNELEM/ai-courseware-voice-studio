# -*- coding: utf-8 -*-
"""Unified CLI for Media Studio Tool (Transcribe, TTS, RVC v2, Audio QA, Video Render, Colab)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from voice_studio import PROJECT_ROOT
from voice_studio.cli_tools import (
    apply_pre_run_choices,
    cmd_clean,
    cmd_configure,
    cmd_input,
    cmd_mcp,
    cmd_models,
    cmd_transcribe,
    cmd_verify,
    sync_deliverables_to_drive,
)
from voice_studio.config.loader import load_pipeline_config, resolve_repo_path, resolve_step_range
from voice_studio.config.models import ORDERED_STEPS
from voice_studio.config.voices import list_builtin_voices, resolve_voice_profile
from voice_studio.core.runtime import probe_runtime
from voice_studio.core.security import redact_secret, validate_safe_id
from voice_studio.core.state import format_job_status, list_jobs
from voice_studio.pipeline.orchestrator import PipelineOrchestrator


def _cmd_run(args: argparse.Namespace) -> int:
    overrides: Dict[str, Any] = {}
    job_id = validate_safe_id(args.job, label="job_id") if args.job.strip() else ""
    if job_id:
        overrides.setdefault("job", {})["id"] = job_id
    if args.no_resume:
        overrides.setdefault("job", {})["resume"] = False
    if args.dry_run:
        overrides.setdefault("job", {})["dry_run"] = True
    elif args.live:
        overrides.setdefault("job", {})["dry_run"] = False
    if getattr(args, "clean_work", False):
        overrides.setdefault("job", {})["clean_work"] = True
    if args.target:
        overrides.setdefault("runtime", {})["target"] = args.target
    if args.voice:
        overrides["voice"] = args.voice
    for arg_k, cfg_k in (("tts_voice", "tts_voice"), ("tts_backend", "tts_backend"), ("hf_speaker", "hf_speaker")):
        val = getattr(args, arg_k, "")
        if val:
            if isinstance(overrides.get("voice"), str):
                overrides["voice"] = {"profile": overrides["voice"]}
            overrides.setdefault("voice", {})[cfg_k] = val
    if args.input:
        overrides.setdefault("input", {})["path"] = args.input
    if args.slides:
        overrides.setdefault("input", {})["slides"] = [
            int(s.strip()) for s in args.slides.split(",") if s.strip().isdigit()
        ]
    if args.slides_png_dir:
        overrides.setdefault("input", {})["slides_png_dir"] = args.slides_png_dir
    if args.pptx:
        overrides.setdefault("input", {})["pptx_path"] = args.pptx
    if args.audio_only:
        overrides.setdefault("output", {})["formats"] = ["wav", "mp3"]
        if not (args.steps or args.from_step or args.to_step):
            overrides["steps"] = ["prepare", "tts", "convert", "qa", "align", "render"]
    if args.steps or args.from_step or args.to_step:
        step_list = [s.strip() for s in args.steps.split(",") if s.strip()] if args.steps else None
        overrides["steps"] = resolve_step_range(steps=step_list, from_step=args.from_step, to_step=args.to_step)
    if args.voice_python:
        overrides.setdefault("runtime", {})["voice_python"] = args.voice_python
    if args.rvc_assets_root:
        overrides.setdefault("runtime", {})["rvc_assets_root"] = args.rvc_assets_root
    if args.colab_url:
        overrides.setdefault("colab", {})["agent_url"] = args.colab_url
    if args.colab_token:
        overrides.setdefault("colab", {})["agent_token"] = args.colab_token
    if args.drive_mount_dir:
        overrides.setdefault("colab", {})["local_mount_dir"] = args.drive_mount_dir

    auto_cfg = args.config
    if not auto_cfg and job_id and not args.input:
        candidate = PROJECT_ROOT / "output" / job_id / "work" / "job_spec.json"
        if candidate.is_file():
            auto_cfg = str(candidate)
            if "steps" not in overrides:
                overrides["steps"] = ["prepare", "tts", "convert", "qa", "align", "render"]

    apply_pre_run_choices(args, overrides, PROJECT_ROOT)
    cfg = load_pipeline_config(config_path=auto_cfg or None, overrides=overrides, base_dir=PROJECT_ROOT)
    orch = PipelineOrchestrator(cfg, base_dir=PROJECT_ROOT)
    orch.run()
    if getattr(args, "sync_drive", False) or getattr(args, "drive_folder", ""):
        res = sync_deliverables_to_drive(cfg, orch.job_dir, getattr(args, "drive_folder", "") or "")
        print(f"[drive-sync] Uploaded deliverables -> {json.dumps(res, ensure_ascii=False)}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    cfg = load_pipeline_config(config_path=args.config or None, base_dir=PROJECT_ROOT)
    out_root = resolve_repo_path(cfg.output_root, PROJECT_ROOT)
    if args.job:
        safe_id = validate_safe_id(args.job, label="job_id")
        st_path = out_root / safe_id / "status.json"
        if not st_path.is_file():
            print(f"[ERROR] No job status found at {st_path}", file=sys.stderr)
            return 1
        st = json.loads(st_path.read_text(encoding="utf-8"))
        print(format_job_status(st))
        return 0
    jobs = list_jobs(out_root)
    if not jobs:
        print(f"No jobs found under {out_root}")
        return 0
    for j in jobs:
        print(f"{j.get('job_id'):<36} | {str(j.get('status')):<12} | {j.get('progress_pct', 0.0):5.1f}% | voice={j.get('voice_id')}")
    return 0


def _cmd_voices(args: argparse.Namespace) -> int:
    if args.voice_id:
        try:
            vp = resolve_voice_profile(args.voice_id)
        except ValueError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
        print(json.dumps({
            "voice_id": vp.voice_id,
            "display_name": vp.display_name,
            "license": vp.license,
            "stage1": vp.stage1.__dict__,
            "stage2": vp.stage2.__dict__,
            "mastering": vp.mastering.__dict__,
        }, ensure_ascii=False, indent=2))
        return 0
    for vid, vp in list_builtin_voices().items():
        rvc_desc = f"rvc_v2:{vp.stage2.repo_id or vp.stage2.local_pth}" if vp.stage2.enabled else "none (direct)"
        tts_desc = f"{vp.stage1.backend}:{vp.stage1.hf_speaker}" if vp.stage1.backend != "edge_tts" else vp.stage1.voice
        print(f"  {vid:<14} | tts={tts_desc:<28} | rvc={rvc_desc} | {vp.display_name}")
    print("\nTip: Add any custom RVC .pth/.index or HuggingFace model via configs/custom_voice.example.yaml")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    overrides: Dict[str, Any] = {}
    if getattr(args, "voice_python", ""):
        overrides.setdefault("runtime", {})["voice_python"] = args.voice_python
    if getattr(args, "rvc_assets_root", ""):
        overrides.setdefault("runtime", {})["rvc_assets_root"] = args.rvc_assets_root
    cfg = load_pipeline_config(config_path=args.config or None, overrides=overrides or None, base_dir=PROJECT_ROOT)
    info = probe_runtime(cfg.runtime.voice_python, cfg.runtime.rvc_assets_root)
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def _apply_colab_cli_overrides(cfg: Any, args: argparse.Namespace) -> None:
    if getattr(args, "target", ""):
        cfg.target = args.target
    if getattr(args, "colab_url", ""):
        cfg.colab.agent_url = args.colab_url
        cfg.target = "colab_agent"
    if getattr(args, "colab_token", ""):
        cfg.colab.agent_token = args.colab_token
    if getattr(args, "drive_mount_dir", ""):
        cfg.colab.local_mount_dir = args.drive_mount_dir
        cfg.target = "colab_drive"


def _cmd_colab(args: argparse.Namespace) -> int:
    from voice_studio.colab.agent import start_agent_server
    from voice_studio.colab.client import ColabClient
    from voice_studio.colab.drive_bridge import authenticate_google_drive, run_colab_drive_worker

    cfg = load_pipeline_config(config_path=args.config or None, base_dir=PROJECT_ROOT)
    sub = args.colab_action
    if sub == "auth":
        target_mode = getattr(args, "target", "") or cfg.target
        if target_mode == "colab_cli":
            from voice_studio.colab.colab_cli_bridge import check_colab_cli_auth, resolve_colab_cli_binary
            from voice_studio.mcp_setup import perform_first_run_login

            oauth_code = getattr(args, "oauth_code", "") or ""
            if oauth_code:
                res = perform_first_run_login(base_dir=PROJECT_ROOT, mode="colab_cli", oauth_code=oauth_code)
                print(json.dumps(res, indent=2, ensure_ascii=False))
                return 0
            auth_info = check_colab_cli_auth(resolve_colab_cli_binary(PROJECT_ROOT), PROJECT_ROOT)
            if not auth_info["signed_in"]:
                if sys.stdin is not None and sys.stdin.isatty() and auth_info.get("binary"):
                    import subprocess

                    rc = subprocess.run([auth_info["binary"], "usage"]).returncode
                    if rc != 0:
                        return rc
                    perform_first_run_login(base_dir=PROJECT_ROOT, mode="colab_cli")
                    auth_info = check_colab_cli_auth(auth_info["binary"], PROJECT_ROOT)
                else:
                    res = perform_first_run_login(base_dir=PROJECT_ROOT, mode="colab_cli")
                    print(json.dumps(res, indent=2, ensure_ascii=False))
                    return 0 if res.get("status", {}).get("colab_cli", {}).get("signed_in") else 1
            else:
                perform_first_run_login(base_dir=PROJECT_ROOT, mode="colab_cli")
            print(json.dumps(auth_info, indent=2, ensure_ascii=False))
            return 0
        secrets = resolve_repo_path(args.secrets or cfg.colab.client_secrets_path, PROJECT_ROOT)
        token_p = resolve_repo_path(args.token_path or cfg.colab.token_path, PROJECT_ROOT)
        authenticate_google_drive(secrets, token_p)
        print(f"[colab] Google Drive OAuth2 sign-in complete. Token saved at {token_p} (0600)")
        return 0
    if sub == "ping":
        _apply_colab_cli_overrides(cfg, args)
        print(json.dumps(ColabClient(cfg, base_dir=PROJECT_ROOT).ping(), indent=2, ensure_ascii=False))
        return 0
    if sub in ("status", "download", "logs"):
        safe_id = validate_safe_id(args.job, label="job_id")
        _apply_colab_cli_overrides(cfg, args)
        client = ColabClient(cfg, base_dir=PROJECT_ROOT)
        if sub == "logs":
            res = client.fetch_logs(safe_id, offset=int(getattr(args, "offset", 0) or 0))
            print(res.get("chunk") or "(no logs yet)")
            return 0
        if sub == "status":
            st = client.fetch_status(safe_id)
            if not st:
                print(f"[colab] Job {safe_id} not found remotely.")
                return 1
            print(format_job_status(st))
            return 0
        out_dir = resolve_repo_path(cfg.output_root, PROJECT_ROOT) / safe_id
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = client.download_outputs(out_dir, safe_id)
        print(f"[colab] Downloaded & verified outputs -> {dest}")
        return 0
    if sub == "exec":
        _apply_colab_cli_overrides(cfg, args)
        res = ColabClient(cfg, base_dir=PROJECT_ROOT).exec_command(args.cmd, cwd=getattr(args, "cwd", "") or "")
        if res.get("stdout"):
            print(res["stdout"], end="" if res["stdout"].endswith("\n") else "\n")
        if res.get("stderr"):
            print(res["stderr"], file=sys.stderr, end="" if res["stderr"].endswith("\n") else "\n")
        return int(res.get("returncode", 0))
    if sub == "shell":
        _apply_colab_cli_overrides(cfg, args)
        return ColabClient(cfg, base_dir=PROJECT_ROOT).run_interactive_shell()
    if sub == "sync-drive":
        from voice_studio.colab.gdrive_cli import GdriveCliBridge

        src_dir = resolve_repo_path(args.source, PROJECT_ROOT)
        files = [p for p in sorted(src_dir.iterdir()) if p.is_file()] if src_dir.is_dir() else [src_dir]
        bridge = GdriveCliBridge(root_folder_name=cfg.colab.root_folder_name or "VoiceStudioColab")
        res = bridge.replace_folder_files(args.drive_folder or cfg.colab.root_folder_name, files, trash_prefixes=list(args.trash_prefix or []))
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    if sub == "agent":
        server, wstate = start_agent_server(
            host=args.host, port=args.port, token=args.colab_token, work_root=Path(args.workdir) if args.workdir else None
        )
        print(f"[colab-agent] Listening on http://{args.host}:{args.port} | token={redact_secret(wstate.token)} | workdir={wstate.work_root}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
        return 0
    if sub == "drive-worker":
        mount = Path(args.drive_mount_dir or cfg.colab.local_mount_dir or "/content/drive/MyDrive")
        processed = run_colab_drive_worker(
            mount,
            root_folder_name=cfg.colab.root_folder_name,
            watch=bool(args.watch),
            poll_interval_sec=float(args.poll_interval),
            idle_timeout_sec=float(args.idle_timeout),
        )
        print(f"[colab-drive-worker] Processed job(s): {processed}")
        return 0
    return 1


def _cmd_internal_step(args: argparse.Namespace) -> int:
    safe_id = validate_safe_id(args.job, label="job_id")
    cfg = load_pipeline_config(
        config_path=args.config,
        overrides={"job": {"id": safe_id}, "runtime": {"target": "local"}},
        base_dir=PROJECT_ROOT,
    )
    PipelineOrchestrator(cfg, base_dir=PROJECT_ROOT).run_single_internal_step(args.step)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Builds the clean Media Studio CLI parser."""
    parser = argparse.ArgumentParser(
        prog="studio.py",
        description="Voice & Media Studio: Whisper ASR, Edge-TTS, Gawr Gura / Custom RVC v2, Audio QA, 1080p Render & Colab",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run full workflow or selected steps locally or on Colab")
    p_run.add_argument("--config", default="", help="Path to YAML/JSON job config (defaults to configs/studio.yaml)")
    p_run.add_argument("--job", default="", help="Job ID under output/ (auto-generated if omitted)")
    p_run.add_argument("--input", default="", help="Input script (.json/.md/.txt) or media (.wav/.mp3/.mp4)")
    p_run.add_argument("--slides", default="", help="Comma-separated segment numbers (e.g. 1,2)")
    p_run.add_argument("--slides-png-dir", default="", help="Optional slide PNG directory")
    p_run.add_argument("--pptx", default="", help="Optional PPTX deck for progressive reveal extraction")
    p_run.add_argument("--voice", default="", help="Voice profile: gawr_gura | gawr_gura_hf | hf_female | direct_tts")
    p_run.add_argument("--tts-backend", default="", help="Override Stage 1 TTS backend: edge_tts | hf_kokoro_thai")
    p_run.add_argument("--hf-speaker", default="", help="Override HF Kokoro Thai speaker (e.g. f_mid_warm, f_young_bright)")
    p_run.add_argument("--tts-voice", default="", help="Override Edge-TTS voice (e.g. th-TH-PremwadeeNeural)")
    p_run.add_argument("--audio-only", action="store_true", help="Output clean WAV/MP3 deliverables without encoding MP4")
    p_run.add_argument("--steps", default="", help=f"Comma-separated steps from {list(ORDERED_STEPS)}")
    p_run.add_argument("--from-step", default="", help="Start at this step")
    p_run.add_argument("--to-step", default="", help="Stop after this step")
    p_run.add_argument("--target", choices=["local", "colab_cli", "colab_drive", "colab_agent"], default="", help="Execution target")
    p_run.add_argument("--voice-python", default="", help="Explicit GPU/ASR Python binary")
    p_run.add_argument("--rvc-assets-root", default="", help="RVC weights & Applio cache directory")
    p_run.add_argument("--colab-url", default="", help="Colab HTTP Agent base URL")
    p_run.add_argument("--colab-token", default="", help="Colab HTTP Agent Bearer token")
    p_run.add_argument("--drive-mount-dir", default="", help="Mounted Google Drive directory path")
    p_run.add_argument("--dry-run", action="store_true", help="Run offline dry-run without GPU/network")
    p_run.add_argument("--live", action="store_true", help="Force real synthesis even if saved job_spec used dry-run")
    p_run.add_argument("--no-resume", action="store_true", help="Re-run completed steps instead of resuming")
    p_run.add_argument("--clean-work", action="store_true", help="Remove intermediate staging folders once deliverables finish")
    p_run.add_argument("--gpu-tier", choices=["l4", "a100", "t4", "cpu"], default="", help="Override Colab GPU tier")
    p_run.add_argument("--high-ram", action="store_true", help="Require High-RAM Colab runtime")
    p_run.add_argument("--standard-ram", action="store_true", help="Use Standard-RAM Colab runtime")
    p_run.add_argument("--session-name", default="", help="Colab CLI session name (default: voice-studio-pro)")
    p_run.add_argument("--open-terminal", action="store_true", help="Pop open a live terminal monitor window for Colab runs")
    p_run.add_argument("--choose", action="store_true", help="Interactively choose target, GPU tier, and voice before running")
    p_run.add_argument("--sync-drive", action="store_true", help="Upload/replace final deliverables into Google Drive when finished")
    p_run.add_argument("--drive-folder", default="", help="Target Google Drive folder name/path/ID for --sync-drive")
    p_run.set_defaults(func=_cmd_run)

    p_tr = sub.add_parser("transcribe", help="Transcribe audio/video (Whisper ASR) into TXT, SRT, JSON & Studio script")
    p_tr.add_argument("--input", required=True, help="Input audio/video file (.wav, .mp3, .m4a, .mp4, etc.)")
    p_tr.add_argument("--out", default="", help="Direct output directory (omit to create tracked job under output/)")
    p_tr.add_argument("--job", default="", help="Job ID under output/ when running as a job")
    p_tr.add_argument("--config", default="", help="Optional YAML config")
    p_tr.add_argument("--backend", default="", help="ASR engine: auto | faster_whisper | openai_whisper | transformers")
    p_tr.add_argument("--model", default="", help="Whisper model ID (default: openai/whisper-small)")
    p_tr.add_argument("--language", default=None, help="Language code e.g. th, en (empty for auto-detect)")
    p_tr.add_argument("--voice-python", default="", help="Python binary with Whisper/torch installed")
    p_tr.add_argument("--via-job", action="store_true", help="Run inside tracked output/<job_id>/")
    p_tr.add_argument("--dry-run", action="store_true", help="Generate offline dry-run transcript placeholder")
    p_tr.set_defaults(func=cmd_transcribe)

    p_st = sub.add_parser("status", help="Show local job progress and artifacts")
    p_st.add_argument("--config", default="", help="Optional studio config")
    p_st.add_argument("--job", default="", help="Specific job ID (omit to list all jobs)")
    p_st.set_defaults(func=_cmd_status)

    p_vc = sub.add_parser("voices", help="List or inspect active voice profiles")
    p_vc.add_argument("voice_id", nargs="?", default="", help="Optional voice ID (gawr_gura | gawr_gura_hf | hf_female | direct_tts)")
    p_vc.set_defaults(func=_cmd_voices)

    p_doc = sub.add_parser("doctor", help="Inspect Python, CUDA GPU, ASR engines, FFmpeg, and RVC paths")
    p_doc.add_argument("--config", default="", help="Optional studio config")
    p_doc.add_argument("--voice-python", default="", help="Explicit GPU Python binary to inspect")
    p_doc.add_argument("--rvc-assets-root", default="", help="RVC weights & Applio cache directory")
    p_doc.set_defaults(func=_cmd_doctor)

    p_bnd = sub.add_parser("bundle", help="Build and CRC-verify Google Colab production ZIP bundle")
    p_bnd.add_argument("--include", action="append", default=[], help="Optional extra folder/file relative path to include")

    def _run_bundle(args: argparse.Namespace) -> int:
        from voice_studio.colab.bundle import build_production_bundle

        return build_production_bundle(PROJECT_ROOT, include_paths=list(args.include or []))

    p_bnd.set_defaults(func=_run_bundle)

    p_mod = sub.add_parser("models", help="Inspect and SHA-256 verify essential models in models/")
    p_mod.add_argument("models_action", nargs="?", choices=["status", "verify"], default="verify")
    p_mod.add_argument("--no-hash", action="store_true", help="Check sizes without SHA-256")
    p_mod.add_argument("--json", action="store_true", help="Emit JSON")
    p_mod.set_defaults(func=cmd_models)

    p_ver = sub.add_parser("verify", help="Run AST quality & hygiene verification gate")
    p_ver.set_defaults(func=cmd_verify)

    p_in = sub.add_parser("input", help="Validate script/media/PPTX inputs, view formats, or scaffold templates")
    in_sub = p_in.add_subparsers(dest="input_action", required=True)
    p_in_fmt = in_sub.add_parser("formats", help="Show all supported input formats, JSON schema, and templates")
    p_in_fmt.set_defaults(func=cmd_input)
    p_in_chk = in_sub.add_parser("check", help="Validate script/subtitle/media input and preview Thai phonetics")
    p_in_chk.add_argument("path", help="Path to .json/.md/.txt/.srt/.vtt or media file")
    p_in_chk.add_argument("--voice", default="gawr_gura", help="Voice profile for TTS preview")
    p_in_chk.add_argument("--vocab", default="", help="Optional custom phonetic vocab JSON")
    p_in_chk.set_defaults(func=cmd_input)
    p_in_init = in_sub.add_parser("init", help="Create a starter input script or custom vocab inside input/")
    p_in_init.add_argument("target", help="Relative target filename inside input/ (e.g. my_talk.json)")
    p_in_init.add_argument("--format", choices=["json", "md", "txt", "srt", "vocab"], default="")
    p_in_init.add_argument("--overwrite", action="store_true")
    p_in_init.set_defaults(func=cmd_input)

    p_cln = sub.add_parser("clean", help="Remove intermediate staging files or wipe old output/ jobs cleanly")
    p_cln.add_argument("--job", default="", help="Specific job ID to prune intermediate files")
    p_cln.add_argument("--all", action="store_true", help="Remove all old jobs in output/ and cache/")
    p_cln.add_argument("--samples", action="store_true", help="Clear generated preview artifacts under samples/")
    p_cln.add_argument("--drive", action="store_true", help="Trash stale jobs in Drive VoiceStudioColab folder")
    p_cln.add_argument("--config", default="", help="Optional studio config")
    p_cln.set_defaults(func=cmd_clean)

    p_cfg = sub.add_parser("configure", help="Inspect or update config & .env choices before running (supports --interactive)")
    p_cfg.add_argument("--show", action="store_true", help="Show effective config and Colab/Drive auth status")
    p_cfg.add_argument("--interactive", action="store_true", help="Interactive menu to choose target, GPU, RAM, voice & monitor")
    p_cfg.add_argument("--profile", choices=["colab_pro", "local"], default="colab_pro")
    p_cfg.add_argument("--target", choices=["local", "colab_cli", "colab_drive", "colab_agent"], default="")
    p_cfg.add_argument("--gpu-tier", choices=["l4", "a100", "t4", "cpu"], default="")
    p_cfg.add_argument("--high-ram", action="store_true")
    p_cfg.add_argument("--standard-ram", action="store_true")
    p_cfg.add_argument("--voice", default="", help="gawr_gura or direct_tts")
    p_cfg.add_argument("--sample-rate", type=int, default=None)
    p_cfg.add_argument("--bit-depth", type=int, default=None)
    p_cfg.add_argument("--session-name", default="")
    p_cfg.add_argument("--open-terminal", choices=["0", "1"], default="")
    p_cfg.set_defaults(func=cmd_configure)

    p_mcp = sub.add_parser("mcp", help="Run MCP server (--setup for first-run login & config, --status for check)")
    p_mcp.add_argument("--setup", action="store_true", help="Run first-run auth, register voice-studio + colab-mcp, and verify")
    p_mcp.add_argument("--login", action="store_true", help="Trigger/verify Drive/Colab authentication and save .env (0600)")
    p_mcp.add_argument("--status", action="store_true", help="Inspect auth, global mcp_config.json, and probe MCP servers")
    p_mcp.add_argument("--mode", choices=["auto", "colab_cli", "gdrive", "oauth", "agent"], default="auto")
    p_mcp.add_argument("--oauth-code", default="", help="Optional Google OAuth2 authorization code from browser sign-in")
    p_mcp.add_argument("--secrets", default="", help="Optional Google OAuth client_secrets.json")
    p_mcp.add_argument("--colab-url", default="", help="Optional Colab HTTP Agent HTTPS URL")
    p_mcp.add_argument("--colab-token", default="", help="Optional Colab HTTP Agent Bearer token")
    p_mcp.add_argument("--gpu-tier", choices=["l4", "a100", "t4"], default="", help="Update Colab Pro GPU tier")
    p_mcp.add_argument("--target", choices=["local", "colab_cli", "colab_drive", "colab_agent"], default="")
    p_mcp.add_argument("--voice", default="", help="Update default voice profile")
    p_mcp.add_argument("--profile", choices=["colab_pro", "local"], default="colab_pro")
    p_mcp.set_defaults(func=cmd_mcp)

    p_int = sub.add_parser("internal-step", help=argparse.SUPPRESS)
    p_int.add_argument("--config", required=True)
    p_int.add_argument("--job", required=True)
    p_int.add_argument("--step", required=True)
    p_int.set_defaults(func=_cmd_internal_step)

    p_col = sub.add_parser("colab", help="Google Colab sign-in, ping, live logs/shell/exec, worker agent, status, download")
    col_sub = p_col.add_subparsers(dest="colab_action", required=True)
    for act in ("auth", "ping", "status", "download", "logs", "exec", "shell", "agent", "drive-worker", "sync-drive"):
        sp = col_sub.add_parser(act)
        sp.add_argument("--config", default="", help="Optional studio config")
        if act == "sync-drive":
            sp.add_argument("--source", required=True, help="Local directory or file to upload/replace in Google Drive")
            sp.add_argument("--drive-folder", default="", help="Drive folder name, path, or folder ID")
            sp.add_argument("--trash-prefix", action="append", default=[], help="Obsolete filename prefix to trash when replacing")
        if act == "auth":
            sp.add_argument("--target", choices=["colab_cli", "colab_drive"], default="", help="Auth provider target")
            sp.add_argument("--oauth-code", default="", help="Google OAuth2 authorization code for colab_cli")
            sp.add_argument("--secrets", default="", help="Path to Google OAuth client_secrets.json")
            sp.add_argument("--token-path", default="", help="Path to store OAuth token JSON")
        if act in ("ping", "status", "download", "logs", "exec", "shell"):
            sp.add_argument("--target", choices=["colab_cli", "colab_drive", "colab_agent"], default="", help="Colab bridge target")
            sp.add_argument("--colab-url", default="", help="Colab HTTP Agent base URL")
            sp.add_argument("--colab-token", default="", help="Colab HTTP Agent Bearer token")
            sp.add_argument("--drive-mount-dir", default="", help="Mounted Google Drive directory")
        if act in ("status", "download", "logs"):
            sp.add_argument("--job", required=True, help="Remote job ID")
        if act == "logs":
            sp.add_argument("--offset", type=int, default=0, help="Byte offset to tail from")
        if act == "exec":
            sp.add_argument("--cmd", required=True, help="Shell command to execute on Colab worker")
            sp.add_argument("--cwd", default="", help="Relative working dir inside worker")
        if act == "agent":
            sp.add_argument("--host", default="127.0.0.1")
            sp.add_argument("--port", type=int, default=8765)
            sp.add_argument("--colab-token", default="", help="Bearer auth token")
            sp.add_argument("--workdir", default="", help="Worker workspace directory")
        if act == "drive-worker":
            sp.add_argument("--drive-mount-dir", default="/content/drive/MyDrive")
            sp.add_argument("--watch", action="store_true", help="Continuously watch Drive for jobs")
            sp.add_argument("--poll-interval", type=float, default=5.0, help="Poll interval (sec)")
            sp.add_argument("--idle-timeout", type=float, default=3600.0, help="Exit after N idle sec")
        sp.set_defaults(func=_cmd_colab)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    eff_argv = list(sys.argv[1:] if argv is None else argv)
    if not eff_argv:
        parser.print_help()
        print(
            "\nQuick Start:\n"
            "  python studio.py input formats                  # Inspect all supported input types & templates\n"
            "  python studio.py input init my_talk.json        # Scaffold starter script inside input/\n"
            "  python studio.py input check input/sample_neutral.json\n"
            "  python studio.py run --input input/sample_neutral.json --job demo_gura\n"
            "  python studio.py clean --job demo_gura          # Prune intermediate work folders, keep deliverables"
        )
        return 0
    args = parser.parse_args(eff_argv)
    return int(args.func(args))

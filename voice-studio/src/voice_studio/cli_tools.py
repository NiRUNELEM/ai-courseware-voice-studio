# -*- coding: utf-8 -*-
"""Additional Media Studio CLI subcommands: standalone transcribe, models verification, and AST architecture gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from voice_studio import PROJECT_ROOT
from voice_studio.config.loader import load_pipeline_config, resolve_repo_path
from voice_studio.core.models_manager import format_models_report, verify_local_models
from voice_studio.pipeline.orchestrator import PipelineOrchestrator


def cmd_transcribe(args: argparse.Namespace) -> int:
    """Runs standalone Whisper ASR transcription (TXT, SRT, JSON, and Studio script) on audio/video."""
    import os
    import subprocess
    from typing import Any, Dict
    from voice_studio.core.runtime import has_module, resolve_voice_python

    media_path = resolve_repo_path(args.input, PROJECT_ROOT)
    out_dir = resolve_repo_path(args.out, PROJECT_ROOT) if args.out else None

    if args.via_job or not out_dir:
        transcribe_ov: Dict[str, Any] = {}
        if args.backend:
            transcribe_ov["backend"] = args.backend
        if args.model:
            transcribe_ov["model"] = args.model
        if args.language is not None:
            transcribe_ov["language"] = args.language
        overrides: Dict[str, Any] = {"input": {"path": str(media_path)}, "steps": ["transcribe"]}
        if transcribe_ov:
            overrides["transcribe"] = transcribe_ov
        if args.job:
            overrides["job"] = {"id": args.job}
        if args.dry_run:
            overrides.setdefault("job", {})["dry_run"] = True
        if args.voice_python:
            overrides["runtime"] = {"voice_python": args.voice_python}
        cfg = load_pipeline_config(config_path=args.config or None, overrides=overrides, base_dir=PROJECT_ROOT)
        PipelineOrchestrator(cfg, base_dir=PROJECT_ROOT).run()
        return 0

    cfg = load_pipeline_config(config_path=args.config or None, base_dir=PROJECT_ROOT)
    tcfg = cfg.transcribe
    backend = args.backend or tcfg.backend or "auto"
    model_id = args.model or tcfg.model or "openai/whisper-small"
    language = args.language if args.language is not None else tcfg.language

    has_asr = has_module("faster_whisper") or has_module("whisper") or (has_module("transformers") and has_module("torch"))
    if not args.dry_run and not has_asr:
        voice_py = resolve_voice_python(args.voice_python or cfg.runtime.voice_python or "")
        if voice_py and os.path.abspath(voice_py) != os.path.abspath(sys.executable):
            cmd = [voice_py, str(PROJECT_ROOT / "studio.py"), "transcribe", "--input", str(media_path), "--out", str(out_dir)]
            if args.config:
                cmd.extend(["--config", str(resolve_repo_path(args.config, PROJECT_ROOT))])
            if args.backend:
                cmd.extend(["--backend", args.backend])
            if args.model:
                cmd.extend(["--model", args.model])
            if args.language is not None:
                cmd.extend(["--language", args.language])
            print(f"[transcribe] Delegating ASR -> {voice_py}")
            return int(subprocess.run(cmd, cwd=str(PROJECT_ROOT)).returncode)

    from voice_studio.audio.transcribe import transcribe_audio

    res = transcribe_audio(
        media_path=media_path,
        output_dir=out_dir,
        backend=backend,
        model=model_id,
        language=language,
        beam_size=tcfg.beam_size,
        vad_filter=tcfg.vad_filter,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """Inspects or SHA-256 verifies essential models/ contents."""
    fast = getattr(args, "no_hash", False) or (args.models_action == "status")
    report = verify_local_models(base_dir=PROJECT_ROOT, verify_checksums=not fast)
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_models_report(report))
    return 0 if report.get("passed") else 1


def cmd_verify(args: argparse.Namespace) -> int:
    """Runs the 5-Gate Polyglot AST architecture verification gate cleanly."""
    src_dir = PROJECT_ROOT / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    from quality.verify_architecture import print_architecture_report, scan_workspace

    rep = scan_workspace(PROJECT_ROOT)
    print_architecture_report(rep)
    return 0 if rep["passed"] else 1


def cmd_mcp(args: argparse.Namespace) -> int:
    """Runs MCP Stdio Server or handles first-run setup, auth, status, and config."""
    from voice_studio.mcp_server import serve_mcp_stdio
    from voice_studio.mcp_setup import (
        inspect_auth_and_mcp_status,
        perform_first_run_login,
        probe_stdio_mcp_server,
        resolve_colab_mcp_command,
        update_studio_config,
    )

    if getattr(args, "status", False):
        report = inspect_auth_and_mcp_status(base_dir=PROJECT_ROOT)
        report["voice_studio_probe"] = probe_stdio_mcp_server(sys.executable, [str(PROJECT_ROOT / "studio.py"), "mcp"])
        c_cmd, c_args = resolve_colab_mcp_command(PROJECT_ROOT)
        if c_cmd:
            report["colab_mcp_probe"] = probe_stdio_mcp_server(c_cmd, c_args)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if getattr(args, "setup", False) or getattr(args, "login", False):
        res = perform_first_run_login(
            base_dir=PROJECT_ROOT,
            mode=getattr(args, "mode", "auto") or "auto",
            secrets_path=getattr(args, "secrets", "") or "",
            colab_url=getattr(args, "colab_url", "") or "",
            colab_token=getattr(args, "colab_token", "") or "",
            oauth_code=getattr(args, "oauth_code", "") or "",
        )
        cfg_updates = {
            k: v
            for k, v in (
                ("target", getattr(args, "target", "") or None),
                ("gpu_tier", getattr(args, "gpu_tier", "") or None),
                ("voice", getattr(args, "voice", "") or None),
            )
            if v is not None
        }
        if cfg_updates:
            res["config_update"] = update_studio_config(
                profile=getattr(args, "profile", "colab_pro") or "colab_pro",
                updates=cfg_updates,
                base_dir=PROJECT_ROOT,
            )
        res["voice_studio_probe"] = probe_stdio_mcp_server(sys.executable, [str(PROJECT_ROOT / "studio.py"), "mcp"])
        c_cmd, c_args = resolve_colab_mcp_command(PROJECT_ROOT)
        if c_cmd:
            res["colab_mcp_probe"] = probe_stdio_mcp_server(c_cmd, c_args)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    return serve_mcp_stdio(base_dir=PROJECT_ROOT)


def cmd_input(args: argparse.Namespace) -> int:
    """Validates scripts/media/PPTX inputs, prints format guide, or scaffolds templates inside `input/`."""
    from voice_studio.content.validator import describe_input_formats, init_input_file, validate_input_source

    act = args.input_action
    if act == "formats":
        print(json.dumps(describe_input_formats(PROJECT_ROOT), indent=2, ensure_ascii=False))
        return 0
    if act == "init":
        created = init_input_file(
            target_name=args.target,
            fmt=getattr(args, "format", "") or "",
            overwrite=bool(getattr(args, "overwrite", False)),
            base_dir=PROJECT_ROOT,
        )
        print(f"[input] Created template -> {created}")
        return 0
    report = validate_input_source(
        input_path=args.path,
        voice=getattr(args, "voice", "gawr_gura") or "gawr_gura",
        extra_vocab_path=getattr(args, "vocab", "") or "",
        base_dir=PROJECT_ROOT,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("valid") else 1


def cmd_clean(args: argparse.Namespace) -> int:
    """Cleans intermediate pipeline artifacts or clears stale output/cache/sample/Drive workspaces."""
    import shutil
    from voice_studio.core.security import validate_safe_id
    from voice_studio.core.state import cleanup_intermediate_job_dirs

    cfg = load_pipeline_config(config_path=args.config or None, base_dir=PROJECT_ROOT)
    out_root = resolve_repo_path(cfg.output_root, PROJECT_ROOT)
    report: Dict[str, Any] = {}
    if getattr(args, "all", False):
        removed = []
        if out_root.is_dir():
            for child in sorted(out_root.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    removed.append(child.name)
        cache_dir = PROJECT_ROOT / "cache"
        if cache_dir.is_dir():
            for c in sorted(cache_dir.iterdir()):
                if c.name != ".gitkeep":
                    shutil.rmtree(c, ignore_errors=True) if c.is_dir() else c.unlink(missing_ok=True)
        report["cleaned_jobs"] = removed
        report["output_root"] = str(out_root)
    if getattr(args, "samples", False):
        wiped_samples = []
        samples_root = PROJECT_ROOT / "samples"
        if samples_root.is_dir():
            for child in sorted(samples_root.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    wiped_samples.append(child.name)
        report["cleaned_samples"] = wiped_samples
    if getattr(args, "drive", False):
        from voice_studio.colab.gdrive_cli import GdriveCliBridge

        bridge = GdriveCliBridge(root_folder_name=cfg.colab.root_folder_name or "VoiceStudioColab")
        report["cleaned_drive_items"] = bridge.clean_studio_folder()
    if report:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    job_id = validate_safe_id(args.job, label="job_id") if getattr(args, "job", "") else ""
    targets = [out_root / job_id] if job_id else [p for p in sorted(out_root.iterdir()) if p.is_dir()] if out_root.is_dir() else []
    summary = {j.name: cleanup_intermediate_job_dirs(j) for j in targets if j.is_dir()}
    print(json.dumps({"cleaned_intermediate": summary}, indent=2, ensure_ascii=False))
    return 0


def _prompt_choice(label: str, choices: List[str], default: str) -> str:
    raw = input(f"{label} [{'/'.join(choices)}] (default={default}): ").strip().lower()
    return raw if raw in choices else default


def cmd_configure(args: argparse.Namespace) -> int:
    """Views or updates Voice Studio YAML configs and .env choices interactively or via CLI flags."""
    import os
    from voice_studio.mcp_setup import _update_dotenv, inspect_auth_and_mcp_status, update_studio_config

    profile = getattr(args, "profile", "colab_pro") or "colab_pro"
    cfg_file = PROJECT_ROOT / "configs" / ("colab_pro.yaml" if profile in ("colab", "colab_pro") else "studio.yaml")
    updates: Dict[str, Any] = {}
    env_updates: Dict[str, str] = {}

    if getattr(args, "interactive", False):
        cur = load_pipeline_config(config_path=str(cfg_file), base_dir=PROJECT_ROOT)
        updates["target"] = _prompt_choice("Execution target", ["colab_cli", "local", "colab_drive", "colab_agent"], cur.target)
        updates["gpu_tier"] = _prompt_choice("Colab GPU tier", ["l4", "a100", "t4", "cpu"], cur.runtime.gpu_tier)
        hr_ans = _prompt_choice("High-RAM runtime", ["true", "false"], "true" if cur.runtime.high_ram else "false")
        updates["high_ram"] = hr_ans == "true"
        updates["voice"] = _prompt_choice("Voice profile", ["gawr_gura", "direct_tts"], cur.voice)
        term_ans = _prompt_choice("Live terminal monitor window", ["1", "0"], os.environ.get("VOICE_STUDIO_OPEN_TERMINAL", "0"))
        env_updates["VOICE_STUDIO_OPEN_TERMINAL"] = term_ans
    else:
        for flag, key in (
            ("target", "target"),
            ("gpu_tier", "gpu_tier"),
            ("voice", "voice"),
            ("session_name", "session_name"),
            ("sample_rate", "sample_rate"),
            ("bit_depth", "bit_depth"),
        ):
            val = getattr(args, flag, None)
            if val not in ("", None):
                updates[key] = val
        if getattr(args, "high_ram", False):
            updates["high_ram"] = True
        elif getattr(args, "standard_ram", False):
            updates["high_ram"] = False
        if getattr(args, "open_terminal", "") in ("0", "1"):
            env_updates["VOICE_STUDIO_OPEN_TERMINAL"] = str(args.open_terminal)
        if getattr(args, "session_name", ""):
            env_updates["COLAB_CLI_SESSION_NAME"] = str(args.session_name)

    res: Dict[str, Any] = {"profile": profile, "config_path": str(cfg_file)}
    if updates:
        res["yaml_update"] = update_studio_config(profile=profile, updates=updates, base_dir=PROJECT_ROOT)
    if env_updates:
        _update_dotenv(PROJECT_ROOT / ".env", env_updates)
        res["env_updated"] = sorted(env_updates.keys())
    eff = load_pipeline_config(config_path=str(cfg_file), base_dir=PROJECT_ROOT)
    res["effective"] = {
        "target": eff.target,
        "gpu_tier": eff.runtime.gpu_tier,
        "high_ram": eff.runtime.high_ram,
        "voice": eff.voice,
        "sample_rate": eff.voice_profile.mastering.sample_rate if eff.voice_profile else 40000,
        "bit_depth": eff.voice_profile.mastering.bit_depth if eff.voice_profile else 16,
        "session_name": eff.colab.session_name,
        "open_terminal": os.environ.get("VOICE_STUDIO_OPEN_TERMINAL", "0"),
    }
    if getattr(args, "show", False) or (not updates and not env_updates):
        res["auth"] = inspect_auth_and_mcp_status(base_dir=PROJECT_ROOT)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def apply_pre_run_choices(args: argparse.Namespace, overrides: Dict[str, Any], base_dir: Path) -> None:
    """Applies pre-run CLI flags or interactive TTY choices into run overrides."""
    import os

    if getattr(args, "choose", False) and sys.stdin is not None and sys.stdin.isatty():
        cur_target = str(overrides.get("runtime", {}).get("target") or "colab_cli")
        cur_gpu = str(overrides.get("runtime", {}).get("gpu_tier") or "l4")
        cur_voice = str(overrides.get("voice") if isinstance(overrides.get("voice"), str) else "gawr_gura")
        overrides.setdefault("runtime", {})["target"] = _prompt_choice(
            "Select execution target", ["colab_cli", "local", "colab_drive", "colab_agent"], cur_target
        )
        if overrides["runtime"]["target"] == "colab_cli":
            overrides["runtime"]["gpu_tier"] = _prompt_choice("Select Colab GPU", ["l4", "a100", "t4"], cur_gpu)
            overrides["runtime"]["high_ram"] = _prompt_choice("Enable High-RAM", ["true", "false"], "true") == "true"
        overrides["voice"] = _prompt_choice("Select voice profile", ["gawr_gura", "direct_tts"], cur_voice)

    if getattr(args, "gpu_tier", ""):
        overrides.setdefault("runtime", {})["gpu_tier"] = args.gpu_tier
    if getattr(args, "high_ram", False):
        overrides.setdefault("runtime", {})["high_ram"] = True
    elif getattr(args, "standard_ram", False):
        overrides.setdefault("runtime", {})["high_ram"] = False
    if getattr(args, "session_name", ""):
        overrides.setdefault("colab", {})["session_name"] = args.session_name
    if getattr(args, "open_terminal", False):
        os.environ["VOICE_STUDIO_OPEN_TERMINAL"] = "1"


def sync_deliverables_to_drive(cfg: Any, job_dir: Path, folder_spec: str, trash_prefixes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Uploads/replaces finished deliverables and timestamped ZIP into Google Drive cleanly."""
    from voice_studio.colab.gdrive_cli import GdriveCliBridge

    bridge = GdriveCliBridge(root_folder_name=cfg.colab.root_folder_name or "VoiceStudioColab")
    deliv_dir = job_dir / "deliverables"
    files_to_upload: List[Path] = []
    if deliv_dir.is_dir():
        for ext in ("*.mp4", "*.wav", "*.mp3", "*.pdf"):
            files_to_upload.extend(sorted(deliv_dir.glob(ext)))
    ts_zips = sorted(job_dir.glob(f"{cfg.job_id}_deliverables_*.zip"))
    if ts_zips:
        files_to_upload.append(ts_zips[-1])
    elif (job_dir / "deliverables_bundle.zip").is_file():
        files_to_upload.append(job_dir / "deliverables_bundle.zip")
    target = folder_spec or f"{cfg.colab.root_folder_name or 'VoiceStudioColab'}/{cfg.job_id}"
    return bridge.replace_folder_files(target, files_to_upload, trash_prefixes=trash_prefixes)

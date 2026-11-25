# -*- coding: utf-8 -*-
"""Stdio JSON-RPC 2.0 Model Context Protocol (MCP) server for Voice & Media Studio."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from voice_studio import PROJECT_ROOT
from voice_studio.colab.bundle import build_production_bundle
from voice_studio.colab.client import ColabClient
from voice_studio.config.loader import load_pipeline_config, resolve_repo_path, resolve_step_range
from voice_studio.config.voices import list_builtin_voices, resolve_voice_profile
from voice_studio.core.models_manager import verify_local_models
from voice_studio.core.runtime import probe_runtime
from voice_studio.core.security import validate_safe_id
from voice_studio.core.state import list_jobs
from voice_studio.pipeline.orchestrator import PipelineOrchestrator

MCP_PROTOCOL_VERSION = "2024-11-05"

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "studio_doctor",
        "description": "Inspect local Python, CUDA GPU, FFmpeg/NVENC, Whisper ASR backends, and RVC directories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {"type": "string", "description": "Optional config YAML/JSON path"},
                "voice_python": {"type": "string", "description": "Optional Python executable with PyTorch/Whisper"},
            },
        },
    },
    {
        "name": "studio_voices",
        "description": "List available voice profiles or inspect a specific voice profile configuration.",
        "inputSchema": {
            "type": "object",
            "properties": {"voice_id": {"type": "string", "description": "Optional voice ID (e.g. gawr_gura, direct_tts)"}},
        },
    },
    {
        "name": "studio_models_verify",
        "description": "Verify local RVC weights and Applio models in models/manifest.json.",
        "inputSchema": {
            "type": "object",
            "properties": {"no_hash": {"type": "boolean", "description": "Skip SHA-256 checksum check"}},
        },
    },
    {
        "name": "studio_status",
        "description": "Get progress and artifacts for a Voice & Media Studio job or list all jobs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Optional job ID under output/"},
                "config_path": {"type": "string", "description": "Optional config path"},
            },
        },
    },
    {
        "name": "studio_transcribe",
        "description": "Transcribe audio or video into TXT, SRT, JSON, and Studio script using Whisper ASR.",
        "inputSchema": {
            "type": "object",
            "required": ["input_path"],
            "properties": {
                "input_path": {"type": "string", "description": "Path to audio/video file"},
                "out_dir": {"type": "string", "description": "Direct output directory"},
                "config_path": {"type": "string", "description": "Optional config path (e.g. configs/colab_pro.yaml)"},
                "backend": {"type": "string"},
                "model": {"type": "string"},
                "language": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
        },
    },
    {
        "name": "studio_run",
        "description": "Execute Voice & Media Studio workflow (prepare, tts, convert, qa, align, render) locally or on Colab.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {"type": "string", "description": "YAML/JSON config path (configs/studio.yaml, configs/colab_pro.yaml, or external project job YAML)"},
                "input_path": {"type": "string", "description": "Input script (.json/.md/.txt/.srt/.vtt) or audio/video path"},
                "job_id": {"type": "string"},
                "voice": {"type": "string", "description": "Voice profile (e.g. gawr_gura, direct_tts)"},
                "target": {"type": "string", "enum": ["local", "colab_cli", "colab_drive", "colab_agent"]},
                "steps": {"type": "array", "items": {"type": "string"}},
                "audio_only": {"type": "boolean"},
                "clean_work": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
                "colab_url": {"type": "string"},
                "colab_token": {"type": "string"},
                "drive_mount_dir": {"type": "string"},
            },
        },
    },
    {
        "name": "studio_bundle",
        "description": "Build and CRC-verify exports/voice_studio_colab_bundle.zip for Google Colab deployment.",
        "inputSchema": {
            "type": "object",
            "properties": {"include_paths": {"type": "array", "items": {"type": "string"}}},
        },
    },
    {
        "name": "studio_colab_ping",
        "description": "Check connectivity and GPU health of a configured Google Colab HTTP Agent or Drive bridge.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {"type": "string"},
                "target": {"type": "string", "enum": ["colab_cli", "colab_drive", "colab_agent"]},
                "ensure_runtime": {"type": "boolean", "description": "Disconnect mismatched runtime and provision configured GPU tier before reporting"},
                "colab_url": {"type": "string"},
                "colab_token": {"type": "string"},
                "drive_mount_dir": {"type": "string"},
            },
        },
    },
    {
        "name": "studio_colab_download",
        "description": "Fetch status or download completed deliverables for a job from Colab bridge with SHA-256 verification.",
        "inputSchema": {
            "type": "object",
            "required": ["job_id"],
            "properties": {
                "job_id": {"type": "string"},
                "config_path": {"type": "string"},
                "target": {"type": "string", "enum": ["colab_cli", "colab_drive", "colab_agent"]},
                "ensure_runtime": {"type": "boolean", "description": "Disconnect mismatched runtime and provision configured GPU tier before reporting"},
                "colab_url": {"type": "string"},
                "colab_token": {"type": "string"},
                "drive_mount_dir": {"type": "string"},
            },
        },
    },
    {
        "name": "studio_colab_logs",
        "description": "Read incremental Live Terminal log output for a running or completed Colab job.",
        "inputSchema": {
            "type": "object",
            "required": ["job_id"],
            "properties": {
                "job_id": {"type": "string"},
                "offset": {"type": "integer"},
                "config_path": {"type": "string"},
                "target": {"type": "string", "enum": ["colab_cli", "colab_drive", "colab_agent"]},
                "ensure_runtime": {"type": "boolean", "description": "Disconnect mismatched runtime and provision configured GPU tier before reporting"},
                "colab_url": {"type": "string"},
                "colab_token": {"type": "string"},
            },
        },
    },
    {
        "name": "studio_colab_exec",
        "description": "Execute a shell or diagnostic command directly on the connected Colab CLI or HTTP Agent runtime.",
        "inputSchema": {
            "type": "object",
            "required": ["command"],
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout_sec": {"type": "number"},
                "config_path": {"type": "string"},
                "target": {"type": "string", "enum": ["colab_cli", "colab_agent"]},
                "colab_url": {"type": "string"},
                "colab_token": {"type": "string"},
            },
        },
    },
    {
        "name": "studio_auth_status",
        "description": "Inspect authentication state for Colab CLI, Drive, Agent, and MCP registration.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "studio_auth_login",
        "description": "Perform first-run authentication setup (colab_cli / Google Drive OAuth2 / Agent) and sync MCP config.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["auto", "colab_cli", "gdrive", "oauth", "agent"]},
                "oauth_code": {"type": "string", "description": "Optional Google OAuth2 authorization code from browser sign-in to complete colab_cli login via MCP"},
                "secrets_path": {"type": "string"},
                "colab_url": {"type": "string"},
                "colab_token": {"type": "string"},
            },
        },
    },
    {
        "name": "studio_config",
        "description": "Inspect or persist Studio/Colab Pro configuration settings cleanly (GPU tier, target, bit depth, RVC FP32).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {"type": "string", "enum": ["local", "colab_pro"]},
                "target": {"type": "string"},
                "gpu_tier": {"type": "string", "enum": ["l4", "a100", "t4"]},
                "high_ram": {"type": "boolean"},
                "voice": {"type": "string"},
                "sample_rate": {"type": "integer"},
                "bit_depth": {"type": "integer"},
                "resampler_precision": {"type": "integer"},
                "rvc_fp32": {"type": "boolean"},
                "intermediate_float": {"type": "boolean"},
                "colab_url": {"type": "string"},
                "colab_token": {"type": "string"},
            },
        },
    },
    {
        "name": "studio_prepare_preview",
        "description": "Preview Thai phonetic normalization, hyphen preservation, and TTS strings before synthesis.",
        "inputSchema": {
            "type": "object",
            "required": ["input_or_text"],
            "properties": {
                "input_or_text": {"type": "string"},
                "voice": {"type": "string"},
                "vocab_path": {"type": "string"},
                "is_inline_text": {"type": "boolean"},
            },
        },
    },
]


def _tool_doctor(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    cfg = load_pipeline_config(config_path=args.get("config_path") or None, base_dir=base_dir)
    return probe_runtime(str(args.get("voice_python") or cfg.runtime.voice_python or ""), cfg.runtime.rvc_assets_root)


def _tool_voices(args: Dict[str, Any], _base_dir: Path) -> Dict[str, Any]:
    vid = str(args.get("voice_id") or "").strip()
    if vid:
        vp = resolve_voice_profile(vid)
        return {"voice_id": vp.voice_id, "display_name": vp.display_name, "stage1": vp.stage1.__dict__, "stage2": vp.stage2.__dict__, "mastering": vp.mastering.__dict__}
    return {"voices": {k: {"display_name": v.display_name, "rvc_enabled": v.stage2.enabled} for k, v in list_builtin_voices().items()}}


def _tool_models_verify(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    return verify_local_models(base_dir=base_dir, verify_checksums=not bool(args.get("no_hash", False)))


def _tool_status(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    cfg = load_pipeline_config(config_path=args.get("config_path") or None, base_dir=base_dir)
    out_root = resolve_repo_path(cfg.output_root, base_dir)
    job_id = str(args.get("job_id") or "").strip()
    if job_id:
        safe_id = validate_safe_id(job_id, label="job_id")
        st_path = out_root / safe_id / "status.json"
        if not st_path.is_file():
            raise FileNotFoundError(f"Job status not found: {st_path}")
        return json.loads(st_path.read_text(encoding="utf-8"))
    return {"jobs": list_jobs(out_root)}


def _tool_transcribe(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    from voice_studio.audio.transcribe import transcribe_audio

    media_path = resolve_repo_path(str(args["input_path"]), base_dir)
    cfg = load_pipeline_config(config_path=args.get("config_path") or None, base_dir=base_dir)
    tcfg = cfg.transcribe
    out_dir = resolve_repo_path(str(args["out_dir"]), base_dir) if args.get("out_dir") else resolve_repo_path(cfg.output_root, base_dir) / f"tr_{validate_safe_id(media_path.stem, label='job_id')}"
    return transcribe_audio(
        media_path=media_path,
        output_dir=out_dir,
        backend=str(args.get("backend") or tcfg.backend or "auto"),
        model=str(args.get("model") or tcfg.model or "openai/whisper-small"),
        language=str(args["language"]) if "language" in args and args["language"] is not None else tcfg.language,
        beam_size=tcfg.beam_size,
        vad_filter=tcfg.vad_filter,
        dry_run=bool(args.get("dry_run", False)),
    )


def _tool_run(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    if args.get("job_id"):
        overrides.setdefault("job", {})["id"] = validate_safe_id(str(args["job_id"]), label="job_id")
    if "dry_run" in args:
        overrides.setdefault("job", {})["dry_run"] = bool(args["dry_run"])
    if args.get("clean_work"):
        overrides.setdefault("job", {})["clean_work"] = True
    if args.get("input_path"):
        overrides.setdefault("input", {})["path"] = str(args["input_path"])
    if args.get("voice"):
        overrides["voice"] = str(args["voice"])
    if args.get("target"):
        overrides.setdefault("runtime", {})["target"] = str(args["target"])
    if args.get("audio_only"):
        overrides.setdefault("output", {})["formats"] = ["wav", "mp3"]
    if args.get("steps"):
        overrides["steps"] = resolve_step_range(steps=[str(s) for s in args["steps"]])
    for src_k, dst_k in (("colab_url", "agent_url"), ("colab_token", "agent_token"), ("drive_mount_dir", "local_mount_dir")):
        if args.get(src_k):
            overrides.setdefault("colab", {})[dst_k] = str(args[src_k])
    cfg = load_pipeline_config(config_path=args.get("config_path") or None, overrides=overrides or None, base_dir=base_dir)
    return PipelineOrchestrator(cfg, base_dir=base_dir).run()


def _tool_bundle(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    rc = build_production_bundle(base_dir, include_paths=list(args.get("include_paths") or []))
    return {"exit_code": rc, "bundle_path": str(base_dir / "exports/voice_studio_colab_bundle.zip")}


def _colab_client_from_args(args: Dict[str, Any], base_dir: Path) -> tuple[ColabClient, Any]:
    overrides: Dict[str, Any] = {}
    if args.get("target"):
        overrides.setdefault("runtime", {})["target"] = str(args["target"])
    for src_k, dst_k in (("colab_url", "agent_url"), ("colab_token", "agent_token"), ("drive_mount_dir", "local_mount_dir")):
        if args.get(src_k):
            overrides.setdefault("colab", {})[dst_k] = str(args[src_k])
    cfg = load_pipeline_config(config_path=args.get("config_path") or None, overrides=overrides or None, base_dir=base_dir)
    return ColabClient(cfg, base_dir=base_dir), cfg


def _tool_colab_ping(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    client, _ = _colab_client_from_args(args, base_dir)
    return client.ping(ensure=bool(args.get("ensure_runtime", False)))


def _tool_colab_download(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    safe_id = validate_safe_id(str(args["job_id"]), label="job_id")
    client, cfg = _colab_client_from_args(args, base_dir)
    out_dir = resolve_repo_path(cfg.output_root, base_dir) / safe_id
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = client.download_outputs(out_dir, safe_id)
    return {"job_id": safe_id, "output_dir": str(dest), "status": client.fetch_status(safe_id)}


def _tool_colab_logs(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    safe_id = validate_safe_id(str(args["job_id"]), label="job_id")
    client, _ = _colab_client_from_args(args, base_dir)
    return client.fetch_logs(safe_id, offset=int(args.get("offset") or 0))


def _tool_colab_exec(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    client, cfg = _colab_client_from_args(args, base_dir)
    if client._effective_target() not in ("colab_cli", "colab_agent"):
        exec_target = "colab_agent" if cfg.colab.agent_url else "colab_cli"
        client, _ = _colab_client_from_args({**args, "target": exec_target}, base_dir)
    return client.exec_command(
        command=str(args["command"]),
        cwd=str(args.get("cwd") or ""),
        timeout_sec=float(args.get("timeout_sec") or 120.0),
    )


def _tool_auth_status(_args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    from voice_studio.mcp_setup import inspect_auth_and_mcp_status

    return inspect_auth_and_mcp_status(base_dir=base_dir)


def _tool_auth_login(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    from voice_studio.mcp_setup import perform_first_run_login

    return perform_first_run_login(
        base_dir=base_dir,
        mode=str(args.get("mode") or "auto"),
        secrets_path=str(args.get("secrets_path") or ""),
        colab_url=str(args.get("colab_url") or ""),
        colab_token=str(args.get("colab_token") or ""),
        oauth_code=str(args.get("oauth_code") or ""),
    )


def _tool_config(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    from voice_studio.mcp_setup import update_studio_config

    profile = str(args.get("profile") or "colab_pro")
    updates = {k: v for k, v in args.items() if k != "profile"}
    return update_studio_config(profile=profile, updates=updates, base_dir=base_dir)


def _tool_prepare_preview(args: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    from voice_studio.content.loader import preview_prepared_content

    return preview_prepared_content(
        input_path_or_text=str(args["input_or_text"]),
        voice=str(args.get("voice") or "gawr_gura"),
        is_inline_text=bool(args.get("is_inline_text", False)),
        base_dir=base_dir,
        vocab_path=str(args.get("vocab_path") or ""),
    )


TOOL_HANDLERS: Dict[str, Callable[[Dict[str, Any], Path], Dict[str, Any]]] = {
    "studio_doctor": _tool_doctor,
    "studio_voices": _tool_voices,
    "studio_models_verify": _tool_models_verify,
    "studio_status": _tool_status,
    "studio_transcribe": _tool_transcribe,
    "studio_run": _tool_run,
    "studio_bundle": _tool_bundle,
    "studio_colab_ping": _tool_colab_ping,
    "studio_colab_download": _tool_colab_download,
    "studio_colab_logs": _tool_colab_logs,
    "studio_colab_exec": _tool_colab_exec,
    "studio_auth_status": _tool_auth_status,
    "studio_auth_login": _tool_auth_login,
    "studio_config": _tool_config,
    "studio_prepare_preview": _tool_prepare_preview,
}


def handle_jsonrpc_request(msg: Dict[str, Any], base_dir: Path = PROJECT_ROOT) -> Optional[Dict[str, Any]]:
    """Dispatches a single MCP JSON-RPC 2.0 request and returns response dict (None for notifications)."""
    method = str(msg.get("method") or "")
    req_id = msg.get("id")
    params = dict(msg.get("params") or {})

    if req_id is None and method.startswith("notifications/"):
        return None
    if method == "initialize":
        result: Any = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "voice-studio", "version": "1.0.0"},
        }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOL_DEFINITIONS}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = dict(params.get("arguments") or {})
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool: {name}"}}
        try:
            with contextlib.redirect_stdout(sys.stderr):
                payload = handler(arguments, base_dir)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}], "isError": False},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True},
            }
    if req_id is None:
        return None
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not supported: {method}"}}


def _emit_stdio_response(resp: Dict[str, Any], use_content_length: bool) -> None:
    payload = json.dumps(resp, ensure_ascii=False).encode("utf-8")
    if use_content_length:
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        sys.stdout.buffer.write(header + payload)
    else:
        sys.stdout.buffer.write(payload + b"\n")
    sys.stdout.buffer.flush()


def serve_mcp_stdio(base_dir: Path = PROJECT_ROOT) -> int:
    """Runs dual-framing (NDJSON and Content-Length) JSON-RPC 2.0 MCP server loop over stdin/stdout."""
    buf = sys.stdin.buffer
    while True:
        raw_line = buf.readline()
        if not raw_line:
            break
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        use_cl = line.lower().startswith("content-length:")
        req_id: Any = None
        try:
            if use_cl:
                length = int(line.split(":", 1)[1].strip())
                while True:
                    hdr = buf.readline()
                    if not hdr or hdr in (b"\r\n", b"\n"):
                        break
                msg = json.loads(buf.read(length).decode("utf-8"))
            else:
                msg = json.loads(line)
            req_id = msg.get("id")
            resp = handle_jsonrpc_request(msg, base_dir=base_dir)
        except Exception as exc:
            resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32700, "message": str(exc)}}
        if resp is not None:
            _emit_stdio_response(resp, use_content_length=use_cl)
    return 0

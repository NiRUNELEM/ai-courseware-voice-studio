# -*- coding: utf-8 -*-
"""JSON Schema definitions for all 16 Voice & Media Studio MCP tools."""

from __future__ import annotations

from typing import Any, Dict, List

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
        "description": "List available voice profiles and EQ presets, or inspect a specific voice profile configuration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "voice_id": {"type": "string", "description": "Optional voice ID (e.g. gawr_gura_hybrid, gawr_gura, direct_tts, hf_female)"},
            },
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
        "description": "Execute Voice & Media Studio workflow (prepare, tts, convert, qa, align, render) locally or on Colab with full custom voice/DSP/video overrides.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {"type": "string", "description": "YAML/JSON config path (configs/studio.yaml, configs/colab_pro.yaml, or external job YAML)"},
                "input_path": {"type": "string", "description": "Input script (.json/.md/.txt/.srt/.vtt) or audio/video path"},
                "job_id": {"type": "string"},
                "voice": {"type": "string", "description": "Voice profile (e.g. gawr_gura_hybrid, gawr_gura, direct_tts, custom_rvc)"},
                "target": {"type": "string", "enum": ["local", "colab_cli", "colab_drive", "colab_agent"]},
                "steps": {"type": "array", "items": {"type": "string"}},
                "slides": {"type": "array", "items": {"type": "integer"}, "description": "Optional subset of slide numbers to render (e.g. [1, 2, 3])"},
                "extra_vocab_path": {"type": "string", "description": "Optional custom phonetic vocab JSON path"},
                "voice_overrides": {"type": "object", "description": "Custom voice/TTS/RVC overrides (e.g. {tts_backend, rvc: {pitch_shift, f0_stabilizer, local_pth}})"},
                "output_overrides": {"type": "object", "description": "Custom mastering/video overrides (e.g. {eq_preset, eq_bands, highpass_hz, limiter_db, video_codec, progress_bar})"},
                "audio_only": {"type": "boolean"},
                "resume": {"type": "boolean"},
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
        "description": "Check connectivity and GPU health of a configured Google Colab CLI, HTTP Agent, or Drive bridge.",
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
                "ensure_runtime": {"type": "boolean"},
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
        "name": "studio_colab_stop",
        "description": "Stop the active Google Colab CLI GPU session immediately to prevent idle Compute Unit consumption.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_name": {"type": "string", "description": "Optional Colab session name (defaults to configured colab.session_name)"},
                "config_path": {"type": "string"},
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
        "description": "Inspect or persist Studio/Colab Pro configuration settings (GPU tier, target, DSP EQ/gate/limiter, RVC FP32/F0 stabilizer, video codec).",
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
                "target_lufs": {"type": "number"},
                "highpass_hz": {"type": "integer"},
                "lowpass_hz": {"type": "integer"},
                "eq_preset": {"type": "string", "enum": ["anime_female", "warm_male", "neutral", "off"]},
                "noise_gate": {"type": "boolean"},
                "noise_gate_threshold": {"type": "number"},
                "gate_ratio": {"type": "number"},
                "gate_release_ms": {"type": "number"},
                "limiter_db": {"type": "number"},
                "pre_rvc_conditioning": {"type": "boolean"},
                "f0_stabilizer": {"type": "boolean"},
                "rvc_fp32": {"type": "boolean"},
                "intermediate_float": {"type": "boolean"},
                "video_codec": {"type": "string", "enum": ["auto", "hevc", "h264", "hevc_nvenc", "h264_nvenc", "libx264", "libx265"]},
                "progress_bar": {"type": "boolean"},
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

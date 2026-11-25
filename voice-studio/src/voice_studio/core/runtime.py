# -*- coding: utf-8 -*-
"""Runtime environment probing (CUDA GPU, FFmpeg, ASR engines, voice Python worker, and RVC directories)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from voice_studio import PROJECT_ROOT


def has_module(module_name: str) -> bool:
    """Checks whether a Python module is importable in the current interpreter."""
    return importlib.util.find_spec(module_name) is not None


def probe_ffmpeg() -> Dict[str, Any]:
    """Detects FFmpeg binary and probes usable NVENC hardware encoding."""
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    if not ffmpeg_bin:
        return {"ffmpeg": False, "ffprobe": bool(ffprobe_bin), "nvenc": False}
    nvenc = False
    try:
        res = subprocess.run([ffmpeg_bin, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False)
        listed = [enc for enc in ("hevc_nvenc", "h264_nvenc") if enc in res.stdout]
        if listed:
            with tempfile.NamedTemporaryFile(suffix=".mp4") as probe_out:
                for enc in listed:
                    probe_cmd = [
                        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "color=c=black:s=128x128:d=0.05",
                        "-frames:v", "2", "-c:v", enc, "-pix_fmt", "yuv420p", probe_out.name,
                    ]
                    p_res = subprocess.run(probe_cmd, capture_output=True, check=False)
                    if p_res.returncode == 0 and os.path.getsize(probe_out.name) > 0:
                        nvenc = True
                        break
    except OSError as exc:
        print(f"[runtime] ffmpeg encoder probe warning: {exc}")
    return {"ffmpeg": True, "ffprobe": bool(ffprobe_bin), "nvenc": nvenc}


def probe_gpu_in_current_python() -> Dict[str, Any]:
    """Inspects CUDA GPU availability cleanly without crashing when torch is absent or SM arch is unsupported."""
    info: Dict[str, Any] = {"torch": False, "cuda": False, "cuda_usable": False, "device_name": "cpu", "vram_gb": 0.0}
    if not has_module("torch"):
        return info
    try:
        import torch

        info["torch"] = True
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            info["cuda"] = True
            info["device_name"] = props.name
            info["vram_gb"] = round(props.total_memory / float(1024**3), 2)
            try:
                _ = (torch.ones(2, device="cuda") + 1).item() if False else float((torch.ones(1, device="cuda") + 1.0)[0].item())
                info["cuda_usable"] = True
            except Exception as cuda_exc:
                info["cuda_warning"] = str(cuda_exc)
    except Exception as exc:
        info["probe_error"] = str(exc)
    return info


def probe_python_voice_capabilities(python_bin: str) -> Dict[str, Any]:
    """Probes an external Python interpreter for edge_tts, torch/CUDA, and ASR engine availability."""
    snippet = (
        "import importlib.util, json\n"
        "mods = {m: importlib.util.find_spec(m) is not None for m in "
        "('edge_tts', 'torch', 'faster_whisper', 'whisper', 'transformers')}\n"
        "cuda = False\n"
        "if mods['torch']:\n"
        "    import torch\n"
        "    cuda = bool(torch.cuda.is_available())\n"
        "mods['cuda'] = cuda\n"
        "print(json.dumps(mods))\n"
    )
    res = subprocess.run([python_bin, "-c", snippet], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return {"edge_tts": False, "torch": False, "cuda": False, "faster_whisper": False, "whisper": False, "transformers": False}
    try:
        return dict(json.loads(res.stdout.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        return {"edge_tts": False, "torch": False, "cuda": False, "faster_whisper": False, "whisper": False, "transformers": False}


def resolve_voice_python(configured_python: str = "") -> Optional[str]:
    """Returns path to a Python interpreter with voice/GPU libraries when configured or in env."""
    if has_module("edge_tts") and has_module("torch"):
        return sys.executable
    for cand in (configured_python.strip(), os.environ.get("VOICE_STUDIO_PYTHON", "").strip()):
        if cand and os.path.isfile(cand):
            return cand
    return None


def resolve_rvc_assets_base(configured_root: str = "") -> str:
    """Resolves RVC model/Applio directory locally under models/rvc or on Colab under /content/rvc_cache."""
    for cand in (configured_root.strip(), os.environ.get("RVC_ASSETS_ROOT", "").strip()):
        if cand:
            p = Path(cand)
            return str(p if p.is_absolute() else (PROJECT_ROOT / p).resolve())
    if os.path.exists("/content"):
        return "/content/rvc_cache"
    return str((PROJECT_ROOT / "models" / "rvc").resolve())


def probe_runtime(configured_voice_python: str = "", configured_rvc_root: str = "") -> Dict[str, Any]:
    """Collects full local runtime diagnostics for CLI health checks and remote Colab agents."""
    ff = probe_ffmpeg()
    gpu = probe_gpu_in_current_python()
    vp = resolve_voice_python(configured_voice_python)
    if vp and os.path.abspath(vp) != os.path.abspath(sys.executable):
        vp_caps = probe_python_voice_capabilities(vp)
    else:
        vp_caps = {
            "edge_tts": has_module("edge_tts"),
            "torch": gpu["torch"],
            "cuda": gpu["cuda"],
            "faster_whisper": has_module("faster_whisper"),
            "whisper": has_module("whisper"),
            "transformers": has_module("transformers"),
        }
    return {
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "cpu_count": os.cpu_count() or 1,
        "ffmpeg": ff["ffmpeg"],
        "ffprobe": ff["ffprobe"],
        "nvenc": ff["nvenc"],
        "torch": gpu["torch"],
        "cuda": gpu["cuda"],
        "cuda_usable": gpu["cuda_usable"],
        "device_name": gpu["device_name"],
        "vram_gb": gpu["vram_gb"],
        "voice_python": vp or "",
        "voice_capabilities": vp_caps,
        "rvc_assets_root": resolve_rvc_assets_base(configured_rvc_root),
    }

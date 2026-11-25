# -*- coding: utf-8 -*-
"""Voice Studio: Configurable Thai+English TTS, RVC v2, Mastering, and 1080p@60fps Video SDK & CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

__version__ = "2.2.0"

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent

__all__ = [
    "__version__",
    "PACKAGE_DIR",
    "PROJECT_ROOT",
    "VoiceStudio",
    "PipelineConfig",
    "VoiceProfile",
    "Stage1Config",
    "Stage2Config",
    "MasteringConfig",
    "OutputConfig",
    "EQ_PRESETS",
    "load_pipeline_config",
    "list_builtin_voices",
    "resolve_voice_profile",
]


def __getattr__(name: str) -> Any:
    """Lazy public SDK exports so importing voice_studio keeps CLI startup fast."""
    if name == "VoiceStudio":
        from voice_studio.sdk import VoiceStudio

        return VoiceStudio
    if name in ("PipelineConfig", "VoiceProfile", "Stage1Config", "Stage2Config", "MasteringConfig", "OutputConfig"):
        from voice_studio.config import models

        return getattr(models, name)
    if name == "load_pipeline_config":
        from voice_studio.config.loader import load_pipeline_config

        return load_pipeline_config
    if name in ("list_builtin_voices", "resolve_voice_profile"):
        from voice_studio.config import voices

        return getattr(voices, name)
    if name == "EQ_PRESETS":
        from voice_studio.audio.mastering import EQ_PRESETS

        return EQ_PRESETS
    raise AttributeError(f"module 'voice_studio' has no attribute {name!r}")

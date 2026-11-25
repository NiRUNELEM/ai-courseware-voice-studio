# -*- coding: utf-8 -*-
"""Unified configuration loader, typed dataclasses, and voice profile helpers."""

from voice_studio.config.loader import (
    MEDIA_EXTENSIONS,
    is_media_input_path,
    load_pipeline_config,
    resolve_repo_path,
    resolve_step_range,
    validate_steps,
)
from voice_studio.config.models import (
    DEFAULT_SCRIPT_STEPS,
    ORDERED_STEPS,
    AudioQAConfig,
    ColabBridgeConfig,
    ContentConfig,
    MasteringConfig,
    OutputConfig,
    PipelineConfig,
    RuntimeConfig,
    Stage1Config,
    Stage2Config,
    TranscribeConfig,
    VoiceProfile,
)
from voice_studio.config.voices import (
    BUILTIN_VOICES,
    build_voice_profile,
    list_builtin_voices,
    parse_structured_file,
    profile_to_rvc_dict,
    resolve_voice_profile,
)

__all__ = [
    "AudioQAConfig",
    "BUILTIN_VOICES",
    "ColabBridgeConfig",
    "ContentConfig",
    "DEFAULT_SCRIPT_STEPS",
    "MEDIA_EXTENSIONS",
    "MasteringConfig",
    "ORDERED_STEPS",
    "OutputConfig",
    "PipelineConfig",
    "RuntimeConfig",
    "Stage1Config",
    "Stage2Config",
    "TranscribeConfig",
    "VoiceProfile",
    "build_voice_profile",
    "is_media_input_path",
    "list_builtin_voices",
    "load_pipeline_config",
    "parse_structured_file",
    "profile_to_rvc_dict",
    "resolve_repo_path",
    "resolve_step_range",
    "resolve_voice_profile",
    "validate_steps",
]

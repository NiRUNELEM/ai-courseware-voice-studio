# -*- coding: utf-8 -*-
"""Modular audio package: ASR transcription, Edge-TTS, RVC v2 conversion, mastering & QA gate."""

from voice_studio.audio.mastering import (
    apply_clean_speech_mastering,
    concatenate_wav_items,
    convert_to_pcm_wav,
    create_silence_pcm,
    measure_wav_acoustics,
    normalize_pcm16_lufs,
)
from voice_studio.audio.qa_gate import (
    AudioGateResult,
    audit_phonetics_zero_english,
    compute_audio_metrics,
    print_gate_report,
    validate_audio_deliverables,
)
from voice_studio.audio.rvc_assets import (
    configure_torch_runtime,
    default_rvc_dirs,
    ensure_applio_repo,
    ensure_rvc_model_assets,
    extract_rvc_model_zip,
)
from voice_studio.audio.stage1_tts import (
    EDGE_DEFAULT_PACING_SEC,
    EDGE_DEFAULT_PITCH,
    EDGE_DEFAULT_RATE,
    EDGE_DEFAULT_VOICE,
    estimate_speech_duration,
    generate_dry_run_audio,
    read_wav_duration_safe,
    synthesize_stage1_batch,
    synthesize_with_edge_tts,
)
from voice_studio.audio.stage2_rvc import (
    convert_stage1_to_rvc_batch,
    convert_wav_with_rvc,
    synthesize_edge_rvc_item,
)
from voice_studio.audio.stage_runner import run_stage1_tts, run_stage2_voice_convert
from voice_studio.audio.synthesizer import VoiceSynthesizer
from voice_studio.audio.transcribe import transcribe_audio

__all__ = [
    "AudioGateResult",
    "EDGE_DEFAULT_PACING_SEC",
    "EDGE_DEFAULT_PITCH",
    "EDGE_DEFAULT_RATE",
    "EDGE_DEFAULT_VOICE",
    "VoiceSynthesizer",
    "apply_clean_speech_mastering",
    "audit_phonetics_zero_english",
    "compute_audio_metrics",
    "concatenate_wav_items",
    "configure_torch_runtime",
    "convert_stage1_to_rvc_batch",
    "convert_to_pcm_wav",
    "convert_wav_with_rvc",
    "create_silence_pcm",
    "default_rvc_dirs",
    "ensure_applio_repo",
    "ensure_rvc_model_assets",
    "estimate_speech_duration",
    "extract_rvc_model_zip",
    "generate_dry_run_audio",
    "measure_wav_acoustics",
    "normalize_pcm16_lufs",
    "print_gate_report",
    "read_wav_duration_safe",
    "run_stage1_tts",
    "run_stage2_voice_convert",
    "synthesize_edge_rvc_item",
    "synthesize_stage1_batch",
    "synthesize_with_edge_tts",
    "transcribe_audio",
    "validate_audio_deliverables",
]

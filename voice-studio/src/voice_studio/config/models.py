# -*- coding: utf-8 -*-
"""Typed configuration models for ASR transcription, TTS, RVC v2 conversion, mastering & Colab Pro."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

ORDERED_STEPS: tuple[str, ...] = (
    "transcribe",
    "prepare",
    "tts",
    "convert",
    "qa",
    "align",
    "render",
)

DEFAULT_SCRIPT_STEPS: tuple[str, ...] = (
    "prepare",
    "tts",
    "convert",
    "qa",
    "align",
    "render",
)


@dataclass
class Stage1Config:
    backend: str = "edge_tts"
    voice: str = "th-TH-PremwadeeNeural"
    hf_model_repo: str = "kunato/wayu-kokoro-thai-v1"
    hf_speaker: str = "f_mid_warm"
    hf_speed: float = 0.96
    rate: str = "-6%"
    pitch: str = "+0Hz"
    sample_rate: int = 40000
    text_mode: str = "bilingual"
    clause_spacing: bool = True
    pacing_sec: float = 0.30


@dataclass
class Stage2Config:
    backend: str = "rvc_v2"
    enabled: bool = True
    source_type: str = "huggingface"
    repo_id: str = "KeroroK66/GawrGura"
    zip_filename: str = "Gawr Gura - Weights.gg Model.zip"
    pth_filename: str = "model.pth"
    index_filename: str = "model.index"
    default_subdir: str = "gawr_gura"
    local_pth: str = ""
    local_index: str = ""
    pitch_shift: int = 2
    f0_method: str = "rmvpe"
    index_rate: float = 0.12
    volume_envelope: float = 0.25
    protect: float = 0.45
    hop_length: int = 128
    input_gate_threshold: float = 0.008
    input_target_rms: float = 0.140
    input_peak_ceiling: float = 0.78
    output_peak_ceiling: float = 0.89
    rvc_fp32: bool = False
    intermediate_float: bool = False


@dataclass
class MasteringConfig:
    sample_rate: int = 40000
    bit_depth: int = 16
    resampler_precision: int = 20
    target_lufs: float = -18.0
    denoise: bool = True
    denoise_nf_db: int = -48
    denoise_nr_db: int = 8
    declick: bool = True
    noise_gate: bool = True
    noise_gate_threshold: float = 0.007
    inter_item_silence: float = 0.40
    mp3_bitrate: str = "192k"


@dataclass
class VoiceProfile:
    voice_id: str
    display_name: str
    description: str = ""
    engine_alias: str = ""
    license: str = "custom"
    license_scope: str = "internal_non_commercial_education"
    stage1: Stage1Config = field(default_factory=Stage1Config)
    stage2: Stage2Config = field(default_factory=Stage2Config)
    mastering: MasteringConfig = field(default_factory=MasteringConfig)
    attribution: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscribeConfig:
    backend: str = "auto"
    model: str = "openai/whisper-small"
    language: str = "th"
    beam_size: int = 1
    vad_filter: bool = False
    chunk_length_s: int = 30
    formats: List[str] = field(default_factory=lambda: ["txt", "srt", "json"])


@dataclass
class ContentConfig:
    mode: str = "custom"
    path: str = "input/sample_neutral.json"
    slides: List[int] = field(default_factory=list)
    slides_png_dir: str = ""
    pptx_path: str = ""
    timeline_json: str = ""
    reveal_manifest: str = ""
    reveal_key: str = ""
    extra_vocab_path: str = ""
    title: str = ""
    zero_latin_phonetic: bool = False


@dataclass
class OutputConfig:
    dir: str = "output"
    prefix: str = "studio"
    formats: List[str] = field(default_factory=lambda: ["wav", "mp3", "mp4"])
    fps: int = 60
    mp3_bitrate: str = "192k"
    aac_bitrate: str = "192k"
    nvenc_preset: str = "p4"
    nvenc_cq: int = 21


@dataclass
class RuntimeConfig:
    voice_python: str = ""
    rvc_assets_root: str = "models/rvc"
    device: str = "cuda"
    gpu_tier: str = "auto"
    high_ram: bool = False
    precision: str = "auto"
    stage1_workers: int = 4
    cpu_workers: int = 0
    video_workers: int = 0
    fps: int = 60


@dataclass
class AudioQAConfig:
    duration_tolerance: float = 1.0
    min_snr_db: float = 28.0
    target_lufs: float = -18.0
    lufs_tolerance: float = 1.5


@dataclass
class ColabBridgeConfig:
    mode: str = "colab_drive"
    client_secrets_path: str = ".colab_auth/client_secrets.json"
    token_path: str = ".colab_auth/token.json"
    root_folder_name: str = "voice_studio_jobs"
    local_mount_dir: str = ""
    agent_url: str = ""
    agent_token: str = ""
    poll_interval_sec: float = 10.0
    timeout_sec: float = 7200.0
    request_timeout_sec: float = 30.0
    notebook_path: str = "colab/voice_studio_colab.ipynb"
    notebook_drive_id: str = ""
    bundle_zip_path: str = "exports/voice_studio_colab_bundle.zip"
    session_name: str = "voice-studio-pro"
    cli_binary: str = ""
    disconnect_mismatched: bool = True
    connect_browser_mcp: bool = False


@dataclass
class PipelineConfig:
    job_id: str = ""
    output_root: str = "output"
    resume: bool = True
    dry_run: bool = False
    clean_work: bool = False
    target: str = "local"
    voice: str = "gawr_gura"
    phonetic_vocab_path: str = "src/voice_studio/content/phonetic_vocab.json"
    content: ContentConfig = field(default_factory=ContentConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    steps: List[str] = field(default_factory=lambda: list(DEFAULT_SCRIPT_STEPS))
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    audio_qa: AudioQAConfig = field(default_factory=AudioQAConfig)
    colab: ColabBridgeConfig = field(default_factory=ColabBridgeConfig)
    voice_profile: VoiceProfile | None = None
    raw: Dict[str, Any] = field(default_factory=dict)

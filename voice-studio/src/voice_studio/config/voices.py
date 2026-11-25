# -*- coding: utf-8 -*-
"""Clean essential voice profiles (Gawr Gura RVC v2, Direct Edge-TTS, and pluggable Custom RVC)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from voice_studio.config.models import MasteringConfig, Stage1Config, Stage2Config, VoiceProfile

DEFAULT_GAWR_GURA_PRESET: Dict[str, Any] = {
    "voice_id": "gawr_gura",
    "display_name": "Gawr Gura [RVC v2] [RMVPE] [Ov2Super] 275e",
    "description": "Edge-TTS PremwadeeNeural + Gawr Gura Ov2Super RVC v2 + Noise Gate -16 LUFS",
    "engine_alias": "edge-gura-rvc",
    "license": "openrail",
    "license_scope": "internal_non_commercial_education",
    "stage1": {
        "backend": "edge_tts",
        "voice": "th-TH-PremwadeeNeural",
        "rate": "-6%",
        "pitch": "+0Hz",
        "sample_rate": 40000,
        "text_mode": "bilingual",
        "clause_spacing": True,
        "pacing_sec": 0.30,
    },
    "stage2": {
        "backend": "rvc_v2",
        "enabled": True,
        "source_type": "huggingface",
        "repo_id": "KeroroK66/GawrGura",
        "zip_filename": "Gawr Gura - Weights.gg Model.zip",
        "pth_filename": "model.pth",
        "index_filename": "model.index",
        "default_subdir": "gawr_gura",
        "local_pth": "",
        "local_index": "",
        "pitch_shift": 2,
        "f0_method": "rmvpe",
        "index_rate": 0.10,
        "volume_envelope": 0.75,
        "protect": 0.55,
        "hop_length": 128,
    },
    "mastering": {
        "sample_rate": 40000,
        "target_lufs": -18.0,
        "declick": True,
        "denoise": True,
        "noise_gate": True,
        "noise_gate_threshold": 0.007,
        "inter_item_silence": 0.40,
    },
}

DEFAULT_DIRECT_TTS_PRESET: Dict[str, Any] = {
    "voice_id": "direct_tts",
    "display_name": "Microsoft Edge PremwadeeNeural (Direct TTS Baseline)",
    "description": "Stage 1 Edge-TTS + studio mastering (no RVC vocal conversion)",
    "engine_alias": "edge-direct",
    "license": "LGPLv3",
    "license_scope": "internal_non_commercial_education",
    "stage1": {
        "backend": "edge_tts",
        "voice": "th-TH-PremwadeeNeural",
        "rate": "-6%",
        "pitch": "+0Hz",
        "sample_rate": 40000,
        "text_mode": "bilingual",
        "clause_spacing": True,
        "pacing_sec": 0.30,
    },
    "stage2": {
        "backend": "none",
        "enabled": False,
        "source_type": "local",
        "repo_id": "",
        "zip_filename": "",
        "pth_filename": "",
        "index_filename": "",
        "default_subdir": "",
    },
    "mastering": {
        "sample_rate": 40000,
        "target_lufs": -18.0,
        "declick": True,
        "denoise": True,
        "noise_gate": True,
        "noise_gate_threshold": 0.007,
        "inter_item_silence": 0.40,
    },
}

DEFAULT_HF_FEMALE_PRESET: Dict[str, Any] = {
    "voice_id": "hf_female",
    "display_name": "Wayu Kokoro Thai v1 — Young Bright Female (HuggingFace Bilingual ONNX)",
    "description": "Direct bilingual Thai+English female speech via kunato/wayu-kokoro-thai-v1 (no RVC conversion).",
    "engine_alias": "hf-kokoro-female",
    "license": "Apache-2.0 (kunato/wayu-kokoro-thai-v1)",
    "license_scope": "general_permissive",
    "stage1": {
        "backend": "hf_kokoro_thai",
        "voice": "th-TH-PremwadeeNeural",
        "hf_model_repo": "kunato/wayu-kokoro-thai-v1",
        "hf_speaker": "f_young_bright",
        "hf_speed": 0.96,
        "sample_rate": 40000,
        "text_mode": "bilingual",
        "clause_spacing": True,
        "pacing_sec": 0.30,
    },
    "stage2": {
        "backend": "none",
        "enabled": False,
        "source_type": "local",
        "repo_id": "",
        "zip_filename": "",
        "pth_filename": "",
        "index_filename": "",
        "default_subdir": "",
    },
    "mastering": {
        "sample_rate": 40000,
        "target_lufs": -18.0,
        "declick": True,
        "denoise": True,
        "noise_gate": True,
        "noise_gate_threshold": 0.007,
        "inter_item_silence": 0.40,
    },
}

DEFAULT_GAWR_GURA_HF_PRESET: Dict[str, Any] = {
    "voice_id": "gawr_gura_hf",
    "display_name": "Gawr Gura (RVC v2 Tuned from HF Wayu Kokoro Thai f_mid_warm)",
    "description": "Stage 1 kunato/wayu-kokoro-thai-v1 (f_mid_warm, phonetic_spaced, speed=0.92) -> Stage 2 KeroroK66/GawrGura RVC v2 (pitch +1, index 0.08, protect 0.52).",
    "engine_alias": "hf-kokoro-gura-rvc",
    "license": "non-commercial-fan-voice-model",
    "license_scope": "internal_non_commercial_education",
    "stage1": {
        "backend": "hf_kokoro_thai",
        "voice": "th-TH-PremwadeeNeural",
        "hf_model_repo": "kunato/wayu-kokoro-thai-v1",
        "hf_speaker": "f_mid_warm",
        "hf_speed": 0.92,
        "sample_rate": 40000,
        "text_mode": "phonetic_spaced",
        "clause_spacing": True,
        "pacing_sec": 0.30,
    },
    "stage2": {
        "backend": "rvc_v2",
        "enabled": True,
        "source_type": "huggingface",
        "repo_id": "KeroroK66/GawrGura",
        "zip_filename": "Gawr Gura - Weights.gg Model.zip",
        "pth_filename": "model.pth",
        "index_filename": "model.index",
        "default_subdir": "gawr_gura",
        "pitch_shift": 1,
        "f0_method": "rmvpe",
        "index_rate": 0.08,
        "volume_envelope": 0.25,
        "protect": 0.52,
        "hop_length": 128,
        "rvc_fp32": True,
        "intermediate_float": True,
    },
    "mastering": {
        "sample_rate": 40000,
        "target_lufs": -18.0,
        "denoise": True,
        "denoise_nf_db": -52,
        "denoise_nr_db": 7,
        "declick": True,
        "noise_gate": True,
        "noise_gate_threshold": 0.006,
        "inter_item_silence": 0.40,
    },
}

from voice_studio.config.voice_presets_hf import (
    DEFAULT_GAWR_GURA_F5_PRESET,
    DEFAULT_GAWR_GURA_HYBRID_PRESET,
    DEFAULT_GAWR_GURA_OMNIVOICE_PRESET,
)

BUILTIN_VOICES: Dict[str, Dict[str, Any]] = {
    "gawr_gura": DEFAULT_GAWR_GURA_PRESET,
    "direct_tts": DEFAULT_DIRECT_TTS_PRESET,
    "hf_female": DEFAULT_HF_FEMALE_PRESET,
    "gawr_gura_hf": DEFAULT_GAWR_GURA_HF_PRESET,
    "gawr_gura_omnivoice": DEFAULT_GAWR_GURA_OMNIVOICE_PRESET,
    "gawr_gura_f5": DEFAULT_GAWR_GURA_F5_PRESET,
    "gawr_gura_hybrid": DEFAULT_GAWR_GURA_HYBRID_PRESET,
}

VOICE_ALIASES: Dict[str, str] = {
    "gura": "gawr_gura",
    "edge-gura-rvc": "gawr_gura",
    "direct": "direct_tts",
    "premwadee": "direct_tts",
    "premwadee_direct": "direct_tts",
    "edge": "direct_tts",
    "wayu": "hf_female",
    "kokoro_thai": "hf_female",
    "hf_tts": "hf_female",
    "gura_hf": "gawr_gura_hf",
    "gawrgura_hf": "gawr_gura_hf",
}


def parse_structured_file(path: Path) -> Dict[str, Any]:
    """Loads YAML or JSON mapping from disk cleanly."""
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(raw_text)
        return data if isinstance(data, dict) else {}
    try:
        import yaml

        data = yaml.safe_load(raw_text)
        return data if isinstance(data, dict) else {}
    except ImportError as exc:
        raise RuntimeError(f"PyYAML is required to parse {path}; install PyYAML or use JSON config.") from exc


def build_voice_profile(
    voice_block: Optional[Dict[str, Any]] = None,
    output_block: Optional[Dict[str, Any]] = None,
) -> VoiceProfile:
    """Constructs a validated VoiceProfile from unified studio YAML `voice` and `output` blocks."""
    vb = dict(voice_block or {})
    ob = dict(output_block or {})

    raw_id = str(vb.get("profile") or vb.get("voice_id") or "gawr_gura").strip()
    canonical = VOICE_ALIASES.get(raw_id.lower(), raw_id)
    base = dict(BUILTIN_VOICES.get(canonical, DEFAULT_GAWR_GURA_PRESET))

    s1_d = dict(base["stage1"])
    s2_d = dict(base["stage2"])
    m_d = dict(base["mastering"])

    # Flat intuitive keys in configs/studio.yaml
    if vb.get("tts_backend"):
        s1_d["backend"] = str(vb["tts_backend"])
    if vb.get("tts_voice"):
        s1_d["voice"] = str(vb["tts_voice"])
    if vb.get("hf_model_repo"):
        s1_d["hf_model_repo"] = str(vb["hf_model_repo"])
    if vb.get("hf_speaker"):
        s1_d["hf_speaker"] = str(vb["hf_speaker"])
    if "hf_speed" in vb and vb["hf_speed"] is not None:
        s1_d["hf_speed"] = float(vb["hf_speed"])
    if vb.get("tts_rate"):
        s1_d["rate"] = str(vb["tts_rate"])
    if vb.get("tts_pitch"):
        s1_d["pitch"] = str(vb["tts_pitch"])
    if "pacing_sec" in vb:
        s1_d["pacing_sec"] = float(vb["pacing_sec"])
    if vb.get("text_mode"):
        s1_d["text_mode"] = str(vb["text_mode"])
    if "clause_spacing" in vb:
        s1_d["clause_spacing"] = bool(vb["clause_spacing"])

    if isinstance(vb.get("stage1"), dict):
        s1_d.update(vb["stage1"])

    rvc_raw = vb.get("rvc") if isinstance(vb.get("rvc"), dict) else (vb.get("stage2") if isinstance(vb.get("stage2"), dict) else {})
    if canonical in ("direct_tts", "hf_female") and "rvc" not in vb and "stage2" not in vb:
        s2_d["enabled"] = False
        s2_d["backend"] = "none"
    elif isinstance(rvc_raw, dict) and rvc_raw:
        if "subdir" in rvc_raw and "default_subdir" not in rvc_raw:
            s2_d["default_subdir"] = str(rvc_raw["subdir"])
        for k, v in rvc_raw.items():
            if k in Stage2Config.__dataclass_fields__:
                s2_d[k] = v
        if s2_d.get("enabled", True) and str(s2_d.get("backend", "")).lower() != "none":
            s2_d["enabled"] = True
            s2_d["backend"] = "rvc_v2"
        else:
            s2_d["enabled"] = False
            s2_d["backend"] = "none"

    if isinstance(vb.get("mastering"), dict):
        for k, v in vb["mastering"].items():
            if k in MasteringConfig.__dataclass_fields__ and v is not None:
                m_d[k] = v
    for k in MasteringConfig.__dataclass_fields__:
        if k in ob and ob[k] is not None:
            m_d[k] = ob[k]
    if "sample_rate" in ob and ob["sample_rate"]:
        s1_d["sample_rate"] = int(ob["sample_rate"])

    s1_backend = str(s1_d.get("backend") or "edge_tts").lower()
    s1_project = (
        f"{s1_d.get('hf_model_repo', 'kunato/wayu-kokoro-thai-v1')} ({s1_d.get('hf_speaker', 'f_mid_warm')})"
        if s1_backend != "edge_tts"
        else f"rany2/edge-tts ({s1_d['voice']})"
    )
    attribution = {
        "license_scope": str(vb.get("license_scope") or base.get("license_scope", "internal_non_commercial_education")),
        "stage1_tts": {"project": s1_project},
        "stage2_voice_model": (
            {
                "project": str(s2_d.get("repo_id") or s2_d.get("local_pth") or canonical),
                "license": str(vb.get("license") or base.get("license", "custom")),
            }
            if s2_d.get("enabled") and s2_d.get("backend") == "rvc_v2"
            else {"project": "none (direct Stage 1 TTS)"}
        ),
        "inference_engine": {"project": "IAHispano/Applio", "license": "MIT"} if s2_d.get("enabled") else {},
    }

    return VoiceProfile(
        voice_id=canonical,
        display_name=str(vb.get("display_name") or base.get("display_name") or canonical),
        description=str(vb.get("description") or base.get("description") or ""),
        engine_alias=str(vb.get("engine_alias") or base.get("engine_alias") or canonical),
        license=str(vb.get("license") or base.get("license") or "custom"),
        license_scope=str(vb.get("license_scope") or base.get("license_scope") or "internal_non_commercial_education"),
        stage1=Stage1Config(**{k: v for k, v in s1_d.items() if k in Stage1Config.__dataclass_fields__}),
        stage2=Stage2Config(**{k: v for k, v in s2_d.items() if k in Stage2Config.__dataclass_fields__}),
        mastering=MasteringConfig(**{k: v for k, v in m_d.items() if k in MasteringConfig.__dataclass_fields__}),
        attribution=attribution,
    )


def list_builtin_voices() -> Dict[str, VoiceProfile]:
    """Returns active built-in voice profiles."""
    return {vid: build_voice_profile({"profile": vid}) for vid in BUILTIN_VOICES}


def resolve_voice_profile(
    voice_name: str = "gawr_gura",
    inline_override: Optional[Dict[str, Any]] = None,
    output_block: Optional[Dict[str, Any]] = None,
) -> VoiceProfile:
    """Resolves either an active built-in profile or an arbitrary custom config dict."""
    if inline_override:
        merged = dict(inline_override)
        merged.setdefault("profile", voice_name)
        return build_voice_profile(merged, output_block=output_block)
    cleaned = (voice_name or "gawr_gura").strip()
    canonical = VOICE_ALIASES.get(cleaned.lower(), cleaned)
    if canonical not in BUILTIN_VOICES:
        raise ValueError(
            f"Unknown built-in voice profile {voice_name!r}. Available: {sorted(BUILTIN_VOICES)} "
            "(or configure custom voice.rvc fields in configs/studio.yaml)."
        )
    return build_voice_profile({"profile": canonical}, output_block=output_block)


def profile_to_rvc_dict(profile: VoiceProfile) -> Dict[str, Any]:
    """Converts VoiceProfile into the parameter dict consumed by RVC converter + FFmpeg mastering."""
    s1, s2, m = profile.stage1, profile.stage2, profile.mastering
    return {
        "name": profile.display_name,
        "repo_id": s2.repo_id,
        "zip_filename": s2.zip_filename,
        "pth_filename": s2.pth_filename,
        "index_filename": s2.index_filename,
        "default_subdir": s2.default_subdir or profile.voice_id,
        "source_type": s2.source_type,
        "local_pth": s2.local_pth,
        "local_index": s2.local_index,
        "license": profile.license,
        "edge_voice": s1.voice,
        "edge_rate": s1.rate,
        "edge_pitch": s1.pitch,
        "pitch": int(s2.pitch_shift),
        "f0_method": s2.f0_method,
        "index_rate": float(s2.index_rate),
        "volume_envelope": float(s2.volume_envelope),
        "protect": float(s2.protect),
        "hop_length": int(s2.hop_length),
        "input_gate_threshold": float(s2.input_gate_threshold),
        "input_target_rms": float(s2.input_target_rms),
        "input_peak_ceiling": float(s2.input_peak_ceiling),
        "output_peak_ceiling": float(s2.output_peak_ceiling),
        "rvc_fp32": bool(s2.rvc_fp32),
        "intermediate_float": bool(s2.intermediate_float),
        "f0_stabilizer": bool(s2.f0_stabilizer),
        "pre_gate_rms_ms": float(s2.pre_gate_rms_ms),
        "pre_gate_ramp_ms": float(s2.pre_gate_ramp_ms),
        "sample_rate": int(m.sample_rate),
        "bit_depth": int(m.bit_depth),
        "resampler_precision": int(m.resampler_precision),
        "target_lufs": float(m.target_lufs),
        "denoise": bool(m.denoise),
        "denoise_nf_db": int(m.denoise_nf_db),
        "denoise_nr_db": int(m.denoise_nr_db),
        "declick": bool(m.declick),
        "noise_gate": bool(m.noise_gate),
        "noise_gate_threshold": float(m.noise_gate_threshold),
        "gate_ratio": float(m.gate_ratio),
        "gate_release_ms": float(m.gate_release_ms),
        "highpass_hz": int(m.highpass_hz),
        "lowpass_hz": int(m.lowpass_hz),
        "eq_preset": str(m.eq_preset),
        "eq_bands": list(m.eq_bands),
        "comp_threshold_db": float(m.comp_threshold_db),
        "comp_ratio": float(m.comp_ratio),
        "limiter_db": float(m.limiter_db),
        "pre_rvc_conditioning": bool(m.pre_rvc_conditioning),
        "mp3_bitrate": str(m.mp3_bitrate),
    }

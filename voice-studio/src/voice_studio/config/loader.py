# -*- coding: utf-8 -*-
"""Unified YAML/JSON configuration loader, path resolver, and clean step range validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from voice_studio import PROJECT_ROOT
from voice_studio.config.models import (
    DEFAULT_SCRIPT_STEPS,
    ORDERED_STEPS,
    AudioQAConfig,
    ColabBridgeConfig,
    ContentConfig,
    OutputConfig,
    PipelineConfig,
    RuntimeConfig,
    TranscribeConfig,
)
from voice_studio.config.voices import build_voice_profile, parse_structured_file

MEDIA_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".mp4", ".mkv", ".webm", ".mov"}

STEP_ALIASES: Dict[str, str] = {
    "prepare_content": "prepare",
    "tts_stage1": "tts",
    "voice_convert": "convert",
    "validate_audio": "qa",
    "align_timeline": "align",
    "render_video": "render",
}


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def load_dotenv_file(env_path: Path) -> None:
    """Loads KEY=VALUE pairs from .env without overriding already exported OS env vars."""
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        k = key.strip()
        v = val.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def resolve_repo_path(raw_path: str, base_dir: Path = PROJECT_ROOT) -> Path:
    """Resolves a config path relative to base_dir, voice-studio subdir, cwd, or avoids nested dir duplication."""
    p = Path(raw_path)
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == base_dir.name and not (base_dir / p).exists():
        stripped = Path(*p.parts[1:]) if len(p.parts) > 1 else Path(".")
        return (base_dir / stripped).resolve()
    base_cand = base_dir / p
    if base_cand.exists():
        return base_cand
    nested_cand = base_dir / "voice-studio" / p
    if nested_cand.exists():
        return nested_cand
    cwd_cand = Path.cwd() / p
    if cwd_cand.exists():
        return cwd_cand.resolve()
    return base_cand


def _resolve_external_config_paths(custom_data: Dict[str, Any], cfg_dir: Path, base_dir: Path) -> Dict[str, Any]:
    """Resolves relative paths inside an external YAML relative to cfg_dir cleanly."""
    out = dict(custom_data)

    def _pick(raw_val: str, is_output: bool = False) -> str:
        p = Path(raw_val)
        if not raw_val or p.is_absolute():
            return raw_val
        cfg_cand = (cfg_dir / p).resolve()
        if is_output and (raw_val.startswith("./") or raw_val.startswith("../")):
            return str(cfg_cand)
        if not (base_dir / p).exists() and cfg_cand.exists():
            return str(cfg_cand)
        return raw_val

    for sec, keys, is_out in (
        ("input", ("path", "slides_png_dir", "pptx_path", "timeline_json", "reveal_manifest", "extra_vocab_path"), False),
        ("output", ("dir",), True),
        ("job", ("output_dir", "output_root"), True),
    ):
        if isinstance(out.get(sec), dict):
            sec_d = dict(out[sec])
            for k in keys:
                if isinstance(sec_d.get(k), str) and sec_d[k]:
                    sec_d[k] = _pick(sec_d[k], is_output=is_out)
            out[sec] = sec_d
    if isinstance(out.get("phonetic_vocab_path"), str) and out["phonetic_vocab_path"]:
        out["phonetic_vocab_path"] = _pick(out["phonetic_vocab_path"], is_output=False)
    return out


def validate_steps(steps: Iterable[str]) -> List[str]:
    """Validates and normalizes step list against ORDERED_STEPS."""
    selected: List[str] = []
    for raw in steps:
        token = raw.strip()
        if not token:
            continue
        name = STEP_ALIASES.get(token, token)
        if name not in ORDERED_STEPS:
            raise ValueError(f"Unknown pipeline step {raw!r}; expected one of {list(ORDERED_STEPS)}")
        if name not in selected:
            selected.append(name)
    if not selected:
        raise ValueError("No valid pipeline steps selected.")
    return [s for s in ORDERED_STEPS if s in selected]


def resolve_step_range(
    steps: Optional[List[str]] = None,
    from_step: str = "",
    to_step: str = "",
) -> List[str]:
    """Resolves either an explicit step list or an inclusive [from_step..to_step] slice."""
    if steps:
        return validate_steps(steps)
    norm_from = STEP_ALIASES.get(from_step.strip(), from_step.strip()) if from_step else ""
    norm_to = STEP_ALIASES.get(to_step.strip(), to_step.strip()) if to_step else ""
    start_idx = 0 if not norm_from else ORDERED_STEPS.index(norm_from) if norm_from in ORDERED_STEPS else -1
    end_idx = len(ORDERED_STEPS) - 1 if not norm_to else ORDERED_STEPS.index(norm_to) if norm_to in ORDERED_STEPS else -1
    if start_idx < 0:
        raise ValueError(f"Invalid --from-step {from_step!r}; expected one of {list(ORDERED_STEPS)}")
    if end_idx < 0:
        raise ValueError(f"Invalid --to-step {to_step!r}; expected one of {list(ORDERED_STEPS)}")
    if start_idx > end_idx:
        raise ValueError(f"--from-step ({from_step}) cannot come after --to-step ({to_step})")
    return list(ORDERED_STEPS[start_idx : end_idx + 1])


def is_media_input_path(raw_path: str) -> bool:
    """Returns True when the configured input path is an audio or video file for ASR transcription."""
    return bool(raw_path) and Path(raw_path).suffix.lower() in MEDIA_EXTENSIONS


def load_pipeline_config(
    config_path: Optional[str | Path] = None,
    overrides: Optional[Dict[str, Any]] = None,
    base_dir: Path = PROJECT_ROOT,
) -> PipelineConfig:
    """Loads unified configs/studio.yaml plus optional job config and CLI overrides into PipelineConfig."""
    load_dotenv_file(base_dir / ".env")
    default_cfg_path = base_dir / "configs" / "studio.yaml"
    merged: Dict[str, Any] = parse_structured_file(default_cfg_path) if default_cfg_path.is_file() else {}
    if config_path:
        custom_path = resolve_repo_path(str(config_path), base_dir)
        if not custom_path.is_file():
            raise FileNotFoundError(f"Studio config file not found: {custom_path}")
        custom_data = parse_structured_file(custom_path)
        cfg_dir = custom_path.resolve().parent
        if not cfg_dir.is_relative_to(base_dir.resolve()):
            custom_data = _resolve_external_config_paths(custom_data, cfg_dir, base_dir)
        merged = _deep_merge(merged, custom_data)
    if overrides:
        ovr = dict(overrides)
        target_hint = str((ovr.get("runtime") or {}).get("target") or merged.get("runtime", {}).get("target") or "")
    else:
        ovr = {}
        target_hint = str((merged.get("runtime") or {}).get("target") or "")
    if target_hint == "colab_cli" and config_path:
        colab_pro_path = base_dir / "configs" / "colab_pro.yaml"
        if colab_pro_path.is_file() and Path(str(config_path)).name != "colab_pro.yaml":
            pro_data = parse_structured_file(colab_pro_path)
            custom_again = parse_structured_file(resolve_repo_path(str(config_path), base_dir))
            cfg_dir_a = resolve_repo_path(str(config_path), base_dir).resolve().parent
            if not cfg_dir_a.is_relative_to(base_dir.resolve()):
                custom_again = _resolve_external_config_paths(custom_again, cfg_dir_a, base_dir)
            merged = _deep_merge(_deep_merge(merged, pro_data), custom_again)
    if overrides:
        if isinstance(ovr.get("voice"), str):
            v_str = ovr.pop("voice")
            ovr_v = dict(merged.get("voice") or {})
            if v_str != ovr_v.get("profile") and "rvc" not in ovr:
                # Switching to built-in profile resets any custom RVC fields inherited from prior config
                merged_voice = {"profile": v_str}
                merged = dict(merged)
                merged["voice"] = merged_voice
            else:
                ovr.setdefault("voice", {})["profile"] = v_str  # type: ignore[index]
        elif isinstance(ovr.get("voice"), dict) and ovr["voice"].get("profile"):
            v_str = str(ovr["voice"]["profile"])
            ovr_v = dict(merged.get("voice") or {})
            if v_str != ovr_v.get("profile") and "rvc" not in ovr["voice"]:
                merged = dict(merged)
                merged["voice"] = {"profile": v_str}
        merged = _deep_merge(merged, ovr)

    job_d = dict(merged.get("job") or {})
    input_d = dict(merged.get("input") or merged.get("content") or {})
    if "input_path" in input_d and not input_d.get("path"):
        input_d["path"] = input_d.pop("input_path")
    input_d.pop("input_path", None)
    input_d = {k: v for k, v in input_d.items() if k in ContentConfig.__dataclass_fields__}

    tr_d = {k: v for k, v in dict(merged.get("transcribe") or {}).items() if k in TranscribeConfig.__dataclass_fields__}
    out_d = dict(merged.get("output") or {})
    voice_d = dict(merged.get("voice") or {}) if isinstance(merged.get("voice"), dict) else {"profile": str(merged.get("voice") or "gawr_gura")}
    runtime_d = dict(merged.get("runtime") or {})
    colab_d = {k: v for k, v in dict(merged.get("colab") or {}).items() if k in ColabBridgeConfig.__dataclass_fields__}

    if not runtime_d.get("voice_python") and os.environ.get("VOICE_STUDIO_PYTHON"):
        runtime_d["voice_python"] = os.environ["VOICE_STUDIO_PYTHON"]
    if not runtime_d.get("rvc_assets_root") and os.environ.get("RVC_ASSETS_ROOT"):
        runtime_d["rvc_assets_root"] = os.environ["RVC_ASSETS_ROOT"]
    if not colab_d.get("agent_url") and os.environ.get("COLAB_AGENT_URL"):
        colab_d["agent_url"] = os.environ["COLAB_AGENT_URL"]
    if not colab_d.get("agent_token") and os.environ.get("COLAB_AGENT_TOKEN"):
        colab_d["agent_token"] = os.environ["COLAB_AGENT_TOKEN"]
    if not colab_d.get("local_mount_dir") and os.environ.get("GOOGLE_DRIVE_MOUNT_DIR"):
        colab_d["local_mount_dir"] = os.environ["GOOGLE_DRIVE_MOUNT_DIR"]

    voice_profile = build_voice_profile(voice_d, output_block=out_d)

    output_dir = str(out_d.get("dir") or job_d.get("output_dir") or job_d.get("output_root") or "output")
    target = str(runtime_d.get("target") or merged.get("target") or "local")
    if target == "colab_cli" and voice_profile.stage2.enabled:
        voice_profile.stage2.rvc_fp32 = bool((voice_d.get("rvc") or {}).get("rvc_fp32", True))
        voice_profile.stage2.intermediate_float = bool((voice_d.get("rvc") or {}).get("intermediate_float", True))
    if "mode" not in colab_d:
        colab_d["mode"] = target if target in ("colab_cli", "colab_agent", "colab_drive") else "colab_drive"

    qa_cfg = AudioQAConfig(
        duration_tolerance=float(out_d.get("duration_tolerance", 1.0)),
        min_snr_db=float(out_d.get("min_snr_db", 28.0)),
        target_lufs=float(out_d.get("target_lufs", -18.0)),
        lufs_tolerance=float(out_d.get("lufs_tolerance", 1.5)),
    )
    out_cfg = OutputConfig(
        dir=output_dir,
        prefix=str(out_d.get("prefix") or "studio"),
        formats=[str(f).lower() for f in (out_d.get("formats") or ["wav", "mp3", "mp4"])],
        fps=int(out_d.get("fps") or runtime_d.get("fps") or 60),
        mp3_bitrate=str(out_d.get("mp3_bitrate") or "192k"),
        aac_bitrate=str(out_d.get("aac_bitrate") or "192k"),
        nvenc_preset=str(out_d.get("nvenc_preset") or "p4"),
        nvenc_cq=int(out_d.get("nvenc_cq") or 21),
    )

    explicit_steps = merged.get("steps")
    caller_set_steps = bool(overrides and "steps" in overrides)
    in_path_str = str(input_d.get("path") or "")
    if explicit_steps:
        steps = validate_steps([str(s) for s in explicit_steps])
        if is_media_input_path(in_path_str) and not caller_set_steps and "transcribe" not in steps:
            steps = validate_steps(["transcribe", *steps])
    elif is_media_input_path(in_path_str):
        steps = list(ORDERED_STEPS)
    else:
        steps = list(DEFAULT_SCRIPT_STEPS)

    runtime_clean = {k: v for k, v in runtime_d.items() if k in RuntimeConfig.__dataclass_fields__}
    return PipelineConfig(
        job_id=str(job_d.get("id", "")),
        output_root=output_dir,
        resume=bool(job_d.get("resume", True)),
        dry_run=bool(job_d.get("dry_run", False)),
        clean_work=bool(job_d.get("clean_work", False)),
        target=target,
        voice=voice_profile.voice_id,
        phonetic_vocab_path=str(merged.get("phonetic_vocab_path", "src/voice_studio/content/phonetic_vocab.json")),
        content=ContentConfig(**input_d),
        transcribe=TranscribeConfig(**tr_d),
        output=out_cfg,
        steps=steps,
        runtime=RuntimeConfig(**runtime_clean),
        audio_qa=qa_cfg,
        colab=ColabBridgeConfig(**colab_d),
        voice_profile=voice_profile,
        raw=merged,
    )

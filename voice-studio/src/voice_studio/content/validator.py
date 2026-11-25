# -*- coding: utf-8 -*-
"""Input validator and starter template scaffolder for Voice & Media Studio."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List

from voice_studio import PROJECT_ROOT
from voice_studio.config.loader import is_media_input_path, resolve_repo_path
from voice_studio.content.loader import parse_script_input, preview_prepared_content
from voice_studio.core.security import safe_resolve_under

SCRIPT_EXTENSIONS = {".json", ".md", ".txt", ".srt", ".vtt"}
TEMPLATE_FILES = {
    "json": "input/templates/script_template.json",
    "md": "input/templates/script_template.md",
    "txt": "input/templates/script_template.txt",
    "srt": "input/templates/subtitles_template.srt",
    "vocab": "input/templates/custom_vocab.example.json",
}


def validate_input_source(
    input_path: str,
    voice: str = "gawr_gura",
    extra_vocab_path: str = "",
    base_dir: Path = PROJECT_ROOT,
) -> Dict[str, Any]:
    """Validates script/subtitle/media inputs, linked slide assets, and Thai phonetic readiness."""
    in_path = resolve_repo_path(input_path, base_dir)
    errors: List[str] = []
    warnings: List[str] = []
    if not in_path.is_file():
        return {"valid": False, "input_path": str(in_path), "errors": [f"Input file not found: {in_path}"], "warnings": []}

    suf = in_path.suffix.lower()
    if is_media_input_path(str(in_path)):
        return {
            "valid": True,
            "kind": "media_asr",
            "input_path": str(in_path),
            "size_bytes": in_path.stat().st_size,
            "errors": [],
            "warnings": [],
        }
    if suf not in SCRIPT_EXTENSIONS:
        return {
            "valid": False,
            "kind": "unknown",
            "input_path": str(in_path),
            "errors": [f"Unsupported extension '{suf}'. Supported scripts: {sorted(SCRIPT_EXTENSIONS)}"],
            "warnings": [],
        }

    doc, raw_slides = parse_script_input(in_path, base_dir=base_dir)
    if not raw_slides:
        errors.append("No segments/slides parsed from input.")
    for idx, sl in enumerate(raw_slides, start=1):
        items = sl.get("bilingual_item_scripts") or sl.get("items") or ([sl["script"]] if sl.get("script") else []) or sl.get("item_scripts")
        if not items:
            errors.append(f"Segment #{idx} has no narration text (`items` or `script`).")

    for field_name, expect_dir in (("slides_png_dir", True), ("pptx_path", False), ("extra_vocab_path", False), ("timeline_json", False), ("reveal_manifest", False)):
        val = str(doc.get(field_name) or (extra_vocab_path if field_name == "extra_vocab_path" else "")).strip()
        if not val:
            continue
        rel = (in_path.parent / val).resolve()
        target = rel if rel.exists() else resolve_repo_path(val, base_dir)
        ok = target.is_dir() if expect_dir else target.is_file()
        if not ok:
            errors.append(f"Referenced `{field_name}` not found: {target}")
        elif field_name == "slides_png_dir":
            pngs = sorted(target.glob("slide_*.png"))
            if len(pngs) < len(raw_slides):
                warnings.append(f"`slides_png_dir` has {len(pngs)} slide_*.png file(s) for {len(raw_slides)} segment(s); missing slides will use rendered cards.")

    preview = preview_prepared_content(str(in_path), voice=voice, is_inline_text=False, base_dir=base_dir)
    warnings.extend(preview.get("warnings") or [])
    return {
        "valid": len(errors) == 0,
        "kind": f"script_{suf.lstrip('.')}",
        "input_path": str(in_path),
        "segment_count": len(raw_slides),
        "errors": errors,
        "warnings": warnings,
        "preview": preview["segments"],
    }


def init_input_file(
    target_name: str,
    fmt: str = "",
    overwrite: bool = False,
    base_dir: Path = PROJECT_ROOT,
) -> Path:
    """Scaffolds a starter script or custom vocab file safely inside `input/` from `input/templates/`."""
    rel = target_name.strip().replace("\\", "/").lstrip("/")
    if not rel:
        raise ValueError("Target filename must not be empty.")
    fmt_key = (fmt or Path(rel).suffix.lstrip(".") or "json").lower()
    if fmt_key not in TEMPLATE_FILES:
        raise ValueError(f"Unsupported template format '{fmt_key}'. Choose from {sorted(TEMPLATE_FILES)}")
    if "." not in Path(rel).name and fmt_key != "vocab":
        rel = f"{rel}.{fmt_key}"
    elif fmt_key == "vocab" and not rel.endswith(".json"):
        rel = f"{rel}.json"

    src = base_dir / TEMPLATE_FILES[fmt_key]
    if not src.is_file():
        raise FileNotFoundError(f"Missing template: {src}")
    dst = safe_resolve_under(base_dir / "input", rel)
    if dst.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing input file: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def describe_input_formats(base_dir: Path = PROJECT_ROOT) -> Dict[str, Any]:
    """Returns clear specification of supported input formats, schema, and starter templates."""
    return {
        "input_dir": str(base_dir / "input"),
        "guide": str(base_dir / "input" / "README.md"),
        "schema": str(base_dir / "input" / "schema" / "input_script.schema.json"),
        "supported_formats": [
            {"ext": ".json", "role": "Structured multi-segment script (supports slides_png_dir, pptx_path, inline phonetic_vocab, extra_vocab_path)", "template": "input/templates/script_template.json"},
            {"ext": ".md", "role": "Markdown slides split by '# Heading' or '=== Slide 01: Title ==='", "template": "input/templates/script_template.md"},
            {"ext": ".txt", "role": "Plain-text narration split by blank lines", "template": "input/templates/script_template.txt"},
            {"ext": ".srt / .vtt", "role": "Timed subtitle cues (auto-strips cue numbers, timestamps, tags)", "template": "input/templates/subtitles_template.srt"},
            {"ext": ".wav / .mp3 / .mp4 / .m4a / .mkv", "role": "Audio/Video auto-transcribed via Whisper ASR then dubbed/rendered", "template": "input/media/"},
            {"ext": ".pptx + PNGs", "role": "Optional PowerPoint deck (auto-converted to 1080p PNGs if slides_png_dir omitted)", "template": "input/slides/"},
        ],
        "commands": [
            "studio.py input init my_talk.json",
            "studio.py input check input/my_talk.json",
            "studio.py run --input input/my_talk.json --voice gawr_gura --job my_job",
        ],
    }

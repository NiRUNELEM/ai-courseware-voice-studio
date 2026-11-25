# -*- coding: utf-8 -*-
"""Generic timeline manifest loader and segment timing builder."""

from __future__ import annotations

import json
import os
import re
import wave
from typing import Any, Dict, List, Optional


def _extract_slides_dict(raw_slides: Any, slides_dict: Dict[int, Any]) -> None:
    if isinstance(raw_slides, list):
        for rec in raw_slides:
            s_no = int(rec.get("slide_no", rec.get("slide", 0)))
            if s_no > 0:
                slides_dict[s_no] = rec
    elif isinstance(raw_slides, dict):
        for k, v in raw_slides.items():
            try:
                s_no = int(v.get("slide_no", k) if isinstance(v, dict) else k)
                slides_dict[s_no] = v
            except (ValueError, TypeError) as exc:
                print(f"[WARN] Skipping invalid slide key {k!r}: {exc}")


def load_timeline_manifest(timeline_path: str, deck_key: Optional[str] = None) -> Dict[int, Any]:
    """Parses timeline/reveal manifests cleanly across list, slides, or chapters schemas."""
    slides_dict: Dict[int, Any] = {}
    if not timeline_path or not os.path.exists(timeline_path):
        return slides_dict
    with open(timeline_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        _extract_slides_dict(data, slides_dict)
    elif isinstance(data, dict):
        if "chapters" in data and isinstance(data["chapters"], dict):
            ch_data = data["chapters"].get(str(deck_key)) if deck_key else None
            if not ch_data:
                for k in sorted(data["chapters"].keys()):
                    ch_data = data["chapters"][k]
                    break
            if isinstance(ch_data, dict):
                _extract_slides_dict(ch_data.get("slides", {}), slides_dict)
        elif "slides" in data and isinstance(data["slides"], (list, dict)):
            _extract_slides_dict(data["slides"], slides_dict)
        elif "timeline" in data and isinstance(data["timeline"], list):
            for item in data["timeline"]:
                s_no = int(item.get("slide_no", 0))
                if s_no > 0:
                    slides_dict.setdefault(s_no, {"slide_no": s_no, "items": []})["items"].append(item)
        else:
            for k, v in data.items():
                try:
                    slides_dict[int(k)] = v
                except (ValueError, TypeError) as exc:
                    print(f"[WARN] Skipping non-numeric manifest key {k!r}: {exc}")
    return slides_dict


def detect_total_slides(
    slides_dir: str,
    manifest: List[Dict[str, Any]],
    timeline_manifest: Dict[int, Any],
) -> int:
    """Infers total slide count dynamically from PNGs, manifest, or timeline."""
    counts = [len(manifest), len(timeline_manifest)]
    if slides_dir and os.path.isdir(slides_dir):
        png_nums = [
            int(m.group(1))
            for fn in os.listdir(slides_dir)
            if (m := re.match(r"slide_(\d+)\.png$", fn))
        ]
        if png_nums:
            counts.append(max(png_nums))
    return max(counts) if counts else 0


def read_wav_duration(wav_file: str) -> float:
    """Measures duration of a PCM WAV file in seconds."""
    with wave.open(wav_file, "rb") as wf:
        return wf.getnframes() / float(max(1, wf.getframerate()))

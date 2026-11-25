# -*- coding: utf-8 -*-
"""Generic PPTX Shape & Table-Row Hierarchical Reveal Extractor (1920x1080)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

CANVAS_W = 1920
CANVAS_H = 1080
CHROME_HEADER_MAX_Y = 170
CHROME_FOOTER_MIN_Y = 1005
CONTAINMENT_TOLERANCE_PX = 15
CARD_MIN_W = 220
CARD_MIN_H = 80
CHILD_CARD_MIN_W = 160
CHILD_CARD_MIN_H = 40


def emu_to_pixels(
    left: int, top: int, width: int, height: int, slide_w: int, slide_h: int
) -> Tuple[int, int, int, int, int, int]:
    """Converts EMU coordinates into 1920x1080 pixel integer box with canvas clamping."""
    sx = CANVAS_W / float(slide_w)
    sy = CANVAS_H / float(slide_h)
    x1 = max(0, min(CANVAS_W, int(round(left * sx))))
    y1 = max(0, min(CANVAS_H, int(round(top * sy))))
    x2 = max(x1, min(CANVAS_W, x1 + int(round(width * sx))))
    y2 = max(y1, min(CANVAS_H, y1 + int(round(height * sy))))
    return x1, y1, x2, y2, x2 - x1, y2 - y1


def is_contained(inner_box: List[int], outer_box: List[int], tol: int = CONTAINMENT_TOLERANCE_PX) -> bool:
    """Checks if inner_box is geometrically inside outer_box within tolerance."""
    return (
        inner_box[0] >= outer_box[0] - tol
        and inner_box[1] >= outer_box[1] - tol
        and inner_box[2] <= outer_box[2] + tol
        and inner_box[3] <= outer_box[3] + tol
    )


def collect_shapes_recursive(shapes_collection: Any) -> List[Any]:
    """Recursively expands grouped PPTX shapes."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    flat_shapes: List[Any] = []
    for s in shapes_collection:
        if s.shape_type == MSO_SHAPE_TYPE.GROUP:
            flat_shapes.extend(collect_shapes_recursive(s.shapes))
        else:
            flat_shapes.append(s)
    return flat_shapes


def _make_reveal(
    reveal_id: str,
    level: int,
    parent_id: Optional[str],
    r_type: str,
    bbox: List[int],
    text: str,
    semantic_role: str,
    order: int = 0,
) -> Dict[str, Any]:
    return {
        "reveal_id": reveal_id,
        "order": order,
        "level": level,
        "parent_id": parent_id,
        "type": r_type,
        "bbox": bbox,
        "text": text,
        "semantic_role": semantic_role,
        "transition": {"effect": "alpha_dissolve", "duration_sec": 0.35},
    }


def extract_slide_hierarchy(
    slide: Any,
    s_no: int,
    slide_w: int,
    slide_h: int,
    static_slides: Optional[set[int]] = None,
) -> Dict[str, Any]:
    """Parses a PPTX slide into Level-0 Containers and Level-1 Child Reveals."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    if static_slides and s_no in static_slides:
        return {"slide_no": s_no, "title": f"Slide {s_no}", "has_progressive": False, "containers": [], "reveals_count": 0, "reveals": []}

    sy = CANVAS_H / float(slide_h)
    content_shapes: List[Dict[str, Any]] = []
    slide_title = ""

    for s in collect_shapes_recursive(slide.shapes):
        x1, y1, x2, y2, w, h = emu_to_pixels(s.left, s.top, s.width, s.height, slide_w, slide_h)
        if x1 <= 10 and y1 <= 10 and w >= CANVAS_W - 20 and h >= CANVAS_H - 20:
            continue
        if y1 < CHROME_HEADER_MAX_Y and y2 <= CHROME_HEADER_MAX_Y:
            if s.has_text_frame:
                t_txt = " ".join(p.text for p in s.text_frame.paragraphs if p.text).strip()
                if len(t_txt) > len(slide_title):
                    slide_title = t_txt
            continue
        if y1 >= CHROME_FOOTER_MIN_Y or w < 15 or h < 15:
            continue
        content_shapes.append({"shape": s, "box": [x1, y1, x2, y2], "w": w, "h": h, "area": w * h})

    containers: List[Dict[str, Any]] = []
    child_cards: List[Dict[str, Any]] = []
    table_reveals: List[Dict[str, Any]] = []
    text_shapes: List[Dict[str, Any]] = []
    content_shapes.sort(key=lambda x: x["area"], reverse=True)

    for item in content_shapes:
        s, box, w, h = item["shape"], item["box"], item["w"], item["h"]
        if s.has_table:
            tbl = s.table
            tbl_id = f"s{s_no:02d}_tbl"
            containers.append({
                "container_id": tbl_id, "id": tbl_id, "type": "table", "bbox": box,
                "style": {"bg_tone": "light", "corner_radius": 8},
                "heading": f"ตารางข้อมูล ({len(tbl.rows)} แถว)", "badge": None,
                "rows_count": len(tbl.rows), "cols_count": len(tbl.columns),
            })
            cur_y_emu = s.top
            for r_idx, row in enumerate(tbl.rows):
                r_box = [box[0], int(round(cur_y_emu * sy)), box[2], int(round((cur_y_emu + row.height) * sy))]
                r_text = " | ".join(c.text.strip().replace("\n", " ") for c in row.cells if c.text.strip())
                if r_idx == 0:
                    table_reveals.append(_make_reveal(f"{tbl_id}_header", 0, tbl_id, "table_header", r_box, r_text, "table_header"))
                else:
                    table_reveals.append(_make_reveal(f"{tbl_id}_r{r_idx:02d}", 1, tbl_id, "table_row", r_box, r_text, f"table_row_{r_idx}", r_idx))
                cur_y_emu += row.height
            continue

        if s.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
            parent_c = next((c for c in containers if is_contained(box, c["bbox"])), None)
            if parent_c is None and w >= CARD_MIN_W and h >= CARD_MIN_H:
                c_id = f"s{s_no:02d}_c{len(containers):02d}"
                c_tone = "light"
                try:
                    if s.fill.type == 1 and hasattr(s.fill.fore_color, "rgb"):
                        rgb = s.fill.fore_color.rgb
                        if rgb[0] < 50 and rgb[1] < 50 and rgb[2] < 50:
                            c_tone = "dark"
                except (AttributeError, TypeError, ValueError):
                    c_tone = "light"
                containers.append({
                    "container_id": c_id, "id": c_id,
                    "type": "card" if c_tone == "light" else "code_window",
                    "bbox": box,
                    "style": {"bg_tone": c_tone, "corner_radius": 14 if c_tone == "light" else 12},
                    "heading": "", "badge": None,
                })
            elif parent_c is not None and w >= CHILD_CARD_MIN_W and h >= CHILD_CARD_MIN_H:
                if parent_c.get("type") == "code_window" and h <= 58 and box[1] <= parent_c["bbox"][1] + 12:
                    continue
                card_text = " ".join(p.text for p in s.text_frame.paragraphs if p.text).strip() if s.has_text_frame else ""
                child_cards.append(_make_reveal(f"{parent_c['container_id']}_sub{len(child_cards):02d}", 1, parent_c["container_id"], "child_card", box, card_text, "child_card"))
            continue

        if s.has_text_frame:
            text_shapes.append(item)

    for ts in text_shapes:
        t_box = ts["box"]
        t_text = " ".join(p.text for p in ts["shape"].text_frame.paragraphs if p.text).strip()
        matched = False
        for cc in child_cards:
            if is_contained(t_box, cc["bbox"]):
                cc["text"] = t_text if not cc["text"] else (cc["text"] if t_text in cc["text"] else f"{cc['text']} | {t_text}")
                matched = True
                break
        if not matched:
            for c in containers:
                if is_contained(t_box, c["bbox"]) and not c["heading"]:
                    c["heading"] = t_text
                    break

    reveals: List[Dict[str, Any]] = []
    if table_reveals:
        for c in containers:
            if c["type"] != "table":
                r_type = "container_card" if c["type"] == "card" else "callout_card"
                reveals.append(_make_reveal(f"{c['container_id']}_rev", 0, None, r_type, c["bbox"], c["heading"], "container_card"))
        reveals.extend(table_reveals)
    elif child_cards:
        for c in containers:
            reveals.append(_make_reveal(f"{c['container_id']}_rev", 0, None, "container_card", c["bbox"], c["heading"], "container_card"))
        child_cards.sort(key=lambda x: (x["bbox"][1] // 50, x["bbox"][0]))
        reveals.extend(child_cards)
    else:
        for c in containers:
            reveals.append(_make_reveal(f"{c['container_id']}_rev", 0, None, "container_card", c["bbox"], c["heading"], "card_reveal"))

    reveals.sort(key=lambda r: (r["level"], r["bbox"][1] // 80, r["bbox"][0]))
    for idx, r in enumerate(reveals):
        r["order"] = idx
    return {
        "slide_no": s_no,
        "title": slide_title or f"Slide {s_no}",
        "has_progressive": len(reveals) > 1,
        "containers": containers,
        "reveals_count": len(reveals),
        "reveals": reveals,
    }


def extract_presentation_hierarchy(pptx_path: str, deck_key: str = "deck") -> Dict[str, Any]:
    """Extracts hierarchical reveals for an arbitrary PPTX presentation."""
    from pptx import Presentation

    if not os.path.isfile(pptx_path):
        raise FileNotFoundError(f"Presentation file not found: {pptx_path}")
    prs = Presentation(pptx_path)
    total_slides = len(prs.slides)
    static_slides = {1, total_slides - 1, total_slides} if total_slides >= 3 else {1}
    slides_map = {
        str(s_idx + 1): extract_slide_hierarchy(slide, s_idx + 1, prs.slide_width, prs.slide_height, static_slides=static_slides)
        for s_idx, slide in enumerate(prs.slides)
    }
    return {"deck_key": deck_key, "source_pptx": pptx_path, "total_slides": total_slides, "slides": slides_map}


def save_presentation_reveals(pptx_path: str, output_path: str, deck_key: str = "deck") -> Dict[str, Any]:
    """Extracts PPTX reveals for one presentation and writes them cleanly to output_path."""
    deck = extract_presentation_hierarchy(pptx_path, deck_key=deck_key)
    manifest = {
        "metadata": {
            "generator": "voice_studio.video.reveals",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "canvas_width": CANVAS_W,
            "canvas_height": CANVAS_H,
            "aspect_ratio": "16:9",
        },
        "slides": deck["slides"],
    }
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest

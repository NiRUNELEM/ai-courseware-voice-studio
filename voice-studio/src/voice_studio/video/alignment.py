# -*- coding: utf-8 -*-
"""Generic audio-to-visual reveal timestamp alignment engine and job timeline builder."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import wave
from typing import Any, Dict, List, Optional, Tuple

from voice_studio.audio.stage1_tts import estimate_speech_duration
from voice_studio.video.manifest import load_timeline_manifest

SILENCE_GAP_DEFAULT = 0.400


def get_audio_duration(file_path: str) -> float:
    """Measures audio duration in seconds with wave -> soundfile -> ffprobe cascade."""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Missing audio file: {file_path}")
    try:
        with wave.open(file_path, "rb") as w:
            frames, rate = w.getnframes(), w.getframerate()
            if rate > 0 and frames >= 0:
                return float(frames) / float(rate)
    except Exception as exc:
        print(f"[align] wave duration probe fallback for {file_path}: {exc}")
    try:
        import soundfile as sf

        return float(sf.info(file_path).duration)
    except Exception as exc:
        print(f"[align] soundfile duration probe fallback for {file_path}: {exc}")
    if shutil.which("ffprobe"):
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    raise RuntimeError(f"Cannot measure duration for: {file_path}")


def _reveal_item(rb: Any, slide_no: int, idx: int, a_idx: int, a_start: float, a_end: float) -> Dict[str, Any]:
    bbox = rb.get("bbox", rb) if isinstance(rb, dict) else rb
    rid = rb.get("reveal_id", f"s{slide_no:02d}_rev_{idx:02d}") if isinstance(rb, dict) else f"s{slide_no:02d}_rev_{idx:02d}"
    lvl = rb.get("level", 1) if isinstance(rb, dict) else 1
    pid = rb.get("parent_id", None) if isinstance(rb, dict) else None
    txt = rb.get("text", "") if isinstance(rb, dict) else ""
    typ = rb.get("type", "card") if isinstance(rb, dict) else "card"
    return {
        "item_index": idx,
        "reveal_id": rid,
        "level": lvl,
        "parent_id": pid,
        "type": typ,
        "bbox": bbox,
        "text": txt,
        "audio_item_index": a_idx,
        "audio_start_sec": a_start,
        "audio_end_sec": a_end,
        "duration_sec": round(max(0.0, a_end - a_start), 3),
        "start_sec": a_start,
        "end_sec": a_end,
    }


def resolve_alignment(
    raw_boxes: List[Any],
    audio_items: List[Dict[str, Any]],
    slide_no: int,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Aligns M visual reveal items to N audio items using the 4-scenario engine."""
    m_cnt = len(raw_boxes)
    n_cnt = len(audio_items)
    if m_cnt == 0:
        return "static_no_reveals", []
    if n_cnt == 0:
        return "static_no_audio", []

    if m_cnt == n_cnt:
        reveals = [
            _reveal_item(raw_boxes[k], slide_no, k, k, audio_items[k]["audio_start_sec"], audio_items[k]["audio_end_sec"])
            for k in range(m_cnt)
        ]
        return "exact_1_to_1", reveals

    if m_cnt > n_cnt:
        reveals = []
        q, r = divmod(m_cnt, n_cnt)
        box_idx = 0
        for j in range(n_cnt):
            count = q + 1 if j < r else q
            a = audio_items[j]
            sub_dur = round(a["duration_sec"] / float(count), 3) if count > 0 else 0.0
            for sub_k in range(count):
                v_start = round(a["audio_start_sec"] + sub_k * sub_dur, 3)
                v_end = round(v_start + sub_dur, 3) if sub_k < count - 1 else a["audio_end_sec"]
                reveals.append(_reveal_item(raw_boxes[box_idx], slide_no, box_idx, j, v_start, v_end))
                box_idx += 1
        return "subdivided_audio_buckets", reveals

    reveals = []
    for i in range(m_cnt):
        j = int(i * n_cnt / float(m_cnt))
        a_start = audio_items[j]["audio_start_sec"]
        a_end = audio_items[int((i + 1) * n_cnt / float(m_cnt))]["audio_start_sec"] if i < m_cnt - 1 else audio_items[-1]["audio_end_sec"]
        reveals.append(_reveal_item(raw_boxes[i], slide_no, i, j, a_start, a_end))
    return "proportional_anchoring", reveals


def _load_reveal_boxes(
    reveal_manifest_path: str = "",
    reveal_key: str = "",
    pptx_path: str = "",
    timeline_json_path: str = "",
) -> Dict[int, List[Any]]:
    out: Dict[int, List[Any]] = {}
    if reveal_manifest_path and os.path.isfile(reveal_manifest_path):
        data = json.loads(open(reveal_manifest_path, "r", encoding="utf-8").read())
        slides_raw: Dict[str, Any] = {}
        if "chapters" in data and isinstance(data["chapters"], dict):
            key = reveal_key or next(iter(sorted(data["chapters"].keys())), "")
            if key in data["chapters"]:
                slides_raw = dict(data["chapters"][key].get("slides") or {})
        elif "slides" in data and isinstance(data["slides"], dict):
            slides_raw = dict(data["slides"])
        elif isinstance(data, dict):
            slides_raw = data
        for k, v in slides_raw.items():
            if str(k).isdigit():
                boxes = v.get("reveals", v.get("items", [])) if isinstance(v, dict) else (v if isinstance(v, list) else [])
                out[int(k)] = list(boxes)
        return out

    if pptx_path and os.path.isfile(pptx_path):
        from voice_studio.video.reveals import extract_presentation_hierarchy

        deck = extract_presentation_hierarchy(pptx_path, deck_key=reveal_key or "deck")
        for k, v in (deck.get("slides") or {}).items():
            if str(k).isdigit():
                out[int(k)] = list(v.get("reveals") or [])
        return out

    if timeline_json_path and os.path.isfile(timeline_json_path):
        tl = load_timeline_manifest(timeline_json_path, deck_key=reveal_key or None)
        for s_no, v in tl.items():
            if isinstance(v, dict) and v.get("reveals"):
                out[int(s_no)] = list(v["reveals"])
    return out


def generate_job_timeline(
    job_dir: str,
    prepared_slides: List[Dict[str, Any]],
    silence_gap: float = SILENCE_GAP_DEFAULT,
    reveal_manifest_path: str = "",
    reveal_key: str = "",
    pptx_path: str = "",
    timeline_json_path: str = "",
) -> Dict[str, Any]:
    """Builds exact per-segment and global reveal timeline from measured job WAVs + optional reveals."""
    items_dir = os.path.join(job_dir, "audio_items_raw")
    reveal_map = _load_reveal_boxes(
        reveal_manifest_path=reveal_manifest_path,
        reveal_key=reveal_key,
        pptx_path=pptx_path,
        timeline_json_path=timeline_json_path,
    )

    timeline: Dict[str, Any] = {}
    global_cumulative_sec = 0.0
    for idx, sl in enumerate(prepared_slides, start=1):
        s_no = int(sl.get("slide_no", idx))
        scripts = list(sl.get("tts_items") or sl.get("item_scripts") or [sl.get("script", "")])
        item_files: List[str] = []
        if os.path.isdir(items_dir):
            expected_names = [f"slide_{s_no:02d}_item_{j:02d}.wav" for j in range(len(scripts))]
            if expected_names and all(os.path.isfile(os.path.join(items_dir, fn)) for fn in expected_names):
                item_files = expected_names
            else:
                item_files = sorted(
                    f for f in os.listdir(items_dir)
                    if f.startswith(f"slide_{s_no:02d}_") and f.endswith(".wav") and "_raw" not in f
                )

        audio_items: List[Dict[str, Any]] = []
        cur_t = 0.0
        if item_files:
            duration_source = "measured_wav"
            for j, fn in enumerate(item_files):
                dur = get_audio_duration(os.path.join(items_dir, fn))
                start_sec = round(cur_t, 3)
                end_sec = round(start_sec + dur, 3)
                audio_items.append({
                    "item_index": j,
                    "audio_file": f"audio_items_raw/{fn}",
                    "audio_start_sec": start_sec,
                    "audio_end_sec": end_sec,
                    "duration_sec": round(dur, 3),
                    "text": scripts[j] if j < len(scripts) else "",
                })
                cur_t = end_sec + silence_gap
        elif scripts:
            duration_source = "estimated_phonetic"
            for j, txt in enumerate(scripts):
                dur = estimate_speech_duration(str(txt), speed=0.90)
                start_sec = round(cur_t, 3)
                end_sec = round(start_sec + dur, 3)
                audio_items.append({
                    "item_index": j,
                    "audio_file": f"audio_items_raw/slide_{s_no:02d}_item_{j:02d}.wav",
                    "audio_start_sec": start_sec,
                    "audio_end_sec": end_sec,
                    "duration_sec": round(dur, 3),
                    "text": str(txt),
                })
                cur_t = end_sec + silence_gap
        else:
            duration_source = "none"

        total_slide_dur = audio_items[-1]["audio_end_sec"] if audio_items else 0.0
        raw_boxes = reveal_map.get(s_no, [])
        strategy, reveals = resolve_alignment(raw_boxes, audio_items, s_no)
        timeline[str(s_no)] = {
            "slide_no": s_no,
            "title": sl.get("title", f"Segment {s_no}"),
            "raw_title": sl.get("raw_title", sl.get("title", f"Segment {s_no}")),
            "total_duration_sec": round(total_slide_dur, 3),
            "global_start_sec": round(global_cumulative_sec, 3),
            "global_end_sec": round(global_cumulative_sec + total_slide_dur, 3),
            "duration_source": duration_source,
            "audio_file": f"audio_slides_wav/slide_{s_no:02d}.wav",
            "audio_mp3_file": f"audio_slides_mp3/slide_{s_no:02d}.mp3",
            "slide_png": f"slides_png/slide_{s_no:02d}.png",
            "has_reveals": len(reveals) > 0,
            "num_reveals": len(reveals),
            "num_audio_items": len(audio_items),
            "alignment_strategy": strategy,
            "reveals": reveals,
            "items": reveals,
            "audio_items": audio_items,
        }
        global_cumulative_sec += total_slide_dur
    return timeline


def validate_timeline(timeline: Dict[str, Any], expected_count: Optional[int] = None) -> int:
    """Validates alignment invariants and slide completeness. Returns violation count."""
    violations = 0
    if expected_count is not None and len(timeline) != expected_count:
        print(f"[FAIL] Expected {expected_count} segments, got {len(timeline)}")
        violations += 1
    for s_no_str, data in timeline.items():
        total_dur = float(data["total_duration_sec"])
        if total_dur <= 0.0:
            print(f"[FAIL] Segment {s_no_str} non-positive duration {total_dur}")
            violations += 1
        if float(data.get("global_end_sec", 0.0)) < float(data.get("global_start_sec", 0.0)):
            print(f"[FAIL] Segment {s_no_str} non-monotonic global range")
            violations += 1
        for it in data.get("reveals", []):
            if it["audio_end_sec"] > total_dur + 0.05 or it["audio_start_sec"] > it["audio_end_sec"]:
                print(f"[FAIL] Segment {s_no_str} invalid reveal timing {it.get('reveal_id')}")
                violations += 1
    return violations

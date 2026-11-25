# -*- coding: utf-8 -*-
"""Generic content loader and preparer for JSON/Markdown/TXT scripts or ASR-transcribed media."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from voice_studio import PROJECT_ROOT
from voice_studio.config.loader import is_media_input_path, resolve_repo_path
from voice_studio.config.models import PipelineConfig
from voice_studio.content.phonetic import clean_script_text, find_remaining_latin
from voice_studio.content.spacing import select_tts_text
from voice_studio.core.state import save_json_atomic

SRT_TS_RE = re.compile(
    r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{3})"
)


def _ts_to_sec(h: Optional[str], m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_srt_or_vtt(path: Path) -> Dict[str, Any]:
    """Parses SRT/VTT subtitle files into timed narration segments cleanly."""
    blocks = re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8-sig").strip())
    slides: List[Dict[str, Any]] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines or lines[0].upper().startswith(("WEBVTT", "NOTE", "STYLE")):
            continue
        ts_idx = next((i for i, ln in enumerate(lines) if SRT_TS_RE.match(ln)), -1)
        if ts_idx < 0:
            continue
        m = SRT_TS_RE.match(lines[ts_idx])
        assert m is not None
        start_s = _ts_to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
        end_s = _ts_to_sec(m.group(5), m.group(6), m.group(7), m.group(8))
        cue_text = " ".join(re.sub(r"<[^>]+>", "", ln).strip() for ln in lines[ts_idx + 1 :] if ln.strip()).strip()
        if cue_text:
            s_no = len(slides) + 1
            slides.append({
                "slide_no": s_no,
                "title": f"Subtitle {s_no:02d}",
                "duration_est_sec": round(max(0.0, end_s - start_s), 3),
                "items": [cue_text],
            })
    return {"title": path.stem, "slides": slides}


def parse_script_input(in_path: Path, base_dir: Path = PROJECT_ROOT) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Loads JSON, Markdown, TXT, or SRT/VTT script input into (doc_meta, raw_slides)."""
    suf = in_path.suffix.lower()
    if suf == ".json":
        doc = json.loads(in_path.read_text(encoding="utf-8"))
        if isinstance(doc, list):
            return {}, doc
        if isinstance(doc, dict):
            return doc, list(doc.get("slides") or doc.get("segments") or [])
        raise ValueError(f"Unsupported JSON root type in {in_path}")
    if suf in (".srt", ".vtt"):
        parsed = _parse_srt_or_vtt(in_path)
        return parsed, parsed["slides"]
    parsed = _parse_markdown_or_text(in_path)
    return parsed, parsed["slides"]


def resolve_extra_vocab(
    doc: Dict[str, Any],
    cfg: PipelineConfig,
    in_path: Path,
    base_dir: Path,
) -> Optional[Dict[str, Any]]:
    """Merges optional custom phonetic_vocab file or inline dictionary overrides."""
    merged: Dict[str, Any] = {"exact_compounds": [], "phrases": [], "words": {}}
    has_extra = False
    for raw_vp in (str(doc.get("extra_vocab_path") or ""), str(cfg.content.extra_vocab_path or "")):
        if not raw_vp.strip():
            continue
        p = Path(raw_vp)
        vp = p if p.is_absolute() else ((in_path.parent / p) if (in_path.parent / p).is_file() else resolve_repo_path(raw_vp, base_dir))
        if not vp.is_file():
            raise FileNotFoundError(f"Configured extra_vocab_path not found: {vp}")
        extra = json.loads(vp.read_text(encoding="utf-8"))
        merged["exact_compounds"].extend(list(extra.get("exact_compounds") or []))
        merged["phrases"].extend(list(extra.get("phrases") or []))
        merged["words"].update(dict(extra.get("words") or {}))
        has_extra = True
    inline = doc.get("phonetic_vocab")
    if isinstance(inline, dict):
        merged["exact_compounds"].extend(list(inline.get("exact_compounds") or []))
        merged["phrases"].extend(list(inline.get("phrases") or []))
        merged["words"].update(dict(inline.get("words") or {}))
        has_extra = True
    return merged if has_extra else None


def _parse_markdown_or_text(path: Path) -> Dict[str, Any]:
    raw_text = path.read_text(encoding="utf-8")
    lines = raw_text.splitlines()
    slides: List[Dict[str, Any]] = []
    cur_title = path.stem
    cur_lines: List[str] = []

    def _flush() -> None:
        body = "\n".join(cur_lines).strip()
        if not body:
            return
        paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        slides.append({"slide_no": len(slides) + 1, "title": cur_title, "items": paras})

    for line in lines:
        m_slide = re.match(r"^(?:===\s*Slide\s+\d+\s*:\s*(.*?)\s*===|#{1,3}\s+(.+))$", line.strip())
        if m_slide:
            _flush()
            cur_title = (m_slide.group(1) or m_slide.group(2) or path.stem).strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    _flush()

    if not slides and raw_text.strip():
        paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", raw_text) if p.strip()]
        slides = [{"slide_no": 1, "title": path.stem, "items": paras}]
    return {"title": path.stem, "slides": slides}


def _normalize_slide_records(
    raw_slides: List[Dict[str, Any]],
    cfg: PipelineConfig,
    vocab_path: Path,
    extra_vocab: Optional[Dict[str, Any]] = None,
) -> tuple[List[Dict[str, Any]], List[tuple[int, int, List[str]]]]:
    profile = cfg.voice_profile
    assert profile is not None
    text_mode = profile.stage1.text_mode
    clause_spacing = profile.stage1.clause_spacing
    filter_set = set(cfg.content.slides) if cfg.content.slides else None

    prepared: List[Dict[str, Any]] = []
    unknown_latin: List[tuple[int, int, List[str]]] = []

    for idx, sl in enumerate(raw_slides, start=1):
        s_no = int(sl.get("slide_no", idx))
        if filter_set is not None and s_no not in filter_set:
            continue
        raw_title = str(sl.get("raw_title") or sl.get("title") or f"Segment {s_no}")
        phon_title = str(sl.get("title") or clean_script_text(raw_title, vocab_path=vocab_path, extra_vocab=extra_vocab))

        raw_items_src = list(
            sl.get("bilingual_item_scripts")
            or sl.get("items")
            or ([sl["script"]] if sl.get("script") else [])
            or sl.get("item_scripts")
            or []
        )
        raw_items_clean = [str(x).strip() for x in raw_items_src if str(x).strip()]
        raw_items = [re.sub(r"\[เว้นจังหวะ[^\]]*\]", "", x).strip() for x in raw_items_clean]

        phon_items_src = list(sl.get("item_scripts_phonetic") or sl.get("phonetic_items") or [])
        phon_items: List[str] = []
        tts_items: List[str] = []
        has_raw_english_src = bool(sl.get("bilingual_item_scripts") or sl.get("items") or sl.get("script"))
        for j, raw_it in enumerate(raw_items_clean):
            if not has_raw_english_src and j < len(phon_items_src) and str(phon_items_src[j]).strip():
                phon_it = clean_script_text(str(phon_items_src[j]).strip(), vocab_path=vocab_path, extra_vocab=extra_vocab)
            else:
                phon_it = clean_script_text(raw_it, vocab_path=vocab_path, extra_vocab=extra_vocab)
            leaks = find_remaining_latin(phon_it)
            if leaks:
                unknown_latin.append((s_no, j, leaks))
            phon_items.append(phon_it)
            tts_items.append(select_tts_text(raw_it, phon_it, text_mode=text_mode, clause_spacing=clause_spacing))

        spoken_phon = " ".join(phon_items)
        prepared.append(
            {
                "slide_no": s_no,
                "title": phon_title,
                "raw_title": raw_title,
                "duration_est_sec": float(sl.get("duration_est_sec", 0.0)),
                "pacing": str(sl.get("pacing", "normal")),
                "script": str(sl.get("script") or " ".join(raw_items)),
                "bilingual_item_scripts": raw_items,
                "spoken_phonetic": spoken_phon,
                "item_scripts": phon_items,
                "item_scripts_phonetic": phon_items,
                "phonetic_items": phon_items,
                "tts_items": tts_items,
            }
        )
    return prepared, unknown_latin


def load_and_prepare_content(
    cfg: PipelineConfig,
    job_dir: Path,
    base_dir: Path = PROJECT_ROOT,
) -> Dict[str, Any]:
    """Resolves script or transcribed ASR input, normalizes phonetics & spacing, and saves prepared_slides.json."""
    vocab_path = resolve_repo_path(cfg.phonetic_vocab_path, base_dir)
    if not cfg.content.path:
        raise ValueError("Missing input.path in configuration")

    transcribed_script = job_dir / "transcript" / "script_from_audio.json"
    if is_media_input_path(cfg.content.path):
        if not transcribed_script.is_file():
            raise FileNotFoundError(
                f"Input '{cfg.content.path}' is audio/video; run 'transcribe' step first so {transcribed_script} exists."
            )
        in_path = transcribed_script
    else:
        in_path = resolve_repo_path(cfg.content.path, base_dir)
        if not in_path.is_file():
            raise FileNotFoundError(f"Input file not found: {in_path}")

    meta: Dict[str, Any] = {
        "mode": cfg.content.mode or "custom",
        "title": cfg.content.title or cfg.output.prefix or in_path.stem,
        "slides_png_dir": "",
        "pptx_path": "",
        "timeline_json": "",
        "reveal_manifest": "",
        "reveal_key": str(cfg.content.reveal_key or ""),
        "zero_latin_phonetic": bool(cfg.content.zero_latin_phonetic),
    }

    doc, raw_slides = parse_script_input(in_path, base_dir=base_dir)
    if doc.get("title") and not cfg.content.title:
        meta["title"] = str(doc["title"])
    for field in ("slides_png_dir", "pptx_path", "timeline_json", "reveal_manifest", "extra_vocab_path"):
        if doc.get(field) and not getattr(cfg.content, field):
            raw_val = str(doc[field])
            rel_in = (in_path.parent / raw_val).resolve()
            meta[field] = str(rel_in if rel_in.exists() else resolve_repo_path(raw_val, base_dir))
    if doc.get("reveal_key") and not meta["reveal_key"]:
        meta["reveal_key"] = str(doc["reveal_key"])

    for field in ("slides_png_dir", "pptx_path", "timeline_json", "reveal_manifest"):
        val = str(getattr(cfg.content, field) or "")
        if val:
            meta[field] = str(resolve_repo_path(val, base_dir))

    extra_vocab = resolve_extra_vocab(doc, cfg, in_path, base_dir)
    prepared_slides, unknown_latin = _normalize_slide_records(raw_slides, cfg, vocab_path, extra_vocab=extra_vocab)
    if not prepared_slides:
        raise ValueError(f"No narration segments found in {in_path}")
    if cfg.content.zero_latin_phonetic and unknown_latin:
        raise ValueError(f"Unmapped Latin tokens detected with zero_latin_phonetic=True: {unknown_latin[:5]}")
    if unknown_latin:
        print(f"[content] Notice: {len(unknown_latin)} item(s) have unmapped Latin words (kept phoneticized where mapped): {unknown_latin[:3]}")

    work_dir = job_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    prepared_path = work_dir / "prepared_slides.json"
    save_json_atomic(prepared_path, {"meta": meta, "slides": prepared_slides})
    slides_json_path = work_dir / "slides_phonetic.json"
    slides_json_path.write_text(json.dumps(prepared_slides, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "meta": meta,
        "slides": prepared_slides,
        "prepared_path": str(prepared_path),
        "slides_json_path": str(slides_json_path),
    }


def preview_prepared_content(
    input_path_or_text: str,
    voice: str = "gawr_gura",
    is_inline_text: bool = False,
    base_dir: Path = PROJECT_ROOT,
    vocab_path: str = "",
) -> Dict[str, Any]:
    """Previews normalized Thai phonetic output, spacing, and pronunciation pitfalls before TTS."""
    from voice_studio.config.voices import resolve_voice_profile
    from voice_studio.content.phonetic import DEFAULT_VOCAB_PATH

    eff_vocab = resolve_repo_path(vocab_path, base_dir) if vocab_path else DEFAULT_VOCAB_PATH
    vp = resolve_voice_profile(voice)
    if is_inline_text:
        clean_ph = clean_script_text(input_path_or_text, vocab_path=eff_vocab)
        tts_txt = select_tts_text(input_path_or_text, clean_ph, text_mode=vp.stage1.text_mode, clause_spacing=vp.stage1.clause_spacing)
        return {
            "voice": vp.voice_id,
            "raw_text": input_path_or_text,
            "phonetic_text": clean_ph,
            "tts_text": tts_txt,
            "remaining_latin": find_remaining_latin(clean_ph),
        }

    in_path = resolve_repo_path(input_path_or_text, base_dir)
    if not in_path.is_file():
        raise FileNotFoundError(f"Input file not found: {in_path}")
    from voice_studio.config.loader import load_pipeline_config

    cfg = load_pipeline_config(overrides={"voice": voice}, base_dir=base_dir)
    doc, raw_slides = parse_script_input(in_path, base_dir=base_dir)
    extra_vocab = resolve_extra_vocab(doc, cfg, in_path, base_dir)
    segments = []
    warnings = []
    for idx, sl in enumerate(raw_slides, start=1):
        s_no = int(sl.get("slide_no", idx))
        items = list(sl.get("bilingual_item_scripts") or sl.get("items") or ([sl["script"]] if sl.get("script") else []) or sl.get("item_scripts") or [])
        norm_items = []
        for it in items:
            raw_s = str(it).strip()
            ph = clean_script_text(raw_s, vocab_path=eff_vocab, extra_vocab=extra_vocab)
            tts_s = select_tts_text(raw_s, ph, text_mode=vp.stage1.text_mode, clause_spacing=vp.stage1.clause_spacing)
            latin = find_remaining_latin(ph)
            if latin:
                warnings.append(f"Slide {s_no}: unmapped Latin tokens {latin}")
            norm_items.append({"raw": raw_s, "phonetic": ph, "tts": tts_s})
        segments.append({"slide_no": s_no, "title": sl.get("title", f"Segment {s_no}"), "items": norm_items})
    return {"input_path": str(in_path), "voice": vp.voice_id, "segment_count": len(segments), "segments": segments, "warnings": warnings}

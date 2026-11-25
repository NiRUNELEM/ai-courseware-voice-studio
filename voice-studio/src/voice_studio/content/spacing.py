# -*- coding: utf-8 -*-
"""Royal Society Thai clause spacing and human breath-group prosody formatter for Stage 1 TTS."""

from __future__ import annotations

import re
from typing import List

from voice_studio.content.thai_reading import expand_pause_markers, normalize_thai_numbers_and_symbols

try:
    from pythainlp.tokenize import word_tokenize as _pythai_word_tokenize
except ImportError:  # pragma: no cover - fallback when pythainlp is absent
    _pythai_word_tokenize = None

_PRIMARY_LINKERS = {
    "เพื่อ", "โดยเฉพาะ", "โดยที่", "ซึ่ง", "และที่สำคัญ", "หรือเรียกว่า",
    "พร้อมกับ", "รวมถึง", "ทำให้", "ก่อนจะ", "ก่อนที่", "หลังจาก", "ตั้งแต่",
    "จนถึง", "ภายใต้", "สำหรับ", "หาก", "เมื่อ",
}
_SECONDARY_LINKERS = {"และ", "หรือ", "แต่", "โดย"}
_NO_BREAK_BEFORE_PREFIXES = ("กันก่อน", "เลยค่ะ", "นะคะ", "ด้วยกัน", "เสมอค่ะ", "ทันทีค่ะ", "ทั้งหมดค่ะ", "ไว้ก่อน", "เจเอส", "ดับเบิลยู", "ไอดี", "คุกกี้", "คีย์", "ลิมิต", "เบส")
_GLUE_LEFT_WORDS = ("บทที่", "สไลด์ที่", "ขั้นที่", "ขั้นตอนที่", "เสาหลักที่", "รูปแบบที่", "กลุ่มที่", "ส่วนที่", "ประการที่", "โมเดลที่", "กรณีที่", "เอลิเซีย", "เจ", "โอ", "เอพีไอ", "อาร์กอน", "ไทป์บ็อกซ์", "เซิร์ฟเวอร์", "โอเพนเอพีไอ", "อาจารย์", "ปิยะ")
_DISCOURSE_BREAK_BEFORE = (
    "อย่างไรก็ตาม", "นอกจากนี้", "ดังนั้น", "เพราะฉะนั้น", "ในทางกลับกัน",
    "โดยเฉพาะอย่างยิ่ง", "สรุปได้ว่า", "กล่าวโดยสรุป", "และที่สำคัญ",
    "ในขณะเดียวกัน", "ไม่เพียงเท่านั้น", "อย่างไรก็ดี", "ต่อมา", "จากนั้น", "สุดท้ายนี้",
)
_INTRO_BREAK_AFTER = (
    "ประกอบด้วย", "ได้แก่", "กล่าวคือ", "หมายความว่า", "ยกตัวอย่างเช่น", "ตัวอย่างเช่น",
    "ประการแรก", "ประการที่สอง", "ประการที่สาม", "ประการสุดท้าย",
)


def _tokenize_thai_words(text: str) -> List[str]:
    if _pythai_word_tokenize is not None:
        return list(_pythai_word_tokenize(text, engine="newmm"))
    return re.findall(r"\S+|\s+", text)


def _valid_split_Boundary(left: str, right: str) -> bool:
    if len(left) < 28 or len(right) < 22:
        return False
    if right.startswith(_NO_BREAK_BEFORE_PREFIXES):
        return False
    return not left.endswith(_GLUE_LEFT_WORDS)


def _chunk_long_clause(clause: str, max_chars: int = 125) -> str:
    """Subdivides long comma-free Thai clauses into comfortable human breath groups (<= max_chars)."""
    s = clause.strip()
    if len(s) <= max_chars:
        return s

    # Subdivide clause if uninterrupted length > max_chars OR dense spaced tokens > 9
    parts_out: List[str] = []
    remaining = s
    while len(remaining) > max_chars or remaining.count(" ") >= 9:
        tokens = _tokenize_thai_words(remaining)
        target_mid = min(max_chars - 12, max(38, int(len(remaining) * 0.55)))
        best_idx = -1
        best_score = 10**9
        cur_len = 0

        for idx, tok in enumerate(tokens):
            tok_s = tok.strip()
            if (tok_s in _PRIMARY_LINKERS or tok_s in _SECONDARY_LINKERS) and 28 <= cur_len <= max_chars + 12:
                left = "".join(tokens[:idx]).rstrip(" ,")
                right = "".join(tokens[idx:]).lstrip(" ,")
                # Never break short compound verb/noun pairs joined by และ/หรือ without preceding space
                prev_char = tokens[idx - 1][-1:] if idx > 0 else ""
                if tok_s in ("และ", "หรือ") and not prev_char.isspace() and len(right) < 42:
                    cur_len += len(tok)
                    continue
                if _valid_split_Boundary(left, right):
                    tier_penalty = 0 if tok_s in _PRIMARY_LINKERS else 25
                    score = abs(cur_len - target_mid) + tier_penalty
                    if score < best_score:
                        best_score = score
                        best_idx = idx
            cur_len += len(tok)

        if best_idx < 0 and (len(remaining) > 105 or remaining.count(" ") >= 9):
            cur_len = 0
            for idx, tok in enumerate(tokens):
                if tok.isspace() and 34 <= cur_len <= max_chars:
                    left = "".join(tokens[:idx]).rstrip(" ,")
                    right = "".join(tokens[idx + 1 :]).lstrip(" ,")
                    if _valid_split_Boundary(left, right) and (left.count(" ") >= 3 or len(left) >= 42):
                        score = abs(cur_len - target_mid)
                        if score < best_score:
                            best_score = score
                            best_idx = idx + 1
                cur_len += len(tok)

        if best_idx <= 0:
            break
        left = "".join(tokens[:best_idx]).rstrip(" ,")
        right = "".join(tokens[best_idx:]).lstrip(" ,")
        if not left or not right or len(left) < 24:
            break
        parts_out.append(left)
        remaining = right

    parts_out.append(remaining)
    return ", ".join(p for p in parts_out if p)


def _chunk_long_thai_clauses(text: str, max_chars: int = 125) -> str:
    """Applies breath-group chunking across each punctuated segment without altering delimiters."""
    segments = re.split(r"(\.\.\.|[,.!?])", text)
    rebuilt: List[str] = []
    for seg in segments:
        if seg in ("...", ",", ".", "!", "?"):
            rebuilt.append(seg)
        elif seg.strip():
            lead_space = " " if seg.startswith(" ") else ""
            trail_space = " " if seg.endswith(" ") else ""
            rebuilt.append(f"{lead_space}{_chunk_long_clause(seg.strip(), max_chars=max_chars)}{trail_space}")
    return "".join(rebuilt)


def add_thai_clause_spacing(text: str) -> str:
    """Inserts Royal Society minor/major breaks and human breath-group pauses without distorting words."""
    t = expand_pause_markers(text or "").strip()
    if not t:
        return ""

    # Standard greeting pauses
    t = re.sub(r"สวัสดี\s*(ค่ะ|ครับ)\s*(ผู้เรียนทุกท่าน|ทุกท่าน)", r"สวัสดี\1 \2,", t)
    t = t.replace("ค่ะ ในบทเรียนนี้", "ค่ะ. ในบทเรียนนี้")

    # Major sentence breaks after internal polite particles followed by another sentence (universal period stop)
    t = re.sub(
        r"([ก-ฮะ-์]{2,})\s*(ค่ะ|นะคะ|ครับ|นะครับ)\s+(?=(?!ผู้เรียนทุกท่าน|ทุกท่าน)\S)",
        lambda m: f"{m.group(1)}{' ' if ' ' in m.group(0).split(m.group(2))[0] else ''}{m.group(2)}. ",
        t,
    )

    # Minor breaks after enumerations/introducers
    for kw in _INTRO_BREAK_AFTER:
        t = re.sub(rf"(?<!\.\s)(?<!,\s){re.escape(kw)}(?!\s*[,.\n…])\s*", f"{kw}, ", t)

    # Minor breaks before major discourse transitions when preceded by sufficient context
    for kw in _DISCOURSE_BREAK_BEFORE:
        t = re.sub(rf"(?<=[^\s,.…])\s+({re.escape(kw)})", r", \1", t)
        t = re.sub(rf"(?<=[ก-ฮะ-์]{{2}})\s+({re.escape(kw)})\s+", r", \1 ", t)

    # Break before step/pillar/pattern labels inside long enumerations
    t = re.sub(
        r"(?<=[ก-ฮะ-์])\s+((?:เสาหลักที่|รูปแบบที่|ขั้นตอนที่|ขั้นที่|กลุ่มที่|ส่วนที่|ประการที่|โมเดลที่|กรณีที่|ข้อที่|ใบที่|ไฟล์ที่)(?:\s*[^\s,]+)?)",
        r", \1",
        t,
    )

    # Subdivide only genuinely long uninterrupted breath groups
    t = re.sub(r"\s*,\s*", ", ", t)
    t = re.sub(r"\s*\.\.\.\s*", "... ", t)
    t = _chunk_long_thai_clauses(t, max_chars=125)

    # Final punctuation hygiene
    t = re.sub(r"(?:,\s*){2,}", ", ", t)
    t = re.sub(r"\.\.\.\s*,+", "... ", t)
    t = re.sub(r",\s*\.\.\.", "... ", t)
    t = re.sub(r"\s+([,.!?])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def select_tts_text(
    raw_text: str,
    phonetic_text: str,
    text_mode: str = "phonetic_spaced",
    clause_spacing: bool = True,
) -> str:
    """Selects and formats the final Stage 1 TTS string according to voice profile config."""
    mode = (text_mode or "phonetic_spaced").strip().lower()
    if mode == "bilingual":
        base = expand_pause_markers(raw_text).strip() or expand_pause_markers(phonetic_text).strip()
        return add_thai_clause_spacing(base) if clause_spacing else base
    if mode == "raw":
        return expand_pause_markers(raw_text).strip()
    base = normalize_thai_numbers_and_symbols(phonetic_text.strip() or raw_text.strip())
    if mode in ("phonetic_spaced", "phonetic_tts") or clause_spacing:
        return add_thai_clause_spacing(base)
    return base

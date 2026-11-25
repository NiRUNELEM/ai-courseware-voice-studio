# -*- coding: utf-8 -*-
"""Shared Thai phonetic normalization engine for narration manifests and custom scripts."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from voice_studio import PROJECT_ROOT
from voice_studio.content.thai_reading import expand_pause_markers

DEFAULT_VOCAB_PATH = PROJECT_ROOT / "src" / "voice_studio" / "content" / "phonetic_vocab.json"


def load_vocab_dict(vocab_path: Optional[Path] = None, extra_vocab: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Loads base phonetic dictionary JSON and merges optional custom overrides cleanly."""
    vpath = Path(vocab_path or DEFAULT_VOCAB_PATH)
    vocab = json.loads(vpath.read_text(encoding="utf-8"))
    if extra_vocab:
        exact = list(extra_vocab.get("exact_compounds") or []) + list(vocab.get("exact_compounds") or [])
        phrases = list(extra_vocab.get("phrases") or []) + list(vocab.get("phrases") or [])
        words = dict(vocab.get("words") or {})
        words.update(dict(extra_vocab.get("words") or {}))
        vocab = {"exact_compounds": exact, "phrases": phrases, "words": words}
    return vocab


class PhoneticNormalizer:
    """Compiles compound, phrase, and word phonetic rules from a vocab JSON dictionary."""

    def __init__(self, vocab_path: Optional[Path] = None, extra_vocab: Optional[Dict[str, Any]] = None) -> None:
        self.vocab_path = Path(vocab_path or DEFAULT_VOCAB_PATH)
        vocab = load_vocab_dict(self.vocab_path, extra_vocab=extra_vocab)
        self._exact_compounds: List[Tuple[re.Pattern[str], str]] = []
        for key, val in vocab.get("exact_compounds", []):
            if not key:
                continue
            prefix = r"(?<![A-Za-z0-9_])" if key[0].isalnum() else ""
            suffix = r"(?![A-Za-z0-9_])" if key[-1].isalnum() else ""
            self._exact_compounds.append((re.compile(prefix + re.escape(key) + suffix), val))

        raw_phrases = list(vocab.get("phrases") or [])
        self._phrase_lookup = {k.lower(): v for k, v in raw_phrases}
        phrase_keys = sorted(raw_phrases, key=lambda x: len(x[0]), reverse=True)
        self._phrase_re = (
            re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(k) for k, _ in phrase_keys) + r")(?![A-Za-z0-9_])", re.IGNORECASE)
            if phrase_keys
            else None
        )

        words_map = dict(vocab.get("words") or {})
        self._word_exact = dict(words_map)
        self._word_lower = {k.lower(): v for k, v in words_map.items()}
        word_keys_sorted = sorted(words_map.keys(), key=len, reverse=True)
        self._word_re = (
            re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(k) for k in word_keys_sorted) + r")(?![A-Za-z])", re.IGNORECASE)
            if word_keys_sorted
            else None
        )

    def normalize(self, text: str) -> str:
        """Converts script narration into Thai phonetic text."""
        if not text:
            return ""
        out = expand_pause_markers(text)
        out = out.replace("`", " ")
        for pat, thai in self._exact_compounds:
            out = pat.sub(f" {thai} ", out)
        if self._phrase_re is not None:
            out = self._phrase_re.sub(lambda m: f" {self._phrase_lookup[m.group(1).lower()]} ", out)
        out = out.replace("$", " ").replace("_", " ").replace("%", " เปอร์เซ็นต์")
        out = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z])", " ดอท ", out)
        # Preserve hyphens inside Thai phonetic syllables & pure numeric codes (ISBN/NIST); only split Latin hyphenated tokens.
        out = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])-(?=[A-Za-z])", " ", out)
        if self._phrase_re is not None:
            out = self._phrase_re.sub(lambda m: f" {self._phrase_lookup[m.group(1).lower()]} ", out)
        if self._word_re is not None:
            out = self._word_re.sub(
                lambda m: f" {self._word_exact.get(m.group(1), self._word_lower[m.group(1).lower()])} ",
                out,
            )
        # Normalize all database phonetic variants to natural Thai "ดาต้าเบส" (single word avoids high-tone "เบ้ส").
        out = re.sub(
            r"เดย์\s*-?\s*ทะ\s*-?\s*เบส|เด\s*-?\s*ทะ\s*-?\s*เบส|ดาต้า\s*-?\s*เบส|ดาตา\s*-?\s*เบส",
            "ดาต้าเบส",
            out,
        )
        out = re.sub(r"\s+([.,!?])", r"\1", out)
        out = re.sub(r"\s{2,}", " ", out).strip()
        return out


@lru_cache(maxsize=4)
def _cached_normalizer(vocab_str: str) -> PhoneticNormalizer:
    return PhoneticNormalizer(Path(vocab_str))


def clean_script_text(
    text: str,
    vocab_path: Optional[Path] = None,
    extra_vocab: Optional[Dict[str, Any]] = None,
) -> str:
    """Normalizes script narration into Thai phonetic text using cached normalizer or custom extra vocab."""
    vpath = str((vocab_path or DEFAULT_VOCAB_PATH).resolve())
    if extra_vocab:
        return PhoneticNormalizer(Path(vpath), extra_vocab=extra_vocab).normalize(text)
    return _cached_normalizer(vpath).normalize(text)


def find_remaining_latin(text: str) -> List[str]:
    """Returns any lingering Latin tokens left after phonetic normalization."""
    return re.findall(r"[A-Za-z]+", text)


def normalize_manifest(
    narration_manifest_path: Path,
    output_phonetic_path: Path,
    vocab_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Normalizes an arbitrary narration manifest JSON into a slide phonetic manifest JSON."""
    narration = json.loads(narration_manifest_path.read_text(encoding="utf-8"))
    segments = narration.get("segments") or narration.get("slides") or []

    output_slides: List[Dict[str, Any]] = []
    latin_leaks: List[tuple[int, str, List[str]]] = []

    for idx, seg in enumerate(segments):
        slide_no = int(seg.get("slide_no", idx + 1))
        raw_script = str(seg.get("script") or "")
        raw_items = list(seg.get("item_scripts") or seg.get("items") or ([raw_script] if raw_script else []))

        clean_title = clean_script_text(str(seg.get("title", f"สไลด์ {slide_no}")), vocab_path=vocab_path)
        spoken_phonetic = clean_script_text(raw_script, vocab_path=vocab_path)
        clean_items = [clean_script_text(str(item), vocab_path=vocab_path) for item in raw_items]

        for field_name, val in [("title", clean_title), ("spoken_phonetic", spoken_phonetic)]:
            leaks = find_remaining_latin(val)
            if leaks:
                latin_leaks.append((slide_no, field_name, leaks))
        for item_idx, item_val in enumerate(clean_items):
            leaks = find_remaining_latin(item_val)
            if leaks:
                latin_leaks.append((slide_no, f"item_{item_idx}", leaks))

        bilingual_items = [re.sub(r"\[เว้นจังหวะ[^\]]*\]", "", str(item)).strip() for item in raw_items]
        output_slides.append(
            {
                "slide_no": slide_no,
                "title": clean_title,
                "raw_title": seg.get("title", ""),
                "duration_est_sec": seg.get("duration_est_sec", 0),
                "pacing": seg.get("pacing", "normal"),
                "script": re.sub(r"\[เว้นจังหวะ[^\]]*\]", "", raw_script).strip(),
                "bilingual_item_scripts": bilingual_items,
                "spoken_phonetic": spoken_phonetic,
                "item_scripts": clean_items,
                "item_scripts_phonetic": clean_items,
                "phonetic_items": clean_items,
            }
        )

    output_phonetic_path.parent.mkdir(parents=True, exist_ok=True)
    output_phonetic_path.write_text(json.dumps(output_slides, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "slides": len(output_slides),
        "out_path": str(output_phonetic_path),
        "latin_leaks": latin_leaks,
    }

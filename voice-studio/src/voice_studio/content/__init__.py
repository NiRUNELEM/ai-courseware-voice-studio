# -*- coding: utf-8 -*-
"""Content ingestion, phonetic normalization, and Thai clause prosody spacing."""

from voice_studio.content.loader import load_and_prepare_content, parse_script_input, preview_prepared_content
from voice_studio.content.phonetic import (
    DEFAULT_VOCAB_PATH,
    PhoneticNormalizer,
    clean_script_text,
    find_remaining_latin,
    normalize_manifest,
)
from voice_studio.content.spacing import add_thai_clause_spacing, select_tts_text
from voice_studio.content.thai_reading import expand_pause_markers, normalize_thai_numbers_and_symbols
from voice_studio.content.validator import init_input_file, validate_input_source

__all__ = [
    "DEFAULT_VOCAB_PATH",
    "PhoneticNormalizer",
    "add_thai_clause_spacing",
    "clean_script_text",
    "expand_pause_markers",
    "find_remaining_latin",
    "init_input_file",
    "load_and_prepare_content",
    "normalize_manifest",
    "normalize_thai_numbers_and_symbols",
    "parse_script_input",
    "preview_prepared_content",
    "select_tts_text",
    "validate_input_source",
]

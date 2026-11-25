#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Voice & Media Studio CLI (Transcribe, TTS, RVC v2, Audio QA, Video Render, Google Colab)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voice_studio.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

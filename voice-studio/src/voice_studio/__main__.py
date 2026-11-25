# -*- coding: utf-8 -*-
"""Module entrypoint enabling `python -m voice_studio`."""

from __future__ import annotations

import sys

from voice_studio.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

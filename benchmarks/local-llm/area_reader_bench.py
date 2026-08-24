#!/usr/bin/env python3
"""Benchmark CLI wrapper for the shared area-reader runner."""

from __future__ import annotations

from pathlib import Path
import sys

REPO_TOOL_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_TOOL_ROOT))

from area_reader.workflow import *  # re-export benchmark helpers for tests/importers
from area_reader.workflow import main


if __name__ == "__main__":
    sys.exit(main())

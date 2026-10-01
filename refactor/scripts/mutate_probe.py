#!/usr/bin/env python3
"""Thin wrapper: the per-WP mutation probe named in plan §0.5 is ``refactor/gate1/mutate.py --module``.

    uv run python refactor/scripts/mutate_probe.py logic/stress.py [mutate.py options...]

is ``uv run python refactor/gate1/mutate.py --module logic/stress.py [options...]``. See that file's docstring.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "gate1" / "mutate.py"

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        sys.exit(f"usage: {Path(sys.argv[0]).name} <module> [mutate.py options]  (e.g. logic/stress.py --list)")
    sys.argv = [str(RUNNER), "--module", *sys.argv[1:]]
    runpy.run_path(str(RUNNER), run_name="__main__")

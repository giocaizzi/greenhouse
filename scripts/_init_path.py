#!/usr/bin/env python3
"""Path initialization helper for OpenClaw compatibility wrappers.

This module ensures the irrigation package can be imported from scripts/
by adding src/ to Python path. Import this at the top of any script.
"""

import sys
from pathlib import Path

# Add src to path for package imports
_skill_root = Path(__file__).parent.parent
_src_path = str(_skill_root / "src")

if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

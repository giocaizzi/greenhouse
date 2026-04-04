#!/usr/bin/env python3
"""OpenClaw compatibility wrapper for main CLI."""

import sys
from pathlib import Path

# Add src/ to path for package imports (needed when running directly, not via pip install)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tuya_irrigation.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

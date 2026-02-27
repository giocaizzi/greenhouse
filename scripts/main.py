#!/usr/bin/env python3
"""OpenClaw compatibility wrapper for main CLI.

This script ensures the irrigation CLI works seamlessly within OpenClaw skills context
by managing the Python path and calling the main package CLI.
"""

import sys
from pathlib import Path

# Add src to path for package imports
skill_root = Path(__file__).parent.parent
sys.path.insert(0, str(skill_root / "src"))

# Import and run main CLI
from tuya_irrigation.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

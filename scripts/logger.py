#!/usr/bin/env python3
"""OpenClaw compatibility wrapper for logger daemon.

This script ensures the irrigation logger works seamlessly within OpenClaw skills context.
"""

import sys
from pathlib import Path

# Add src to path for package imports
skill_root = Path(__file__).parent.parent
sys.path.insert(0, str(skill_root / "src"))

# Import and run logger daemon
from tuya_irrigation.logger_daemon import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

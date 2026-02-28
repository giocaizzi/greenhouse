#!/usr/bin/env python3
"""OpenClaw compatibility wrapper for report generator."""

import sys

# Initialize path for package imports
import _init_path  # noqa: F401

# Import and run report
from tuya_irrigation.report import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

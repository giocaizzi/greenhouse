#!/usr/bin/env python3
"""OpenClaw compatibility wrapper for logger daemon.

This script ensures the irrigation logger works seamlessly within OpenClaw skills context.
"""

import sys

# Initialize path for package imports
import _init_path  # noqa: F401

# Import and run logger daemon
from tuya_irrigation.logger_daemon import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

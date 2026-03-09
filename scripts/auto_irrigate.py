#!/usr/bin/env python3
"""HEARTBEAT entrypoint — delegates to CLI irrigate command.

Usage:
    python3 scripts/auto_irrigate.py              # Full pipeline
    python3 scripts/auto_irrigate.py --temp 15.0   # Override temp
    python3 scripts/auto_irrigate.py --dry-run     # Analysis only
"""

import sys

# Initialize path for package imports
import _init_path  # noqa: F401

from tuya_irrigation.cli import main  # noqa: E402

if __name__ == "__main__":
    # Translate: auto_irrigate.py [--cluster N] [--temp T] [--dry-run]
    # Into:     main.py irrigate N [--temp T] [--dry-run]

    import argparse

    parser = argparse.ArgumentParser(description="HEARTBEAT irrigation entrypoint")
    parser.add_argument("--cluster", type=int, default=1, help="Cluster ID (default: 1)")
    parser.add_argument("--temp", type=float, help="Override temperature")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only")
    parser.add_argument("--db", help="Database path")

    args = parser.parse_args()

    # Build CLI args for main.py irrigate
    cli_args = ["irrigate", str(args.cluster)]
    if args.temp is not None:
        cli_args.extend(["--temp", str(args.temp)])
    if args.dry_run:
        cli_args.append("--dry-run")
    if args.db:
        cli_args.extend(["--db", args.db])

    sys.argv = ["main.py"] + cli_args
    sys.exit(main())

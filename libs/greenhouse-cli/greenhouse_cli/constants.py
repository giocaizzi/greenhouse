"""CLI/TUI-side constants.

The CLI talks to the server over HTTP only and may not import ``greenhouse_core``, so the few
values it shares with the server live here (mirrored, not imported).
"""

# Server the CLI and TUI talk to when neither ``--server`` nor $IRRIGATION_SERVER_URL is set.
DEFAULT_SERVER_URL = "http://localhost:8000"

# Weekday bitmask with every day set (Mon=1 … Sun=64); mirrors core FULL_WEEKDAY_MASK.
ALL_WEEKDAYS = 127

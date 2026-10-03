"""CLI/TUI-side constants.

The CLI talks to the server over HTTP only and may not import ``greenhouse_core``, so the few
values it shares with the server live here (mirrored, not imported).
"""

# Server the CLI and TUI talk to when neither ``--server`` nor $IRRIGATION_SERVER_URL is set.
DEFAULT_SERVER_URL = "http://localhost:8000"

# Weekday bitmask with every day set (Mon=1 … Sun=64); mirrors core FULL_WEEKDAY_MASK.
ALL_WEEKDAYS = 127

# Time units for the TUI's relative ages and chart axes; mirror core SECONDS_PER_HOUR / SECONDS_PER_DAY.
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400
HOURS_PER_DAY = 24

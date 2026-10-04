"""Pure TUI builders: API payload → table rows, key/value rows, rich text (no widgets, no I/O).

One module per screen area; every builder is re-exported here so screens call ``render.<builder>``.
"""

from __future__ import annotations

from greenhouse_cli.tui.render.cluster_panels import (
    decision_panel,
    efficacy_rows,
    forecast_rows,
    insights_text,
    irrigator_info,
    learn_report,
    stats_rows,
)
from greenhouse_cli.tui.render.cluster_tabs import (
    config_rows,
    decision_rows,
    history_rows,
    plant_rows,
    sensor_rows,
    window_rows,
)
from greenhouse_cli.tui.render.settings import (
    account_line,
    global_config_rows,
    preference_rows,
    vacation_rows,
)
from greenhouse_cli.tui.render.system import (
    device_rows,
    job_rows,
    quality_rows,
    scheduler_panel_rows,
)

__all__ = [
    "account_line",
    "config_rows",
    "decision_panel",
    "decision_rows",
    "device_rows",
    "efficacy_rows",
    "forecast_rows",
    "global_config_rows",
    "history_rows",
    "insights_text",
    "irrigator_info",
    "job_rows",
    "learn_report",
    "plant_rows",
    "preference_rows",
    "quality_rows",
    "scheduler_panel_rows",
    "sensor_rows",
    "stats_rows",
    "vacation_rows",
    "window_rows",
]

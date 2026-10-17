"""MCP tool annotations: a human title plus behaviour hints for every published tool.

``fastapi-mcp`` 0.4 builds ``mcp.types.Tool`` objects with no ``title`` or
``annotations``, so clients (claude.ai) treat every tool as a destructive write and
cannot group or auto-approve the read-only ones. ``annotate_tools`` fills them in
after ``FastApiMCP`` has derived the tools.

The hints follow the route's HTTP method (MCP spec 2025-11-25, tool annotations):
``GET`` is read-only, ``DELETE`` destructive, ``PUT`` an idempotent non-destructive
write, ``POST`` a non-destructive write. ``_DESTRUCTIVE_POSTS`` overrides the
``POST`` default where a call cannot be taken back: starting water on real hardware
or stopping every irrigator at once.
"""

from mcp.types import Tool, ToolAnnotations

# Every tool needs a title: a new route fails `tests/server/test_mcp.py` until it gets one.
TOOL_TITLES: dict[str, str] = {
    # clusters
    "list_clusters": "List clusters",
    "create_cluster": "Create cluster",
    "get_cluster": "Get cluster",
    "update_cluster": "Update cluster",
    "delete_cluster": "Delete cluster",
    "get_cluster_detail": "Get cluster detail",
    "cluster_status": "Get cluster status",
    # plants
    "list_plants": "List plants in a cluster",
    "add_plant": "Add plant",
    "update_plant": "Update plant",
    "delete_plant": "Delete plant",
    "restore_plant": "Restore plant",
    "move_plant": "Move plant to another cluster",
    "list_all_plants": "List all plants",
    "get_plant": "Get plant",
    "get_plant_health": "Get plant health",
    "plant_health_timeline": "Get plant health timeline",
    "trigger_health_snapshot": "Take plant health snapshot",
    "sync_plants": "Sync plant care data",
    # irrigators
    "get_irrigator": "Get irrigator",
    "add_irrigator": "Add irrigator",
    "update_irrigator": "Update irrigator",
    "delete_irrigator": "Delete irrigator",
    "list_all_irrigators": "List all irrigators",
    "start_irrigator": "Start irrigator",
    "stop_irrigator": "Stop irrigator",
    "log_manual": "Log manual watering",
    # sensors
    "list_sensors": "List sensors in a cluster",
    "add_sensor": "Add sensor",
    "get_sensor": "Get sensor",
    "update_sensor": "Update sensor",
    "delete_sensor": "Delete sensor",
    "restore_sensor": "Restore sensor",
    "move_sensor": "Move sensor to another cluster",
    "list_all_sensors": "List all sensors",
    "list_sensor_assignments": "List sensor assignments",
    # configs
    "get_config": "Get cluster config",
    "set_config": "Set cluster config",
    "get_effective_config": "Get effective cluster config",
    "get_global_config": "Get global config",
    "update_global_config": "Update global config",
    # operations
    "irrigate": "Run smart irrigation",
    "check_all": "Check all clusters",
    "check_single": "Check cluster",
    "sync": "Sync sensor readings",
    "learn": "Get learning report",
    "history": "Get irrigation history",
    "stats": "Get cluster stats",
    "stats_export": "Export cluster stats (CSV)",
    "monitor": "Monitor cluster sensors",
    "cluster_insights": "Get cluster insights",
    "cluster_efficacy": "Get irrigation efficacy",
    "get_cluster_forecast": "Get cluster forecast",
    "quality_report": "Get data quality report",
    # charts
    "cluster_chart_data": "Get cluster chart data",
    "cluster_heatmap": "Get cluster heatmap",
    "cluster_overlay": "Get cluster overlay",
    "plant_chart_data": "Get plant chart data",
    # scheduler and health
    "list_jobs": "List scheduler jobs",
    "delete_job": "Delete scheduler job",
    "pause_scheduler": "Pause scheduler",
    "resume_scheduler": "Resume scheduler",
    "health": "Health check",
    "system_health": "Get system health",
    # alerts
    "list_alerts": "List alerts",
    "get_alert": "Get alert",
    "acknowledge_alert": "Acknowledge alert",
    "resolve_alert": "Resolve alert",
    "refresh_all_alerts": "Refresh all alerts",
    "refresh_cluster_alerts": "Refresh cluster alerts",
    # activity, decisions, windows
    "list_activity": "List activity",
    "list_decisions": "List irrigation decisions",
    "list_windows": "List watering windows",
    "add_window": "Add watering window",
    "update_window": "Update watering window",
    "delete_window": "Delete watering window",
    # preferences, vacation, search, bulk, auth
    "get_preferences": "Get preferences",
    "update_preferences": "Update preferences",
    "list_vacation_windows": "List vacation windows",
    "create_vacation_window": "Create vacation window",
    "update_vacation_window": "Update vacation window",
    "delete_vacation_window": "Delete vacation window",
    "global_search": "Search",
    "bulk_stop_all": "Emergency stop all irrigators",
    "login": "Log in",
    "logout": "Log out",
    "whoami": "Get current user",
}

# POSTs whose effect cannot be undone: water reaches real plants, or every irrigator stops.
_DESTRUCTIVE_POSTS = frozenset({"irrigate", "check_all", "check_single", "start_irrigator", "bulk_stop_all"})


def tool_annotations(name: str, method: str) -> ToolAnnotations:
    """Return the annotations for the tool ``name`` served by an HTTP ``method`` route."""
    title = TOOL_TITLES[name]
    match method.lower():
        case "get":
            return ToolAnnotations(title=title, readOnlyHint=True)
        case "delete":
            return ToolAnnotations(title=title, readOnlyHint=False, destructiveHint=True)
        case "put":
            return ToolAnnotations(title=title, readOnlyHint=False, destructiveHint=False, idempotentHint=True)
        case _:
            return ToolAnnotations(title=title, readOnlyHint=False, destructiveHint=name in _DESTRUCTIVE_POSTS)


def annotate_tools(tools: list[Tool], operation_map: dict[str, dict[str, str]]) -> None:
    """Set ``title`` and ``annotations`` on each tool in place (the list ``tools/list`` serves).

    Raises:
        KeyError: a tool has no entry in ``TOOL_TITLES``.
    """
    for tool in tools:
        tool.annotations = tool_annotations(tool.name, operation_map[tool.name]["method"])
        tool.title = tool.annotations.title

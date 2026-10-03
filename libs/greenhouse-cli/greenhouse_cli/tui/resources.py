"""Form field specs for every editable resource, pre-filled from API payloads.

Values offered in selects mirror the server's accepted vocabularies (plant
DB categories / needs, config modes); free-text stays free-text so a newer
server value never gets rejected client-side.
"""

from __future__ import annotations

from greenhouse_cli.tui.screens.forms import Field

ENVIRONMENTS = [("indoor", "indoor"), ("outdoor", "outdoor")]
CATEGORIES = [(c, c) for c in ("tropical", "fern", "succulent", "cacti", "fruit_tree")]
NEEDS = [(n, n) for n in ("low", "medium", "high")]
MODES = [("smart", "smart"), ("schedule", "schedule"), ("manual", "manual")]


def cluster_fields(cluster: dict | None = None) -> list[Field]:
    c = cluster or {}
    return [
        Field("name", "Name", value=c.get("name"), required=True),
        Field("location", "Location", value=c.get("location")),
        Field("environment", "Environment", "select", c.get("environment", "indoor"), ENVIRONMENTS, required=True),
    ]


def plant_fields(plant: dict | None = None) -> list[Field]:
    p = plant or {}
    return [
        Field("species", "Species", value=p.get("species"), required=True, placeholder="Monstera deliciosa"),
        Field("category", "Category", "select", p.get("category"), CATEGORIES),
        Field("water_needs", "Water needs", "select", p.get("water_needs"), NEEDS),
        Field("light_needs", "Light needs", "select", p.get("light_needs"), NEEDS),
        Field("ideal_temp_min", "Temp min °C", "float", p.get("ideal_temp_min")),
        Field("ideal_temp_max", "Temp max °C", "float", p.get("ideal_temp_max")),
        Field("ideal_humidity_min", "Humidity min %", "float", p.get("ideal_humidity_min")),
        Field("ideal_humidity_max", "Humidity max %", "float", p.get("ideal_humidity_max")),
        Field("notes", "Notes", value=p.get("notes")),
    ]


def sensor_fields(sensor: dict | None = None, plants: list[dict] | None = None) -> list[Field]:
    s = sensor or {}
    plant_options = [(f"{p['species']} (#{p['id']})", p["id"]) for p in plants or []]
    fields = []
    if sensor is None:
        fields.append(Field("tuya_device_id", "Tuya device ID", required=True))
    fields += [
        Field("name", "Name", value=s.get("name"), required=sensor is None),
        Field("type", "Model / type", value=s.get("type"), required=sensor is None, placeholder="tuya.tr301z"),
        Field("plant_id", "Plant", "select", s.get("plant_id"), plant_options),
    ]
    return fields


def irrigator_fields(irrigator: dict | None = None) -> list[Field]:
    i = irrigator or {}
    fields = []
    if irrigator is None:
        fields.append(Field("tuya_device_id", "Tuya device ID", required=True))
    fields += [
        Field("name", "Name", value=i.get("name"), required=irrigator is None),
        Field("type", "Model / type", value=i.get("type"), required=irrigator is None, placeholder="rainpoint.ik10pw"),
        Field("reservoir_l", "Reservoir (L)", "float", i.get("reservoir_l")),
        Field("flow_rate_l_per_min", "Flow (L/min)", "float", i.get("flow_rate_l_per_min")),
        Field("config", "Config (JSON)", "json", i.get("config"), placeholder='{"ip": "…", "version": "3.5"}'),
    ]
    return fields


def config_fields(config: dict | None = None) -> list[Field]:
    """Cluster or global irrigation config (blank = keep current / inherit)."""
    c = config or {}
    return [
        Field("mode", "Mode", "select", c.get("mode"), MODES),
        Field("duration_minutes", "Duration (min)", "int", c.get("duration_minutes")),
        Field("interval_hours", "Interval (h)", "int", c.get("interval_hours")),
        Field("auto_run", "Auto-run", "bool", c.get("auto_run")),
        Field("daily_cap_minutes", "Daily cap (min)", "int", c.get("daily_cap_minutes")),
        Field("max_events_per_day", "Max events / day", "int", c.get("max_events_per_day")),
        Field("quiet_start_hour", "Quiet from (0-23)", "int", c.get("quiet_start_hour")),
        Field("quiet_end_hour", "Quiet until (0-23)", "int", c.get("quiet_end_hour")),
    ]


def window_fields(window: dict | None = None) -> list[Field]:
    w = window or {}
    return [
        Field("start_hour", "Start hour (0-23)", "int", w.get("start_hour"), required=True),
        Field("end_hour", "End hour (excl.)", "int", w.get("end_hour"), required=True),
        Field("weekday_mask", "Weekday mask", "int", w.get("weekday_mask", 127), placeholder="Mon=1 … Sun=64; 127=all"),
        Field("label", "Label", value=w.get("label")),
    ]


def vacation_fields(window: dict | None = None) -> list[Field]:
    v = window or {}
    return [
        Field("starts_at", "Starts (UTC)", "datetime", v.get("starts_at"), required=window is None),
        Field("ends_at", "Ends (UTC)", "datetime", v.get("ends_at"), required=window is None),
        Field("contact_email", "Contact email", value=v.get("contact_email")),
        Field("notes", "Notes", value=v.get("notes")),
    ]


def preference_fields(prefs: dict) -> list[Field]:
    return [
        Field("units", "Units", "select", prefs.get("units"), [("metric", "metric"), ("imperial", "imperial")]),
        Field("timezone", "Timezone", value=prefs.get("timezone")),
        Field("theme", "Web theme", value=prefs.get("theme")),
        Field("default_cluster_id", "Default cluster ID", "int", prefs.get("default_cluster_id")),
        Field("refresh_interval_seconds", "Web refresh (s)", "int", prefs.get("refresh_interval_seconds")),
        Field("dry_run_global", "Global dry-run (never actuate)", "bool", prefs.get("dry_run_global")),
        Field("notify_manual", "Notify: manual runs", "bool", prefs.get("notify_manual")),
        Field("notify_emergency", "Notify: emergencies", "bool", prefs.get("notify_emergency")),
        Field("notify_alerts", "Notify: alerts", "bool", prefs.get("notify_alerts")),
        Field("notify_auto", "Notify: auto runs", "bool", prefs.get("notify_auto")),
    ]

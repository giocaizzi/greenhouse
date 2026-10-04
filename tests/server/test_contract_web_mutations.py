"""Characterization: every POST / DELETE web route — happy path + its validation / 404 branch.

Each case runs on a *fresh* seeded app (see ``test_contract_web_html.build_app``) so cases are
independent. Pinned per case in ``golden/web/mutations/<case>.json``:

* the request (method, url, form fields, extra headers);
* status, the contract headers (``location``, ``set-cookie``, ``content-type``, every ``HX-*``);
* the response body (as a list of lines, version-normalized) and the template name +
  sorted context keys of every ``TemplateResponse`` rendered;
* the DB effect: per table, rows added (full row), removed (primary keys) and changed
  (``column: [before, after]``) — diffed between just before and just after the request;
* the recorded fake device-adapter ``calls`` (actuating routes must never reach real devices).

There are no PUT routes in the web router. Nothing is normalized but the package version.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import inspect, text

from golden import assert_golden_json
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.deps import get_device_registry
from server import test_contract_web_html as _web
from server.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME
from server.test_contract_web_html import (
    ALERT_ACKED,
    ALERT_OPEN,
    ALERT_RESOLVED,
    ALOCASIA,
    EMPTY,
    INDOOR,
    IRR_INDOOR,
    IRR_OUTDOOR,
    LOQUAT,
    MONSTERA,
    OUTDOOR,
    S_AMBIENT,
    S_MONSTERA,
    VACATION_PAST,
    VACATION_UPCOMING,
    WINDOW,
    T,
    normalize,
    pinned_headers,
)

ADHOC_JOB = "adhoc_contract_job"

# Shared fixtures (pytest collects fixtures bound to module attributes).
make_client = _web.make_client
web_env = _web.web_env


# ── Per-case setup hooks (run against the fresh app before the "before" snapshot) ──────────────


def _cap_indoor_to_one_event(app, repo: IrrigationRepository) -> None:
    repo.set_irrigation_config(cluster_id=INDOOR, max_events_per_day=1)


def _device_start_fails(app, repo) -> None:
    app.state.fake_devices.irrigator.start_result = (False, "fake start failed")


def _device_stop_fails(app, repo) -> None:
    app.state.fake_devices.irrigator.stop_result = (False, "fake stop failed")


def _no_device_registry(app, repo) -> None:
    app.dependency_overrides[get_device_registry] = lambda: None


def _quiet_hours_now(app, repo) -> None:
    # Frozen clock is 10:00 UTC; make 09–12 quiet for the indoor cluster.
    repo.set_irrigation_config(cluster_id=INDOOR, quiet_start_hour=9, quiet_end_hour=12)


def _unregister_check_all(app, repo) -> None:
    from greenhouse_server.scheduler import CHECK_ALL_JOB_ID, scheduler

    scheduler.remove_job(CHECK_ALL_JOB_ID)


def _nothing() -> None:  # pragma: no cover — scheduled but never fired (scheduler starts paused)
    return None


@dataclass(frozen=True)
class Mut:
    """One mutating request against a fresh seeded app."""

    name: str
    method: str
    url: str
    form: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    setup: Callable | None = None
    app_kwargs: dict[str, Any] = field(default_factory=dict)
    running_scheduler: bool = False  # start the process scheduler (paused) + register an ad-hoc job


M = Mut
REAL_AUTH = {"bypass_auth": False}
HX = {"HX-Request": "true"}

PLANT_FORM = {
    "species": "Dracaena marginata",
    "category": "tropical",
    "water_needs": "low",
    "light_needs": "medium",
    "ideal_temp_min": "16",
    "ideal_temp_max": "28",
    "ideal_humidity_min": "40",
    "ideal_humidity_max": "60",
    "notes": "Corner",
}
PREFS_FORM = {
    "units": "imperial",
    "timezone": "UTC",
    "theme": "light",
    "refresh_interval_seconds": "15",
    "default_cluster_id": str(OUTDOOR),
    "dry_run_global": "on",
    "notify_manual": "on",
    "notify_alerts": "on",
}

MUTATIONS: tuple[Mut, ...] = (
    # alerts
    M("ack_alert", "POST", f"/alerts/{ALERT_OPEN}/ack", headers=HX),
    M("ack_alert__already_acked", "POST", f"/alerts/{ALERT_ACKED}/ack", headers=HX),
    M("ack_alert__missing", "POST", "/alerts/999/ack", headers=HX),
    M("resolve_alert", "POST", f"/alerts/{ALERT_ACKED}/resolve", headers=HX),
    M("resolve_alert__already_resolved", "POST", f"/alerts/{ALERT_RESOLVED}/resolve", headers=HX),
    M("resolve_alert__missing", "POST", "/alerts/999/resolve", headers=HX),
    M("sync_alerts", "POST", "/alerts/sync", headers=HX),
    # analytics / scheduler
    M("scheduler_delete_job__not_running", "POST", f"/scheduler/jobs/{ADHOC_JOB}/delete", headers=HX),
    M("scheduler_delete_job", "POST", f"/scheduler/jobs/{ADHOC_JOB}/delete", headers=HX, running_scheduler=True),
    M("scheduler_delete_job__core", "POST", "/scheduler/jobs/check_all/delete", headers=HX, running_scheduler=True),
    M("scheduler_delete_job__unknown", "POST", "/scheduler/jobs/nope/delete", headers=HX, running_scheduler=True),
    M("scheduler_pause", "POST", "/scheduler/pause"),
    M("scheduler_pause__check_all_unregistered", "POST", "/scheduler/pause", setup=_unregister_check_all),
    M("scheduler_resume", "POST", "/scheduler/resume"),
    M("bulk_stop_all_web", "POST", "/bulk/stop-all", headers=HX),
    M("bulk_stop_all_web__device_fails", "POST", "/bulk/stop-all", headers=HX, setup=_device_stop_fails),
    # auth (real auth enforcement; login/logout sit outside the auth wall)
    M(
        "login_submit",
        "POST",
        "/login",
        {"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD, "next": "/alerts?status=open"},
        app_kwargs=REAL_AUTH,
    ),
    M(
        "login_submit__offsite_next",
        "POST",
        "/login",
        {"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD, "next": "//evil.example/x"},
        app_kwargs=REAL_AUTH,
    ),
    M(
        "login_submit__bad_password",
        "POST",
        "/login",
        {"username": TEST_ADMIN_USERNAME, "password": "wrong", "next": "/vacation"},
        app_kwargs=REAL_AUTH,
    ),
    M("login_submit__missing_field", "POST", "/login", {"username": TEST_ADMIN_USERNAME}, app_kwargs=REAL_AUTH),
    M(
        "login_submit__auth_disabled",
        "POST",
        "/login",
        {"username": "anyone", "password": "x", "next": "/quality"},
        app_kwargs={"bypass_auth": False, "auth_enabled": False},
    ),
    M("logout_submit", "POST", "/logout", app_kwargs=REAL_AUTH),
    # clusters
    M("create_cluster", "POST", "/clusters", {"name": "Herb Box", "location": "Kitchen", "environment": "outdoor"}),
    M("create_cluster__defaults", "POST", "/clusters", {"name": "Bare"}),
    M("create_cluster__missing_name", "POST", "/clusters", {"location": "Kitchen"}),
    M(
        "update_cluster",
        "POST",
        f"/clusters/{INDOOR}/edit",
        {"name": "Jungle", "location": "", "environment": "indoor"},
    ),
    M("update_cluster__404", "POST", "/clusters/999/edit", {"name": "Ghost"}),
    M("delete_cluster", "DELETE", f"/clusters/{EMPTY}", headers=HX),
    M("delete_cluster__populated", "DELETE", f"/clusters/{OUTDOOR}", headers=HX),
    M("delete_cluster__404", "DELETE", "/clusters/999", headers=HX),
    # configs
    M(
        "save_config",
        "POST",
        f"/clusters/{INDOOR}/config",
        {
            "mode": "fixed",
            "duration_minutes": "4",
            "interval_hours": "24",
            "auto_run": "false",
            "quiet_start_hour": "22",
            "quiet_end_hour": "6",
        },
    ),
    M("save_config__all_blank_inherits", "POST", f"/clusters/{OUTDOOR}/config", {}),
    M("save_config__bad_tri_bool", "POST", f"/clusters/{INDOOR}/config", {"auto_run": "maybe"}),
    M("save_config__bad_hour", "POST", f"/clusters/{INDOOR}/config", {"quiet_start_hour": "noon"}),
    M("save_config__non_numeric_duration", "POST", f"/clusters/{INDOOR}/config", {"duration_minutes": "abc"}),
    M("save_config__404", "POST", "/clusters/999/config", {"mode": "smart"}),
    M(
        "save_global_config",
        "POST",
        "/config/global",
        {
            "mode": "smart",
            "duration_minutes": "2",
            "interval_hours": "8",
            "auto_run": "on",
            "daily_cap_minutes": "20",
            "max_events_per_day": "",
            "quiet_start_hour": "23",
            "quiet_end_hour": "7",
        },
    ),
    M("save_global_config__negative_cap", "POST", "/config/global", {"daily_cap_minutes": "-1"}),
    M("save_global_config__non_numeric_cap", "POST", "/config/global", {"max_events_per_day": "lots"}),
    M("save_global_config__bad_hour", "POST", "/config/global", {"quiet_end_hour": "x"}),
    # irrigators
    M(
        "create_irrigator",
        "POST",
        f"/clusters/{EMPTY}/irrigators",
        {
            "tuya_device_id": "fake_tuya_device_00000003",
            "name": "Shelf Pump",
            "type": "rainpoint.ik10pw",
            "device_ip": " 192.0.2.7 ",
            "local_key": "fakelocalkeyyyyy",
            "reservoir_l": "5",
            "flow_rate_l_per_min": "0.25",
        },
    ),
    M(
        "create_irrigator__already_has_one",
        "POST",
        f"/clusters/{INDOOR}/irrigators",
        {"tuya_device_id": "fake_tuya_device_00000009", "name": "Second", "type": "rainpoint.ik10pw"},
    ),
    M(
        "create_irrigator__duplicate_device_id",
        "POST",
        f"/clusters/{EMPTY}/irrigators",
        {
            "tuya_device_id": "fake_tuya_device_aabbccdd",
            "name": "Clone",
            "type": "rainpoint.ik10pw",
            "reservoir_l": "5",
        },
    ),
    M(
        "create_irrigator__bad_capacity",
        "POST",
        f"/clusters/{EMPTY}/irrigators",
        {"tuya_device_id": "fake_tuya_device_00000003", "name": "P", "type": "rainpoint.ik10pw", "reservoir_l": "lots"},
    ),
    M(
        "create_irrigator__negative_capacity",
        "POST",
        f"/clusters/{EMPTY}/irrigators",
        {
            "tuya_device_id": "fake_tuya_device_00000003",
            "name": "P",
            "type": "rainpoint.ik10pw",
            "flow_rate_l_per_min": "-1",
        },
    ),
    M(
        "create_irrigator__404",
        "POST",
        "/clusters/999/irrigators",
        {"tuya_device_id": "fake_tuya_device_00000003", "name": "P", "type": "rainpoint.ik10pw"},
    ),
    M(
        "update_irrigator",
        "POST",
        f"/clusters/{INDOOR}/irrigators/edit",
        {
            "name": "Jungle Pump v2",
            "type": "rainpoint.ik10pw",
            "device_ip": "192.0.2.9",
            "local_key": "",
            "reservoir_l": "",
        },
    ),
    M("update_irrigator__404", "POST", f"/clusters/{EMPTY}/irrigators/edit", {"name": "X", "type": "rainpoint.ik10pw"}),
    M("delete_irrigator", "DELETE", f"/clusters/{OUTDOOR}/irrigators", headers=HX),
    M("delete_irrigator__404", "DELETE", f"/clusters/{EMPTY}/irrigators", headers=HX),
    M("start_irrigator", "POST", f"/irrigators/{IRR_INDOOR}/start", {"minutes": "2"}, headers=HX),
    M("start_irrigator__no_minutes", "POST", f"/irrigators/{IRR_OUTDOOR}/start", {}, headers=HX),
    M("start_irrigator__bad_minutes", "POST", f"/irrigators/{IRR_INDOOR}/start", {"minutes": "two"}, headers=HX),
    M(
        "start_irrigator__cap_reached",
        "POST",
        f"/irrigators/{IRR_INDOOR}/start",
        {"minutes": "2"},
        headers=HX,
        setup=_cap_indoor_to_one_event,
    ),
    M(
        "start_irrigator__device_fails",
        "POST",
        f"/irrigators/{IRR_INDOOR}/start",
        {"minutes": "2"},
        headers=HX,
        setup=_device_start_fails,
    ),
    M(
        "start_irrigator__no_registry",
        "POST",
        f"/irrigators/{IRR_INDOOR}/start",
        {"minutes": "2"},
        headers=HX,
        setup=_no_device_registry,
    ),
    M("start_irrigator__404", "POST", "/irrigators/999/start", {"minutes": "2"}, headers=HX),
    M("stop_irrigator", "POST", f"/irrigators/{IRR_INDOOR}/stop", headers=HX),
    M("stop_irrigator__device_fails", "POST", f"/irrigators/{IRR_INDOOR}/stop", headers=HX, setup=_device_stop_fails),
    M("stop_irrigator__404", "POST", "/irrigators/999/stop", headers=HX),
    M("log_manual_submit", "POST", f"/irrigators/{IRR_INDOOR}/log-manual", {"minutes": "3", "notes": "Watering can"}),
    M(
        "log_manual_submit__cap_reached",
        "POST",
        f"/irrigators/{IRR_INDOOR}/log-manual",
        {"minutes": "3"},
        setup=_cap_indoor_to_one_event,
    ),
    M("log_manual_submit__missing_minutes", "POST", f"/irrigators/{IRR_INDOOR}/log-manual", {"notes": "x"}),
    M("log_manual_submit__404", "POST", "/irrigators/999/log-manual", {"minutes": "3"}),
    # operations
    M("irrigate__dry_run", "POST", f"/clusters/{INDOOR}/irrigate", {"dry_run": "1"}, headers=HX),
    M("irrigate", "POST", f"/clusters/{INDOOR}/irrigate", {}, headers=HX),
    M(
        "irrigate__outdoor_temp_override",
        "POST",
        f"/clusters/{OUTDOOR}/irrigate",
        {"temp_override": "31.5"},
        headers=HX,
    ),
    M("irrigate__quiet_hours", "POST", f"/clusters/{INDOOR}/irrigate", {}, headers=HX, setup=_quiet_hours_now),
    M(
        "irrigate__quiet_hours_forced",
        "POST",
        f"/clusters/{INDOOR}/irrigate",
        {"force": "true"},
        headers=HX,
        setup=_quiet_hours_now,
    ),
    M("irrigate__empty_cluster", "POST", f"/clusters/{EMPTY}/irrigate", {}, headers=HX),
    M("irrigate__missing_cluster", "POST", "/clusters/999/irrigate", {}, headers=HX),
    M("check_single", "POST", f"/clusters/{INDOOR}/check", headers=HX),
    M("check_single__404", "POST", "/clusters/999/check", headers=HX),
    M("check_all", "POST", "/check", headers=HX),
    M("sync_all", "POST", "/sync", {"hours": "6"}, headers=HX),
    M("sync_all__bad_hours", "POST", "/sync", {"hours": "six"}, headers=HX),
    M("sync_plants", "POST", "/plants/sync", {}, headers=HX),
    M("sync_plants__one_plant", "POST", "/plants/sync", {"plant_id": str(ALOCASIA)}, headers=HX),
    M("sync_plants__one_cluster", "POST", "/plants/sync", {"cluster_id": str(OUTDOOR)}, headers=HX),
    M("sync_plants__missing_plant", "POST", "/plants/sync", {"plant_id": "999"}, headers=HX),
    # plant dashboard
    M("move_plant_web", "POST", f"/clusters/{INDOOR}/plants/{MONSTERA}/move", {"target_cluster_id": str(OUTDOOR)}),
    M(
        "move_plant_web__same_cluster",
        "POST",
        f"/clusters/{INDOOR}/plants/{MONSTERA}/move",
        {"target_cluster_id": str(INDOOR)},
    ),
    M(
        "move_plant_web__missing_target",
        "POST",
        f"/clusters/{INDOOR}/plants/{MONSTERA}/move",
        {"target_cluster_id": "999"},
    ),
    M(
        "move_plant_web__wrong_cluster",
        "POST",
        f"/clusters/{OUTDOOR}/plants/{MONSTERA}/move",
        {"target_cluster_id": "3"},
    ),
    # plants
    M("create_plant", "POST", f"/clusters/{EMPTY}/plants", PLANT_FORM),
    M("create_plant__species_only", "POST", f"/clusters/{EMPTY}/plants", {"species": "Unknown fern"}),
    M("create_plant__bad_float", "POST", f"/clusters/{EMPTY}/plants", {"species": "X", "ideal_temp_min": "warm"}),
    M("create_plant__404", "POST", "/clusters/999/plants", {"species": "X"}),
    M(
        "update_plant",
        "POST",
        f"/clusters/{INDOOR}/plants/{MONSTERA}/edit",
        {**PLANT_FORM, "species": "Monstera deliciosa"},
    ),
    M("update_plant__404", "POST", f"/clusters/{OUTDOOR}/plants/{MONSTERA}/edit", {"species": "X"}),
    M("delete_plant", "DELETE", f"/clusters/{OUTDOOR}/plants/{LOQUAT}", headers=HX),
    M("delete_plant__404", "DELETE", f"/clusters/{OUTDOOR}/plants/{MONSTERA}", headers=HX),
    # preferences
    M("update_preferences", "POST", "/preferences", PREFS_FORM),
    M("update_preferences__bad_default_cluster", "POST", "/preferences", {**PREFS_FORM, "default_cluster_id": "x"}),
    M("update_preferences__missing_field", "POST", "/preferences", {"units": "metric"}),
    M("update_theme", "POST", "/preferences/theme", {"theme": "light"}),
    M("update_theme__invalid", "POST", "/preferences/theme", {"theme": "neon"}),
    # sensors
    M(
        "create_sensor",
        "POST",
        f"/clusters/{INDOOR}/sensors",
        {
            "tuya_device_id": "fake_tuya_sensor_00000005",
            "name": "Spare Probe",
            "type": "tuya.tr301z",
            "plant_id": "1",
        },
    ),
    M(
        "create_sensor__plant_in_other_cluster",
        "POST",
        f"/clusters/{INDOOR}/sensors",
        {"tuya_device_id": "fake_tuya_sensor_00000005", "name": "P", "type": "tuya.tr301z", "plant_id": str(LOQUAT)},
    ),
    M(
        "create_sensor__duplicate_device_id",
        "POST",
        f"/clusters/{INDOOR}/sensors",
        {"tuya_device_id": "fake_tuya_sensor_00000002", "name": "Clone", "type": "tuya.tr301z"},
    ),
    M(
        "create_sensor__bad_plant_id",
        "POST",
        f"/clusters/{INDOOR}/sensors",
        {"tuya_device_id": "fake_tuya_sensor_00000005", "name": "P", "type": "tuya.tr301z", "plant_id": "one"},
    ),
    M(
        "create_sensor__404",
        "POST",
        "/clusters/999/sensors",
        {"tuya_device_id": "fake_tuya_sensor_00000005", "name": "P", "type": "tuya.tr301z"},
    ),
    M(
        "update_sensor",
        "POST",
        f"/clusters/{INDOOR}/sensors/{S_AMBIENT}/edit",
        {"name": "Room Climate+", "type": "tuya.tr301z", "plant_id": str(MONSTERA)},
    ),
    M(
        "update_sensor__unassign",
        "POST",
        f"/clusters/{INDOOR}/sensors/{S_MONSTERA}/edit",
        {"name": "Monstera Probe", "type": "tuya.tr301z", "plant_id": ""},
    ),
    M(
        "update_sensor__plant_in_other_cluster",
        "POST",
        f"/clusters/{INDOOR}/sensors/{S_AMBIENT}/edit",
        {"name": "R", "type": "tuya.tr301z", "plant_id": str(LOQUAT)},
    ),
    M(
        "update_sensor__bad_plant_id",
        "POST",
        f"/clusters/{INDOOR}/sensors/{S_AMBIENT}/edit",
        {"name": "R", "type": "tuya.tr301z", "plant_id": "x"},
    ),
    M("update_sensor__404", "POST", f"/clusters/{OUTDOOR}/sensors/{S_MONSTERA}/edit", {"name": "R", "type": "x"}),
    M("delete_sensor", "DELETE", f"/clusters/{INDOOR}/sensors/{S_AMBIENT}", headers=HX),
    M("delete_sensor__404", "DELETE", f"/clusters/{OUTDOOR}/sensors/{S_AMBIENT}", headers=HX),
    # vacation
    M(
        "create_vacation",
        "POST",
        "/vacation",
        {"starts_at": "2026-05-01", "ends_at": str(T + 30 * 86400), "contact_email": " a@example.com ", "notes": " "},
    ),
    M("create_vacation__ends_before_start", "POST", "/vacation", {"starts_at": "2026-05-10", "ends_at": "2026-05-01"}),
    M("create_vacation__bad_date", "POST", "/vacation", {"starts_at": "next week", "ends_at": "2026-05-01"}),
    M(
        "update_vacation",
        "POST",
        f"/vacation/{VACATION_UPCOMING}/edit",
        {"starts_at": "2026-04-20", "ends_at": "2026-04-25", "contact_email": "", "notes": "Shorter"},
    ),
    M("update_vacation__404", "POST", "/vacation/999/edit", {"starts_at": "2026-04-20", "ends_at": "2026-04-25"}),
    M(
        "update_vacation__ends_before_start",
        "POST",
        f"/vacation/{VACATION_PAST}/edit",
        {"starts_at": "2026-04-25", "ends_at": "2026-04-20"},
    ),
    M("delete_vacation", "POST", f"/vacation/{VACATION_PAST}/delete"),
    M("delete_vacation__404", "POST", "/vacation/999/delete"),
    # windows
    M(
        "create_window",
        "POST",
        f"/clusters/{INDOOR}/windows",
        {"start_hour": "18", "end_hour": "21", "weekday_mask": ["32", "64"], "label": " Weekend evenings "},
    ),
    M(
        "create_window__same_hours",
        "POST",
        f"/clusters/{INDOOR}/windows",
        {"start_hour": "6", "end_hour": "6", "weekday_mask": ["1"]},
    ),
    M(
        "create_window__hour_out_of_range",
        "POST",
        f"/clusters/{INDOOR}/windows",
        {"start_hour": "6", "end_hour": "24", "weekday_mask": ["1"]},
    ),
    M("create_window__no_weekday", "POST", f"/clusters/{INDOOR}/windows", {"start_hour": "6", "end_hour": "9"}),
    M(
        "create_window__bad_bit",
        "POST",
        f"/clusters/{INDOOR}/windows",
        {"start_hour": "6", "end_hour": "9", "weekday_mask": ["3"]},
    ),
    M(
        "create_window__non_int_bit",
        "POST",
        f"/clusters/{INDOOR}/windows",
        {"start_hour": "6", "end_hour": "9", "weekday_mask": ["mon"]},
    ),
    M(
        "create_window__404",
        "POST",
        "/clusters/999/windows",
        {"start_hour": "6", "end_hour": "9", "weekday_mask": ["1"]},
    ),
    M(
        "update_window",
        "POST",
        f"/clusters/{OUTDOOR}/windows/{WINDOW}/edit",
        {"start_hour": "5", "end_hour": "8", "weekday_mask": ["1", "2", "4", "8", "16", "32", "64"], "label": ""},
    ),
    M(
        "update_window__404",
        "POST",
        f"/clusters/{INDOOR}/windows/{WINDOW}/edit",
        {"start_hour": "5", "end_hour": "8", "weekday_mask": ["1"]},
    ),
    M(
        "update_window__same_hours",
        "POST",
        f"/clusters/{OUTDOOR}/windows/{WINDOW}/edit",
        {"start_hour": "5", "end_hour": "5", "weekday_mask": ["1"]},
    ),
    M("delete_window", "DELETE", f"/clusters/{OUTDOOR}/windows/{WINDOW}", headers=HX),
    M("delete_window__404", "DELETE", f"/clusters/{INDOOR}/windows/{WINDOW}", headers=HX),
)


# ── DB diff helpers ───────────────────────────────────────────────────────────────────────────


def db_snapshot(engine) -> dict[str, dict[str, dict]]:
    """``{table: {primary key (as text): row dict}}`` for every table."""
    snap: dict[str, dict[str, dict]] = {}
    insp = inspect(engine)
    with engine.connect() as conn:
        for table in sorted(insp.get_table_names()):
            pk_cols = insp.get_pk_constraint(table)["constrained_columns"] or []
            rows = conn.execute(text(f'SELECT * FROM "{table}"')).mappings().all()  # noqa: S608 — inspector names
            keyed = {}
            for row in rows:
                key_cols = pk_cols or list(row.keys())
                keyed["/".join(str(row[c]) for c in key_cols)] = dict(row)
            snap[table] = keyed
    return snap


def db_effect(before: dict, after: dict) -> dict[str, dict]:
    """Rows added (full), removed (keys only) and changed (``col: [before, after]``) per table."""
    effect: dict[str, dict] = {}
    for table in sorted(set(before) | set(after)):
        old, new = before.get(table, {}), after.get(table, {})
        added = [new[k] for k in sorted(set(new) - set(old), key=_natural)]
        removed = sorted(set(old) - set(new), key=_natural)
        changed = {}
        for key in sorted(set(old) & set(new), key=_natural):
            delta = {c: [old[key][c], new[key][c]] for c in old[key] if old[key][c] != new[key].get(c)}
            if delta:
                changed[key] = delta
        entry = {name: value for name, value in (("added", added), ("removed", removed), ("changed", changed)) if value}
        if entry:
            effect[table] = entry
    return effect


def _natural(key: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in key.split("/"))


# ── The test ──────────────────────────────────────────────────────────────────────────────────


def test_mutation_cases_are_unique_and_cover_every_web_write_route():
    """Every non-GET web route has at least one case; case names are unique."""
    import re

    from greenhouse_server.web.router import web_router
    from server.test_contract_web_html import web_routes

    names = [m.name for m in MUTATIONS]
    assert len(names) == len(set(names))
    for route in web_routes(web_router.routes):
        for method in route.methods - {"GET", "HEAD"}:
            pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", route.path) + "$"
            assert any(m.method == method and re.match(pattern, m.url) for m in MUTATIONS), (method, route.path)


@pytest.mark.parametrize("case", MUTATIONS, ids=lambda m: m.name)
def test_web_mutation_golden(case, make_client, monkeypatch):
    """Status, headers, body, templates, DB effect and fake-device calls of one write on a fresh app."""
    app, client = make_client(**case.app_kwargs)
    spy = _web.TemplateSpy()
    spy.install(monkeypatch)
    engine = app.state.session_factory.kw["bind"]
    if case.setup is not None:
        session = app.state.session_factory()
        try:
            case.setup(app, IrrigationRepository(session))
            session.commit()
        finally:
            session.close()

    from greenhouse_server.scheduler import scheduler, start_scheduler, stop_scheduler

    if case.running_scheduler:
        start_scheduler(paused=True)
        scheduler.add_job(_nothing, "interval", hours=1, id=ADHOC_JOB, name="Ad-hoc contract job")
    try:
        before = db_snapshot(engine)
        resp = client.request(case.method, case.url, data=case.form, headers=case.headers, follow_redirects=False)
        after = db_snapshot(engine)
        jobs_after = sorted(job.id for job in scheduler.get_jobs())
    finally:
        if case.running_scheduler:
            stop_scheduler()

    wiring = app.state.fake_devices
    record = {
        "request": {"method": case.method, "url": case.url, "form": case.form, "headers": case.headers},
        "status": resp.status_code,
        "headers": pinned_headers(resp),
        "body_lines": normalize(resp.text).split("\n"),
        "templates": spy.calls,  # for write routes: [[template name, sorted context keys], …]
        "db_effect": db_effect(before, after),
        "device_calls": {"irrigator": wiring.irrigator.calls, "sensor": wiring.sensor.calls},
    }
    if case.running_scheduler or "scheduler" in case.url:
        record["scheduler_jobs_after"] = jobs_after
    assert_golden_json(f"web/mutations/{case.name}.json", record)


# ── Observed bugs (pinned, not fixed) ─────────────────────────────────────────────────────────


def _run(make_client, method: str, url: str, form: dict | None = None, setup: Callable | None = None):
    app, client = make_client()
    if setup is not None:
        session = app.state.session_factory()
        try:
            setup(app, IrrigationRepository(session))
            session.commit()
        finally:
            session.close()
    engine = app.state.session_factory.kw["bind"]
    before = db_snapshot(engine)
    resp = client.request(method, url, data=form, headers=HX, follow_redirects=False)
    return app, resp, db_effect(before, db_snapshot(engine))


def test_bulk_stop_all_current_behavior_reports_success_when_device_stop_fails(make_client):
    """Pins current (buggy) behavior: emergency stop ignores ``adapter.stop()``'s ``(False, msg)`` — see REFACTOR_NOTES.md.

    ``services/bulk.py::stop_all_irrigators`` only counts *exceptions* as errors, so a device that
    reports a failed stop is still counted as stopped, gets a ``stop``/``emergency`` event, and
    the web fragment says "Every device is now off."
    """
    app, resp, effect = _run(make_client, "POST", "/bulk/stop-all", setup=_device_stop_fails)
    assert resp.status_code == 200
    assert "Stopped 2 irrigators." in resp.text
    assert "Every device is now off." in resp.text
    assert app.state.fake_devices.irrigator.calls == [("stop", IRR_INDOOR), ("stop", IRR_OUTDOOR)]
    assert [row["triggered_by"] for row in effect["irrigation_events"]["added"]] == ["emergency", "emergency"]


@pytest.mark.parametrize("action", ["ack", "resolve"])
def test_alert_action_on_missing_alert_current_behavior_returns_200_success_toast(action, make_client):
    """Pins current (buggy) behavior: ack/resolve of a missing alert is a 200 with a success toast — see REFACTOR_NOTES.md.

    ``web/routes/alerts.py`` never checks the repository's ``None`` (the JSON API returns 404):
    the row partial renders empty and ``HX-Toast`` still announces success.
    """
    _, resp, effect = _run(make_client, "POST", f"/alerts/999/{action}")
    assert resp.status_code == 200
    assert resp.text.strip() == ""
    assert json.loads(resp.headers["HX-Toast"])["severity"] == "success"
    assert effect == {}


@pytest.mark.parametrize(
    ("url", "form"),
    [
        (f"/clusters/{INDOOR}/config", {"duration_minutes": "abc"}),
        (f"/clusters/{INDOOR}/config", {"interval_hours": "1.5"}),
        ("/config/global", {"duration_minutes": "abc"}),
        (f"/clusters/{EMPTY}/plants", {"species": "X", "ideal_temp_min": "warm"}),
        (f"/clusters/{INDOOR}/plants/{MONSTERA}/edit", {"species": "X", "ideal_humidity_max": "wet"}),
        (f"/clusters/{INDOOR}/irrigate", {"temp_override": "hot"}),
        ("/plants/sync", {"plant_id": "one"}),
    ],
)
def test_non_numeric_form_value_current_behavior_is_unhandled_500(url, form, make_client):
    """Pins current (buggy) behavior: bare ``int()``/``float()`` on form text escapes as a plain 500 — see REFACTOR_NOTES.md.

    Sibling fields go through helpers that raise ``HTTPException(400)``; these do not, so the
    ``ValueError`` bypasses the HTML error page and nothing is written.
    """
    _, resp, effect = _run(make_client, "POST", url, form)
    assert resp.status_code == 500
    assert resp.text == "Internal Server Error"
    assert resp.headers["content-type"].startswith("text/plain")
    assert effect == {}


def test_irrigate_missing_cluster_current_behavior_500_from_template(make_client):
    """Pins current (buggy) behavior: irrigating an unknown cluster crashes the decision panel — see REFACTOR_NOTES.md.

    ``run_irrigation_pipeline`` returns ``{"action": "error", "reason": "cluster not found", …}``;
    ``partials/_decision_panel.html`` formats ``result.temperature`` (undefined → ``is not none``
    is true) and raises ``UndefinedError`` → plain 500 instead of a 404 page.
    """
    app, resp, effect = _run(make_client, "POST", "/clusters/999/irrigate", {})
    assert resp.status_code == 500
    assert resp.text == "Internal Server Error"
    assert effect == {}
    assert app.state.fake_devices.irrigator.calls == []


def test_delete_cluster_current_behavior_leaves_orphan_rows(make_client):
    """Pins current behavior: deleting a populated cluster leaves rows that still point at it — see REFACTOR_NOTES.md.

    ``repository.delete_cluster`` removes the cluster's plants, sensors (+ readings), irrigator
    (+ events) and config, but not its irrigation windows, decision logs, alerts or sensor
    assignments (SQLite FK enforcement is off, so nothing complains).
    """
    app, resp, effect = _run(make_client, "DELETE", f"/clusters/{OUTDOOR}")
    assert resp.status_code == 200
    assert set(effect) == {
        "clusters",
        "irrigation_configs",
        "irrigation_events",
        "irrigators",
        "plants",
        "sensor_readings",
        "sensors",
    }
    engine = app.state.session_factory.kw["bind"]
    with engine.connect() as conn:
        orphans = {
            table: conn.execute(text(sql), {"c": OUTDOOR}).scalar_one()
            for table, sql in {
                "irrigation_windows": "SELECT COUNT(*) FROM irrigation_windows WHERE cluster_id = :c",
                "decision_logs": "SELECT COUNT(*) FROM decision_logs WHERE cluster_id = :c",
                "alerts": "SELECT COUNT(*) FROM alerts WHERE cluster_id = :c",
                "sensor_assignments": "SELECT COUNT(*) FROM sensor_assignments WHERE plant_id = 3",
            }.items()
        }
    assert orphans == {"irrigation_windows": 1, "decision_logs": 1, "alerts": 1, "sensor_assignments": 1}

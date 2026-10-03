"""Gate 1 mutation-campaign gap tests (server) — characterization, current behavior.

Each test kills one mutant that survived the Phase-1 safety net (ids in the
docstrings, details in ``refactor/gate1/mutation.md``). They pin what the code
does TODAY; nothing here asserts what it *should* do. Hermetic: in-memory
SQLite (``tmp_db``), frozen clock (``frozen_clock`` at ``golden.FROZEN_INSTANT``),
no network, fake device adapters written in this module.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from engine_grid import plant_database
from golden import FROZEN_TS, install_offline_weather
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import DeviceHealthState
from greenhouse_core.models import Alert
from greenhouse_core.repository import IrrigationRepository
from greenhouse_core.utils import set_display_timezone
from greenhouse_server import scheduler as scheduler_module
from greenhouse_server.services import manual_control as manual_control_module
from greenhouse_server.services import pump_watcher as pump_watcher_module
from greenhouse_server.services.health_monitor import DeviceHealthMonitor
from greenhouse_server.services.irrigation import IrrigationService, schedule_pump_watcher
from greenhouse_server.services.leak import LeakDetectionService
from greenhouse_server.services.manual_control import ManualActionError, check_rate_limits, manual_start
from greenhouse_server.services.pump_watcher import PumpWatcherService
from greenhouse_server.services.sync import SyncService
from greenhouse_server.web.context import is_hx
from greenhouse_server.web.filters import age_seconds, format_minutes, moisture_badge, severity_class, strip_emoji

from .conftest import _make_stubbed_app

MCP_TOKEN = "gap-mcp-token-0123456789abcdef0123"


@pytest.fixture
def db(tmp_db, frozen_clock, clean_env):
    return tmp_db


def _cluster_with_sensors(db, n: int, *, irrigator: bool = False):
    cid = db.add_cluster("Gap Cluster")
    sensors = [
        db.add_sensor(
            cluster_id=cid,
            tuya_device_id=f"fake_tuya_sensor_gap{i:04d}",
            name=f"Gap Probe {i}",
            sensor_type="soil_moisture",
            config={},
        )
        for i in range(n)
    ]
    irr = None
    if irrigator:
        irr = db.add_irrigator(
            cluster_id=cid,
            tuya_device_id="fake_tuya_device_gap0001",
            name="Gap Pump",
            irrigator_type="tuya_cloud",
            config={},
        )
    db.session.commit()
    return cid, sensors, irr


# ── services/sync.py ────────────────────────────────────────────────────────


def _snapshot_case(db):
    cid, (s1, s2), _ = _cluster_with_sensors(db, 2)
    db.add_sensor_reading(
        sensor_id=s1, timestamp=FROZEN_TS - 600, soil_moisture=30.0, temperature=20.0, light=100, env_humidity=40.0
    )
    db.add_sensor_reading(
        sensor_id=s2, timestamp=FROZEN_TS - 600, soil_moisture=50.0, temperature=24.0, light=900, env_humidity=60.0
    )
    return cid


def test_cluster_snapshot_soil_is_min_light_is_max(db):
    """svcsync-04: the pipeline snapshot takes the BRIGHTEST sensor's light and the DRIEST soil."""
    cid = _snapshot_case(db)
    snap = SyncService(db, None, None).ensure_fresh_and_read(cid)
    assert snap == {"temperature": 22.0, "soil_moisture": 30.0, "env_humidity": 50.0, "light": 900}


# ── services/irrigation.py ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("soil", "status"),
    [(29.5, "very_dry"), (30.0, "dry"), (33.0, "dry"), (44.9, "dry"), (60.0, "ok"), (70.0, "ok"), (70.5, "wet")],
)
def test_monitor_status_bands_around_the_plant_target(db, soil, status):
    """irrigation-15 / irrigation-16: monitor bands for a 45-60 % plant.

    ``very_dry`` < target_min − 15, ``dry`` < target_min, ``wet`` > target_max + 10, else ``ok``.
    """
    cid = db.add_cluster("Monitor Gap")
    pid = db.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
    sid = db.add_sensor(
        cluster_id=cid,
        tuya_device_id="fake_tuya_sensor_mon0001",
        name="Monitor Probe",
        sensor_type="soil_moisture",
        config={},
        plant_id=pid,
    )
    db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=soil)
    svc = IrrigationService(db, None, SyncService(db, None, None), None, plant_database())
    (row,) = svc.monitor_cluster(cid)["sensors"]
    assert (row["target_min"], row["target_max"], row["status"]) == (45.0, 60.0, status)


class _FakeScheduler:
    running = True

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})


def test_scheduled_pump_watcher_watches_for_the_duration_in_seconds(db, monkeypatch):
    """irrigation-27: the watcher job watches ``duration_minutes * 60`` seconds."""
    _, _, irr_id = _cluster_with_sensors(db, 0, irrigator=True)
    watched: list[tuple] = []

    class RecordingWatcher:
        def __init__(self, repo, registry, **kwargs):
            pass

        def watch(self, irrigator, duration_seconds, *, started_at=None):
            watched.append((irrigator.id, duration_seconds, started_at))
            return {"outcome": "completed"}

    fake = _FakeScheduler()
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=None,
            device_registry=DeviceRegistry(),
            session_factory=lambda: db.session,
            health_monitor=None,
        )
    )
    monkeypatch.setattr(scheduler_module, "scheduler", fake)
    monkeypatch.setattr(scheduler_module, "_app", app)
    monkeypatch.setattr(pump_watcher_module, "PumpWatcherService", RecordingWatcher)
    assert schedule_pump_watcher(irr_id, 3, FROZEN_TS) is True
    (job,) = fake.jobs
    job["func"]()
    assert watched == [(irr_id, 180, FROZEN_TS)]


def test_monitor_reads_the_cleaned_view(db):
    """irrigation-33: monitor uses ``clean_readings_desc`` — an out-of-range latest value (150 %) is
    dropped by the range gate and the previous sample (50 %) is reported."""
    cid, (sid,), _ = _cluster_with_sensors(db, 1)
    db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 1800, soil_moisture=50.0)
    db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=150.0)
    svc = IrrigationService(db, None, SyncService(db, None, None), None, plant_database())
    (row,) = svc.monitor_cluster(cid)["sensors"]
    assert (row["soil_moisture"], row["status"]) == (50.0, "ok")


class _Weather:
    """Offline-style weather stub with a fixed current reading and no forecast."""

    def get_current(self):
        return {"feels_like": 25.0}

    def get_forecast(self, hours: int = 6):
        return None


@pytest.mark.parametrize(
    ("environment", "temp", "source"),
    [("indoor", 15.0, "sensor"), ("greenhouse", 25.0, "open-meteo"), ("outdoor", 25.0, "open-meteo")],
)
def test_temperature_source_only_indoor_prefers_the_sensor(db, environment, temp, source):
    """irrigation-34: only ``environment == "indoor"`` reads the sensor first; any other value
    (e.g. "greenhouse") is treated like outdoor and prefers the weather feels-like."""
    cid = db.add_cluster("Temp Gap", environment=environment)
    db.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
    sid = db.add_sensor(
        cluster_id=cid,
        tuya_device_id="fake_tuya_sensor_tmp0001",
        name="Temp Probe",
        sensor_type="soil_moisture",
        config={},
    )
    db.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=50.0, temperature=15.0)
    svc = IrrigationService(db, None, SyncService(db, None, None), _Weather(), plant_database())
    result = svc.run_irrigation_pipeline(cid, dry_run=True)
    assert (result["temperature"], result["temperature_source"]) == (temp, source)


@pytest.fixture
def api(clean_env, frozen_clock):
    app, engine = _make_stubbed_app(bypass_auth=True)
    install_offline_weather(app)
    yield TestClient(app, raise_server_exceptions=False), app
    engine.dispose()


def test_check_all_has_alerts_counts_thirsty_plants(api):
    """web-13: ``POST /check`` sets ``has_alerts`` when a monitored cluster has plants needing water."""
    client, app = api
    session = app.state.session_factory()
    repo = IrrigationRepository(session)
    cid = repo.add_cluster("Thirsty")
    pid = repo.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
    sid = repo.add_sensor(
        cluster_id=cid,
        tuya_device_id="fake_tuya_sensor_thr0001",
        name="Thirsty Probe",
        sensor_type="soil_moisture",
        config={},
        plant_id=pid,
    )
    repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=20.0)
    session.commit()
    session.close()
    body = client.post("/api/v1/check").json()
    (result,) = body["results"]
    assert (result["action"], result["alerts"], result["needs_water"]) == (
        "monitored",
        [],
        ["Thirsty Probe (Monstera deliciosa): 20%"],
    )
    assert body["has_alerts"] is True


@pytest.mark.parametrize(
    ("patch", "status"),
    [({"ends_at": FROZEN_TS + 3600}, 400), ({"ends_at": 0}, 400), ({"ends_at": FROZEN_TS + 3601}, 200)],
)
def test_vacation_update_requires_start_strictly_before_end(api, patch, status):
    """web-15 / web-16: the effective window must satisfy ``starts_at < ends_at``; an explicit
    ``ends_at = 0`` is applied (and rejected), not treated as absent."""
    client, _ = api
    created = client.post("/api/v1/vacation", json={"starts_at": FROZEN_TS + 3600, "ends_at": FROZEN_TS + 7200}).json()
    resp = client.put(f"/api/v1/vacation/{created['id']}", json=patch)
    assert resp.status_code == status


def test_deleting_a_core_scheduler_job_is_409(api):
    """web-17: ``DELETE /scheduler/jobs/{core}`` answers 409 with the pause hint."""
    client, _ = api
    resp = client.delete("/api/v1/scheduler/jobs/check_all")
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith("Job check_all is a built-in scheduler job and cannot be deleted.")


@pytest.mark.parametrize(
    ("force", "triggered_by", "code"),
    [
        ("", "auto", "quiet_hours"),
        ("true", "manual", "sensor_adequate"),
        ("on", "manual", "sensor_adequate"),
        ("1", "manual", "sensor_adequate"),
        ("yes", "auto", "quiet_hours"),
    ],
)
def test_web_irrigate_force_flag_spellings(api, force, triggered_by, code):
    """web-19: the web irrigate form treats ``true`` / ``on`` / ``1`` (any case) as force; anything else is not."""
    client, app = api
    session = app.state.session_factory()
    repo = IrrigationRepository(session)
    cid = repo.add_cluster("Force Gap")
    repo.add_plant(cluster_id=cid, species="Monstera deliciosa", category="tropical")
    sid = repo.add_sensor(
        cluster_id=cid,
        tuya_device_id="fake_tuya_sensor_frc0001",
        name="Force Probe",
        sensor_type="soil_moisture",
        config={},
    )
    repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=50.0)
    repo.set_irrigation_config(cid, quiet_start_hour=9, quiet_end_hour=11)  # 10:00 UTC is quiet
    session.commit()
    session.close()
    resp = client.post(f"/clusters/{cid}/irrigate", data={"dry_run": "1", "no_sync": "1", "force": force})
    assert resp.status_code == 200
    session = app.state.session_factory()
    (log,) = IrrigationRepository(session).list_decision_logs(cid)
    assert (log.triggered_by, log.primary_code) == (triggered_by, code)
    session.close()


@pytest.mark.parametrize(
    ("alerts", "badge"), [([{"severity": "warning", "type": "x", "message": "m"}], True), ([], False)]
)
def test_web_check_all_badge_follows_cluster_alerts(api, monkeypatch, alerts, badge):
    """web-20: the web ``POST /check`` partial shows the "alerts" badge iff some result carries alerts."""
    client, _ = api
    result = {"cluster_id": 1, "cluster_name": "Badge", "action": "monitored", "alerts": alerts, "maintenance": []}
    monkeypatch.setattr(IrrigationService, "check_all_clusters", lambda self: [result])
    html = client.post("/check").text
    assert ('<span class="badge-warning">alerts</span>' in html) is badge


@pytest.mark.parametrize("verb", ["pause", "resume"])
def test_scheduler_toggle_unexpected_failure_is_500_naming_the_verb(api, monkeypatch, verb):
    """web-18: an unexpected scheduler error maps to 500 ``Failed to <pause|resume> job: …``."""
    client, _ = api

    def boom(repo, paused):
        raise RuntimeError("jobstore down")

    monkeypatch.setattr(scheduler_module, "set_check_all_paused", boom)
    resp = client.post(f"/api/v1/scheduler/{verb}")
    assert (resp.status_code, resp.json()) == (500, {"detail": f"Failed to {verb} job: jobstore down"})


# ── scheduler.py ────────────────────────────────────────────────────────────


def test_sync_job_backfills_six_hours_and_commits(db, monkeypatch):
    """scheduler-10: the background sync job asks for a 6 h log window and commits."""
    calls: list[int] = []
    monkeypatch.setattr(SyncService, "sync_all_sensors", lambda self, hours=24: calls.append(hours) or {})
    commits: list[int] = []
    session = SimpleNamespace(commit=lambda: commits.append(1), rollback=lambda: None, close=lambda: None)
    app = SimpleNamespace(
        state=SimpleNamespace(device_gateway=object(), device_registry=None, session_factory=lambda: session)
    )
    monkeypatch.setattr(scheduler_module, "_app", app)
    scheduler_module._sync_job()
    assert (calls, commits) == ([6], [1])


# ── auth (server) ───────────────────────────────────────────────────────────


@pytest.fixture
def mcp_client(clean_env, frozen_clock):
    app, engine = _make_stubbed_app(bypass_auth=False, mcp_token=MCP_TOKEN)
    install_offline_weather(app)
    yield TestClient(app, raise_server_exceptions=False)
    engine.dispose()


@pytest.mark.parametrize(
    ("token", "status"),
    [(MCP_TOKEN, 200), (MCP_TOKEN + "x", 401), (MCP_TOKEN[:-1], 401)],
)
def test_mcp_token_on_api_must_match_exactly(mcp_client, token, status):
    """auth-12: the MCP bearer is accepted on ``/api/v1`` only on an exact match (not a prefix)."""
    resp = mcp_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status


# ── services/leak.py ────────────────────────────────────────────────────────

LEAK_START = FROZEN_TS - 3600  # the check runs 30 min later; every sample is in the past


def _leak_check(db, before: list[float], after: list[float]):
    """Seed one sensor with ``before`` (−50 min…) and ``after`` (+5 min, every 5 min) and run the check."""
    cid, (sid,), _ = _cluster_with_sensors(db, 1, irrigator=True)
    for i, soil in enumerate(before):
        db.add_sensor_reading(sensor_id=sid, timestamp=LEAK_START - 600 * (len(before) - i), soil_moisture=soil)
    for i, soil in enumerate(after):
        db.add_sensor_reading(sensor_id=sid, timestamp=LEAK_START + 300 * (i + 1), soil_moisture=soil)
    alerts = LeakDetectionService(db, plant_database()).check_after_irrigation(cid, LEAK_START)
    return cid, sid, alerts


@pytest.mark.parametrize(
    ("case", "before", "after", "reason"),
    [
        ("two-after-samples-inconclusive", [40.0, 40.0], [96.0, 97.0], None),  # leak-01
        ("pinned-exactly-95-is-not-pinned", [40.0, 40.0], [95.0, 95.0, 95.0], None),  # leak-02
        ("pinned-needs-every-tail-sample", [40.0, 40.0], [70.0, 96.0, 80.0], None),  # leak-03
        ("pinned", [40.0, 40.0], [70.0, 96.0, 97.0], "pinned >95%"),
        ("one-before-sample-inconclusive", [40.0], [50.0, 60.0, 70.0], None),  # leak-04
        ("baseline-is-median", [20.0, 40.0, 41.0], [50.0, 60.0, 65.0], None),  # leak-05
        (
            "within-settle-tolerance-of-peak",
            [40.0, 40.0],
            [50.0, 72.0, 71.0],
            "still rising after irrigation",
        ),  # leak-06
        # four baseline samples so the joint Hampel window does not flag the baseline itself
        ("rise-within-window-too-small", [40.0] * 4, [70.0, 72.0, 71.5], None),  # leak-07 / leak-09
        ("rise-within-window-enough", [40.0] * 4, [70.0, 72.5, 72.1], "still rising after irrigation"),
        ("exactly-30-above-baseline", [40.0, 40.0], [50.0, 60.0, 70.0], "still rising after irrigation"),  # leak-08
        ("settled", [40.0, 40.0], [60.0, 55.0, 50.0], None),
    ],
)
def test_leak_verdict_table(db, case, before, after, reason):
    """leak-01…09: the stuck-valve rules at their exact thresholds.

    min 3 after / 2 before samples; pinned = every one of the last 2 samples > 95 %;
    rising = last within 2 % of the peak AND rose > 2 % through the window AND last
    ≥ median(before) + 30 %.
    """
    _, sid, alerts = _leak_check(db, before, after)
    assert [json.loads(a.payload_json)["reason"] for a in alerts] == ([reason] if reason else [])
    if reason:
        (alert,) = alerts
        assert (alert.severity, alert.entity_id, alert.code) == ("critical", sid, "leak_or_stuck_valve")


def test_leak_hold_activity_and_message(db):
    """leak-14 / leak-15 / leak-16: one ``leak_hold`` activity per confirmed check (24 h horizon), none otherwise."""
    cid, sid, alerts = _leak_check(db, [40.0, 40.0], [50.0, 60.0, 70.0])
    (alert,) = alerts
    assert alert.message == "Gap Probe 0: soil moisture still rising after irrigation (latest=70.0%)"
    (activity,) = db.list_activity_events(entity_type="cluster", entity_id=cid, source="leak", limit=10)
    assert activity.code == "leak_hold"
    assert json.loads(activity.payload_json) == {
        "started_at": LEAK_START,
        "hold_until": FROZEN_TS + 24 * 3600,
        "sensor_ids": [sid],
    }


def test_settled_sensor_releases_only_its_own_open_leak_alert(db):
    """leak-10 / leak-12 / leak-13: a settled verdict resolves the open leak alert of THAT sensor only.

    Sensor A settles → its open alert is resolved (at the frozen now); A's already-resolved
    older alert keeps its original ``resolved_at``; sensor B (inconclusive, no samples)
    keeps its open alert.
    """
    cid, (a, b), _ = _cluster_with_sensors(db, 2, irrigator=True)

    def leak_alert(key, sensor_id):
        return db.upsert_alert(
            key,
            "leak",
            "leak_or_stuck_valve",
            "Possible leak",
            "m",
            severity="critical",
            entity_type="sensor",
            entity_id=sensor_id,
            cluster_id=cid,
            seen_at=FROZEN_TS - 7200,
        )

    old_a = leak_alert("gap:leak:a:old", a)
    old_a.status, old_a.resolved_at = "resolved", FROZEN_TS - 5000
    open_a, open_b = leak_alert("gap:leak:a", a), leak_alert("gap:leak:b", b)
    db.session.flush()
    for i, soil in enumerate([40.0, 40.0]):
        db.add_sensor_reading(sensor_id=a, timestamp=LEAK_START - 600 * (2 - i), soil_moisture=soil)
    for i, soil in enumerate([60.0, 55.0, 50.0]):
        db.add_sensor_reading(sensor_id=a, timestamp=LEAK_START + 300 * (i + 1), soil_moisture=soil)
    assert LeakDetectionService(db, plant_database()).check_after_irrigation(cid, LEAK_START) == []
    assert (open_a.status, open_a.resolved_at) == ("resolved", FROZEN_TS)
    assert (old_a.status, old_a.resolved_at) == ("resolved", FROZEN_TS - 5000)
    assert (open_b.status, open_b.resolved_at) == ("open", None)


def test_leak_check_without_findings_writes_no_activity(db):
    """leak-15: a settled check records no ``leak_hold`` activity."""
    cid, _, alerts = _leak_check(db, [40.0, 40.0], [60.0, 55.0, 50.0])
    assert alerts == []
    assert db.list_activity_events(entity_type="cluster", entity_id=cid, source="leak", limit=10) == []


# ── web/filters.py ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("age", "text"), [(59, "59s ago"), (60, "1m ago"), (3599, "59m ago"), (3600, "1h ago")])
def test_age_seconds_unit_boundaries(frozen_clock, age, text):
    """web-01: ``age_seconds`` switches unit at exactly 60 s / 3600 s (lower bound inclusive)."""
    assert age_seconds(FROZEN_TS - age) == text


def test_template_filters_current_behavior():
    """web-03 / web-05 / web-06 / web-07: pure Jinja filters at their edges."""
    assert strip_emoji("soil dry ;  temp ok;light low") == "soil dry; temp ok; light low"
    assert [moisture_badge(v, 45.0, 60.0) for v in (44.9, 45.0, 60.0, 60.1)] == ["low", "ok", "ok", "high"]
    assert [severity_class(s) for s in ("CRITICAL", "Warning", "info", None)] == ["danger", "warning", "info", "muted"]
    assert [format_minutes(n) for n in (59, 60, 90, 120)] == ["59 min", "1h", "1h 30m", "2h"]


# ── services/health_monitor.py ──────────────────────────────────────────────


def test_backfill_sensor_fault_needs_every_reading_in_the_window(db):
    """health-09: SENSOR_FAULT is back-filled only when ALL of the newest 5 rows carry water_warning."""
    _, (sid,), _ = _cluster_with_sensors(db, 1)
    for i in range(5):
        db.add_sensor_reading(
            sensor_id=sid, timestamp=FROZEN_TS - 600 * (i + 1), soil_moisture=40.0, water_warning=(i != 2)
        )
    DeviceHealthMonitor(repo=db, registry=DeviceRegistry()).backfill_from_history()
    assert db.session.scalars(select(Alert)).all() == []


# ── services/pump_watcher.py ────────────────────────────────────────────────


class _ScriptedAdapter:
    """Irrigator adapter replaying a list of ``offline`` flags (no error text), then healthy reads."""

    def __init__(self, offline_flags: list[bool]) -> None:
        self.flags = list(offline_flags)
        self.reads = 0

    def read_health(self, irrigator):
        offline = self.flags[self.reads] if self.reads < len(self.flags) else False
        self.reads += 1
        return DeviceHealthState(observed_at=FROZEN_TS, offline=offline, alarms=frozenset(), raw={"source": "local"})


def _watch(db, offline_flags: list[bool], *, seconds: int = 60, max_failures: int = 3):
    _, _, irr_id = _cluster_with_sensors(db, 0, irrigator=True)
    adapter = _ScriptedAdapter(offline_flags)
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda: adapter)
    clock = {"t": 0.0}

    def _sleep(seconds_: float) -> None:
        clock["t"] += seconds_

    watcher = PumpWatcherService(
        db,
        registry,
        poll_seconds=1.0,
        warmup_seconds=0.0,
        max_read_failures=max_failures,
        clock=lambda: clock["t"],
        sleep=_sleep,
    )
    return watcher.watch(db.get_irrigator(irr_id), seconds, started_at=FROZEN_TS), adapter


def test_pump_watcher_counts_offline_reads_as_failures(db):
    """pump-04: an ``offline`` state (no error text) counts toward the read-failure budget → abandoned."""
    result, adapter = _watch(db, [True] * 10)
    assert (result["outcome"], result["polls"], result["read_failures"]) == ("abandoned", 3, 3)
    assert adapter.reads == 3


def test_pump_watcher_failure_budget_counts_consecutive_failures_only(db):
    """pump-09: a healthy read resets the failure counter — alternating failures never abandon."""
    result, adapter = _watch(db, [True, False, True, False, True, False, True], seconds=10)
    assert (result["outcome"], result["polls"], result["read_failures"]) == ("completed", 10, 0)
    assert adapter.reads == 10


# ── services/manual_control.py ──────────────────────────────────────────────


class _StartingAdapter:
    def start(self, irrigator, minutes=None):
        return True, "started"


def _manual_world(db, **caps):
    cid, _, irr_id = _cluster_with_sensors(db, 0, irrigator=True)
    if caps:
        db.set_irrigation_config(cid, **caps)
    db.session.commit()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", _StartingAdapter)
    return cid, db.get_irrigator(irr_id), registry


def test_daily_cap_counts_only_start_minutes(db):
    """manual-03 / manual-11: the per-irrigator minute cap sums ``start`` events only (an ``off`` row's
    minutes don't count), and an open-ended start (``minutes=None``) requests 0 minutes."""
    cid, irr, _ = _manual_world(db, daily_cap_minutes=5)
    db.add_irrigation_event(irrigator_id=irr.id, action="off", triggered_by="manual", duration_minutes=10)
    db.add_irrigation_event(irrigator_id=irr.id, action="start", triggered_by="manual", duration_minutes=2)
    check_rate_limits(db, cid, irr.id, 3)  # 2 + 3 == 5 → allowed
    db.add_irrigation_event(irrigator_id=irr.id, action="start", triggered_by="manual", duration_minutes=3)
    check_rate_limits(db, cid, irr.id, None)  # 5 used + 0 requested == 5 → allowed
    with pytest.raises(ManualActionError) as exc:
        check_rate_limits(db, cid, irr.id, 1)
    assert (exc.value.status_code, exc.value.detail) == (409, "irrigator daily cap reached")


def test_manual_start_checks_registry_before_caps(db):
    """manual-04: with no device registry a start is 503 even when the caps would also refuse it."""
    cid, irr, _ = _manual_world(db, max_events_per_day=1)
    db.add_irrigation_event(irrigator_id=irr.id, action="start", triggered_by="manual", duration_minutes=2)
    with pytest.raises(ManualActionError) as exc:
        manual_start(db, None, None, irr, 3, via="API")
    assert exc.value.status_code == 503


def test_manual_start_schedules_a_manual_watcher(db, monkeypatch):
    """manual-06: a timed manual start schedules the dry-run watcher as ``triggered_by="manual"``
    (so shutdown leaves it on the device timer) — and none without a duration."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        manual_control_module,
        "schedule_pump_watcher",
        lambda irrigator_id, minutes, started_at, **kw: calls.append((irrigator_id, minutes, started_at, kw)),
    )
    _, irr, registry = _manual_world(db)
    assert manual_start(db, registry, None, irr, 4, via="API") == "started"
    assert manual_start(db, registry, None, irr, None, via="API") == "started"
    assert calls == [(irr.id, 4, FROZEN_TS, {"triggered_by": "manual"})]


# ── scheduler.py (jobs / timezone) ──────────────────────────────────────────


def test_health_monitor_job_binds_the_job_repo_before_polling(monkeypatch):
    """scheduler-20: the health job re-binds the monitor to its own session's repo, polls, commits."""
    events: list = []

    class Monitor:
        def bind_repo(self, repo):
            events.append(("bind", repo.session))

        def poll_all(self):
            events.append(("poll",))

    session = SimpleNamespace(
        commit=lambda: events.append(("commit",)), rollback=lambda: events.append(("rollback",)), close=lambda: None
    )
    app = SimpleNamespace(state=SimpleNamespace(health_monitor=Monitor(), session_factory=lambda: session))
    monkeypatch.setattr(scheduler_module, "_app", app)
    scheduler_module._health_monitor_job()
    assert events == [("bind", session), ("poll",), ("commit",)]


def test_check_job_binds_the_health_monitor_then_checks_and_commits(monkeypatch):
    """scheduler-23: the check job re-binds the health monitor to its session's repo before checking."""
    events: list = []

    class Monitor:
        def bind_repo(self, repo):
            events.append(("bind", repo.session))

    monkeypatch.setattr(IrrigationService, "check_all_clusters", lambda self: events.append(("check",)) or [])
    session = SimpleNamespace(
        commit=lambda: events.append(("commit",)), rollback=lambda: events.append(("rollback",)), close=lambda: None
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            device_gateway=None,
            device_registry=None,
            session_factory=lambda: session,
            health_monitor=Monitor(),
            weather_client=None,
            plant_db=None,
        )
    )
    monkeypatch.setattr(scheduler_module, "_app", app)
    scheduler_module._check_job()
    assert events == [("bind", session), ("check",), ("commit",)]


def test_unchanged_timezone_preference_is_a_no_op(clean_env):
    """scheduler-22: re-saving the current display timezone does not rebuild jobs or the weather client."""
    set_display_timezone("UTC")
    sentinel = object()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=None, weather_client=sentinel)))
    scheduler_module.apply_timezone_preference(request, "UTC")
    scheduler_module.apply_timezone_preference(request, None)
    assert request.app.state.weather_client is sentinel


def test_scheduler_unknown_timezone_falls_back_to_utc(clean_env):
    """scheduler-19: an unknown preference timezone binds the scheduler to UTC (not any other zone)."""
    app, engine = _make_stubbed_app(bypass_auth=True)
    try:
        scheduler_module.init_scheduler(app, app.state.settings, "Not/AZone")
        assert str(scheduler_module.scheduler.timezone) == "UTC"
    finally:
        scheduler_module.init_scheduler(app, app.state.settings, "UTC")
        engine.dispose()


# ── web/context.py ──────────────────────────────────────────────────────────


def test_blank_theme_preference_renders_auto(api):
    """web-22: an empty stored theme falls back to ``auto`` in the server-rendered ``data-theme``."""
    client, _ = api
    assert client.put("/api/v1/preferences", json={"theme": ""}).status_code == 200
    assert '<html lang="en" data-theme="auto">' in client.get("/").text


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("TRUE", True), ("false", False), ("", False)])
def test_is_hx_reads_the_header_value(value, expected):
    """web-23: ``is_hx`` is true only when ``HX-Request`` equals "true" (case-insensitive), not merely present."""
    request = Request({"type": "http", "headers": [(b"hx-request", value.encode())]})
    assert is_hx(request) is expected

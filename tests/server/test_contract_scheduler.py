"""Characterization of ``greenhouse_server.scheduler`` (I-iii) — job bodies and job registry.

The scheduler is one process-wide ``BackgroundScheduler`` plus a module global
``_app`` that ``init_scheduler`` rebinds on every ``create_app``. Job bodies are
called here **directly** (synchronously, scheduler stopped unless a test says
otherwise) with ``_app`` pointing at a seeded app, and their effects are pinned:
which services run, what is written and committed, and that each body is a
silent no-op / swallow-and-log in its degraded modes (no gateway, no monitor,
no ``_app``, service raising).

Registry contracts pinned: every job (core and ad-hoc) runs with
``max_instances=1``, ``coalesce=True``, ``misfire_grace_time=None``;
``init_scheduler`` is idempotent; ``paused`` is true only for an explicitly
paused job and mirrors ``preferences.scheduler_paused``; the web form and the
API reach the same ``set_check_all_paused`` and both work on a stopped
scheduler; built-in jobs are refused (409) while ad-hoc ones are deletable.
There is no API to *add* or *modify* jobs — the only runtime "modify" path is
the timezone preference, which rebuilds the two wall-clock cron jobs.

Goldens: ``tests/golden/orchestration/scheduler_*.json``.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from golden import FROZEN_TS, assert_golden_json
from greenhouse_core.constants import LEAK_ALERT_CODE
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_server import scheduler as sched
from greenhouse_server.scheduler import scheduler as bg_scheduler
from greenhouse_server.services import irrigation as irrigation_mod
from server.test_contract_pipeline import Pipeline, RecordingGateway

HOUR = 3600
CORE_JOB_ORDER = ["sensor_sync", "check_all", "plant_health_snapshot", "sensor_anomaly", "device_health_monitor"]


@pytest.fixture
def p(clean_env, frozen_clock):
    pipeline = Pipeline()
    yield pipeline
    pipeline.close()
    if bg_scheduler.running:
        sched.stop_scheduler()


@pytest.fixture
def paused_running_scheduler(p):
    """Start the process-wide scheduler without letting it fire anything."""
    sched.start_scheduler(paused=True)
    try:
        yield bg_scheduler
    finally:
        sched.stop_scheduler()


def _job_view(job) -> dict:
    trigger_tz = getattr(job.trigger, "timezone", None)
    return {
        "id": job.id,
        "name": job.name,
        "func": job.func_ref,
        "trigger": str(job.trigger),
        "trigger_timezone": str(trigger_tz) if trigger_tz is not None else None,
        "args": list(job.args),
        # Pending jobs (stopped scheduler) only get the job defaults applied on
        # start(), so on a stopped scheduler these attributes are absent.
        "max_instances": getattr(job, "max_instances", "<unset>"),
        "coalesce": getattr(job, "coalesce", "<unset>"),
        "misfire_grace_time": getattr(job, "misfire_grace_time", "<unset>"),
        "executor": getattr(job, "executor", "<unset>"),
    }


def _registry_view() -> dict:
    return {
        "jobs": [_job_view(j) for j in bg_scheduler.get_jobs()],
        "get_jobs": sched.get_jobs(),
        "core_job_ids": sorted(sched.core_job_ids()),
        "check_all_paused": sched.is_check_all_paused(),
        "scheduler_running": bg_scheduler.running,
        "scheduler_timezone": str(bg_scheduler.timezone),
    }


def _prefs_paused(p: Pipeline) -> bool:
    with p.repo() as repo:
        return repo.get_preferences().scheduler_paused


# ── Registry ────────────────────────────────────────────────────────────────


def test_registered_jobs_golden(p):
    """The five built-in jobs, in registration order, with their triggers and defaults."""
    view = _registry_view()
    assert [j["id"] for j in view["jobs"]] == CORE_JOB_ORDER
    assert_golden_json("orchestration/scheduler_registry.json", view)


def test_init_scheduler_is_idempotent(p):
    """Calling ``init_scheduler`` again for the same app yields the identical job set (no duplicates)."""
    before = _registry_view()
    sched.init_scheduler(p.app, p.app.state.settings, tz_name="UTC")
    sched.init_scheduler(p.app, p.app.state.settings, tz_name="UTC")
    assert _registry_view() == before
    assert sched._app is p.app


def test_init_scheduler_rebinds_the_module_app(p, clean_env):
    """A second ``create_app`` takes over ``scheduler._app`` (the trap behind the lazy imports)."""
    second = Pipeline()
    try:
        assert sched._app is second.app
        assert [j.id for j in bg_scheduler.get_jobs()] == CORE_JOB_ORDER
    finally:
        second.close()


def test_every_job_has_safe_defaults_stopped_and_running(p):
    """``max_instances=1``, ``coalesce=True``, ``misfire_grace_time=None`` on core *and* ad-hoc jobs."""
    p.app.state.device_registry = p.wiring.registry
    irrigation_mod._add_leak_check_job(1, FROZEN_TS)  # ad-hoc job on a stopped scheduler
    sched.start_scheduler(paused=True)
    try:
        assert irrigation_mod.schedule_pump_watcher(1, 2, FROZEN_TS) is True
        bg_scheduler.add_job(lambda: None, "date", run_date="2026-04-15 11:00:00", id="custom-adhoc")
        jobs = bg_scheduler.get_jobs()
        assert len(jobs) == len(CORE_JOB_ORDER) + 3
        for job in jobs:
            assert (job.max_instances, job.coalesce, job.misfire_grace_time) == (1, True, None), job.id
    finally:
        sched.stop_scheduler()


def test_running_scheduler_reports_next_run_times(p, paused_running_scheduler):
    """On a running scheduler every core job has a ``next_run_time`` (frozen clock → exact values)."""
    assert_golden_json(
        "orchestration/scheduler_registry_running.json",
        {"get_jobs": sched.get_jobs(), "paused": sched.is_check_all_paused()},
    )


def test_timezone_preference_rebuilds_cron_jobs_and_keeps_pause(p):
    """The only runtime 'modify' path: a timezone change re-adds the two cron jobs in the new zone."""
    assert p.call("POST", "/api/v1/scheduler/pause")["response"] == {"paused": True}
    p.call("PUT", "/api/v1/preferences", {"timezone": "Europe/Rome"})
    view = _registry_view()
    assert view["check_all_paused"] is True
    assert _prefs_paused(p) is True
    assert_golden_json("orchestration/scheduler_registry_after_tz_change.json", view)


def test_no_api_to_add_or_modify_jobs(p):
    """The scheduler API surface is list / delete / pause / resume (+ /health) — nothing adds or edits jobs."""
    routes = sorted(
        (method.upper(), path)
        for path, ops in p.app.openapi()["paths"].items()
        for method, op in ops.items()
        if "scheduler" in op.get("tags", [])
    )
    assert routes == [
        ("DELETE", "/api/v1/scheduler/jobs/{job_id}"),
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/health/system"),
        ("GET", "/api/v1/scheduler/jobs"),
        ("POST", "/api/v1/scheduler/pause"),
        ("POST", "/api/v1/scheduler/resume"),
    ]


# ── paused flag ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("running", [False, True], ids=["stopped", "running"])
def test_paused_flag_is_explicit_and_mirrors_preferences(p, running):
    """``paused`` is true only for ``check_all`` after an explicit pause, and equals the persisted flag."""
    if running:
        sched.start_scheduler(paused=True)
    try:

        def paused_ids() -> list[str]:
            return [j["id"] for j in p.call("GET", "/api/v1/scheduler/jobs")["response"] if j["paused"]]

        assert paused_ids() == []
        assert _prefs_paused(p) is False
        assert p.call("POST", "/api/v1/scheduler/pause")["response"] == {"paused": True}
        assert paused_ids() == ["check_all"]
        assert _prefs_paused(p) is True and sched.is_check_all_paused() is True
        assert p.call("POST", "/api/v1/scheduler/pause")["response"] == {"paused": True}  # idempotent
        assert p.call("POST", "/api/v1/scheduler/resume")["response"] == {"paused": False}
        assert paused_ids() == []
        assert _prefs_paused(p) is False and sched.is_check_all_paused() is False
    finally:
        if running:
            sched.stop_scheduler()


def test_web_and_api_pause_share_one_function_on_a_stopped_scheduler(p, monkeypatch):
    """Web ``POST /scheduler/{pause,resume}`` and the API both go through ``set_check_all_paused``."""
    calls: list[tuple[str, bool]] = []
    real = sched.set_check_all_paused
    origin = {"via": None}

    def spy(repo, paused):
        calls.append((origin["via"], paused))
        return real(repo, paused)

    import greenhouse_server.web.routes.analytics as web_analytics

    monkeypatch.setattr(sched, "set_check_all_paused", spy)
    # The web module may hold its own reference to the function; patch it too if present.
    monkeypatch.setattr(web_analytics, "set_check_all_paused", spy, raising=False)
    assert bg_scheduler.running is False

    results = []
    for via, method_path in (
        ("api", "/api/v1/scheduler/pause"),
        ("web", "/scheduler/pause"),
        ("api", "/api/v1/scheduler/resume"),
        ("web", "/scheduler/resume"),
    ):
        origin["via"] = via
        resp = p.client.post(method_path, follow_redirects=False)
        results.append((via, resp.status_code, resp.headers.get("location"), _prefs_paused(p)))

    assert calls == [("api", True), ("web", True), ("api", False), ("web", False)]
    assert results == [
        ("api", 200, None, True),
        ("web", 303, "/scheduler", True),
        ("api", 200, None, False),
        ("web", 303, "/scheduler", False),
    ]


def test_pause_without_check_all_job_is_404_and_persists_nothing(p):
    bg_scheduler.remove_job("check_all")
    try:
        api = p.call("POST", "/api/v1/scheduler/pause")
        web = p.client.post("/scheduler/pause", follow_redirects=False)
        assert (api["status"], api["response"]) == (404, {"detail": "Job check_all not found"})
        assert web.status_code == 404
        assert _prefs_paused(p) is False
    finally:
        sched.init_scheduler(p.app, p.app.state.settings, tz_name="UTC")


def test_apply_persisted_pause_edge_cases(p, monkeypatch, caplog):
    """Startup re-apply: no-op when not persisted / job missing; a failing pause is logged, not raised."""
    sched.apply_persisted_pause(False)
    assert sched.is_check_all_paused() is False
    sched.apply_persisted_pause(True)
    assert sched.is_check_all_paused() is True
    sched.init_scheduler(p.app, p.app.state.settings, tz_name="UTC")

    def broken(job_id):
        raise RuntimeError("cannot pause")

    monkeypatch.setattr(bg_scheduler, "pause_job", broken)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.scheduler"):
        sched.apply_persisted_pause(True)
    assert "Failed to re-apply persisted scheduler pause" in caplog.text
    monkeypatch.undo()
    bg_scheduler.remove_job("check_all")
    sched.apply_persisted_pause(True)  # job missing → silently nothing
    assert sched.is_check_all_paused() is False


# ── delete ──────────────────────────────────────────────────────────────────


def test_delete_core_vs_adhoc_jobs(p):
    """Built-in jobs → 409 (API) and nothing changes; an ad-hoc job is deleted; unknown → 404."""
    irrigation_mod._add_leak_check_job(1, FROZEN_TS)
    adhoc_id = f"leak-check-1-{FROZEN_TS}"
    out = {}
    for job_id in CORE_JOB_ORDER:
        resp = p.call("DELETE", f"/api/v1/scheduler/jobs/{job_id}")
        out[job_id] = (resp["status"], resp["response"]["detail"])
    out[adhoc_id] = (lambda r: (r["status"], r["response"]))(p.call("DELETE", f"/api/v1/scheduler/jobs/{adhoc_id}"))
    out["nope"] = (lambda r: (r["status"], r["response"]))(p.call("DELETE", "/api/v1/scheduler/jobs/nope"))
    assert [j.id for j in bg_scheduler.get_jobs()] == CORE_JOB_ORDER
    assert {k: v[0] for k, v in out.items()} == {**dict.fromkeys(CORE_JOB_ORDER, 409), adhoc_id: 200, "nope": 404}
    assert_golden_json("orchestration/scheduler_delete.json", out)


def test_web_delete_requires_running_scheduler(p):
    """The web delete form answers 503 on a stopped scheduler, then 409 / 200 / 404 like the API."""
    irrigation_mod._add_leak_check_job(1, FROZEN_TS)
    stopped = p.client.post("/scheduler/jobs/check_all/delete")
    sched.start_scheduler(paused=True)
    try:
        core = p.client.post("/scheduler/jobs/check_all/delete")
        adhoc = p.client.post(f"/scheduler/jobs/leak-check-1-{FROZEN_TS}/delete")
        missing = p.client.post("/scheduler/jobs/nope/delete")
    finally:
        sched.stop_scheduler()
    assert [r.status_code for r in (stopped, core, adhoc, missing)] == [503, 409, 200, 404]
    assert adhoc.text == ""


# ── Job bodies ─────────────────────────────────────────────────────────────


def _seed_series(p: Pipeline, *, ages: range, soil: float = 45.0, cluster: str = "Golden Cluster") -> dict:
    ids = p.seed_cluster(cluster, soil=None)
    with p.repo() as repo:
        for age in ages:
            repo.add_sensor_reading(
                sensor_id=ids["sensor_id"], timestamp=FROZEN_TS - age, soil_moisture=soil, temperature=22.0
            )
    return ids


def test_sync_job_is_a_noop_without_gateway(p, monkeypatch):
    opened = []
    monkeypatch.setattr(p.app.state, "session_factory", lambda: opened.append(1))
    sched._sync_job()
    assert opened == []


def test_sync_job_with_gateway_syncs_every_sensor_and_commits(p):
    _seed_series(p, ages=range(2 * HOUR, 6 * HOUR, HOUR))
    p.seed_cluster("No sensor", sensor=False, soil=None)
    gw = RecordingGateway({"soil_moisture": 41.0, "temperature": 21.0})
    p.app.state.device_gateway = gw
    p.mark()
    sched._sync_job()
    new = p.db_rows()["sensor_readings"]
    assert gw.calls == [
        ["get_device_logs", "fake_sensor_001", (FROZEN_TS - 2 * HOUR - 60) * 1000],
        ["group_logs_by_timestamp", 0],
        ["get_live_reading", "fake_sensor_001"],
    ]
    assert [(r["sensor_id"], r["timestamp"], r["soil_moisture"]) for r in new] == [(1, FROZEN_TS, 41.0)]


def test_sync_job_failure_is_rolled_back_and_logged(p, monkeypatch, caplog):
    from greenhouse_server.services.sync import SyncService

    p.app.state.device_gateway = RecordingGateway()

    def explode(self, hours=24):
        raise RuntimeError("cloud down")

    monkeypatch.setattr(SyncService, "sync_all_sensors", explode)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.scheduler"):
        sched._sync_job()
    assert "Sync job failed" in caplog.text


def test_health_snapshot_job_writes_daily_rows(p):
    _seed_series(p, ages=range(0, 12 * HOUR, HOUR))
    sched._health_snapshot_job()
    with p.app.state.session_factory() as session:
        from greenhouse_core.models import PlantHealthDaily

        rows = [
            {c: getattr(r, c) for c in ("plant_id", "date_key", "score", "sample_count", "timestamp")}
            for r in session.query(PlantHealthDaily).order_by(PlantHealthDaily.id)
        ]
    assert [(r["plant_id"], r["date_key"], r["timestamp"]) for r in rows] == [(1, "2026-04-15", FROZEN_TS)]
    assert_golden_json("orchestration/scheduler_health_snapshot.json", rows)


def test_health_snapshot_job_failure_is_logged(p, monkeypatch, caplog):
    from greenhouse_server.services.health import PlantHealthService

    monkeypatch.setattr(PlantHealthService, "snapshot_daily", lambda self: 1 / 0)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.scheduler"):
        sched._health_snapshot_job()
    assert "Plant health snapshot job failed" in caplog.text


def test_anomaly_job_raises_stale_alert_and_notifies(p):
    # 12 hourly readings ending 5 h ago → silent for > 2 × the median interval.
    _seed_series(p, ages=range(5 * HOUR, 17 * HOUR, HOUR))
    p.mark()
    sched._anomaly_job()
    alerts = p.db_rows()["alerts"]
    assert [(a["source"], a["code"], a["status"]) for a in alerts] == [("anomaly", "sensor_stale", "open")]
    assert_golden_json("orchestration/scheduler_anomaly.json", {"alerts": alerts, "notifications": p.notifier.sent})


def test_anomaly_job_failure_is_logged(p, monkeypatch, caplog):
    from greenhouse_server.services.anomaly import SensorAnomalyService

    monkeypatch.setattr(SensorAnomalyService, "scan", lambda self: 1 / 0)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.scheduler"):
        sched._anomaly_job()
    assert "Anomaly scan job failed" in caplog.text


def test_health_monitor_job_noops(p, monkeypatch):
    """No ``_app`` → return; no monitor wired (tests' default) → return without opening a session."""
    opened = []
    monkeypatch.setattr(p.app.state, "session_factory", lambda: opened.append(1))
    assert getattr(p.app.state, "health_monitor", None) is None
    sched._health_monitor_job()
    monkeypatch.setattr(sched, "_app", None)
    sched._health_monitor_job()
    assert opened == []


def test_init_health_monitor_then_job_polls_every_device(p):
    """With a registry, ``init_health_monitor`` wires the singleton; the job polls irrigators and sensors."""
    ids = p.seed_cluster(soil=35.0)
    sched.init_health_monitor(p.app, p.app.state.settings)
    assert getattr(p.app.state, "health_monitor", None) is None  # no registry on app.state → skipped
    p.app.state.device_registry = p.wiring.registry
    sched.init_health_monitor(p.app, p.app.state.settings)
    monitor = p.app.state.health_monitor
    p.wiring.irrigator.set_health(DeviceHealthState(observed_at=FROZEN_TS, alarms=frozenset({HealthAlarm.NO_WATER})))
    p.mark()
    sched._health_monitor_job()
    assert p.app.state.health_monitor is monitor
    assert p.wiring.irrigator.calls == [("read_health", ids["irrigator_id"])]
    assert p.wiring.sensor.calls == [("read_health", ids["sensor_id"])]
    alerts = p.db_rows()["alerts"]
    assert [(a["code"], a["entity_type"], a["status"]) for a in alerts] == [("no_water", "irrigator", "open")]
    # The cache now blocks actuation for the irrigator.
    with p.repo() as repo:
        assert monitor.is_actuation_blocked(repo.get_irrigator(ids["irrigator_id"])) == (True, [HealthAlarm.NO_WATER])


def test_health_monitor_job_failure_is_logged(p, monkeypatch, caplog):
    p.app.state.device_registry = p.wiring.registry
    sched.init_health_monitor(p.app, p.app.state.settings)
    monkeypatch.setattr(p.app.state.health_monitor, "poll_all", lambda: 1 / 0)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.scheduler"):
        sched._health_monitor_job()
    assert "Device health monitor job failed" in caplog.text


def test_init_health_monitor_startup_hook_failure_still_wires_monitor(p, monkeypatch, caplog):
    from greenhouse_server.services.health_monitor import DeviceHealthMonitor

    p.app.state.device_registry = p.wiring.registry
    monkeypatch.setattr(DeviceHealthMonitor, "backfill_from_history", lambda self: 1 / 0)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.scheduler"):
        sched.init_health_monitor(p.app, p.app.state.settings)
    assert isinstance(p.app.state.health_monitor, DeviceHealthMonitor)
    assert "Health monitor startup hooks failed" in caplog.text


def test_init_health_monitor_leaves_old_pump_dry_run_alerts_alone(p):
    """OD3: the startup migration of pre-unification ``pump_dry_run`` alerts is gone.

    An open alert with the old ``pump::pump_dry_run::…`` key used to be resolved by
    ``init_health_monitor``; it now stays as it is (resolve it by hand if one exists).
    """
    ids = p.seed_cluster(soil=35.0)
    with p.repo() as repo:
        repo.upsert_alert(
            dedup_key=f"pump::pump_dry_run::{ids['cluster_id']}::irrigator{ids['irrigator_id']}",
            source="pump",
            code="pump_dry_run",
            title="Pump dry-run",
            message="row from the pre-unification code",
            severity="critical",
            entity_type="irrigator",
            entity_id=ids["irrigator_id"],
            cluster_id=ids["cluster_id"],
        )
    p.app.state.device_registry = p.wiring.registry
    sched.init_health_monitor(p.app, p.app.state.settings)
    assert [(a["code"], a["status"]) for a in p.db_rows()["alerts"]] == [("pump_dry_run", "open")]


def test_get_cloud_reads_app_state(p, monkeypatch):
    assert sched._get_cloud() is None
    gw = RecordingGateway()
    p.app.state.device_gateway = gw
    assert sched._get_cloud() is gw
    monkeypatch.setattr(sched, "_app", None)
    assert sched._get_cloud() is None


# ── Leak follow-up job bodies ───────────────────────────────────────────────


def _seed_leak_shape(p: Pipeline, started_at: int) -> dict:
    """Baseline 40 % before ``started_at``, then a climb that never settles (a stuck valve)."""
    ids = p.seed_cluster(soil=None)
    with p.repo() as repo:
        for i, soil in enumerate((40.0, 40.5, 40.0, 39.5)):
            repo.add_sensor_reading(
                sensor_id=ids["sensor_id"], timestamp=started_at - 1800 + i * 300, soil_moisture=soil
            )
        for i, soil in enumerate((50.0, 58.0, 65.0, 71.0, 76.0, 80.0)):
            repo.add_sensor_reading(
                sensor_id=ids["sensor_id"], timestamp=started_at + 300 + i * 300, soil_moisture=soil
            )
    return ids


def test_run_leak_check_raises_hold_once_and_marks_done(p):
    started_at = FROZEN_TS - 2 * HOUR
    _seed_leak_shape(p, started_at)
    p.mark()
    irrigation_mod._run_leak_check(1, started_at)
    first = p.db_rows()
    irrigation_mod._run_leak_check(1, started_at)  # idempotent: marker found → skipped
    second = p.db_rows()
    assert second == first
    assert [a["code"] for a in first["activity_events"]] == ["leak_hold", "leak_check"]
    assert [(a["code"], a["status"]) for a in first["alerts"]] == [(LEAK_ALERT_CODE, "open")]
    assert_golden_json(
        "orchestration/scheduler_leak_check.json", {"db_writes": first, "notifications": p.notifier.sent}
    )


def test_run_leak_check_noops_and_failure(p, monkeypatch, caplog):
    from greenhouse_server.services.leak import LeakDetectionService

    started_at = FROZEN_TS - 2 * HOUR
    _seed_leak_shape(p, started_at)
    p.mark()
    monkeypatch.setattr(sched, "_app", None)
    irrigation_mod._run_leak_check(1, started_at)
    monkeypatch.setattr(sched, "_app", p.app)
    monkeypatch.setattr(LeakDetectionService, "check_after_irrigation", lambda self, c, s: 1 / 0)
    with caplog.at_level(logging.ERROR, logger="greenhouse_server.services.irrigation"):
        irrigation_mod._run_leak_check(1, started_at)
    assert "Leak check job failed for cluster 1" in caplog.text
    # Neither call left a marker (so a restart would re-arm it).
    assert p.db_rows()["activity_events"] == []


def test_rearm_leak_checks(p):
    """Stopped scheduler → 0; running → pending auto starts re-armed (overdue ones run now), manual ones never."""
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=600)  # due in 20 min
    p.add_event(ids["irrigator_id"], age=2 * HOUR)  # overdue → now
    p.add_event(ids["irrigator_id"], age=3 * HOUR, triggered_by="manual")  # never re-armed
    p.add_event(ids["irrigator_id"], age=30 * HOUR)  # outside LEAK_HOLD_HOURS
    assert irrigation_mod.rearm_leak_checks() == 0
    sched.start_scheduler(paused=True)
    try:
        assert irrigation_mod.rearm_leak_checks() == 2
        jobs = sorted((j.id, str(j.trigger)) for j in bg_scheduler.get_jobs() if j.id.startswith("leak-check-"))
    finally:
        sched.stop_scheduler()
    assert jobs == [
        (f"leak-check-1-{FROZEN_TS - 2 * HOUR}", "date[2026-04-15 10:00:00 UTC]"),
        (f"leak-check-1-{FROZEN_TS - 600}", "date[2026-04-15 10:20:00 UTC]"),
    ]


def test_schedule_pump_watcher_guards(p):
    """Watcher scheduling refuses: non-positive duration, stopped scheduler, no registry, feature off."""
    assert irrigation_mod.schedule_pump_watcher(1, 0, FROZEN_TS) is False
    assert irrigation_mod.schedule_pump_watcher(1, 5, FROZEN_TS) is False  # scheduler stopped
    sched.start_scheduler(paused=True)
    try:
        assert irrigation_mod.schedule_pump_watcher(1, 5, FROZEN_TS) is False  # no registry on app.state
        p.app.state.device_registry = p.wiring.registry
        p.app.state.settings = p.app.state.settings.model_copy(update={"pump_watcher_enabled": False})
        assert irrigation_mod.schedule_pump_watcher(1, 5, FROZEN_TS) is False
        p.app.state.settings = p.app.state.settings.model_copy(update={"pump_watcher_enabled": True})
        assert irrigation_mod.schedule_pump_watcher(1, 5, FROZEN_TS, triggered_by="manual") is True
        (job,) = [j for j in bg_scheduler.get_jobs() if j.id.startswith("pump-watcher-")]
        assert (job.id, job.name, str(job.trigger)) == (
            f"pump-watcher-1-{FROZEN_TS}",
            "Pump watcher irrigator 1",
            "date[2026-04-15 10:00:00 UTC]",
        )
    finally:
        sched.stop_scheduler()


def test_check_job_with_app_runs_check_all_via_api_equivalent(p):
    """``_check_job`` on a seeded app (registry wired) actuates exactly like ``POST /check``."""
    p.seed_cluster(soil=35.0)
    p.app.state.device_registry = p.wiring.registry
    p.mark()
    sched._check_job()
    db = p.db_rows()
    assert [(e["action"], e["triggered_by"]) for e in db["irrigation_events"]] == [("start", "auto")]
    assert [(r["action"], r["actuated"]) for r in db["decision_logs"]] == [("irrigate", True)]
    assert [n["kind"] for n in p.notifier.sent] == ["irrigation"]


def test_health_endpoint_reflects_scheduler_state(p):
    """``GET /health`` exposes ``scheduler_running`` and the same job serializer as ``/scheduler/jobs``."""
    client = TestClient(p.app)
    stopped = client.get("/api/v1/health").json()
    assert stopped["scheduler_running"] is False
    assert stopped["jobs"] == client.get("/api/v1/scheduler/jobs").json()

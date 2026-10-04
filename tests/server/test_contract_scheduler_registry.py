"""Characterization: the APScheduler job registry built by ``create_app`` (gap 9.3 / checklist #10).

``greenhouse_server.scheduler.scheduler`` is one process-wide ``BackgroundScheduler``.
``create_app`` → ``init_scheduler`` re-configures it (timezone from
``UserPreferences.timezone``, job defaults) and registers the five core jobs. Apps
are built with ``enable_scheduler=False``, so after ``create_app`` the jobs are
*pending* (APScheduler applies ``job_defaults`` and computes ``next_run_time`` only
when the scheduler starts). This module snapshots both states:

1. pending, straight after ``create_app`` — registration order, ids, names, func
   refs, trigger type/fields/timezone/start date;
2. started in APScheduler's ``paused`` mode — the job-store view (store order =
   next-run order), ``misfire_grace_time`` / ``coalesce`` / ``max_instances``,
   ``next_run_time`` and pause state, plus the public ``get_jobs()`` dicts.

The wall clock is frozen *before* the app is built: interval triggers take
``start_date = now + interval`` at add time, so freezing makes every timestamp
reproducible (nothing is normalized). Scenarios cover the default settings, custom
cadences and a persisted timezone + pause (the legacy interval scenario left with OD3).
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from golden import assert_golden_json, install_offline_weather
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server import scheduler as sched_mod
from greenhouse_server.app import create_app
from greenhouse_server.config import Settings

_BASE = {
    "db_url": "sqlite://",
    "enable_scheduler": False,
    "auth_enabled": True,
    "auth_secret_key": "unit-test-jwt-secret-do-not-use-in-prod-please",
    "auth_admin_username": "test-admin",
    "auth_admin_password": "test-admin-pw-123",
}

SCENARIOS = {
    "default": {"settings": {}, "prefs": None},
    "custom_cadence": {"settings": {"sync_interval_minutes": 30, "check_cron_hours": "0,6,12,18"}, "prefs": None},
    "persisted_tz_and_pause": {"settings": {}, "prefs": {"timezone": "Europe/Rome", "scheduler_paused": True}},
}


def _engine():
    return create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)


def _build(settings_override: dict, prefs: dict | None):
    """Build the app like ``_make_stubbed_app``; with ``prefs``, persist them and rebuild."""
    engine = _engine()
    settings = Settings(_env_file=None, **{**_BASE, **settings_override})
    app = create_app(settings, engine=engine)
    if prefs is not None:
        session = app.state.session_factory()
        repo = IrrigationRepository(session)
        row = repo.get_preferences()
        for key, value in prefs.items():
            setattr(row, key, value)
        session.commit()
        session.close()
        app = create_app(settings, engine=engine)  # restart: re-reads tz + pause
    install_offline_weather(app)
    return app, engine


def _trigger(trigger) -> dict:
    out = {
        "type": type(trigger).__name__,
        "str": str(trigger),
        "repr": repr(trigger),
        "timezone": str(trigger.timezone),
        "jitter": trigger.jitter,
    }
    if hasattr(trigger, "interval"):
        out["interval_seconds"] = trigger.interval.total_seconds()
        out["start_date"] = trigger.start_date.isoformat()
        out["end_date"] = trigger.end_date.isoformat() if trigger.end_date else None
    if hasattr(trigger, "fields"):
        out["fields"] = [[f.name, str(f), f.is_default] for f in trigger.fields]
        out["start_date"] = trigger.start_date.isoformat() if trigger.start_date else None
        out["end_date"] = trigger.end_date.isoformat() if trigger.end_date else None
    return out


def _pending_job(job) -> dict:
    return {
        "id": job.id,
        "name": job.name,
        "func_ref": job.func_ref,
        "trigger": _trigger(job.trigger),
        "executor": job.executor,
        "args": list(job.args),
        "kwargs": dict(job.kwargs),
        "pending": job.pending,
    }


def _stored_job(job) -> dict:
    return {
        "id": job.id,
        "func_ref": job.func_ref,
        "misfire_grace_time": job.misfire_grace_time,
        "coalesce": job.coalesce,
        "max_instances": job.max_instances,
        "executor": job.executor,
        "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        "pending": job.pending,
    }


def _snapshot(app) -> dict:
    scheduler = sched_mod.scheduler
    assert not scheduler.running
    snap = {
        "scheduler": {
            "class": type(scheduler).__name__,
            "timezone": str(scheduler.timezone),
            "job_defaults": dict(scheduler._job_defaults),
        },
        "pending_jobs": [_pending_job(j) for j in scheduler.get_jobs()],
        "pending_get_jobs": sched_mod.get_jobs(),
        "core_job_ids": sorted(sched_mod.core_job_ids()),
        "check_all_paused": sched_mod.is_check_all_paused(),
    }
    sched_mod.start_scheduler(paused=True)
    try:
        snap["started_paused_state"] = scheduler.state
        snap["stored_jobs"] = [_stored_job(j) for j in scheduler.get_jobs()]
        snap["running_get_jobs"] = sched_mod.get_jobs()
    finally:
        sched_mod.stop_scheduler()
    return snap


def test_scheduler_registry_matches_golden(clean_env, frozen_clock):
    """Every scenario's pending + started registry equals ``contracts/scheduler_jobs.json``."""
    observed = {}
    for scenario, spec in SCENARIOS.items():
        app, engine = _build(spec["settings"], spec["prefs"])
        try:
            observed[scenario] = _snapshot(app)
        finally:
            engine.dispose()
    assert_golden_json("contracts/scheduler_jobs.json", observed)


def test_registration_order_and_identity(clean_env, frozen_clock):
    """The five core jobs, in ``init_scheduler`` registration order, bound to the app."""
    app, engine = _build({}, None)
    try:
        jobs = sched_mod.scheduler.get_jobs()
        assert [(j.id, j.name, j.func_ref) for j in jobs] == [
            ("sensor_sync", "Sensor data sync", "greenhouse_server.scheduler:_sync_job"),
            ("check_all", "Check all clusters", "greenhouse_server.scheduler:_check_job"),
            (
                "plant_health_snapshot",
                "Daily plant health snapshot",
                "greenhouse_server.scheduler:_health_snapshot_job",
            ),
            ("sensor_anomaly", "Sensor anomaly scan", "greenhouse_server.scheduler:_anomaly_job"),
            ("device_health_monitor", "Device health monitor", "greenhouse_server.scheduler:_health_monitor_job"),
        ]
        assert sched_mod._app is app
        assert sched_mod.CHECK_ALL_JOB_ID == "check_all"
        assert sched_mod._TZ_BOUND_CRON_JOBS == ("check_all", "plant_health_snapshot")
        assert sched_mod._JOB_DEFAULTS == {"misfire_grace_time": None, "coalesce": True, "max_instances": 1}
    finally:
        engine.dispose()


def test_rebuilding_the_app_does_not_duplicate_jobs(clean_env, frozen_clock):
    app1, engine1 = _build({}, None)
    app2, engine2 = _build({"sync_interval_minutes": 45}, None)
    try:
        jobs = sched_mod.scheduler.get_jobs()
        assert len(jobs) == 5
        assert jobs[0].trigger.interval.total_seconds() == 45 * 60
        assert sched_mod._app is app2
    finally:
        engine1.dispose()
        engine2.dispose()


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_registry_ignores_the_removed_check_interval_hours(clean_env, frozen_clock):
    """OD3: an old ``IRRIGATION_CHECK_INTERVAL_HOURS`` setting no longer becomes ``*/N`` and logs nothing."""
    # Attach directly to the module logger: ``init_db`` runs Alembic's ``fileConfig``,
    # which replaces the root handlers (and with them pytest's caplog handler).
    handler = _ListHandler()
    sched_logger = logging.getLogger("greenhouse_server.scheduler")
    sched_logger.addHandler(handler)
    try:
        app, engine = _build({"check_interval_hours": 6}, None)
        hour = next(str(f) for f in sched_mod.scheduler.get_job("check_all").trigger.fields if f.name == "hour")
        engine.dispose()
    finally:
        sched_logger.removeHandler(handler)
    assert hour == "*"
    assert handler.records == []

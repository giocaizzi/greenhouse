"""Scheduler registration tests — confirm cron trigger and cooldown gating."""

import time

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fake_data import FAKE_DEVICE_ID, FAKE_PLANT_SPECIES, FAKE_SENSOR_ID
from greenhouse_core.constants import MIN_COOLDOWN_HOURS
from greenhouse_core.logic import IrrigationLogic
from greenhouse_core.models import Base
from greenhouse_core.plant_db import get_plant_database
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.app import create_app
from greenhouse_server.config import Settings
from greenhouse_server.scheduler import scheduler as bg_scheduler


def _build_app(cron_hours: str = "*", interval_hours: int | None = None):
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    settings = Settings(
        db_url="sqlite://",
        enable_scheduler=False,
        check_cron_hours=cron_hours,
        check_interval_hours=interval_hours,
    )
    create_app(settings, engine=engine)
    return engine


class TestCheckAllCronRegistration:
    """`check_all` registers as a CronTrigger driven by check_cron_hours."""

    def test_check_all_uses_cron_trigger(self):
        """check_all is registered with a CronTrigger (not an IntervalTrigger)."""
        engine = _build_app()
        try:
            job = bg_scheduler.get_job("check_all")
            assert job is not None, "check_all job not registered"
            assert isinstance(job.trigger, CronTrigger)
            assert not isinstance(job.trigger, IntervalTrigger)
        finally:
            engine.dispose()

    def test_cron_trigger_runs_hourly_by_default(self):
        """Default cron hour='*' and minute='0' — fires every hour at :00."""
        engine = _build_app(cron_hours="*")
        try:
            job = bg_scheduler.get_job("check_all")
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["hour"] == "*"
            assert fields["minute"] == "0"
        finally:
            engine.dispose()

    def test_cron_trigger_honors_custom_hours(self):
        """A custom IRRIGATION_CHECK_CRON_HOURS list is wired into the trigger."""
        engine = _build_app(cron_hours="0,6,12,18")
        try:
            job = bg_scheduler.get_job("check_all")
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["hour"] == "0,6,12,18"
            assert fields["minute"] == "0"
        finally:
            engine.dispose()


class TestLegacyIntervalShim:
    """`IRRIGATION_CHECK_INTERVAL_HOURS` is honored and translated to `*/N` cron."""

    def test_interval_translates_to_step_cron(self, monkeypatch):
        """When only the legacy var is set, the trigger becomes `*/N` and a warning fires."""
        from greenhouse_server import scheduler as sched_mod

        warnings_seen: list[str] = []
        monkeypatch.setattr(sched_mod.logger, "warning", lambda msg, *a, **kw: warnings_seen.append(msg % a))
        engine = _build_app(cron_hours="*", interval_hours=2)
        try:
            job = bg_scheduler.get_job("check_all")
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["hour"] == "*/2"
            assert fields["minute"] == "0"
            assert any("IRRIGATION_CHECK_INTERVAL_HOURS is deprecated" in msg for msg in warnings_seen)
        finally:
            engine.dispose()

    def test_explicit_cron_hours_wins_over_legacy_interval(self, monkeypatch):
        """If both are set, the new cron var wins and no shim warning is emitted."""
        from greenhouse_server import scheduler as sched_mod

        warnings_seen: list[str] = []
        monkeypatch.setattr(sched_mod.logger, "warning", lambda msg, *a, **kw: warnings_seen.append(msg % a))
        engine = _build_app(cron_hours="0,12", interval_hours=6)
        try:
            job = bg_scheduler.get_job("check_all")
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["hour"] == "0,12"
            assert not any("IRRIGATION_CHECK_INTERVAL_HOURS is deprecated" in msg for msg in warnings_seen)
        finally:
            engine.dispose()


class TestCooldownGatesActuation:
    """Hourly checks must NOT actuate more often than the engine cooldown allows."""

    def test_24_hourly_checks_yield_at_most_4_actuations(self, monkeypatch):
        """With a 6h cooldown and 24 simulated hourly checks, at most 4 actuations.

        The scheduler can be tuned to fire frequently (default hourly), but the
        engine's MIN_COOLDOWN_HOURS gate must continue to cap real actuations.
        """
        engine = create_engine("sqlite://", echo=False)
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                repo = IrrigationRepository(session)
                cluster_id = repo.add_cluster("Cooldown Cluster")
                repo.add_plant(
                    cluster_id=cluster_id,
                    species=FAKE_PLANT_SPECIES,
                    water_needs="medium",
                )
                irrigator_id = repo.add_irrigator(
                    cluster_id=cluster_id,
                    tuya_device_id=FAKE_DEVICE_ID,
                    name="Irrigator",
                    irrigator_type="tuya_cloud",
                    config={},
                )
                sensor_id = repo.add_sensor(
                    cluster_id=cluster_id,
                    tuya_device_id=FAKE_SENSOR_ID,
                    name="Dry Sensor",
                    sensor_type="soil_moisture",
                    config={},
                )
                session.commit()

                logic = IrrigationLogic(repo, get_plant_database())
                start = int(time.time()) - 24 * 3600

                actuations = 0
                # Patch the engine's and repository's clocks for each
                # simulated hourly check — both consult `time.time()` when
                # deciding the cooldown cutoff.
                from greenhouse_core import repository as repo_mod
                from greenhouse_core.logic import engine as engine_mod

                for step in range(24):
                    now = start + step * 3600
                    monkeypatch.setattr(engine_mod.time, "time", lambda now=now: now)
                    monkeypatch.setattr(repo_mod.time, "time", lambda now=now: now)
                    # Reading at "now" — always very dry, so absent the cooldown
                    # gate the engine would irrigate every hour.
                    repo.add_sensor_reading(sensor_id=sensor_id, timestamp=now, soil_moisture=20.0)
                    decision = logic.decide_for_cluster(cluster_id)
                    if decision.action.value == "irrigate":
                        repo.add_irrigation_event(
                            irrigator_id=irrigator_id,
                            action="start",
                            triggered_by="auto",
                            duration_minutes=decision.duration_minutes,
                            timestamp=now,
                        )
                        actuations += 1
                    session.commit()

                # 24h / 6h cooldown = at most 4 actuations.
                max_allowed = 24 // MIN_COOLDOWN_HOURS
                assert max_allowed == 4
                assert actuations <= max_allowed, (
                    f"engine fired {actuations} times in 24h; cooldown should cap at {max_allowed}"
                )
                # And it must have fired at least once — otherwise the test
                # isn't actually exercising the cooldown gate.
                assert actuations >= 1
        finally:
            engine.dispose()


@pytest.fixture(autouse=True)
def _clear_scheduler_jobs():
    """Reset the module-level scheduler between tests to avoid bleed-through."""
    yield
    for job in list(bg_scheduler.get_jobs()):
        bg_scheduler.remove_job(job.id)

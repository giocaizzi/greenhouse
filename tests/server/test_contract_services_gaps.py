"""Characterization: service branches left unpinned before the WP4 restructuring.

The WP4 coverage precondition (refactor plan §0.4 G.1) found lines/branches that no test
reached in ``services/{health,forecast,maintenance,insights,health_monitor,anomaly,efficacy,
charts}.py``, and the pump-watcher M-pre mutation run (§0.5) left surviving mutants in
``PumpWatcherService.watch`` / ``_handle_trip``. Each test below pins **current** behavior of one
of those spots, exactly, under a frozen clock with no network (plant care / weather / learner are
in-module fakes). Odd behavior is pinned as ``…_current_behavior``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from fake_devices import FakeIrrigatorAdapter
from golden import FROZEN_TS
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.models import ActivityEvent, Alert, IrrigationEvent
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.schemas import CareInsight, EfficacyItemResponse, EfficacyListResponse, OverlayDataset
from greenhouse_server.services import forecast as forecast_module
from greenhouse_server.services.anomaly import SensorAnomalyService
from greenhouse_server.services.charts import build_overlay_payload
from greenhouse_server.services.efficacy import score_cluster
from greenhouse_server.services.forecast import ForecastService
from greenhouse_server.services.health import PlantHealthService
from greenhouse_server.services.health_monitor import DeviceHealthMonitor
from greenhouse_server.services.insights import InsightsService
from greenhouse_server.services.maintenance import collect_maintenance_alerts
from greenhouse_server.services.pump_watcher import PumpWatcherService

PUMP_LOGGER = "greenhouse_server.services.pump_watcher"


class CarePlantDB:
    """Plant-DB stand-in: every plant gets the same care dict."""

    def __init__(self, care: dict) -> None:
        self.care = care
        self.calls: list[tuple] = []

    def get_care_data(self, species=None, category=None) -> dict:
        self.calls.append((species, category))
        return dict(self.care)


@pytest.fixture
def repo(tmp_db, frozen_clock):
    return tmp_db


def _cluster_with_sensor(repo, *, plant: bool = True, environment: str = "indoor", n_sensors: int = 1):
    cid = repo.add_cluster("Gap Cluster", environment=environment)
    pid = repo.add_plant(cluster_id=cid, species="Gapus plantus", category="tropical") if plant else None
    sids = [
        repo.add_sensor(
            cluster_id=cid,
            tuya_device_id=f"gap-sensor-{i}",
            name=f"Gap Sensor {i}",
            sensor_type="soil_moisture",
            config={},
            plant_id=pid,
            assignment_started_at=FROZEN_TS - 30 * 86400,
        )
        for i in range(n_sensors)
    ]
    repo.session.commit()
    return cid, pid, sids


# ── health.compute_score (T4.3) ────────────────────────────────────────────────


def test_health_score_of_unknown_plant_is_the_empty_score(repo):
    svc = PlantHealthService(repo, CarePlantDB({}))
    assert svc.compute_score(999) == {
        "score": None,
        "soil_in_band_pct": None,
        "temp_in_band_pct": None,
        "humidity_in_band_pct": None,
        "efficiency": None,
        "sample_count": 0,
    }


def test_health_score_without_soil_values_scores_temperature_and_humidity_only(repo):
    _, pid, (sid,) = _cluster_with_sensor(repo)
    repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 7200, temperature=20.0, env_humidity=60.0)
    repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 3600, temperature=30.0, env_humidity=80.0)
    repo.session.commit()
    care = {
        "soil_moisture_target": "40-60",
        "ideal_temp_min_c": 18,
        "ideal_temp_max_c": 25,
        "ideal_humidity_min": 50,
        "ideal_humidity_max": 70,
    }
    assert PlantHealthService(repo, CarePlantDB(care)).compute_score(pid) == {
        "score": 50.0,
        "soil_in_band_pct": None,
        "temp_in_band_pct": 50.0,
        "humidity_in_band_pct": 50.0,
        "efficiency": None,
        "sample_count": 2,
    }


def test_health_score_ignores_temperature_and_humidity_without_both_care_bounds(repo):
    _, pid, (sid,) = _cluster_with_sensor(repo)
    repo.add_sensor_reading(
        sensor_id=sid, timestamp=FROZEN_TS - 3600, soil_moisture=50.0, temperature=40.0, env_humidity=5.0
    )
    repo.session.commit()
    care = {"soil_moisture_target": "40-60", "ideal_temp_min_c": 18, "ideal_humidity_max": 70}
    assert PlantHealthService(repo, CarePlantDB(care)).compute_score(pid) == {
        "score": 100.0,
        "soil_in_band_pct": 100.0,
        "temp_in_band_pct": None,
        "humidity_in_band_pct": None,
        "efficiency": None,
        "sample_count": 1,
    }


# ── forecast.predict_next_irrigation (T4.4) ─────────────────────────────────────


class FakeLearner:
    """Replaces IrrigationLearner inside services.forecast: one canned drainage rate per sensor."""

    drainage: float | None = None

    def __init__(self, repo, plant_db) -> None:
        pass

    def get_plant_profile(self, sensor):
        if self.drainage is None:
            return None
        return SimpleNamespace(avg_drainage_per_hour=self.drainage)


class FakeForecastWeather:
    def __init__(self, forecast: dict | None) -> None:
        self.forecast = forecast
        self.calls: list[int] = []

    def get_forecast(self, hours: int = 6):
        self.calls.append(hours)
        return self.forecast


@pytest.fixture
def learner(monkeypatch):
    monkeypatch.setattr(forecast_module, "IrrigationLearner", FakeLearner)
    monkeypatch.setattr(FakeLearner, "drainage", None)
    return FakeLearner


def _forecast_world(repo, *, n_sensors: int = 1, environment: str = "indoor"):
    cid, _, sids = _cluster_with_sensor(repo, n_sensors=n_sensors, environment=environment)
    for sid in sids:
        repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=60.0)
    repo.session.commit()
    return cid


def test_forecast_non_negative_learned_drainage_falls_back_to_minus_two(repo, learner):
    learner.drainage = 0.5
    cid = _forecast_world(repo)
    result = ForecastService(repo, CarePlantDB({"soil_moisture_target": "40-60"})).predict_next_irrigation(cid)
    assert result.model_dump() == {
        "cluster_id": cid,
        "next_predicted_at": FROZEN_TS + 36000,
        "hours_until_next": 10.0,
        "projected_min_moisture": 60.0,
        "method": "drainage_slope",
        "confidence": 0.4,
        "explanation": "Gapus plantus will hit its 40% min in ~10.0h based on -2.0%/h drainage.",
        "weather_skip": False,
        "weather_reason": None,
        "precipitation_next_6h_mm": None,
    }


def test_forecast_three_profiled_sensors_give_high_confidence(repo, learner):
    learner.drainage = -4.0
    cid = _forecast_world(repo, n_sensors=3)
    result = ForecastService(repo, CarePlantDB({"soil_moisture_target": "40-60"})).predict_next_irrigation(cid)
    assert (result.confidence, result.method, result.hours_until_next) == (0.7, "drainage_slope", 5.0)


@pytest.mark.parametrize(
    ("forecast", "skip", "reason", "precip"),
    [
        ({"precipitation_mm": 3.5}, True, "rain forecast (3.5mm in next 6h)", 3.5),
        ({"precipitation_mm": 2.0}, False, None, 2.0),
        ({"precipitation_mm": None}, False, None, 0.0),
        ({}, False, None, 0.0),
    ],
)
def test_forecast_outdoor_rain_outlook(repo, learner, forecast, skip, reason, precip):
    cid = _forecast_world(repo, environment="outdoor")
    weather = FakeForecastWeather(forecast)
    svc = ForecastService(repo, CarePlantDB({"soil_moisture_target": "40-60"}), weather_client=weather)
    result = svc.predict_next_irrigation(cid)
    assert (result.weather_skip, result.weather_reason, result.precipitation_next_6h_mm) == (skip, reason, precip)
    assert weather.calls == [6]
    assert (result.method, result.confidence, result.hours_until_next) == ("fallback_constant", 0.2, 10.0)


def test_forecast_indoor_cluster_never_asks_the_weather(repo, learner):
    cid = _forecast_world(repo)
    weather = FakeForecastWeather({"precipitation_mm": 9.0})
    result = ForecastService(repo, CarePlantDB({}), weather_client=weather).predict_next_irrigation(cid)
    assert weather.calls == []
    assert (result.weather_skip, result.precipitation_next_6h_mm) == (False, None)


# ── maintenance.collect_maintenance_alerts (T4.5) ──────────────────────────────


def _lux_readings(repo, sid, lux: int) -> None:
    for k in range(3):
        repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600 * (k + 1), light=lux)
    repo.session.commit()


def test_maintenance_raises_low_light_for_a_dim_plant(repo):
    cid, _, (sid,) = _cluster_with_sensor(repo)
    _lux_readings(repo, sid, 100)
    plant_db = CarePlantDB({"ideal_light_lux_min": 5000})
    assert collect_maintenance_alerts(repo, cid, plant_db) == [
        {"severity": "warning", "type": "low_light", "message": "Gap Sensor 0: avg 100 lux (seasonal min 4250)"}
    ]
    assert plant_db.calls == [("Gapus plantus", "tropical")]


def test_maintenance_light_check_needs_a_plant(repo):
    cid, _, (sid,) = _cluster_with_sensor(repo, plant=False)
    _lux_readings(repo, sid, 100)
    plant_db = CarePlantDB({"ideal_light_lux_min": 5000})
    assert collect_maintenance_alerts(repo, cid, plant_db) == []
    assert plant_db.calls == []


# ── insights.cluster_insights (T4.9) ───────────────────────────────────────────


def test_insights_keep_only_the_first_alert_of_each_type(repo):
    cid, _, _ = _cluster_with_sensor(repo, n_sensors=2)  # two sensors, no readings → two stale_data alerts
    result = InsightsService(repo, PlantDatabase()).cluster_insights(cid)
    assert result is not None
    assert result.insights == [
        CareInsight(
            code="stale_data",
            severity="warning",
            title="Stale sensor data",
            message="Gap Sensor 0: no recent data (last: never)",
            suggestion="Check Wi-Fi connection and sensor battery.",
        )
    ]


# ── health_monitor.backfill_from_history (T4.11) ───────────────────────────────


def test_backfill_does_not_re_raise_an_open_sensor_fault(repo):
    _, _, (sid,) = _cluster_with_sensor(repo)
    for k in range(5):
        repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600 * (k + 1), water_warning=True)
    repo.upsert_alert(
        dedup_key=f"health:sensor:{sid}:sensor_fault",
        source="health",
        code="sensor_fault",
        title="pre-existing title",
        message="pre-existing message",
        severity="warning",
        entity_type="sensor",
        entity_id=sid,
    )
    repo.session.commit()
    DeviceHealthMonitor(repo, DeviceRegistry(), clock=lambda: FROZEN_TS).backfill_from_history()
    rows = [(a.dedup_key, a.title, a.message, a.status) for a in repo.session.query(Alert).all()]
    assert rows == [(f"health:sensor:{sid}:sensor_fault", "pre-existing title", "pre-existing message", "open")]


# ── anomaly.SensorAnomalyService.scan (T4.14) ──────────────────────────────────


@pytest.mark.parametrize("soil_values", [0, 5])
def test_anomaly_skips_drift_with_too_few_soil_values_but_still_checks_staleness(repo, soil_values):
    cid, _, (sid,) = _cluster_with_sensor(repo)
    latest = FROZEN_TS - 3600
    for k in range(12):  # every 10 min; the newest is an hour old → stale
        soil = 50.0 if k < soil_values else None
        repo.add_sensor_reading(sensor_id=sid, timestamp=latest - 600 * k, soil_moisture=soil, temperature=20.0)
    repo.session.commit()
    alerts = SensorAnomalyService(repo).scan()
    assert [(a.code, a.message) for a in alerts] == [
        ("sensor_stale", "Gap Sensor 0: no reading for 60 min (expected every 10 min)")
    ]
    assert repo.session.query(Alert).count() == 1


# ── efficacy.score_cluster (T4.15) ─────────────────────────────────────────────


def test_efficacy_of_a_cluster_without_irrigator_is_empty(repo):
    cid, _, _ = _cluster_with_sensor(repo)
    assert score_cluster(repo, cid) == EfficacyListResponse(cluster_id=cid, days=14, items=[])


def test_efficacy_skips_start_events_without_a_positive_duration(repo):
    cid, _, _ = _cluster_with_sensor(repo)
    iid = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="gap-pump", name="Gap Pump", irrigator_type="tuya_cloud", config={}
    )
    repo.add_irrigation_event(iid, "start", "auto", duration_minutes=0, timestamp=FROZEN_TS - 7200)
    repo.add_irrigation_event(iid, "start", "auto", duration_minutes=None, timestamp=FROZEN_TS - 5400)
    good = repo.add_irrigation_event(iid, "start", "auto", duration_minutes=5, timestamp=FROZEN_TS - 3600)
    repo.session.commit()
    assert score_cluster(repo, cid).items == [
        EfficacyItemResponse(
            event_id=good,
            timestamp=FROZEN_TS - 3600,
            irrigator_name="Gap Pump",
            duration_minutes=5,
            before_pct=None,
            after_pct=None,
            score=None,
        )
    ]


# ── charts.build_overlay_payload (T4.17) ───────────────────────────────────────


def test_overlay_buckets_skip_missing_metric_values(repo):
    cid, _, (sid,) = _cluster_with_sensor(repo)
    repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, env_humidity=55.0)
    repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 1200, soil_moisture=40.0, env_humidity=65.0)
    repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 1800, light=5000)
    repo.session.commit()
    payload = build_overlay_payload(repo, cid, 24)
    assert payload is not None
    assert payload.datasets == [
        OverlayDataset(metric="soil", points=[(FROZEN_TS - 1200, 40.0)]),
        OverlayDataset(metric="humidity", points=[(FROZEN_TS - 1200, 65.0), (FROZEN_TS - 600, 55.0)]),
        OverlayDataset(metric="light", points=[(FROZEN_TS - 1800, 50.0)], original_max=10000.0),
    ]


# ── pump_watcher: M-pre survivors (T4.10 / T4.12) ──────────────────────────────


@dataclass
class ScriptedAdapter(FakeIrrigatorAdapter):
    """Irrigator whose read_health replays a script of states, and whose stop may raise."""

    script: list[DeviceHealthState] = field(default_factory=list)
    stop_error: Exception | None = None

    def read_health(self, irrigator):
        self.calls.append(("read_health", irrigator.id))
        return self.script.pop(0)

    def stop(self, irrigator):
        self.calls.append(("stop", irrigator.id))
        if self.stop_error is not None:
            raise self.stop_error
        return self.stop_result


OK = DeviceHealthState(observed_at=FROZEN_TS)
TIMEOUT = DeviceHealthState(observed_at=FROZEN_TS, raw={"error": "timeout"})
OFFLINE = DeviceHealthState(observed_at=FROZEN_TS, offline=True)
DRY = DeviceHealthState(observed_at=FROZEN_TS, alarms=frozenset({HealthAlarm.NO_WATER}), raw={"alarm_raw": 1})


@pytest.fixture
def pump(repo):
    cid = repo.add_cluster("Pump Gap Cluster")
    iid = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="pg", name="Gap Pump", irrigator_type="tuya_cloud", config={}
    )
    repo.session.commit()
    adapter = ScriptedAdapter()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda: adapter)
    return SimpleNamespace(repo=repo, irrigator=repo.get_irrigator(iid), adapter=adapter, registry=registry)


def _ticks(values: list[float]) -> Iterator[float]:
    return iter(values)


def _watcher(pump, clock_values: list[float], **kwargs) -> PumpWatcherService:
    ticks = _ticks(clock_values)
    return PumpWatcherService(pump.repo, pump.registry, clock=lambda: next(ticks), sleep=lambda s: None, **kwargs)


def test_watch_read_error_counts_as_a_failure_and_is_logged(pump, caplog):
    pump.adapter.script = [TIMEOUT]
    watcher = _watcher(pump, [0.0, 0.0, 0.0, 0.5], max_read_failures=1)
    with caplog.at_level(logging.WARNING, logger=PUMP_LOGGER):
        result = watcher.watch(pump.irrigator, 60)
    assert result == {"outcome": "abandoned", "polls": 1, "read_failures": 1, "alarm_raw": None, "elapsed_seconds": 0.5}
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        (
            "WARNING",
            f"Pump watcher abandoning irrigator {pump.irrigator.id} after 1 read failures (last: timeout) "
            "— irrigation continues unprotected",
        )
    ]


def test_watch_offline_failure_message_is_device_offline(pump, caplog):
    pump.adapter.script = [OFFLINE, OFFLINE]
    watcher = _watcher(pump, [0.0, 0.0, 0.0, 1.0, 1.5], max_read_failures=2)
    with caplog.at_level(logging.WARNING, logger=PUMP_LOGGER):
        result = watcher.watch(pump.irrigator, 60)
    assert result == {"outcome": "abandoned", "polls": 2, "read_failures": 2, "alarm_raw": None, "elapsed_seconds": 1.5}
    assert [r.getMessage() for r in caplog.records] == [
        f"Pump watcher abandoning irrigator {pump.irrigator.id} after 2 read failures (last: device offline) "
        "— irrigation continues unprotected"
    ]


def test_watch_good_read_resets_the_failure_count(pump):
    pump.adapter.script = [TIMEOUT, OK, TIMEOUT, OK]
    # deadline, warm-up, then one "now" per poll; the fifth "now" is past the deadline
    watcher = _watcher(pump, [0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 60.0], max_read_failures=2)
    assert watcher.watch(pump.irrigator, 60) == {
        "outcome": "completed",
        "polls": 4,
        "read_failures": 0,
        "alarm_raw": None,
        "elapsed_seconds": 60.0,
    }


def test_watch_completed_reports_zero_read_failures_even_after_a_failed_read(pump):
    pump.adapter.script = [TIMEOUT]
    watcher = _watcher(pump, [0.0, 0.0, 1.0, 60.0], max_read_failures=5)
    assert watcher.watch(pump.irrigator, 60) == {
        "outcome": "completed",
        "polls": 1,
        "read_failures": 0,
        "alarm_raw": None,
        "elapsed_seconds": 60.0,
    }


def test_watch_negative_duration_current_behavior_reports_negative_elapsed(pump):
    """Pins current behavior: a negative duration completes at once with elapsed = -duration offset."""
    watcher = _watcher(pump, [100.0, 100.0, 100.0])
    assert watcher.watch(pump.irrigator, -5) == {
        "outcome": "completed",
        "polls": 0,
        "read_failures": 0,
        "alarm_raw": None,
        "elapsed_seconds": -5.0,
    }
    assert pump.adapter.calls == []


def test_trip_with_a_raising_stop_records_the_failure(pump, caplog):
    pump.adapter.script = [DRY]
    pump.adapter.stop_error = RuntimeError("boom")
    watcher = _watcher(pump, [0.0, 0.0, 0.0, 0.0], warmup_seconds=0)
    with caplog.at_level(logging.ERROR, logger=PUMP_LOGGER):
        result = watcher.watch(pump.irrigator, 60, started_at=FROZEN_TS - 42)
    assert result == {"outcome": "tripped", "polls": 1, "read_failures": 0, "alarm_raw": 1, "elapsed_seconds": 0.0}
    iid = pump.irrigator.id
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("ERROR", f"Pump watcher could not stop irrigator {iid}"),
        (
            "CRITICAL",
            f"Pump dry-run detected on irrigator {iid} (cluster {pump.irrigator.cluster_id}) after 1 polls: "
            "alarm_raw=1, stop_ok=False, stop_msg=adapter.stop raised: boom",
        ),
    ]
    event = pump.repo.session.query(IrrigationEvent).one()
    assert event.notes == "pump dry-run detected after ~42s (DP 105=1); stop_ok=False"
    activity = pump.repo.session.query(ActivityEvent).one()
    payload = activity.payload_json
    assert '"stop_ok": false' in payload
    assert '"stop_message": "adapter.stop raised: boom"' in payload


def test_trip_commit_failure_rolls_back(pump, monkeypatch, caplog):
    pump.adapter.script = [DRY]
    calls: list[str] = []

    def _commit():
        calls.append("commit")
        raise RuntimeError("db gone")

    monkeypatch.setattr(pump.repo.session, "commit", _commit)
    monkeypatch.setattr(pump.repo.session, "rollback", lambda: calls.append("rollback"))
    watcher = _watcher(pump, [0.0, 0.0, 0.0, 0.0], warmup_seconds=0)
    assert watcher.watch(pump.irrigator, 60, started_at=FROZEN_TS - 42)["outcome"] == "tripped"
    assert calls == ["commit", "rollback"]


def test_trip_rollback_failure_is_swallowed(pump, monkeypatch, caplog):
    pump.adapter.script = [DRY]

    def _fail():
        raise RuntimeError("db gone")

    monkeypatch.setattr(pump.repo.session, "commit", _fail)
    monkeypatch.setattr(pump.repo.session, "rollback", _fail)
    watcher = _watcher(pump, [0.0, 0.0, 0.0, 0.0], warmup_seconds=0)
    with caplog.at_level(logging.ERROR, logger=PUMP_LOGGER):
        assert watcher.watch(pump.irrigator, 60, started_at=FROZEN_TS - 42)["outcome"] == "tripped"
    assert [r.getMessage() for r in caplog.records][-1] == (
        f"Failed to commit pump dry-run side effects for irrigator {pump.irrigator.id}"
    )

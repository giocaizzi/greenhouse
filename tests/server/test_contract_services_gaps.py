"""Characterization: service branches left unpinned before the services were restructured.

A coverage run found lines/branches that no test
reached in ``services/{health,forecast,maintenance,insights,health_monitor,anomaly,efficacy,
charts}.py``, and a pump-watcher mutation run left surviving mutants in
``PumpWatcherService.watch`` / ``_handle_trip``. Each test below pins **current** behavior of one
of those spots, exactly, under a frozen clock with no network (plant care / weather / learner are
in-module fakes). Odd behavior is pinned as ``…_current_behavior``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, TypedDict, Unpack, cast

import pytest

from fake_devices import FakeIrrigatorAdapter
from golden import FROZEN_TS
from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.models import (
    ActivityEvent,
    ActivitySource,
    Alert,
    EntityType,
    EventAction,
    IrrigationEvent,
    TriggeredBy,
)
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

if TYPE_CHECKING:
    from collections.abc import Iterator

    from time_machine import Traveller

    from greenhouse_core.models import Irrigator, Sensor
    from greenhouse_core.repository import IrrigationRepository
    from greenhouse_server.services.weather import WeatherClient

PUMP_LOGGER = "greenhouse_server.services.pump_watcher"


class CarePlantDB:
    """Plant-DB stand-in: every plant gets the same care dict."""

    def __init__(self, care: dict[str, object]) -> None:
        """Serve ``care`` for every lookup and record the arguments."""
        self.care = care
        self.calls: list[tuple[str | None, str | None]] = []

    def get_care_data(self, species: str | None = None, category: str | None = None) -> dict[str, object]:
        """Record the lookup and return a copy of the canned care data."""
        self.calls.append((species, category))
        return dict(self.care)


def _care_db(care: dict[str, object]) -> PlantDatabase:
    """A :class:`CarePlantDB` typed as the plant DB it stands in for (it only serves ``get_care_data``)."""
    return cast("PlantDatabase", CarePlantDB(care))


@pytest.fixture
def repo(tmp_db: IrrigationRepository, frozen_clock: Traveller) -> IrrigationRepository:
    """The in-memory repository under a frozen clock."""
    del frozen_clock  # requested only for its side effect
    return tmp_db


def _cluster_with_sensor(
    repo: IrrigationRepository, *, plant: bool = True, environment: str = "indoor", n_sensors: int = 1
) -> tuple[int, int | None, list[int]]:
    cid = repo.add_cluster("Gap Cluster", environment=environment)
    pid = repo.add_plant(cluster_id=cid, species="Gapus plantus", category="tropical") if plant else None
    sids = [
        repo.add_sensor(
            cluster_id=cid,
            tuya_device_id=f"gap-sensor-{i}",
            name=f"Gap Sensor {i}",
            sensor_type="tuya.tr301z",
            config={},
            plant_id=pid,
            assignment_started_at=FROZEN_TS - 30 * 86400,
        )
        for i in range(n_sensors)
    ]
    repo.session.commit()
    return cid, pid, sids


# ── health.compute_score ──────────────────────────────────────────────────────────


def test_health_score_of_unknown_plant_is_the_empty_score(repo: IrrigationRepository) -> None:
    """Health score of unknown plant is the empty score."""
    svc = PlantHealthService(repo, _care_db({}))
    assert svc.compute_score(999) == {
        "score": None,
        "soil_in_band_pct": None,
        "temp_in_band_pct": None,
        "humidity_in_band_pct": None,
        "efficiency": None,
        "sample_count": 0,
    }


def test_health_score_without_soil_values_scores_temperature_and_humidity_only(repo: IrrigationRepository) -> None:
    """Health score without soil values scores temperature and humidity only."""
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
    assert pid is not None
    assert PlantHealthService(repo, _care_db(care)).compute_score(pid) == {
        "score": 50.0,
        "soil_in_band_pct": None,
        "temp_in_band_pct": 50.0,
        "humidity_in_band_pct": 50.0,
        "efficiency": None,
        "sample_count": 2,
    }


def test_health_score_ignores_temperature_and_humidity_without_both_care_bounds(repo: IrrigationRepository) -> None:
    """Health score ignores temperature and humidity without both care bounds."""
    _, pid, (sid,) = _cluster_with_sensor(repo)
    repo.add_sensor_reading(
        sensor_id=sid, timestamp=FROZEN_TS - 3600, soil_moisture=50.0, temperature=40.0, env_humidity=5.0
    )
    repo.session.commit()
    care = {"soil_moisture_target": "40-60", "ideal_temp_min_c": 18, "ideal_humidity_max": 70}
    assert pid is not None
    assert PlantHealthService(repo, _care_db(care)).compute_score(pid) == {
        "score": 100.0,
        "soil_in_band_pct": 100.0,
        "temp_in_band_pct": None,
        "humidity_in_band_pct": None,
        "efficiency": None,
        "sample_count": 1,
    }


# ── forecast.predict_next_irrigation ──────────────────────────────────────────────


class FakeLearner:
    """Replaces IrrigationLearner inside services.forecast: one canned drainage rate per sensor."""

    drainage: float | None = None

    def __init__(self, repo: IrrigationRepository, plant_db: PlantDatabase) -> None:
        """Accept the learner's constructor arguments; the drainage is a class attribute."""

    def get_plant_profile(self, _sensor: Sensor) -> SimpleNamespace | None:
        """A profile carrying the configured drainage rate, or ``None`` when unset."""
        if self.drainage is None:
            return None
        return SimpleNamespace(avg_drainage_per_hour=self.drainage)


class FakeForecastWeather:
    """Weather stand-in serving a fixed forecast and recording the requested windows."""

    def __init__(self, forecast: dict[str, float | None] | None) -> None:
        """Serve ``forecast`` (``None`` = no forecast)."""
        self.forecast = forecast
        self.calls: list[int] = []

    def get_forecast(self, hours: int = 6) -> dict[str, float | None] | None:
        """Record the window and return the canned forecast."""
        self.calls.append(hours)
        return self.forecast


@pytest.fixture
def learner(monkeypatch: pytest.MonkeyPatch) -> type[FakeLearner]:
    """Swap the forecast service's learner for :class:`FakeLearner` (no drainage by default)."""
    monkeypatch.setattr(forecast_module, "IrrigationLearner", FakeLearner)
    monkeypatch.setattr(FakeLearner, "drainage", None)
    return FakeLearner


def _forecast_world(repo: IrrigationRepository, *, n_sensors: int = 1, environment: str = "indoor") -> int:
    cid, _, sids = _cluster_with_sensor(repo, n_sensors=n_sensors, environment=environment)
    for sid in sids:
        repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600, soil_moisture=60.0)
    repo.session.commit()
    return cid


def test_forecast_non_negative_learned_drainage_falls_back_to_minus_two(
    repo: IrrigationRepository, learner: type[FakeLearner]
) -> None:
    """Forecast non negative learned drainage falls back to minus two."""
    learner.drainage = 0.5
    cid = _forecast_world(repo)
    result = ForecastService(repo, _care_db({"soil_moisture_target": "40-60"})).predict_next_irrigation(cid)
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


def test_forecast_three_profiled_sensors_give_high_confidence(
    repo: IrrigationRepository, learner: type[FakeLearner]
) -> None:
    """Forecast three profiled sensors give high confidence."""
    learner.drainage = -4.0
    cid = _forecast_world(repo, n_sensors=3)
    result = ForecastService(repo, _care_db({"soil_moisture_target": "40-60"})).predict_next_irrigation(cid)
    assert (result.confidence, result.method, result.hours_until_next) == (0.7, "drainage_slope", 5.0)


@pytest.mark.usefixtures("learner")
@pytest.mark.parametrize(
    ("forecast", "skip", "reason", "precip"),
    [
        ({"precipitation_mm": 3.5}, True, "rain forecast (3.5mm in next 6h)", 3.5),
        ({"precipitation_mm": 2.0}, False, None, 2.0),
        ({"precipitation_mm": None}, False, None, 0.0),
        ({}, False, None, 0.0),
    ],
)
def test_forecast_outdoor_rain_outlook(
    repo: IrrigationRepository, forecast: dict[str, float | None], *, skip: bool, reason: str | None, precip: float
) -> None:
    """Forecast outdoor rain outlook."""
    cid = _forecast_world(repo, environment="outdoor")
    weather = FakeForecastWeather(forecast)
    svc = ForecastService(
        repo, _care_db({"soil_moisture_target": "40-60"}), weather_client=cast("WeatherClient", weather)
    )
    result = svc.predict_next_irrigation(cid)
    assert (result.weather_skip, result.weather_reason, result.precipitation_next_6h_mm) == (skip, reason, precip)
    assert weather.calls == [6]
    assert (result.method, result.confidence, result.hours_until_next) == ("fallback_constant", 0.2, 10.0)


@pytest.mark.usefixtures("learner")
def test_forecast_indoor_cluster_never_asks_the_weather(repo: IrrigationRepository) -> None:
    """Forecast indoor cluster never asks the weather."""
    cid = _forecast_world(repo)
    weather = FakeForecastWeather({"precipitation_mm": 9.0})
    result = ForecastService(repo, _care_db({}), weather_client=cast("WeatherClient", weather)).predict_next_irrigation(
        cid
    )
    assert weather.calls == []
    assert (result.weather_skip, result.precipitation_next_6h_mm) == (False, None)


# ── maintenance.collect_maintenance_alerts ────────────────────────────────────────


def _lux_readings(repo: IrrigationRepository, sid: int, lux: int) -> None:
    for k in range(3):
        repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600 * (k + 1), light=lux)
    repo.session.commit()


def test_maintenance_raises_low_light_for_a_dim_plant(repo: IrrigationRepository) -> None:
    """Maintenance raises low light for a dim plant."""
    cid, _, (sid,) = _cluster_with_sensor(repo)
    _lux_readings(repo, sid, 100)
    plant_db = CarePlantDB({"ideal_light_lux_min": 5000})
    assert collect_maintenance_alerts(repo, cid, cast("PlantDatabase", plant_db)) == [
        {"severity": "warning", "type": "low_light", "message": "Gap Sensor 0: avg 100 lux (seasonal min 4250)"}
    ]
    assert plant_db.calls == [("Gapus plantus", "tropical")]


def test_maintenance_light_check_needs_a_plant(repo: IrrigationRepository) -> None:
    """Maintenance light check needs a plant."""
    cid, _, (sid,) = _cluster_with_sensor(repo, plant=False)
    _lux_readings(repo, sid, 100)
    plant_db = CarePlantDB({"ideal_light_lux_min": 5000})
    assert collect_maintenance_alerts(repo, cid, cast("PlantDatabase", plant_db)) == []
    assert plant_db.calls == []


# ── insights.cluster_insights ─────────────────────────────────────────────────────


def test_insights_keep_only_the_first_alert_of_each_type(repo: IrrigationRepository) -> None:
    """Insights keep only the first alert of each type."""
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


# ── health_monitor.backfill_from_history ──────────────────────────────────────────


def test_backfill_resolves_legacy_open_sensor_fault_alerts(repo: IrrigationRepository) -> None:
    """B-10 follow-up: a sensor_fault alert left open from before the fix is resolved at startup, once."""
    _, _, (sid,) = _cluster_with_sensor(repo)
    repo.upsert_alert(
        dedup_key=f"health:sensor:{sid}:sensor_fault",
        source=ActivitySource.HEALTH,
        code="sensor_fault",
        title="Sensor Fault · Probe",
        message="legacy",
        severity="warning",
        entity_type=EntityType.SENSOR,
        entity_id=sid,
    )
    repo.session.commit()
    monitor = DeviceHealthMonitor(repo, DeviceRegistry(), clock=lambda: FROZEN_TS)
    monitor.backfill_from_history()  # no readings at all: the sweep still runs
    monitor.backfill_from_history()  # idempotent: nothing left to resolve, nothing raised
    rows = [(a.dedup_key, a.status) for a in repo.session.query(Alert).all()]
    assert rows == [(f"health:sensor:{sid}:sensor_fault", "resolved")]


def test_backfill_does_not_re_raise_an_open_low_battery(repo: IrrigationRepository) -> None:
    """Backfill does not re raise an open low battery."""
    _, _, (sid,) = _cluster_with_sensor(repo)
    for k in range(5):
        repo.add_sensor_reading(sensor_id=sid, timestamp=FROZEN_TS - 600 * (k + 1), battery_state="low")
    repo.upsert_alert(
        dedup_key=f"health:sensor:{sid}:low_battery",
        source=ActivitySource.HEALTH,
        code="low_battery",
        title="pre-existing title",
        message="pre-existing message",
        severity="warning",
        entity_type=EntityType.SENSOR,
        entity_id=sid,
    )
    repo.session.commit()
    DeviceHealthMonitor(repo, DeviceRegistry(), clock=lambda: FROZEN_TS).backfill_from_history()
    rows = [(a.dedup_key, a.title, a.message, a.status) for a in repo.session.query(Alert).all()]
    assert rows == [(f"health:sensor:{sid}:low_battery", "pre-existing title", "pre-existing message", "open")]


# ── anomaly.SensorAnomalyService.scan ─────────────────────────────────────────────


@pytest.mark.parametrize("soil_values", [0, 5])
def test_anomaly_skips_drift_with_too_few_soil_values_but_still_checks_staleness(
    repo: IrrigationRepository, soil_values: int
) -> None:
    """Anomaly skips drift with too few soil values but still checks staleness."""
    _cid, _, (sid,) = _cluster_with_sensor(repo)
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


# ── efficacy.score_cluster ────────────────────────────────────────────────────────


def test_efficacy_of_a_cluster_without_irrigator_is_empty(repo: IrrigationRepository) -> None:
    """Efficacy of a cluster without irrigator is empty."""
    cid, _, _ = _cluster_with_sensor(repo)
    assert score_cluster(repo, cid) == EfficacyListResponse(cluster_id=cid, days=14, items=[])


def test_efficacy_skips_start_events_without_a_positive_duration(repo: IrrigationRepository) -> None:
    """Efficacy skips start events without a positive duration."""
    cid, _, _ = _cluster_with_sensor(repo)
    iid = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="gap-pump", name="Gap Pump", irrigator_type="rainpoint.ik10pw", config={}
    )
    repo.add_irrigation_event(iid, EventAction.START, TriggeredBy.AUTO, duration_minutes=0, timestamp=FROZEN_TS - 7200)
    repo.add_irrigation_event(
        iid, EventAction.START, TriggeredBy.AUTO, duration_minutes=None, timestamp=FROZEN_TS - 5400
    )
    good = repo.add_irrigation_event(
        iid, EventAction.START, TriggeredBy.AUTO, duration_minutes=5, timestamp=FROZEN_TS - 3600
    )
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


# ── charts.build_overlay_payload ──────────────────────────────────────────────────


def test_overlay_buckets_skip_missing_metric_values(repo: IrrigationRepository) -> None:
    """Overlay buckets skip missing metric values."""
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


# ── pump_watcher: mutation survivors ──────────────────────────────────────────────


@dataclass
class ScriptedAdapter(FakeIrrigatorAdapter):
    """Irrigator whose read_health replays a script of states, and whose stop may raise."""

    script: list[DeviceHealthState] = field(default_factory=list)
    stop_error: Exception | None = None

    def read_health(self, irrigator: Irrigator) -> DeviceHealthState:
        """Record the read and return the next scripted health state."""
        self.calls.append(("read_health", irrigator.id))
        return self.script.pop(0)

    def stop(self, irrigator: Irrigator) -> tuple[bool, str]:
        """Record the stop; raise the configured error or return the configured result."""
        self.calls.append(("stop", irrigator.id))
        if self.stop_error is not None:
            raise self.stop_error
        return self.stop_result


OK = DeviceHealthState(observed_at=FROZEN_TS)
TIMEOUT = DeviceHealthState(observed_at=FROZEN_TS, raw={"error": "timeout"})
OFFLINE = DeviceHealthState(observed_at=FROZEN_TS, offline=True)
DRY = DeviceHealthState(observed_at=FROZEN_TS, alarms=frozenset({HealthAlarm.NO_WATER}), raw={"alarm_raw": 1})


@dataclass
class PumpWorld:
    """What the ``pump`` fixture wires up."""

    repo: IrrigationRepository
    irrigator: Irrigator
    adapter: ScriptedAdapter
    registry: DeviceRegistry


class WatcherKnobs(TypedDict, total=False):
    """The :class:`PumpWatcherService` options a test varies (the clock and sleep are scripted)."""

    max_read_failures: int
    warmup_seconds: float


@pytest.fixture
def pump(repo: IrrigationRepository) -> PumpWorld:
    """One irrigator driven by a :class:`ScriptedAdapter` through a real registry."""
    cid = repo.add_cluster("Pump Gap Cluster")
    iid = repo.add_irrigator(
        cluster_id=cid, tuya_device_id="pg", name="Gap Pump", irrigator_type="rainpoint.ik10pw", config={}
    )
    repo.session.commit()
    adapter = ScriptedAdapter()
    registry = DeviceRegistry()
    registry.register_irrigator("rainpoint.ik10pw", lambda: adapter)
    irrigator = repo.get_irrigator(iid)
    assert irrigator is not None
    return PumpWorld(repo=repo, irrigator=irrigator, adapter=adapter, registry=registry)


def _ticks(values: list[float]) -> Iterator[float]:
    return iter(values)


def _watcher(pump: PumpWorld, clock_values: list[float], **kwargs: Unpack[WatcherKnobs]) -> PumpWatcherService:
    ticks = _ticks(clock_values)
    return PumpWatcherService(pump.repo, pump.registry, clock=lambda: next(ticks), sleep=lambda _s: None, **kwargs)


def test_watch_read_error_counts_as_a_failure_and_is_logged(pump: PumpWorld, caplog: pytest.LogCaptureFixture) -> None:
    """Watch read error counts as a failure and is logged."""
    pump.adapter.script = [TIMEOUT]
    watcher = _watcher(pump, [0.0, 0.0, 0.0, 0.5], max_read_failures=1)
    with caplog.at_level(logging.WARNING, logger=PUMP_LOGGER):
        result = watcher.watch(pump.irrigator, 60)
    assert result == {"outcome": "abandoned", "polls": 1, "read_failures": 1, "alarm_raw": None, "elapsed_seconds": 0.5}
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        (
            "WARNING",
            (
                f"Pump watcher abandoning irrigator {pump.irrigator.id} after 1 read failures (last: timeout) "
                "— irrigation continues unprotected"
            ),
        )
    ]


def test_watch_offline_failure_message_is_device_offline(pump: PumpWorld, caplog: pytest.LogCaptureFixture) -> None:
    """Watch offline failure message is device offline."""
    pump.adapter.script = [OFFLINE, OFFLINE]
    watcher = _watcher(pump, [0.0, 0.0, 0.0, 1.0, 1.5], max_read_failures=2)
    with caplog.at_level(logging.WARNING, logger=PUMP_LOGGER):
        result = watcher.watch(pump.irrigator, 60)
    assert result == {"outcome": "abandoned", "polls": 2, "read_failures": 2, "alarm_raw": None, "elapsed_seconds": 1.5}
    assert [r.getMessage() for r in caplog.records] == [
        (
            f"Pump watcher abandoning irrigator {pump.irrigator.id} after 2 read failures (last: device offline) "
            "— irrigation continues unprotected"
        )
    ]


def test_watch_good_read_resets_the_failure_count(pump: PumpWorld) -> None:
    """Watch good read resets the failure count."""
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


def test_watch_completed_reports_zero_read_failures_even_after_a_failed_read(pump: PumpWorld) -> None:
    """Watch completed reports zero read failures even after a failed read."""
    pump.adapter.script = [TIMEOUT]
    watcher = _watcher(pump, [0.0, 0.0, 1.0, 60.0], max_read_failures=5)
    assert watcher.watch(pump.irrigator, 60) == {
        "outcome": "completed",
        "polls": 1,
        "read_failures": 0,
        "alarm_raw": None,
        "elapsed_seconds": 60.0,
    }


def test_watch_negative_duration_completes_at_once_with_zero_elapsed(pump: PumpWorld) -> None:
    """A negative duration is clamped to 0: the watch completes at once and reports 0 s elapsed."""
    watcher = _watcher(pump, [100.0, 100.0, 100.0])
    assert watcher.watch(pump.irrigator, -5) == {
        "outcome": "completed",
        "polls": 0,
        "read_failures": 0,
        "alarm_raw": None,
        "elapsed_seconds": 0.0,
    }
    assert pump.adapter.calls == []


def test_trip_with_a_raising_stop_records_the_failure(pump: PumpWorld, caplog: pytest.LogCaptureFixture) -> None:
    """Trip with a raising stop records the failure."""
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
            (
                f"Pump dry-run detected on irrigator {iid} (cluster {pump.irrigator.cluster_id}) after 1 polls: "
                "alarm_raw=1, stop_ok=False, stop_msg=adapter.stop raised: boom"
            ),
        ),
    ]
    event = pump.repo.session.query(IrrigationEvent).one()
    assert event.notes == "pump dry-run detected after ~42s (DP 105=1); stop_ok=False"
    activity = pump.repo.session.query(ActivityEvent).one()
    payload = activity.payload_json
    assert payload is not None
    assert '"stop_ok": false' in payload
    assert '"stop_message": "adapter.stop raised: boom"' in payload


def test_trip_commit_failure_rolls_back(pump: PumpWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trip commit failure rolls back."""
    pump.adapter.script = [DRY]
    calls: list[str] = []

    def _commit() -> None:
        calls.append("commit")
        msg = "db gone"
        raise RuntimeError(msg)

    monkeypatch.setattr(pump.repo.session, "commit", _commit)
    monkeypatch.setattr(pump.repo.session, "rollback", lambda: calls.append("rollback"))
    watcher = _watcher(pump, [0.0, 0.0, 0.0, 0.0], warmup_seconds=0)
    assert watcher.watch(pump.irrigator, 60, started_at=FROZEN_TS - 42)["outcome"] == "tripped"
    # S5: event, activity and alert each commit on their own, and each failed commit is rolled back.
    assert calls == ["commit", "rollback"] * 3


def test_trip_rollback_failure_is_swallowed(
    pump: PumpWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Trip rollback failure is swallowed."""
    pump.adapter.script = [DRY]

    def _fail() -> None:
        msg = "db gone"
        raise RuntimeError(msg)

    monkeypatch.setattr(pump.repo.session, "commit", _fail)
    monkeypatch.setattr(pump.repo.session, "rollback", _fail)
    watcher = _watcher(pump, [0.0, 0.0, 0.0, 0.0], warmup_seconds=0)
    with caplog.at_level(logging.WARNING, logger=PUMP_LOGGER):
        assert watcher.watch(pump.irrigator, 60, started_at=FROZEN_TS - 42)["outcome"] == "tripped"
    iid = pump.irrigator.id
    assert [(r.levelname, r.getMessage()) for r in caplog.records if r.levelname != "CRITICAL"] == [
        ("ERROR", f"Failed to log aborted irrigation event for irrigator {iid}"),
        ("WARNING", "Rollback after a failed pump dry-run record failed too"),
        ("ERROR", f"Failed to log the pump dry-run activity for irrigator {iid}"),
        ("WARNING", "Rollback after a failed pump dry-run record failed too"),
        ("ERROR", f"Failed to record dry-run state into health monitor for irrigator {iid}"),
        ("WARNING", "Rollback after a failed pump dry-run record failed too"),
    ]

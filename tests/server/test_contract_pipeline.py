"""Golden characterization of the irrigation pipeline (orchestration work package).

Pins *what the orchestration layer does today* for a seeded app: the JSON each
operation route answers, every DB row it writes (irrigation events, decision
logs incl. ``payload_json``, alerts, activity, freshly synced readings), every
call it makes on the fake irrigator/sensor adapters and the recording Tuya
gateway, and every push notification it attempts.

Hermetic: clock frozen at ``FROZEN_INSTANT`` (10:00 UTC — outside the
migration-seeded 00-05 quiet hours), weather offline unless a scenario installs
its own canned stub, no env leakage (``clean_env``), notifier replaced by a
recorder, scheduler stopped (so leak checks / pump watchers are *not*
scheduled — :func:`test_successful_auto_start_schedules_leak_check_and_watcher`
pins the running-scheduler side separately).

Contracts / invariants guarded here:

- full pipeline matrix → ``tests/golden/orchestration/pipeline/<scenario>.json``;
- I6 — every pipeline evaluation (each early return and the final decision)
  writes exactly one ``decision_logs`` row with the expected primary code,
  ``actuated`` flag and a ``payload_json`` that round-trips to
  :class:`IrrigationDecision`;
- I11 — an open leak alert holds ``/irrigate`` even with ``force=true``, while
  ``POST /irrigators/{id}/start`` (the escape hatch) still actuates;
- fixed bugs: B-8 (drift pair D5); B-6 (a device-health block rewrites the decision log);
  S1/B-1 (``dry_run_global`` never actuates); B-4 (effective per-day caps gate manual and
  automatic starts); B-5 (a re-opened alert notifies again).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, TypedDict, Unpack, cast

import pytest
import time_machine
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from golden import FROZEN_INSTANT, FROZEN_TS, assert_golden_json, install_offline_weather
from greenhouse_core.constants import LEAK_ALERT_CODE, LEAK_CHECK_SETTLE_SECONDS
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.logic.decision import IrrigationDecision
from greenhouse_core.models import ENTITY_SENSOR, SOURCE_LEAK, Base
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.deps import get_device_gateway, get_device_registry, get_weather_client
from greenhouse_server.scheduler import scheduler as bg_scheduler
from greenhouse_server.scheduler import start_scheduler, stop_scheduler
from greenhouse_server.services.health_monitor import DeviceHealthMonitor
from greenhouse_server.services.vacation import cluster_budgets, vacation_days
from server.conftest import _make_stubbed_app

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.engine import RowMapping

    from greenhouse_core.models import EventAction, TriggeredBy
    from server.fixtures import FakeDeviceWiring

HOUR = 3600
# Tables whose rows are append-only during a pipeline run: only rows created
# after the scenario's seed are goldened. Alerts are mutable (ack / resolve /
# occurrence_count), so the whole table is dumped.
APPEND_ONLY_TABLES = ("irrigation_events", "decision_logs", "activity_events", "sensor_readings")
MUTABLE_TABLES = ("alerts",)
JSON_COLUMNS = ("payload_json",)


# ── Recording fakes ─────────────────────────────────────────────────────────


class RecordingNotifier:
    """Stand-in for ``NtfyClient`` that records every attempted push."""

    def __init__(self) -> None:
        """Start with no pushes recorded."""
        self.sent: list[dict[str, object]] = []

    def notify_irrigation(self, **kwargs: object) -> bool:
        """Record an irrigation push and report it delivered."""
        self.sent.append({"kind": "irrigation", **kwargs})
        return True

    def notify_alert(self, **kwargs: object) -> bool:
        """Record an alert push and report it delivered."""
        self.sent.append({"kind": "alert", **kwargs})
        return True


class RecordingGateway:
    """Stand-in for ``DeviceGateway``: empty device logs, canned live reading, call log."""

    def __init__(self, live: dict[str, Any] | None = None) -> None:
        """Serve ``live`` (or a dry default) as the live reading and record every call."""
        self.live = live if live is not None else {"soil_moisture": 30.0, "temperature": 23.0}
        self.calls: list[list[object]] = []

    def get_device_logs(self, device_id: str, since_ms: int | None = None, **_kwargs: object) -> list[dict[str, Any]]:
        """Record the call; there are no device logs."""
        self.calls.append(["get_device_logs", device_id, since_ms])
        return []

    def group_logs_by_timestamp(self, logs: list[dict[str, Any]], _tolerance_ms: int = 5000) -> list[dict[str, Any]]:
        """Record how many logs were grouped; there are no groups."""
        self.calls.append(["group_logs_by_timestamp", len(logs)])
        return []

    def get_live_reading(self, device_id: str) -> dict[str, Any]:
        """Record the call and return a copy of the canned reading."""
        self.calls.append(["get_live_reading", device_id])
        return dict(self.live)


class RainyWeather:
    """Canned Open-Meteo answer: mild now, significant rain forecast (no network)."""

    def get_current(self) -> dict[str, float]:
        """A cool, humid current reading."""
        return {"temperature": 17.0, "feels_like": 16.5, "humidity": 80.0}

    def get_forecast(self, hours: int = 6) -> dict[str, float]:  # noqa: ARG002 — mirrors WeatherClient
        """A forecast with enough rain to skip an outdoor irrigation."""
        return {"precipitation_mm": 6.5}


# ── Harness ─────────────────────────────────────────────────────────────────


def _decode_json_columns(row: RowMapping) -> dict[str, Any]:
    """A DB row as a dict with its JSON text columns parsed (empty ones left as they are)."""
    record = dict(row)
    for col in JSON_COLUMNS:
        if record.get(col):
            record[col] = json.loads(record[col])
    return record


SEED_PLANT = {
    "species": "Monstera deliciosa",
    "category": "tropical",
    "water_needs": "medium",
    "ideal_temp_min": 18.0,
    "ideal_temp_max": 27.0,
    "ideal_humidity_min": 60.0,
    "ideal_humidity_max": 80.0,
}


@dataclass(frozen=True, kw_only=True)
class ClusterSpec:
    """What :meth:`Pipeline.seed_cluster` creates besides the cluster itself."""

    environment: str = "indoor"
    plant: bool = True
    sensor: bool = True
    irrigator: bool = True
    soil: float | None = 35.0
    temp: float = 24.0
    reading_ages: tuple[int, ...] = (0, 600, 1200)
    reservoir_l: float | None = None
    flow_rate_l_per_min: float | None = None
    config: dict[str, object] | None = None

    def irrigator_body(self, cid: int) -> dict[str, object]:
        """The irrigator create body; capacity fields only when set."""
        body: dict[str, object] = {
            "tuya_device_id": f"fake_irrigator_{cid:03d}",
            "name": f"Irrigator {cid}",
            "type": "rainpoint.ik10pw",
        }
        if self.reservoir_l is not None:
            body["reservoir_l"] = self.reservoir_l
        if self.flow_rate_l_per_min is not None:
            body["flow_rate_l_per_min"] = self.flow_rate_l_per_min
        return body


class ClusterOptions(TypedDict, total=False):
    """The keyword options :meth:`Pipeline.seed_cluster` accepts — the fields of :class:`ClusterSpec`."""

    environment: str
    plant: bool
    sensor: bool
    irrigator: bool
    soil: float | None
    temp: float
    reading_ages: tuple[int, ...]
    reservoir_l: float | None
    flow_rate_l_per_min: float | None
    config: dict[str, object] | None


class Pipeline:
    """One freshly built, seeded app plus the recorders a scenario inspects."""

    def __init__(self) -> None:
        """Build a stubbed app with offline weather, a recording notifier and fake devices."""
        self.app, self.engine = _make_stubbed_app(bypass_auth=True)
        install_offline_weather(self.app)
        self.notifier = RecordingNotifier()
        self.app.state.ntfy_notifier = self.notifier
        self.wiring: FakeDeviceWiring = self.app.state.fake_devices
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.gateway: RecordingGateway | None = None
        self.calls: list[dict[str, Any]] = []
        self._marks: dict[str, int] = {}

    def close(self) -> None:
        """Close the client and dispose of the engine."""
        self.client.close()
        self.engine.dispose()

    # seeding -----------------------------------------------------------------

    @contextmanager
    def repo(self) -> Iterator[IrrigationRepository]:
        """A repository on a fresh session, committed on success and always closed."""
        session = self.app.state.session_factory()
        try:
            yield IrrigationRepository(session)
            session.commit()
        finally:
            session.close()

    def api(self, method: str, path: str, body: dict[str, object] | None = None) -> dict[str, Any]:
        """Seeding call: must succeed; not recorded in the golden."""
        resp = self.client.request(method, path, json=body)
        assert resp.status_code < HTTPStatus.MULTIPLE_CHOICES, f"{method} {path} -> {resp.status_code} {resp.text}"
        payload: dict[str, Any] = resp.json()
        return payload

    def use_gateway(self, live: dict[str, Any] | None = None) -> RecordingGateway:
        """Install a recording gateway (with ``live`` readings) and return it."""
        self.gateway = RecordingGateway(live)
        self.app.dependency_overrides[get_device_gateway] = lambda: self.gateway
        return self.gateway

    def use_weather(self, stub: object) -> None:
        """Install ``stub`` as the app's weather client, for jobs and requests alike."""
        self.app.state.weather_client = stub
        self.app.dependency_overrides[get_weather_client] = lambda: stub

    def seed_cluster(self, name: str = "Golden Cluster", **options: Unpack[ClusterOptions]) -> dict[str, int]:
        """Create cluster (+plant, sensor, irrigator, config) via the API; readings via the repo.

        ``options`` are the keyword-only fields of :class:`ClusterSpec` (an unknown one is a TypeError).
        """
        spec = ClusterSpec(**options)
        cid = self.api("POST", "/api/v1/clusters", {"name": name, "environment": spec.environment})["id"]
        ids: dict[str, int] = {"cluster_id": cid}
        if spec.plant:
            ids["plant_id"] = self.api("POST", f"/api/v1/clusters/{cid}/plants", SEED_PLANT)["id"]
        if spec.sensor:
            ids["sensor_id"] = self.api(
                "POST",
                f"/api/v1/clusters/{cid}/sensors",
                {
                    "tuya_device_id": f"fake_sensor_{cid:03d}",
                    "name": f"Sensor {cid}",
                    "type": "tuya.tr301z",
                    "plant_id": ids.get("plant_id"),
                },
            )["id"]
        if spec.irrigator:
            ids["irrigator_id"] = self.api("POST", f"/api/v1/clusters/{cid}/irrigator", spec.irrigator_body(cid))["id"]
        self.api(
            "PUT",
            f"/api/v1/clusters/{cid}/config",
            spec.config or {"mode": "smart", "duration_minutes": 2, "interval_hours": 12, "auto_run": True},
        )
        if spec.sensor and spec.soil is not None:
            with self.repo() as repo:
                for age in spec.reading_ages:
                    repo.add_sensor_reading(
                        sensor_id=ids["sensor_id"],
                        timestamp=FROZEN_TS - age,
                        soil_moisture=spec.soil,
                        temperature=spec.temp,
                    )
        return ids

    def add_event(self, irrigator_id: int, *, age: int, action: str = "start", triggered_by: str = "auto") -> None:
        """Seed one irrigation event ``age`` seconds before the frozen instant.

        ``action`` / ``triggered_by`` are raw column values: scenarios seed legacy ones (``off``,
        ``schedule_updated``) that the enums no longer list, hence the casts.
        """
        with self.repo() as repo:
            repo.add_irrigation_event(
                irrigator_id=irrigator_id,
                action=cast("EventAction", action),
                duration_minutes=2,
                triggered_by=cast("TriggeredBy", triggered_by),
                notes="seeded",
                timestamp=FROZEN_TS - age,
            )

    def raise_leak(self, cluster_id: int, sensor_id: int, *, age: int = 0) -> int:
        """Open a leak / stuck-valve alert for ``cluster_id`` ``age`` seconds ago; return its id."""
        with self.repo() as repo:
            alert = repo.upsert_alert(
                dedup_key=f"leak::{LEAK_ALERT_CODE}::{cluster_id}::sensor{sensor_id}",
                source=SOURCE_LEAK,
                code=LEAK_ALERT_CODE,
                title="Possible leak or stuck valve detected",
                message=f"Sensor {cluster_id}: soil moisture still rising after irrigation (latest=78.0%)",
                severity="critical",
                entity_type=ENTITY_SENSOR,
                entity_id=sensor_id,
                cluster_id=cluster_id,
                seen_at=FROZEN_TS - age,
            )
            return alert.id

    # observation ---------------------------------------------------------------

    def mark(self) -> None:
        """Start observing: remember table high-water marks, forget seed-time calls."""
        with self.app.state.session_factory() as session:
            for name in APPEND_ONLY_TABLES:
                table = Base.metadata.tables[name]
                self._marks[name] = session.scalar(select(func.coalesce(func.max(table.c.id), 0)))
        self.wiring.irrigator.calls.clear()
        self.wiring.sensor.calls.clear()
        self.notifier.sent.clear()
        if self.gateway is not None:
            self.gateway.calls.clear()
        self.calls.clear()

    def call(self, method: str, path: str, body: dict[str, object] | None = None) -> dict[str, Any]:
        """Observed call: request, status and JSON body go into the golden."""
        resp = self.client.request(method, path, json=body)
        entry = {"request": f"{method} {path}", "body": body, "status": resp.status_code, "response": resp.json()}
        self.calls.append(entry)
        return entry

    def db_rows(self) -> dict[str, list[dict[str, Any]]]:
        """Rows of every tracked table (only those written after ``mark`` for marked tables)."""
        out: dict[str, list[dict[str, Any]]] = {}
        with self.app.state.session_factory() as session:
            for name in APPEND_ONLY_TABLES + MUTABLE_TABLES:
                table = Base.metadata.tables[name]
                stmt = select(table).order_by(table.c.id)
                if name in self._marks:
                    stmt = stmt.where(table.c.id > self._marks[name])
                out[name] = [_decode_json_columns(row) for row in session.execute(stmt).mappings()]
        return out

    def observed(self) -> dict[str, Any]:
        """Everything a golden pins: calls, DB writes, device and gateway calls, notifications."""
        return {
            "calls": self.calls,
            "db_writes": self.db_rows(),
            "irrigator_adapter_calls": [list(c) for c in self.wiring.irrigator.calls],
            "sensor_adapter_calls": [list(c) for c in self.wiring.sensor.calls],
            "gateway_calls": self.gateway.calls if self.gateway is not None else None,
            "notifications": self.notifier.sent,
        }


@pytest.fixture
def pipeline(clean_env: Path, frozen_clock: time_machine.Traveller) -> Iterator[Pipeline]:
    """A fresh pipeline harness with a clean environment and a frozen clock."""
    del clean_env, frozen_clock  # requested only for their side effects
    p = Pipeline()
    yield p
    p.close()


def _irrigate(p: Pipeline, cid: int = 1, **body: object) -> dict[str, Any]:
    return p.call("POST", f"/api/v1/clusters/{cid}/irrigate", body)


# ── Scenario matrix ─────────────────────────────────────────────────────────
#
# Each scenario seeds a fresh app, calls ``p.mark()`` and then makes the
# observed calls. Every scenario starts from the same base (one indoor cluster:
# Monstera, soil sensor, ``rainpoint.ik10pw`` irrigator, smart/2 min/12 h/auto_run)
# unless it says otherwise.


def sc_dry_irrigates(p: Pipeline) -> None:
    """Scenario: dry irrigates."""
    p.seed_cluster(soil=35.0)
    p.mark()
    _irrigate(p)


def sc_very_dry_critical_stress(p: Pipeline) -> None:
    """Scenario: very dry critical stress."""
    p.seed_cluster(soil=8.0)
    p.mark()
    _irrigate(p)


def sc_wet_skips(p: Pipeline) -> None:
    """Scenario: wet skips."""
    p.seed_cluster(soil=88.0)
    p.mark()
    _irrigate(p)


def sc_adequate_skips(p: Pipeline) -> None:
    """Scenario: adequate skips."""
    p.seed_cluster(soil=55.0)
    p.mark()
    _irrigate(p)


def sc_no_readings_fallback(p: Pipeline) -> None:
    """Scenario: no readings fallback."""
    p.seed_cluster(soil=None)
    p.mark()
    _irrigate(p)


def sc_outdoor_dry_weather_offline(p: Pipeline) -> None:
    """Scenario: outdoor dry weather offline."""
    p.seed_cluster(environment="outdoor", soil=35.0)
    p.mark()
    _irrigate(p)


def sc_outdoor_rain_forecast(p: Pipeline) -> None:
    """Scenario: outdoor rain forecast."""
    p.seed_cluster(environment="outdoor", soil=35.0)
    p.use_weather(RainyWeather())
    p.mark()
    _irrigate(p)


def sc_indoor_no_sync_weather_available(p: Pipeline) -> None:
    """Scenario: indoor no sync weather available."""
    p.seed_cluster(soil=35.0)
    p.use_weather(RainyWeather())
    p.mark()
    _irrigate(p, no_sync=True)


def sc_temp_override(p: Pipeline) -> None:
    """Scenario: temp override."""
    p.seed_cluster(soil=35.0)
    p.mark()
    _irrigate(p, temp_override=31.5)


def sc_no_sync_offline(p: Pipeline) -> None:
    """Scenario: no sync offline."""
    p.seed_cluster(soil=35.0)
    p.mark()
    _irrigate(p, no_sync=True)


def sc_cooldown_active(p: Pipeline) -> None:
    """Scenario: cooldown active."""
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=2 * HOUR)
    p.mark()
    _irrigate(p)


def sc_cooldown_boundary_inside(p: Pipeline) -> None:
    """Scenario: cooldown boundary inside."""
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=6 * HOUR - 60)
    p.mark()
    _irrigate(p)


def sc_cooldown_boundary_outside(p: Pipeline) -> None:
    """Scenario: cooldown boundary outside."""
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=6 * HOUR + 60)
    p.mark()
    _irrigate(p)


def sc_cooldown_counts_manual_start(p: Pipeline) -> None:
    """Scenario: cooldown counts manual start."""
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=HOUR, triggered_by="manual")
    p.mark()
    _irrigate(p)


def sc_cooldown_ignores_non_start(p: Pipeline) -> None:
    """Scenario: cooldown ignores non start."""
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=HOUR, action="schedule_updated")
    p.add_event(ids["irrigator_id"], age=HOUR - 60, action="off", triggered_by="manual")
    p.mark()
    _irrigate(p)


def sc_quiet_hours(p: Pipeline) -> None:
    """Scenario: quiet hours."""
    p.seed_cluster(soil=35.0)
    p.api("PUT", "/api/v1/config/global", {"quiet_start_hour": 9, "quiet_end_hour": 11})
    p.mark()
    _irrigate(p)


def sc_quiet_hours_force(p: Pipeline) -> None:
    """Scenario: quiet hours force."""
    p.seed_cluster(soil=35.0)
    p.api("PUT", "/api/v1/config/global", {"quiet_start_hour": 9, "quiet_end_hour": 11})
    p.mark()
    _irrigate(p, force=True)


def sc_quiet_hours_cluster_opt_out(p: Pipeline) -> None:
    """Scenario: quiet hours cluster opt out."""
    p.seed_cluster(soil=35.0)
    p.api("PUT", "/api/v1/config/global", {"quiet_start_hour": 9, "quiet_end_hour": 11})
    p.api("PUT", "/api/v1/clusters/1/config", {"quiet_start_hour": 0, "quiet_end_hour": 0})
    p.mark()
    _irrigate(p)


def sc_leak_hold_open(p: Pipeline) -> None:
    """Scenario: leak hold open."""
    ids = p.seed_cluster(soil=35.0)
    p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.mark()
    _irrigate(p)


def sc_leak_hold_acknowledged(p: Pipeline) -> None:
    """Scenario: leak hold acknowledged."""
    ids = p.seed_cluster(soil=35.0)
    alert_id = p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.api("POST", f"/api/v1/alerts/{alert_id}/acknowledge")
    p.mark()
    _irrigate(p)


def sc_leak_hold_resolved(p: Pipeline) -> None:
    """Scenario: leak hold resolved."""
    ids = p.seed_cluster(soil=35.0)
    alert_id = p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.api("POST", f"/api/v1/alerts/{alert_id}/resolve")
    p.mark()
    _irrigate(p)


def sc_leak_hold_force(p: Pipeline) -> None:
    """Scenario: leak hold force."""
    ids = p.seed_cluster(soil=35.0)
    p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.mark()
    _irrigate(p, force=True)


def sc_leak_hold_expired(p: Pipeline) -> None:
    """Scenario: leak hold expired."""
    ids = p.seed_cluster(soil=35.0)
    p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=25 * HOUR)
    p.mark()
    _irrigate(p)


def sc_leak_hold_beats_cooldown(p: Pipeline) -> None:
    """Scenario: leak hold beats cooldown."""
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=HOUR)
    p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.mark()
    _irrigate(p)


def sc_leak_escape_hatch_manual_start(p: Pipeline) -> None:
    """Scenario: leak escape hatch manual start."""
    ids = p.seed_cluster(soil=35.0)
    p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.mark()
    _irrigate(p, force=True)
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})


def sc_vacation_without_capacity(p: Pipeline) -> None:
    """Scenario: vacation without capacity."""
    p.seed_cluster(soil=35.0)
    p.api("POST", "/api/v1/vacation", {"starts_at": FROZEN_TS - 86400, "ends_at": FROZEN_TS + 3 * 86400})
    p.mark()
    _irrigate(p)


def sc_vacation_rationing(p: Pipeline) -> None:
    # 6 L * 0.95 usable over a 4-day window → 1.425 L/day → 1 min at 1 L/min (< the 2 min asked).
    """Scenario: vacation rationing."""
    p.seed_cluster(soil=35.0, reservoir_l=6.0, flow_rate_l_per_min=1.0)
    p.api("POST", "/api/v1/vacation", {"starts_at": FROZEN_TS - 3600, "ends_at": FROZEN_TS + 4 * 86400 - 3600})
    p.mark()
    _irrigate(p)


def sc_vacation_budget_exhausted(p: Pipeline) -> None:
    """Scenario: vacation budget exhausted."""
    p.seed_cluster(soil=35.0, reservoir_l=0.5, flow_rate_l_per_min=1.0)
    p.api("POST", "/api/v1/vacation", {"starts_at": FROZEN_TS - 3600, "ends_at": FROZEN_TS + 10 * 86400})
    p.mark()
    _irrigate(p)


def sc_window_outside(p: Pipeline) -> None:
    """Scenario: window outside."""
    p.seed_cluster(soil=35.0)
    p.api("POST", "/api/v1/clusters/1/windows", {"start_hour": 18, "end_hour": 22})
    p.mark()
    _irrigate(p)


def sc_window_inside(p: Pipeline) -> None:
    """Scenario: window inside."""
    p.seed_cluster(soil=35.0)
    p.api("POST", "/api/v1/clusters/1/windows", {"start_hour": 8, "end_hour": 12})
    p.mark()
    _irrigate(p)


def sc_window_outside_critical_stress_obeys_window(p: Pipeline) -> None:
    """Scenario: window outside critical stress obeys window."""
    p.seed_cluster(soil=8.0)
    p.api("POST", "/api/v1/clusters/1/windows", {"start_hour": 18, "end_hour": 22})
    p.mark()
    _irrigate(p)


def sc_caps_reached_automatic(p: Pipeline) -> None:
    """Scenario: caps reached automatic."""
    ids = p.seed_cluster(
        soil=35.0,
        config={
            "mode": "smart",
            "duration_minutes": 2,
            "interval_hours": 12,
            "auto_run": True,
            "daily_cap_minutes": 1,
            "max_events_per_day": 1,
        },
    )
    p.add_event(ids["irrigator_id"], age=7 * HOUR)
    p.mark()
    _irrigate(p)


def sc_caps_reached_manual_start(p: Pipeline) -> None:
    """Scenario: caps reached manual start."""
    ids = p.seed_cluster(
        soil=35.0,
        config={"mode": "smart", "duration_minutes": 2, "auto_run": True, "max_events_per_day": 1},
    )
    p.add_event(ids["irrigator_id"], age=7 * HOUR)
    p.mark()
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/log-manual", {"minutes": 3})


def sc_daily_minutes_cap_manual_start(p: Pipeline) -> None:
    """Scenario: daily minutes cap manual start."""
    ids = p.seed_cluster(soil=35.0, config={"mode": "smart", "daily_cap_minutes": 4})
    p.add_event(ids["irrigator_id"], age=7 * HOUR)
    p.mark()
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 2})


def sc_manual_start_stop_log(p: Pipeline) -> None:
    """Scenario: manual start stop log."""
    ids = p.seed_cluster(soil=35.0)
    p.mark()
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/stop")
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/log-manual", {"minutes": 4, "notes": "by hand"})
    p.call("POST", "/api/v1/irrigators/99/start", {"minutes": 3})
    _irrigate(p)


def sc_manual_start_device_failure(p: Pipeline) -> None:
    """Scenario: manual start device failure."""
    ids = p.seed_cluster(soil=35.0)
    p.wiring.irrigator.start_result = (False, "device timeout")
    p.wiring.irrigator.stop_result = (False, "device asleep")
    p.mark()
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/stop")


def sc_manual_start_no_registry(p: Pipeline) -> None:
    """Scenario: manual start no registry."""
    ids = p.seed_cluster(soil=35.0)
    p.app.dependency_overrides[get_device_registry] = lambda: None
    p.mark()
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/stop")
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/log-manual", {"minutes": 2})


def sc_dry_run(p: Pipeline) -> None:
    """Scenario: dry run."""
    p.seed_cluster(soil=35.0)
    p.mark()
    _irrigate(p, dry_run=True)


def sc_dry_run_skip(p: Pipeline) -> None:
    """Scenario: dry run skip."""
    p.seed_cluster(soil=88.0)
    p.mark()
    _irrigate(p, dry_run=True)


def sc_dry_run_global_preference(p: Pipeline) -> None:
    """Scenario: dry run global preference."""
    p.seed_cluster(soil=35.0)
    p.api("PUT", "/api/v1/preferences", {"dry_run_global": True})
    p.mark()
    _irrigate(p)


def sc_notify_auto_disabled(p: Pipeline) -> None:
    """Scenario: notify auto disabled."""
    p.seed_cluster(soil=35.0)
    p.api("PUT", "/api/v1/preferences", {"notify_auto": False})
    p.mark()
    _irrigate(p)


def sc_fresh_reading_no_gateway_call(p: Pipeline) -> None:
    """Scenario: fresh reading no gateway call."""
    p.seed_cluster(soil=35.0)
    p.use_gateway()
    p.mark()
    _irrigate(p)


def sc_stale_reading_forces_one_sync(p: Pipeline) -> None:
    """Scenario: stale reading forces one sync."""
    p.seed_cluster(soil=35.0, reading_ages=(5 * HOUR, 5 * HOUR + 600))
    p.use_gateway({"soil_moisture": 30.0, "temperature": 23.0})
    p.mark()
    _irrigate(p)


def sc_stale_reading_no_sync_flag(p: Pipeline) -> None:
    """Scenario: stale reading no sync flag."""
    p.seed_cluster(soil=35.0, reading_ages=(5 * HOUR, 5 * HOUR + 600))
    p.use_gateway()
    p.mark()
    _irrigate(p, no_sync=True)


def sc_no_plants(p: Pipeline) -> None:
    """Scenario: no plants."""
    p.seed_cluster(plant=False, soil=35.0)
    p.mark()
    _irrigate(p)


def sc_no_irrigator(p: Pipeline) -> None:
    """Scenario: no irrigator."""
    p.seed_cluster(irrigator=False, soil=35.0)
    p.mark()
    _irrigate(p)


def sc_no_registry(p: Pipeline) -> None:
    """Scenario: no registry."""
    p.seed_cluster(soil=35.0)
    p.app.dependency_overrides[get_device_registry] = lambda: None
    p.mark()
    _irrigate(p)


def sc_unknown_irrigator_model(p: Pipeline) -> None:
    """Scenario: unknown irrigator model."""
    p.seed_cluster(soil=35.0, irrigator=False)
    # A row from an older database: the API refuses an unknown type on write.
    with p.repo() as repo:
        repo.add_irrigator(
            cluster_id=1, tuya_device_id="fake_irrigator_x", name="Mystery", irrigator_type="acme.unknown", config={}
        )
    p.mark()
    _irrigate(p)


def sc_actuation_failed(p: Pipeline) -> None:
    """Scenario: actuation failed."""
    p.seed_cluster(soil=35.0)
    p.wiring.irrigator.start_result = (False, "device timeout")
    p.mark()
    _irrigate(p)


def sc_device_health_block(p: Pipeline) -> None:
    """Scenario: device health block."""
    _install_health_block(p, p.seed_cluster(soil=35.0))
    p.mark()
    _irrigate(p)


def sc_cluster_not_found(p: Pipeline) -> None:
    """Scenario: cluster not found."""
    p.seed_cluster(soil=35.0)
    p.mark()
    _irrigate(p, cid=99)
    p.call("POST", "/api/v1/clusters/99/check")
    p.call("GET", "/api/v1/clusters/99/decisions")


def sc_check_single_dry(p: Pipeline) -> None:
    """Scenario: check single dry."""
    p.seed_cluster(soil=35.0)
    p.mark()
    p.call("POST", "/api/v1/clusters/1/check")


def sc_check_single_auto_run_off(p: Pipeline) -> None:
    """Scenario: check single auto run off."""
    p.seed_cluster(soil=35.0, config={"mode": "smart", "duration_minutes": 2, "auto_run": False})
    p.mark()
    p.call("POST", "/api/v1/clusters/1/check")


def sc_check_single_sensor_only(p: Pipeline) -> None:
    """Scenario: check single sensor only."""
    p.seed_cluster(irrigator=False, soil=20.0)
    p.mark()
    p.call("POST", "/api/v1/clusters/1/check")


def sc_check_all_mixed(p: Pipeline) -> None:
    """Scenario: check all mixed."""
    p.seed_cluster("Alpha", soil=35.0)
    p.seed_cluster("Bravo", soil=88.0)
    p.seed_cluster("Charlie", irrigator=False, soil=20.0)
    p.seed_cluster("Delta", soil=35.0, config={"mode": "smart", "auto_run": False})
    p.seed_cluster("Echo", plant=False, sensor=False)
    p.mark()
    p.call("POST", "/api/v1/check")


def sc_check_all_twice_cooldown(p: Pipeline) -> None:
    """Scenario: check all twice cooldown."""
    p.seed_cluster(soil=35.0)
    p.mark()
    p.call("POST", "/api/v1/check")
    # Five minutes later, so the decision-log order (``evaluated_at`` DESC) has no tie to break.
    with time_machine.travel(FROZEN_INSTANT + timedelta(minutes=5), tick=False):
        p.call("POST", "/api/v1/check")
    p.call("GET", "/api/v1/clusters/1/decisions")
    p.call("GET", "/api/v1/clusters/1/decisions?limit=1")


def sc_check_all_empty(p: Pipeline) -> None:
    """Scenario: check all empty."""
    p.mark()
    p.call("POST", "/api/v1/check")


def _install_health_block(p: Pipeline, ids: dict[str, int]) -> None:
    """Wire a real ``DeviceHealthMonitor`` whose cache holds NO_WATER for the irrigator."""
    p.wiring.irrigator.set_health(DeviceHealthState(observed_at=FROZEN_TS, alarms=frozenset({HealthAlarm.NO_WATER})))
    session = p.app.state.session_factory()
    try:
        monitor = DeviceHealthMonitor(repo=IrrigationRepository(session), registry=p.wiring.registry, notifier=None)
        irrigator = IrrigationRepository(session).get_irrigator(ids["irrigator_id"])
        assert irrigator is not None
        monitor.poll_irrigator(irrigator)
        session.commit()
    finally:
        session.close()
    p.app.state.health_monitor = monitor


SCENARIOS: dict[str, Callable[[Pipeline], None]] = {
    fn.__name__.removeprefix("sc_"): fn
    for fn in (
        sc_dry_irrigates,
        sc_very_dry_critical_stress,
        sc_wet_skips,
        sc_adequate_skips,
        sc_no_readings_fallback,
        sc_outdoor_dry_weather_offline,
        sc_outdoor_rain_forecast,
        sc_indoor_no_sync_weather_available,
        sc_temp_override,
        sc_no_sync_offline,
        sc_cooldown_active,
        sc_cooldown_boundary_inside,
        sc_cooldown_boundary_outside,
        sc_cooldown_counts_manual_start,
        sc_cooldown_ignores_non_start,
        sc_quiet_hours,
        sc_quiet_hours_force,
        sc_quiet_hours_cluster_opt_out,
        sc_leak_hold_open,
        sc_leak_hold_acknowledged,
        sc_leak_hold_resolved,
        sc_leak_hold_force,
        sc_leak_hold_expired,
        sc_leak_hold_beats_cooldown,
        sc_leak_escape_hatch_manual_start,
        sc_vacation_without_capacity,
        sc_vacation_rationing,
        sc_vacation_budget_exhausted,
        sc_window_outside,
        sc_window_inside,
        sc_window_outside_critical_stress_obeys_window,
        sc_caps_reached_automatic,
        sc_caps_reached_manual_start,
        sc_daily_minutes_cap_manual_start,
        sc_manual_start_stop_log,
        sc_manual_start_device_failure,
        sc_manual_start_no_registry,
        sc_dry_run,
        sc_dry_run_skip,
        sc_dry_run_global_preference,
        sc_notify_auto_disabled,
        sc_fresh_reading_no_gateway_call,
        sc_stale_reading_forces_one_sync,
        sc_stale_reading_no_sync_flag,
        sc_no_plants,
        sc_no_irrigator,
        sc_no_registry,
        sc_unknown_irrigator_model,
        sc_actuation_failed,
        sc_device_health_block,
        sc_cluster_not_found,
        sc_check_single_dry,
        sc_check_single_auto_run_off,
        sc_check_single_sensor_only,
        sc_check_all_mixed,
        sc_check_all_twice_cooldown,
        sc_check_all_empty,
    )
}


def run_scenario(name: str) -> dict[str, Any]:
    """Run golden scenario ``name`` on a fresh pipeline and return what it observed."""
    p = Pipeline()
    try:
        SCENARIOS[name](p)
        return p.observed()
    finally:
        p.close()


# ── I6: exactly one decision_logs row per evaluation ───────────────────────

_ONE_LOG_CASES = {
    # scenario: (expected primary_code, expected decision action, actuated)
    "no_plants": ("no_plants", "skip", False),
    "leak_hold_open": ("leak_hold", "skip", False),
    "leak_hold_force": ("leak_hold", "skip", False),
    "cooldown_active": ("cooldown", "skip", False),
    "quiet_hours": ("quiet_hours", "skip", False),
    "outdoor_rain_forecast": ("weather_skip", "skip", False),
    "window_outside": ("outside_window", "skip", False),
    "vacation_budget_exhausted": ("vacation_budget_exhausted", "skip", False),
    "wet_skips": (None, "skip", False),
    "dry_irrigates": (None, "irrigate", True),
    "dry_run": (None, "irrigate", False),
    "actuation_failed": (None, "irrigate", False),
    "no_irrigator": (None, "irrigate", False),
    "check_single_dry": (None, "irrigate", True),
}


# 404s, manual actuation and ``auto_run`` off never reach the engine's persistence.
_ENGINE_ONLY_CASES = ("cluster_not_found", "manual_start_stop_log", "check_single_auto_run_off")


def _assert_one_decision_log(observed: dict[str, Any], name: str) -> None:
    """I6: each early return / final decision persists exactly one row that round-trips."""
    code, action, actuated = _ONE_LOG_CASES[name]
    rows = observed["db_writes"]["decision_logs"]
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == action
    assert row["actuated"] is actuated
    assert row["evaluated_at"] == FROZEN_TS
    decision = IrrigationDecision.model_validate(row["payload_json"])
    assert decision.action.value == action
    primary = decision.primary_code.value if decision.primary_code else None
    assert row["primary_code"] == primary
    if code is not None:
        assert primary == code


@pytest.mark.usefixtures("clean_env", "frozen_clock")
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_pipeline_golden(name: str) -> None:
    """Full matrix: responses, DB writes, adapter/gateway calls, notifications — one golden per scenario.

    The I6 cases also check their decision log, and the engine-only cases that only
    engine evaluations write one.
    """
    observed = run_scenario(name)
    assert_golden_json(f"orchestration/pipeline/{name}.json", observed)
    if name in _ONE_LOG_CASES:
        _assert_one_decision_log(observed, name)
    if name in _ENGINE_ONLY_CASES:
        evaluated = [
            c for c in observed["calls"] if c["request"].endswith("/irrigate") and c["status"] == HTTPStatus.OK
        ]
        assert len(observed["db_writes"]["decision_logs"]) == len(evaluated)


# ── Running scheduler: post-start follow-up jobs ───────────────────────────


def test_successful_auto_start_schedules_leak_check_and_watcher(pipeline: Pipeline) -> None:
    """A successful automatic start schedules ``leak-check-*`` (+30 min) and ``pump-watcher-*`` jobs."""
    pipeline.seed_cluster(soil=35.0)
    # The watcher reads the registry from app.state (not the request override).
    pipeline.app.state.device_registry = pipeline.wiring.registry
    pipeline.mark()
    start_scheduler(paused=True)
    try:
        assert _irrigate(pipeline)["response"]["action"] == "irrigated"
        # get_jobs() orders by next fire time (APScheduler's choice) — sort by id.
        adhoc = sorted(
            (
                {"id": j.id, "name": j.name, "trigger": str(j.trigger), "args": list(j.args)}
                for j in bg_scheduler.get_jobs()
                if j.id.startswith(("leak-check-", "pump-watcher-"))
            ),
            key=lambda job: job["id"],
        )
    finally:
        stop_scheduler()
    assert adhoc == [
        {
            "id": f"leak-check-1-{FROZEN_TS}",
            "name": "Leak check cluster 1",
            "trigger": "date[2026-04-15 10:30:00 UTC]",
            "args": [1, FROZEN_TS],
        },
        {
            "id": f"pump-watcher-1-{FROZEN_TS}",
            "name": "Pump watcher irrigator 1",
            "trigger": "date[2026-04-15 10:00:00 UTC]",
            "args": [],
        },
    ]


def test_forced_run_is_recorded_manual_and_keeps_leak_check_and_watcher(pipeline: Pipeline) -> None:
    """``force=true`` is the user's run: event, push and decision log say ``manual``; follow-ups stay.

    It runs the engine's computed duration unattended, so it gets the 30-min leak check and
    the dry-run watcher like an automatic run, plus a ``leak_check_armed`` marker so the
    restart re-arm finds it.
    """
    pipeline.seed_cluster(soil=35.0)
    pipeline.app.state.device_registry = pipeline.wiring.registry
    pipeline.mark()
    start_scheduler(paused=True)
    try:
        assert _irrigate(pipeline, force=True)["response"]["action"] == "irrigated"
        adhoc = sorted(j.id for j in bg_scheduler.get_jobs() if j.id.startswith(("leak-check-", "pump-watcher-")))
    finally:
        stop_scheduler()
    assert adhoc == [f"leak-check-1-{FROZEN_TS}", f"pump-watcher-1-{FROZEN_TS}"]
    rows = pipeline.db_rows()
    assert [(e["action"], e["triggered_by"]) for e in rows["irrigation_events"]] == [("start", "manual")]
    assert [r["triggered_by"] for r in rows["decision_logs"]] == ["manual"]
    assert [n["triggered_by"] for n in pipeline.notifier.sent] == ["manual"]
    assert [a["code"] for a in rows["activity_events"]] == ["irrigated", "leak_check_armed"]


def test_manual_start_schedules_watcher_and_leak_check(pipeline: Pipeline) -> None:
    """Every manual start with a duration is a real watering: it gets the leak check (owner decision).

    A short run keeps the 30-min check; without a duration (the device timer decides) the check
    is skipped and the activity log says why; the hand-watering log arms nothing.
    """
    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.app.state.device_registry = pipeline.wiring.registry
    pipeline.mark()
    start_scheduler(paused=True)
    try:
        pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
        with time_machine.travel(FROZEN_INSTANT + timedelta(minutes=1), tick=False):
            pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {})
            pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/log-manual", {"minutes": 2})
        jobs = {
            j.id: (str(j.trigger), list(j.args))
            for j in bg_scheduler.get_jobs()
            if j.id.startswith(("leak-check-", "pump-watcher-"))
        }
    finally:
        stop_scheduler()
    assert jobs == {
        f"leak-check-1-{FROZEN_TS}": ("date[2026-04-15 10:30:00 UTC]", [1, FROZEN_TS]),
        f"pump-watcher-1-{FROZEN_TS}": ("date[2026-04-15 10:00:00 UTC]", []),
    }
    leak_rows = [a for a in pipeline.db_rows()["activity_events"] if a["source"] == "leak"]
    assert [(a["code"], a["payload_json"]["started_at"]) for a in leak_rows] == [
        ("leak_check_armed", FROZEN_TS),
        ("leak_check_skipped", FROZEN_TS + 60),
    ]
    assert leak_rows[1]["message"] == (
        "post-watering leak check skipped: manual start without a duration (the device timer decides how long it runs)"
    )


def test_long_manual_start_checks_for_a_leak_after_the_run_has_settled(pipeline: Pipeline) -> None:
    """A long manual run's check is due at start + minutes + settle time, and observes that whole window."""
    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.app.state.device_registry = pipeline.wiring.registry
    pipeline.mark()
    start_scheduler(paused=True)
    try:
        pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 60})
        job = bg_scheduler.get_job(f"leak-check-1-{FROZEN_TS}")
        after = 60 * 60 + LEAK_CHECK_SETTLE_SECONDS
        assert (job.next_run_time.timestamp(), list(job.args)) == (FROZEN_TS + after, [1, FROZEN_TS, after])
    finally:
        stop_scheduler()
    (marker,) = [a for a in pipeline.db_rows()["activity_events"] if a["code"] == "leak_check_armed"]
    assert marker["payload_json"]["check_after_seconds"] == after


# ── Observed bugs (pinned, NOT fixed) ──────────────────────────────────────


def test_dry_run_global_never_actuates_but_stops_still_work(pipeline: Pipeline) -> None:
    """S1 / B-1 fixed: with ``dry_run_global`` on no start reaches the pump; stops always do.

    ``/irrigate`` (with and without ``force``) and ``/check`` evaluate, log a decision
    rewritten to ``skip`` / ``dry_run_global`` and a ``decision_skip`` activity row; the
    manual start is refused with 409 and leaves a ``dry_run_global`` activity row. Manual
    stop, stop-all and the hand-watering log (no hardware) are unaffected.
    """
    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.api("PUT", "/api/v1/preferences", {"dry_run_global": True})
    pipeline.mark()
    auto = _irrigate(pipeline)["response"]
    assert (auto["action"], auto["reasons"][0]["code"]) == ("skip", "dry_run_global")
    assert "would have irrigated 2min on 'Irrigator 1'" in auto["reason"]
    assert _irrigate(pipeline, force=True)["response"]["action"] == "skip"
    assert pipeline.call("POST", "/api/v1/clusters/1/check")["response"]["action"] == "skip"
    manual = pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 1})
    assert (manual["status"], manual["response"]) == (409, {"detail": "global dry run is on: irrigator not started"})
    assert pipeline.wiring.irrigator.calls == []

    assert pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/stop")["status"] == HTTPStatus.OK
    assert pipeline.call("POST", "/api/v1/bulk/stop-all")["response"] == {"stopped": 1, "errors": []}
    assert (
        pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/log-manual", {"minutes": 2})["status"]
        == HTTPStatus.OK
    )
    assert [c[0] for c in pipeline.wiring.irrigator.calls] == ["stop", "stop"]

    rows = pipeline.db_rows()
    assert [(r["action"], r["primary_code"], r["actuated"]) for r in rows["decision_logs"]] == [
        ("skip", "dry_run_global", False)
    ] * 3
    assert [a["code"] for a in rows["activity_events"]] == ["decision_skip"] * 3 + ["dry_run_global"]
    assert [(e["action"], e["triggered_by"]) for e in rows["irrigation_events"]] == [
        ("stop", "manual"),
        ("stop", "emergency"),
        ("start", "manual"),
    ]


def test_caps_checked_by_automatic_pipeline(pipeline: Pipeline) -> None:
    """B-4 fixed: exhausted per-day caps skip ``/irrigate`` and ``/check`` with ``daily_cap_hit``.

    With ``max_events_per_day=1`` / ``daily_cap_minutes=1`` already exhausted, the
    manual start is refused with 409 and the automatic runs (``force`` included) are
    logged as ``skip`` led by ``daily_cap_hit`` — no start reaches the adapter.
    """
    ids = pipeline.seed_cluster(
        soil=35.0,
        config={"mode": "smart", "auto_run": True, "daily_cap_minutes": 1, "max_events_per_day": 1},
    )
    pipeline.add_event(ids["irrigator_id"], age=7 * HOUR)
    pipeline.mark()
    manual = pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 1})
    assert (manual["status"], manual["response"]) == (409, {"detail": "cluster max_events_per_day reached"})
    auto = _irrigate(pipeline)["response"]
    assert (auto["action"], auto["reasons"][0]["code"]) == ("skip", "daily_cap_hit")
    assert _irrigate(pipeline, force=True)["response"]["action"] == "skip"
    assert pipeline.call("POST", "/api/v1/clusters/1/check")["response"]["action"] == "skip"
    logs = pipeline.db_rows()["decision_logs"]
    assert [(r["action"], r["primary_code"], r["actuated"]) for r in logs] == [("skip", "daily_cap_hit", False)] * 3
    assert pipeline.wiring.irrigator.calls == []


def test_global_caps_enforced_by_manual_and_automatic_starts(pipeline: Pipeline) -> None:
    """B-4 fixed: caps set only on the global config bind a cluster that sets none of its own."""
    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.api("PUT", "/api/v1/config/global", {"daily_cap_minutes": 4})
    pipeline.add_event(ids["irrigator_id"], age=7 * HOUR)  # 2 min used of 4
    pipeline.mark()
    refused = pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    assert (refused["status"], refused["response"]) == (409, {"detail": "irrigator daily cap reached"})
    pipeline.api("PUT", "/api/v1/config/global", {"max_events_per_day": 1})
    held = _irrigate(pipeline)["response"]
    assert (held["action"], held["reasons"][0]["code"]) == ("skip", "daily_cap_hit")
    assert "cluster max_events_per_day reached" in held["reason"]
    # A cluster value wins over the global one.
    pipeline.api("PUT", "/api/v1/clusters/1/config", {"max_events_per_day": 2})
    assert _irrigate(pipeline)["response"]["action"] == "irrigated"  # 2 + 2 == 4 minutes fits
    assert [c[0] for c in pipeline.wiring.irrigator.calls] == ["start"]


def test_reraised_alert_is_renotified(pipeline: Pipeline) -> None:
    """B-5 fixed: a re-opened alert notifies again.

    The first ``actuation_failed`` alert pushes; once resolved, the next
    failure re-opens the same row (``occurrence_count`` 2) and pushes again.
    """
    pipeline.seed_cluster(soil=35.0)
    pipeline.wiring.irrigator.start_result = (False, "device timeout")
    pipeline.mark()
    _irrigate(pipeline)
    alert = pipeline.db_rows()["alerts"][0]
    assert [n["kind"] for n in pipeline.notifier.sent] == ["alert"]
    pipeline.api("POST", f"/api/v1/alerts/{alert['id']}/resolve")
    # Failed starts are written as ``attempted`` (not ``start``), so no cooldown blocks the retry.
    _irrigate(pipeline)
    reopened = pipeline.db_rows()["alerts"][0]
    assert (reopened["id"], reopened["status"], reopened["occurrence_count"]) == (alert["id"], "open", 2)
    assert [n["kind"] for n in pipeline.notifier.sent] == ["alert", "alert"]


def test_vacation_create_rejects_reversed_window(pipeline: Pipeline) -> None:
    """D5 (was B-8): ``POST /vacation`` validates ``starts_at < ends_at`` like ``PUT`` and the web forms.

    Reversed and empty windows are rejected with the shared 400 wording and nothing is stored.
    """
    for starts, ends in ((FROZEN_TS + 86400, FROZEN_TS), (FROZEN_TS, FROZEN_TS)):
        rejected = pipeline.call("POST", "/api/v1/vacation", {"starts_at": starts, "ends_at": ends})
        assert rejected["status"] == HTTPStatus.BAD_REQUEST
        assert rejected["response"] == {"detail": "starts_at must be < ends_at"}
    created = pipeline.call("POST", "/api/v1/vacation", {"starts_at": FROZEN_TS, "ends_at": FROZEN_TS + 86400})
    assert created["status"] == HTTPStatus.CREATED
    window_id = created["response"]["id"]
    rejected = pipeline.call("PUT", f"/api/v1/vacation/{window_id}", {"ends_at": FROZEN_TS - 1})
    assert rejected["status"] == HTTPStatus.BAD_REQUEST
    assert rejected["response"] == {"detail": "starts_at must be < ends_at"}
    assert [w["id"] for w in pipeline.call("GET", "/api/v1/vacation")["response"]["items"]] == [window_id]


def test_vacation_budget_projection(pipeline: Pipeline) -> None:
    """``services.vacation`` (web-only read model): per-cluster daily budget, capacity-configured clusters only."""
    pipeline.seed_cluster("Zulu", soil=35.0, reservoir_l=6.0, flow_rate_l_per_min=1.0)
    pipeline.seed_cluster("Alpha", soil=35.0, reservoir_l=10.0, flow_rate_l_per_min=0.5)
    pipeline.seed_cluster("No capacity", soil=35.0, reservoir_l=10.0)
    pipeline.seed_cluster("No irrigator", soil=35.0, irrigator=False)
    assert [vacation_days(0, s) for s in (0, 1, 86400, 86401, 3 * 86400)] == [1, 1, 1, 2, 3]
    with pipeline.repo() as repo:
        budgets = [vars(b) for b in cluster_budgets(repo, FROZEN_TS, FROZEN_TS + 4 * 86400 - 3600)]
    assert budgets == [
        {
            "cluster_id": 2,
            "cluster_name": "Alpha",
            "vacation_days": 4,
            "total_reservoir_l": 10.0,
            "usable_reservoir_l": 9.5,
            "daily_budget_l": 2.38,
        },
        {
            "cluster_id": 1,
            "cluster_name": "Zulu",
            "vacation_days": 4,
            "total_reservoir_l": 6.0,
            "usable_reservoir_l": 5.7,
            "daily_budget_l": 1.42,  # round(5.7 / 4, 2): binary float rounds down
        },
    ]


# ── Compensating stop: a start that cannot be recorded is stopped again ────


@contextmanager
def _failing_commit() -> Iterator[None]:
    """Make the next ``Session.commit`` raise, as a locked database would."""
    real = Session.commit
    state = {"armed": True}

    def commit(self: Session) -> None:
        if state["armed"]:
            state["armed"] = False
            msg = "database is locked"
            raise RuntimeError(msg)
        real(self)

    Session.commit = commit  # type: ignore[method-assign]
    try:
        yield
    finally:
        Session.commit = real  # type: ignore[method-assign]


def test_unrecorded_automatic_start_is_stopped_and_surfaced(
    pipeline: Pipeline, caplog: pytest.LogCaptureFixture
) -> None:
    """If the start event cannot be committed, the pump is stopped at once, logged at ERROR, and the error surfaces."""
    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.mark()
    with _failing_commit(), caplog.at_level("ERROR", logger="greenhouse_server.services.irrigation"):
        resp = pipeline.client.post("/api/v1/clusters/1/irrigate", json={})
    assert resp.status_code == HTTPStatus.INTERNAL_SERVER_ERROR  # the write error surfaces
    assert pipeline.wiring.irrigator.calls == [("start", ids["irrigator_id"], 2), ("stop", ids["irrigator_id"])]
    assert any("compensating stop: stop_ok=True" in m for m in caplog.messages)
    assert pipeline.db_rows()["irrigation_events"] == []
    assert pipeline.notifier.sent == []


def test_unrecorded_start_in_check_all_is_stopped_and_reported(pipeline: Pipeline) -> None:
    """In ``check_all`` the cluster is reported as failed (``check_failed``) and its pump stopped again."""
    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.mark()
    with _failing_commit():
        resp = pipeline.call("POST", "/api/v1/check")
    assert [r["action"] for r in resp["response"]["results"]] == ["error"]
    assert pipeline.wiring.irrigator.calls == [("start", ids["irrigator_id"], 2), ("stop", ids["irrigator_id"])]
    assert [a["code"] for a in pipeline.db_rows()["alerts"]] == ["check_failed"]


def test_unrecorded_manual_start_is_stopped_and_refused(pipeline: Pipeline, caplog: pytest.LogCaptureFixture) -> None:
    """Unrecorded manual start is stopped and refused."""
    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.mark()
    with _failing_commit(), caplog.at_level("ERROR", logger="greenhouse_server.services.irrigation"):
        resp = pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    assert (resp["status"], resp["response"]) == (
        500,
        {"detail": "the start could not be recorded, so the irrigator was stopped again"},
    )
    assert pipeline.wiring.irrigator.calls == [("start", ids["irrigator_id"], 3), ("stop", ids["irrigator_id"])]
    assert any("compensating stop: stop_ok=True" in m for m in caplog.messages)
    assert pipeline.db_rows()["irrigation_events"] == []

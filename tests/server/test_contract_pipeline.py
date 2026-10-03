"""Golden characterization of the irrigation pipeline (orchestration work package).

Pins *what the orchestration layer does today* for a seeded app: the JSON each
operation route answers, every DB row it writes (irrigation events, decision
logs incl. ``payload_json``, alerts, activity, freshly synced readings), every
call it makes on the fake irrigator/sensor adapters and the recording Tuya
gateway, and every push notification it attempts.

Hermetic: clock frozen at ``FROZEN_INSTANT`` (10:00 UTC — outside the
migration-seeded 00–05 quiet hours), weather offline unless a scenario installs
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
- observed bugs B-1, B-4, B-5, B-6 pinned as ``*_current_behavior_*`` (B-8 fixed by drift pair D5).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import contextmanager
from datetime import timedelta

import pytest
import time_machine
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from golden import FROZEN_INSTANT, FROZEN_TS, assert_golden_json, install_offline_weather
from greenhouse_core.constants import LEAK_ALERT_CODE
from greenhouse_core.devices.health import DeviceHealthState, HealthAlarm
from greenhouse_core.logic.decision import IrrigationDecision
from greenhouse_core.models import Base
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.deps import get_device_gateway, get_device_registry, get_weather_client
from server.conftest import _make_stubbed_app

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
        self.sent: list[dict] = []

    def notify_irrigation(self, **kwargs) -> bool:
        self.sent.append({"kind": "irrigation", **kwargs})
        return True

    def notify_alert(self, **kwargs) -> bool:
        self.sent.append({"kind": "alert", **kwargs})
        return True


class RecordingGateway:
    """Stand-in for ``DeviceGateway``: empty device logs, canned live reading, call log."""

    def __init__(self, live: dict | None = None) -> None:
        self.live = live if live is not None else {"soil_moisture": 30.0, "temperature": 23.0}
        self.calls: list[list] = []

    def get_device_logs(self, device_id: str, since_ms: int | None = None, **kwargs) -> list[dict]:
        self.calls.append(["get_device_logs", device_id, since_ms])
        return []

    def group_logs_by_timestamp(self, logs: list[dict], tolerance_ms: int = 5000) -> list[dict]:
        self.calls.append(["group_logs_by_timestamp", len(logs)])
        return []

    def get_live_reading(self, device_id: str) -> dict:
        self.calls.append(["get_live_reading", device_id])
        return dict(self.live)


class RainyWeather:
    """Canned Open-Meteo answer: mild now, significant rain forecast (no network)."""

    def get_current(self) -> dict:
        return {"temperature": 17.0, "feels_like": 16.5, "humidity": 80.0}

    def get_forecast(self, hours: int = 6) -> dict:  # noqa: ARG002 — mirrors WeatherClient
        return {"precipitation_mm": 6.5}


# ── Harness ─────────────────────────────────────────────────────────────────


class Pipeline:
    """One freshly built, seeded app plus the recorders a scenario inspects."""

    def __init__(self) -> None:
        self.app, self.engine = _make_stubbed_app(bypass_auth=True)
        install_offline_weather(self.app)
        self.notifier = RecordingNotifier()
        self.app.state.ntfy_notifier = self.notifier
        self.wiring = self.app.state.fake_devices
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.gateway: RecordingGateway | None = None
        self.calls: list[dict] = []
        self._marks: dict[str, int] = {}

    def close(self) -> None:
        self.client.close()
        self.engine.dispose()

    # seeding -----------------------------------------------------------------

    @contextmanager
    def repo(self):
        session = self.app.state.session_factory()
        try:
            yield IrrigationRepository(session)
            session.commit()
        finally:
            session.close()

    def api(self, method: str, path: str, body: dict | None = None) -> dict:
        """Seeding call: must succeed; not recorded in the golden."""
        resp = self.client.request(method, path, json=body)
        assert resp.status_code < 300, f"{method} {path} -> {resp.status_code} {resp.text}"
        return resp.json()

    def use_gateway(self, live: dict | None = None) -> RecordingGateway:
        self.gateway = RecordingGateway(live)
        self.app.dependency_overrides[get_device_gateway] = lambda: self.gateway
        return self.gateway

    def use_weather(self, stub) -> None:
        self.app.state.weather_client = stub
        self.app.dependency_overrides[get_weather_client] = lambda: stub

    def seed_cluster(
        self,
        name: str = "Golden Cluster",
        *,
        environment: str = "indoor",
        plant: bool = True,
        sensor: bool = True,
        irrigator: bool = True,
        soil: float | None = 35.0,
        temp: float = 24.0,
        reading_ages: tuple[int, ...] = (0, 600, 1200),
        reservoir_l: float | None = None,
        flow_rate_l_per_min: float | None = None,
        config: dict | None = None,
    ) -> dict:
        """Create cluster (+plant, sensor, irrigator, config) via the API; readings via the repo."""
        cid = self.api("POST", "/api/v1/clusters", {"name": name, "environment": environment})["id"]
        ids: dict = {"cluster_id": cid}
        if plant:
            ids["plant_id"] = self.api(
                "POST",
                f"/api/v1/clusters/{cid}/plants",
                {
                    "species": "Monstera deliciosa",
                    "category": "tropical",
                    "water_needs": "medium",
                    "ideal_temp_min": 18.0,
                    "ideal_temp_max": 27.0,
                    "ideal_humidity_min": 60.0,
                    "ideal_humidity_max": 80.0,
                },
            )["id"]
        if sensor:
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
        if irrigator:
            body = {
                "tuya_device_id": f"fake_irrigator_{cid:03d}",
                "name": f"Irrigator {cid}",
                "type": "rainpoint.ik10pw",
            }
            if reservoir_l is not None:
                body["reservoir_l"] = reservoir_l
            if flow_rate_l_per_min is not None:
                body["flow_rate_l_per_min"] = flow_rate_l_per_min
            ids["irrigator_id"] = self.api("POST", f"/api/v1/clusters/{cid}/irrigator", body)["id"]
        self.api(
            "PUT",
            f"/api/v1/clusters/{cid}/config",
            config or {"mode": "smart", "duration_minutes": 2, "interval_hours": 12, "auto_run": True},
        )
        if sensor and soil is not None:
            with self.repo() as repo:
                for age in reading_ages:
                    repo.add_sensor_reading(
                        sensor_id=ids["sensor_id"], timestamp=FROZEN_TS - age, soil_moisture=soil, temperature=temp
                    )
        return ids

    def add_event(self, irrigator_id: int, *, age: int, action: str = "start", triggered_by: str = "auto") -> None:
        with self.repo() as repo:
            repo.add_irrigation_event(
                irrigator_id=irrigator_id,
                action=action,
                duration_minutes=2,
                triggered_by=triggered_by,
                notes="seeded",
                timestamp=FROZEN_TS - age,
            )

    def raise_leak(self, cluster_id: int, sensor_id: int, *, age: int = 0) -> int:
        with self.repo() as repo:
            alert = repo.upsert_alert(
                dedup_key=f"leak::{LEAK_ALERT_CODE}::{cluster_id}::sensor{sensor_id}",
                source="leak",
                code=LEAK_ALERT_CODE,
                title="Possible leak or stuck valve detected",
                message=f"Sensor {cluster_id}: soil moisture still rising after irrigation (latest=78.0%)",
                severity="critical",
                entity_type="sensor",
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

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        """Observed call: request, status and JSON body go into the golden."""
        resp = self.client.request(method, path, json=body)
        entry = {"request": f"{method} {path}", "body": body, "status": resp.status_code, "response": resp.json()}
        self.calls.append(entry)
        return entry

    def db_rows(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        with self.app.state.session_factory() as session:
            for name in APPEND_ONLY_TABLES + MUTABLE_TABLES:
                table = Base.metadata.tables[name]
                stmt = select(table).order_by(table.c.id)
                if name in self._marks:
                    stmt = stmt.where(table.c.id > self._marks[name])
                rows = []
                for row in session.execute(stmt).mappings():
                    record = dict(row)
                    for col in JSON_COLUMNS:
                        if record.get(col):
                            record[col] = json.loads(record[col])
                    rows.append(record)
                out[name] = rows
        return out

    def observed(self) -> dict:
        return {
            "calls": self.calls,
            "db_writes": self.db_rows(),
            "irrigator_adapter_calls": [list(c) for c in self.wiring.irrigator.calls],
            "sensor_adapter_calls": [list(c) for c in self.wiring.sensor.calls],
            "gateway_calls": self.gateway.calls if self.gateway is not None else None,
            "notifications": self.notifier.sent,
        }


@pytest.fixture
def pipeline(clean_env, frozen_clock):
    p = Pipeline()
    yield p
    p.close()


def _irrigate(p: Pipeline, cid: int = 1, **body) -> dict:
    return p.call("POST", f"/api/v1/clusters/{cid}/irrigate", body)


# ── Scenario matrix ─────────────────────────────────────────────────────────
#
# Each scenario seeds a fresh app, calls ``p.mark()`` and then makes the
# observed calls. Every scenario starts from the same base (one indoor cluster:
# Monstera, soil sensor, ``rainpoint.ik10pw`` irrigator, smart/2 min/12 h/auto_run)
# unless it says otherwise.


def sc_dry_irrigates(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.mark()
    _irrigate(p)


def sc_very_dry_critical_stress(p: Pipeline) -> None:
    p.seed_cluster(soil=8.0)
    p.mark()
    _irrigate(p)


def sc_wet_skips(p: Pipeline) -> None:
    p.seed_cluster(soil=88.0)
    p.mark()
    _irrigate(p)


def sc_adequate_skips(p: Pipeline) -> None:
    p.seed_cluster(soil=55.0)
    p.mark()
    _irrigate(p)


def sc_no_readings_fallback(p: Pipeline) -> None:
    p.seed_cluster(soil=None)
    p.mark()
    _irrigate(p)


def sc_outdoor_dry_weather_offline(p: Pipeline) -> None:
    p.seed_cluster(environment="outdoor", soil=35.0)
    p.mark()
    _irrigate(p)


def sc_outdoor_rain_forecast(p: Pipeline) -> None:
    p.seed_cluster(environment="outdoor", soil=35.0)
    p.use_weather(RainyWeather())
    p.mark()
    _irrigate(p)


def sc_indoor_no_sync_weather_available(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.use_weather(RainyWeather())
    p.mark()
    _irrigate(p, no_sync=True)


def sc_temp_override(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.mark()
    _irrigate(p, temp_override=31.5)


def sc_no_sync_offline(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.mark()
    _irrigate(p, no_sync=True)


def sc_cooldown_active(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=2 * HOUR)
    p.mark()
    _irrigate(p)


def sc_cooldown_boundary_inside(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=6 * HOUR - 60)
    p.mark()
    _irrigate(p)


def sc_cooldown_boundary_outside(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=6 * HOUR + 60)
    p.mark()
    _irrigate(p)


def sc_cooldown_counts_manual_start(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=HOUR, triggered_by="manual")
    p.mark()
    _irrigate(p)


def sc_cooldown_ignores_non_start(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=HOUR, action="schedule_updated")
    p.add_event(ids["irrigator_id"], age=HOUR - 60, action="off", triggered_by="manual")
    p.mark()
    _irrigate(p)


def sc_quiet_hours(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.api("PUT", "/api/v1/config/global", {"quiet_start_hour": 9, "quiet_end_hour": 11})
    p.mark()
    _irrigate(p)


def sc_quiet_hours_force(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.api("PUT", "/api/v1/config/global", {"quiet_start_hour": 9, "quiet_end_hour": 11})
    p.mark()
    _irrigate(p, force=True)


def sc_quiet_hours_cluster_opt_out(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.api("PUT", "/api/v1/config/global", {"quiet_start_hour": 9, "quiet_end_hour": 11})
    p.api("PUT", "/api/v1/clusters/1/config", {"quiet_start_hour": 0, "quiet_end_hour": 0})
    p.mark()
    _irrigate(p)


def sc_leak_hold_open(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.mark()
    _irrigate(p)


def sc_leak_hold_acknowledged(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    alert_id = p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.api("POST", f"/api/v1/alerts/{alert_id}/acknowledge")
    p.mark()
    _irrigate(p)


def sc_leak_hold_resolved(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    alert_id = p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.api("POST", f"/api/v1/alerts/{alert_id}/resolve")
    p.mark()
    _irrigate(p)


def sc_leak_hold_force(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.mark()
    _irrigate(p, force=True)


def sc_leak_hold_expired(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=25 * HOUR)
    p.mark()
    _irrigate(p)


def sc_leak_hold_beats_cooldown(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.add_event(ids["irrigator_id"], age=HOUR)
    p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.mark()
    _irrigate(p)


def sc_leak_escape_hatch_manual_start(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    p.mark()
    _irrigate(p, force=True)
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})


def sc_vacation_without_capacity(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.api("POST", "/api/v1/vacation", {"starts_at": FROZEN_TS - 86400, "ends_at": FROZEN_TS + 3 * 86400})
    p.mark()
    _irrigate(p)


def sc_vacation_rationing(p: Pipeline) -> None:
    # 6 L * 0.95 usable over a 4-day window → 1.425 L/day → 1 min at 1 L/min (< the 2 min asked).
    p.seed_cluster(soil=35.0, reservoir_l=6.0, flow_rate_l_per_min=1.0)
    p.api("POST", "/api/v1/vacation", {"starts_at": FROZEN_TS - 3600, "ends_at": FROZEN_TS + 4 * 86400 - 3600})
    p.mark()
    _irrigate(p)


def sc_vacation_budget_exhausted(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0, reservoir_l=0.5, flow_rate_l_per_min=1.0)
    p.api("POST", "/api/v1/vacation", {"starts_at": FROZEN_TS - 3600, "ends_at": FROZEN_TS + 10 * 86400})
    p.mark()
    _irrigate(p)


def sc_window_outside(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.api("POST", "/api/v1/clusters/1/windows", {"start_hour": 18, "end_hour": 22})
    p.mark()
    _irrigate(p)


def sc_window_inside(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.api("POST", "/api/v1/clusters/1/windows", {"start_hour": 8, "end_hour": 12})
    p.mark()
    _irrigate(p)


def sc_window_outside_critical_stress_overrides(p: Pipeline) -> None:
    p.seed_cluster(soil=8.0)
    p.api("POST", "/api/v1/clusters/1/windows", {"start_hour": 18, "end_hour": 22})
    p.mark()
    _irrigate(p)


def sc_caps_reached_automatic(p: Pipeline) -> None:
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
    ids = p.seed_cluster(
        soil=35.0,
        config={"mode": "smart", "duration_minutes": 2, "auto_run": True, "max_events_per_day": 1},
    )
    p.add_event(ids["irrigator_id"], age=7 * HOUR)
    p.mark()
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/log-manual", {"minutes": 3})


def sc_daily_minutes_cap_manual_start(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0, config={"mode": "smart", "daily_cap_minutes": 4})
    p.add_event(ids["irrigator_id"], age=7 * HOUR)
    p.mark()
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 2})


def sc_manual_start_stop_log(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.mark()
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/stop")
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/log-manual", {"minutes": 4, "notes": "by hand"})
    p.call("POST", "/api/v1/irrigators/99/start", {"minutes": 3})
    _irrigate(p)


def sc_manual_start_device_failure(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.wiring.irrigator.start_result = (False, "device timeout")
    p.wiring.irrigator.stop_result = (False, "device asleep")
    p.mark()
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/stop")


def sc_manual_start_no_registry(p: Pipeline) -> None:
    ids = p.seed_cluster(soil=35.0)
    p.app.dependency_overrides[get_device_registry] = lambda: None
    p.mark()
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/stop")
    p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/log-manual", {"minutes": 2})


def sc_dry_run(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.mark()
    _irrigate(p, dry_run=True)


def sc_dry_run_skip(p: Pipeline) -> None:
    p.seed_cluster(soil=88.0)
    p.mark()
    _irrigate(p, dry_run=True)


def sc_dry_run_global_preference(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.api("PUT", "/api/v1/preferences", {"dry_run_global": True})
    p.mark()
    _irrigate(p)


def sc_notify_auto_disabled(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.api("PUT", "/api/v1/preferences", {"notify_auto": False})
    p.mark()
    _irrigate(p)


def sc_fresh_reading_no_gateway_call(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.use_gateway()
    p.mark()
    _irrigate(p)


def sc_stale_reading_forces_one_sync(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0, reading_ages=(5 * HOUR, 5 * HOUR + 600))
    p.use_gateway({"soil_moisture": 30.0, "temperature": 23.0})
    p.mark()
    _irrigate(p)


def sc_stale_reading_no_sync_flag(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0, reading_ages=(5 * HOUR, 5 * HOUR + 600))
    p.use_gateway()
    p.mark()
    _irrigate(p, no_sync=True)


def sc_no_plants(p: Pipeline) -> None:
    p.seed_cluster(plant=False, soil=35.0)
    p.mark()
    _irrigate(p)


def sc_no_irrigator(p: Pipeline) -> None:
    p.seed_cluster(irrigator=False, soil=35.0)
    p.mark()
    _irrigate(p)


def sc_no_registry(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.app.dependency_overrides[get_device_registry] = lambda: None
    p.mark()
    _irrigate(p)


def sc_unknown_irrigator_model(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0, irrigator=False)
    p.api(
        "POST",
        "/api/v1/clusters/1/irrigator",
        {"tuya_device_id": "fake_irrigator_x", "name": "Mystery", "type": "acme.unknown"},
    )
    p.mark()
    _irrigate(p)


def sc_actuation_failed(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.wiring.irrigator.start_result = (False, "device timeout")
    p.mark()
    _irrigate(p)


def sc_device_health_block(p: Pipeline) -> None:
    _install_health_block(p, p.seed_cluster(soil=35.0))
    p.mark()
    _irrigate(p)


def sc_cluster_not_found(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.mark()
    _irrigate(p, cid=99)
    p.call("POST", "/api/v1/clusters/99/check")
    p.call("GET", "/api/v1/clusters/99/decisions")


def sc_check_single_dry(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.mark()
    p.call("POST", "/api/v1/clusters/1/check")


def sc_check_single_auto_run_off(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0, config={"mode": "smart", "duration_minutes": 2, "auto_run": False})
    p.mark()
    p.call("POST", "/api/v1/clusters/1/check")


def sc_check_single_sensor_only(p: Pipeline) -> None:
    p.seed_cluster(irrigator=False, soil=20.0)
    p.mark()
    p.call("POST", "/api/v1/clusters/1/check")


def sc_check_all_mixed(p: Pipeline) -> None:
    p.seed_cluster("Alpha", soil=35.0)
    p.seed_cluster("Bravo", soil=88.0)
    p.seed_cluster("Charlie", irrigator=False, soil=20.0)
    p.seed_cluster("Delta", soil=35.0, config={"mode": "smart", "auto_run": False})
    p.seed_cluster("Echo", plant=False, sensor=False)
    p.mark()
    p.call("POST", "/api/v1/check")


def sc_check_all_twice_cooldown(p: Pipeline) -> None:
    p.seed_cluster(soil=35.0)
    p.mark()
    p.call("POST", "/api/v1/check")
    # Five minutes later, so the decision-log order (``evaluated_at`` DESC) has no tie to break.
    with time_machine.travel(FROZEN_INSTANT + timedelta(minutes=5), tick=False):
        p.call("POST", "/api/v1/check")
    p.call("GET", "/api/v1/clusters/1/decisions")
    p.call("GET", "/api/v1/clusters/1/decisions?limit=1")


def sc_check_all_empty(p: Pipeline) -> None:
    p.mark()
    p.call("POST", "/api/v1/check")


def _install_health_block(p: Pipeline, ids: dict) -> None:
    """Wire a real ``DeviceHealthMonitor`` whose cache holds NO_WATER for the irrigator."""
    from greenhouse_server.services.health_monitor import DeviceHealthMonitor

    p.wiring.irrigator.set_health(DeviceHealthState(observed_at=FROZEN_TS, alarms=frozenset({HealthAlarm.NO_WATER})))
    session = p.app.state.session_factory()
    try:
        monitor = DeviceHealthMonitor(repo=IrrigationRepository(session), registry=p.wiring.registry, notifier=None)
        monitor.poll_irrigator(IrrigationRepository(session).get_irrigator(ids["irrigator_id"]))
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
        sc_window_outside_critical_stress_overrides,
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


def run_scenario(name: str) -> dict:
    p = Pipeline()
    try:
        SCENARIOS[name](p)
        return p.observed()
    finally:
        p.close()


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_pipeline_golden(name, clean_env, frozen_clock):
    """Full matrix: responses, DB writes, adapter/gateway calls, notifications — one golden per scenario."""
    assert_golden_json(f"orchestration/pipeline/{name}.json", run_scenario(name))


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
    "vacation_budget_exhausted": (None, "skip", False),
    "wet_skips": (None, "skip", False),
    "dry_irrigates": (None, "irrigate", True),
    "dry_run": (None, "irrigate", False),
    "actuation_failed": (None, "irrigate", False),
    "no_irrigator": (None, "irrigate", False),
    "check_single_dry": (None, "irrigate", True),
}


@pytest.mark.parametrize("name", sorted(_ONE_LOG_CASES))
def test_each_evaluation_writes_exactly_one_decision_log(name, clean_env, frozen_clock):
    """I6: each early return / final decision persists exactly one row that round-trips."""
    code, action, actuated = _ONE_LOG_CASES[name]
    rows = run_scenario(name)["db_writes"]["decision_logs"]
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


@pytest.mark.parametrize("name", ["cluster_not_found", "manual_start_stop_log", "check_single_auto_run_off"])
def test_decision_logs_only_come_from_engine_evaluations(name, clean_env, frozen_clock):
    """404s, manual actuation and ``auto_run`` off never reach the engine's persistence."""
    observed = run_scenario(name)
    evaluated = [c for c in observed["calls"] if c["request"].endswith("/irrigate") and c["status"] == 200]
    assert len(observed["db_writes"]["decision_logs"]) == len(evaluated)


# ── I11: the leak hold vs the escape hatch ─────────────────────────────────


def test_leak_hold_blocks_forced_irrigate_but_not_direct_start(pipeline):
    """I11: ``force=true`` cannot bypass the hold; ``POST /irrigators/{id}/start`` still actuates."""
    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.raise_leak(ids["cluster_id"], ids["sensor_id"], age=HOUR)
    pipeline.mark()

    held = _irrigate(pipeline, force=True)
    assert held["status"] == 200
    assert held["response"]["action"] == "skip"
    assert [r["code"] for r in held["response"]["reasons"]] == ["leak_hold"]
    assert pipeline.wiring.irrigator.calls == []

    started = pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
    assert started["status"] == 200
    assert pipeline.wiring.irrigator.calls == [("start", ids["irrigator_id"], 3)]
    events = pipeline.db_rows()["irrigation_events"]
    assert [(e["action"], e["triggered_by"], e["duration_minutes"]) for e in events] == [("start", "manual", 3)]
    # The hold itself is untouched by the manual start.
    alerts = pipeline.db_rows()["alerts"]
    assert [(a["code"], a["status"]) for a in alerts] == [(LEAK_ALERT_CODE, "open")]


# ── Running scheduler: post-start follow-up jobs ───────────────────────────


def test_successful_auto_start_schedules_leak_check_and_watcher(pipeline):
    """A successful automatic start schedules ``leak-check-*`` (+30 min) and ``pump-watcher-*`` jobs."""
    from greenhouse_server.scheduler import scheduler as bg_scheduler
    from greenhouse_server.scheduler import start_scheduler, stop_scheduler

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


def test_manual_start_schedules_watcher_but_no_leak_check(pipeline):
    """Manual starts get a dry-run watcher (when a duration is given) but never a leak check."""
    from greenhouse_server.scheduler import scheduler as bg_scheduler
    from greenhouse_server.scheduler import start_scheduler, stop_scheduler

    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.app.state.device_registry = pipeline.wiring.registry
    start_scheduler(paused=True)
    try:
        pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 3})
        pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {})
        adhoc = sorted(j.id for j in bg_scheduler.get_jobs() if j.id.startswith(("leak-check-", "pump-watcher-")))
    finally:
        stop_scheduler()
    assert adhoc == [f"pump-watcher-1-{FROZEN_TS}"]


# ── Observed bugs (pinned, NOT fixed) ──────────────────────────────────────


def test_dry_run_global_current_behavior_still_actuates(pipeline):
    """Pins current (buggy) behavior: ``dry_run_global`` is ignored by actuation (B-1) — see REFACTOR_NOTES.md.

    The preference promises "no irrigator ever actuates", yet the pipeline,
    the manual start and ``/check`` all drive the adapter.
    """
    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.api("PUT", "/api/v1/preferences", {"dry_run_global": True})
    pipeline.mark()
    assert _irrigate(pipeline)["response"]["action"] == "irrigated"
    assert pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 1})["status"] == 200
    assert [c[0] for c in pipeline.wiring.irrigator.calls] == ["start", "start"]


def test_caps_current_behavior_not_checked_by_automatic_pipeline(pipeline):
    """Pins current (buggy) behavior: caps are not checked by the automatic pipeline (B-4) — see REFACTOR_NOTES.md.

    With ``max_events_per_day=1`` / ``daily_cap_minutes=1`` already exhausted,
    the manual start is refused with 409 but ``/irrigate`` and ``/check`` still
    actuate; ``TriggerCode.DAILY_CAP_HIT`` never appears. Global caps are not
    enforced by the manual path either (it reads the raw cluster row only).
    """
    ids = pipeline.seed_cluster(
        soil=35.0,
        config={"mode": "smart", "auto_run": True, "daily_cap_minutes": 1, "max_events_per_day": 1},
    )
    pipeline.add_event(ids["irrigator_id"], age=7 * HOUR)
    pipeline.mark()
    manual = pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 1})
    assert manual["status"] == 409
    auto = _irrigate(pipeline)
    assert auto["response"]["action"] == "irrigated"
    assert "daily_cap_hit" not in json.dumps(pipeline.db_rows()["decision_logs"])
    assert [c[0] for c in pipeline.wiring.irrigator.calls] == ["start"]


def test_global_caps_current_behavior_ignored_by_manual_start(pipeline):
    """Pins current (buggy) behavior: global ``max_events_per_day`` is ignored by manual start (B-4) — see REFACTOR_NOTES.md."""
    ids = pipeline.seed_cluster(soil=35.0)
    pipeline.api("PUT", "/api/v1/config/global", {"max_events_per_day": 1, "daily_cap_minutes": 1})
    pipeline.add_event(ids["irrigator_id"], age=7 * HOUR)
    pipeline.mark()
    assert pipeline.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 5})["status"] == 200


def test_reraised_alert_current_behavior_is_not_renotified(pipeline):
    """Pins current (buggy) behavior: a re-opened alert never notifies again (B-5) — see REFACTOR_NOTES.md.

    The first ``actuation_failed`` alert pushes; once resolved, the next
    failure re-opens the same row (``occurrence_count`` 2) and stays silent.
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
    assert [n["kind"] for n in pipeline.notifier.sent] == ["alert"]


def test_device_health_block_current_behavior_not_written_to_decision_log(pipeline):
    """Pins current (buggy) behavior: the device-health block is not re-persisted (B-6) — see REFACTOR_NOTES.md.

    The response says ``skip`` with a ``device_no_water`` reason, but the
    ``decision_logs`` row keeps the engine's ``irrigate`` action and reasons.
    """
    _install_health_block(pipeline, pipeline.seed_cluster(soil=35.0))
    pipeline.mark()
    resp = _irrigate(pipeline)["response"]
    assert resp["action"] == "skip"
    assert "device_no_water" in [r["code"] for r in resp["reasons"]]
    rows = pipeline.db_rows()
    (log,) = rows["decision_logs"]
    assert log["action"] == "irrigate"
    assert log["actuated"] is False
    assert "device_no_water" not in json.dumps(log["payload_json"])
    assert pipeline.wiring.irrigator.calls == []
    assert [a["code"] for a in rows["activity_events"]] == ["decision_skip"]


def test_vacation_create_rejects_reversed_window(pipeline):
    """D5 (was B-8): ``POST /vacation`` validates ``starts_at < ends_at`` like ``PUT`` and the web forms.

    Reversed and empty windows are rejected with the shared 400 wording and nothing is stored.
    """
    for starts, ends in ((FROZEN_TS + 86400, FROZEN_TS), (FROZEN_TS, FROZEN_TS)):
        rejected = pipeline.call("POST", "/api/v1/vacation", {"starts_at": starts, "ends_at": ends})
        assert rejected["status"] == 400
        assert rejected["response"] == {"detail": "starts_at must be < ends_at"}
    created = pipeline.call("POST", "/api/v1/vacation", {"starts_at": FROZEN_TS, "ends_at": FROZEN_TS + 86400})
    assert created["status"] == 201
    window_id = created["response"]["id"]
    rejected = pipeline.call("PUT", f"/api/v1/vacation/{window_id}", {"ends_at": FROZEN_TS - 1})
    assert rejected["status"] == 400
    assert rejected["response"] == {"detail": "starts_at must be < ends_at"}
    assert [w["id"] for w in pipeline.call("GET", "/api/v1/vacation")["response"]["items"]] == [window_id]


def test_vacation_budget_projection(pipeline):
    """``services.vacation`` (web-only read model): per-cluster daily budget, capacity-configured clusters only."""
    from greenhouse_server.services.vacation import cluster_budgets, vacation_days

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

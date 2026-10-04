"""Seed a realistic greenhouse into the stubbed server app for TUI tests.

The TUI is exercised end-to-end against the real FastAPI app (in-memory
SQLite, fake devices — see ``tests/server/conftest.py``) so every client
method it calls is checked against the live route and response schema, not
against hand-written mocks.
"""

from __future__ import annotations

import math
import threading
import time

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from greenhouse_cli.client import IrrigationClient
from greenhouse_core.models import IrrigationEvent, SensorReading
from greenhouse_core.repository import IrrigationRepository

CLUSTERS = [
    {
        "name": "Living Room",
        "environment": "indoor",
        "location": "north window",
        "plants": [
            ("Monstera deliciosa", "tropical", 58.0),
            ("Nephrolepis exaltata", "fern", 38.0),
        ],
        "irrigator": True,
    },
    {
        "name": "Balcony",
        "environment": "outdoor",
        "location": None,
        "plants": [("Citrus limon", "fruit_tree", 22.0)],
        "irrigator": True,
    },
    {
        "name": "Desk",
        "environment": "indoor",
        "location": "office",
        "plants": [("Echeveria elegans", "succulent", 80.0), ("Opuntia microdasys", "cacti", None)],
        "irrigator": False,
    },
]


def _reading_series(sensor_id: int, target: float, now: int, hours: int = 48) -> list[SensorReading]:
    """A sawtooth drying curve ending at ``target`` with a watering bump every ~16h."""
    rows = []
    for i in range(hours * 2):
        ts = now - (hours * 2 - i) * 1800
        phase = (i % 32) / 32
        moisture = target + 12 * (1 - phase) - 6 + math.sin(i / 3) * 1.5
        rows.append(
            SensorReading(
                sensor_id=sensor_id,
                timestamp=ts,
                soil_moisture=round(max(0.0, min(100.0, moisture)), 1),
                temperature=round(21 + 3 * math.sin(i / 8), 1),
                env_humidity=round(55 + 10 * math.cos(i / 10), 1),
                light=int(max(0, 4000 * math.sin(math.pi * ((ts // 3600) % 24) / 24))),
                battery_state="high",
                water_warning=False,
            )
        )
    # Pin the latest reading exactly at the target so moods are predictable.
    rows[-1].soil_moisture = target
    rows[-1].timestamp = now - 300
    return rows


def seed_greenhouse(http: TestClient, engine) -> None:
    """Create clusters, plants, sensors, irrigators, readings, events, alerts, activity."""
    now = int(time.time())
    plant_no = sensor_no = 0
    with Session(engine) as session:
        for c_idx, cluster in enumerate(CLUSTERS, start=1):
            resp = http.post(
                "/api/v1/clusters",
                json={"name": cluster["name"], "environment": cluster["environment"], "location": cluster["location"]},
            )
            assert resp.status_code == 201, resp.text
            cid = resp.json()["id"]
            for species, category, moisture in cluster["plants"]:
                plant_no += 1
                resp = http.post(
                    f"/api/v1/clusters/{cid}/plants",
                    json={"species": species, "category": category, "water_needs": "medium"},
                )
                assert resp.status_code == 201, resp.text
                pid = resp.json()["id"]
                if moisture is None:
                    continue
                sensor_no += 1
                resp = http.post(
                    f"/api/v1/clusters/{cid}/sensors",
                    json={
                        "tuya_device_id": f"fake_sensor_{sensor_no:03d}",
                        "name": f"{species.split()[0]} probe",
                        "type": "tuya.tr301z",
                        "plant_id": pid,
                    },
                )
                assert resp.status_code == 201, resp.text
                session.add_all(_reading_series(resp.json()["id"], moisture, now))
            if cluster["irrigator"]:
                resp = http.post(
                    f"/api/v1/clusters/{cid}/irrigator",
                    json={
                        "tuya_device_id": f"fake_irrigator_{c_idx:03d}",
                        "name": f"{cluster['name']} pump",
                        "type": "rainpoint.ik10pw",
                    },
                )
                assert resp.status_code == 201, resp.text
                iid = resp.json()["id"]
                for hours_ago in (40, 24, 8):
                    session.add(
                        IrrigationEvent(
                            irrigator_id=iid,
                            timestamp=now - hours_ago * 3600,
                            action="start",
                            duration_minutes=3,
                            triggered_by="auto",
                            notes=None,
                        )
                    )
            http.put(
                f"/api/v1/clusters/{cid}/config", json={"mode": "smart", "duration_minutes": 3, "interval_hours": 12}
            )

        repo = IrrigationRepository(session)
        repo.upsert_alert(
            "leak:sensor:3",
            "leak",
            "leak_or_stuck_valve",
            "Possible leak on Balcony",
            "Moisture kept rising 30 min after the pump stopped.",
            severity="critical",
            entity_type="sensor",
            entity_id=3,
            cluster_id=2,
        )
        repo.upsert_alert(
            "battery:sensor:1",
            "health",
            "battery_low",
            "Low battery: Monstera probe",
            "Replace the batteries soon.",
            severity="warning",
            entity_type="sensor",
            entity_id=1,
            cluster_id=1,
        )
        for i, (severity, code, message) in enumerate(
            [
                ("info", "irrigation_started", "Living Room pump ran for 3 min"),
                ("warning", "sensor_stale", "Opuntia probe has not reported in 5h"),
                ("info", "sync_completed", "Synced 4 sensors, 12 new readings"),
            ]
        ):
            repo.add_activity_event(
                "irrigation", "cluster", code, message, entity_id=1, severity=severity, timestamp=now - i * 600
            )
        session.commit()


def tui_client_factory(http: TestClient, log: list | None = None):
    """Build ``IrrigationClient`` instances whose transport is the in-process app.

    The TUI legitimately issues requests concurrently from worker threads. The
    stubbed test app runs every request on ONE in-memory SQLite connection
    (``StaticPool``), which cannot serve concurrent statements — it raises
    ``sqlite3.InterfaceError`` or hands one request another's rows. The real
    server (file SQLite, ``QueuePool``) gives each request its own connection,
    so requests are serialized here to model that isolation.

    When ``log`` is given, every request the TUI makes is appended to it as
    ``(method, path, json_body, params)`` so tests can assert exact API calls.
    """
    lock = _app_locks.setdefault(id(http), threading.Lock())

    def factory(token: str | None) -> IrrigationClient:
        client = IrrigationClient(base_url="http://testserver", token=token or "")
        client.http = http
        original = client._request

        def serialized(method, path, **kwargs):
            if log is not None:
                log.append((method, path, kwargs.get("json"), kwargs.get("params")))
            with lock:
                return original(method, path, **kwargs)

        client._request = serialized
        return client

    return factory


_app_locks: dict[int, threading.Lock] = {}


def writes(log: list) -> list[tuple]:
    """Only the mutating requests from a recorded log (drops GETs)."""
    return [(m, p, body) for m, p, body, _ in log if m != "GET"]

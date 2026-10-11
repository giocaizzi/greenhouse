"""Functional tests for POST /api/v1/bulk/stop-all."""

from http import HTTPStatus

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from greenhouse_core.models import IrrigationEvent

_SEEDED = (
    ("Cluster A", "fake_irrigator_bulk_001", "Irrigator A1"),
    ("Cluster B", "fake_irrigator_bulk_002", "Irrigator B1"),
)


def _seed_irrigators(client: TestClient) -> int:
    """Seed two clusters each with one irrigator; return how many irrigators were seeded."""
    for cluster_id, (cluster_name, device_id, name) in enumerate(_SEEDED, start=1):
        client.post("/api/v1/clusters", json={"name": cluster_name})
        client.post(
            f"/api/v1/clusters/{cluster_id}/irrigator",
            json={"tuya_device_id": device_id, "name": name, "type": "rainpoint.ik10pw"},
        )
    return len(_SEEDED)


class TestBulkStopAll:
    """Tests for bulk stop all."""

    def test_stop_all_returns_correct_count(self, client: TestClient) -> None:
        """Stop-all with two irrigators must report stopped=2."""
        seeded = _seed_irrigators(client)
        resp = client.post("/api/v1/bulk/stop-all")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["stopped"] == seeded
        assert data["errors"] == []

    def test_stop_all_no_irrigators(self, client: TestClient) -> None:
        """Stop-all with zero irrigators returns stopped=0 and no errors."""
        resp = client.post("/api/v1/bulk/stop-all")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["stopped"] == 0
        assert data["errors"] == []

    def test_stop_all_logs_irrigation_events(self, app: FastAPI, client: TestClient) -> None:
        """Each irrigator must have an IrrigationEvent with triggered_by='emergency'."""
        seeded = _seed_irrigators(client)
        client.post("/api/v1/bulk/stop-all")

        # Inspect the database directly via the app's session factory
        session = app.state.session_factory()
        try:
            events = list(session.scalars(select(IrrigationEvent).where(IrrigationEvent.triggered_by == "emergency")))
            assert len(events) == seeded
            for event in events:
                assert event.action == "stop"
                assert event.triggered_by == "emergency"
                assert event.notes == "kill switch"
        finally:
            session.close()

    def test_refused_stop_is_reported_and_not_recorded(self, app: FastAPI, client: TestClient) -> None:
        """A device answering ``(False, msg)`` is an error: not counted, no ``stop`` event."""
        _seed_irrigators(client)
        app.state.fake_devices.irrigator.stop_result = (False, "device asleep")
        data = client.post("/api/v1/bulk/stop-all").json()
        assert data == {
            "stopped": 0,
            "errors": [
                "irrigator 1 (Irrigator A1): stop failed: device asleep",
                "irrigator 2 (Irrigator B1): stop failed: device asleep",
            ],
        }
        session = app.state.session_factory()
        try:
            assert list(session.scalars(select(IrrigationEvent))) == []
        finally:
            session.close()

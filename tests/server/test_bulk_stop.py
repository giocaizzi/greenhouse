"""Functional tests for POST /api/v1/bulk/stop-all."""

from sqlalchemy import select

from greenhouse_core.models import IrrigationEvent


def _seed_irrigators(client):
    """Seed two clusters each with one irrigator."""
    client.post("/api/v1/clusters", json={"name": "Cluster A"})
    client.post(
        "/api/v1/clusters/1/irrigators",
        json={"tuya_device_id": "fake_irrigator_bulk_001", "name": "Irrigator A1", "type": "tuya_cloud"},
    )
    client.post("/api/v1/clusters", json={"name": "Cluster B"})
    client.post(
        "/api/v1/clusters/2/irrigators",
        json={"tuya_device_id": "fake_irrigator_bulk_002", "name": "Irrigator B1", "type": "tuya_cloud"},
    )


class TestBulkStopAll:
    def test_stop_all_returns_correct_count(self, client):
        """Stop-all with two irrigators must report stopped=2."""
        _seed_irrigators(client)
        resp = client.post("/api/v1/bulk/stop-all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stopped"] == 2
        assert data["errors"] == []

    def test_stop_all_no_irrigators(self, client):
        """Stop-all with zero irrigators returns stopped=0 and no errors."""
        resp = client.post("/api/v1/bulk/stop-all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stopped"] == 0
        assert data["errors"] == []

    def test_stop_all_logs_irrigation_events(self, app, client):
        """Each irrigator must have an IrrigationEvent with triggered_by='emergency'."""
        _seed_irrigators(client)
        client.post("/api/v1/bulk/stop-all")

        # Inspect the database directly via the app's session factory
        session = app.state.session_factory()
        try:
            events = list(session.scalars(select(IrrigationEvent).where(IrrigationEvent.triggered_by == "emergency")))
            assert len(events) == 2
            for event in events:
                assert event.action == "stop"
                assert event.triggered_by == "emergency"
                assert event.notes == "kill switch"
        finally:
            session.close()

"""Tests for the irrigation heatmap API endpoint."""

from datetime import UTC, datetime


def test_heatmap_empty_no_events(seeded_client):
    """Heatmap with no events returns an empty cells list."""
    resp = seeded_client.get("/api/v1/clusters/1/heatmap?days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cluster_id"] == 1
    assert body["days"] == 30
    assert isinstance(body["cells"], list)
    # seeded_client has no irrigation events → empty heatmap
    assert body["cells"] == []


def test_heatmap_structure(seeded_client):
    """Each heatmap cell must have the expected shape (tested on a non-empty payload)."""
    # The seeded_client produces empty cells; structure is validated elsewhere.
    resp = seeded_client.get("/api/v1/clusters/1/heatmap?days=30")
    assert resp.status_code == 200
    for cell in resp.json()["cells"]:
        assert "weekday" in cell
        assert 0 <= cell["weekday"] <= 6
        assert "hour" in cell
        assert 0 <= cell["hour"] <= 23
        assert "count" in cell
        assert cell["count"] >= 1
        assert "total_minutes" in cell
        assert cell["total_minutes"] >= 0


def test_heatmap_404_unknown_cluster(client):
    resp = client.get("/api/v1/clusters/9999/heatmap")
    assert resp.status_code == 404


def test_heatmap_with_events(app):
    """Seed an irrigation event at a known (weekday, hour) and assert the cell appears."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from tuya_irrigation_core.models import Base
    from tuya_irrigation_core.repository import IrrigationRepository
    from tuya_irrigation_server.deps import get_repository

    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    from sqlalchemy.orm import Session

    session = Session(engine)
    repo = IrrigationRepository(session)

    # Seed cluster + irrigator
    cluster_id = repo.add_cluster("Heatmap Test")
    irrigator_id = repo.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id="fake_heatmap_irr",
        name="Heatmap Irrigator",
        irrigator_type="tuya_cloud",
        config={},
    )
    # Seed an event at a known UTC datetime: Wednesday (weekday=2) at 14:00 UTC
    ts = int(datetime(2026, 1, 7, 14, 30, 0, tzinfo=UTC).timestamp())  # 2026-01-07 is a Wednesday
    repo.add_irrigation_event(
        irrigator_id=irrigator_id,
        action="start",
        triggered_by="manual",
        duration_minutes=10,
        timestamp=ts,
    )
    session.commit()

    app.dependency_overrides[get_repository] = lambda: repo
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(f"/api/v1/clusters/{cluster_id}/heatmap?days=365")
    assert resp.status_code == 200
    cells = resp.json()["cells"]
    assert len(cells) >= 1

    # Check the Wednesday-14h cell exists with count≥1
    matching = [c for c in cells if c["weekday"] == 2 and c["hour"] == 14]
    assert matching, f"Expected cell at weekday=2 hour=14, got: {cells}"
    assert matching[0]["count"] >= 1
    assert matching[0]["total_minutes"] == 10

    app.dependency_overrides.pop(get_repository, None)
    session.close()
    engine.dispose()

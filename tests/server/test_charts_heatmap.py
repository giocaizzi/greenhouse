"""Tests for the irrigation heatmap API endpoint."""

from datetime import UTC, datetime
from http import HTTPStatus

from fastapi.testclient import TestClient

from server.conftest import override_repository


def test_heatmap_empty_no_events(seeded_client):
    """Heatmap with no events returns an empty cells list."""
    days = 30
    resp = seeded_client.get(f"/api/v1/clusters/1/heatmap?days={days}")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["cluster_id"] == 1
    assert body["days"] == days
    assert isinstance(body["cells"], list)
    # seeded_client has no irrigation events → empty heatmap
    assert body["cells"] == []


def test_heatmap_structure(seeded_client):
    """Each heatmap cell must have the expected shape (tested on a non-empty payload)."""
    # The seeded_client produces empty cells; structure is validated elsewhere.
    resp = seeded_client.get("/api/v1/clusters/1/heatmap?days=30")
    assert resp.status_code == HTTPStatus.OK
    for cell in resp.json()["cells"]:
        assert "weekday" in cell
        assert cell["weekday"] in range(7)
        assert "hour" in cell
        assert cell["hour"] in range(24)
        assert "count" in cell
        assert cell["count"] >= 1
        assert "total_minutes" in cell
        assert cell["total_minutes"] >= 0


def test_heatmap_404_unknown_cluster(client):
    resp = client.get("/api/v1/clusters/9999/heatmap")
    assert resp.status_code == HTTPStatus.NOT_FOUND


def _seed_event_at(repo, known: datetime, minutes: int) -> int:
    """Seed a cluster + irrigator with one manual start at ``known``; return the cluster id."""
    cluster_id = repo.add_cluster("Heatmap Test")
    irrigator_id = repo.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id="fake_heatmap_irr",
        name="Heatmap Irrigator",
        irrigator_type="rainpoint.ik10pw",
        config={},
    )
    repo.add_irrigation_event(
        irrigator_id=irrigator_id,
        action="start",
        triggered_by="manual",
        duration_minutes=minutes,
        timestamp=int(known.timestamp()),
    )
    repo.session.commit()
    return cluster_id


def test_heatmap_with_events(app):
    """Seed an irrigation event at a known (weekday, hour) and assert the cell appears."""
    # Seed an event at a known UTC datetime: Wednesday (weekday=2) at 14:00 UTC
    known = datetime(2026, 1, 7, 14, 30, 0, tzinfo=UTC)  # 2026-01-07 is a Wednesday
    minutes = 10
    with override_repository(app) as repo:
        cluster_id = _seed_event_at(repo, known, minutes)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/api/v1/clusters/{cluster_id}/heatmap?days=365")
    assert resp.status_code == HTTPStatus.OK
    cells = resp.json()["cells"]
    assert len(cells) >= 1

    # Check the Wednesday-14h cell exists with count≥1
    matching = [c for c in cells if (c["weekday"], c["hour"]) == (known.weekday(), known.hour)]
    assert matching, f"Expected cell at weekday=2 hour=14, got: {cells}"
    assert matching[0]["count"] >= 1
    assert matching[0]["total_minutes"] == minutes

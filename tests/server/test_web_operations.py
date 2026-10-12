"""Web action endpoints (irrigate, monitor, check, sync, plants/sync)."""

from http import HTTPStatus

from fastapi.testclient import TestClient


def test_irrigate_dry_run_returns_decision_panel(seeded_client: TestClient) -> None:
    """Irrigate dry run returns decision panel."""
    resp = seeded_client.post(
        "/clusters/1/irrigate",
        data={"dry_run": "on", "no_sync": "on"},
    )
    assert resp.status_code == HTTPStatus.OK
    assert "Irrigation decision" in resp.text


def test_monitor_cluster_returns_panel(seeded_client: TestClient) -> None:
    """Monitor cluster returns panel."""
    resp = seeded_client.get("/clusters/1/monitor")
    assert resp.status_code == HTTPStatus.OK
    assert "Monitor" in resp.text
    assert "Test Cluster" in resp.text


def test_check_single_cluster(seeded_client: TestClient) -> None:
    """Check single cluster."""
    resp = seeded_client.post("/clusters/1/check")
    assert resp.status_code == HTTPStatus.OK
    assert "Check result" in resp.text


def test_check_all_clusters(seeded_client: TestClient) -> None:
    """Check all clusters."""
    resp = seeded_client.post("/check")
    assert resp.status_code == HTTPStatus.OK
    assert "Check result" in resp.text


def test_sync_all_sensors(client: TestClient) -> None:
    # No cloud configured in tests → should return result panel without crashing
    """Sync all sensors."""
    resp = client.post("/sync", data={"hours": "24"})
    assert resp.status_code == HTTPStatus.OK
    assert "Sync result" in resp.text


def test_plants_sync(seeded_client: TestClient) -> None:
    """Plants sync."""
    resp = seeded_client.post("/plants/sync", data={})
    assert resp.status_code == HTTPStatus.OK
    assert "Sync result" in resp.text

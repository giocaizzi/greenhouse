"""Characterization tests for route branches consistency group W2 restructures (plan §0.4 G.6).

Each test pins what a route does today on a line the server suite did not reach: the manual
``POST /sync`` commit, the chart-data 400/404 branches, ``create_app`` with an explicit plant-DB
path, and the stats endpoint on a cluster without an irrigator.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import greenhouse_core
from greenhouse_server.app import create_app
from greenhouse_server.config import Settings
from greenhouse_server.deps import RepoDep, get_sync_service


class _RecordingSync:
    """Stand-in SyncService: writes one row through the request's repository, never commits."""

    def __init__(self, repo) -> None:
        self._repo = repo
        self.hours: list[int] = []

    def sync_all_sensors(self, hours: int = 24) -> dict:
        self.hours.append(hours)
        self._repo.add_cluster(name="written-by-sync")
        return {"total_synced": 3, "total_new": 2, "total_live": 1, "errors": ["sensor 9: boom"]}


def test_sync_route_commits_the_sync_writes_and_maps_the_stats(app, client):
    """``POST /api/v1/sync`` passes ``hours``, commits what the sync wrote and returns the four counters."""
    calls: list[_RecordingSync] = []

    def _fake(repo: RepoDep) -> _RecordingSync:
        svc = _RecordingSync(repo)
        calls.append(svc)
        return svc

    app.dependency_overrides[get_sync_service] = _fake

    resp = client.post("/api/v1/sync", json={"hours": 6})

    assert resp.status_code == 200
    assert resp.json() == {"total_synced": 3, "total_new": 2, "total_live": 1, "errors": ["sensor 9: boom"]}
    assert [svc.hours for svc in calls] == [[6]]
    # A later request (fresh session) sees the row: the route committed it.
    assert [c["name"] for c in client.get("/api/v1/clusters").json()] == ["written-by-sync"]


def test_cluster_chart_data_unsupported_metric_is_400(client):
    resp = client.get("/api/v1/clusters/1/chart-data?metric=bogus")
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Unsupported metric: bogus"}


def test_cluster_chart_data_unknown_cluster_is_404(client):
    resp = client.get("/api/v1/clusters/999/chart-data")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Cluster not found"}


def test_plant_chart_data_unknown_plant_is_404(client):
    resp = client.get("/api/v1/plants/999/chart-data")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Plant not found"}


def test_create_app_loads_the_plant_db_from_settings_path(clean_env):
    """``Settings.plant_db_path`` set → ``app.state.plant_db`` reads that file."""
    bundled = Path(greenhouse_core.__file__).parent / "data" / "plant_database.json"
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    try:
        app = create_app(
            Settings(db_url="sqlite://", enable_scheduler=False, auth_enabled=False, plant_db_path=str(bundled)),
            engine=engine,
        )
        assert Path(app.state.plant_db.db_path) == bundled
    finally:
        engine.dispose()


def test_stats_without_irrigator_current_behavior_500(client):
    """B-N2 pinned: a cluster with no irrigator makes ``GET /clusters/{id}/stats`` fail with HTTP 500."""
    cid = client.post("/api/v1/clusters", json={"name": "Sensors only"}).json()["id"]

    resp = client.get(f"/api/v1/clusters/{cid}/stats")

    assert resp.status_code == 500

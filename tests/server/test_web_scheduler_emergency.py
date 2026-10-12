"""Web scheduler pause/resume and emergency stop button."""

from http import HTTPStatus

from fastapi.testclient import TestClient

from greenhouse_server.scheduler import scheduler as bg_scheduler


def test_scheduler_page_shows_emergency_stop_button(client: TestClient) -> None:
    """Scheduler page shows emergency stop button."""
    resp = client.get("/scheduler")
    assert resp.status_code == HTTPStatus.OK
    assert 'hx-post="/bulk/stop-all"' in resp.text
    assert "Stop all irrigators" in resp.text


def test_dashboard_empty_omits_emergency_stop(client: TestClient) -> None:
    """Dashboard empty omits emergency stop."""
    resp = client.get("/")
    assert resp.status_code == HTTPStatus.OK
    # no clusters seeded → empty state, no Emergency stop control rendered
    assert 'hx-post="/bulk/stop-all"' not in resp.text


def test_bulk_stop_all_when_no_irrigators(client: TestClient) -> None:
    """Bulk stop all when no irrigators."""
    resp = client.post("/bulk/stop-all")
    assert resp.status_code == HTTPStatus.OK
    assert "Stopped 0 irrigators" in resp.text


def test_bulk_stop_all_stops_irrigators(seeded_client: TestClient) -> None:
    """Bulk stop all stops irrigators."""
    resp = seeded_client.post("/bulk/stop-all")
    assert resp.status_code == HTTPStatus.OK
    assert "Stopped 1 irrigator" in resp.text


def _check_all_paused(client: TestClient) -> bool:
    jobs = client.get("/api/v1/scheduler/jobs").json()
    paused: bool = next(j for j in jobs if j["id"] == "check_all")["paused"]
    return paused


def test_scheduler_pause_works_when_not_running(client: TestClient) -> None:
    """Matches the API: pause persists + pauses the registered job, no 503."""
    resp = client.post("/scheduler/pause", follow_redirects=False)
    assert resp.status_code == HTTPStatus.SEE_OTHER
    assert resp.headers["location"] == "/scheduler"
    assert client.get("/api/v1/preferences").json()["scheduler_paused"] is True
    assert _check_all_paused(client) is True


def test_scheduler_resume_works_when_not_running(client: TestClient) -> None:
    """Scheduler resume works when not running."""
    client.post("/scheduler/pause", follow_redirects=False)
    resp = client.post("/scheduler/resume", follow_redirects=False)
    assert resp.status_code == HTTPStatus.SEE_OTHER
    assert client.get("/api/v1/preferences").json()["scheduler_paused"] is False
    assert _check_all_paused(client) is False


def test_scheduler_page_offers_pause_when_not_running(client: TestClient) -> None:
    """Scheduler page offers pause when not running."""
    resp = client.get("/scheduler")
    assert 'action="/scheduler/pause"' in resp.text
    client.post("/scheduler/pause", follow_redirects=False)
    resp = client.get("/scheduler")
    assert 'action="/scheduler/resume"' in resp.text
    assert "check_all paused" in resp.text


def test_scheduler_web_pause_404_when_check_all_missing(client: TestClient) -> None:
    """Scheduler web pause 404 when check all missing."""
    bg_scheduler.remove_job("check_all")
    assert client.post("/scheduler/pause", follow_redirects=False).status_code == HTTPStatus.NOT_FOUND
    assert client.get("/api/v1/preferences").json()["scheduler_paused"] is False

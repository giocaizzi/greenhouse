"""Web scheduler pause/resume and emergency stop button."""


def test_scheduler_page_shows_emergency_stop_button(client):
    resp = client.get("/scheduler")
    assert resp.status_code == 200
    assert 'hx-post="/bulk/stop-all"' in resp.text
    assert "Stop all irrigators" in resp.text


def test_dashboard_shows_emergency_stop_when_clusters_exist(seeded_client):
    resp = seeded_client.get("/")
    assert resp.status_code == 200
    assert 'hx-post="/bulk/stop-all"' in resp.text


def test_dashboard_empty_omits_emergency_stop(client):
    resp = client.get("/")
    assert resp.status_code == 200
    # no clusters seeded → empty state, no Emergency stop control rendered
    assert 'hx-post="/bulk/stop-all"' not in resp.text


def test_bulk_stop_all_when_no_irrigators(client):
    resp = client.post("/bulk/stop-all")
    assert resp.status_code == 200
    assert "Stopped 0 irrigators" in resp.text


def test_bulk_stop_all_stops_irrigators(seeded_client):
    resp = seeded_client.post("/bulk/stop-all")
    assert resp.status_code == 200
    assert "Stopped 1 irrigator" in resp.text


def test_scheduler_pause_503_when_not_running(client):
    # Scheduler disabled in tests, so pause should 503
    resp = client.post("/scheduler/pause")
    assert resp.status_code == 503


def test_scheduler_resume_503_when_not_running(client):
    resp = client.post("/scheduler/resume")
    assert resp.status_code == 503

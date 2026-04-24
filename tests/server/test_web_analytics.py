"""Web history/stats/export/learn/scheduler pages."""


def test_history_page_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/history?hours=24&limit=10")
    assert resp.status_code == 200
    assert "History" in resp.text
    assert "Test Sensor" in resp.text
    assert "Test Irrigator" in resp.text


def test_history_404(client):
    resp = client.get("/clusters/9999/history")
    assert resp.status_code == 404


def test_stats_page_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/stats?days=7")
    assert resp.status_code == 200
    assert "Stats" in resp.text


def test_stats_export_returns_csv(seeded_client):
    resp = seeded_client.get("/clusters/1/stats/export?days=7")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    # CSV header row
    assert "timestamp" in resp.text.splitlines()[0]


def test_learn_page_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/learn")
    assert resp.status_code == 200
    assert "Learning report" in resp.text


def test_scheduler_page_renders(client):
    resp = client.get("/scheduler")
    assert resp.status_code == 200
    # scheduler disabled in tests → "not running" message
    assert "Scheduler" in resp.text


def test_scheduler_delete_missing_job_returns_503(client):
    # scheduler is not running in tests → 503
    resp = client.post("/scheduler/jobs/missing/delete")
    assert resp.status_code == 503

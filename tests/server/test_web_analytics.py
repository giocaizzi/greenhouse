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
    assert "Insights" in resp.text


def test_scheduler_page_renders(client):
    resp = client.get("/scheduler")
    assert resp.status_code == 200
    # scheduler disabled in tests → "not running" message
    assert "Scheduler" in resp.text


def test_scheduler_delete_missing_job_returns_503(client):
    # scheduler is not running in tests → 503
    resp = client.post("/scheduler/jobs/missing/delete")
    assert resp.status_code == 503


def _job_row(html: str, job_id: str) -> str:
    """Return the `<tr>` of the scheduler table that holds ``job_id``."""
    marker = f'<td data-th="ID"><code>{job_id}</code></td>'
    idx = html.index(marker)
    return html[html.rindex("<tr>", 0, idx) : html.index("</tr>", idx)]


def test_scheduler_page_marks_paused_check_all(client, running_scheduler):
    """Regression: the page built job dicts without `paused`, so the row badge
    always read "active" even while check_all was paused."""
    assert client.post("/scheduler/pause", follow_redirects=False).status_code == 303
    resp = client.get("/scheduler")
    assert resp.status_code == 200
    assert "check_all paused" in resp.text  # header badge
    assert 'badge-warning">paused' in _job_row(resp.text, "check_all")
    assert 'badge-ok">active' in _job_row(resp.text, "sensor_sync")


def test_scheduler_page_resume_restores_active(client, running_scheduler):
    client.post("/scheduler/pause", follow_redirects=False)
    assert client.post("/scheduler/resume", follow_redirects=False).status_code == 303
    resp = client.get("/scheduler")
    assert 'badge-ok">active' in _job_row(resp.text, "check_all")
    assert client.get("/api/v1/preferences").json()["scheduler_paused"] is False


def test_scheduler_web_delete_refuses_core_job(client, running_scheduler):
    resp = client.post("/scheduler/jobs/sensor_anomaly/delete")
    assert resp.status_code == 409
    assert running_scheduler.get_job("sensor_anomaly") is not None


def test_scheduler_web_delete_adhoc_job(client, running_scheduler):
    running_scheduler.add_job(lambda: None, "date", run_date="2099-01-01", id="leak-check-1-123")
    assert client.post("/scheduler/jobs/leak-check-1-123/delete").status_code == 200
    assert client.post("/scheduler/jobs/leak-check-1-123/delete").status_code == 404


def test_scheduler_page_hides_delete_for_core_jobs(client, running_scheduler):
    running_scheduler.add_job(lambda: None, "date", run_date="2099-01-01", id="leak-check-1-123", name="Leak")
    resp = client.get("/scheduler")
    assert 'hx-post="/scheduler/jobs/leak-check-1-123/delete"' in resp.text
    for job_id in ("check_all", "sensor_sync", "sensor_anomaly", "plant_health_snapshot", "device_health_monitor"):
        assert f'hx-post="/scheduler/jobs/{job_id}/delete"' not in resp.text

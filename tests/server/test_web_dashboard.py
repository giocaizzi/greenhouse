"""Web dashboard smoke tests."""


def test_dashboard_empty_state(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "No clusters yet" in resp.text
    assert "Create cluster" in resp.text


def test_dashboard_lists_seeded_cluster(seeded_client):
    resp = seeded_client.get("/")
    assert resp.status_code == 200
    assert "Test Cluster" in resp.text
    assert 'hx-get="/clusters/1/status-fragment"' in resp.text


def test_dashboard_includes_chart_assets(client):
    resp = client.get("/")
    assert "chart.js" in resp.text.lower()
    assert "htmx" in resp.text.lower()
    assert "/static/app.css" in resp.text

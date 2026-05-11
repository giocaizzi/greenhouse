"""Web fragment endpoints (health badge, polled partials)."""


def test_health_page_renders(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "System Health" in resp.text
    assert "Scheduler" in resp.text


def test_health_badge_fragment(client):
    resp = client.get("/health/badge")
    assert resp.status_code == 200
    assert "scheduler" in resp.text.lower()


def test_static_assets_served(client):
    resp_css = client.get("/static/app.css")
    assert resp_css.status_code == 200
    assert "cluster-grid" in resp_css.text

    resp_js = client.get("/static/app.js")
    assert resp_js.status_code == 200
    assert "renderIrrigationChart" in resp_js.text

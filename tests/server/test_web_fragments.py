"""Web fragment endpoints (health badge, polled partials)."""

from http import HTTPStatus

from fastapi.testclient import TestClient


def test_health_page_renders(client: TestClient) -> None:
    """Health page renders."""
    resp = client.get("/health")
    assert resp.status_code == HTTPStatus.OK
    assert "System Health" in resp.text
    assert "Scheduler" in resp.text


def test_static_assets_served(client: TestClient) -> None:
    """Static assets served."""
    resp_css = client.get("/static/app.css")
    assert resp_css.status_code == HTTPStatus.OK
    assert "cluster-grid" in resp_css.text

    resp_js = client.get("/static/app.js")
    assert resp_js.status_code == HTTPStatus.OK
    assert "renderIrrigationChart" in resp_js.text

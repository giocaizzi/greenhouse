"""Web fragment endpoints (health badge, polled partials)."""

from http import HTTPStatus

from fastapi.testclient import TestClient


def test_static_assets_served(client: TestClient) -> None:
    """Static assets served."""
    resp_css = client.get("/static/app.css")
    assert resp_css.status_code == HTTPStatus.OK
    assert "cluster-grid" in resp_css.text

    resp_js = client.get("/static/app.js")
    assert resp_js.status_code == HTTPStatus.OK
    assert "renderIrrigationChart" in resp_js.text

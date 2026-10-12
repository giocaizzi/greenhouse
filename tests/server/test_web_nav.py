"""Primary navigation: More overflow menu, footer health link and version, bell, theme toggle."""

from http import HTTPStatus

from fastapi.testclient import TestClient

from greenhouse_server.web.context import APP_VERSION


def test_base_layout_chrome(client: TestClient) -> None:
    """The base layout carries the More menu, bell, footer badge and version, and theme toggle."""
    resp = client.get("/")
    assert resp.status_code == HTTPStatus.OK
    # "More" menu items
    assert 'href="/vacation"' in resp.text
    assert 'href="/health"' in resp.text
    assert 'href="/quality"' in resp.text
    assert 'href="/preferences"' in resp.text
    # The bell should be a real link to /alerts, not a button with inline onclick
    assert "window.location.href='/alerts'" not in resp.text
    assert 'href="/alerts"' in resp.text
    # Health badge wrapper is an anchor pointing at /health
    assert 'id="health-badge"' in resp.text
    # Dark mode toggle and its persistence snippet
    assert 'id="theme-toggle"' in resp.text
    assert "localStorage.getItem('theme')" in resp.text
    # Footer app version
    assert f"v{APP_VERSION}" in resp.text
    assert 'title="App version"' in resp.text

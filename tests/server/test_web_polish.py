"""Polish: HX-aware error partial, dark-mode toggle, API still returns JSON."""


def test_html_404_returns_full_error_page(client):
    resp = client.get("/clusters/9999")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "Error 404" in resp.text
    assert "Cluster not found" in resp.text
    # Non-HX requests get the base layout wrapper (nav, title), not a bare partial.
    assert "<!DOCTYPE html>" in resp.text
    assert "<title>Error 404" in resp.text
    assert '<a href="/scheduler">Scheduler</a>' in resp.text  # nav present


def test_hx_404_returns_bare_partial(client):
    resp = client.get("/clusters/9999", headers={"HX-Request": "true"})
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "Error 404" in resp.text
    # HX swaps into an existing target — no layout wrapper.
    assert "<!DOCTYPE html>" not in resp.text
    assert "<html" not in resp.text.lower()


def test_api_404_still_returns_json(client):
    resp = client.get("/api/v1/clusters/9999")
    assert resp.status_code == 404
    assert "application/json" in resp.headers["content-type"]
    body = resp.json()
    assert body["detail"] == "Cluster not found"


def test_dark_mode_toggle_present(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="theme-toggle"' in resp.text
    # persistence snippet
    assert "localStorage.getItem('theme')" in resp.text


def test_api_422_still_returns_json(client):
    # POST /api/v1/clusters without a body → 422 JSON
    resp = client.post("/api/v1/clusters", json={})
    assert resp.status_code == 422
    assert "application/json" in resp.headers["content-type"]


def test_html_422_returns_error_partial(client):
    # Missing required name field on the web form → error partial
    resp = client.post("/clusters", data={})
    assert resp.status_code == 422
    assert "text/html" in resp.headers["content-type"]
    assert "Error 422" in resp.text


def test_footer_shows_app_version(client):
    from greenhouse_server.web.context import APP_VERSION

    resp = client.get("/")
    assert resp.status_code == 200
    assert f"v{APP_VERSION}" in resp.text
    assert 'title="App version"' in resp.text


def test_closed_command_palette_is_hidden(client):
    # Regression: `.cmdk` sets display:flex (to center the panel when open),
    # which—being an author rule—overrides the UA `dialog:not([open])` reset and
    # leaves the CLOSED palette as a full-viewport z-index:300 layer that renders
    # over the page and swallows taps meant for the mobile bottom nav (issue #74).
    # The guard re-asserts the hidden state when the dialog has no `open` attr.
    resp = client.get("/static/app.css")
    assert resp.status_code == 200
    assert ".cmdk:not([open]) { display: none; }" in resp.text

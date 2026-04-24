"""Polish: HX-aware error partial, dark-mode toggle, API still returns JSON."""


def test_html_404_returns_error_partial(client):
    resp = client.get("/clusters/9999")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "Error 404" in resp.text
    assert "Cluster not found" in resp.text


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

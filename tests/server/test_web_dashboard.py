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
    # Dashboard polls the richer card-fragment for plant↔sensor↔irrigator rollups.
    assert 'hx-get="/clusters/1/card-fragment"' in resp.text


def test_card_fragment_renders_plant_sensor_link(seeded_client):
    """The new card fragment shows the plant species + linked sensor with the ↳ arrow."""
    resp = seeded_client.get("/clusters/1/card-fragment")
    assert resp.status_code == 200
    assert "Monstera deliciosa" in resp.text
    assert "Test Sensor" in resp.text
    # The ↳ arrow connects the plant to its sensor in the rollup.
    assert "↳" in resp.text


def test_card_fragment_404_for_missing_cluster(client):
    resp = client.get("/clusters/9999/card-fragment")
    assert resp.status_code == 404


def test_dashboard_includes_chart_assets(client):
    resp = client.get("/")
    text_lc = resp.text.lower()
    assert "chart.js" in text_lc
    assert "htmx" in text_lc
    assert "/static/app.css" in resp.text
    # Chart.js v4 time-scale axes need a date adapter — without it, no chart renders.
    assert "chartjs-adapter-date-fns" in text_lc, (
        "Chart.js requires a date adapter; see libs/.../web/templates/_base.html"
    )
    assert "chartjs-plugin-annotation" in text_lc

"""Chart-data API endpoints + per-plant dashboard web page."""


def test_single_plant_api(seeded_client):
    resp = seeded_client.get("/api/v1/plants/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["species"] == "Monstera deliciosa"
    assert body["cluster_id"] == 1


def test_single_plant_api_404(client):
    resp = client.get("/api/v1/plants/9999")
    assert resp.status_code == 404


def test_plant_chart_data_shape(seeded_client):
    resp = seeded_client.get("/api/v1/plants/1/chart-data?hours=24&metric=soil_moisture")
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "soil_moisture"
    assert body["hours"] == 24
    assert isinstance(body["datasets"], list)
    assert isinstance(body["events"], list)
    # water_needs=medium → threshold from plant_db mapping (45-65)
    assert body["threshold"]["min"] == 45.0
    assert body["threshold"]["max"] == 65.0
    assert body["threshold"]["source"] == "water_needs:medium"


def test_plant_chart_data_temperature_band(seeded_client):
    resp = seeded_client.get("/api/v1/plants/1/chart-data?metric=temperature")
    assert resp.status_code == 200
    body = resp.json()
    # seeded plant has ideal_temp_min=18, ideal_temp_max=27
    assert body["threshold"]["min"] == 18.0
    assert body["threshold"]["max"] == 27.0


def test_plant_chart_data_unsupported_metric(seeded_client):
    resp = seeded_client.get("/api/v1/plants/1/chart-data?metric=co2")
    assert resp.status_code == 400


def test_cluster_chart_data(seeded_client):
    resp = seeded_client.get("/api/v1/clusters/1/chart-data?hours=24&metric=soil_moisture")
    assert resp.status_code == 200
    body = resp.json()
    # cluster scope with default soil moisture threshold
    assert body["threshold"]["source"] == "default"
    assert body["threshold"]["min"] == 45.0


def test_plant_dashboard_page_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/plants/1")
    assert resp.status_code == 200
    assert "Monstera deliciosa" in resp.text
    assert "Plant care" in resp.text
    assert "Latest readings" in resp.text
    assert "Charts" in resp.text
    # Chart.js CDN + helper available
    assert "renderIrrigationChart" in resp.text
    # Inline JSON payload for at least one chart
    assert 'id="chart-data-soil_moisture"' in resp.text


def test_plant_dashboard_wrong_cluster_404(seeded_client):
    # plant 1 belongs to cluster 1; fetching it as if in cluster 99 should 404
    resp = seeded_client.get("/clusters/99/plants/1")
    assert resp.status_code == 404


def test_plant_chart_fragment(seeded_client):
    resp = seeded_client.get("/clusters/1/plants/1/chart-fragment?metric=temperature&hours=168")
    assert resp.status_code == 200
    assert "canvas" in resp.text.lower()
    assert 'id="chart-data-temperature"' in resp.text


def test_plant_chart_fragment_invalid_metric(seeded_client):
    resp = seeded_client.get("/clusters/1/plants/1/chart-fragment?metric=bogus")
    assert resp.status_code == 400

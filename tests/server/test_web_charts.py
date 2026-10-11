"""Chart-data API endpoints + per-plant dashboard web page."""

from http import HTTPStatus
from types import SimpleNamespace

from greenhouse_core.constants import DEFAULT_SOIL_MOISTURE_MIN
from greenhouse_server.services.charts import _threshold_for_plant

CHART_HOURS = 24
MEDIUM_WATER_NEEDS_BAND = (45.0, 65.0)  # plant_db soil band for water_needs=medium
SEEDED_TEMP_BAND = (18.0, 27.0)  # the seeded plant's ideal_temp_min / ideal_temp_max


def test_single_plant_api(seeded_client):
    resp = seeded_client.get("/api/v1/plants/1")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["species"] == "Monstera deliciosa"
    assert body["cluster_id"] == 1


def test_single_plant_api_404(client):
    resp = client.get("/api/v1/plants/9999")
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_plant_chart_data_shape(seeded_client):
    resp = seeded_client.get(f"/api/v1/plants/1/chart-data?hours={CHART_HOURS}&metric=soil_moisture")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["metric"] == "soil_moisture"
    assert body["hours"] == CHART_HOURS
    assert isinstance(body["datasets"], list)
    assert isinstance(body["events"], list)
    # water_needs=medium → threshold from plant_db mapping (45-65)
    assert (body["threshold"]["min"], body["threshold"]["max"]) == MEDIUM_WATER_NEEDS_BAND
    assert body["threshold"]["source"] == "water_needs:medium"


def test_plant_chart_data_temperature_band(seeded_client):
    resp = seeded_client.get("/api/v1/plants/1/chart-data?metric=temperature")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert (body["threshold"]["min"], body["threshold"]["max"]) == SEEDED_TEMP_BAND


def test_plant_chart_data_unsupported_metric(seeded_client):
    resp = seeded_client.get("/api/v1/plants/1/chart-data?metric=co2")
    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_cluster_chart_data(seeded_client):
    resp = seeded_client.get("/api/v1/clusters/1/chart-data?hours=24&metric=soil_moisture")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    # cluster scope with default soil moisture threshold
    assert body["threshold"]["source"] == "default"
    assert body["threshold"]["min"] == DEFAULT_SOIL_MOISTURE_MIN


def test_plant_dashboard_page_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/plants/1")
    assert resp.status_code == HTTPStatus.OK
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
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_plant_chart_fragment(seeded_client):
    resp = seeded_client.get("/clusters/1/plants/1/chart-fragment?metric=temperature&hours=168")
    assert resp.status_code == HTTPStatus.OK
    assert "canvas" in resp.text.lower()
    assert 'id="chart-data-temperature"' in resp.text


def test_plant_chart_fragment_invalid_metric(seeded_client):
    resp = seeded_client.get("/clusters/1/plants/1/chart-fragment?metric=bogus")
    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_cluster_detail_includes_chart_payloads(seeded_client):
    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == HTTPStatus.OK
    # Inline chart JSON blocks for each metric
    for metric in ("soil_moisture", "temperature", "light", "env_humidity"):
        assert f'id="chart-data-{metric}"' in resp.text


def test_cluster_chart_fragment(seeded_client):
    resp = seeded_client.get("/clusters/1/chart-fragment?metric=temperature&hours=168")
    assert resp.status_code == HTTPStatus.OK
    assert "canvas" in resp.text.lower()
    assert 'id="chart-data-temperature"' in resp.text


def test_cluster_chart_fragment_invalid_metric(seeded_client):
    resp = seeded_client.get("/clusters/1/chart-fragment?metric=bogus")
    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_cluster_chart_fragment_404(client):
    resp = client.get("/clusters/9999/chart-fragment")
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_plant_chart_soil_band_uses_the_shared_target_parser():
    """D10b: the chart's water-needs band reads targets like every other reader (``parse_moisture_target``).

    Before D10b the chart had its own parser: ``"40-60-80"`` fell back to the default band with
    source ``"default"``; now it reads the first two parts. No ``lo-hi`` target at all still
    yields the default band labelled ``"default"``.
    """

    plant = SimpleNamespace(water_needs="medium")

    def db(target):
        return SimpleNamespace(get_water_needs_info=lambda _level: {"soil_moisture_target": target})

    assert _threshold_for_plant(plant, db("40-60-80"), "soil_moisture") == {
        "min": 40.0,
        "max": 60.0,
        "source": "water_needs:medium",
    }
    assert _threshold_for_plant(plant, db("40-60"), "soil_moisture") == {
        "min": 40.0,
        "max": 60.0,
        "source": "water_needs:medium",
    }
    for missing in (None, "", "55"):
        assert _threshold_for_plant(plant, db(missing), "soil_moisture") == {
            "min": 45.0,
            "max": 65.0,
            "source": "default",
        }

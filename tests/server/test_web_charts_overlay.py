"""Web fragment tests for the multi-metric overlay chart partial."""

import json
from http import HTTPStatus

from fastapi.testclient import TestClient


def test_overlay_fragment_contains_canvas(seeded_client: TestClient) -> None:
    """The overlay web fragment must render a <canvas> element."""
    resp = seeded_client.get("/clusters/1/overlay-fragment?hours=72")
    assert resp.status_code == HTTPStatus.OK
    assert "<canvas" in resp.text.lower()


def test_overlay_fragment_contains_json_island(seeded_client: TestClient) -> None:
    """The overlay partial must embed a JSON data island with metric information."""
    resp = seeded_client.get("/clusters/1/overlay-fragment?hours=72")
    assert resp.status_code == HTTPStatus.OK
    assert 'id="chart-data-overlay"' in resp.text


def test_overlay_fragment_json_island_is_valid(seeded_client: TestClient) -> None:
    """The embedded JSON island must be valid and contain expected keys."""
    resp = seeded_client.get("/clusters/1/overlay-fragment?hours=72")
    assert resp.status_code == HTTPStatus.OK
    # Extract the JSON between the script tags
    text = resp.text
    start = text.find('id="chart-data-overlay">')
    assert start != -1
    start += len('id="chart-data-overlay">')
    end = text.find("</script>", start)
    raw_json = text[start:end].strip()
    data = json.loads(raw_json)
    assert "datasets" in data
    assert "events" in data
    assert "normalised" in data
    assert data["normalised"] is True


def test_overlay_fragment_404_unknown_cluster(client: TestClient) -> None:
    """Overlay fragment 404 unknown cluster."""
    resp = client.get("/clusters/9999/overlay-fragment")
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_heatmap_fragment_renders(seeded_client: TestClient) -> None:
    """The heatmap web fragment must render (200 OK)."""
    resp = seeded_client.get("/clusters/1/heatmap-fragment?days=30")
    assert resp.status_code == HTTPStatus.OK
    assert "heatmap" in resp.text.lower()


def test_plant_health_fragment_contains_canvas(seeded_client: TestClient) -> None:
    """The plant health chart fragment must contain a <canvas> element."""
    resp = seeded_client.get("/clusters/1/plants/1/health-fragment")
    assert resp.status_code == HTTPStatus.OK
    assert "<canvas" in resp.text.lower()


def test_plant_health_fragment_contains_json_island(seeded_client: TestClient) -> None:
    """The plant health fragment must embed a JSON data island."""
    resp = seeded_client.get("/clusters/1/plants/1/health-fragment")
    assert resp.status_code == HTTPStatus.OK
    assert "chart-data-health-" in resp.text

"""Web fragment tests for the multi-metric overlay chart partial."""

import json


def test_overlay_fragment_contains_canvas(seeded_client):
    """The overlay web fragment must render a <canvas> element."""
    resp = seeded_client.get("/clusters/1/overlay-fragment?hours=72")
    assert resp.status_code == 200
    assert "<canvas" in resp.text.lower()


def test_overlay_fragment_contains_json_island(seeded_client):
    """The overlay partial must embed a JSON data island with metric information."""
    resp = seeded_client.get("/clusters/1/overlay-fragment?hours=72")
    assert resp.status_code == 200
    assert 'id="chart-data-overlay"' in resp.text


def test_overlay_fragment_json_island_is_valid(seeded_client):
    """The embedded JSON island must be valid and contain expected keys."""
    resp = seeded_client.get("/clusters/1/overlay-fragment?hours=72")
    assert resp.status_code == 200
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


def test_overlay_fragment_404_unknown_cluster(client):
    resp = client.get("/clusters/9999/overlay-fragment")
    assert resp.status_code == 404


def test_heatmap_fragment_renders(seeded_client):
    """The heatmap web fragment must render (200 OK)."""
    resp = seeded_client.get("/clusters/1/heatmap-fragment?days=30")
    assert resp.status_code == 200
    assert "heatmap" in resp.text.lower()


def test_plant_health_fragment_contains_canvas(seeded_client):
    """The plant health chart fragment must contain a <canvas> element."""
    resp = seeded_client.get("/clusters/1/plants/1/health-fragment")
    assert resp.status_code == 200
    assert "<canvas" in resp.text.lower()


def test_plant_health_fragment_contains_json_island(seeded_client):
    """The plant health fragment must embed a JSON data island."""
    resp = seeded_client.get("/clusters/1/plants/1/health-fragment")
    assert resp.status_code == 200
    assert "chart-data-health-" in resp.text

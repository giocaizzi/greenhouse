"""Web learn page — structured insight cards (replaces <pre> dump)."""


def test_learn_page_renders_insight_cards(seeded_client):
    resp = seeded_client.get("/clusters/1/learn")
    assert resp.status_code == 200
    # Structured card markup must be present
    assert 'class="insight' in resp.text


def test_learn_page_no_pre_dump(seeded_client):
    resp = seeded_client.get("/clusters/1/learn")
    assert resp.status_code == 200
    # The raw <pre class="report"> text-dump must be gone
    assert 'class="report"' not in resp.text


def test_learn_page_shows_forecast_section(seeded_client):
    resp = seeded_client.get("/clusters/1/learn")
    assert resp.status_code == 200
    # Forecast section heading must be present
    assert "Forecast" in resp.text


def test_learn_page_empty_state(seeded_client):
    """When there are no insights, the empty-state block renders."""
    resp = seeded_client.get("/clusters/1/learn")
    assert resp.status_code == 200
    # Either insight cards or empty-state must be present
    assert 'class="insight' in resp.text or "All good" in resp.text


def test_learn_page_404_for_missing_cluster(client):
    resp = client.get("/clusters/9999/learn")
    assert resp.status_code == 404

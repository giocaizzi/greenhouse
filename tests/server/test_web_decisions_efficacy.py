"""Web decisions and efficacy tabs."""


def test_decisions_page_renders_empty(seeded_client):
    resp = seeded_client.get("/clusters/1/decisions")
    assert resp.status_code == 200
    assert "No decisions yet" in resp.text


def test_decisions_page_404(client):
    resp = client.get("/clusters/9999/decisions")
    assert resp.status_code == 404


def test_decisions_tab_in_cluster_tabs(seeded_client):
    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == 200
    assert 'href="/clusters/1/decisions"' in resp.text
    assert 'href="/clusters/1/efficacy"' in resp.text


def test_efficacy_page_renders_empty(seeded_client):
    resp = seeded_client.get("/clusters/1/efficacy")
    assert resp.status_code == 200
    # Either the empty state or the table heads must render
    assert "Efficacy" in resp.text or "No scored events" in resp.text


def test_efficacy_page_404(client):
    resp = client.get("/clusters/9999/efficacy")
    assert resp.status_code == 404


def test_efficacy_window_segment_links(seeded_client):
    resp = seeded_client.get("/clusters/1/efficacy?days=30")
    assert resp.status_code == 200
    assert "30d" in resp.text

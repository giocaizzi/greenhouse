"""Web cluster pages: list, detail, polled status fragment."""


def test_clusters_list_empty(client):
    resp = client.get("/clusters")
    assert resp.status_code == 200
    assert "No clusters yet" in resp.text


def test_clusters_list_seeded(seeded_client):
    resp = seeded_client.get("/clusters")
    assert resp.status_code == 200
    assert "Test Cluster" in resp.text
    assert 'href="/clusters/1"' in resp.text


def test_cluster_detail_renders(seeded_client):
    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == 200
    assert "Test Cluster" in resp.text
    assert "Live status" in resp.text
    assert 'hx-get="/clusters/1/status-fragment"' in resp.text
    # nested resources
    assert "Plants" in resp.text
    assert "Monstera" in resp.text  # seeded plant species
    assert "Test Irrigator" in resp.text


def test_cluster_detail_404(client):
    resp = client.get("/clusters/9999")
    assert resp.status_code == 404


def test_cluster_status_fragment(seeded_client):
    resp = seeded_client.get("/clusters/1/status-fragment")
    assert resp.status_code == 200
    # No <html> wrapper — fragment-only response
    assert "<html" not in resp.text.lower()
    # decision rendered
    assert "Decision" in resp.text or "decision" in resp.text.lower()


def test_cluster_status_fragment_404(client):
    resp = client.get("/clusters/9999/status-fragment")
    assert resp.status_code == 404

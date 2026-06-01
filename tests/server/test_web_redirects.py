"""Legacy sub-tab URLs 301 to the unified cluster detail page anchors."""


def test_plants_list_redirects_to_anchor(seeded_client):
    resp = seeded_client.get("/clusters/1/plants", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/clusters/1#plants"


def test_sensors_list_redirects_to_anchor(seeded_client):
    resp = seeded_client.get("/clusters/1/sensors", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/clusters/1#sensors"


def test_irrigators_list_redirects_to_anchor(seeded_client):
    resp = seeded_client.get("/clusters/1/irrigators", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/clusters/1#irrigators"


def test_config_redirects_to_anchor(seeded_client):
    resp = seeded_client.get("/clusters/1/config", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/clusters/1#config"


def test_redirects_still_404_for_missing_cluster(client):
    """The 301 path still calls require_cluster, so unknown ids 404 cleanly."""
    for path in ("plants", "sensors", "irrigators", "config"):
        resp = client.get(f"/clusters/9999/{path}", follow_redirects=False)
        assert resp.status_code == 404, f"/clusters/9999/{path} expected 404, got {resp.status_code}"

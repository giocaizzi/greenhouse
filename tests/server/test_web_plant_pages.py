"""Web plant list and creation form."""


def test_plants_list_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/plants")
    assert resp.status_code == 200
    assert "Monstera deliciosa" in resp.text


def test_plants_list_404_for_missing_cluster(client):
    resp = client.get("/clusters/9999/plants")
    assert resp.status_code == 404


def test_new_plant_form_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/plants/new")
    assert resp.status_code == 200
    assert "Add plant" in resp.text
    assert 'name="species"' in resp.text


def test_create_plant(seeded_client):
    resp = seeded_client.post(
        "/clusters/1/plants",
        data={"species": "Ficus lyrata", "category": "tropical", "water_needs": "medium"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/clusters/1/plants"

    resp2 = seeded_client.get("/clusters/1/plants")
    assert "Ficus lyrata" in resp2.text

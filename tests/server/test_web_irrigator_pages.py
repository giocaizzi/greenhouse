"""Web irrigator list and creation form."""


def test_irrigators_list_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/irrigators")
    assert resp.status_code == 200
    assert "Test Irrigator" in resp.text


def test_new_irrigator_form_renders(seeded_client):
    resp = seeded_client.get("/clusters/1/irrigators/new")
    assert resp.status_code == 200
    assert "Add irrigator" in resp.text
    assert 'name="type"' in resp.text


def test_create_irrigator(seeded_client):
    resp = seeded_client.post(
        "/clusters/1/irrigators",
        data={
            "tuya_device_id": "fake_irr_002",
            "name": "Pump B",
            "type": "tuya_cloud",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp2 = seeded_client.get("/clusters/1/irrigators")
    assert "Pump B" in resp2.text

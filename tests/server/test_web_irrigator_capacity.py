"""Web UI: irrigator capacity inputs (reservoir_l / flow_rate_l_per_min)."""


def test_new_form_renders_capacity_inputs(seeded_client):
    """The create form exposes the two optional capacity inputs."""
    seeded_client.post("/api/v1/clusters", json={"name": "Empty"})
    resp = seeded_client.get("/clusters/2/irrigators/new")
    assert resp.status_code == 200
    assert 'name="reservoir_l"' in resp.text
    assert 'name="flow_rate_l_per_min"' in resp.text
    assert "Reservoir (L)" in resp.text
    assert "Pump flow (L/min)" in resp.text


def test_create_persists_capacity(seeded_client):
    """Submitting the create form persists the capacity fields."""
    seeded_client.post("/api/v1/clusters", json={"name": "Empty"})
    resp = seeded_client.post(
        "/clusters/2/irrigators",
        data={
            "tuya_device_id": "fake_irr_cap",
            "name": "Pump Cap",
            "type": "tuya_cloud",
            "reservoir_l": "15.5",
            "flow_rate_l_per_min": "2.5",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Confirm it round-tripped via the API.
    created = seeded_client.get("/api/v1/clusters/2/irrigator").json()
    assert created["name"] == "Pump Cap"
    assert created["reservoir_l"] == 15.5
    assert created["flow_rate_l_per_min"] == 2.5


def test_create_without_capacity_leaves_none(seeded_client):
    """Blank capacity inputs persist as None, not 0."""
    seeded_client.post("/api/v1/clusters", json={"name": "Empty"})
    resp = seeded_client.post(
        "/clusters/2/irrigators",
        data={
            "tuya_device_id": "fake_irr_nocap",
            "name": "Pump NoCap",
            "type": "tuya_cloud",
            "reservoir_l": "",
            "flow_rate_l_per_min": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    created = seeded_client.get("/api/v1/clusters/2/irrigator").json()
    assert created["name"] == "Pump NoCap"
    assert created["reservoir_l"] is None
    assert created["flow_rate_l_per_min"] is None


def test_edit_form_prefills_capacity(seeded_client):
    """The edit form pre-fills existing capacity values."""
    seeded_client.put(
        "/api/v1/clusters/1/irrigator",
        json={"reservoir_l": 9.0, "flow_rate_l_per_min": 1.2},
    )
    resp = seeded_client.get("/clusters/1/irrigators/edit")
    assert resp.status_code == 200
    assert 'name="reservoir_l"' in resp.text
    assert 'name="flow_rate_l_per_min"' in resp.text
    assert 'value="9.0"' in resp.text
    assert 'value="1.2"' in resp.text


def test_edit_persists_capacity(seeded_client):
    """Submitting the edit form persists updated capacity values."""
    resp = seeded_client.post(
        "/clusters/1/irrigators/edit",
        data={
            "name": "Test Irrigator",
            "type": "tuya_cloud",
            "reservoir_l": "30.0",
            "flow_rate_l_per_min": "3.0",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    got = seeded_client.get("/api/v1/clusters/1/irrigator").json()
    assert got["reservoir_l"] == 30.0
    assert got["flow_rate_l_per_min"] == 3.0

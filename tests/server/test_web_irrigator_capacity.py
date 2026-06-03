"""Web UI: irrigator capacity inputs (reservoir_l / flow_rate_l_per_min)."""


def test_new_form_renders_capacity_inputs(seeded_client):
    """The create form exposes the two optional capacity inputs."""
    resp = seeded_client.get("/clusters/1/irrigators/new")
    assert resp.status_code == 200
    assert 'name="reservoir_l"' in resp.text
    assert 'name="flow_rate_l_per_min"' in resp.text
    assert "Reservoir (L)" in resp.text
    assert "Pump flow (L/min)" in resp.text


def test_create_persists_capacity(seeded_client):
    """Submitting the create form persists the capacity fields."""
    resp = seeded_client.post(
        "/clusters/1/irrigators",
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

    # Find the new irrigator via the API and confirm it round-tripped.
    irrigators = seeded_client.get("/api/v1/irrigators?cluster_id=1").json()["irrigators"]
    created = next(i for i in irrigators if i["name"] == "Pump Cap")
    assert created["reservoir_l"] == 15.5
    assert created["flow_rate_l_per_min"] == 2.5


def test_create_without_capacity_leaves_none(seeded_client):
    """Blank capacity inputs persist as None, not 0."""
    resp = seeded_client.post(
        "/clusters/1/irrigators",
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
    irrigators = seeded_client.get("/api/v1/irrigators?cluster_id=1").json()["irrigators"]
    created = next(i for i in irrigators if i["name"] == "Pump NoCap")
    assert created["reservoir_l"] is None
    assert created["flow_rate_l_per_min"] is None


def test_edit_form_prefills_capacity(seeded_client):
    """The edit form pre-fills existing capacity values."""
    seeded_client.put(
        "/api/v1/clusters/1/irrigators/1",
        json={"reservoir_l": 9.0, "flow_rate_l_per_min": 1.2},
    )
    resp = seeded_client.get("/clusters/1/irrigators/1/edit")
    assert resp.status_code == 200
    assert 'name="reservoir_l"' in resp.text
    assert 'name="flow_rate_l_per_min"' in resp.text
    assert 'value="9.0"' in resp.text
    assert 'value="1.2"' in resp.text


def test_edit_persists_capacity(seeded_client):
    """Submitting the edit form persists updated capacity values."""
    resp = seeded_client.post(
        "/clusters/1/irrigators/1/edit",
        data={
            "name": "Test Irrigator",
            "type": "tuya_cloud",
            "reservoir_l": "30.0",
            "flow_rate_l_per_min": "3.0",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    got = seeded_client.get("/api/v1/clusters/1/irrigators/1").json()
    assert got["reservoir_l"] == 30.0
    assert got["flow_rate_l_per_min"] == 3.0

"""B-15: PATCH-style updates tell an absent field from an explicit ``null``.

Absent → unchanged; ``null`` → cleared for an optional field, ignored for a required one.
"""


def _cluster(client, **extra):
    return client.post("/api/v1/clusters", json={"name": "C1", "location": "Shelf", **extra}).json()["id"]


def test_cluster_location_null_clears_absent_keeps(client):
    cid = _cluster(client)
    kept = client.put(f"/api/v1/clusters/{cid}", json={"environment": "outdoor"}).json()
    assert (kept["name"], kept["location"], kept["environment"]) == ("C1", "Shelf", "outdoor")
    cleared = client.put(f"/api/v1/clusters/{cid}", json={"location": None, "name": None}).json()
    assert (cleared["name"], cleared["location"]) == ("C1", None)


def test_plant_notes_null_clears(client):
    cid = _cluster(client)
    pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": "Monstera deliciosa", "notes": "n"}).json()[
        "id"
    ]
    resp = client.put(f"/api/v1/clusters/{cid}/plants/{pid}", json={"notes": None})
    assert (resp.status_code, resp.json()["notes"], resp.json()["species"]) == (200, None, "Monstera deliciosa")


def test_vacation_notes_and_email_null_clear(client):
    created = client.post(
        "/api/v1/vacation",
        json={"starts_at": 4_000_000_000, "ends_at": 4_000_003_600, "notes": "trip", "contact_email": "a@b.invalid"},
    ).json()
    resp = client.put(f"/api/v1/vacation/{created['id']}", json={"notes": None, "contact_email": None})
    body = resp.json()
    assert (resp.status_code, body["notes"], body["contact_email"], body["starts_at"]) == (
        200,
        None,
        None,
        4_000_000_000,
    )


def test_preferences_default_cluster_null_clears(client):
    cid = _cluster(client)
    assert client.put("/api/v1/preferences", json={"default_cluster_id": cid}).json()["default_cluster_id"] == cid
    body = client.put("/api/v1/preferences", json={"default_cluster_id": None, "theme": None}).json()
    assert (body["default_cluster_id"], body["theme"]) == (None, "auto")


def test_sensor_plant_id_null_unassigns(client):
    cid = _cluster(client)
    pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": "Monstera deliciosa"}).json()["id"]
    sid = client.post(
        f"/api/v1/clusters/{cid}/sensors",
        json={"tuya_device_id": "fake_tuya_sensor_00000001", "name": "S", "type": "tuya.tr301z", "plant_id": pid},
    ).json()["id"]
    assert client.put(f"/api/v1/clusters/{cid}/sensors/{sid}", json={"name": "S2"}).json()["plant_id"] == pid
    assert client.put(f"/api/v1/clusters/{cid}/sensors/{sid}", json={"plant_id": None}).json()["plant_id"] is None


def test_irrigator_capacity_null_clears(client):
    cid = _cluster(client)
    client.post(
        f"/api/v1/clusters/{cid}/irrigator",
        json={"tuya_device_id": "d1", "name": "P", "type": "rainpoint.ik10pw", "reservoir_l": 10},
    )
    body = client.put(f"/api/v1/clusters/{cid}/irrigator", json={"reservoir_l": None}).json()
    assert (body["reservoir_l"], body["name"]) == (None, "P")

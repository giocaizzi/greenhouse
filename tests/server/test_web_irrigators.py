"""Web irrigator secret handling: the local key is a root-level credential.

These guard two properties of the edit flow:

1. A blank ``local_key`` on save PRESERVES the stored key (never wipes it).
2. The stored key is never echoed back into the edit page source.
"""

import json

SECRET_KEY = "fake_local_key_deadbeef"


def _seed_local_irrigator(client, cluster_name="Local Cluster"):
    """Create a cluster + tuya_local irrigator carrying a stored local key.

    Returns the cluster id.
    """
    resp = client.post("/api/v1/clusters", json={"name": cluster_name})
    cluster_id = resp.json()["id"]
    resp = client.post(
        f"/clusters/{cluster_id}/irrigators",
        data={
            "tuya_device_id": "fake_tuya_device_aabbccdd",
            "name": "Local Pump",
            "type": "tuya_local",
            "device_ip": "192.0.2.10",
            "local_key": SECRET_KEY,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return cluster_id


def _stored_config(client, cluster_id):
    resp = client.get(f"/api/v1/clusters/{cluster_id}/irrigator")
    assert resp.status_code == 200
    config = resp.json()["config"]
    return json.loads(config) if isinstance(config, str) else (config or {})


def test_blank_local_key_preserves_stored_key(client):
    """Editing the name with a blank local_key must keep the stored key."""
    cluster_id = _seed_local_irrigator(client)
    assert _stored_config(client, cluster_id)["local_key"] == SECRET_KEY

    resp = client.post(
        f"/clusters/{cluster_id}/irrigators/edit",
        data={
            "name": "Renamed Pump",
            "type": "tuya_local",
            "device_ip": "192.0.2.10",
            "local_key": "",  # blank — must NOT wipe the stored key
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    config = _stored_config(client, cluster_id)
    assert config["local_key"] == SECRET_KEY
    assert config["device_ip"] == "192.0.2.10"


def test_nonblank_local_key_overrides_stored_key(client):
    """Submitting a new local_key replaces the stored one."""
    cluster_id = _seed_local_irrigator(client)
    resp = client.post(
        f"/clusters/{cluster_id}/irrigators/edit",
        data={
            "name": "Local Pump",
            "type": "tuya_local",
            "device_ip": "192.0.2.10",
            "local_key": "fake_local_key_rotated",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert _stored_config(client, cluster_id)["local_key"] == "fake_local_key_rotated"


def test_edit_page_does_not_leak_local_key(client):
    """The stored secret must never appear in the rendered edit page source."""
    cluster_id = _seed_local_irrigator(client)
    resp = client.get(f"/clusters/{cluster_id}/irrigators/edit")
    assert resp.status_code == 200
    assert SECRET_KEY not in resp.text
    # Field is masked and carries no stored value.
    assert 'type="password"' in resp.text
    assert "Leave blank to keep the current key." in resp.text

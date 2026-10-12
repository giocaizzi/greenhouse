"""Web irrigator detail rendering and the singular create form (0:1 cluster→irrigator)."""

from http import HTTPStatus

from fastapi.testclient import TestClient


def test_irrigator_renders_on_cluster_detail(seeded_client: TestClient) -> None:
    """Irrigator renders on cluster detail."""
    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == HTTPStatus.OK
    assert "Test Irrigator" in resp.text


def test_add_affordance_hidden_when_irrigator_exists(seeded_client: TestClient) -> None:
    """Cluster 1 already has an irrigator, so the 'Add irrigator' link must be gone."""
    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == HTTPStatus.OK
    assert "/clusters/1/irrigators/new" not in resp.text


def test_new_irrigator_form_redirects_when_one_exists(seeded_client: TestClient) -> None:
    """Reaching the new form for a cluster that already has an irrigator redirects back."""
    resp = seeded_client.get("/clusters/1/irrigators/new", follow_redirects=False)
    assert resp.status_code == HTTPStatus.SEE_OTHER


def test_new_irrigator_form_renders_for_empty_cluster(seeded_client: TestClient) -> None:
    """New irrigator form renders for empty cluster."""
    seeded_client.post("/api/v1/clusters", json={"name": "Empty"})
    resp = seeded_client.get("/clusters/2/irrigators/new")
    assert resp.status_code == HTTPStatus.OK
    assert "Add irrigator" in resp.text
    assert 'name="type"' in resp.text


def test_add_affordance_shown_for_empty_cluster(seeded_client: TestClient) -> None:
    """Add affordance shown for empty cluster."""
    seeded_client.post("/api/v1/clusters", json={"name": "Empty"})
    resp = seeded_client.get("/clusters/2")
    assert resp.status_code == HTTPStatus.OK
    assert "/clusters/2/irrigators/new" in resp.text


def test_create_irrigator_on_empty_cluster(seeded_client: TestClient) -> None:
    """Create irrigator on empty cluster."""
    seeded_client.post("/api/v1/clusters", json={"name": "Empty"})
    resp = seeded_client.post(
        "/clusters/2/irrigators",
        data={
            "tuya_device_id": "fake_irr_002",
            "name": "Pump B",
            "type": "rainpoint.ik10pw",
        },
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER

    resp2 = seeded_client.get("/clusters/2")
    assert "Pump B" in resp2.text


def test_create_second_irrigator_surfaces_409(seeded_client: TestClient) -> None:
    """Posting a second irrigator to a cluster that already has one returns a 409 error page."""
    resp = seeded_client.post(
        "/clusters/1/irrigators",
        data={
            "tuya_device_id": "fake_irr_999",
            "name": "Extra Pump",
            "type": "rainpoint.ik10pw",
        },
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.CONFLICT
    assert "already has an irrigator" in resp.text

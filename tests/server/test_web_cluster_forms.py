"""Web cluster creation form."""

from http import HTTPStatus


def test_new_cluster_form_renders(client):
    """New cluster form renders."""
    resp = client.get("/clusters/new")
    assert resp.status_code == HTTPStatus.OK
    assert "New cluster" in resp.text
    assert 'name="environment"' in resp.text


def test_create_cluster_redirects_to_detail(client):
    """Create cluster redirects to detail."""
    resp = client.post(
        "/clusters",
        data={"name": "Kitchen", "location": "sink side", "environment": "indoor"},
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER
    assert resp.headers["location"] == "/clusters/1"

    # And the cluster now appears in the list.
    resp2 = client.get("/clusters")
    assert "Kitchen" in resp2.text


def test_create_cluster_missing_name_fails(client):
    """Create cluster missing name fails."""
    resp = client.post("/clusters", data={"environment": "indoor"}, follow_redirects=False)
    # FastAPI's Form(...) treats missing required as 422
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

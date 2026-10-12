"""Web plant list and creation form."""

from http import HTTPStatus

from fastapi.testclient import TestClient


def test_plants_list_renders(seeded_client: TestClient) -> None:
    """Plants list renders, with edit and delete actions per row."""
    resp = seeded_client.get("/clusters/1/plants")
    assert resp.status_code == HTTPStatus.OK
    assert "Monstera deliciosa" in resp.text
    assert 'href="/clusters/1/plants/1/edit"' in resp.text
    assert 'hx-delete="/clusters/1/plants/1"' in resp.text


def test_plants_list_404_for_missing_cluster(client: TestClient) -> None:
    """Plants list 404 for missing cluster."""
    resp = client.get("/clusters/9999/plants")
    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_new_plant_form_renders(seeded_client: TestClient) -> None:
    """New plant form renders."""
    resp = seeded_client.get("/clusters/1/plants/new")
    assert resp.status_code == HTTPStatus.OK
    assert "Add plant" in resp.text
    assert 'name="species"' in resp.text


def test_create_plant(seeded_client: TestClient) -> None:
    """Create plant."""
    resp = seeded_client.post(
        "/clusters/1/plants",
        data={"species": "Ficus lyrata", "category": "tropical", "water_needs": "medium"},
        follow_redirects=False,
    )
    assert resp.status_code == HTTPStatus.SEE_OTHER
    # Plants live inline on the unified detail page now — POST lands at #plants.
    assert resp.headers["location"] == "/clusters/1#plants"

    resp2 = seeded_client.get("/clusters/1/plants")
    assert "Ficus lyrata" in resp2.text


# The add / edit form is read by the ``plant_form`` dependency; its bounds are parsed only after
# the route's 404 lookup, so the error precedence is: 422 (missing species) > 404 > 400.
_BAD_BOUND = {"species": "Ficus lyrata", "ideal_temp_min": "abc"}
_JSON = {"Accept": "application/json"}


def test_plant_form_missing_species_is_422_even_for_an_unknown_cluster(seeded_client: TestClient) -> None:
    """Plant form missing species is 422 even for an unknown cluster."""
    resp = seeded_client.post("/clusters/9999/plants", data={"ideal_temp_min": "abc"}, headers=_JSON)
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert resp.json()["detail"][0]["loc"] == ["body", "species"]


def test_plant_form_unknown_cluster_or_plant_wins_over_a_bad_bound(seeded_client: TestClient) -> None:
    """Plant form unknown cluster or plant wins over a bad bound."""
    assert seeded_client.post("/clusters/9999/plants", data=_BAD_BOUND, headers=_JSON).json() == {
        "detail": "Cluster not found"
    }
    resp = seeded_client.post("/clusters/1/plants/9999/edit", data=_BAD_BOUND, headers=_JSON)
    assert (resp.status_code, resp.json()) == (404, {"detail": "Plant not found in cluster"})


def test_plant_form_bad_bound_is_400_naming_the_field(seeded_client: TestClient) -> None:
    """Plant form bad bound is 400 naming the field."""
    for url in ("/clusters/1/plants", "/clusters/1/plants/1/edit"):
        resp = seeded_client.post(url, data=_BAD_BOUND, headers=_JSON)
        assert (resp.status_code, resp.json()) == (400, {"detail": "Invalid ideal_temp_min: must be a number"})


def test_plant_form_422_lists_body_errors_before_path_errors(seeded_client: TestClient) -> None:
    """A doubly invalid post: the dependency's form errors are collected before the route's path error."""
    resp = seeded_client.post("/clusters/abc/plants", data={}, headers=_JSON)
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert [e["loc"] for e in resp.json()["detail"]] == [["body", "species"], ["path", "cluster_id"]]


def test_plant_edit_form_maps_blanks_to_none(seeded_client: TestClient) -> None:
    """Plant edit form maps blanks to none."""
    resp = seeded_client.post(
        "/clusters/1/plants/1/edit",
        data={"species": "Monstera deliciosa", "category": "", "ideal_temp_min": "16.5"},
        follow_redirects=False,
    )
    assert (resp.status_code, resp.headers["location"]) == (303, "/clusters/1#plants")
    plant = seeded_client.get("/api/v1/clusters/1/plants").json()[0]
    assert (plant["category"], plant["ideal_temp_min"], plant["ideal_temp_max"]) == (None, 16.5, None)

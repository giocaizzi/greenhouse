"""A plant on the built-in soil band is visible: response fields and a ``plant_care_defaulted`` alert (issue #150)."""

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

from fastapi.testclient import TestClient

from engine_grid import plant_database
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.irrigation import IrrigationService
from greenhouse_server.services.plant_care import PLANT_CARE_DEFAULTED_CODE
from greenhouse_server.services.sync import SyncService

if TYPE_CHECKING:
    from greenhouse_server.services.weather import WeatherClient

UNKNOWN_SPECIES = "Unknown planticus"
DEFAULT_BAND = (45.0, 65.0)
FICUS_BAND = (25.0, 40.0)


def _cluster(client: TestClient) -> int:
    return cast("int", client.post("/api/v1/clusters", json={"name": "Care"}).json()["id"])


def _care_alerts(client: TestClient, plant_id: int, status: str = "open") -> list[dict[str, Any]]:
    items = client.get(f"/api/v1/alerts?plant_id={plant_id}&status={status}").json()["items"]
    return [a for a in items if a["code"] == PLANT_CARE_DEFAULTED_CODE]


def _band(body: dict[str, Any]) -> tuple[float, float, str]:
    return body["soil_moisture_min"], body["soil_moisture_max"], body["soil_band_source"]


def test_unknown_species_opens_an_info_alert_and_reports_the_fallback(client: TestClient) -> None:
    """Neither species nor category resolves: the response says ``builtin_fallback`` and one info alert opens."""
    cid = _cluster(client)
    resp = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": UNKNOWN_SPECIES})
    assert resp.status_code == HTTPStatus.CREATED
    assert _band(resp.json()) == (*DEFAULT_BAND, "builtin_fallback")
    pid = resp.json()["id"]
    (alert,) = _care_alerts(client, pid)
    assert alert["severity"] == "info"
    assert alert["cluster_id"] == cid
    assert UNKNOWN_SPECIES in alert["message"]
    assert _band(client.get(f"/api/v1/plants/{pid}").json()) == (*DEFAULT_BAND, "builtin_fallback")


def test_fixing_the_species_resolves_the_alert(client: TestClient) -> None:
    """A species that now resolves clears the alert and the response shows the species band."""
    cid = _cluster(client)
    pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": "Figus"}).json()["id"]
    resp = client.put(f"/api/v1/clusters/{cid}/plants/{pid}", json={"species": "Ficus benjamina"})
    assert _band(resp.json()) == (*FICUS_BAND, "species")
    assert _care_alerts(client, pid) == []
    assert len(_care_alerts(client, pid, status="resolved")) == 1


def test_a_known_category_is_enough(client: TestClient) -> None:
    """An unknown species in a known (any-case) category takes the category band and raises nothing."""
    cid = _cluster(client)
    resp = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": UNKNOWN_SPECIES, "category": "Tropical"})
    assert _band(resp.json()) == (50.0, 65.0, "category")
    assert _care_alerts(client, resp.json()["id"]) == []


def test_breaking_the_species_reopens_the_alert(client: TestClient) -> None:
    """An edit that loses the plant-DB match opens the alert again (one row per plant)."""
    cid = _cluster(client)
    pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": UNKNOWN_SPECIES}).json()["id"]
    client.put(f"/api/v1/clusters/{cid}/plants/{pid}", json={"species": "Monstera deliciosa"})
    client.put(f"/api/v1/clusters/{cid}/plants/{pid}", json={"species": UNKNOWN_SPECIES})
    (alert,) = _care_alerts(client, pid)
    assert alert["occurrence_count"] == 2  # noqa: PLR2004 — opened, resolved, re-opened: the same row


def test_deleting_the_plant_resolves_its_alert(client: TestClient) -> None:
    """SQLite reuses plant ids, so a deleted plant's alert must not stay open for the next plant."""
    cid = _cluster(client)
    pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": UNKNOWN_SPECIES}).json()["id"]
    assert client.delete(f"/api/v1/clusters/{cid}/plants/{pid}").status_code == HTTPStatus.OK
    assert _care_alerts(client, pid) == []


def test_web_form_create_opens_the_alert(client: TestClient) -> None:
    """The web form shares the rule with the API."""
    cid = _cluster(client)
    client.post(f"/clusters/{cid}/plants", data={"species": UNKNOWN_SPECIES}, follow_redirects=False)
    (plant,) = client.get(f"/api/v1/clusters/{cid}/plants").json()
    assert len(_care_alerts(client, plant["id"])) == 1


def test_the_hourly_check_reconciles_plants_written_elsewhere(tmp_db: IrrigationRepository) -> None:
    """A plant stored before this alert existed (no API write since) is caught by the cluster check."""
    db = tmp_db
    cid = db.add_cluster("Legacy")
    pid = db.add_plant(cluster_id=cid, species="Figus", category="Tropicale")
    svc = IrrigationService(db, None, SyncService(db, None, None), cast("WeatherClient", None), plant_database())
    svc.check_cluster(cid)
    (alert,) = [a for a in db.list_alerts(cluster_id=cid) if a.code == PLANT_CARE_DEFAULTED_CODE]
    assert (alert.plant_id, alert.status) == (pid, "open")
    db.update_plant(pid, category="tropical")
    svc.check_cluster(cid)
    db.session.refresh(alert)
    assert alert.status == "resolved"


def test_the_alert_moves_with_the_plant(client: TestClient) -> None:
    """Moving the plant relists its open care alert under the new cluster, still one row (issue #184)."""
    cid = _cluster(client)
    target = cast("int", client.post("/api/v1/clusters", json={"name": "Elsewhere"}).json()["id"])
    pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": UNKNOWN_SPECIES}).json()["id"]
    resp = client.post(f"/api/v1/plants/{pid}/move", json={"target_cluster_id": target})
    assert resp.status_code == HTTPStatus.OK
    (alert,) = _care_alerts(client, pid)
    assert alert["cluster_id"] == target
    moved = client.get(f"/api/v1/alerts?cluster_id={target}").json()["items"]
    assert [a["id"] for a in moved] == [alert["id"]]
    assert client.get(f"/api/v1/alerts?cluster_id={cid}").json()["items"] == []


def test_a_resolved_alert_stays_with_the_old_cluster(client: TestClient) -> None:
    """Resolved alerts are history: they keep the cluster the plant was in."""
    cid = _cluster(client)
    target = cast("int", client.post("/api/v1/clusters", json={"name": "Elsewhere"}).json()["id"])
    pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": "Figus"}).json()["id"]
    client.put(f"/api/v1/clusters/{cid}/plants/{pid}", json={"species": "Ficus benjamina"})
    client.post(f"/api/v1/plants/{pid}/move", json={"target_cluster_id": target})
    (alert,) = _care_alerts(client, pid, status="resolved")
    assert alert["cluster_id"] == cid


def test_an_own_soil_target_resolves_the_alert_and_wins(client: TestClient) -> None:
    """A per-plant target (issue #182) is stored normalised, reported as ``plant`` and clears the alert."""
    cid = _cluster(client)
    pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": UNKNOWN_SPECIES}).json()["id"]
    resp = client.put(f"/api/v1/clusters/{cid}/plants/{pid}", json={"soil_moisture_target": " 60 - 30 "})
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["soil_moisture_target"] == "30-60"
    assert _band(resp.json()) == (30.0, 60.0, "plant")
    assert _care_alerts(client, pid) == []
    cleared = client.put(f"/api/v1/clusters/{cid}/plants/{pid}", json={"soil_moisture_target": None}).json()
    assert _band(cleared) == (*DEFAULT_BAND, "builtin_fallback")
    assert len(_care_alerts(client, pid)) == 1


def test_an_own_soil_target_overrides_the_species(client: TestClient) -> None:
    """The plant's own band beats a resolved species on create; no alert opens."""
    cid = _cluster(client)
    body = {"species": "Ficus benjamina", "soil_moisture_target": "35.5-55"}
    resp = client.post(f"/api/v1/clusters/{cid}/plants", json=body)
    assert resp.status_code == HTTPStatus.CREATED
    assert _band(resp.json()) == (35.5, 55.0, "plant")
    assert _care_alerts(client, resp.json()["id"]) == []


def test_a_malformed_soil_target_is_refused(client: TestClient) -> None:
    """A target the parser would replace with the default band is a 400, never stored."""
    cid = _cluster(client)
    body = {"species": UNKNOWN_SPECIES, "soil_moisture_target": "50"}
    bad = client.post(f"/api/v1/clusters/{cid}/plants", json=body)
    assert bad.status_code == HTTPStatus.BAD_REQUEST
    assert "soil_moisture_target" in bad.json()["detail"]
    pid = client.post(f"/api/v1/clusters/{cid}/plants", json={"species": UNKNOWN_SPECIES}).json()["id"]
    for target in ("20-120", "high-low", ""):
        resp = client.put(f"/api/v1/clusters/{cid}/plants/{pid}", json={"soil_moisture_target": target})
        assert resp.status_code == HTTPStatus.BAD_REQUEST, target
    assert client.get(f"/api/v1/plants/{pid}").json()["soil_moisture_target"] is None


def test_web_form_sets_and_validates_the_soil_target(client: TestClient) -> None:
    """The web form stores the normalised target (blank clears it) and refuses a malformed one with a 400."""
    cid = _cluster(client)
    client.post(f"/clusters/{cid}/plants", data={"species": UNKNOWN_SPECIES, "soil_moisture_target": "40-20"})
    (plant,) = client.get(f"/api/v1/clusters/{cid}/plants").json()
    assert plant["soil_moisture_target"] == "20-40"
    assert _care_alerts(client, plant["id"]) == []
    edit_url = f"/clusters/{cid}/plants/{plant['id']}/edit"
    bad = client.post(edit_url, data={"species": UNKNOWN_SPECIES, "soil_moisture_target": "wet"})
    assert bad.status_code == HTTPStatus.BAD_REQUEST
    client.post(edit_url, data={"species": UNKNOWN_SPECIES}, follow_redirects=False)
    assert client.get(f"/api/v1/plants/{plant['id']}").json()["soil_moisture_target"] is None

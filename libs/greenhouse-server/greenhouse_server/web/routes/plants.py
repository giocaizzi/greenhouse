"""Plant web routes: list, create form, create, edit, archive, restore, purge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, TypedDict

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from greenhouse_core.logic.plant_needs import normalize_moisture_target
from greenhouse_core.repository import NotArchivedError
from greenhouse_server.deps import PlantDbDep, RepoDep, require_cluster, require_plant_in_cluster
from greenhouse_server.services.inventory import remove_plant
from greenhouse_server.services.plant_care import reconcile_care_alert
from greenhouse_server.web.context import base_context
from greenhouse_server.web.forms import parsed_or_400
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


class PlantFormFields(TypedDict):
    """The plant form mapped to repository fields (``add_plant`` / ``update_plant`` keywords)."""

    species: str
    category: str | None
    water_needs: str | None
    light_needs: str | None
    ideal_temp_min: float | None
    ideal_temp_max: float | None
    ideal_humidity_min: float | None
    ideal_humidity_max: float | None
    soil_moisture_target: str | None
    notes: str | None


def _parse_bound(raw: str, field: str) -> float | None:
    """Blank → None; a non-number is a 400 page naming the field, never an unhandled 500."""
    return parsed_or_400(raw, float, error=f"Invalid {field}: must be a number")


def _soil_target(raw: str) -> str | None:
    """Blank → None (the plant database's band); a malformed target is a 400 with the API's wording."""
    if not raw.strip():
        return None
    try:
        return normalize_moisture_target(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@dataclass(frozen=True)
class PlantForm:
    """The plant form as posted — create and edit share it; every field is the raw string.

    FastAPI builds it as a dependency (:data:`PlantFormDep`) from the ``Form`` declarations, so a
    missing ``species`` is a 422 from FastAPI's own form validation, listed before path errors.
    """

    species: Annotated[str, Form()]
    category: Annotated[str, Form()] = ""
    water_needs: Annotated[str, Form()] = ""
    light_needs: Annotated[str, Form()] = ""
    ideal_temp_min: Annotated[str, Form()] = ""
    ideal_temp_max: Annotated[str, Form()] = ""
    ideal_humidity_min: Annotated[str, Form()] = ""
    ideal_humidity_max: Annotated[str, Form()] = ""
    soil_moisture_target: Annotated[str, Form()] = ""
    notes: Annotated[str, Form()] = ""

    def fields(self) -> PlantFormFields:
        """Map the form to repository fields: blank → None, bounds → float (a non-number is a 400).

        Called by the handler after its 404 lookup, not while the form is read, so an
        unknown cluster or plant stays a 404 even when the bounds are malformed.
        """
        return {
            "species": self.species,
            "category": self.category or None,
            "water_needs": self.water_needs or None,
            "light_needs": self.light_needs or None,
            "ideal_temp_min": _parse_bound(self.ideal_temp_min, "ideal_temp_min"),
            "ideal_temp_max": _parse_bound(self.ideal_temp_max, "ideal_temp_max"),
            "ideal_humidity_min": _parse_bound(self.ideal_humidity_min, "ideal_humidity_min"),
            "ideal_humidity_max": _parse_bound(self.ideal_humidity_max, "ideal_humidity_max"),
            "soil_moisture_target": _soil_target(self.soil_moisture_target),
            "notes": self.notes or None,
        }


PlantFormDep = Annotated[PlantForm, Depends()]


@router.get("/clusters/{cluster_id}/plants")
def list_plants(cluster_id: int, repo: RepoDep) -> Response:
    """Redirect the legacy plants URL to the detail page's plants section (301).

    Plants are rendered inline on the unified cluster detail page; the
    redirect keeps old bookmarks working and drops them at the right anchor.
    """
    require_cluster(repo, cluster_id)
    return RedirectResponse(url=f"/clusters/{cluster_id}#plants", status_code=301)


@router.get("/clusters/{cluster_id}/plants/new")
def new_plant_form(request: Request, cluster_id: int, repo: RepoDep) -> Response:
    """Render the add-plant form."""
    cluster = require_cluster(repo, cluster_id)
    return templates.TemplateResponse(request, "plants/new.html", base_context(request, cluster=cluster))


@router.post("/clusters/{cluster_id}/plants")
def create_plant(cluster_id: int, repo: RepoDep, plant_db: PlantDbDep, form: PlantFormDep) -> Response:
    """Add a plant from the form and return to the cluster's plants section."""
    require_cluster(repo, cluster_id)
    plant_id = repo.add_plant(
        cluster_id=cluster_id,
        **form.fields(),
    )
    reconcile_care_alert(repo, plant_db, require_plant_in_cluster(repo, cluster_id, plant_id))
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#plants", status_code=303)


@router.get("/clusters/{cluster_id}/plants/{plant_id}/edit")
def edit_plant_form(request: Request, cluster_id: int, plant_id: int, repo: RepoDep) -> Response:
    """Render the edit form of one of the cluster's plants."""
    cluster = require_cluster(repo, cluster_id)
    plant = require_plant_in_cluster(repo, cluster_id, plant_id)
    return templates.TemplateResponse(request, "plants/edit.html", base_context(request, cluster=cluster, plant=plant))


@router.post("/clusters/{cluster_id}/plants/{plant_id}/edit")
def update_plant(cluster_id: int, plant_id: int, repo: RepoDep, plant_db: PlantDbDep, form: PlantFormDep) -> Response:
    """Save the plant form and return to the cluster's plants section."""
    plant = require_plant_in_cluster(repo, cluster_id, plant_id)
    repo.update_plant(
        plant_id,
        **form.fields(),
    )
    reconcile_care_alert(repo, plant_db, plant)
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#plants", status_code=303)


@router.delete("/clusters/{cluster_id}/plants/{plant_id}", response_class=HTMLResponse)
def delete_plant(cluster_id: int, plant_id: int, repo: RepoDep, *, purge: Annotated[bool, Query()] = False) -> Response:
    """HTMX-targeted delete: archive the plant, or ``purge`` an archived one; empty body drops the row."""
    require_plant_in_cluster(repo, cluster_id, plant_id)
    try:
        remove_plant(repo, plant_id, purge=purge)
    except NotArchivedError as exc:
        raise HTTPException(409, str(exc)) from None
    repo.commit()
    return HTMLResponse("")


@router.post("/clusters/{cluster_id}/plants/{plant_id}/restore")
def restore_plant(cluster_id: int, plant_id: int, repo: RepoDep) -> Response:
    """Bring an archived plant back (its sensors stay unlinked) and return to the plants section."""
    require_plant_in_cluster(repo, cluster_id, plant_id)
    repo.restore_plant(plant_id)
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#plants", status_code=303)

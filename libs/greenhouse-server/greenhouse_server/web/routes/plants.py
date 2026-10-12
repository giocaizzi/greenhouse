"""Plant web routes: list, create form, create, edit, delete."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, TypedDict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from greenhouse_server.deps import RepoDep, require_cluster, require_plant_in_cluster
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
    notes: str | None


def _parse_bound(raw: str, field: str) -> float | None:
    """Blank → None; a non-number is a 400 page naming the field, never an unhandled 500."""
    return parsed_or_400(raw, float, error=f"Invalid {field}: must be a number")


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
def create_plant(cluster_id: int, repo: RepoDep, form: PlantFormDep) -> Response:
    """Add a plant from the form and return to the cluster's plants section."""
    require_cluster(repo, cluster_id)
    repo.add_plant(
        cluster_id=cluster_id,
        **form.fields(),
    )
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#plants", status_code=303)


@router.get("/clusters/{cluster_id}/plants/{plant_id}/edit")
def edit_plant_form(request: Request, cluster_id: int, plant_id: int, repo: RepoDep) -> Response:
    """Render the edit form of one of the cluster's plants."""
    cluster = require_cluster(repo, cluster_id)
    plant = require_plant_in_cluster(repo, cluster_id, plant_id)
    return templates.TemplateResponse(request, "plants/edit.html", base_context(request, cluster=cluster, plant=plant))


@router.post("/clusters/{cluster_id}/plants/{plant_id}/edit")
def update_plant(cluster_id: int, plant_id: int, repo: RepoDep, form: PlantFormDep) -> Response:
    """Save the plant form and return to the cluster's plants section."""
    require_plant_in_cluster(repo, cluster_id, plant_id)
    repo.update_plant(
        plant_id,
        **form.fields(),
    )
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#plants", status_code=303)


@router.delete("/clusters/{cluster_id}/plants/{plant_id}", response_class=HTMLResponse)
def delete_plant(cluster_id: int, plant_id: int, repo: RepoDep) -> Response:
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    require_plant_in_cluster(repo, cluster_id, plant_id)
    repo.delete_plant(plant_id)
    repo.commit()
    return HTMLResponse("")

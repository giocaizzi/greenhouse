"""Plant web routes: list, create form, create, edit, delete."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from greenhouse_server.deps import RepoDep, require_cluster, require_plant_in_cluster
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


def _opt_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


def _plant_form_fields(
    *,
    species: str,
    category: str,
    water_needs: str,
    light_needs: str,
    ideal_temp_min: str,
    ideal_temp_max: str,
    ideal_humidity_min: str,
    ideal_humidity_max: str,
    notes: str,
) -> dict[str, Any]:
    """Map the plant form (create and edit share it) to repository fields: blank → None, bounds → float."""
    return {
        "species": species,
        "category": category or None,
        "water_needs": water_needs or None,
        "light_needs": light_needs or None,
        "ideal_temp_min": _opt_float(ideal_temp_min),
        "ideal_temp_max": _opt_float(ideal_temp_max),
        "ideal_humidity_min": _opt_float(ideal_humidity_min),
        "ideal_humidity_max": _opt_float(ideal_humidity_max),
        "notes": notes or None,
    }


@router.get("/clusters/{cluster_id}/plants")
def list_plants(cluster_id: int, repo: RepoDep):
    """Redirect the legacy plants URL to the detail page's plants section (301).

    Plants are rendered inline on the unified cluster detail page; the
    redirect keeps old bookmarks working and drops them at the right anchor.
    """
    require_cluster(repo, cluster_id)
    return RedirectResponse(url=f"/clusters/{cluster_id}#plants", status_code=301)


@router.get("/clusters/{cluster_id}/plants/new")
def new_plant_form(request: Request, cluster_id: int, repo: RepoDep):
    """Render the add-plant form."""
    cluster = require_cluster(repo, cluster_id)
    return templates.TemplateResponse(request, "plants/new.html", base_context(request, cluster=cluster))


@router.post("/clusters/{cluster_id}/plants")
def create_plant(
    request: Request,
    cluster_id: int,
    repo: RepoDep,
    species: str = Form(...),
    category: str = Form(""),
    water_needs: str = Form(""),
    light_needs: str = Form(""),
    ideal_temp_min: str = Form(""),
    ideal_temp_max: str = Form(""),
    ideal_humidity_min: str = Form(""),
    ideal_humidity_max: str = Form(""),
    notes: str = Form(""),
):
    """Add a plant from the form and return to the cluster's plants section."""
    require_cluster(repo, cluster_id)
    repo.add_plant(
        cluster_id=cluster_id,
        **_plant_form_fields(
            species=species,
            category=category,
            water_needs=water_needs,
            light_needs=light_needs,
            ideal_temp_min=ideal_temp_min,
            ideal_temp_max=ideal_temp_max,
            ideal_humidity_min=ideal_humidity_min,
            ideal_humidity_max=ideal_humidity_max,
            notes=notes,
        ),
    )
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#plants", status_code=303)


@router.get("/clusters/{cluster_id}/plants/{plant_id}/edit")
def edit_plant_form(request: Request, cluster_id: int, plant_id: int, repo: RepoDep):
    """Render the edit form of one of the cluster's plants."""
    cluster = require_cluster(repo, cluster_id)
    plant = require_plant_in_cluster(repo, cluster_id, plant_id)
    return templates.TemplateResponse(request, "plants/edit.html", base_context(request, cluster=cluster, plant=plant))


@router.post("/clusters/{cluster_id}/plants/{plant_id}/edit")
def update_plant(
    request: Request,
    cluster_id: int,
    plant_id: int,
    repo: RepoDep,
    species: str = Form(...),
    category: str = Form(""),
    water_needs: str = Form(""),
    light_needs: str = Form(""),
    ideal_temp_min: str = Form(""),
    ideal_temp_max: str = Form(""),
    ideal_humidity_min: str = Form(""),
    ideal_humidity_max: str = Form(""),
    notes: str = Form(""),
):
    """Save the plant form and return to the cluster's plants section."""
    require_plant_in_cluster(repo, cluster_id, plant_id)
    repo.update_plant(
        plant_id,
        **_plant_form_fields(
            species=species,
            category=category,
            water_needs=water_needs,
            light_needs=light_needs,
            ideal_temp_min=ideal_temp_min,
            ideal_temp_max=ideal_temp_max,
            ideal_humidity_min=ideal_humidity_min,
            ideal_humidity_max=ideal_humidity_max,
            notes=notes,
        ),
    )
    repo.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}#plants", status_code=303)


@router.delete("/clusters/{cluster_id}/plants/{plant_id}", response_class=HTMLResponse)
def delete_plant(cluster_id: int, plant_id: int, repo: RepoDep):
    """HTMX-targeted delete; returns an empty HTML body so the row is removed."""
    require_plant_in_cluster(repo, cluster_id, plant_id)
    repo.delete_plant(plant_id)
    repo.commit()
    return HTMLResponse("")

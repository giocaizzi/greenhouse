"""Plant web routes: list, create form, create."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from greenhouse_server.deps import RepoDep, require_cluster
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)


def _opt_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


@router.get("/clusters/{cluster_id}/plants")
def list_plants(request: Request, cluster_id: int, repo: RepoDep):
    cluster = require_cluster(repo, cluster_id)
    plants = repo.get_plants_in_cluster(cluster_id)
    return templates.TemplateResponse(
        request, "plants/list.html", base_context(request, cluster=cluster, plants=plants)
    )


@router.get("/clusters/{cluster_id}/plants/new")
def new_plant_form(request: Request, cluster_id: int, repo: RepoDep):
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
    require_cluster(repo, cluster_id)
    repo.add_plant(
        cluster_id=cluster_id,
        species=species,
        category=category or None,
        water_needs=water_needs or None,
        light_needs=light_needs or None,
        ideal_temp_min=_opt_float(ideal_temp_min),
        ideal_temp_max=_opt_float(ideal_temp_max),
        ideal_humidity_min=_opt_float(ideal_humidity_min),
        ideal_humidity_max=_opt_float(ideal_humidity_max),
        notes=notes or None,
    )
    repo.session.commit()
    return RedirectResponse(url=f"/clusters/{cluster_id}/plants", status_code=303)

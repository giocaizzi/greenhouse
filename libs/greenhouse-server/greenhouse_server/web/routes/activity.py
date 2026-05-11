"""Activity timeline web routes: full-page list + HTMX infinite-scroll fragment."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from greenhouse_core.schemas import ActivityEventResponse
from greenhouse_server.deps import RepoDep
from greenhouse_server.web.context import base_context
from greenhouse_server.web.templating import templates

router = APIRouter(include_in_schema=False)

_PAGE_SIZE = 50


def _fetch_events(
    repo,
    *,
    entity_type: str | None,
    source: str | None,
    severity: str | None,
    before: int | None,
) -> tuple[list[ActivityEventResponse], int | None]:
    """Fetch one page of events and return (items, next_cursor)."""
    rows = repo.list_activity_events(
        entity_type=entity_type or None,
        source=source or None,
        severity=severity or None,
        before=before,
        limit=_PAGE_SIZE,
    )
    items = [ActivityEventResponse.model_validate(r) for r in rows]
    next_cursor = items[-1].timestamp if len(items) == _PAGE_SIZE else None
    return items, next_cursor


@router.get("/activity")
def activity_list(
    request: Request,
    repo: RepoDep,
    entity_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    severity: str | None = Query(default=None),
):
    items, next_cursor = _fetch_events(repo, entity_type=entity_type, source=source, severity=severity, before=None)
    return templates.TemplateResponse(
        request,
        "activity/list.html",
        base_context(
            request,
            items=items,
            next_cursor=next_cursor,
            entity_type=entity_type or "",
            source=source or "",
            severity=severity or "",
        ),
    )


@router.get("/activity/page")
def activity_page(
    request: Request,
    repo: RepoDep,
    before: int | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    severity: str | None = Query(default=None),
):
    items, next_cursor = _fetch_events(repo, entity_type=entity_type, source=source, severity=severity, before=before)
    return templates.TemplateResponse(
        request,
        "partials/_activity_rows.html",
        base_context(
            request,
            items=items,
            next_cursor=next_cursor,
            entity_type=entity_type or "",
            source=source or "",
            severity=severity or "",
        ),
    )

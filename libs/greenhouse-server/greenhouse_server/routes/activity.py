"""Activity timeline routes."""

from fastapi import APIRouter, Query

from greenhouse_core.schemas import ActivityEventResponse, ActivityListResponse
from greenhouse_server.deps import RepoDep

router = APIRouter(tags=["activity"])


@router.get("/activity", response_model=ActivityListResponse)
def list_activity(
    repo: RepoDep,
    entity_type: str | None = Query(default=None),
    entity_id: int | None = Query(default=None),
    source: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    before: int | None = Query(default=None),
) -> ActivityListResponse:
    """List the cross-cutting activity timeline, newest events first.

    Supports cursor-based pagination: pass `before` as the Unix timestamp of
    the last item received to fetch the next page. A `next_cursor` is included
    in the response when more items may exist.

    Args:
        entity_type: Filter by entity type (e.g. `cluster`, `plant`, `sensor`).
        entity_id: Filter by numeric entity identifier.
        source: Filter by event source (e.g. `irrigation`, `learning`).
        severity: Filter by severity level (`info`, `warning`, `error`).
        limit: Maximum number of items per page (default 100, max 500).
        before: Cursor timestamp — return only events older than this value.

    Returns:
        Ordered activity items and a `next_cursor` timestamp when more pages exist.
    """
    items = repo.list_activity_events(
        entity_type=entity_type,
        entity_id=entity_id,
        source=source,
        severity=severity,
        limit=limit,
        before=before,
    )
    next_cursor = items[-1].timestamp if len(items) == limit else None
    return ActivityListResponse(
        items=[ActivityEventResponse.model_validate(e) for e in items],
        next_cursor=next_cursor,
    )

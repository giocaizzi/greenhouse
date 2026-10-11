"""Activity timeline routes."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from greenhouse_core.schemas import ActivityEventResponse, ActivityListResponse
from greenhouse_server.deps import RepoDep

router = APIRouter(tags=["activity"])


@dataclass(frozen=True, slots=True)
class _ActivityQuery:
    """``GET /activity`` query parameters (one OpenAPI parameter each, in this order)."""

    entity_type: Annotated[str | None, Query()] = None
    entity_id: Annotated[int | None, Query()] = None
    source: Annotated[str | None, Query()] = None
    severity: Annotated[str | None, Query()] = None
    limit: Annotated[int, Query(ge=1, le=500)] = 100
    before: Annotated[int | None, Query()] = None


@router.get("/activity", response_model=ActivityListResponse)
def list_activity(repo: RepoDep, query: Annotated[_ActivityQuery, Depends()]) -> ActivityListResponse:
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
        entity_type=query.entity_type,
        entity_id=query.entity_id,
        source=query.source,
        severity=query.severity,
        limit=query.limit,
        before=query.before,
    )
    next_cursor = items[-1].timestamp if len(items) == query.limit else None
    return ActivityListResponse(
        items=[ActivityEventResponse.model_validate(e) for e in items],
        next_cursor=next_cursor,
    )

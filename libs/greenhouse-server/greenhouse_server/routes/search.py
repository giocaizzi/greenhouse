"""Global search route."""

from fastapi import APIRouter, Query

from greenhouse_core.schemas import SearchResponse
from greenhouse_server.deps import RepoDep
from greenhouse_server.services.search import search as _search

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchResponse, summary="Global search across all resources")
def global_search(
    repo: RepoDep,
    q: str = Query("", description="Search query"),
    limit: int = Query(20, ge=1, le=100, description="Maximum number of hits to return"),
):
    """Search clusters, plants, sensors, and irrigators by name or device ID prefix.

    Case-insensitive LIKE match on cluster name/location, plant species/notes,
    and sensor/irrigator name and Tuya device ID prefix. Results are capped at
    5 hits per entity type and then trimmed to ``limit`` total.

    Args:
        q: Free-text search query. Empty string returns no results.
        limit: Maximum total hits (default 20, max 100).

    Returns:
        The query string echoed back and a ranked list of matching hits with
        entity type, label, sublabel, and deep-link href.
    """
    hits = _search(repo, q, limit=limit)
    return SearchResponse(query=q, hits=hits)

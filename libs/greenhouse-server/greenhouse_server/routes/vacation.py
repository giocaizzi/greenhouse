"""Vacation window routes."""

from fastapi import APIRouter, status

from greenhouse_core.schemas import (
    SuccessResponse,
    UpdateVacationWindowRequest,
    VacationCreateRequest,
    VacationListResponse,
    VacationResponse,
)
from greenhouse_server.deps import RepoDep, require_vacation_window, require_valid_vacation_range

router = APIRouter(prefix="/vacation", tags=["vacation"])


@router.get("", response_model=VacationListResponse, summary="List vacation windows")
def list_vacation_windows(repo: RepoDep):
    """Return all vacation windows together with the currently active one.

    Returns:
        A list of all windows and the active window (spanning now), if any.
    """
    return VacationListResponse.model_validate(
        {"active": repo.get_active_vacation(), "items": repo.list_vacation_windows()}
    )


@router.post(
    "", response_model=VacationResponse, status_code=status.HTTP_201_CREATED, summary="Create a vacation window"
)
def create_vacation_window(request: VacationCreateRequest, repo: RepoDep):
    """Schedule a vacation window that makes the engine ration water to last the trip.

    While the window is active the decision engine appends a
    ``VACATION_ACTIVE`` reason to every decision and, for clusters whose
    irrigators have both ``reservoir_l`` and ``flow_rate_l_per_min`` set,
    enforces a per-day burn-down budget: each irrigation's duration is trimmed
    to fit the remaining tank allowance (``VACATION_RATIONING``), or flipped to
    SKIP when no usable water remains this cycle (``VACATION_BUDGET_EXHAUSTED``).
    When no irrigator in a cluster has capacity configured the window is purely
    informational and irrigation proceeds exactly as it would normally.

    Args:
        request: Window start and end as Unix timestamps, optional contact email
            and free-text notes.

    Returns:
        The newly created vacation window.

    Raises:
        HTTPException: 400 if ``starts_at`` is not strictly before ``ends_at``.
    """
    require_valid_vacation_range(request.starts_at, request.ends_at)
    window = repo.add_vacation_window(
        starts_at=request.starts_at,
        ends_at=request.ends_at,
        contact_email=request.contact_email,
        notes=request.notes,
    )
    repo.commit()
    return window


@router.put("/{window_id}", response_model=VacationResponse, summary="Update a vacation window")
def update_vacation_window(window_id: int, request: UpdateVacationWindowRequest, repo: RepoDep):
    """Partially update a vacation window.

    Only fields present in the request body are modified; omitted fields are
    left unchanged. The effective ``starts_at`` and ``ends_at`` after the patch
    must satisfy ``starts_at < ends_at`` — otherwise the request is rejected
    so the vacation gate can never end up reversed.

    Args:
        window_id: Numeric ID of the vacation window to update.
        request: Any subset of starts_at, ends_at, contact_email, notes.

    Returns:
        The updated vacation window.

    Raises:
        HTTPException: 404 if no window with that ID exists, 400 if the
            resulting ``starts_at`` is not strictly before ``ends_at``.
    """
    row = require_vacation_window(repo, window_id)
    effective_start = request.starts_at if request.starts_at is not None else row.starts_at
    effective_end = request.ends_at if request.ends_at is not None else row.ends_at
    require_valid_vacation_range(effective_start, effective_end)
    updated = repo.update_vacation_window(window_id, **request.model_dump(exclude_unset=True))
    repo.commit()
    return updated


@router.delete("/{window_id}", response_model=SuccessResponse, summary="Delete a vacation window")
def delete_vacation_window(window_id: int, repo: RepoDep):
    """Remove a vacation window by ID.

    Args:
        window_id: Numeric ID of the vacation window to delete.

    Returns:
        Success acknowledgement.

    Raises:
        HTTPException: 404 if no window with that ID exists.
    """
    require_vacation_window(repo, window_id)
    repo.delete_vacation_window(window_id)
    repo.commit()
    return SuccessResponse(success=True)

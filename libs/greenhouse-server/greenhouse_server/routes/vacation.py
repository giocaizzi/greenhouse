"""Vacation window routes."""

from fastapi import APIRouter, HTTPException, status

from greenhouse_core.schemas import SuccessResponse, VacationCreateRequest, VacationListResponse, VacationResponse
from greenhouse_server.deps import RepoDep

router = APIRouter(prefix="/vacation", tags=["vacation"])


@router.get("", response_model=VacationListResponse, summary="List vacation windows")
def list_vacation_windows(repo: RepoDep):
    """Return all vacation windows together with the currently active one.

    Returns:
        A list of all windows and the active window (spanning now), if any.
    """
    return VacationListResponse(
        active=repo.get_active_vacation(),
        items=repo.list_vacation_windows(),
    )


@router.post(
    "", response_model=VacationResponse, status_code=status.HTTP_201_CREATED, summary="Create a vacation window"
)
def create_vacation_window(request: VacationCreateRequest, repo: RepoDep):
    """Schedule a vacation window during which the irrigation engine will hold.

    Args:
        request: Window start and end as Unix timestamps, optional contact email
            and free-text notes.

    Returns:
        The newly created vacation window.
    """
    window = repo.add_vacation_window(
        starts_at=request.starts_at,
        ends_at=request.ends_at,
        contact_email=request.contact_email,
        notes=request.notes,
    )
    repo.session.commit()
    return window


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
    deleted = repo.delete_vacation_window(window_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Vacation window not found")
    repo.session.commit()
    return SuccessResponse(success=True)

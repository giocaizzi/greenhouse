"""Device registration rules shared by the JSON API and the web UI.

Both surfaces register irrigators (and sensors) through these functions so the
validation, the duplicate-device handling and the capacity follow-up are one
implementation. Each caller keeps its own I/O mapping (status code, detail or
template) and commits on success; on a refusal the session is already rolled back.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from greenhouse_core.repository import IrrigationRepository, IrrigatorExistsError

__all__ = ["DeviceIdExistsError", "IrrigatorExistsError", "create_irrigator"]


class DeviceIdExistsError(LookupError):
    """The Tuya device id is already registered (the database's unique constraint refused it)."""


def create_irrigator(
    repo: IrrigationRepository,
    cluster_id: int,
    *,
    tuya_device_id: str,
    name: str,
    irrigator_type: str,
    config: dict[str, Any],
    reservoir_l: float | None = None,
    flow_rate_l_per_min: float | None = None,
) -> int:
    """Add the cluster's irrigator, then persist any capacity values; return its id.

    ``add_irrigator`` does not take the capacity columns, so they are written in a
    follow-up update when either is supplied. The caller commits.

    Raises:
        IrrigatorExistsError: the cluster already has an irrigator (session rolled back).
        DeviceIdExistsError: the Tuya device id is already registered (session rolled back).
    """
    try:
        irrigator_id = repo.add_irrigator(
            cluster_id=cluster_id,
            tuya_device_id=tuya_device_id,
            name=name,
            irrigator_type=irrigator_type,
            config=config,
        )
    except IrrigatorExistsError:
        repo.session.rollback()
        raise
    except IntegrityError:
        repo.session.rollback()
        raise DeviceIdExistsError(tuya_device_id) from None
    if reservoir_l is not None or flow_rate_l_per_min is not None:
        repo.update_irrigator(irrigator_id, reservoir_l=reservoir_l, flow_rate_l_per_min=flow_rate_l_per_min)
    return irrigator_id

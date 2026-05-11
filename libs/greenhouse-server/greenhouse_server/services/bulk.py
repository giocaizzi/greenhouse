"""Bulk operations service — emergency stop all irrigators."""

import time

from greenhouse_core.devices import TuyaDeviceManager
from greenhouse_core.repository import IrrigationRepository


def stop_all_irrigators(
    repo: IrrigationRepository,
    device_manager: TuyaDeviceManager | None,
) -> tuple[int, list[str]]:
    """Send an emergency stop to every irrigator and log the event.

    When ``device_manager`` is None (test environment or missing credentials)
    the stop command is skipped but the event is still logged and the irrigator
    is counted as stopped.

    Args:
        repo: Active repository for listing irrigators and logging events.
        device_manager: Live device manager, or None in test / credential-less envs.

    Returns:
        A (stopped_count, errors) tuple where errors contains one entry per
        irrigator that raised an exception during device communication.
    """
    irrigators = repo.list_all_irrigators()
    stopped = 0
    errors: list[str] = []

    for irrigator in irrigators:
        try:
            if device_manager is not None:
                device_manager.irrigator_stop(irrigator)

            repo.add_irrigation_event(
                irrigator_id=irrigator.id,
                action="stop",
                triggered_by="emergency",
                notes="kill switch",
                timestamp=int(time.time()),
            )
            stopped += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"irrigator {irrigator.id} ({irrigator.name}): {exc}")

    repo.session.commit()
    return stopped, errors

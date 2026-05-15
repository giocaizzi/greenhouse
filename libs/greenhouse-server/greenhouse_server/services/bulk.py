"""Bulk operations service — emergency stop all irrigators."""

import time

from greenhouse_core.devices import DeviceRegistry, UnknownDeviceModel
from greenhouse_core.repository import IrrigationRepository


def stop_all_irrigators(
    repo: IrrigationRepository,
    registry: DeviceRegistry | None,
) -> tuple[int, list[str]]:
    """Send an emergency stop to every irrigator and log the event.

    When ``registry`` is None (test environment or missing credentials) the
    stop command is skipped but the event is still logged and the irrigator
    is counted as stopped. Irrigators whose model is not in the registry
    surface as a per-device error rather than aborting the batch.

    Args:
        repo: Active repository for listing irrigators and logging events.
        registry: Device registry, or None in test / credential-less envs.

    Returns:
        A (stopped_count, errors) tuple where errors contains one entry per
        irrigator that raised an exception during device communication.
    """
    irrigators = repo.list_all_irrigators()
    stopped = 0
    errors: list[str] = []

    for irrigator in irrigators:
        try:
            if registry is not None:
                try:
                    adapter = registry.get_irrigator(irrigator)
                except UnknownDeviceModel as exc:
                    errors.append(f"irrigator {irrigator.id} ({irrigator.name}): {exc}")
                    continue
                adapter.stop(irrigator)

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

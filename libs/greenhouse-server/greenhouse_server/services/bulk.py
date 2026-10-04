"""Bulk operations service — emergency stop all irrigators."""

import time
from typing import NamedTuple

from greenhouse_core.devices import DeviceRegistry, UnknownDeviceModel
from greenhouse_core.models import EVENT_ACTION_STOP, TRIGGERED_BY_EMERGENCY
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.notify import NtfyClient, maybe_notify


class StopAllResult(NamedTuple):
    """``stop_all_irrigators`` result; still unpacks as ``stopped, errors``."""

    stopped: int
    errors: list[str]


def stop_all_irrigators(
    repo: IrrigationRepository,
    registry: DeviceRegistry | None,
    notifier: NtfyClient | None = None,
) -> StopAllResult:
    """Send an emergency stop to every irrigator, log the events, then push the emergency notice.

    When ``registry`` is None (test environment or missing credentials) the
    stop command is skipped but the event is still logged and the irrigator
    is counted as stopped. Irrigators whose model is not in the registry
    surface as a per-device error rather than aborting the batch. Commits
    because the stop commands have already reached the hardware: their events
    must be durable whatever the caller does next — and before the push, so it
    never announces unsaved events. The one emergency-stop path for the JSON
    API and the web kill switch, so both notify alike.

    Args:
        repo: Active repository for listing irrigators and logging events.
        registry: Device registry, or None in test / credential-less envs.
        notifier: ntfy client, or None when notifications are unconfigured.

    Returns:
        A ``(stopped, errors)`` StopAllResult where errors contains one entry per
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
                action=EVENT_ACTION_STOP,
                triggered_by=TRIGGERED_BY_EMERGENCY,
                notes="kill switch",
                timestamp=int(time.time()),
            )
            stopped += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"irrigator {irrigator.id} ({irrigator.name}): {exc}")

    repo.commit()
    maybe_notify(
        notifier,
        repo.get_preferences(),
        "emergency",
        lambda n: n.notify_irrigation(
            triggered_by=TRIGGERED_BY_EMERGENCY,
            irrigator_name=f"{stopped} irrigator(s)",
            detail="kill switch" + (f", {len(errors)} error(s)" if errors else ""),
        ),
    )
    return StopAllResult(stopped, errors)

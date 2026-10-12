"""Bulk operations service — emergency stop all irrigators."""

import time
from typing import NamedTuple

from greenhouse_core.devices import DeviceRegistry
from greenhouse_core.models import EVENT_ACTION_STOP, TRIGGERED_BY_EMERGENCY, Irrigator
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.notify import NtfyClient, maybe_notify


class StopAllResult(NamedTuple):
    """``stop_all_irrigators`` result; still unpacks as ``stopped, errors``."""

    stopped: int
    errors: list[str]


def _stop_one(repo: IrrigationRepository, registry: DeviceRegistry | None, irrigator: Irrigator) -> str | None:
    """Stop one irrigator and log its ``stop``/``emergency`` event; returns the error, or None when it stopped.

    A refused stop (``adapter.stop()`` → ``(False, msg)``) is an error like a raised one: the
    device may still be running, so it is neither counted nor recorded as stopped.
    """
    label = f"irrigator {irrigator.id} ({irrigator.name})"
    if registry is not None:
        try:
            stop_ok, stop_msg = registry.get_irrigator(irrigator).stop(irrigator)
        except Exception as exc:  # noqa: BLE001 — one device's failure must not stop the others
            return f"{label}: {exc}"
        if not stop_ok:
            return f"{label}: stop failed: {stop_msg}"
    repo.add_irrigation_event(
        irrigator_id=irrigator.id,
        action=EVENT_ACTION_STOP,
        triggered_by=TRIGGERED_BY_EMERGENCY,
        notes="kill switch",
        timestamp=int(time.time()),
    )
    return None


def stop_all_irrigators(
    repo: IrrigationRepository,
    registry: DeviceRegistry | None,
    notifier: NtfyClient | None = None,
) -> StopAllResult:
    """Send an emergency stop to every irrigator, log the events, then push the emergency notice.

    When ``registry`` is None (test environment or missing credentials) the
    stop command is skipped but the event is still logged and the irrigator
    is counted as stopped. An irrigator whose model is not in the registry,
    whose stop raises, or whose device refuses the stop (``(False, msg)``)
    surfaces as a per-device error — not counted, no ``stop`` event — rather
    than aborting the batch. Commits because the stop commands have already
    reached the hardware: their events must be durable whatever the caller
    does next — and before the push, so it never announces unsaved events.
    The one emergency-stop path for the JSON API and the web kill switch, so
    both notify alike.

    Args:
        repo: Active repository for listing irrigators and logging events.
        registry: Device registry, or None in test / credential-less envs.
        notifier: ntfy client, or None when notifications are unconfigured.

    Returns:
        A ``(stopped, errors)`` StopAllResult where errors contains one entry per
        irrigator that could not be confirmed stopped.
    """
    stopped = 0
    errors: list[str] = []
    for irrigator in repo.list_all_irrigators():
        error = _stop_one(repo, registry, irrigator)
        if error is None:
            stopped += 1
        else:
            errors.append(error)

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

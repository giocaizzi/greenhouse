"""The server's actuation gates: what may still stop an IRRIGATE decision before ``adapter.start``.

The engine decides; these gates hold the run when the server knows better, in a fixed order —
the first that fires wins:

1. device health — an open NO_WATER / RAIN / OFFLINE alarm on the irrigator (a real-world
   block outranks a policy);
2. per-day caps — ``max_events_per_day`` / ``daily_cap_minutes``, cluster row over global row
   (B-4);
3. global dry run — ``dry_run_global``, the last word before the pump (S1).

``force`` bypasses none of them. Each gate only *describes* its block (:class:`GateBlock`);
:class:`~greenhouse_server.services.irrigation.IrrigationService` turns it into the logged skip.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from greenhouse_core.logic.decision import Reason, Severity, TriggerCode
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.caps import daily_cap_violation
from greenhouse_server.services.health_alarms import HEALTH_ALARM_TO_TRIGGER

if TYPE_CHECKING:
    from greenhouse_core.logic.decision import IrrigationDecision
    from greenhouse_core.models import Irrigator
    from greenhouse_server.services.health_monitor import DeviceHealthMonitor


@dataclass(frozen=True, slots=True)
class GateBlock:
    """Why a gate held the run: the Reason that leads the trail, the activity payload, any alarms."""

    lead: Reason
    payload: dict[str, Any]
    blocking_alarms: list[str] | None = None


def first_gate_block(
    repo: IrrigationRepository,
    health_monitor: "DeviceHealthMonitor | None",
    cluster_id: int,
    irrigator: "Irrigator",
    decision: "IrrigationDecision",
) -> GateBlock | None:
    """The first gate that holds this run, in the fixed order above; None when it may actuate."""
    return (
        _health_block(health_monitor, irrigator)
        or _caps_block(repo, cluster_id, irrigator, decision.duration_minutes)
        or _dry_run_block(repo, irrigator, decision.duration_minutes)
    )


def _health_block(health_monitor: "DeviceHealthMonitor | None", irrigator: "Irrigator") -> GateBlock | None:
    """Device health: a blocking alarm in the monitor's cache (none wired → never blocks)."""
    if health_monitor is None:
        return None
    blocked, blocking_alarms = health_monitor.is_actuation_blocked(irrigator)
    if not blocked:
        return None
    primary = blocking_alarms[0]
    alarms = [a.value for a in blocking_alarms]
    return GateBlock(
        Reason(
            code=HEALTH_ALARM_TO_TRIGGER[primary],
            message=f"Actuation blocked by device health: {primary.value} on '{irrigator.name}'",
            severity=Severity.CRITICAL,
        ),
        payload={"blocking_alarms": alarms, "irrigator_id": irrigator.id},
        blocking_alarms=alarms,
    )


def _caps_block(repo: IrrigationRepository, cluster_id: int, irrigator: "Irrigator", duration: int) -> GateBlock | None:
    """Per-day caps: the same effective caps the manual start refuses on (B-4)."""
    violation = daily_cap_violation(repo, cluster_id, irrigator.id, duration)
    if violation is None:
        return None
    return GateBlock(
        Reason(
            code=TriggerCode.DAILY_CAP_HIT,
            message=f"daily cap hit: {violation} ({duration}min run on '{irrigator.name}' not started)",
            severity=Severity.WARNING,
        ),
        payload={"daily_cap": violation, "irrigator_id": irrigator.id, "duration_minutes": duration},
    )


def _dry_run_block(repo: IrrigationRepository, irrigator: "Irrigator", duration: int) -> GateBlock | None:
    """Global dry run: evaluated and logged, never actuated, saying what would have run (S1)."""
    if not repo.get_preferences().dry_run_global:
        return None
    return GateBlock(
        Reason(
            code=TriggerCode.DRY_RUN_GLOBAL,
            message=f"global dry run: would have irrigated {duration}min on '{irrigator.name}' (pump not started)",
            severity=Severity.WARNING,
        ),
        payload={"dry_run_global": True, "irrigator_id": irrigator.id, "duration_minutes": duration},
    )

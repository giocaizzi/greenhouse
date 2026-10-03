"""Vacation windows: the shared write-path rule and the web UI's water-budget projection.

``validate_vacation_range`` is the one ``starts_at < ends_at`` rule every write path
(API create/update, web create/edit) applies. The budget helpers are pure read-only
projections that turn per-irrigator capacity (``reservoir_l`` + ``flow_rate_l_per_min``)
and a vacation window into a human-readable readout. No actuation, no writes — the
engine owns the real rationing math (see ``logic/engine.py``); this only *projects* it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from greenhouse_core.constants import SECONDS_PER_DAY, VACATION_RESERVOIR_USABLE_FRACTION
from greenhouse_core.repository import IrrigationRepository


class VacationRangeError(ValueError):
    """A vacation window does not start strictly before it ends; ``str(exc)`` is the user-facing message."""


def validate_vacation_range(starts_at: int, ends_at: int) -> None:
    """Reject a reversed or empty window so the vacation gate can never end up reversed.

    Raises:
        VacationRangeError: ``starts_at`` is not strictly before ``ends_at``.
    """
    if starts_at >= ends_at:
        raise VacationRangeError("starts_at must be < ends_at")


@dataclass(frozen=True)
class ClusterBudget:
    """Projected vacation water budget for a single cluster."""

    cluster_id: int
    cluster_name: str
    vacation_days: int
    total_reservoir_l: float
    usable_reservoir_l: float
    daily_budget_l: float


def vacation_days(starts_at: int, ends_at: int) -> int:
    """Return the whole-day span of a window, floored at 1 day.

    Args:
        starts_at: Window start as a Unix timestamp.
        ends_at: Window end as a Unix timestamp.

    Returns:
        The number of days the window covers (at least 1).
    """
    return max(1, math.ceil((ends_at - starts_at) / SECONDS_PER_DAY))


def cluster_budgets(repo: IrrigationRepository, starts_at: int, ends_at: int) -> list[ClusterBudget]:
    """Project the per-day water budget for every capacity-configured cluster.

    A cluster contributes a readout only when its irrigator has both
    ``reservoir_l`` and ``flow_rate_l_per_min`` set. Usable volume is
    ``reservoir_l * VACATION_RESERVOIR_USABLE_FRACTION``; the daily budget is
    that usable volume divided by the vacation span in days. Clusters with no
    irrigator or no configured capacity are omitted (the caller renders nothing
    for them, matching the engine's no-op behavior).

    Args:
        repo: Active repository session.
        starts_at: Vacation window start as a Unix timestamp.
        ends_at: Vacation window end as a Unix timestamp.

    Returns:
        One :class:`ClusterBudget` per capacity-configured cluster, ordered by
        cluster name.
    """
    days = vacation_days(starts_at, ends_at)
    budgets: list[ClusterBudget] = []
    for cluster in repo.list_clusters():
        irrigator = repo.get_irrigator_for_cluster(cluster.id)
        if not irrigator or not (irrigator.reservoir_l and irrigator.flow_rate_l_per_min):
            continue
        total = irrigator.reservoir_l
        usable = total * VACATION_RESERVOIR_USABLE_FRACTION
        budgets.append(
            ClusterBudget(
                cluster_id=cluster.id,
                cluster_name=cluster.name,
                vacation_days=days,
                total_reservoir_l=round(total, 2),
                usable_reservoir_l=round(usable, 2),
                daily_budget_l=round(usable / days, 2),
            )
        )
    budgets.sort(key=lambda b: b.cluster_name)
    return budgets

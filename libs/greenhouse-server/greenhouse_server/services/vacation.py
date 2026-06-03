"""Vacation water-budget projection for the web UI.

Pure read-only helpers that turn per-irrigator capacity (``reservoir_l`` +
``flow_rate_l_per_min``) and a vacation window into a human-readable budget
readout. No actuation, no writes — the engine owns the real rationing math
(see ``logic/engine.py``); this only *projects* it for display.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from greenhouse_core.constants import VACATION_RESERVOIR_USABLE_FRACTION
from greenhouse_core.repository import IrrigationRepository

_SECONDS_PER_DAY = 86_400


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
    return max(1, math.ceil((ends_at - starts_at) / _SECONDS_PER_DAY))


def cluster_budgets(repo: IrrigationRepository, starts_at: int, ends_at: int) -> list[ClusterBudget]:
    """Project the per-day water budget for every capacity-configured cluster.

    A cluster contributes a readout only when at least one of its irrigators
    has both ``reservoir_l`` and ``flow_rate_l_per_min`` set. Usable volume is
    ``reservoir_l * VACATION_RESERVOIR_USABLE_FRACTION`` summed across those
    irrigators; the daily budget is that usable volume divided by the vacation
    span in days. Clusters with no configured capacity are omitted (the caller
    renders nothing for them, matching the engine's no-op behavior).

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
        irrigators = repo.get_irrigators_in_cluster(cluster.id)
        cap = [i for i in irrigators if i.reservoir_l and i.flow_rate_l_per_min]
        if not cap:
            continue
        total = sum(i.reservoir_l for i in cap)
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

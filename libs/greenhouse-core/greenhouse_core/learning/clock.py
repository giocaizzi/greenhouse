"""The learning heuristics' clock: the light season in the preferences timezone."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from greenhouse_core.logic.timing import local_now

if TYPE_CHECKING:
    from greenhouse_core.repository import IrrigationRepository


def light_month(db: IrrigationRepository) -> int:
    """The current month in the preferences timezone — the clock the engine's light season reads.

    Learning and the engine must agree on the light season around a month
    boundary, so both take the month from ``UserPreferences.timezone``
    (UTC when unset) via :func:`~greenhouse_core.logic.timing.local_now`.
    """
    prefs = db.get_preferences()
    return local_now(int(time.time()), prefs.timezone if prefs else None).month

"""Robust cleaning of noisy sensor series so decisions key on a clear signal.

Capacitive soil probes and Tuya's cloud logs are messy: single-sample **spikes**
that revert one reading later, **flat runs** when a probe wedges, and **dirty
out-of-range** values from comms glitches (a humidity probe reporting 250 %, a
negative-temperature blip). Left unfiltered these corrupt the very aggregates the
engine trusts — and because the *driest plant drives the call* (invariant #2), a
single spurious low ``soil_moisture`` reading is enough to trigger a needless
irrigation.

Raw rows in ``sensor_readings`` are the permanent record and are **never
mutated**. This module produces a *cleaned view* at read time, consumed by the
decision snapshot (``logic/sensors.py``) and trend analysis (``logic/trends.py``):

* **Range gate** — values outside per-metric physical bounds
  (``SENSOR_PHYSICAL_RANGES``) are dropped as dirty.
* **Hampel spike filter** — the standard robust time-series outlier test: a point
  is rejected when it deviates from its rolling-window median by more than
  ``n`` scaled MADs. The MAD scale carries a floor (``CLEANING_MAD_FLOOR``) so a
  flat run (MAD ≈ 0) does not make every later genuine change look anomalous.

Cleaning is **field-independent and advisory**: an outlier in ``soil_moisture``
does not discard that reading's ``temperature``. Rejected values become ``None``
so the existing ``is not None`` filters downstream simply skip them.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from greenhouse_core.constants import (
    CLEANING_HAMPEL_MIN_READINGS,
    CLEANING_HAMPEL_N_SIGMA,
    CLEANING_HAMPEL_WINDOW_RADIUS,
    CLEANING_MAD_FLOOR,
    CLEANING_MAD_SCALE,
    SENSOR_PHYSICAL_RANGES,
)

# Numeric metrics cleaned independently. ``battery_state`` / ``water_warning``
# are categorical/boolean device flags and pass through untouched.
_NUMERIC_FIELDS = ("temperature", "soil_moisture", "env_humidity", "light")


@dataclass
class CleanedReading:
    """A reading mirroring ``SensorReading``'s read attributes, post-cleaning.

    Same field names as the ORM row so existing consumers iterate over it
    unchanged; numeric values judged dirty or spiky are ``None``.
    """

    timestamp: int
    temperature: float | None = None
    soil_moisture: float | None = None
    env_humidity: float | None = None
    light: int | None = None
    battery_state: str | None = None
    water_warning: bool | None = None


def _hampel_outlier_mask(values: list[float], radius: int, n_sigma: float) -> list[bool]:
    """Flag spikes in a contiguous numeric series via a centered Hampel filter.

    Args:
        values: The in-range values for a single metric, in time order.
        radius: Half-width of the rolling window (window size ``2*radius+1``).
        n_sigma: Deviation, in scaled MADs, beyond which a point is a spike.

    Returns:
        A per-index mask; ``True`` marks the value as an outlier to drop. With
        fewer than ``CLEANING_HAMPEL_MIN_READINGS`` points the series is too
        short to judge spikes, so nothing is flagged.
    """
    n = len(values)
    mask = [False] * n
    if n < CLEANING_HAMPEL_MIN_READINGS:
        return mask
    for i in range(n):
        lo = max(0, i - radius)
        hi = min(n, i + radius + 1)
        window = values[lo:hi]
        median = statistics.median(window)
        mad = statistics.median([abs(v - median) for v in window])
        # Floor the scale so a flat window (MAD≈0) doesn't make any change look
        # infinitely anomalous — that would shred a legitimate step change.
        scale = max(CLEANING_MAD_SCALE * mad, CLEANING_MAD_FLOOR)
        if abs(values[i] - median) > n_sigma * scale:
            mask[i] = True
    return mask


def clean_readings(readings) -> list[CleanedReading]:
    """Return a cleaned, chronologically-sorted view of one sensor's readings.

    Apply per metric to a single sensor's series (never a mix of sensors): each
    metric is range-gated, then spike-filtered independently. Raw rows are not
    modified.

    Args:
        readings: ``SensorReading`` rows (any order) for one sensor.

    Returns:
        ``CleanedReading`` items sorted oldest→newest with dirty/spiky numeric
        values replaced by ``None``.
    """
    ordered = sorted(readings, key=lambda r: r.timestamp)
    cleaned = [
        CleanedReading(
            timestamp=r.timestamp,
            temperature=r.temperature,
            soil_moisture=r.soil_moisture,
            env_humidity=r.env_humidity,
            light=r.light,
            battery_state=r.battery_state,
            water_warning=r.water_warning,
        )
        for r in ordered
    ]

    for field in _NUMERIC_FIELDS:
        low, high = SENSOR_PHYSICAL_RANGES[field]
        # 1) Range gate — drop physically impossible values outright.
        for cr in cleaned:
            value = getattr(cr, field)
            if value is not None and not (low <= value <= high):
                setattr(cr, field, None)

        # 2) Spike filter over the surviving in-range values. Track original
        # indices so flagged positions can be nulled back in the full series.
        indices = [i for i, cr in enumerate(cleaned) if getattr(cr, field) is not None]
        values = [float(getattr(cleaned[i], field)) for i in indices]
        mask = _hampel_outlier_mask(values, CLEANING_HAMPEL_WINDOW_RADIUS, CLEANING_HAMPEL_N_SIGMA)
        for position, is_outlier in enumerate(mask):
            if is_outlier:
                setattr(cleaned[indices[position]], field, None)

    return cleaned

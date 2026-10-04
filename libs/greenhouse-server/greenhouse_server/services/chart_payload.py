"""Fixed-key shapes of the plant / cluster chart payloads built in :mod:`greenhouse_server.services.charts`."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from greenhouse_server.services.charts import Metric


class ChartDataset(TypedDict):
    """One sensor's series: ``(timestamp, value)`` points, oldest first."""

    sensor_id: int
    sensor_name: str
    points: list[tuple[int, float]]


class ChartEvent(TypedDict):
    """One irrigation-event marker on a chart."""

    timestamp: int
    action: str
    duration_minutes: int | None


class ChartThreshold(TypedDict):
    """The metric's target band (either bound may be open) and where it came from."""

    min: float | None
    max: float | None
    source: str


class ChartPayload(TypedDict):
    """A plant or cluster chart: a plain dict at runtime (``ChartPayloadResponse``, the web chart JSON)."""

    metric: Metric
    hours: int
    datasets: list[ChartDataset]
    events: list[ChartEvent]
    threshold: ChartThreshold

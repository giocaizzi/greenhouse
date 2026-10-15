"""Long chart ranges send hourly means; up to 14 days every raw reading (issue #178)."""

from __future__ import annotations

import json
import time
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

from greenhouse_core.constants import CHART_RAW_MAX_HOURS, SECONDS_PER_HOUR
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.chart_series import bucket_points

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

LONG_RANGE_HOURS = 720  # 30 days
READING_STEP_SECONDS = 720  # 5 readings per hour, like the Tuya sensors
READINGS = 50  # 10 hours of readings


@pytest.fixture
def repo(app: FastAPI) -> Iterator[IrrigationRepository]:
    """A repository on the app's database, closed after the test."""
    session = app.state.session_factory()
    try:
        yield IrrigationRepository(session)
    finally:
        session.close()


@pytest.fixture
def with_readings(seeded_client: TestClient, repo: IrrigationRepository) -> TestClient:
    """The seeded cluster's sensor with ten hours of readings, five an hour."""
    start = (int(time.time()) // SECONDS_PER_HOUR - 12) * SECONDS_PER_HOUR
    for i in range(READINGS):
        ts = start + i * READING_STEP_SECONDS
        repo.add_sensor_reading(1, timestamp=ts, soil_moisture=40.0 + i % 5, env_humidity=60.0, light=100)
    repo.commit()
    return seeded_client


def test_bucket_points_keeps_raw_up_to_fourteen_days() -> None:
    """At the raw limit the points pass through unchanged."""
    points = [(0, 1.0), (60, 2.0)]
    assert bucket_points(points, CHART_RAW_MAX_HOURS) == points


def test_bucket_points_averages_per_hour_beyond_fourteen_days() -> None:
    """Past the raw limit each hour becomes one mean point at the hour's start."""
    points = [(3600, 1.0), (4000, 2.0), (7199, 4.0), (7200, 5.0)]
    assert bucket_points(points, CHART_RAW_MAX_HOURS + 1) == [(3600, 2.33), (7200, 5.0)]


@pytest.mark.parametrize("scope", ["clusters", "plants"])
def test_chart_data_is_hourly_for_long_ranges(with_readings: TestClient, scope: str) -> None:
    """A 30-day cluster or plant chart sends one point per hour, a 14-day one every reading."""
    url = f"/api/v1/{scope}/1/chart-data?metric=soil_moisture&hours="
    long_points = with_readings.get(url + str(LONG_RANGE_HOURS)).json()["datasets"][0]["points"]
    short_points = with_readings.get(url + str(CHART_RAW_MAX_HOURS)).json()["datasets"][0]["points"]
    assert len(short_points) == READINGS
    assert len(long_points) == READINGS * READING_STEP_SECONDS // SECONDS_PER_HOUR
    assert all(ts % SECONDS_PER_HOUR == 0 for ts, _ in long_points)
    assert {value for _, value in long_points} == {42.0}


def test_overlay_is_hourly_for_long_ranges(with_readings: TestClient) -> None:
    """The 30-day multi-metric overlay averages per hour instead of per minute."""
    resp = with_readings.get(f"/clusters/1/overlay-fragment?hours={LONG_RANGE_HOURS}")
    assert resp.status_code == HTTPStatus.OK
    marker = 'id="chart-data-overlay">'
    start = resp.text.index(marker) + len(marker)
    data = json.loads(resp.text[start : resp.text.index("</script>", start)])
    soil = next(d for d in data["datasets"] if d["metric"] == "soil")
    assert len(soil["points"]) == READINGS * READING_STEP_SECONDS // SECONDS_PER_HOUR

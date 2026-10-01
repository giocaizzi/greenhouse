"""Characterization of invariant 10 — cleaned view for judgements, raw rows for the archive (I10).

One soil sensor reports a steady ~45 % series whose **latest** reading is a
single 5 % spike (rejected by the Hampel filter), and a second sensor whose
only recent reading is physically impossible (150 %, rejected by the range
gate). Pinned:

- *archive / display* — chart JSON, cluster history and the data-quality report
  read the raw rows: the spike and the out-of-range value are still there, and
  the second sensor counts as "fresh" (no ``stale_sensor`` issue);
- *judgement* — the ``/status`` decision snapshot, a dry-run ``/irrigate`` and
  ``/monitor`` read the cleaned view: the driest value is 44 %, not 5 %, so the
  cluster is not irrigated as "very dry".

Golden: ``tests/golden/orchestration/raw_vs_clean.json``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import time_machine

from golden import FROZEN_INSTANT, FROZEN_TS, assert_golden_json
from server.test_contract_pipeline import Pipeline

STEADY = (45.0, 46.0, 45.0, 44.0, 45.0, 46.0, 45.0, 44.0, 45.0)
SPIKE = 5.0
IMPOSSIBLE = 150.0


@pytest.fixture
def spiky(clean_env, frozen_clock):
    p = Pipeline()
    # Devices are registered (and the sensor→plant assignment opened) two days
    # earlier, so the assignment-aware plant chart covers every seeded reading.
    with time_machine.travel(FROZEN_INSTANT - timedelta(days=2), tick=False):
        ids = p.seed_cluster(soil=None)
        # Second sensor, no plant: one ancient sane reading + one fresh impossible one.
        second = p.api(
            "POST",
            f"/api/v1/clusters/{ids['cluster_id']}/sensors",
            {"tuya_device_id": "fake_sensor_002", "name": "Glitchy", "type": "soil_moisture"},
        )["id"]
    with p.repo() as repo:
        # Oldest first, every 30 min, the spike is the newest row (60 s ago).
        for i, soil in enumerate(STEADY):
            repo.add_sensor_reading(
                sensor_id=ids["sensor_id"],
                timestamp=FROZEN_TS - 60 - (len(STEADY) - i) * 1800,
                soil_moisture=soil,
                temperature=22.0,
            )
        repo.add_sensor_reading(
            sensor_id=ids["sensor_id"], timestamp=FROZEN_TS - 60, soil_moisture=SPIKE, temperature=22.0
        )
        repo.add_sensor_reading(sensor_id=second, timestamp=FROZEN_TS - 30 * 3600, soil_moisture=50.0)
        repo.add_sensor_reading(sensor_id=second, timestamp=FROZEN_TS - 120, soil_moisture=IMPOSSIBLE)
    p.ids = {**ids, "second_sensor_id": second}
    p.mark()
    yield p
    p.close()


def _soil_points(chart: dict) -> list[float]:
    return [value for ds in chart["datasets"] for _ts, value in ds["points"]]


def test_raw_views_keep_the_spike_and_cleaned_views_ignore_it(spiky):
    p = spiky
    chart = p.call("GET", "/api/v1/clusters/1/chart-data?hours=24&metric=soil_moisture")["response"]
    plant_chart = p.call("GET", f"/api/v1/plants/{p.ids['plant_id']}/chart-data?hours=24")["response"]
    history = p.call("GET", "/api/v1/clusters/1/history?hours=48&limit=50")["response"]
    quality = p.call("GET", "/api/v1/quality/report")["response"]
    status = p.call("GET", "/api/v1/clusters/1/status")["response"]
    dry_run = p.call("POST", "/api/v1/clusters/1/irrigate", {"dry_run": True})["response"]
    monitor = p.call("GET", "/api/v1/clusters/1/monitor")["response"]

    # Raw (archive / display) side.
    assert SPIKE in _soil_points(chart) and IMPOSSIBLE in _soil_points(chart)
    assert SPIKE in _soil_points(plant_chart)
    history_soil = {s["sensor_id"]: [r["soil_moisture"] for r in s["readings"]] for s in history["sensors"]}
    assert history_soil[p.ids["sensor_id"]][0] == SPIKE  # newest first
    assert history_soil[p.ids["second_sensor_id"]][0] == IMPOSSIBLE
    stale = [i for i in quality["issues"] if i["code"] == "stale_sensor"]
    assert stale == []  # the impossible reading still counts as "fresh"
    status_latest = {s["id"]: s["last_reading"]["soil_moisture"] for s in status["sensors"]}
    # Raw latest rows are what the status page displays.
    assert status_latest == {p.ids["sensor_id"]: SPIKE, p.ids["second_sensor_id"]: IMPOSSIBLE}

    # Cleaned (judgement) side.
    snapshot_min = 44.0
    # 44 % is just below the Monstera band → "moderately dry", not the "very dry" a 5 % reading would give.
    assert (status["decision"]["action"], status["decision"]["reason"]) == (
        "irrigate",
        "soil moderately dry (driest=44%)",
    )
    assert [r["code"] for r in status["decision"]["reasons"]] == ["sensor_dry"]
    assert (dry_run["action"], dry_run["duration_minutes"]) == ("irrigate", 2)
    assert dry_run["reasons"][0]["message"] == status["decision"]["reasons"][0]["message"]
    assert all(str(int(SPIKE)) + "%" not in r["message"] for r in dry_run["reasons"])
    decision_log = p.db_rows()["decision_logs"][0]["payload_json"]
    assert decision_log["sensor_snapshot"]["min_soil_moisture"] == snapshot_min
    monitor_soil = {s["sensor_id"]: (s["soil_moisture"], s["status"]) for s in monitor["sensors"]}
    # /monitor cleans too, but over a 2 h window: only 4 samples at this 30-min cadence, below
    # CLEANING_HAMPEL_MIN_READINGS, so the spike survives (see the dedicated test below).
    assert monitor_soil[p.ids["sensor_id"]] == (SPIKE, "very_dry")
    # The range gate still applies regardless of window size.
    assert monitor_soil[p.ids["second_sensor_id"]] == (None, "no_data")

    assert_golden_json(
        "orchestration/raw_vs_clean.json",
        {
            "chart": chart,
            "plant_chart": plant_chart,
            "history": history,
            "quality": quality,
            "status_decision": status["decision"],
            "status_sensors": status["sensors"],
            "dry_run": dry_run,
            "monitor": monitor,
            "decision_log_snapshot": decision_log["sensor_snapshot"],
        },
    )


def test_anomaly_scan_sees_what_cleaning_masks(spiky):
    """The ``sensor_drift`` scan reads raw rows, so it flags the very spike the snapshot ignored."""
    from greenhouse_server import scheduler

    rows_before = spiky.db_rows()["alerts"]
    assert rows_before == []
    scheduler._anomaly_job()
    alerts = spiky.db_rows()["alerts"]
    assert [(a["code"], a["entity_id"]) for a in alerts] == [("sensor_drift", spiky.ids["sensor_id"])]


def test_monitor_current_behavior_short_window_defeats_spike_filter(spiky):
    """Pins current behavior: ``/monitor`` cleans only its 2 h slice, too short to judge spikes.

    ``monitor_cluster`` feeds ``clean_readings_desc`` the last 2 h of readings.
    With a 30-min cadence that is 4 samples (< ``CLEANING_HAMPEL_MIN_READINGS`` = 5),
    so the Hampel filter is skipped and the 5 % spike becomes "very_dry" and a
    ``needs_water`` entry — while the decision snapshot (24 h window) ignores it.
    Observation for REFACTOR_NOTES.md; not fixed.
    """
    monitor = spiky.call("GET", "/api/v1/clusters/1/monitor")["response"]
    assert monitor["needs_water"] == ["Sensor 1 (Monstera deliciosa): 5%"]

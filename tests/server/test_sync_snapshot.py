"""Tests for the cluster snapshot the irrigation pipeline actuates on.

``SyncService.ensure_fresh_and_read`` answers "what is this cluster like right
now?". It used to answer with whichever sensor happened to be first in the
list, so in a two-plant cluster the value attributed to the cycle was a coin
flip between the plants (issue #103: the number in the irrigation note read as
the other plant's). The snapshot is a cluster-level aggregate instead, and
soil moisture follows invariant #2 — the driest plant drives the call.
"""

import time

import pytest

from greenhouse_server.services.sync import SyncService


@pytest.fixture
def cluster_with_two_sensors(tmp_db):
    """Cluster with a dry sensor and a wet one; returns (cluster_id, dry_id, wet_id)."""
    cluster_id = tmp_db.add_cluster("Two Sensor Cluster")
    dry_id = tmp_db.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="fake_sensor_dry",
        name="Dry Plant Sensor",
        sensor_type="soil_moisture",
        config={},
    )
    wet_id = tmp_db.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="fake_sensor_wet",
        name="Wet Plant Sensor",
        sensor_type="soil_moisture",
        config={},
    )
    return cluster_id, dry_id, wet_id


def _service(repo):
    return SyncService(repo, registry=None, cloud=None)


class TestClusterSnapshot:
    def test_soil_moisture_is_the_driest_sensor(self, tmp_db, cluster_with_two_sensors):
        """Invariant #2 — not "whichever sensor came back first from the DB"."""
        cluster_id, dry_id, wet_id = cluster_with_two_sensors
        now = int(time.time())
        tmp_db.add_sensor_reading(sensor_id=dry_id, timestamp=now - 60, soil_moisture=24.0, temperature=22.0)
        tmp_db.add_sensor_reading(sensor_id=wet_id, timestamp=now - 60, soil_moisture=69.0, temperature=24.0)
        tmp_db.session.commit()

        snapshot = _service(tmp_db).ensure_fresh_and_read(cluster_id)

        assert snapshot["soil_moisture"] == pytest.approx(24.0)

    def test_temperature_is_averaged_across_sensors(self, tmp_db, cluster_with_two_sensors):
        cluster_id, dry_id, wet_id = cluster_with_two_sensors
        now = int(time.time())
        tmp_db.add_sensor_reading(sensor_id=dry_id, timestamp=now - 60, soil_moisture=30.0, temperature=20.0)
        tmp_db.add_sensor_reading(sensor_id=wet_id, timestamp=now - 60, soil_moisture=60.0, temperature=24.0)
        tmp_db.session.commit()

        snapshot = _service(tmp_db).ensure_fresh_and_read(cluster_id)

        assert snapshot["temperature"] == pytest.approx(22.0)

    def test_snapshot_uses_the_latest_value_per_sensor(self, tmp_db, cluster_with_two_sensors):
        cluster_id, dry_id, wet_id = cluster_with_two_sensors
        now = int(time.time())
        tmp_db.add_sensor_reading(sensor_id=dry_id, timestamp=now - 7200, soil_moisture=12.0)
        tmp_db.add_sensor_reading(sensor_id=dry_id, timestamp=now - 60, soil_moisture=35.0)
        tmp_db.add_sensor_reading(sensor_id=wet_id, timestamp=now - 60, soil_moisture=55.0)
        tmp_db.session.commit()

        snapshot = _service(tmp_db).ensure_fresh_and_read(cluster_id)

        assert snapshot["soil_moisture"] == pytest.approx(35.0)

    def test_spike_reading_does_not_become_the_snapshot(self, tmp_db, cluster_with_two_sensors):
        """The snapshot feeds actuation, so it reads the cleaned view (invariant #10)."""
        cluster_id, dry_id, wet_id = cluster_with_two_sensors
        now = int(time.time())
        for i, moisture in enumerate([2.0, 55.0, 54.0, 56.0, 55.0, 54.0, 55.0]):
            tmp_db.add_sensor_reading(sensor_id=dry_id, timestamp=now - i * 3600, soil_moisture=moisture)
        tmp_db.add_sensor_reading(sensor_id=wet_id, timestamp=now - 60, soil_moisture=60.0)
        tmp_db.session.commit()

        snapshot = _service(tmp_db).ensure_fresh_and_read(cluster_id)

        assert snapshot["soil_moisture"] >= 50.0

    def test_sensor_without_readings_is_skipped(self, tmp_db, cluster_with_two_sensors):
        cluster_id, dry_id, _wet_id = cluster_with_two_sensors
        tmp_db.add_sensor_reading(sensor_id=dry_id, timestamp=int(time.time()) - 60, soil_moisture=42.0)
        tmp_db.session.commit()

        snapshot = _service(tmp_db).ensure_fresh_and_read(cluster_id)

        assert snapshot["soil_moisture"] == pytest.approx(42.0)

    def test_no_readings_returns_none(self, tmp_db, cluster_with_two_sensors):
        cluster_id, _dry_id, _wet_id = cluster_with_two_sensors

        assert _service(tmp_db).ensure_fresh_and_read(cluster_id) is None

    def test_no_sensors_returns_none(self, tmp_db):
        cluster_id = tmp_db.add_cluster("Empty Cluster")

        assert _service(tmp_db).ensure_fresh_and_read(cluster_id) is None

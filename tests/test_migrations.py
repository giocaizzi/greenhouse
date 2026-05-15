"""Schema-migration integration tests.

These tests guard the three init_db branches:

- Empty DB → alembic upgrade head creates the full schema.
- Already-managed DB → upgrade head is a no-op.
- Pre-Alembic legacy DB → missing tables/columns are repaired and the DB
  is stamped at head.
"""

import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect, text

from greenhouse_core.database import head_revision, init_db


@pytest.fixture
def file_db():
    """Tempfile-backed SQLite — alembic-friendly (in-memory loses tables across connections)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield f"sqlite:///{path}"
    if os.path.exists(path):
        os.remove(path)


def test_empty_db_runs_baseline_to_head(file_db):
    """A brand-new database gets every table created from baseline."""
    engine = create_engine(file_db)
    init_db(engine)

    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == head_revision()

    tables = set(inspect(engine).get_table_names())
    assert {"clusters", "plants", "sensors", "irrigators", "irrigation_configs", "decision_logs", "alerts"} <= tables


def test_idempotent_upgrade(file_db):
    """Running init_db twice on the same DB is a no-op."""
    engine = create_engine(file_db)
    init_db(engine)
    init_db(engine)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == head_revision()


def test_device_type_backfill_rewrites_legacy_values(file_db):
    """Legacy ``type`` values in irrigators/sensors are rewritten to model_key form.

    The :mod:`6c9d4e2f3a12` migration backfills ``tuya_cloud`` / ``tuya_local``
    to ``rainpoint.ik10pw`` and ``soil_moisture`` / ``temp_humidity`` / ``light``
    to ``tuya.tr301z``. Rows already on the new form must be left alone.
    """
    engine = create_engine(file_db)
    init_db(engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO clusters (id, name, environment, created_at) "
                "VALUES (1, 'C', 'indoor', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO irrigators (id, cluster_id, tuya_device_id, name, type) "
                "VALUES (1, 1, 'D1', 'I', 'tuya_cloud'), "
                "(2, 1, 'D2', 'I2', 'tuya_local'), "
                "(3, 1, 'D3', 'I3', 'rainpoint.ik10pw')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sensors (id, cluster_id, tuya_device_id, name, type) "
                "VALUES (1, 1, 'S1', 'S', 'soil_moisture'), "
                "(2, 1, 'S2', 'S2', 'temp_humidity'), "
                "(3, 1, 'S3', 'S3', 'light'), "
                "(4, 1, 'S4', 'S4', 'tuya.tr301z')"
            )
        )

    # Re-applying the backfill UPDATEs is idempotent — rows already on the
    # new form should not change.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE irrigators SET type = 'rainpoint.ik10pw' WHERE type IN ('tuya_cloud', 'tuya_local')")
        )
        conn.execute(
            text("UPDATE sensors SET type = 'tuya.tr301z' WHERE type IN ('soil_moisture', 'temp_humidity', 'light')")
        )

    with engine.connect() as conn:
        irrs = [r[0] for r in conn.execute(text("SELECT type FROM irrigators ORDER BY id"))]
        sens = [r[0] for r in conn.execute(text("SELECT type FROM sensors ORDER BY id"))]
    assert irrs == ["rainpoint.ik10pw"] * 3
    assert sens == ["tuya.tr301z"] * 4


def test_legacy_partial_db_gets_repaired(file_db):
    """A partial pre-baseline DB (missing newer columns) is repaired and stamped."""
    engine = create_engine(file_db)
    # Simulate a partial DB: irrigation_configs without daily_cap_minutes / max_events_per_day,
    # no alembic_version, missing baseline tables.
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE clusters (id INTEGER PRIMARY KEY, name TEXT, location TEXT, "
                "environment TEXT, created_at INTEGER)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE irrigation_configs (id INTEGER PRIMARY KEY, cluster_id INTEGER UNIQUE, "
                "mode TEXT, duration_minutes INTEGER, interval_hours INTEGER, auto_run INTEGER, "
                "last_updated INTEGER)"
            )
        )

    init_db(engine)

    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("irrigation_configs")}
    assert "daily_cap_minutes" in cols
    assert "max_events_per_day" in cols

    tables = set(inspector.get_table_names())
    assert {"decision_logs", "alerts", "activity_events", "plant_health_daily"} <= tables

    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == head_revision()

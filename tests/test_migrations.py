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

from tuya_irrigation_core.database import head_revision, init_db


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

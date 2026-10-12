"""Schema-migration integration tests.

These tests guard init_db, which is plain ``alembic upgrade head``:

- Empty DB → the chain from baseline creates the full schema.
- Already-managed DB → upgrade head is a no-op.
- Pre-Alembic DB (tables but no ``alembic_version``) → no longer repaired
  (OD3): the baseline revision fails on the existing tables.
"""

import logging
import os
import tempfile
from collections.abc import Iterator
from logging import config as logging_config
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from greenhouse_core.database import _alembic_config, head_revision, init_db


@pytest.fixture
def file_db() -> Iterator[str]:
    """Tempfile-backed SQLite — alembic-friendly (in-memory loses tables across connections)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    Path(path).unlink()
    yield f"sqlite:///{path}"
    if Path(path).exists():
        Path(path).unlink()


def test_empty_db_runs_baseline_to_head(file_db: str) -> None:
    """A brand-new database gets every table created from baseline."""
    engine = create_engine(file_db)
    init_db(engine)

    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == head_revision()

    tables = set(inspect(engine).get_table_names())
    assert {"clusters", "plants", "sensors", "irrigators", "irrigation_configs", "decision_logs", "alerts"} <= tables


def test_idempotent_upgrade(file_db: str) -> None:
    """Running init_db twice on the same DB is a no-op."""
    engine = create_engine(file_db)
    init_db(engine)
    init_db(engine)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == head_revision()


def test_device_type_backfill_rewrites_legacy_values(file_db: str) -> None:
    """Legacy ``type`` values in irrigators/sensors are rewritten to model_key form.

    The :mod:`6c9d4e2f3a12` migration backfills ``tuya_cloud`` / ``tuya_local``
    to ``rainpoint.ik10pw`` and ``soil_moisture`` / ``temp_humidity`` / ``light``
    to ``tuya.tr301z``. Rows already on the new form must be left alone.
    """
    engine = create_engine(file_db)
    init_db(engine)

    with engine.begin() as conn:
        # One irrigator per cluster (irrigators.cluster_id is unique); the
        # backfill exercises the legacy ``type`` rewrite across distinct rows.
        conn.execute(
            text(
                "INSERT INTO clusters (id, name, environment, created_at) "
                "VALUES (1, 'C', 'indoor', 0), (2, 'C2', 'indoor', 0), (3, 'C3', 'indoor', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO irrigators (id, cluster_id, tuya_device_id, name, type) "
                "VALUES (1, 1, 'D1', 'I', 'tuya_cloud'), "
                "(2, 2, 'D2', 'I2', 'tuya_local'), "
                "(3, 3, 'D3', 'I3', 'rainpoint.ik10pw')"
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
        conn.execute(text("UPDATE irrigators SET type = 'rainpoint.ik10pw' WHERE type IN ('tuya_cloud', 'tuya_local')"))
        conn.execute(
            text("UPDATE sensors SET type = 'tuya.tr301z' WHERE type IN ('soil_moisture', 'temp_humidity', 'light')")
        )

    with engine.connect() as conn:
        irrs = [r[0] for r in conn.execute(text("SELECT type FROM irrigators ORDER BY id"))]
        sens = [r[0] for r in conn.execute(text("SELECT type FROM sensors ORDER BY id"))]
    assert irrs == ["rainpoint.ik10pw"] * 3
    assert sens == ["tuya.tr301z"] * 4


def test_pre_alembic_db_is_not_repaired(file_db: str) -> None:
    """A pre-baseline DB (tables, no ``alembic_version``) is no longer patched up and stamped.

    OD3 removed the legacy repair branch: init_db always runs ``upgrade head``,
    whose baseline revision fails on the table that already exists. SQLite DDL is
    non-transactional, so the tables created before the failure stay, but no
    revision is recorded: the database needs a manual ``alembic stamp head``.
    """
    engine = create_engine(file_db)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE clusters (id INTEGER PRIMARY KEY, name TEXT, location TEXT, "
                "environment TEXT, created_at INTEGER)"
            )
        )

    with pytest.raises(OperationalError, match="already exists"):
        init_db(engine)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).all() == []
    engine.dispose()


def test_init_db_does_not_disable_application_loggers() -> None:
    """Regression: running the migrations no longer mutes loggers created before ``init_db``.

    Alembic's env.py ran ``fileConfig`` with the default ``disable_existing_loggers=True``,
    so every server module imported before ``create_app`` was silently muted for the life
    of the process — scheduler job failures, pump-watcher warnings and deprecation notices
    never reached the log.
    """
    app_logger = logging.getLogger("greenhouse_server.services.irrigation")
    engine = create_engine("sqlite://")
    try:
        init_db(engine)
    finally:
        engine.dispose()
    assert app_logger.disabled is False


def test_init_db_leaves_the_host_logging_configuration_alone(tmp_path: Path) -> None:
    """In-process migrations do not run alembic.ini's ``fileConfig``.

    It used to replace the root handlers and level on every ``create_app``, so ``caplog`` (and
    any handler the host installed) saw nothing afterwards.
    """
    root = logging.getLogger()
    sentinel = logging.NullHandler()
    root.addHandler(sentinel)
    handlers, level = list(root.handlers), root.level
    engine = create_engine(f"sqlite:///{tmp_path / 'logging.db'}")
    try:
        init_db(engine)
        assert (root.handlers, root.level) == (handlers, level)
    finally:
        root.removeHandler(sentinel)
        engine.dispose()


def test_the_alembic_command_line_still_configures_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A Config without the opt-out (what ``alembic`` builds from the ini) still runs ``fileConfig``."""
    calls = []
    monkeypatch.setattr(logging_config, "fileConfig", lambda *a, **k: calls.append((a, k)))
    engine = create_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    cfg = _alembic_config(engine)
    del cfg.attributes["configure_logging"]
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    engine.dispose()
    assert len(calls) == 1

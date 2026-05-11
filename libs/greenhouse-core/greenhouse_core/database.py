"""Database engine, session, and Alembic-backed schema initialisation."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from greenhouse_core.models import Base

log = logging.getLogger(__name__)


def create_db_engine(db_url: str) -> Engine:
    """Create a SQLAlchemy engine from a database URL."""
    return create_engine(db_url)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to an engine."""
    return sessionmaker(bind=engine)


def init_db(engine: Engine) -> None:
    """Bring the database schema up to Alembic head.

    Branches:

    - **Empty database**: run ``alembic upgrade head`` from baseline.
    - **Pre-Alembic legacy** (tables but no ``alembic_version``): create any
      missing tables, repair any columns the ORM declared after the DB was
      first written, then stamp at head.
    - **Already managed**: run ``alembic upgrade head`` normally.

    The legacy repair is intentionally additive only (``ALTER TABLE … ADD
    COLUMN``); destructive changes go through reviewed Alembic revisions.
    """
    cfg = _alembic_config(engine)
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        ctx = MigrationContext.configure(conn)
        current = ctx.get_current_revision()
        real_tables = set(inspect(conn).get_table_names()) - {"alembic_version"}

        if current is None and real_tables:
            log.info("legacy database detected — creating missing tables, repairing columns, stamping head")
            Base.metadata.create_all(bind=conn)
            _add_missing_columns(conn)
            command.stamp(cfg, "head")
            conn.commit()
            return

        command.upgrade(cfg, "head")
        conn.commit()


def _alembic_config(engine: Engine) -> Config:
    """Build an in-memory Alembic Config bound to the given engine URL."""
    here = Path(__file__).resolve().parent
    cfg_path = here / "alembic.ini"
    cfg = Config(str(cfg_path))
    cfg.set_main_option("script_location", str(here / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


def _add_missing_columns(conn) -> None:
    """ALTER TABLE ADD COLUMN for any ORM column missing from a live table.

    Used only by the legacy-recovery branch of :func:`init_db`. After this
    runs once and the DB is stamped at head, future schema changes go
    through Alembic revisions and this function never fires again.
    """
    inspector = inspect(conn)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        live_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in live_cols:
                continue
            ddl_type = column.type.compile(dialect=conn.dialect)
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl_type}"
            conn.execute(text(ddl))
            log.info("legacy repair: %s ADD COLUMN %s %s", table.name, column.name, ddl_type)


def head_revision() -> str | None:
    """Return the current head revision id (used by tests / diagnostics)."""
    here = Path(__file__).resolve().parent
    script = ScriptDirectory(str(here / "migrations"))
    return script.get_current_head()

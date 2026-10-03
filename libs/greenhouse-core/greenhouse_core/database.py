"""Database engine, session, and Alembic-backed schema initialisation."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(db_url: str) -> Engine:
    """Create a SQLAlchemy engine from a database URL."""
    return create_engine(db_url)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to an engine."""
    return sessionmaker(bind=engine)


def init_db(engine: Engine) -> None:
    """Bring the database schema up to Alembic head (``alembic upgrade head``).

    An empty database runs the whole chain from baseline; an Alembic-managed one
    applies its pending revisions (a no-op at head). A database created before
    Alembic (tables but no ``alembic_version``) is no longer repaired here: check
    its schema against head and ``alembic stamp head`` it by hand first.
    """
    cfg = _alembic_config(engine)
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
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


def head_revision() -> str | None:
    """Return the current head revision id (used by tests / diagnostics)."""
    here = Path(__file__).resolve().parent
    script = ScriptDirectory(str(here / "migrations"))
    return script.get_current_head()

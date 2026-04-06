"""Database engine and session management."""

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tuya_irrigation_core.models import Base

# TODO: fix this should be generic from a provided path or a default
_DEFAULT_DATA_DIR = Path.home() / ".openclaw/workspace/skills/tuya-irrigation/data"
DATA_DIR = Path(os.environ["IRRIGATION_DATA_DIR"]) if os.environ.get("IRRIGATION_DATA_DIR") else _DEFAULT_DATA_DIR
DB_PATH = Path(os.environ["IRRIGATION_DB_PATH"]) if os.environ.get("IRRIGATION_DB_PATH") else DATA_DIR / "irrigation.db"


def create_db_engine(db_url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine.

    If no URL is provided, uses the default SQLite path from environment/defaults.
    """
    if db_url is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{DB_PATH}"
    return create_engine(db_url)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to an engine."""
    return sessionmaker(bind=engine)


def init_db(engine: Engine) -> None:
    """Create all tables from ORM models."""
    Base.metadata.create_all(bind=engine)

"""Database engine and session management."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tuya_irrigation_core.models import Base


def create_db_engine(db_url: str) -> Engine:
    """Create a SQLAlchemy engine from a database URL."""
    return create_engine(db_url)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to an engine."""
    return sessionmaker(bind=engine)


def init_db(engine: Engine) -> None:
    """Create all tables from ORM models."""
    Base.metadata.create_all(bind=engine)

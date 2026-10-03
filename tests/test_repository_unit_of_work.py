"""IrrigationRepository.commit / rollback / flush delegate to the bound session (audit 46 C-TX-1)."""

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from greenhouse_core.models import Base, Cluster
from greenhouse_core.repository import IrrigationRepository


def _count_in_new_session(engine) -> int:
    with Session(engine) as other:
        return other.scalar(select(func.count()).select_from(Cluster))


def test_commit_makes_writes_visible_to_other_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'uow.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repo = IrrigationRepository(session)
        repo.add_cluster("Committed")
        assert _count_in_new_session(engine) == 0
        repo.commit()
    assert _count_in_new_session(engine) == 1
    engine.dispose()


def test_rollback_discards_uncommitted_writes(tmp_db):
    tmp_db.add_cluster("Discarded")
    tmp_db.rollback()
    assert tmp_db.list_clusters() == []


def test_flush_assigns_ids_without_committing(tmp_db):
    cluster = Cluster(name="Flushed", created_at=0, environment="indoor")
    tmp_db.session.add(cluster)
    assert cluster.id is None
    tmp_db.flush()
    assert cluster.id is not None
    tmp_db.rollback()
    assert tmp_db.list_clusters() == []

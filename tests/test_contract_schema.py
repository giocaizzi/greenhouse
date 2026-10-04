"""Characterization: ORM DDL, the Alembic head, and migrated-vs-ORM schema.

- ``contracts/ddl.sql`` — ``CREATE TABLE`` / ``CREATE INDEX`` for every table of
  ``Base.metadata`` compiled for SQLite, in ``metadata.sorted_tables`` order with each
  table's indexes sorted by name (the exact recipe behind the ``PHASE0_DDL_SHA256`` fingerprint);
- ``contracts/orm_columns.json`` — per column what DDL does not show: Python-side
  ``default`` / ``onupdate``, ``index`` / ``unique`` flags, foreign keys; plus the
  mapped class → table map;
- the literal Alembic head ``a1d3f5b7c902`` and the full revision chain;
- ``contracts/migrated_schema.json`` — SQLAlchemy reflection of a temp-file SQLite
  after ``init_db`` (the schema production actually runs), and
  ``contracts/schema_drift.json`` — every difference between that and
  ``Base.metadata.create_all`` on another temp file. Drift is recorded as observed
  behavior, not fixed.
"""

from __future__ import annotations

import hashlib

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from golden import assert_golden, assert_golden_json
from greenhouse_core import database
from greenhouse_core.database import head_revision, init_db
from greenhouse_core.models import Base

# sha256 of "\n".join(DDL statements), recorded at the original baseline (main @ a1b2622, hence "PHASE0").
PHASE0_DDL_SHA256 = (
    "7b6a2d72054011a900438ae7e7edd471acbffc6c6a2f2d60cc1583973fb36f2b"  # gitleaks:allow — content hash, not a secret
)

ALEMBIC_HEAD = "a1d3f5b7c902"
ALEMBIC_CHAIN = [
    ("1c9b09f02432", None),
    ("2a4d1e7c8f02", "1c9b09f02432"),
    ("3f1a8b5c9d10", "2a4d1e7c8f02"),
    ("4a7e2b9c8d10", "3f1a8b5c9d10"),
    ("5b8f3c2d4e11", "4a7e2b9c8d10"),
    ("6c9d4e2f3a12", "5b8f3c2d4e11"),
    ("7d3c1f9b4a08", "6c9d4e2f3a12"),
    ("7d0e5f3a4b13", "7d3c1f9b4a08"),
    ("8e1a4d6c5f20", "7d0e5f3a4b13"),
    ("9f2b5e7c6a31", "8e1a4d6c5f20"),
    ("a1d3f5b7c902", "9f2b5e7c6a31"),
]


def _ddl_statements() -> list[str]:
    dialect = sqlite.dialect()
    statements = []
    for table in Base.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)))
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)))
    return statements


def test_orm_ddl_matches_golden():
    # Trailing spaces SQLAlchemy emits after each column comma are stripped so the
    # repo's trailing-whitespace / end-of-file pre-commit hooks leave the golden
    # alone; the exact bytes are pinned by the fingerprint test below.
    text = "\n".join(_ddl_statements())
    lines = [line.rstrip() for line in text.splitlines()]
    assert_golden("contracts/ddl.sql", "\n".join(lines).strip("\n") + "\n")


def test_orm_ddl_matches_phase0_fingerprint():
    assert hashlib.sha256("\n".join(_ddl_statements()).encode()).hexdigest() == PHASE0_DDL_SHA256


def _default_repr(default) -> str | None:
    if default is None:
        return None
    if default.is_callable:
        fn = default.arg
        return f"callable:{getattr(fn, '__qualname__', repr(fn))}"
    if default.is_scalar:
        return f"scalar:{default.arg!r}"
    return f"{type(default).__name__}:{default.arg!r}"


def test_orm_column_metadata_matches_golden():
    tables = {}
    for table in Base.metadata.sorted_tables:
        tables[table.name] = {
            "columns": [
                {
                    "name": c.name,
                    "type": str(c.type.compile(dialect=sqlite.dialect())),
                    "primary_key": c.primary_key,
                    "nullable": c.nullable,
                    "unique": c.unique,
                    "index": c.index,
                    "default": _default_repr(c.default),
                    "onupdate": _default_repr(c.onupdate),
                    "server_default": str(c.server_default.arg) if c.server_default is not None else None,
                    "foreign_keys": sorted(fk.target_fullname for fk in c.foreign_keys),
                    "autoincrement": str(c.autoincrement),
                }
                for c in table.columns
            ],
            "constraints": sorted(
                [type(k).__name__, k.name, [c.name for c in k.columns]]
                for k in table.constraints
                if type(k).__name__ != "PrimaryKeyConstraint"
            ),
            "indexes": sorted([i.name, [c.name for c in i.columns], i.unique] for i in table.indexes),
        }
    mapped = sorted(f"{m.class_.__name__}={m.local_table.name}" for m in Base.registry.mappers)
    assert_golden_json("contracts/orm_columns.json", {"tables": tables, "mapped_classes": mapped})
    assert len(tables) == 17


def test_alembic_head_is_literal():
    assert head_revision() == ALEMBIC_HEAD


def test_alembic_revision_chain():
    script = ScriptDirectory(
        str(database._alembic_config(create_engine("sqlite://")).get_main_option("script_location"))
    )
    chain = [(rev.revision, rev.down_revision) for rev in script.walk_revisions(base="base", head="heads")]
    assert list(reversed(chain)) == ALEMBIC_CHAIN
    assert script.get_heads() == [ALEMBIC_HEAD]


def _reflect(engine) -> dict:
    insp = inspect(engine)
    out = {}
    for name in sorted(insp.get_table_names()):
        out[name] = {
            "columns": [
                {
                    "name": c["name"],
                    "type": str(c["type"]),
                    "nullable": c["nullable"],
                    "default": c["default"],
                    "primary_key": c["primary_key"],
                }
                for c in insp.get_columns(name)
            ],
            "primary_key": insp.get_pk_constraint(name),
            "foreign_keys": insp.get_foreign_keys(name),
            "indexes": sorted(insp.get_indexes(name), key=lambda i: i["name"] or ""),
            "unique_constraints": sorted(
                insp.get_unique_constraints(name), key=lambda u: (u["name"] or "", u["column_names"])
            ),
        }
    return out


@pytest.fixture
def migrated_and_created(tmp_path):
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    created = create_engine(f"sqlite:///{tmp_path / 'created.db'}")
    init_db(migrated)
    Base.metadata.create_all(created)
    yield _reflect(migrated), _reflect(created)
    migrated.dispose()
    created.dispose()


def _drift(migrated: dict, created: dict) -> dict:
    drift: dict = {
        "tables_only_in_migrated": sorted(set(migrated) - set(created)),
        "tables_only_in_create_all": sorted(set(created) - set(migrated)),
        "tables": {},
    }
    for name in sorted(set(migrated) & set(created)):
        per_table = {}
        for aspect in migrated[name]:
            if aspect == "columns":
                mig_cols = {c["name"]: c for c in migrated[name]["columns"]}
                orm_cols = {c["name"]: c for c in created[name]["columns"]}
                cols = {}
                for col in sorted(set(mig_cols) | set(orm_cols)):
                    if mig_cols.get(col) != orm_cols.get(col):
                        cols[col] = {"migrated": mig_cols.get(col), "create_all": orm_cols.get(col)}
                if [c["name"] for c in migrated[name]["columns"]] != [c["name"] for c in created[name]["columns"]]:
                    cols["<column order>"] = {
                        "migrated": [c["name"] for c in migrated[name]["columns"]],
                        "create_all": [c["name"] for c in created[name]["columns"]],
                    }
                if cols:
                    per_table["columns"] = cols
            elif migrated[name][aspect] != created[name][aspect]:
                per_table[aspect] = {"migrated": migrated[name][aspect], "create_all": created[name][aspect]}
        if per_table:
            drift["tables"][name] = per_table
    return drift


def test_migrated_schema_matches_golden(migrated_and_created):
    migrated, _ = migrated_and_created
    assert_golden_json("contracts/migrated_schema.json", migrated)


def test_schema_drift_current_behavior_migrations_differ_from_create_all(migrated_and_created):
    """Pins current drift: the Alembic chain and ``Base.metadata.create_all`` produce
    different SQLite schemas — migrations add server defaults (``irrigation_windows.
    weekday_mask``, ``user_preferences.scheduler_paused``/``notify_*``, ``users.is_active``)
    and *named* unique constraints (``uq_irrigators_cluster_id``, ``uq_users_username``)
    that the ORM does not declare; plus the ``alembic_version`` table. Fresh test DBs
    (``tmp_db`` fixture → ``create_all``) therefore differ from production — see
    REFACTOR_NOTES.md.
    """
    migrated, created = migrated_and_created
    drift = _drift(migrated, created)
    assert_golden_json("contracts/schema_drift.json", drift)
    assert drift["tables_only_in_migrated"] == ["alembic_version"]
    assert sorted(drift["tables"]) == ["irrigation_windows", "irrigators", "user_preferences", "users"]

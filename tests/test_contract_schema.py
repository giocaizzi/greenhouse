"""Characterization: ORM DDL, the Alembic head, and migrated-vs-ORM schema.

- ``contracts/ddl.sql`` — ``CREATE TABLE`` / ``CREATE INDEX`` for every table of
  ``Base.metadata`` compiled for SQLite, in ``metadata.sorted_tables`` order with each
  table's indexes sorted by name (the exact recipe behind the ``PHASE0_DDL_SHA256`` fingerprint);
- ``contracts/orm_columns.json`` — per column what DDL does not show: Python-side
  ``default`` / ``onupdate``, ``index`` / ``unique`` flags, foreign keys; plus the
  mapped class → table map;
- the literal Alembic head ``d5e7a9b1c326`` and the full revision chain;
- ``contracts/migrated_schema.json`` — SQLAlchemy reflection of a temp-file SQLite
  after ``init_db`` (the schema production actually runs), which must equal
  ``Base.metadata.create_all`` on another temp file (no drift; only ``alembic_version``
  is extra).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import ColumnDefault, DefaultClause, Table, create_engine, inspect
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import ColumnCollectionConstraint, CreateIndex, CreateTable

from golden import assert_golden, assert_golden_json
from greenhouse_core.database import _alembic_config, head_revision, init_db
from greenhouse_core.models import Base

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy import Constraint, Engine, FetchedValue, FromClause
    from sqlalchemy.engine import Inspector
    from sqlalchemy.engine.interfaces import ReflectedIndex
    from sqlalchemy.schema import DefaultGenerator

TABLE_COUNT = 17

# sha256 of "\n".join(DDL statements), re-recorded for sensors/plants.archived_at (#149).
PHASE0_DDL_SHA256 = (
    "8108b8a68e963a1b83c1c10e1f1b84a8df62a07aa2be0aa545e25aaec4ca9ab9"  # gitleaks:allow — content hash, not a secret
)

ALEMBIC_HEAD = "d5e7a9b1c326"
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
    ("b2e4a6c8d013", "a1d3f5b7c902"),
    ("b3c5e7f9a104", "b2e4a6c8d013"),
    ("c4d6f8a0b215", "b3c5e7f9a104"),
    ("d5e7a9b1c326", "c4d6f8a0b215"),
]


def _ddl_statements() -> list[str]:
    dialect = sqlite.dialect()
    statements = []
    for table in Base.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)))
        statements.extend(
            str(CreateIndex(index).compile(dialect=dialect))
            for index in sorted(table.indexes, key=lambda i: i.name or "")
        )
    return statements


def test_orm_ddl_matches_golden() -> None:
    """The ORM's DDL matches the pinned golden (trailing spaces stripped)."""
    # Trailing spaces SQLAlchemy emits after each column comma are stripped so the
    # repo's trailing-whitespace / end-of-file pre-commit hooks leave the golden
    # alone; the exact bytes are pinned by the fingerprint test below.
    text = "\n".join(_ddl_statements())
    lines = [line.rstrip() for line in text.splitlines()]
    assert_golden("contracts/ddl.sql", "\n".join(lines).strip("\n") + "\n")


def test_orm_ddl_matches_phase0_fingerprint() -> None:
    """The exact DDL bytes match the phase-0 fingerprint."""
    assert hashlib.sha256("\n".join(_ddl_statements()).encode()).hexdigest() == PHASE0_DDL_SHA256


def _default_repr(default: DefaultGenerator | None) -> str | None:
    if default is None:
        return None
    assert isinstance(default, ColumnDefault)  # every model default carries an ``arg``
    if default.is_callable:
        fn = default.arg
        return f"callable:{getattr(fn, '__qualname__', repr(fn))}"
    if default.is_scalar:
        return f"scalar:{default.arg!r}"
    return f"{type(default).__name__}:{default.arg!r}"


def _server_default_repr(server_default: FetchedValue | None) -> str | None:
    if server_default is None:
        return None
    assert isinstance(server_default, DefaultClause)  # every model server default is a literal clause
    return str(server_default.arg)


def _constraint_columns(constraint: Constraint) -> list[str]:
    assert isinstance(constraint, ColumnCollectionConstraint)  # the models declare column constraints only
    return [c.name for c in constraint.columns]


def _table_name(selectable: FromClause) -> str:
    assert isinstance(selectable, Table)  # every mapper maps a plain table
    return selectable.name


def test_orm_column_metadata_matches_golden() -> None:
    """Column types, nullability and defaults match the golden."""
    tables: dict[str, dict[str, Any]] = {}
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
                    "server_default": _server_default_repr(c.server_default),
                    "foreign_keys": sorted(fk.target_fullname for fk in c.foreign_keys),
                    "autoincrement": str(c.autoincrement),
                }
                for c in table.columns
            ],
            "constraints": sorted(
                (
                    [type(k).__name__, k.name, _constraint_columns(k)]
                    for k in table.constraints
                    if type(k).__name__ != "PrimaryKeyConstraint"
                ),
                # a table can mix named and unnamed constraints of one kind (None sorts first)
                key=lambda row: (row[0], row[1] is not None, row[1] or "", row[2]),
            ),
            "indexes": sorted([i.name, [c.name for c in i.columns], i.unique] for i in table.indexes),
        }
    mapped = sorted(f"{m.class_.__name__}={_table_name(m.local_table)}" for m in Base.registry.mappers)
    assert_golden_json("contracts/orm_columns.json", {"tables": tables, "mapped_classes": mapped})
    assert len(tables) == TABLE_COUNT


def test_alembic_head_is_literal() -> None:
    """The Alembic head is the pinned revision."""
    assert head_revision() == ALEMBIC_HEAD


def test_alembic_revision_chain() -> None:
    """The revision chain is linear and matches the pinned order."""
    script = ScriptDirectory(str(_alembic_config(create_engine("sqlite://")).get_main_option("script_location")))
    chain = [(rev.revision, rev.down_revision) for rev in script.walk_revisions(base="base", head="heads")]
    assert list(reversed(chain)) == ALEMBIC_CHAIN
    assert script.get_heads() == [ALEMBIC_HEAD]


def _sqlite_columns(insp: Inspector, table: str) -> list[dict[str, Any]]:
    # SQLite also reports ``primary_key`` per column, a key ReflectedColumn does not declare.
    return [dict(c) for c in insp.get_columns(table)]


def _plain_index(index: ReflectedIndex) -> dict[str, Any]:
    """The reflected index with a partial index's ``WHERE`` clause as text (a TextClause compares by identity)."""
    options = {key: str(value) for key, value in index.get("dialect_options", {}).items()}
    return {**index, "dialect_options": options}


def _reflect(engine: Engine) -> dict[str, Any]:
    insp = inspect(engine)
    out: dict[str, Any] = {}
    for name in sorted(insp.get_table_names()):
        out[name] = {
            "columns": [
                {
                    "name": c["name"],
                    # SQLAlchemy 2.1 renders ``Float`` as DOUBLE in new DDL; the migrations
                    # (frozen) still create FLOAT. SQLite gives both REAL affinity.
                    "type": str(c["type"]).replace("DOUBLE", "FLOAT"),
                    "nullable": c["nullable"],
                    "default": c["default"],
                    "primary_key": c["primary_key"],
                }
                for c in _sqlite_columns(insp, name)
            ],
            "primary_key": insp.get_pk_constraint(name),
            "foreign_keys": insp.get_foreign_keys(name),
            "indexes": sorted((_plain_index(i) for i in insp.get_indexes(name)), key=lambda i: i["name"] or ""),
            "unique_constraints": sorted(
                insp.get_unique_constraints(name), key=lambda u: (u["name"] or "", u["column_names"])
            ),
        }
    return out


@pytest.fixture
def migrated_and_created(tmp_path: Path) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Provide one database built by migrations and one built from the ORM metadata."""
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    created = create_engine(f"sqlite:///{tmp_path / 'created.db'}")
    init_db(migrated)
    Base.metadata.create_all(created)
    yield _reflect(migrated), _reflect(created)
    migrated.dispose()
    created.dispose()


def _drift(migrated: dict[str, Any], created: dict[str, Any]) -> dict[str, Any]:
    drift: dict[str, Any] = {
        "tables_only_in_migrated": sorted(set(migrated) - set(created)),
        "tables_only_in_create_all": sorted(set(created) - set(migrated)),
        "tables": {},
    }
    for name in sorted(set(migrated) & set(created)):
        per_table = _table_drift(migrated[name], created[name])
        if per_table:
            drift["tables"][name] = per_table
    return drift


def _table_drift(migrated_table: dict[str, Any], created_table: dict[str, Any]) -> dict[str, Any]:
    per_table: dict[str, Any] = {}
    for aspect, migrated_value in migrated_table.items():
        if aspect == "columns":
            cols = _column_drift(migrated_value, created_table["columns"])
            if cols:
                per_table["columns"] = cols
        elif migrated_value != created_table[aspect]:
            per_table[aspect] = {"migrated": migrated_value, "create_all": created_table[aspect]}
    return per_table


def _column_drift(migrated_columns: list[dict[str, Any]], created_columns: list[dict[str, Any]]) -> dict[str, Any]:
    mig_cols = {c["name"]: c for c in migrated_columns}
    orm_cols = {c["name"]: c for c in created_columns}
    cols: dict[str, Any] = {}
    for col in sorted(set(mig_cols) | set(orm_cols)):
        if mig_cols.get(col) != orm_cols.get(col):
            cols[col] = {"migrated": mig_cols.get(col), "create_all": orm_cols.get(col)}
    if [c["name"] for c in migrated_columns] != [c["name"] for c in created_columns]:
        cols["<column order>"] = {
            "migrated": [c["name"] for c in migrated_columns],
            "create_all": [c["name"] for c in created_columns],
        }
    return cols


def test_migrated_schema_matches_golden(migrated_and_created: tuple[dict[str, Any], dict[str, Any]]) -> None:
    """The migrated schema matches the golden."""
    migrated, _ = migrated_and_created
    assert_golden_json("contracts/migrated_schema.json", migrated)


def test_migrations_and_create_all_produce_the_same_schema(
    migrated_and_created: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """The Alembic chain and ``Base.metadata.create_all`` produce the same SQLite schema.

    The models declare the server defaults (``irrigation_windows.weekday_mask``,
    ``user_preferences.scheduler_paused`` / ``notify_*``, ``users.is_active``) and the *named*
    unique constraints (``uq_irrigators_cluster_id``, ``uq_users_username``) the migrations
    create, so fresh test DBs (``tmp_db`` → ``create_all``) match production. Only the
    ``alembic_version`` bookkeeping table differs.
    """
    migrated, created = migrated_and_created
    drift = _drift(migrated, created)
    assert drift == {"tables_only_in_migrated": ["alembic_version"], "tables_only_in_create_all": [], "tables": {}}

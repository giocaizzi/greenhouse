"""The repository's session holder: unit of work and the private helpers every entity mixin shares."""

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from greenhouse_core.repository._types import (
    DeviceIdExistsError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from sqlalchemy import Select
    from sqlalchemy.orm import InstrumentedAttribute

    from greenhouse_core.models import Base


class RepositoryBase:
    """Hold the session; transaction control and shared query/patch helpers."""

    def __init__(self, session: Session) -> None:
        """Wrap ``session``; the caller owns its lifetime."""
        self.session = session

    # ── Unit of work ──────────────────────────────────────────────────────────
    # The one spelling for transaction control outside this module: handlers commit CRUD,
    # a service commits only when a side effect must follow a durable write.

    def commit(self) -> None:
        """Commit the current transaction on the repository's session."""
        self.session.commit()

    def rollback(self) -> None:
        """Roll back the current transaction on the repository's session."""
        self.session.rollback()

    @contextmanager
    def refusing_duplicate_device_id(self, tuya_device_id: str) -> "Iterator[None]":
        """Turn a unique-constraint refusal inside the block into ``DeviceIdExistsError``.

        The session is rolled back before the domain error is raised, so callers outside
        the persistence layer never handle SQLAlchemy exceptions. ``add_irrigator`` /
        ``add_sensor`` themselves still raise the raw ``IntegrityError``.
        """
        try:
            yield
        except IntegrityError:
            self.rollback()
            raise DeviceIdExistsError(tuya_device_id) from None

    def flush(self) -> None:
        """Flush pending changes to the database without committing."""
        self.session.flush()

    # ── Generic helpers used by health / quality services ─────────────────────

    @staticmethod
    def _page(
        stmt: "Select[Any]",
        id_column: "InstrumentedAttribute[int]",
        *,
        limit: int | None,
        after_id: int | None,
    ) -> "Select[Any]":
        """Id-cursor pagination, applied after the caller's own filters so the WHERE order is unchanged."""
        if after_id is not None:
            stmt = stmt.where(id_column > after_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return stmt

    # ── Mutations for CRUD edit/delete ────────────────────────────────────────

    def _patch_fields(
        self,
        row: "Base",
        fields: "Mapping[str, Any]",
        *,
        json_fields: frozenset[str] = frozenset(),
    ) -> None:
        """PATCH the supplied keys: ``None`` clears a nullable column, absent keys stay untouched.

        Callers pass only the keys the client sent (``model_dump(exclude_unset=True)``),
        so "absent" and an explicit ``null`` stay distinct. ``None`` for a NOT NULL
        column (a name, a flag) is skipped rather than violating the constraint;
        dict values of ``json_fields`` are JSON-encoded; keys that are not
        attributes of the row are ignored.
        """
        columns = inspect(type(row)).columns
        for key, value in fields.items():
            if value is None:
                if key in columns and columns[key].nullable:
                    setattr(row, key, None)
                continue
            if key in json_fields and isinstance(value, dict):
                setattr(row, key, json.dumps(value))
            elif hasattr(row, key):
                setattr(row, key, value)

    def _delete_by_id(self, model: "type[Base]", row_id: int) -> bool:
        """Delete one row by primary key; ``False`` when it does not exist (shared by the plain deletes)."""
        row = self.session.get(model, row_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

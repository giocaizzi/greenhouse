"""Irrigators and the layered irrigation config (cluster row over the global row)."""

import json
import time
from typing import TYPE_CHECKING, Any, Unpack

from sqlalchemy import func, or_, select

from greenhouse_core.models import (
    GlobalIrrigationConfig,
    IrrigationConfig,
    Irrigator,
)
from greenhouse_core.repository._base import RepositoryBase
from greenhouse_core.repository._types import (
    _GLOBAL_CONFIG_DEFAULTS,
    EffectiveConfig,
    IrrigationConfigPatch,
    IrrigatorExistsError,
    IrrigatorPatch,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class IrrigatorMixin(RepositoryBase):
    """Irrigators and irrigation configs."""

    # ── Irrigators ────────────────────────────────────────────────────────────

    def add_irrigator(
        self,
        cluster_id: int,
        tuya_device_id: str,
        name: str,
        irrigator_type: str,
        config: dict[str, Any],
    ) -> int:
        """Add an irrigator device and return its ID.

        A cluster has at most one irrigator. Raises IrrigatorExistsError when
        the cluster already has one.
        """
        if self.get_irrigator_for_cluster(cluster_id) is not None:
            msg = f"cluster {cluster_id} already has an irrigator"
            raise IrrigatorExistsError(msg)
        irrigator = Irrigator(
            cluster_id=cluster_id,
            tuya_device_id=tuya_device_id,
            name=name,
            type=irrigator_type,
            config=json.dumps(config),
        )
        self.session.add(irrigator)
        self.session.flush()
        return irrigator.id

    def get_irrigator(self, irrigator_id: int) -> Irrigator | None:
        """Get an irrigator by ID."""
        return self.session.get(Irrigator, irrigator_id)

    def get_irrigator_for_cluster(self, cluster_id: int) -> Irrigator | None:
        """Get the cluster's single irrigator, or None if it has none."""
        return self.session.scalars(select(Irrigator).where(Irrigator.cluster_id == cluster_id)).first()

    # ── Irrigation Configs ────────────────────────────────────────────────────

    _CONFIG_PATCHABLE_FIELDS = (
        "mode",
        "duration_minutes",
        "interval_hours",
        "auto_run",
        "daily_cap_minutes",
        "max_events_per_day",
        "quiet_start_hour",
        "quiet_end_hour",
    )

    def set_irrigation_config(self, cluster_id: int, **fields: Unpack[IrrigationConfigPatch]) -> int:
        """Upsert a cluster's irrigation config, mutating only the fields provided.

        Every field is nullable: passing ``None`` clears the cluster-level
        override (the effective resolver will then fall through to the
        global default). Fields omitted entirely retain their stored value
        — so callers acting on a single form input do not need to round-trip
        every column.
        """
        patch: Mapping[str, object] = fields  # indexed by the patchable-field names below
        existing = self.session.scalar(select(IrrigationConfig).where(IrrigationConfig.cluster_id == cluster_id))
        if existing:
            for key in self._CONFIG_PATCHABLE_FIELDS:
                if key in patch:
                    setattr(existing, key, patch[key])
            existing.last_updated = int(time.time())
            self.session.flush()
            return existing.id

        config = IrrigationConfig(
            cluster_id=cluster_id,
            last_updated=int(time.time()),
            **{k: v for k, v in fields.items() if k in self._CONFIG_PATCHABLE_FIELDS},
        )
        self.session.add(config)
        self.session.flush()
        return config.id

    def get_irrigation_config(self, cluster_id: int) -> IrrigationConfig | None:
        """Get the declared (raw, possibly partial) config for a cluster."""
        return self.session.scalar(select(IrrigationConfig).where(IrrigationConfig.cluster_id == cluster_id))

    # ── Global Irrigation Config (singleton) ─────────────────────────────────

    def get_global_irrigation_config(self) -> GlobalIrrigationConfig:
        """Return the singleton global config row, creating one if missing.

        Production deployments get the canonical seed (quiet hours 00:00–05:00,
        ``auto_run`` defaulting to True) from the migration. This auto-create
        path covers fresh-DB scenarios that bypass migrations — primarily the
        test suite, which calls ``Base.metadata.create_all`` and then immediately
        starts asserting against the engine. Seeding ``quiet_start = quiet_end =
        None`` here means quiet hours are *off* by default in those flows; tests
        that need the production quiet window opt into it explicitly.
        """
        row = self.session.scalar(select(GlobalIrrigationConfig).limit(1))
        if row:
            return row
        row = GlobalIrrigationConfig(last_updated=int(time.time()))
        self.session.add(row)
        self.session.flush()
        return row

    def update_global_irrigation_config(self, **fields: Unpack[IrrigationConfigPatch]) -> GlobalIrrigationConfig:
        """Patch the singleton global config; only the supplied keys are set.

        Pass ``None`` to clear a previously set field (the effective resolver
        then falls through to the project-wide constant). Omit a key entirely
        to leave its stored value untouched.
        """
        patch: Mapping[str, object] = fields  # indexed by the patchable-field names below
        row = self.get_global_irrigation_config()
        for key in self._CONFIG_PATCHABLE_FIELDS:
            if key in patch:
                setattr(row, key, patch[key])
        row.last_updated = int(time.time())
        self.session.flush()
        return row

    # ── Effective config resolution ──────────────────────────────────────────

    def get_effective_config(self, cluster_id: int) -> EffectiveConfig:
        """Resolve every config field walking cluster → global → constants.

        Returns a dict keyed by field name; each value is
        ``{"value": resolved, "source": "cluster" | "global" | "default"}``
        so the API/UI can render inheritance state without re-querying.
        """
        cluster_cfg = self.get_irrigation_config(cluster_id)
        global_cfg = self.get_global_irrigation_config()
        out: EffectiveConfig = {}
        for field in self._CONFIG_PATCHABLE_FIELDS:
            cluster_value = getattr(cluster_cfg, field, None) if cluster_cfg else None
            if cluster_value is not None:
                out[field] = {"value": cluster_value, "source": "cluster"}
                continue
            global_value = getattr(global_cfg, field, None)
            if global_value is not None:
                out[field] = {"value": global_value, "source": "global"}
                continue
            out[field] = {"value": _GLOBAL_CONFIG_DEFAULTS.get(field), "source": "default"}
        return out

    def list_all_irrigators(
        self,
        *,
        filter_cluster_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> list[Irrigator]:
        """All irrigators regardless of cluster.

        Args:
            filter_cluster_id: Restrict results to one cluster.
            limit: Maximum rows to return; ``None`` means no cap.
            after_id: Id-based cursor — return only rows with ``id > after_id``.

        Returns:
            Irrigators ordered by id ascending so cursor pagination is stable.
        """
        stmt = select(Irrigator).order_by(Irrigator.id)
        if filter_cluster_id is not None:
            stmt = stmt.where(Irrigator.cluster_id == filter_cluster_id)
        return list(self.session.scalars(self._page(stmt, Irrigator.id, limit=limit, after_id=after_id)))

    # ── Search (Command-K palette) ────────────────────────────────────────────
    # ``pattern`` is a SQL LIKE pattern matched case-insensitively; ``prefix`` is the raw
    # query, matched case-sensitively as a Tuya device-id prefix.

    def search_irrigators(self, pattern: str, prefix: str, limit: int) -> list[Irrigator]:
        """Irrigators whose name matches ``pattern`` or whose Tuya device id starts with ``prefix``."""
        return list(
            self.session.scalars(
                select(Irrigator)
                .where(
                    or_(
                        func.lower(Irrigator.name).like(func.lower(pattern)),
                        Irrigator.tuya_device_id.like(f"{prefix}%"),
                    )
                )
                .limit(limit)
            )
        )

    def update_irrigator(self, irrigator_id: int, **fields: Unpack[IrrigatorPatch]) -> Irrigator | None:
        """Patch irrigator fields; ``config`` is JSON-serialised if a dict."""
        irrigator = self.session.get(Irrigator, irrigator_id)
        if not irrigator:
            return None
        self._patch_fields(irrigator, fields, json_fields=frozenset({"config"}))
        self.session.flush()
        return irrigator

    def delete_irrigator(self, irrigator_id: int) -> bool:
        """Delete an irrigator (cascades to its events)."""
        return self._delete_by_id(Irrigator, irrigator_id)

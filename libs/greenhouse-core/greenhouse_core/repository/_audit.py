"""Audit-trail rows: decision logs, activity events and the alert inbox."""

import json
import time
from typing import TYPE_CHECKING, Any, Unpack

from sqlalchemy import func, select

from greenhouse_core.models import (
    ENTITY_CLUSTER,
    TRIGGERED_BY_AUTO,
    ActivityEvent,
    ActivitySource,
    Alert,
    DecisionLog,
    EntityType,
)
from greenhouse_core.repository._base import RepositoryBase
from greenhouse_core.repository._types import (
    ActivityEventOptions,
    ActivityFilters,
    AlertOptions,
    DecisionLogEntry,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


class AuditMixin(RepositoryBase):
    """Decision logs, activity events and alerts."""

    # ── Decision Log ──────────────────────────────────────────────────────────

    def add_decision_log(
        self,
        cluster_id: int,
        **entry: Unpack[DecisionLogEntry],
    ) -> int:
        """Persist a single decision evaluation; returns the new row id."""
        row = DecisionLog(
            cluster_id=cluster_id,
            evaluated_at=entry["evaluated_at"],
            action=entry["action"],
            duration_minutes=entry["duration_minutes"],
            interval_hours=entry["interval_hours"],
            confidence=entry["confidence"],
            primary_code=entry["primary_code"],
            reason_text=entry["reason_text"],
            payload_json=json.dumps(entry["payload"], default=str),
            triggered_by=entry.get("triggered_by", TRIGGERED_BY_AUTO),
            actuated=entry.get("actuated", False),
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def set_decision_actuated(self, log_id: int, *, actuated: bool = True) -> None:
        """Mark a persisted decision log as having driven (or not) real actuation."""
        row = self.session.get(DecisionLog, log_id)
        if row is not None:
            row.actuated = actuated
            self.session.flush()

    def rewrite_decision_log(
        self,
        log_id: int,
        *,
        action: str,
        primary_code: str | None,
        reason_text: str,
        payload: dict[str, Any],
    ) -> None:
        """Overwrite a persisted decision with the verdict a server-side gate turned it into.

        The engine logs its decision before the server's actuation gates (device health,
        global dry run, daily caps) run; when one of them turns the run into a skip, the
        row is rewritten so the audit trail shows the decision that was actually applied.
        """
        row = self.session.get(DecisionLog, log_id)
        if row is None:
            return
        row.action = action
        row.primary_code = primary_code
        row.reason_text = reason_text
        row.payload_json = json.dumps(payload, default=str)
        self.session.flush()

    def list_decision_logs(self, cluster_id: int, limit: int = 50) -> list[DecisionLog]:
        """Recent decisions for a cluster, newest first."""
        return list(
            self.session.scalars(
                select(DecisionLog)
                .where(DecisionLog.cluster_id == cluster_id)
                .order_by(DecisionLog.evaluated_at.desc())
                .limit(limit)
            )
        )

    # ── Activity Events ───────────────────────────────────────────────────────

    def add_activity_event(
        self,
        source: ActivitySource,
        entity_type: EntityType,
        code: str,
        message: str,
        **options: Unpack[ActivityEventOptions],
    ) -> int:
        """Append a polymorphic activity event; returns the new row id."""
        payload = options.get("payload")
        row = ActivityEvent(
            timestamp=options.get("timestamp") or int(time.time()),
            source=source,
            entity_type=entity_type,
            entity_id=options.get("entity_id"),
            severity=options.get("severity", "info"),
            code=code,
            message=message,
            payload_json=json.dumps(payload, default=str) if payload else None,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def list_activity_events(self, **filters: Unpack[ActivityFilters]) -> list[ActivityEvent]:
        """List events newest-first with optional filters and pagination cursor."""
        entity_type, entity_id = filters.get("entity_type"), filters.get("entity_id")
        source, severity, before = filters.get("source"), filters.get("severity"), filters.get("before")
        stmt = select(ActivityEvent).order_by(ActivityEvent.timestamp.desc()).limit(filters.get("limit", 100))
        if entity_type:
            stmt = stmt.where(ActivityEvent.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(ActivityEvent.entity_id == entity_id)
        if source:
            stmt = stmt.where(ActivityEvent.source == source)
        if severity:
            stmt = stmt.where(ActivityEvent.severity == severity)
        if before is not None:
            stmt = stmt.where(ActivityEvent.timestamp < before)
        return list(self.session.scalars(stmt))

    def list_activity_events_since(
        self,
        since: int,
        *,
        source: str,
        codes: "Iterable[str]",
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> list[ActivityEvent]:
        """Every event with one of ``codes`` at or after ``since``, newest first — no row cap.

        For lookups that must not miss an old row inside a time window (the leak-check
        markers): a time bound, never a row limit, keeps the scan complete. ``entity_type`` /
        ``entity_id`` narrow it to one entity when given.
        """
        stmt = (
            select(ActivityEvent)
            .where(
                ActivityEvent.timestamp >= since,
                ActivityEvent.source == source,
                ActivityEvent.code.in_(list(codes)),
            )
            .order_by(ActivityEvent.timestamp.desc(), ActivityEvent.id.desc())
        )
        if entity_type is not None:
            stmt = stmt.where(ActivityEvent.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(ActivityEvent.entity_id == entity_id)
        return list(self.session.scalars(stmt))

    # ── Alerts ────────────────────────────────────────────────────────────────

    def upsert_alert(
        self,
        dedup_key: str,
        source: ActivitySource,
        code: str,
        title: str,
        message: str,
        **options: Unpack[AlertOptions],
    ) -> Alert:
        """Insert or refresh an alert keyed by ``dedup_key``.

        Increments occurrence_count and updates last_seen_at when an open or
        acknowledged alert with the same key already exists. A resolved alert
        with the same key is reopened.
        """
        now = options.get("seen_at") or int(time.time())
        severity = options.get("severity", "info")
        existing = self.session.scalar(select(Alert).where(Alert.dedup_key == dedup_key))
        if existing:
            existing.last_seen_at = now
            existing.occurrence_count += 1
            existing.severity = severity
            existing.message = message
            existing.title = title
            if existing.status == "resolved":
                existing.status = "open"
                existing.resolved_at = None
            self.session.flush()
            return existing
        alert = Alert(
            dedup_key=dedup_key,
            source=source,
            code=code,
            severity=severity,
            entity_type=options.get("entity_type", ENTITY_CLUSTER),
            entity_id=options.get("entity_id"),
            cluster_id=options.get("cluster_id"),
            plant_id=options.get("plant_id"),
            title=title,
            message=message,
            payload_json=json.dumps(payload, default=str) if (payload := options.get("payload")) else None,
            status="open",
            first_seen_at=now,
            last_seen_at=now,
            occurrence_count=1,
        )
        self.session.add(alert)
        self.session.flush()
        return alert

    def list_alerts(
        self,
        *,
        status: str | None = None,
        cluster_id: int | None = None,
        plant_id: int | None = None,
        limit: int = 100,
        after_id: int | None = None,
    ) -> list[Alert]:
        """List alerts ordered by last_seen_at desc with optional filters.

        Args:
            status: Filter by lifecycle status.
            cluster_id: Restrict to alerts attached to one cluster.
            plant_id: Restrict to alerts attached to one plant.
            limit: Maximum rows to return.
            after_id: Id-based cursor — return only rows with ``id < after_id``.
                Combined with the ``last_seen_at desc`` ordering this gives a
                stable backward-walk for paging through the inbox.
        """
        stmt = select(Alert).order_by(Alert.last_seen_at.desc(), Alert.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(Alert.status == status)
        if cluster_id is not None:
            stmt = stmt.where(Alert.cluster_id == cluster_id)
        if plant_id is not None:
            stmt = stmt.where(Alert.plant_id == plant_id)
        if after_id is not None:
            stmt = stmt.where(Alert.id < after_id)
        return list(self.session.scalars(stmt))

    def get_alert(self, alert_id: int) -> Alert | None:
        """Fetch a single alert by id."""
        return self.session.get(Alert, alert_id)

    def get_active_alert(
        self,
        code: str,
        *,
        cluster_id: int | None = None,
        since: int | None = None,
    ) -> Alert | None:
        """Return the most recently-seen unresolved alert for ``code``, if any.

        "Unresolved" means ``open`` or ``acknowledged``: acknowledging says the
        operator has seen the alert, not that the condition is gone, so only an
        explicit resolve clears it. Callers that gate behaviour on an alert
        (the engine's leak hold) pass ``since`` to bound how long a stale
        detection may keep gating.

        Args:
            code: Alert code to look for (e.g. ``LEAK_ALERT_CODE``).
            cluster_id: Restrict to one cluster.
            since: Only consider alerts whose ``last_seen_at`` is at or after
                this Unix timestamp.

        Returns:
            The matching Alert with the newest ``last_seen_at``, or ``None``.
        """
        stmt = (
            select(Alert)
            .where(Alert.code == code, Alert.status != "resolved")
            .order_by(Alert.last_seen_at.desc(), Alert.id.desc())
            .limit(1)
        )
        if cluster_id is not None:
            stmt = stmt.where(Alert.cluster_id == cluster_id)
        if since is not None:
            stmt = stmt.where(Alert.last_seen_at >= since)
        return self.session.scalar(stmt)

    def list_unresolved_alerts(self, source: str) -> list[Alert]:
        """Every open or acknowledged alert from ``source``, oldest first — no row cap.

        For state that must be rebuilt from the inbox (the health monitor's actuation cache
        after a restart): a missed row would silently drop a block.
        """
        return list(
            self.session.scalars(
                select(Alert).where(Alert.source == source, Alert.status != "resolved").order_by(Alert.id)
            )
        )

    def get_open_alert_by_key(self, dedup_key: str) -> Alert | None:
        """The unresolved (open or acknowledged) alert with ``dedup_key``, if any."""
        return self.session.scalar(select(Alert).where(Alert.dedup_key == dedup_key, Alert.status != "resolved"))

    def acknowledge_alert(self, alert_id: int) -> Alert | None:
        """Move an open alert to ``acknowledged``; returns the updated row."""
        alert = self.session.get(Alert, alert_id)
        if not alert:
            return None
        if alert.status == "open":
            alert.status = "acknowledged"
            alert.acknowledged_at = int(time.time())
            self.session.flush()
        return alert

    def resolve_alert(self, alert_id: int) -> Alert | None:
        """Mark an alert resolved; returns the updated row."""
        alert = self.session.get(Alert, alert_id)
        if not alert:
            return None
        alert.status = "resolved"
        alert.resolved_at = int(time.time())
        self.session.flush()
        return alert

    def count_open_alerts(self) -> int:
        """Total open alerts (powers the top-bar bell badge)."""
        return self.session.scalar(select(func.count()).select_from(Alert).where(Alert.status == "open")) or 0

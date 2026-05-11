"""Alert inbox service — bridges learning + maintenance into persisted alerts.

Each producer (learning, maintenance, leak, anomaly) computes its current
findings on demand. ``sync_cluster_alerts`` upserts those findings into the
persisted ``alerts`` table keyed by stable dedup keys, and resolves any
previously-open alerts whose triggering condition has cleared. The inbox
becomes the single source of truth for the bell badge and /alerts page.
"""

import time

from greenhouse_core.models import (
    ENTITY_CLUSTER,
    ENTITY_SENSOR,
    Alert,
    Cluster,
)
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.maintenance import collect_learning_alerts, collect_maintenance_alerts

SOURCE_LEARNING = "learning"
SOURCE_MAINTENANCE = "maintenance"
SOURCE_DECISION = "decision"
SOURCE_LEAK = "leak"
SOURCE_ANOMALY = "anomaly"
SOURCE_SYSTEM = "system"


def _dedup_key(source: str, code: str, cluster_id: int | None, message: str) -> str:
    """Deterministic key so repeats collapse onto the same row.

    ``message`` is included to distinguish per-sensor variants of the same
    code (e.g. "stale_data" for sensor A vs. sensor B). Stripping leading
    "<sensor name>:" makes the key stable when the message body changes
    cosmetically but the offending sensor stays the same.
    """
    head = message.split(":", 1)[0].strip().lower()
    return f"{source}::{code}::{cluster_id or 0}::{head}"


def _title_for(code: str, message: str) -> str:
    """Short human title — first ":" prefix or capitalized code."""
    head = message.split(":", 1)[0].strip()
    if head and head != message:
        return f"{code.replace('_', ' ').title()} · {head}"
    return code.replace("_", " ").title()


def sync_cluster_alerts(
    repo: IrrigationRepository,
    cluster_id: int,
    plant_db: PlantDatabase,
) -> list[Alert]:
    """Recompute alerts for a cluster and reconcile with the inbox.

    Returns the list of currently-open or acknowledged alerts for the
    cluster after sync. Alerts whose triggering condition is no longer
    present are auto-resolved.
    """
    cluster = repo.get_cluster(cluster_id)
    if not cluster:
        return []
    now = int(time.time())

    findings: list[tuple[str, dict]] = []
    for raw in collect_learning_alerts(repo, cluster_id, plant_db):
        findings.append((SOURCE_LEARNING, raw))
    for raw in collect_maintenance_alerts(repo, cluster_id, plant_db):
        findings.append((SOURCE_MAINTENANCE, raw))

    seen_keys: set[str] = set()
    for source, raw in findings:
        code = raw.get("type", "alert")
        message = raw.get("message", "")
        severity = raw.get("severity", "info")
        key = _dedup_key(source, code, cluster_id, message)
        seen_keys.add(key)
        repo.upsert_alert(
            dedup_key=key,
            source=source,
            code=code,
            title=_title_for(code, message),
            message=message,
            severity=severity,
            entity_type=ENTITY_CLUSTER,
            entity_id=cluster_id,
            cluster_id=cluster_id,
            seen_at=now,
        )

    auto_resolve_cleared(repo, cluster_id, sources=(SOURCE_LEARNING, SOURCE_MAINTENANCE), seen_keys=seen_keys)
    return repo.list_alerts(cluster_id=cluster_id, limit=200)


def auto_resolve_cleared(
    repo: IrrigationRepository,
    cluster_id: int,
    sources: tuple[str, ...],
    seen_keys: set[str],
) -> None:
    """Close any open/ack alerts from these sources whose condition has cleared."""
    for alert in repo.list_alerts(cluster_id=cluster_id, limit=200):
        if alert.status == "resolved" or alert.source not in sources:
            continue
        if alert.dedup_key not in seen_keys:
            repo.resolve_alert(alert.id)


def sync_all_alerts(repo: IrrigationRepository, plant_db: PlantDatabase) -> int:
    """Run sync_cluster_alerts across every cluster; returns total open count."""
    clusters: list[Cluster] = repo.list_clusters()
    for c in clusters:
        sync_cluster_alerts(repo, c.id, plant_db)
    return repo.count_open_alerts()


def raise_alert(
    repo: IrrigationRepository,
    *,
    source: str,
    code: str,
    title: str,
    message: str,
    severity: str = "info",
    cluster_id: int | None = None,
    plant_id: int | None = None,
    sensor_id: int | None = None,
    payload: dict | None = None,
) -> Alert:
    """Convenience wrapper: build a dedup_key and upsert an alert."""
    entity_type = ENTITY_SENSOR if sensor_id else ENTITY_CLUSTER
    entity_id = sensor_id or cluster_id
    key = _dedup_key(source, code, cluster_id, f"{entity_type}{entity_id or 0}")
    return repo.upsert_alert(
        dedup_key=key,
        source=source,
        code=code,
        title=title,
        message=message,
        severity=severity,
        entity_type=entity_type,
        entity_id=entity_id,
        cluster_id=cluster_id,
        plant_id=plant_id,
        payload=payload,
    )

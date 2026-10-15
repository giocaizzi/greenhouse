"""Alert inbox service — bridges learning + maintenance into persisted alerts.

Each producer (learning, maintenance, leak, anomaly) computes its current
findings on demand. ``sync_cluster_alerts`` upserts those findings into the
persisted ``alerts`` table keyed by stable dedup keys, and resolves any
previously-open alerts whose triggering condition has cleared. The inbox
becomes the single source of truth for the bell badge and /alerts page.
"""

import time
from typing import Any, NotRequired, TypedDict, Unpack

from greenhouse_core.constants import (
    ALERT_RENOTIFY_INTERVAL_SECONDS,
    ALERT_RENOTIFY_OCCURRENCES,
    ALERT_RENOTIFY_SEVERITIES,
    ALERT_SCAN_LIMIT,
)
from greenhouse_core.models import (
    ENTITY_CLUSTER,
    ENTITY_PLANT,
    ENTITY_SENSOR,
    SOURCE_LEARNING,
    SOURCE_MAINTENANCE,
    ActivitySource,
    Alert,
    Cluster,
)
from greenhouse_core.plant_db import PlantDatabase
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.maintenance import AlertFinding, collect_learning_alerts, collect_maintenance_alerts
from greenhouse_server.services.notify import NtfyClient, maybe_notify

# Alert severities that warrant a push (info is suppressed).
_NOTIFY_SEVERITIES = ("warning", "critical")


def _dedup_key(source: ActivitySource, code: str, cluster_id: int | None, message: str) -> str:
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


def _push_due(alert: Alert, *, was_open: bool) -> bool:
    """Whether this upsert of ``alert`` earns a push (see :func:`notify_if_new_alert`)."""
    if not was_open:
        return True
    if alert.severity not in ALERT_RENOTIFY_SEVERITIES:
        return False
    if alert.occurrence_count in ALERT_RENOTIFY_OCCURRENCES:
        return True
    if alert.occurrence_count < max(ALERT_RENOTIFY_OCCURRENCES):
        return False
    quiet_since = max(alert.last_notified_at or alert.first_seen_at, alert.acknowledged_at or 0)
    return alert.last_seen_at - quiet_since >= ALERT_RENOTIFY_INTERVAL_SECONDS


def notify_if_new_alert(
    repo: IrrigationRepository, notifier: NtfyClient | None, alert: Alert, *, was_open: bool
) -> None:
    """Push a warning/critical alert this upsert opened, and re-push one that stays open.

    "Opened" means the row is new *or* a resolved row was re-opened (B-5: the old
    ``occurrence_count == 1`` gate kept a re-raised alert silent forever). A repeat
    of an alert that is still open or acknowledged (``was_open``) stays silent, except
    that one in ``ALERT_RENOTIFY_SEVERITIES`` escalates (#146: an ``actuation_failed``
    warning once went 44 occurrences on a single push): it pushes again at each ``ALERT_RENOTIFY_OCCURRENCES``
    count, then at most once per ``ALERT_RENOTIFY_INTERVAL_SECONDS`` while unresolved.
    Acknowledging restarts that interval, so an acknowledged alert is quiet until the next
    step. ``info`` advisories stay silent. The ``notify_alerts`` preference toggle is
    honoured via :func:`maybe_notify`.

    Args:
        repo: Active repository session (reads the preferences row).
        notifier: ntfy client, or None when notifications are unconfigured.
        alert: The row the upsert returned; ``last_notified_at`` is stamped when a push is due.
        was_open: Whether an unresolved alert with the same dedup key existed before the upsert.
    """
    if notifier is None or alert.severity not in _NOTIFY_SEVERITIES:
        return
    if not _push_due(alert, was_open=was_open):
        return
    alert.last_notified_at = alert.last_seen_at
    maybe_notify(
        notifier,
        repo.get_preferences(),
        "alerts",
        lambda n: n.notify_alert(severity=alert.severity, title=alert.title, message=alert.message),
    )


def is_alert_open(repo: IrrigationRepository, dedup_key: str) -> bool:
    """Whether an unresolved alert holds ``dedup_key`` — read before an upsert to feed ``was_open``."""
    return repo.get_open_alert_by_key(dedup_key) is not None


def sync_cluster_alerts(
    repo: IrrigationRepository,
    cluster_id: int,
    plant_db: PlantDatabase,
    notifier: NtfyClient | None = None,
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

    findings: list[tuple[ActivitySource, AlertFinding]] = [
        (SOURCE_LEARNING, raw) for raw in collect_learning_alerts(repo, cluster_id, plant_db)
    ]
    findings.extend((SOURCE_MAINTENANCE, raw) for raw in collect_maintenance_alerts(repo, cluster_id, plant_db))

    seen_keys: set[str] = set()
    for source, raw in findings:
        code = raw.get("type", "alert")
        message = raw.get("message", "")
        severity = raw.get("severity", "info")
        key = _dedup_key(source, code, cluster_id, message)
        seen_keys.add(key)
        was_open = is_alert_open(repo, key)
        alert = repo.upsert_alert(
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
        notify_if_new_alert(repo, notifier, alert, was_open=was_open)

    auto_resolve_cleared(repo, cluster_id, sources=(SOURCE_LEARNING, SOURCE_MAINTENANCE), seen_keys=seen_keys)
    return repo.list_alerts(cluster_id=cluster_id, limit=ALERT_SCAN_LIMIT)


def auto_resolve_cleared(
    repo: IrrigationRepository,
    cluster_id: int,
    sources: tuple[ActivitySource, ...],
    seen_keys: set[str],
) -> None:
    """Close any open/ack alerts from these sources whose condition has cleared."""
    for alert in repo.list_alerts(cluster_id=cluster_id, limit=ALERT_SCAN_LIMIT):
        if alert.status == "resolved" or alert.source not in sources:
            continue
        if alert.dedup_key not in seen_keys:
            repo.resolve_alert(alert.id)


def sync_all_alerts(
    repo: IrrigationRepository,
    plant_db: PlantDatabase,
    notifier: NtfyClient | None = None,
) -> int:
    """Run sync_cluster_alerts across every cluster; returns total open count."""
    clusters: list[Cluster] = repo.list_clusters()
    for c in clusters:
        sync_cluster_alerts(repo, c.id, plant_db, notifier=notifier)
    return repo.count_open_alerts()


class NewAlert(TypedDict):
    """The alert :func:`raise_alert` opens or refreshes, passed as keywords.

    ``severity`` defaults to ``"info"``; a ``sensor_id`` makes the sensor the alert's entity,
    else a ``plant_id`` the plant (otherwise the cluster).
    """

    source: ActivitySource
    code: str
    title: str
    message: str
    severity: NotRequired[str]
    cluster_id: NotRequired[int | None]
    plant_id: NotRequired[int | None]
    sensor_id: NotRequired[int | None]
    payload: NotRequired[dict[str, Any] | None]


def _entity_key(
    source: ActivitySource, code: str, cluster_id: int | None, sensor_id: int | None, plant_id: int | None = None
) -> str:
    """The dedup key :func:`raise_alert` gives an alert on a sensor, a plant, else its cluster (or system-wide).

    A plant's key leaves the cluster out, so the alert stays one row when the plant moves cluster.
    """
    if plant_id and not sensor_id:
        return _dedup_key(source, code, None, f"{ENTITY_PLANT}{plant_id}")
    entity_type = ENTITY_SENSOR if sensor_id else ENTITY_CLUSTER
    return _dedup_key(source, code, cluster_id, f"{entity_type}{sensor_id or cluster_id or 0}")


def raise_alert(repo: IrrigationRepository, *, notifier: NtfyClient | None = None, **new: Unpack[NewAlert]) -> Alert:
    """Convenience wrapper: build a dedup_key, upsert the alert, push when this call opened it."""
    sensor_id, cluster_id, plant_id = new.get("sensor_id"), new.get("cluster_id"), new.get("plant_id")
    entity_type = ENTITY_SENSOR if sensor_id else ENTITY_PLANT if plant_id else ENTITY_CLUSTER
    entity_id = sensor_id or plant_id or cluster_id
    key = _entity_key(new["source"], new["code"], cluster_id, sensor_id, plant_id)
    was_open = is_alert_open(repo, key)
    alert = repo.upsert_alert(
        dedup_key=key,
        source=new["source"],
        code=new["code"],
        title=new["title"],
        message=new["message"],
        severity=new.get("severity", "info"),
        entity_type=entity_type,
        entity_id=entity_id,
        cluster_id=cluster_id,
        plant_id=plant_id,
        payload=new.get("payload"),
    )
    notify_if_new_alert(repo, notifier, alert, was_open=was_open)
    return alert


class AlertEntity(TypedDict, total=False):
    """The entity :func:`clear_alert` resolves an alert on; none of them means system-wide."""

    cluster_id: int | None
    sensor_id: int | None
    plant_id: int | None


def find_open_alert(
    repo: IrrigationRepository, *, source: ActivitySource, code: str, **entity: Unpack[AlertEntity]
) -> Alert | None:
    """The unresolved alert :func:`raise_alert` keyed on ``code`` and this cluster / sensor / plant, if any."""
    key = _entity_key(source, code, entity.get("cluster_id"), entity.get("sensor_id"), entity.get("plant_id"))
    return repo.get_open_alert_by_key(key)


def clear_alert(
    repo: IrrigationRepository, *, source: ActivitySource, code: str, **entity: Unpack[AlertEntity]
) -> None:
    """Resolve the open alert :func:`raise_alert` keyed on ``code`` and this cluster / sensor / plant."""
    alert = find_open_alert(repo, source=source, code=code, **entity)
    if alert is not None:
        repo.resolve_alert(alert.id)

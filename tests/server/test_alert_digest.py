"""The daily ntfy digest of critical alerts still unresolved (issue #181)."""

import time_machine

from golden import FROZEN_TS
from greenhouse_core.constants import SECONDS_PER_DAY, SECONDS_PER_HOUR
from greenhouse_core.models import SOURCE_LEAK
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.alerts import push_critical_digest
from greenhouse_server.services.notify import NtfyClient


class _RecordingNotifier(NtfyClient):
    """Records ``notify_alert`` calls instead of doing HTTP (never binds a topic)."""

    def __init__(self) -> None:
        self.alerts: list[dict[str, object]] = []

    def notify_alert(self, **kwargs: object) -> bool:
        self.alerts.append(kwargs)
        return True


def _alert(db: IrrigationRepository, key: str, severity: str, seen_at: int) -> int:
    return db.upsert_alert(
        key, SOURCE_LEAK, "leak_or_stuck_valve", f"Leak {key}", "m", severity=severity, seen_at=seen_at
    ).id


@time_machine.travel(FROZEN_TS, tick=False)
def test_digest_counts_unresolved_criticals_and_names_the_oldest(tmp_db: IrrigationRepository) -> None:
    """Open and acknowledged criticals count; resolved and non-critical ones do not."""
    oldest = _alert(tmp_db, "a", "critical", FROZEN_TS - 3 * SECONDS_PER_DAY - 4 * SECONDS_PER_HOUR)
    tmp_db.acknowledge_alert(oldest)
    _alert(tmp_db, "b", "critical", FROZEN_TS - SECONDS_PER_HOUR)
    tmp_db.resolve_alert(_alert(tmp_db, "c", "critical", FROZEN_TS - 9 * SECONDS_PER_DAY))
    _alert(tmp_db, "d", "warning", FROZEN_TS - 9 * SECONDS_PER_DAY)
    notifier = _RecordingNotifier()
    push_critical_digest(tmp_db, notifier)
    assert notifier.alerts == [
        {"severity": "critical", "title": "2 critical alerts still open", "message": "Oldest, open 3d 4h: Leak a"}
    ]


def test_no_unresolved_critical_means_no_push(tmp_db: IrrigationRepository) -> None:
    """A quiet day sends nothing."""
    _alert(tmp_db, "w", "warning", FROZEN_TS)
    notifier = _RecordingNotifier()
    push_critical_digest(tmp_db, notifier)
    assert notifier.alerts == []


def test_notify_alerts_off_silences_the_digest(tmp_db: IrrigationRepository) -> None:
    """The digest is an alert push: the ``notify_alerts`` preference gates it."""
    _alert(tmp_db, "a", "critical", FROZEN_TS)
    tmp_db.update_preferences(notify_alerts=False)
    notifier = _RecordingNotifier()
    push_critical_digest(tmp_db, notifier)
    assert notifier.alerts == []

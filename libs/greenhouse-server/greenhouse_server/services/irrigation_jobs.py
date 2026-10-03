"""Post-irrigation leak-check job plumbing: schedule, run once, and re-arm after a restart.

Background-job code, kept apart from the pipeline in :mod:`.irrigation` (which arms a check
after every auto start and re-exports these names). The scheduler is imported lazily, inside
each function, because it sits above the services layer and reads the app at call time.
Records go to the ``greenhouse_server.services.irrigation`` logger, the pipeline's, so log
routing and filters keyed on it keep matching.
"""

import json
import logging
import time as _time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from greenhouse_core.constants import LEAK_CHECK_ACTIVITY_SCAN_LIMIT, LEAK_CHECK_DELAY_SECONDS, LEAK_HOLD_HOURS
from greenhouse_core.models import ENTITY_CLUSTER, EVENT_ACTION_START, SOURCE_LEAK, TRIGGERED_BY_AUTO
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.jobs import job_session, read_session

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger("greenhouse_server.services.irrigation")

LEAK_CHECK_ACTIVITY_CODE = "leak_check"
# Activity codes that prove a start's leak check already completed:
# ``leak_check`` is written by every completed check; ``leak_hold`` is
# written when a check raised a hold (and predates the ``leak_check`` marker).
_LEAK_CHECK_DONE_CODES = frozenset({LEAK_CHECK_ACTIVITY_CODE, "leak_hold"})


def _leak_check_done(repo: IrrigationRepository, cluster_id: int, started_at: int) -> bool:
    """True when the leak check for this (cluster, start) has already completed.

    There is no dedicated table for leak-check runs; the activity log is the
    durable record. Every completed check writes a ``leak_check`` row (see
    :func:`_run_leak_check`), and a check that raised a hold also wrote a
    ``leak_hold`` row — both carry ``started_at`` in their payload.
    """
    for event in repo.list_activity_events(
        entity_type=ENTITY_CLUSTER, entity_id=cluster_id, source=SOURCE_LEAK, limit=LEAK_CHECK_ACTIVITY_SCAN_LIMIT
    ):
        if event.code not in _LEAK_CHECK_DONE_CODES or not event.payload_json:
            continue
        try:
            if json.loads(event.payload_json).get("started_at") == started_at:
                return True
        except (ValueError, AttributeError):
            continue
    return False


def _run_leak_check(cluster_id: int, started_at: int) -> None:
    """Scheduler job: run the post-irrigation leak check once, then mark it done.

    Idempotent: skips when the check for this start already completed (a
    normal job and a restart re-arm can both exist for the same start). The
    ``leak_check`` marker is committed in the same transaction as the check's
    own effects, so a failed check leaves no marker and is re-armed on the
    next startup.
    """
    from greenhouse_server.scheduler import _app
    from greenhouse_server.services.leak import LeakDetectionService

    if _app is None:
        return
    # commit=False: the "already done" early return leaves its read-only transaction to close().
    with job_session(_app, logger, "Leak check job failed for cluster %d", cluster_id, commit=False) as session:
        repo = IrrigationRepository(session)
        if _leak_check_done(repo, cluster_id, started_at):
            logger.debug("Leak check for cluster %d start %d already done", cluster_id, started_at)
            return
        alerts = LeakDetectionService(
            repo, _app.state.plant_db, notifier=getattr(_app.state, "ntfy_notifier", None)
        ).check_after_irrigation(cluster_id, started_at)
        repo.add_activity_event(
            source=SOURCE_LEAK,
            entity_type=ENTITY_CLUSTER,
            entity_id=cluster_id,
            code=LEAK_CHECK_ACTIVITY_CODE,
            message=(
                f"post-irrigation leak check: {len(alerts)} sensor(s) flagged"
                if alerts
                else "post-irrigation leak check: no leak found"
            ),
            severity="info",
            payload={"started_at": started_at, "flagged_sensor_ids": [a.entity_id for a in alerts]},
        )
        session.commit()


def _add_leak_check_job(cluster_id: int, started_at: int, *, run_at: int | None = None) -> None:
    from greenhouse_server.scheduler import scheduler

    due = started_at + LEAK_CHECK_DELAY_SECONDS
    scheduler.add_job(
        _run_leak_check,
        "date",
        run_date=datetime.fromtimestamp(run_at if run_at is not None else due, tz=UTC),
        args=[cluster_id, started_at],
        id=f"leak-check-{cluster_id}-{started_at}",
        name=f"Leak check cluster {cluster_id}",
        replace_existing=True,
    )


def _schedule_leak_check(cluster_id: int, started_at: int) -> None:
    """Schedule a one-shot leak detection check 30 minutes after an irrigation start.

    Skips silently when the scheduler is not running (test environments).
    Tests should call ``LeakDetectionService.check_after_irrigation`` directly.
    The job is in-memory; :func:`rearm_leak_checks` restores it after a restart.
    """
    try:
        from greenhouse_server.scheduler import scheduler

        if not scheduler.running:
            return
        _add_leak_check_job(cluster_id, started_at)
    except Exception:
        # Scheduling must never block irrigation
        logger.debug("Could not schedule leak check for cluster %d", cluster_id, exc_info=True)


def _rearm_from_events(repo: IrrigationRepository, now: int) -> "Iterator[None]":
    """Schedule a leak check for every recent auto start whose check never completed; yield once per job.

    A generator rather than a returned count, so the caller's tally keeps the jobs already added
    when the scan fails midway.
    """
    for irrigator in repo.list_all_irrigators():
        for event in repo.get_recent_events(irrigator.id, hours=LEAK_HOLD_HOURS):
            if event.action != EVENT_ACTION_START or event.triggered_by != TRIGGERED_BY_AUTO:
                continue
            if _leak_check_done(repo, irrigator.cluster_id, event.timestamp):
                continue
            due = event.timestamp + LEAK_CHECK_DELAY_SECONDS
            _add_leak_check_job(irrigator.cluster_id, event.timestamp, run_at=max(due, now))
            yield


def rearm_leak_checks() -> int:
    """Re-schedule leak checks lost to a restart; call once after the scheduler starts.

    Leak-check jobs live only in memory, so a restart inside the 30-min window
    after an auto start used to drop the check — and with it the leak hold.
    Scans auto ``start`` events from the last ``LEAK_HOLD_HOURS`` (older
    findings would be outside the hold horizon) and schedules every one whose
    check has not completed (see :func:`_leak_check_done`): at its normal due
    time if that is still ahead, otherwise immediately. Manual starts never
    get a leak check, so they are not re-armed. Idempotent across restarts.

    Returns:
        Number of leak checks scheduled (0 when the scheduler isn't running).
    """
    from greenhouse_server.scheduler import _app, scheduler

    if not scheduler.running or _app is None:
        return 0
    now = int(_time.time())
    scheduled = 0
    with read_session(_app) as session:
        try:
            repo = IrrigationRepository(session)
            for _ in _rearm_from_events(repo, now):
                scheduled += 1
        except Exception:
            logger.exception("Re-arming leak checks after restart failed")
    if scheduled:
        logger.info("Re-armed %d post-irrigation leak check(s) after restart", scheduled)
    return scheduled

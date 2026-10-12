"""Characterization of ``check_all_clusters`` transaction isolation (I-ii).

``IrrigationService.check_all_clusters`` commits each cluster's work as soon as
it finishes; a cluster whose check raises is rolled back *alone*, reported as
``action="error"`` and raises a ``check_failed`` alert that the next successful
check of that cluster resolves. Earlier clusters' real
``IrrigationEvent(action="start")`` rows survive a later crash (the cooldown
depends on them). The same holds when the scheduler's ``_check_job`` body
drives the loop instead of ``POST /api/v1/check``.

The failing cluster here raises **after** its own check ran to completion —
i.e. after it actuated the fake pump and wrote its start event, decision log
and activity row. The pipeline commits a successful start the moment the device
accepts it (S3), so the crash's rollback cannot erase that run: the start event,
the actuated decision log and the ``irrigated`` activity survive, and the 6 h
cooldown sees the pump that really ran.

Golden: ``tests/golden/orchestration/check_all.json``.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, NoReturn

import pytest
from sqlalchemy.orm import Session

from golden import FROZEN_TS, assert_golden_json
from greenhouse_server import scheduler
from greenhouse_server.services.irrigation import CHECK_FAILED_ALERT_CODE, IrrigationService
from server.conftest import run_core_job
from server.test_contract_pipeline import Pipeline

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import time_machine

    from greenhouse_server.services.irrigation import CheckResult


class Boom(RuntimeError):  # noqa: N818 — the name is in the pinned golden (repr in alert text)
    """The injected failure (a distinct type so the golden shows exactly where it came from)."""


@pytest.fixture
def three_dry_clusters(clean_env: Path, frozen_clock: time_machine.Traveller) -> Iterator[Pipeline]:
    """A pipeline with three dry, fully wired clusters, marked so only the check's writes count."""
    del clean_env, frozen_clock  # requested only for their side effects
    p = Pipeline()
    for name in ("Alpha", "Bravo", "Charlie"):
        p.seed_cluster(name, soil=35.0)
    p.mark()
    yield p
    p.close()


@pytest.fixture
def tx_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every ``Session.commit`` / ``Session.rollback`` in call order."""
    log: list[str] = []
    real_commit, real_rollback = Session.commit, Session.rollback

    def commit(self: Session) -> None:
        log.append("commit")
        real_commit(self)

    def rollback(self: Session) -> None:
        log.append("rollback")
        real_rollback(self)

    monkeypatch.setattr(Session, "commit", commit)
    monkeypatch.setattr(Session, "rollback", rollback)
    return log


def _crash_after_check(monkeypatch: pytest.MonkeyPatch, tx_log: list[str], failing: set[int]) -> dict[str, set[int]]:
    """Make ``check_cluster`` run fully, then raise for the cluster ids in ``failing``."""
    original = IrrigationService.check_cluster
    state = {"failing": failing}

    def flaky(self: IrrigationService, cluster_id: int) -> CheckResult:
        tx_log.append(f"check:{cluster_id}")
        result = original(self, cluster_id)
        if cluster_id in state["failing"]:
            msg = f"cluster {cluster_id} crashed after writing"
            raise Boom(msg)
        return result

    monkeypatch.setattr(IrrigationService, "check_cluster", flaky)
    return state


def _summary(p: Pipeline, tx_log: list[str]) -> dict[str, Any]:
    observed = p.observed()
    db = observed["db_writes"]
    return {
        "calls": observed["calls"],
        "transactions": list(tx_log),
        "start_events_by_irrigator": [
            (e["irrigator_id"], e["action"], e["triggered_by"]) for e in db["irrigation_events"]
        ],
        "decision_logs": [
            (r["cluster_id"], r["action"], r["primary_code"], r["actuated"]) for r in db["decision_logs"]
        ],
        "activity": [(a["entity_id"], a["code"]) for a in db["activity_events"]],
        "alerts": db["alerts"],
        "irrigator_adapter_calls": observed["irrigator_adapter_calls"],
        "notifications": observed["notifications"],
    }


def test_crash_after_writes_rolls_back_only_the_failing_cluster(
    three_dry_clusters: Pipeline, tx_log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """I-ii (1)+(3): Bravo crashes after actuating; Alpha's and Charlie's start events survive."""
    p = three_dry_clusters
    _crash_after_check(monkeypatch, tx_log, failing={2})
    resp = p.call("POST", "/api/v1/check")
    assert resp["status"] == HTTPStatus.OK
    by_id = {r["cluster_id"]: r for r in resp["response"]["results"]}
    assert [by_id[i]["action"] for i in (1, 2, 3)] == ["irrigated", "error", "irrigated"]
    assert by_id[2]["notes"] == "check failed: Boom('cluster 2 crashed after writing')"

    summary = _summary(p, tx_log)
    # S3: cluster 2's pump really ran, so its start was committed before the crash and survives.
    assert summary["start_events_by_irrigator"] == [(1, "start", "auto"), (2, "start", "auto"), (3, "start", "auto")]
    assert [(row[0], row[3]) for row in summary["decision_logs"]] == [(1, True), (2, True), (3, True)]
    assert [c[:2] for c in summary["irrigator_adapter_calls"]] == [["start", 1], ["start", 2], ["start", 3]]
    (alert,) = summary["alerts"]
    assert (alert["code"], alert["status"], alert["severity"], alert["cluster_id"]) == (
        CHECK_FAILED_ALERT_CODE,
        "open",
        "error",
        2,
    )
    # Each successful start commits on its own (S3), ahead of the per-cluster commit.
    assert summary["transactions"] == [
        "check:1",
        "commit",
        "commit",
        "check:2",
        "commit",
        "rollback",
        "commit",
        "check:3",
        "commit",
        "commit",
        "commit",
    ]
    assert_golden_json("orchestration/check_all_crash_after_writes.json", summary)


def test_crash_in_first_cluster_does_not_block_later_ones(
    three_dry_clusters: Pipeline, tx_log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """I-ii (2): a crash in the first cluster still lets the later clusters run and commit."""
    p = three_dry_clusters
    _crash_after_check(monkeypatch, tx_log, failing={1})
    resp = p.call("POST", "/api/v1/check")
    actions = [r["action"] for r in resp["response"]["results"]]
    assert actions == ["error", "irrigated", "irrigated"]
    summary = _summary(p, tx_log)
    assert summary["start_events_by_irrigator"] == [(1, "start", "auto"), (2, "start", "auto"), (3, "start", "auto")]
    assert [a["cluster_id"] for a in summary["alerts"]] == [1]


def test_check_failed_alert_lifecycle(
    three_dry_clusters: Pipeline, tx_log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next successful check resolves ``check_failed``; a later crash re-opens the same row."""
    p = three_dry_clusters
    state = _crash_after_check(monkeypatch, tx_log, failing={2})
    p.call("POST", "/api/v1/check")
    first = p.db_rows()["alerts"]
    assert [(a["status"], a["occurrence_count"]) for a in first] == [("open", 1)]

    state["failing"] = set()
    second = p.call("POST", "/api/v1/check")
    # Every cluster is in cooldown now — cluster 2 too: its crashed run's start event survived (S3).
    assert [r["action"] for r in second["response"]["results"]] == ["skip", "skip", "skip"]
    resolved = p.db_rows()["alerts"]
    assert [(a["status"], a["resolved_at"]) for a in resolved] == [("resolved", FROZEN_TS)]

    state["failing"] = {2}
    p.call("POST", "/api/v1/check")
    reopened = p.db_rows()["alerts"]
    assert [(a["id"], a["status"], a["occurrence_count"], a["resolved_at"]) for a in reopened] == [
        (first[0]["id"], "open", 2, None)
    ]
    assert_golden_json("orchestration/check_all_alert_lifecycle.json", _summary(p, tx_log))


def test_scheduler_check_job_isolates_clusters_the_same_way(
    three_dry_clusters: Pipeline, tx_log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """I-ii (4): the scheduler's ``_check_job`` body gives the same per-cluster isolation."""
    p = three_dry_clusters
    assert scheduler._app is p.app  # noqa: SLF001 — pins the module global init_scheduler rebinds per create_app
    # The job reads the registry from app.state, not from the request-level override.
    p.app.state.device_registry = p.wiring.registry
    _crash_after_check(monkeypatch, tx_log, failing={2})

    run_core_job(scheduler.CHECK_ALL_JOB_ID)

    summary = _summary(p, tx_log)
    assert summary["start_events_by_irrigator"] == [(1, "start", "auto"), (2, "start", "auto"), (3, "start", "auto")]
    assert [(a["code"], a["cluster_id"], a["status"]) for a in summary["alerts"]] == [
        (CHECK_FAILED_ALERT_CODE, 2, "open")
    ]
    # The job's own trailing commit replaces the route's.
    # Each successful start commits on its own (S3), ahead of the per-cluster commit.
    assert summary["transactions"] == [
        "check:1",
        "commit",
        "commit",
        "check:2",
        "commit",
        "rollback",
        "commit",
        "check:3",
        "commit",
        "commit",
        "commit",
    ]
    assert_golden_json("orchestration/check_all_scheduler_job.json", summary)


def test_scheduler_check_job_without_registry_reports_errors_but_commits(
    three_dry_clusters: Pipeline, tx_log: list[str]
) -> None:
    """With no registry on ``app.state`` (degraded mode) the job evaluates and logs, but cannot actuate."""
    p = three_dry_clusters
    assert getattr(p.app.state, "device_registry", None) is None
    run_core_job(scheduler.CHECK_ALL_JOB_ID)
    summary = _summary(p, tx_log)
    assert summary["start_events_by_irrigator"] == []
    assert summary["irrigator_adapter_calls"] == []
    assert [(r[0], r[1], r[3]) for r in summary["decision_logs"]] == [
        (1, "irrigate", False),
        (2, "irrigate", False),
        (3, "irrigate", False),
    ]
    assert summary["transactions"] == ["commit", "commit", "commit", "commit"]


def test_scheduler_check_job_swallows_a_total_failure(
    three_dry_clusters: Pipeline, tx_log: list[str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If ``check_all_clusters`` itself raises, the job rolls back, logs and returns (never raises)."""

    def explode(_self: IrrigationService) -> NoReturn:
        msg = "whole check exploded"
        raise Boom(msg)

    monkeypatch.setattr(IrrigationService, "check_all_clusters", explode)
    with caplog.at_level("ERROR", logger="greenhouse_server.scheduler"):
        run_core_job(scheduler.CHECK_ALL_JOB_ID)
    assert tx_log == ["rollback"]
    assert [r.getMessage() for r in caplog.records if r.name == "greenhouse_server.scheduler"] == ["Check job failed"]
    assert three_dry_clusters.db_rows()["decision_logs"] == []

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
and activity row — so "rolled back alone" is observable: those writes vanish
while the hardware call stays recorded (current behavior, pinned).

Golden: ``tests/golden/orchestration/check_all.json``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from golden import FROZEN_TS, assert_golden_json
from greenhouse_server.services.irrigation import CHECK_FAILED_ALERT_CODE, IrrigationService
from server.test_contract_pipeline import Pipeline


class Boom(RuntimeError):
    """The injected failure (a distinct type so the golden shows exactly where it came from)."""


@pytest.fixture
def three_dry_clusters(clean_env, frozen_clock):
    p = Pipeline()
    for name in ("Alpha", "Bravo", "Charlie"):
        p.seed_cluster(name, soil=35.0)
    p.mark()
    yield p
    p.close()


@pytest.fixture
def tx_log(monkeypatch):
    """Record every ``Session.commit`` / ``Session.rollback`` in call order."""
    log: list[str] = []
    real_commit, real_rollback = Session.commit, Session.rollback

    def commit(self):
        log.append("commit")
        return real_commit(self)

    def rollback(self):
        log.append("rollback")
        return real_rollback(self)

    monkeypatch.setattr(Session, "commit", commit)
    monkeypatch.setattr(Session, "rollback", rollback)
    return log


def _crash_after_check(monkeypatch, tx_log: list[str], failing: set[int]) -> dict:
    """Make ``check_cluster`` run fully, then raise for the cluster ids in ``failing``."""
    original = IrrigationService.check_cluster
    state = {"failing": failing}

    def flaky(self, cluster_id):
        tx_log.append(f"check:{cluster_id}")
        result = original(self, cluster_id)
        if cluster_id in state["failing"]:
            raise Boom(f"cluster {cluster_id} crashed after writing")
        return result

    monkeypatch.setattr(IrrigationService, "check_cluster", flaky)
    return state


def _summary(p: Pipeline, tx_log: list[str]) -> dict:
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


def test_crash_after_writes_rolls_back_only_the_failing_cluster(three_dry_clusters, tx_log, monkeypatch):
    """I-ii (1)+(3): Bravo crashes after actuating; Alpha's and Charlie's start events survive."""
    p = three_dry_clusters
    _crash_after_check(monkeypatch, tx_log, failing={2})
    resp = p.call("POST", "/api/v1/check")
    assert resp["status"] == 200
    by_id = {r["cluster_id"]: r for r in resp["response"]["results"]}
    assert [by_id[i]["action"] for i in (1, 2, 3)] == ["irrigated", "error", "irrigated"]
    assert by_id[2]["notes"] == "check failed: Boom('cluster 2 crashed after writing')"

    summary = _summary(p, tx_log)
    assert summary["start_events_by_irrigator"] == [(1, "start", "auto"), (3, "start", "auto")]
    assert [row[0] for row in summary["decision_logs"]] == [1, 3]
    # The pump for cluster 2 really ran; only its records were rolled back.
    assert [c[:2] for c in summary["irrigator_adapter_calls"]] == [["start", 1], ["start", 2], ["start", 3]]
    (alert,) = summary["alerts"]
    assert (alert["code"], alert["status"], alert["severity"], alert["cluster_id"]) == (
        CHECK_FAILED_ALERT_CODE,
        "open",
        "error",
        2,
    )
    assert summary["transactions"] == [
        "check:1",
        "commit",
        "check:2",
        "rollback",
        "commit",
        "check:3",
        "commit",
        "commit",
    ]
    assert_golden_json("orchestration/check_all_crash_after_writes.json", summary)


def test_crash_in_first_cluster_does_not_block_later_ones(three_dry_clusters, tx_log, monkeypatch):
    """I-ii (2): a crash in the first cluster still lets the later clusters run and commit."""
    p = three_dry_clusters
    _crash_after_check(monkeypatch, tx_log, failing={1})
    resp = p.call("POST", "/api/v1/check")
    actions = [r["action"] for r in resp["response"]["results"]]
    assert actions == ["error", "irrigated", "irrigated"]
    summary = _summary(p, tx_log)
    assert summary["start_events_by_irrigator"] == [(2, "start", "auto"), (3, "start", "auto")]
    assert [a["cluster_id"] for a in summary["alerts"]] == [1]


def test_check_failed_alert_lifecycle(three_dry_clusters, tx_log, monkeypatch):
    """The next successful check resolves ``check_failed``; a later crash re-opens the same row."""
    p = three_dry_clusters
    state = _crash_after_check(monkeypatch, tx_log, failing={2})
    p.call("POST", "/api/v1/check")
    first = p.db_rows()["alerts"]
    assert [(a["status"], a["occurrence_count"]) for a in first] == [("open", 1)]

    state["failing"] = set()
    second = p.call("POST", "/api/v1/check")
    # Clusters 1 and 3 are now in cooldown; cluster 2 finally irrigates.
    assert [r["action"] for r in second["response"]["results"]] == ["skip", "irrigated", "skip"]
    resolved = p.db_rows()["alerts"]
    assert [(a["status"], a["resolved_at"]) for a in resolved] == [("resolved", FROZEN_TS)]

    state["failing"] = {2}
    p.call("POST", "/api/v1/check")
    reopened = p.db_rows()["alerts"]
    assert [(a["id"], a["status"], a["occurrence_count"], a["resolved_at"]) for a in reopened] == [
        (first[0]["id"], "open", 2, None)
    ]
    assert_golden_json("orchestration/check_all_alert_lifecycle.json", _summary(p, tx_log))


def test_scheduler_check_job_isolates_clusters_the_same_way(three_dry_clusters, tx_log, monkeypatch):
    """I-ii (4): the scheduler's ``_check_job`` body gives the same per-cluster isolation."""
    from greenhouse_server import scheduler

    p = three_dry_clusters
    assert scheduler._app is p.app  # init_scheduler rebinds the module global per create_app
    # The job reads the registry from app.state, not from the request-level override.
    p.app.state.device_registry = p.wiring.registry
    _crash_after_check(monkeypatch, tx_log, failing={2})

    scheduler._check_job()

    summary = _summary(p, tx_log)
    assert summary["start_events_by_irrigator"] == [(1, "start", "auto"), (3, "start", "auto")]
    assert [(a["code"], a["cluster_id"], a["status"]) for a in summary["alerts"]] == [
        (CHECK_FAILED_ALERT_CODE, 2, "open")
    ]
    # The job's own trailing commit replaces the route's.
    assert summary["transactions"] == [
        "check:1",
        "commit",
        "check:2",
        "rollback",
        "commit",
        "check:3",
        "commit",
        "commit",
    ]
    assert_golden_json("orchestration/check_all_scheduler_job.json", summary)


def test_scheduler_check_job_without_registry_reports_errors_but_commits(three_dry_clusters, tx_log):
    """With no registry on ``app.state`` (degraded mode) the job evaluates and logs, but cannot actuate."""
    from greenhouse_server import scheduler

    p = three_dry_clusters
    assert getattr(p.app.state, "device_registry", None) is None
    scheduler._check_job()
    summary = _summary(p, tx_log)
    assert summary["start_events_by_irrigator"] == []
    assert summary["irrigator_adapter_calls"] == []
    assert [(r[0], r[1], r[3]) for r in summary["decision_logs"]] == [
        (1, "irrigate", False),
        (2, "irrigate", False),
        (3, "irrigate", False),
    ]
    assert summary["transactions"] == ["commit", "commit", "commit", "commit"]


def test_scheduler_check_job_swallows_a_total_failure(three_dry_clusters, tx_log, monkeypatch, caplog):
    """If ``check_all_clusters`` itself raises, the job rolls back, logs and returns (never raises)."""
    from greenhouse_server import scheduler

    def explode(self):
        raise Boom("whole check exploded")

    monkeypatch.setattr(IrrigationService, "check_all_clusters", explode)
    with caplog.at_level("ERROR", logger="greenhouse_server.scheduler"):
        scheduler._check_job()
    assert tx_log == ["rollback"]
    assert [r.getMessage() for r in caplog.records if r.name == "greenhouse_server.scheduler"] == ["Check job failed"]
    assert three_dry_clusters.db_rows()["decision_logs"] == []

"""Golden decision grid for ``IrrigationLogic.decide_for_cluster`` (characterization).

Pins, for ~1,400 declarative cases built by :mod:`engine_grid`, the full
``IrrigationDecision.model_dump(mode="json")`` (action, dosage, confidence,
reasons with codes **in order**, snapshot/stress/trends/weather) together with the
persisted ``decision_logs`` row.

Goldens live under ``tests/golden/engine/`` as JSON Lines: one case per line,
``{"case": <id>, ...record}`` with sorted keys (compact so every file stays far
below the repo's 512 KB large-file hook; large families are sharded by
:func:`engine_grid.golden_name`). On mismatch the test first reports which cases
and which top-level fields differ, then the byte-level diff.

This is what the code does *today* — a refactor of ``logic/`` or ``learning/``
must leave every golden byte-identical (``refactor/BRIEF.md`` rules 1 and 8).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict

import pytest

from engine_grid import build_cases, golden_name, run_case
from golden import GOLDEN_DIR, UPDATE_ENV_VAR, assert_golden

_SHARDS: dict[str, list] = defaultdict(list)
for _case in build_cases():
    _SHARDS[golden_name(_case)].append(_case)


def _render(records: dict[str, dict]) -> str:
    return "".join(
        json.dumps({"case": case_id, **record}, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for case_id, record in records.items()
    )


def _explain_mismatch(name: str, records: dict[str, dict]) -> None:
    """Fail with a per-case, per-field summary before the raw line diff."""
    path = GOLDEN_DIR / name
    if os.environ.get(UPDATE_ENV_VAR) == "1" or not path.exists():
        return
    expected = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        expected[row.pop("case")] = row
    problems = []
    for case_id in sorted(set(expected) | set(records)):
        if case_id not in records or case_id not in expected:
            problems.append(f"{case_id}: {'missing from run' if case_id not in records else 'new case'}")
            continue
        old, new = expected[case_id], records[case_id]
        if old != new:
            fields = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
            detail = []
            if "decision" in fields and old["decision"] and new["decision"]:
                detail = sorted(k for k in old["decision"] if old["decision"].get(k) != new["decision"].get(k))
            problems.append(f"{case_id}: {fields} {detail}")
    if problems:
        shown = "\n".join(problems[:40])
        more = f"\n… {len(problems) - 40} more" if len(problems) > 40 else ""
        pytest.fail(f"{len(problems)} case(s) differ from golden/{name}:\n{shown}{more}", pytrace=False)


def _assert_audit_invariants(case_id: str, record: dict) -> None:
    """Invariant #6/#10/#11 cross-checks that must hold for every grid case."""
    assert record["irrigation_events_written"] == 0, case_id  # the engine never writes events (#11)
    assert record["sensor_readings_unchanged"] is True, case_id  # raw rows never mutated (#10)
    if record["decision"] is None:  # unknown cluster → no evaluation, no row
        assert record["decision_logs"] == [], case_id
        return
    assert len(record["decision_logs"]) == 1, case_id  # exactly one row per evaluation (#6)
    row = record["decision_logs"][0]
    assert row["actuated"] is False, case_id  # the engine never claims actuation
    assert row["payload_equals_decision"] is True, case_id
    reasons = record["decision"]["reasons"]
    # A sensor-path decision can end with NO reasons (e.g. temperature-only data):
    # primary_code is then NULL and reason_text "no specific conditions".
    assert row["primary_code"] == (reasons[0]["code"] if reasons else None), case_id
    assert record["decision_log_id_matches"] is True, case_id


@pytest.mark.parametrize("name", sorted(_SHARDS))
def test_decision_grid_matches_golden(name, clean_env):
    records = {case.id: run_case(case) for case in _SHARDS[name]}

    for case_id, record in records.items():
        _assert_audit_invariants(case_id, record)
    _explain_mismatch(name, records)
    assert_golden(name, _render(records))


def test_grid_case_ids_are_unique_and_stable():
    cases = build_cases()

    assert len({c.id for c in cases}) == len(cases)
    assert 1000 <= len(cases) < 2000
    assert [c.id for c in build_cases()] == [c.id for c in cases]


def test_golden_files_cover_exactly_the_grid():
    """No orphaned golden files (a removed case must not leave a stale golden behind)."""
    on_disk = {p.relative_to(GOLDEN_DIR).as_posix() for p in (GOLDEN_DIR / "engine").rglob("*.jsonl")}

    assert on_disk == set(_SHARDS)


def test_run_case_is_deterministic(clean_env):
    """Same case twice → identical record (no hidden clock / ordering leaks)."""
    sample = build_cases("misc") + build_cases("learning")

    assert [run_case(c) for c in sample] == [run_case(c) for c in sample]

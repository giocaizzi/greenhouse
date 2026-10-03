#!/usr/bin/env python3
"""Gate 1 mutation campaign — prove the Phase-1 safety net fails when behavior changes.

A curated, reusable mutation runner. Each mutant is an exact source snippet plus its
replacement in one production file. The runner applies ONE mutant at a time, runs the
pytest target set that is meant to guard that code, records KILLED (tests failed) or
SURVIVED (tests stayed green), and always writes the original bytes back.

Why curated instead of mutmut: mutmut 3 keys its trampolines by file path
(``libs.greenhouse-core.greenhouse_core...``) while the tests import
``greenhouse_core...`` through pytest's ``pythonpath`` — it refuses to run against
this uv-workspace layout (see ``refactor/gate1/mutation.md``). Curated mutants are
also easier to triage: every one targets a real decision point (comparison,
boundary, pipeline order, side effect, user-visible string, ordering, default).

Usage (from the repo root)::

    uv run python refactor/gate1/mutate.py --list                 # show the catalogue
    uv run python refactor/gate1/mutate.py --baseline             # every group green on HEAD?
    uv run python refactor/gate1/mutate.py --only engine-         # run a subset (id prefix)
    uv run python refactor/gate1/mutate.py --area logic --resume  # skip ids already recorded
    uv run python refactor/gate1/mutate.py --report               # summary table from results

Per-WP mode (plan §0.5, M-pre / M-post) — operator-generated mutants for one module::

    uv run python refactor/gate1/mutate.py --targets                          # the mutation-list modules
    uv run python refactor/gate1/mutate.py --module logic/stress.py --list    # generated catalogue
    uv run python refactor/gate1/mutate.py --module logic/stress.py \
        --results refactor/wp-handoff/mutation/stress-pre.jsonl              # M-pre (default tests: target map)
    uv run python refactor/gate1/mutate.py --module logic/stress.py --function detect_stress_conditions \
        --tests "tests/test_logic.py ..." --sample 150 --seed 0              # narrowed / sampled run
    uv run python refactor/gate1/mutate.py --module logic/stress.py --summary --results post.jsonl \
        --compare pre.jsonl --map function-map.json                          # M-post verdict

Generated operators (target §11): comparison flip, ``and``<->``or``, +/-1 on numeric literals,
``True``<->``False``, statement deletion (-> ``pass``). Only code inside functions is mutated
(docstrings and annotations are skipped). Mutant **identity** = (operator, original snippet ->
replacement snippet, enclosing function qualname[, occurrence]); line numbers are never part of it,
so M-pre and M-post results match across refactors through a WP function map (JSON
``{"old.qualname": ["new.qualname", ...]}``). Mutation is in place; each file is restored with
``git checkout -- <file>`` in a ``finally`` (verified byte-for-byte) and the runner refuses to start
on a dirty worktree. Every pytest run uses ``PYTHONHASHSEED=0`` and the machine-wide test lock
``/tmp/greenhouse-tests.lock`` (``MUTATE_NO_LOCK=1`` disables it).

Results are appended to ``--results`` (JSON lines, default in ``$TMPDIR``) so an
interrupted campaign can be resumed. The production tree is verified clean
(``git diff --quiet -- libs/``) before and after the run.

Status meanings: KILLED (pytest exit 1), KILLED-ERROR (collection/internal error
caused by the mutant, exit 2/3), TIMEOUT (counted as killed), SURVIVED (exit 0),
INVALID (snippet not found / not unique / mutant does not compile — a catalogue
bug, never counted), EQUIVALENT (declared in the catalogue with a proof, excluded
from the denominator; still run so the claim is checked).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = "libs/greenhouse-core/greenhouse_core/"
SRV = "libs/greenhouse-server/greenhouse_server/"
CLI = "libs/greenhouse-cli/greenhouse_cli/"
TEST_LOCK = "/tmp/greenhouse-tests.lock"

# ── Test target sets ────────────────────────────────────────────────────────
# Each group = the contract goldens for the area + the existing tests that the
# baseline coverage map ties to the module. Paths are grouped by directory
# (core → devices → server → cli) to dodge the known "fixture 'client' not
# found" pytest quirk when server/core/server files are interleaved.

ENGINE_TESTS = [
    "tests/test_contract_decision_grid.py",
    "tests/test_invariants_engine.py",
    "tests/test_properties_logic.py",
    "tests/test_logic.py",
    "tests/test_engine_timing.py",
    "tests/test_leak_hold.py",
    "tests/test_vacation_rationing.py",
    "tests/test_cleaning.py",
    "tests/test_timing.py",
    "tests/test_learning.py",
    "tests/test_contract_constants.py",
]

GROUPS: dict[str, list[str]] = {
    "engine": ENGINE_TESTS,
    "logic": ENGINE_TESTS
    + [
        "tests/server/test_contract_raw_vs_clean.py",
        "tests/server/test_contract_pipeline.py",
    ],
    "learning": ENGINE_TESTS
    + [
        "tests/server/test_contract_raw_vs_clean.py",
        "tests/server/test_insights.py",
        "tests/server/test_forecast.py",
        "tests/server/test_plants_health.py",
        "tests/server/test_operations.py",
    ],
    "devices": [
        "tests/test_cloud.py",
        "tests/test_contract_sync.py",
        "tests/devices/",
        "tests/server/test_contract_health_monitor.py",
        "tests/server/test_contract_pump_watcher.py",
        "tests/server/test_contract_sync_service.py",
        "tests/server/test_health_monitor.py",
        "tests/server/test_pump_watcher.py",
        "tests/server/test_irrigators.py",
    ],
    "scheduler": [
        "tests/server/test_contract_scheduler.py",
        "tests/server/test_contract_scheduler_registry.py",
        "tests/server/test_contract_check_all.py",
        "tests/server/test_scheduler.py",
        "tests/server/test_scheduler_jobs.py",
        "tests/server/test_scheduler_pause.py",
        "tests/server/test_scheduler_settings.py",
        "tests/server/test_scheduler_shutdown.py",
        "tests/server/test_leak_rearm.py",
    ],
    "irrigation": [
        "tests/server/test_contract_pipeline.py",
        "tests/server/test_contract_check_all.py",
        "tests/server/test_contract_pump_watcher.py",
        "tests/server/test_contract_scheduler.py",
        "tests/server/test_operations.py",
        "tests/server/test_leak_rearm.py",
        "tests/server/test_leak_detection.py",
        "tests/server/test_notify.py",
        "tests/server/test_decisions.py",
        "tests/server/test_rate_limit.py",
        "tests/server/test_health_monitor.py",
    ],
    "leak": [
        "tests/test_leak_hold.py",
        "tests/server/test_leak_detection.py",
        "tests/server/test_leak_rearm.py",
        "tests/server/test_contract_pipeline.py",
        "tests/server/test_contract_scheduler.py",
    ],
    "manual": [
        "tests/server/test_contract_pipeline.py",
        "tests/server/test_rate_limit.py",
        "tests/server/test_irrigators.py",
        "tests/server/test_notify.py",
        "tests/server/test_contract_web_mutations.py",
    ],
    "sync": [
        "tests/test_contract_sync.py",
        "tests/server/test_contract_sync_service.py",
        "tests/server/test_contract_pipeline.py",
        "tests/server/test_sync_snapshot.py",
        "tests/server/test_contract_scheduler.py",
    ],
    "health": [
        "tests/server/test_contract_health_monitor.py",
        "tests/server/test_health_monitor.py",
        "tests/server/test_contract_scheduler.py",
        "tests/server/test_pump_watcher.py",
    ],
    "pump": [
        "tests/server/test_contract_pump_watcher.py",
        "tests/server/test_pump_watcher.py",
        "tests/server/test_contract_pipeline.py",
        "tests/server/test_irrigators.py",
    ],
    "repository": [
        "tests/test_contract_schema.py",
        "tests/test_contract_decision_grid.py",
        "tests/test_invariants_engine.py",
        "tests/test_leak_hold.py",
        "tests/test_vacation_rationing.py",
        "tests/test_logic.py",
        "tests/test_db.py",
        "tests/server/test_contract_pipeline.py",
        "tests/server/test_contract_web_html.py",
        "tests/server/test_alerts.py",
        "tests/server/test_activity.py",
        "tests/server/test_configs.py",
        "tests/server/test_decisions.py",
        "tests/server/test_clusters.py",
        "tests/server/test_sensors.py",
        "tests/server/test_vacation.py",
    ],
    "auth": [
        "tests/server/test_contract_auth.py",
        "tests/server/test_contract_mcp.py",
        "tests/server/test_contract_web_errors.py",
        "tests/server/test_auth.py",
        "tests/server/test_mcp.py",
    ],
    "web": [
        "tests/server/test_contract_openapi.py",
        "tests/server/test_contract_web_html.py",
        "tests/server/test_contract_web_mutations.py",
        "tests/server/test_contract_web_errors.py",
        "tests/server/test_web_filters.py",
        "tests/server/test_operations.py",
        "tests/server/test_alerts.py",
        "tests/server/test_vacation.py",
        "tests/server/test_scheduler.py",
    ],
    "cli": [
        "tests/cli/test_contract_help.py",
        "tests/cli/test_contract_json_output.py",
        "tests/cli/test_cli.py",
        "tests/cli/test_completeness.py",
    ],
    "tui": [
        "tests/cli/test_contract_tui.py",
        "tests/cli/test_contract_tui_actuation.py",
        "tests/cli/test_contract_tui_runtime.py",
        "tests/cli/test_contract_tui_screens.py",
        "tests/cli/test_tui.py",
    ],
}

# Gap-test files written after the first pass; appended to every group of the
# matching tree so the re-run proves the survivor is now killed.
GAP_TESTS = {
    "core": "tests/test_contract_mutation_gaps.py",
    "server": "tests/server/test_contract_mutation_gaps.py",
    "cli": "tests/cli/test_contract_mutation_gaps.py",
}


@dataclass
class Mutant:
    id: str
    file: str
    old: str
    new: str
    desc: str
    group: str
    area: str
    occurrence: int = 0  # 0 = snippet must be unique; N = replace the N-th match (1-based)
    equivalent: str | None = None  # proof that the mutant cannot change behavior
    offset: int | None = None  # generated mutants: exact character offset of ``old``
    operator: str | None = None  # generated mutants: operator name (identity part)
    qualname: str | None = None  # generated mutants: enclosing function (identity part)
    identity: str | None = None  # operator | original -> replacement | qualname[#n]


CATALOGUE: list[Mutant] = []


def add(area: str, group: str, file: str, rows: list[tuple]) -> None:
    """Register mutants: rows of (id-suffix, old, new, desc[, occurrence[, equivalent]])."""
    for row in rows:
        suffix, old, new, desc, *rest = row
        occurrence = rest[0] if rest else 0
        equivalent = rest[1] if len(rest) > 1 else None
        CATALOGUE.append(
            Mutant(
                id=f"{area}-{suffix}",
                file=file,
                old=old,
                new=new,
                desc=desc,
                group=group,
                area=area,
                occurrence=occurrence,
                equivalent=equivalent,
            )
        )


# The catalogue itself is at the end of this file (section "Catalogue").

# ── Runner ──────────────────────────────────────────────────────────────────


@dataclass
class Result:
    id: str
    area: str
    file: str
    line: int
    desc: str
    status: str
    exit_code: int | None
    seconds: float
    tail: str = ""
    equivalent: str | None = None
    tests: list[str] = field(default_factory=list)
    operator: str | None = None
    qualname: str | None = None
    original: str | None = None
    replacement: str | None = None
    identity: str | None = None


def _locate(src: str, m: Mutant) -> int:
    """Return the character offset of the snippet to replace (validated)."""
    if m.offset is not None:
        if src[m.offset : m.offset + len(m.old)] != m.old:
            raise ValueError("snippet not at its recorded offset (file changed since generation)")
        return m.offset
    count = src.count(m.old)
    if count == 0:
        raise ValueError("snippet not found")
    if m.occurrence == 0:
        if count != 1:
            raise ValueError(f"snippet not unique ({count} matches) — set occurrence")
        return src.index(m.old)
    pos = -1
    for _ in range(m.occurrence):
        pos = src.index(m.old, pos + 1)
    return pos


def _git_clean() -> bool:
    # MUTATE_NO_GIT=1: a plain (non-git) copy of the tree used to run a second
    # campaign shard in parallel; the runner restores original bytes either way.
    if os.environ.get("MUTATE_NO_GIT") == "1":
        return True
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True)
    return status.stdout.strip() == ""


def _restore(path: Path, original: bytes) -> None:
    """Put the file back: ``git checkout -- <file>`` (plan §0.5), verified byte-for-byte."""
    if os.environ.get("MUTATE_NO_GIT") != "1":
        subprocess.run(["git", "checkout", "--", str(path.relative_to(ROOT))], cwd=ROOT, check=True)
    if path.read_bytes() != original:
        path.write_bytes(original)


def _tests_for(m: Mutant, with_gaps: bool) -> list[str]:
    tests = list(GROUPS[m.group])
    if with_gaps:
        tree = "cli" if m.group in ("cli", "tui") else None
        extra = []
        for kind, path in GAP_TESTS.items():
            if (ROOT / path).exists() and (tree is None or kind == tree):
                extra.append(path)
        # keep directory grouping: core gaps first, server gaps after server files
        core = [p for p in extra if p.startswith("tests/test_")]
        rest = [p for p in extra if not p.startswith("tests/test_")]
        # --gaps-only: prove the new gap tests alone kill the mutant (fast re-run).
        tests = core + rest if os.environ.get("MUTATE_GAPS_ONLY") == "1" else core + tests + rest
    return [t for t in dict.fromkeys(tests) if (ROOT / t).exists()]


def run_pytest(tests: list[str], workers: int, timeout: int) -> tuple[int | None, str, float]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
    cmd = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "--no-header", "-o", "addopts="]
    if os.environ.get("MUTATE_NO_LOCK") != "1":
        cmd = ["flock", TEST_LOCK, *cmd]
    if workers:
        cmd += ["-p", "xdist", "-n", str(workers)]
    cmd += tests
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT", time.monotonic() - t0
    out = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, "\n".join(out[-6:]), time.monotonic() - t0


def run_mutant(m: Mutant, workers: int, timeout: int, with_gaps: bool) -> Result:
    path = ROOT / m.file
    original = path.read_bytes()
    src = original.decode()
    try:
        pos = _locate(src, m)
    except ValueError as exc:
        return Result(m.id, m.area, m.file, 0, m.desc, "INVALID", None, 0.0, str(exc), m.equivalent)
    line = src.count("\n", 0, pos) + 1
    mutated = src[:pos] + m.new + src[pos + len(m.old) :]
    try:
        if m.file.endswith(".py"):
            compile(mutated, str(path), "exec")
    except SyntaxError as exc:
        return Result(m.id, m.area, m.file, line, m.desc, "INVALID", None, 0.0, f"syntax: {exc}", m.equivalent)
    tests = m_tests if (m_tests := _TEST_OVERRIDE.get(m.id)) else _tests_for(m, with_gaps)
    try:
        path.write_text(mutated)
        code, tail, secs = run_pytest(tests, workers, timeout)
    finally:
        _restore(path, original)
    status = classify(code, tail)
    operator = m.operator or "curated"
    qualname = m.qualname or (enclosing_qualname(src, line) if m.file.endswith(".py") else None)
    return Result(
        m.id,
        m.area,
        m.file,
        line,
        m.desc,
        status,
        code,
        round(secs, 1),
        tail,
        m.equivalent,
        tests,
        operator=operator,
        qualname=qualname,
        original=_norm(m.old),
        replacement=_norm(m.new),
        identity=m.identity or _identity(operator, m.old, m.new, qualname),
    )


def classify(code: int | None, tail: str) -> str:
    """Map a pytest exit code to a mutant status.

    With ``-x`` under xdist a real test failure ends the session as
    ``Interrupted`` (exit 2), so exit 2 counts as KILLED when the summary line
    reports failed tests; any other exit 2/3 is a collection/internal error.
    """
    if code is None:
        return "TIMEOUT"
    if code == 0:
        return "SURVIVED"
    if code == 1 or (code == 2 and re.search(r"\b\d+ failed\b", tail)):
        return "KILLED"
    if code in (2, 3):
        return "KILLED-ERROR"
    return f"ERROR({code})"


def load_results(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if path.exists():
        for raw in path.read_text().splitlines():
            if raw.strip():
                rec = json.loads(raw)
                if rec["status"] != "INVALID" and (rec["exit_code"] is not None or rec["status"] == "TIMEOUT"):
                    rec["status"] = classify(rec["exit_code"], rec["tail"])
                # Equivalence proofs live in the catalogue; a proof added after
                # the run still applies to the recorded result.
                if rec["id"] in _BY_ID:
                    rec["equivalent"] = _BY_ID[rec["id"]].equivalent
                out[rec["id"]] = rec  # last record per id wins (re-runs)
    return out


KILLED_STATES = ("KILLED", "KILLED-ERROR", "TIMEOUT")


def report(results: dict[str, dict], after: dict[str, dict] | None = None) -> str:
    """Per-area markdown table. ``after`` = re-run of survivors with the gap tests.

    A mutant declared equivalent is excluded from the denominator only while it
    survives; if the suite kills it the equivalence claim was wrong and it counts
    as killed.
    """
    after = after or {}
    by_area: dict[str, list[dict]] = {}
    for rec in results.values():
        by_area.setdefault(rec["area"], []).append(rec)
    head = (
        "| area | mutants | killed | survived | equivalent | invalid | score | survived after gap tests | score after |"
    )
    lines = [head, "|---|---|---|---|---|---|---|---|---|"]
    tot = {"k": 0, "s": 0, "s2": 0}
    for area in sorted(by_area):
        recs = by_area[area]
        killed = sum(1 for r in recs if r["status"] in KILLED_STATES)
        equiv = sum(1 for r in recs if r["equivalent"] and r["status"] == "SURVIVED")
        invalid = sum(1 for r in recs if r["status"] == "INVALID")
        surv = [r for r in recs if r["status"] == "SURVIVED" and not r["equivalent"]]
        surv2 = [r for r in surv if after.get(r["id"], {}).get("status") not in KILLED_STATES]
        denom = killed + len(surv)
        tot["k"] += killed
        tot["s"] += len(surv)
        tot["s2"] += len(surv2)
        score = f"{100 * killed / denom:.0f}%" if denom else "-"
        score2 = f"{100 * (denom - len(surv2)) / denom:.0f}%" if denom else "-"
        lines.append(
            f"| {area} | {len(recs)} | {killed} | {len(surv)} | {equiv} | {invalid} | {score} | {len(surv2)} | {score2} |"
        )
    denom = tot["k"] + tot["s"]
    if denom:
        lines.append(
            f"| **total** | {sum(len(v) for v in by_area.values())} | {tot['k']} | {tot['s']} | | | "
            f"**{100 * tot['k'] / denom:.1f}%** | {tot['s2']} | **{100 * (denom - tot['s2']) / denom:.1f}%** |"
        )
    return "\n".join(lines)


# ── Per-WP mode: operator-generated mutants (plan §0.5, target §11) ─────────

# The decision-critical modules on the mutation list (plan §0.5, incl. Rev 2 core sync.py),
# each with the test group used when ``--tests`` is not given. ``plan-engine`` is the
# plan's $ENGINE alias; WPs normally pass their own subset with ``--tests``.
PLAN_ENGINE_TESTS = [
    "tests/test_contract_decision_grid.py",
    "tests/test_engine_timing.py",
    "tests/test_invariants_engine.py",
    "tests/test_leak_hold.py",
    "tests/test_logic.py",
    "tests/test_properties_logic.py",
    "tests/test_timing.py",
    "tests/test_vacation_rationing.py",
    "tests/server/test_contract_check_all.py",
    "tests/server/test_contract_pipeline.py",
]
GROUPS["plan-engine"] = PLAN_ENGINE_TESTS

MUTATION_TARGETS: dict[str, str] = {
    CORE + "logic/engine.py": "plan-engine",
    CORE + "logic/stress.py": "plan-engine",
    CORE + "logic/trends.py": "plan-engine",
    CORE + "logic/sensors.py": "plan-engine",
    CORE + "logic/fallback.py": "plan-engine",
    CORE + "learning/issues.py": "learning",
    CORE + "sync.py": "devices",
    SRV + "services/irrigation.py": "irrigation",
    SRV + "services/leak.py": "leak",
    SRV + "services/pump_watcher.py": "pump",
    SRV + "scheduler.py": "scheduler",
}

# mutant id -> explicit test list (set by --tests for generated runs)
_TEST_OVERRIDE: dict[str, list[str]] = {}

_CMP_FLIP: dict[type, tuple[type, str, str]] = {
    # op -> (replacement op, regex matching the original token in the gap, replacement token)
    ast.Lt: (ast.LtE, r"(?<![<>=!])<(?![<=])", "<="),
    ast.LtE: (ast.Lt, r"<=", "<"),
    ast.Gt: (ast.GtE, r"(?<![<>=!-])>(?![>=])", ">="),
    ast.GtE: (ast.Gt, r">=", ">"),
    ast.Eq: (ast.NotEq, r"==", "!="),
    ast.NotEq: (ast.Eq, r"!=", "=="),
    ast.Is: (ast.IsNot, r"\bis\b(?!\s+not\b)", "is not"),
    ast.IsNot: (ast.Is, r"\bis\s+not\b", "is"),
    ast.In: (ast.NotIn, r"(?<!\bnot\s)\bin\b", "not in"),
    ast.NotIn: (ast.In, r"\bnot\s+in\b", "in"),
}
_DELETABLE = (ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Return, ast.Raise)


def _norm(snippet: str) -> str:
    return " ".join(snippet.split())


def _identity(operator: str, old: str, new: str, qualname: str | None) -> str:
    return f"{operator} | {_norm(old)} -> {_norm(new)} | {qualname or '<module>'}"


def _function_spans(tree: ast.AST) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qual = f"{prefix}{child.name}"
                spans.append((qual, child.lineno, child.end_lineno or child.lineno))
                walk(child, f"{qual}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return spans


def enclosing_qualname(src: str, line: int) -> str | None:
    """Innermost function containing ``line`` (None at module level)."""
    best: tuple[str, int, int] | None = None
    for span in _function_spans(ast.parse(src)):
        if span[1] <= line <= span[2] and (best is None or span[1] >= best[1]):
            best = span
    return best[0] if best else None


class _Source:
    """Character offsets for ast (line, utf-8 byte column) positions."""

    def __init__(self, src: str) -> None:
        self.src = src
        self.lines = src.splitlines(keepends=True)
        self.starts = [0]
        for text in self.lines:
            self.starts.append(self.starts[-1] + len(text))

    def offset(self, lineno: int, col: int) -> int:
        line = self.lines[lineno - 1]
        return self.starts[lineno - 1] + len(line.encode()[:col].decode())

    def span(self, node: ast.AST) -> tuple[int, int]:
        start = self.offset(node.lineno, node.col_offset)  # type: ignore[attr-defined]
        end = self.offset(node.end_lineno, node.end_col_offset)  # type: ignore[attr-defined]
        return start, end


def _skip_ids(func: ast.AST) -> set[int]:
    """Node ids never mutated: docstrings, annotations, nested defs' signatures."""
    skip: set[int] = set()
    for node in ast.walk(func):
        body = getattr(node, "body", None)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                skip.update(id(n) for n in ast.walk(first))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for part in (node.args, node.returns, *node.decorator_list):
                if part is not None:
                    skip.update(id(n) for n in ast.walk(part))
        if isinstance(node, ast.AnnAssign):
            skip.update(id(n) for n in ast.walk(node.annotation))
        if isinstance(node, ast.arg) and node.annotation is not None:
            skip.update(id(n) for n in ast.walk(node.annotation))
    return skip


def _gap_mutant(text: str, pattern: str, token: str) -> str | None:
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        return None
    m = matches[0]
    return text[: m.start()] + token + text[m.end() :]


def _compare_mutants(node: ast.Compare, source: _Source) -> list[tuple[str, int, str, str]]:
    out = []
    start, end = source.span(node)
    operands = [node.left, *node.comparators]
    for i, op in enumerate(node.ops):
        flip = _CMP_FLIP.get(type(op))
        if flip is None:
            continue
        g0 = source.span(operands[i])[1]
        g1 = source.span(operands[i + 1])[0]
        new_gap = _gap_mutant(source.src[g0:g1], flip[1], flip[2])
        if new_gap is None:
            continue
        old = source.src[start:end]
        new = source.src[start:g0] + new_gap + source.src[g1:end]
        out.append(("cmp-flip", start, old, new))
    return out


def _boolop_mutants(node: ast.BoolOp, source: _Source) -> list[tuple[str, int, str, str]]:
    start, end = source.span(node)
    src_from, to = ("and", "or") if isinstance(node.op, ast.And) else ("or", "and")
    pieces, cursor = [], start
    for left, right in zip(node.values, node.values[1:], strict=False):
        g0, g1 = source.span(left)[1], source.span(right)[0]
        new_gap = _gap_mutant(source.src[g0:g1], rf"\b{src_from}\b", to)
        if new_gap is None:
            return []
        pieces += [source.src[cursor:g0], new_gap]
        cursor = g1
    pieces.append(source.src[cursor:end])
    return [("bool-flip", start, source.src[start:end], "".join(pieces))]


def _constant_mutants(node: ast.Constant, source: _Source, context: ast.AST) -> list[tuple[str, int, str, str]]:
    """Flip a bool / shift a number by one; the snippet is the enclosing expression, for a stable identity."""
    if isinstance(node.value, bool):
        replacements = [("bool-const", "False" if node.value else "True")]
    elif isinstance(node.value, int | float):
        replacements = [("num-plus1", repr(node.value + 1)), ("num-minus1", repr(node.value - 1))]
    else:
        return []
    c_start, c_end = source.span(node)
    start, end = source.span(context)
    old = source.src[start:end]
    return [(op, start, old, source.src[start:c_start] + new + source.src[c_end:end]) for op, new in replacements]


def _context(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST:
    """Nearest enclosing expression other than a unary sign (or the statement) — the constant's context."""
    current = parents.get(id(node), node)
    while isinstance(current, ast.UnaryOp | ast.keyword) or not isinstance(current, ast.expr | ast.stmt):
        nxt = parents.get(id(current))
        if nxt is None:
            break
        current = nxt
    if isinstance(current, ast.stmt) and not isinstance(current, _DELETABLE):
        return node  # e.g. a compound statement header: keep the bare literal
    return current


def _statement_mutants(node: ast.stmt, source: _Source) -> list[tuple[str, int, str, str]]:
    start, end = source.span(node)
    rest = source.src[end:].split("\n", 1)[0].strip()
    if rest and not rest.startswith("#"):
        return []  # another statement shares the line (`;`)
    return [("stmt-delete", start, source.src[start:end], "pass")]


def generate_mutants(file: str) -> list[Mutant]:
    """Operator-generated mutants for every function body in ``file`` (repo-relative)."""
    src = (ROOT / file).read_text()
    source = _Source(src)
    tree = ast.parse(src)
    stem = Path(file).stem
    raw: list[tuple[str, int, str, str, str]] = []
    for qual, func in _iter_functions(tree):
        skip = _skip_ids(func)
        parents = {id(child): parent for parent in ast.walk(func) for child in ast.iter_child_nodes(parent)}
        nested = {
            id(n)
            for child in ast.walk(func)
            if child is not func and isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            for n in ast.walk(child)
        }
        for node in ast.walk(func):
            if node is func or id(node) in skip or id(node) in nested:
                continue
            found: list[tuple[str, int, str, str]] = []
            if isinstance(node, ast.Compare):
                found = _compare_mutants(node, source)
            elif isinstance(node, ast.BoolOp):
                found = _boolop_mutants(node, source)
            elif isinstance(node, ast.Constant):
                found = _constant_mutants(node, source, _context(node, parents))
            elif isinstance(node, _DELETABLE):
                found = _statement_mutants(node, source)
            raw += [(op, pos, old, new, qual) for op, pos, old, new in found]
    raw.sort(key=lambda r: (r[1], r[0], r[3]))
    mutants: list[Mutant] = []
    seen: dict[str, int] = {}
    for op, pos, old, new, qual in raw:
        ident = _identity(op, old, new, qual)
        seen[ident] = seen.get(ident, 0) + 1
        if seen[ident] > 1:
            ident = f"{ident}#{seen[ident]}"
        mutants.append(
            Mutant(
                id=f"{stem}:{qual}:{op}:{len(mutants) + 1:03d}",
                file=file,
                old=old,
                new=new,
                desc=f"{op}: {_norm(old)[:60]} -> {_norm(new)[:40]}",
                group=MUTATION_TARGETS.get(file, "engine"),
                area=f"gen-{stem}",
                offset=pos,
                operator=op,
                qualname=qual,
                identity=ident,
            )
        )
    return mutants


def _iter_functions(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    out: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qual = f"{prefix}{child.name}"
                out.append((qual, child))
                walk(child, f"{qual}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def resolve_module(spec: str) -> str:
    """Accept ``logic/stress.py``, ``greenhouse_core/logic/stress.py``, a repo path or a dotted name."""
    candidates = [spec]
    if not spec.endswith(".py"):
        candidates.append(spec.replace(".", "/") + ".py")
    found = []
    for cand in candidates:
        cand = cand.lstrip("./")
        for base in ("", "libs/greenhouse-core/", "libs/greenhouse-server/", "libs/greenhouse-cli/", CORE, SRV, CLI):
            if (ROOT / base / cand).is_file():
                found.append((Path(base) / cand).as_posix())
    found = sorted(set(found))
    if len(found) != 1:
        raise SystemExit(f"--module {spec!r}: {'no match' if not found else 'ambiguous: ' + ', '.join(found)}")
    return found[0]


def _in_lines(m: Mutant, src: str, ranges: list[tuple[int, int]]) -> bool:
    line = src.count("\n", 0, m.offset or 0) + 1
    return any(a <= line <= b for a, b in ranges)


def select_generated(args: argparse.Namespace) -> list[Mutant]:
    file = resolve_module(args.module)
    mutants = generate_mutants(file)
    if args.function:
        mutants = [
            m for m in mutants if any(m.qualname == f or (m.qualname or "").startswith(f + ".") for f in args.function)
        ]
    if args.lines:
        ranges = [tuple(int(x) for x in r.split("-", 1)) if "-" in r else (int(r), int(r)) for r in args.lines]
        src = (ROOT / file).read_text()
        mutants = [m for m in mutants if _in_lines(m, src, ranges)]  # type: ignore[arg-type]
    if args.operator:
        mutants = [m for m in mutants if m.operator in args.operator]
    if args.sample and len(mutants) > args.sample:
        chosen = set(random.Random(args.seed).sample(range(len(mutants)), args.sample))
        mutants = [m for i, m in enumerate(mutants) if i in chosen]
    if args.tests:
        tests = [t for chunk in args.tests for t in chunk.split()]
        for m in mutants:
            _TEST_OVERRIDE[m.id] = tests
    return mutants


def _translate(identity: str, fmap: dict[str, list[str]]) -> set[str]:
    """All identities an M-pre identity may have after the WP's function moves."""
    head, _, qual = identity.rpartition(" | ")
    qual, hash_, occ = qual.partition("#")
    targets = fmap.get(qual, [qual])
    return {f"{head} | {t}{hash_}{occ}" for t in targets}


def summarize_generated(
    results: dict[str, dict], compare: dict[str, dict] | None, fmap: dict[str, list[str]]
) -> tuple[str, bool]:
    """Killed/survived per identity; with ``compare`` (M-pre results) also the M-post verdict."""
    recs = [r for r in results.values() if r.get("identity")]
    killed = [r for r in recs if r["status"] in KILLED_STATES]
    survived = [r for r in recs if r["status"] == "SURVIVED"]
    invalid = [r for r in recs if r["status"] == "INVALID"]
    denom = len(killed) + len(survived)
    rate = 100 * len(killed) / denom if denom else 0.0
    secs = sum(r.get("seconds") or 0 for r in recs)
    lines = [
        f"mutants {len(recs)} | killed {len(killed)} | survived {len(survived)} | invalid {len(invalid)} | "
        f"kill rate {rate:.1f}% | test time {secs / 60:.1f} min",
    ]
    lines += [f"  SURVIVED {r['identity']}" for r in sorted(survived, key=lambda r: r["identity"])]
    ok = True
    if compare is not None:
        post = {r["identity"]: r for r in recs}
        regressions, unmatched = [], []
        for pre in compare.values():
            if not pre.get("identity") or pre["status"] not in KILLED_STATES:
                continue
            matches = [post[i] for i in _translate(pre["identity"], fmap) if i in post]
            if not matches:
                unmatched.append(pre["identity"])
            elif any(m["status"] == "SURVIVED" for m in matches):
                regressions.append(pre["identity"])
        ok = rate >= 75.0 and not regressions
        lines.append(
            f"M-post: kill rate {'OK' if rate >= 75.0 else 'BELOW 75%'}; killed-in-pre now surviving: {len(regressions)}"
        )
        lines += [f"  REGRESSION {i}" for i in regressions]
        lines.append(
            f"  (killed-in-pre identities with no post counterpart — code removed/rewritten: {len(unmatched)})"
        )
        lines += [f"  UNMATCHED {i}" for i in unmatched]
    return "\n".join(lines), ok


def run_generated(args: argparse.Namespace) -> int:
    mutants = select_generated(args)
    if args.list:
        for m in mutants:
            print(f"{m.id:58} {m.identity}")
        print(f"{len(mutants)} mutants")
        return 0
    if args.summary:
        compare = load_results(args.compare) if args.compare else None
        fmap = json.loads(args.map.read_text()) if args.map else {}
        text, ok = summarize_generated(load_results(args.results), compare, fmap)
        print(text)
        return 0 if ok else 1
    if not _git_clean():
        print("refusing to run: the worktree is dirty (git status --porcelain is not empty)", file=sys.stderr)
        return 2
    if not mutants:
        print("no mutants selected")
        return 0
    tests = _TEST_OVERRIDE.get(mutants[0].id) or GROUPS[mutants[0].group]
    if not args.skip_baseline:
        code, tail, secs = run_pytest(tests, args.workers, args.timeout)
        print(f"baseline exit={code} {secs:.1f}s {tail.splitlines()[-1] if tail else ''}", flush=True)
        if code != 0:
            print("refusing to run: the test set is not green on unmutated code", file=sys.stderr)
            return 2
    done = load_results(args.results) if args.resume else {}
    t0 = time.monotonic()
    with args.results.open("a") as fh:
        for i, m in enumerate(mutants, 1):
            if m.id in done:
                continue
            res = run_mutant(m, args.workers, args.timeout, with_gaps=False)
            fh.write(json.dumps(asdict(res)) + "\n")
            fh.flush()
            print(f"[{i}/{len(mutants)}] {res.status:12} {res.seconds:6.1f}s {m.identity}", flush=True)
    print(f"wall time {(time.monotonic() - t0) / 60:.1f} min")
    text, _ = summarize_generated(load_results(args.results), None, {})
    print(text)
    clean = _git_clean()
    print("worktree clean:", clean)
    return 0 if clean else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="print the catalogue and exit")
    ap.add_argument("--check", action="store_true", help="validate every snippet applies and compiles")
    ap.add_argument("--baseline", action="store_true", help="run every used group on unmutated code")
    ap.add_argument("--report", action="store_true", help="print the summary table from the results file")
    ap.add_argument("--after", type=Path, help="results of the survivor re-run with --with-gaps (for --report)")
    ap.add_argument("--only", action="append", default=[], help="mutant id prefix (repeatable)")
    ap.add_argument("--area", action="append", default=[], help="area name (repeatable)")
    ap.add_argument("--resume", action="store_true", help="skip ids already in the results file")
    ap.add_argument("--with-gaps", action="store_true", help="also run the gap-test files")
    ap.add_argument("--workers", type=int, default=2, help="pytest-xdist workers (0 = in-process)")
    ap.add_argument("--timeout", type=int, default=1200, help="per-mutant pytest timeout (s)")
    ap.add_argument(
        "--results",
        type=Path,
        default=Path(tempfile.gettempdir()) / "greenhouse-mutation-results.jsonl",
        help="JSON-lines results file",
    )
    gen = ap.add_argument_group("per-WP mode (generated mutants for one module)")
    gen.add_argument("--targets", action="store_true", help="print the mutation-list modules and their test groups")
    gen.add_argument("--module", help="module to mutate (e.g. logic/stress.py, greenhouse_core.sync)")
    gen.add_argument("--function", action="append", default=[], help="enclosing qualname (repeatable; prefix.)")
    gen.add_argument("--lines", action="append", default=[], help="line range A-B (repeatable)")
    gen.add_argument("--operator", action="append", default=[], help="operator name (repeatable)")
    gen.add_argument("--tests", action="append", default=[], help="explicit test paths (space-separated, repeatable)")
    gen.add_argument("--sample", type=int, default=0, help="seeded sample size (budget fallback, plan: 150)")
    gen.add_argument("--seed", type=int, default=0, help="sample seed")
    gen.add_argument("--skip-baseline", action="store_true", help="do not check the test set on unmutated code")
    gen.add_argument("--summary", action="store_true", help="killed/survived per identity from --results")
    gen.add_argument("--compare", type=Path, help="M-pre results for the M-post verdict (with --summary)")
    gen.add_argument("--map", type=Path, help='WP function map JSON {"old.qualname": ["new.qualname", ...]}')
    args = ap.parse_args()

    if args.targets:
        for file, group in MUTATION_TARGETS.items():
            print(f"{file:60} {group:12} {' '.join(GROUPS[group])}")
        return 0
    if args.module:
        return run_generated(args)

    selected = [
        m
        for m in CATALOGUE
        if (not args.only or any(m.id.startswith(p) for p in args.only)) and (not args.area or m.area in args.area)
    ]
    ids = [m.id for m in CATALOGUE]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        print(f"duplicate mutant ids: {sorted(dupes)}", file=sys.stderr)
        return 2

    if args.list:
        for m in selected:
            print(f"{m.id:28} {m.group:11} {m.file.split('/', 2)[-1]:55} {m.desc}")
        print(f"{len(selected)} mutants")
        return 0
    if args.check:
        bad = 0
        for m in selected:
            src = (ROOT / m.file).read_text()
            try:
                pos = _locate(src, m)
                if m.file.endswith(".py"):
                    compile(src[:pos] + m.new + src[pos + len(m.old) :], m.file, "exec")
            except (ValueError, SyntaxError) as exc:
                bad += 1
                print(f"INVALID {m.id}: {exc}")
        print(f"{len(selected) - bad}/{len(selected)} mutants apply cleanly")
        return 1 if bad else 0
    if args.report:
        after = load_results(args.after) if args.after else None
        print(report(load_results(args.results), after))
        return 0
    if args.baseline:
        groups = sorted({m.group for m in selected})
        for g in groups:
            code, tail, secs = run_pytest(GROUPS[g], args.workers, args.timeout)
            print(f"{g:12} exit={code} {secs:6.1f}s  {tail.splitlines()[-1] if tail else ''}", flush=True)
        return 0

    if not _git_clean():
        print("refusing to run: the worktree is dirty (git status --porcelain is not empty)", file=sys.stderr)
        return 2
    done = load_results(args.results) if args.resume else {}
    with args.results.open("a") as fh:
        for i, m in enumerate(selected, 1):
            if m.id in done:
                continue
            res = run_mutant(m, args.workers, args.timeout, args.with_gaps)
            fh.write(json.dumps(asdict(res)) + "\n")
            fh.flush()
            print(f"[{i}/{len(selected)}] {res.status:12} {res.seconds:6.1f}s {m.id:28} {m.desc}", flush=True)
            if res.status == "INVALID":
                print(f"    -> {res.tail}", flush=True)
    clean = _git_clean()
    print("worktree clean:", clean)
    return 0 if clean else 1


# ── Catalogue ───────────────────────────────────────────────────────────────
# Rows: (id-suffix, exact original snippet, replacement, description
#        [, occurrence (1-based, 0 = must be unique) [, equivalence proof]]).

_E = CORE + "logic/engine.py"
_LEAK_BLOCK = (
    "        leak_hold = self._enforce_leak_hold(cluster_id, evaluated_at)\n"
    "        if leak_hold is not None:\n"
    "            if persist:\n"
    "                self._persist(leak_hold, triggered_by)\n"
    "            return leak_hold\n"
)
_COOL_BLOCK = (
    "        cooldown = self._enforce_cooldown(cluster_id, evaluated_at)\n"
    "        if cooldown is not None:\n"
    "            if persist:\n"
    "                self._persist(cooldown, triggered_by)\n"
    "            return cooldown\n"
)
_WW = "        if _apply_water_warning_rule(decision):\n            return _finalize(decision)\n"
_CS = "        if _apply_critical_stress_rule(decision):\n            return _finalize(decision)\n"
_WIN = (
    "        window_skip = self._apply_window_rule(cluster, cluster_id, evaluated_at, decision)\n"
    "        if window_skip is not None:\n"
    "            return _finalize(window_skip)\n"
)
add(
    "engine",
    "engine",
    _E,
    [
        (
            "01",
            "        if not cluster:\n            return None\n",
            "        if not cluster:\n            pass\n",
            "missing cluster no longer returns None",
        ),
        (
            "02",
            "                confidence=0.0,\n                code=TriggerCode.NO_PLANTS,",
            "                confidence=0.5,\n                code=TriggerCode.NO_PLANTS,",
            "NO_PLANTS confidence 0.0 -> 0.5",
        ),
        (
            "03",
            "            if persist:\n                self._persist(decision, triggered_by)\n            return decision\n\n        # Safety",
            "            return decision\n\n        # Safety",
            "NO_PLANTS decision no longer persisted",
        ),
        (
            "04",
            "\n" + _LEAK_BLOCK + "\n" + _COOL_BLOCK,
            "\n" + _COOL_BLOCK + "\n" + _LEAK_BLOCK,
            "pipeline order: cooldown gate before leak hold",
        ),
        (
            "05",
            "            if persist:\n                self._persist(leak_hold, triggered_by)\n",
            "",
            "leak-hold decision no longer persisted",
        ),
        (
            "06",
            "if quiet_window is not None and not bypass_quiet_hours:",
            "if quiet_window is not None:",
            "quiet hours ignore bypass (force)",
        ),
        (
            "07",
            'message=(f"quiet hours active ({quiet_window[0]:02d}:00',
            'message=(f"quiet hours active ({quiet_window[0]}:00',
            "quiet-hours message drops zero padding",
        ),
        (
            "08",
            "            if quiet_window is not None and bypass_quiet_hours:",
            "            if False:",
            "manual override reason never appended",
        ),
        (
            "09",
            "            return _finalize(weather_skip)",
            "            return weather_skip",
            "weather skip bypasses _finalize (not persisted)",
        ),
        (
            "10",
            "get_recent_sensor_data(self.db, cluster_id, hours=24)",
            "get_recent_sensor_data(self.db, cluster_id, hours=48)",
            "snapshot lookback 24h -> 48h",
        ),
        (
            "11",
            "if not sensors or not snapshot.has_data:",
            "if not sensors and not snapshot.has_data:",
            "fallback gate or -> and",
        ),
        (
            "12",
            "            confidence=0.5,\n            sensor_snapshot=snapshot,",
            "            confidence=0.6,\n            sensor_snapshot=snapshot,",
            "base confidence 0.5 -> 0.6",
        ),
        ("13", _WW + _CS, _CS + _WW, "pipeline order: critical stress before water warning"),
        (
            "14",
            _WW + _CS + "\n        # Cluster-level timing gate — checked AFTER stress overrides on purpose:\n"
            "        # a wilting plant still gets water at 2am, a healthy one doesn't.\n" + _WIN,
            _WIN + _WW + _CS,
            "pipeline order: window gate before stress overrides",
        ),
        (
            "15",
            "        _apply_temperature_adjustment(decision, ideal_temp_range)\n"
            "        _apply_humidity_adjustment(decision, ideal_humidity_range)\n",
            "        _apply_humidity_adjustment(decision, ideal_humidity_range)\n"
            "        _apply_temperature_adjustment(decision, ideal_temp_range)\n",
            "pipeline order: humidity before temperature adjustment",
        ),
        (
            "16",
            "        self._apply_seasonal_multiplier(cluster, decision, plant_care, evaluated_at)\n",
            "",
            "seasonal multiplier step removed",
        ),
        ("17", "        _apply_trend_adjustment(decision)\n\n", "\n", "trend adjustment step removed"),
        (
            "18",
            "            return (int(start), int(end))",
            "            return (int(end), int(start))",
            "quiet window tuple swapped (message)",
        ),
        (
            "19",
            "        tz_name = prefs.timezone if prefs else None\n        if is_within_quiet_hours(",
            "        tz_name = None\n        if is_within_quiet_hours(",
            "quiet hours ignore preferences timezone",
        ),
        (
            "20",
            "        if is_within_irrigation_window(windows, now_unix=evaluated_at, tz_name=tz_name):",
            "        if not is_within_irrigation_window(windows, now_unix=evaluated_at, tz_name=tz_name):",
            "window rule negated",
        ),
        (
            "21",
            '"season_frequency_multiplier_outdoor" if environment == "outdoor" else',
            '"season_frequency_multiplier_outdoor" if environment != "outdoor" else',
            "outdoor season key inverted",
        ),
        ("22", "        if multiplier == 1.0:\n            return\n", "", "multiplier == 1.0 no-op removed"),
        (
            "23",
            "int(round(decision.interval_hours / multiplier))",
            "int(round(decision.interval_hours * multiplier))",
            "seasonal multiplier applied as interval factor (* not /)",
        ),
        (
            "24",
            "SEASONAL_HOLD if multiplier < 1.0 else",
            "SEASONAL_HOLD if multiplier > 1.0 else",
            "seasonal HOLD/BOOST swapped",
        ),
        (
            "25",
            "new_interval = max(MIN_INTERVAL_HOURS, min(MAX_INTERVAL_HOURS, new_interval))",
            "new_interval = min(MAX_INTERVAL_HOURS, new_interval)",
            "seasonal clamp lower bound removed",
        ),
        (
            "26",
            "max(0, math.ceil((vac.ends_at - now) / 86400))",
            "max(0, math.floor((vac.ends_at - now) / 86400))",
            "vacation 'returns in' ceil -> floor",
        ),
        (
            "27",
            "usable_l = irr.reservoir_l * VACATION_RESERVOIR_USABLE_FRACTION",
            "usable_l = irr.reservoir_l * 1.0",
            "vacation usable fraction 0.95 -> 1.0",
        ),
        (
            "28",
            "if binding_max_min >= decision.duration_minutes:",
            "if binding_max_min > decision.duration_minutes:",
            "vacation budget boundary >= -> >",
        ),
        (
            "29",
            "if binding_max_min >= VACATION_MIN_RUN_MINUTES:",
            "if binding_max_min > VACATION_MIN_RUN_MINUTES:",
            "vacation min-run boundary >= -> >",
        ),
        ("30", "daily_budget_l * (day_index + 1)", "daily_budget_l * day_index", "vacation cumulative day off-by-one"),
        (
            "31",
            "        if decision.action is not Action.IRRIGATE:\n            return\n",
            "",
            "vacation rationing applied to SKIP decisions",
        ),
        ("32", "                actuated=False,", "                actuated=True,", "persisted decision actuated=True"),
        ("33", "since=now - hold_seconds)", "since=now - hold_seconds + 1)", "leak hold edge inclusive -> exclusive"),
        ("34", "({hours_left:.1f}h left)", "({hours_left:.0f}h left)", "leak hold message precision"),
        (
            "35",
            "            0,\n            LEAK_HOLD_HOURS,",
            "            0,\n            DEFAULT_INTERVAL_HOURS,",
            "leak hold interval 24 -> 12",
        ),
        (
            "36",
            'if event.action != "start":',
            'if event.action not in ("start", "schedule_updated"):',
            "cooldown counts schedule_updated (issue #103 regression)",
        ),
        (
            "37",
            "event.timestamp > latest_event.timestamp",
            "event.timestamp < latest_event.timestamp",
            "cooldown picks the oldest start, not the latest",
        ),
        (
            "38",
            "get_recent_events(irr.id, hours=MIN_COOLDOWN_HOURS)",
            "get_recent_events(irr.id, hours=MIN_COOLDOWN_HOURS - 1)",
            "cooldown window 6h -> 5h",
        ),
        (
            "39",
            'if self._weather is None or cluster.environment == "indoor":',
            'if self._weather is None or cluster.environment != "outdoor":',
            "weather rule only for 'outdoor'",
        ),
        ("40", "if precip <= 2.0:", "if precip < 2.0:", "rain threshold <= -> <"),
        (
            "41",
            "self._weather.get_forecast(hours=6)",
            "self._weather.get_forecast(hours=12)",
            "forecast horizon 6h -> 12h",
        ),
        (
            "42",
            "            if alerts:\n                stress.learning_alerts",
            "            if not alerts:\n                stress.learning_alerts",
            "learning alerts gate negated",
        ),
        (
            "43",
            "    decision.confidence = CONFIDENCE_WATER_WARNING",
            "    decision.confidence = CONFIDENCE_CRITICAL_STRESS",
            "water-warning confidence changed",
        ),
        (
            "44",
            "        decision.action = Action.SKIP\n        decision.interval_hours = MAX_INTERVAL_HOURS\n        decision.confidence = CONFIDENCE_OVER_WATERING",
            "        decision.action = Action.SKIP\n        decision.interval_hours = DEFAULT_INTERVAL_HOURS\n        decision.confidence = CONFIDENCE_OVER_WATERING",
            "over-watering interval 24 -> 12",
        ),
        (
            "45",
            "min_soil = snapshot.min_soil_moisture if snapshot.min_soil_moisture is not None else snapshot.avg_soil_moisture",
            "min_soil = snapshot.avg_soil_moisture",
            "driest plant -> average (invariant #2)",
        ),
        (
            "46",
            "has_conflict = (min_soil < target_min) and (max_soil > target_max - CONFLICT_WET_MARGIN)",
            "has_conflict = (min_soil < target_min) or (max_soil > target_max - CONFLICT_WET_MARGIN)",
            "conflict and -> or",
        ),
        (
            "47",
            "if min_soil < target_min - VERY_DRY_MARGIN:",
            "if min_soil <= target_min - VERY_DRY_MARGIN:",
            "very-dry boundary < -> <=",
        ),
        ("48", "    elif min_soil < target_min:", "    elif min_soil <= target_min:", "dry boundary < -> <="),
        (
            "49",
            "elif snapshot.avg_soil_moisture <= target_max:",
            "elif snapshot.avg_soil_moisture < target_max:",
            "adequate/wet boundary <= -> <",
        ),
        (
            "50",
            "target_min = min(r[0] for r in target_ranges)",
            "target_min = max(r[0] for r in target_ranges)",
            "target_min min -> max over plants",
        ),
        (
            "51",
            "if avg_temp > temp_range[1] + TEMP_ADJUST_OFFSET:",
            "if avg_temp >= temp_range[1] + TEMP_ADJUST_OFFSET:",
            "temp-high boundary > -> >=",
        ),
        (
            "52",
            "if avg_hum < humidity_range[0] - HUMIDITY_VERY_LOW_OFFSET:",
            "if avg_hum < humidity_range[0] - HUMIDITY_LOW_OFFSET:",
            "humidity very-low offset 20 -> 5",
        ),
        (
            "53",
            "if avg_light > LIGHT_VERY_BRIGHT * sf:",
            "if avg_light > LIGHT_VERY_BRIGHT:",
            "very-bright drops seasonal factor",
        ),
        (
            "54",
            "    elif avg_light < LIGHT_VERY_DARK * sf:",
            "    elif avg_light < LIGHT_DARK * sf:",
            "very-dark threshold 50 -> 150",
        ),
        (
            "55",
            "if duration_delta == 0 and interval_delta == 0:",
            "if duration_delta == 0 or interval_delta == 0:",
            "water-needs no-op gate and -> or",
        ),
        (
            "56",
            "max(DEFAULT_DURATION_MINUTES, decision.duration_minutes + WATER_NEEDS_DURATION_STEP)",
            "max(DEFAULT_DURATION_MINUTES, decision.duration_minutes - WATER_NEEDS_DURATION_STEP)",
            "high water-needs step sign flipped",
        ),
        (
            "57",
            "avg_temp and avg_temp > TREND_TEMP_RISING_HOT_C",
            "avg_temp and avg_temp >= TREND_TEMP_RISING_HOT_C",
            "trend hot boundary > -> >=",
        ),
        (
            "58",
            "    if trends.irrigation_frequency_low:",
            "    if trends.irrigation_frequency_high:",
            "under-watering keyed on high frequency",
        ),
        (
            "59",
            "    severity: Severity = Severity.INFO,\n    sensor_snapshot",
            "    severity: Severity = Severity.WARNING,\n    sensor_snapshot",
            "_decision_with_reason default severity INFO -> WARNING",
        ),
        (
            "60",
            "            MIN_COOLDOWN_HOURS,\n            confidence=CONFIDENCE_COOLDOWN,\n            code=TriggerCode.COOLDOWN,",
            "            DEFAULT_INTERVAL_HOURS,\n            confidence=CONFIDENCE_COOLDOWN,\n            code=TriggerCode.COOLDOWN,",
            "cooldown decision interval 6 -> 12",
        ),
        (
            "61",
            "(last irrigation {hours_ago:.1f}h ago, trigger: {latest_event.triggered_by})",
            "(last irrigation {hours_ago:.1f}h ago)",
            "cooldown message drops trigger",
        ),
        (
            "62",
            "        decision.duration_minutes = 0\n        decision.confidence = CONFIDENCE_COOLDOWN",
            "        decision.confidence = CONFIDENCE_COOLDOWN",
            "vacation exhausted keeps duration",
        ),
        (
            "63",
            '    if trends.soil_moisture_trend == "declining":',
            '    if trends.soil_moisture_trend == "falling":',
            "declining-trend adjustment keyed on wrong label",
        ),
        (
            "64",
            '        except Exception:\n            log.warning("failed to persist decision log", exc_info=True)',
            '        except ValueError:\n            log.warning("failed to persist decision log", exc_info=True)',
            "persistence failure no longer swallowed (best-effort)",
        ),
    ],
)

add(
    "cleaning",
    "logic",
    CORE + "logic/cleaning.py",
    [
        (
            "01",
            "if n < CLEANING_HAMPEL_MIN_READINGS:",
            "if n <= CLEANING_HAMPEL_MIN_READINGS:",
            "min-readings boundary < -> <=",
        ),
        ("02", "hi = min(n, i + radius + 1)", "hi = min(n, i + radius)", "window right edge off-by-one"),
        (
            "03",
            "scale = max(CLEANING_MAD_SCALE * mad, CLEANING_MAD_FLOOR)",
            "scale = CLEANING_MAD_SCALE * mad",
            "MAD floor removed",
        ),
        (
            "04",
            "if abs(values[i] - median) > n_sigma * scale:",
            "if abs(values[i] - median) >= n_sigma * scale:",
            "spike test > -> >=",
        ),
        (
            "05",
            "if value is not None and not (low <= value <= high):",
            "if value is not None and not (low < value <= high):",
            "range gate lower bound inclusive -> exclusive",
        ),
        (
            "06",
            "ordered = sorted(readings, key=lambda r: r.timestamp)",
            "ordered = sorted(readings, key=lambda r: -r.timestamp)",
            "clean_readings sorts DESC",
        ),
        (
            "07",
            "return list(reversed(clean_readings(readings)))",
            "return clean_readings(readings)",
            "clean_readings_desc returns ASC",
        ),
        (
            "08",
            "    split = len(ordered_before)\n    return cleaned[:split], cleaned[split:]",
            "    return clean_readings(ordered_before), clean_readings(ordered_after)",
            "clean_readings_around cleans halves separately",
        ),
        ("09", "lo = max(0, i - radius)", "lo = max(0, i - radius + 1)", "window left edge off-by-one"),
        ("10", "median = statistics.median(window)", "median = statistics.mean(window)", "rolling median -> mean"),
        ("11", "                setattr(cr, field, None)", "                pass", "range gate disabled"),
    ],
)
add(
    "timing",
    "logic",
    CORE + "logic/timing.py",
    [
        ("01", "return start <= hour < end", "return start <= hour <= end", "window end inclusive"),
        (
            "02",
            "return hour >= start or hour < end",
            "return hour > start or hour < end",
            "wrap window start exclusive",
        ),
        (
            "03",
            "    if start == end:\n        return False\n    if start < end:",
            "    if start == end:\n        return True\n    if start < end:",
            "empty window (start==end) matches every hour",
        ),
        ("04", "return 1 << dt.weekday()", "return 1 << ((dt.weekday() + 1) % 7)", "weekday bit shifted by one day"),
        ("05", "        if not (w.weekday_mask & bit):\n            continue\n", "", "weekday mask ignored"),
        (
            "06",
            "    if not windows:\n        return True\n    dt = local_now",
            "    if not windows:\n        return False\n    dt = local_now",
            "no windows -> never allowed (issue #83 regression)",
        ),
        ("07", "if month in (3, 4, 5):", "if month in (3, 4):", "May no longer spring"),
        (
            "08",
            "month = ((month - 1 + 6) % 12) + 1",
            "month = ((month - 1 + 3) % 12) + 1",
            "southern hemisphere offset 6 -> 3",
        ),
        (
            "09",
            '    except ZoneInfoNotFoundError:\n        return ZoneInfo("UTC")',
            '    except ZoneInfoNotFoundError:\n        return ZoneInfo("Europe/Rome")',
            "bad tz falls back to Rome, not UTC",
        ),
        (
            "10",
            "    if plant_override:\n        if (val := plant_override.get(season)) is not None:\n            return float(val)\n"
            "    if category_override:\n        if (val := category_override.get(season)) is not None:\n            return float(val)\n",
            "    if category_override:\n        if (val := category_override.get(season)) is not None:\n            return float(val)\n"
            "    if plant_override:\n        if (val := plant_override.get(season)) is not None:\n            return float(val)\n",
            "multiplier precedence category before species",
        ),
        (
            "11",
            'table = DEFAULT_SEASON_MULTIPLIER_OUTDOOR if environment == "outdoor" else',
            'table = DEFAULT_SEASON_MULTIPLIER_OUTDOOR if environment != "outdoor" else',
            "built-in table indoor/outdoor swapped",
        ),
        (
            "12",
            "    if start_hour == end_hour:\n        return False\n    dt = local_now",
            "    dt = local_now",
            "quiet hours start==end guard removed",
            0,
            "_hour_in_range() itself returns False when start == end, so the guard is redundant",
        ),
        (
            "13",
            "    return float(table.get(season, 1.0))",
            "    return float(table.get(season, 1.1))",
            "missing-season default 1.0 -> 1.1",
            0,
            "both built-in tables define all four seasons and Season is a closed Literal, so the default is unreachable",
        ),
    ],
)
add(
    "stress",
    "logic",
    CORE + "logic/stress.py",
    [
        (
            "01",
            "snapshot.avg_env_humidity < hum_range[0] - 20:",
            "snapshot.avg_env_humidity < hum_range[0] - 15:",
            "low-humidity margin 20 -> 15",
        ),
        (
            "02",
            "snapshot.avg_light < seasonal_min * 0.4:",
            "snapshot.avg_light < seasonal_min * 0.5:",
            "low-light factor 0.4 -> 0.5",
        ),
        (
            "03",
            "if avg_soil < SOIL_MOISTURE_CRITICAL:",
            "if avg_soil <= SOIL_MOISTURE_CRITICAL:",
            "critical soil < -> <=",
        ),
        (
            "04",
            "if trends.soil_moisture_delta < -10:",
            "if trends.soil_moisture_delta < -5:",
            "steep decline -10 -> -5",
        ),
        (
            "05",
            "snapshot.avg_temperature > temp_range[1] + 5:",
            "snapshot.avg_temperature > temp_range[1] + 3:",
            "heat-stress margin 5 -> 3",
        ),
        (
            "06",
            "        if trends.irrigation_frequency_high:\n            stress.over_watering",
            "        if trends.irrigation_frequency_low:\n            stress.over_watering",
            "over-watering keyed on low frequency",
        ),
        ("07", 'stress.water_stress += " + declining"', "pass", "critical + declining suffix dropped"),
        (
            "08",
            'min_lux_needed = max((d.get("ideal_light_lux_min", 0) for d in plant_care), default=0)',
            'min_lux_needed = min((d.get("ideal_light_lux_min", 0) for d in plant_care), default=0)',
            "light need max -> min over plants",
        ),
        (
            "09",
            'elif avg_soil < SOIL_MOISTURE_LOW and trends.soil_moisture_trend == "declining":',
            'elif avg_soil < SOIL_MOISTURE_LOW or trends.soil_moisture_trend == "declining":',
            "low+declining and -> or",
        ),
        (
            "10",
            "snapshot.avg_soil_moisture > SOIL_MOISTURE_SATURATED:",
            "snapshot.avg_soil_moisture >= SOIL_MOISTURE_SATURATED:",
            "saturated boundary > -> >=",
        ),
    ],
)
add(
    "trends",
    "logic",
    CORE + "logic/trends.py",
    [
        (
            "01",
            "if len(all_readings) >= TREND_MIN_READINGS:",
            "if len(all_readings) > TREND_MIN_READINGS:",
            "min readings >= -> >",
        ),
        ("02", "mid = len(all_readings) // 2", "mid = len(all_readings) // 3", "split point halves -> thirds"),
        (
            "03",
            "if delta < -TREND_MOISTURE_THRESHOLD:",
            "if delta <= -TREND_MOISTURE_THRESHOLD:",
            "declining boundary < -> <=",
        ),
        (
            "04",
            "temp_first = [r.temperature for r in all_readings[:mid] if r.temperature]",
            "temp_first = [r.temperature for r in all_readings[:mid] if r.temperature is not None]",
            "0.0 C counted in temperature trend (truthiness -> is not None)",
        ),
        (
            "05",
            "e.action == EVENT_ACTION_START and e.duration_minutes",
            'e.action in (EVENT_ACTION_START, "schedule_updated") and e.duration_minutes',
            "cadence counts schedule_updated",
        ),
        (
            "06",
            "if avg_per_day < 1 and avg_duration < 2:",
            "if avg_per_day < 1 or avg_duration < 2:",
            "frequency-low and -> or",
        ),
        ("07", "elif avg_per_day > 3:", "elif avg_per_day >= 3:", "frequency-high boundary > -> >="),
        (
            "08",
            "get_recent_events(irrigator.id, hours=7 * 24)",
            "get_recent_events(irrigator.id, hours=24)",
            "cadence window 7d -> 1d",
        ),
        ("09", "            all_readings.sort(key=lambda r: r.timestamp)\n", "", "pooled readings not re-sorted"),
        (
            "10",
            "elif delta_temp < -TREND_TEMP_THRESHOLD:",
            "elif delta_temp <= -TREND_TEMP_THRESHOLD:",
            "falling boundary < -> <=",
        ),
        (
            "11",
            "db.get_recent_readings(sensor.id, hours=48)",
            "db.get_recent_readings(sensor.id, hours=24)",
            "trend lookback 48h -> 24h",
        ),
    ],
)
add(
    "fallback",
    "logic",
    CORE + "logic/fallback.py",
    [
        ("01", "if temp <= TEMP_COLD:", "if temp < TEMP_COLD:", "cold band <= -> <"),
        ("02", "elif temp <= TEMP_WARM:", "elif temp < TEMP_WARM:", "warm band <= -> <"),
        (
            "03",
            "interval = max(MIN_INTERVAL_HOURS, interval - 4)",
            "interval = max(MIN_INTERVAL_HOURS, interval - 2)",
            "high-needs step 4 -> 2",
        ),
        (
            "04",
            "interval = min(MAX_INTERVAL_HOURS, interval + 6)",
            "interval = min(MAX_INTERVAL_HOURS, interval + 4)",
            "low-needs step 6 -> 4",
        ),
        (
            "05",
            'Action.SKIP if effective_mode == "manual" else Action.IRRIGATE',
            'Action.SKIP if effective_mode != "manual" else Action.IRRIGATE',
            "config fallback manual/smart inverted",
        ),
        (
            "06",
            'base.duration_minutes = int(effective["duration_minutes"]["value"] or DEFAULT_DURATION_MINUTES)',
            "base.duration_minutes = DEFAULT_DURATION_MINUTES",
            "config duration ignored",
        ),
        (
            "07",
            "    base.confidence = CONFIDENCE_TEMP_FALLBACK",
            "    base.confidence = CONFIDENCE_CONFIG_FALLBACK",
            "temp fallback confidence changed",
        ),
        ("08", "        if config:\n", "        if not config:\n", "config branch negated"),
        ("09", "elif temp <= TEMP_HOT:", "elif temp < TEMP_HOT:", "hot band <= -> <"),
        (
            "10",
            'message=f"temperature-based ({temp:.0f}°C, {water_needs} water needs)"',
            'message=f"temperature-based ({temp:.1f}°C, {water_needs} water needs)"',
            "fallback message precision",
        ),
    ],
)
add(
    "decision",
    "logic",
    CORE + "logic/decision.py",
    [
        (
            "01",
            'return "; ".join(r.message for r in self.reasons) or "no specific conditions"',
            'return ", ".join(r.message for r in self.reasons) or "no specific conditions"',
            "reason_text separator",
        ),
        (
            "02",
            'return "; ".join(r.message for r in self.reasons) or "no specific conditions"',
            'return "; ".join(r.message for r in self.reasons) or ""',
            "empty reason_text placeholder dropped",
        ),
        (
            "03",
            "return self.reasons[0].code if self.reasons else None",
            "return self.reasons[-1].code if self.reasons else None",
            "primary_code = last reason",
        ),
        (
            "04",
            'for attr in ("avg_temperature", "avg_env_humidity", "avg_soil_moisture", "avg_light")',
            'for attr in ("avg_temperature", "avg_env_humidity", "avg_soil_moisture")',
            "has_data ignores light",
        ),
        (
            "05",
            "model_config = ConfigDict(from_attributes=True, validate_assignment=True)",
            "model_config = ConfigDict(from_attributes=True, validate_assignment=False)",
            "validate_assignment disabled",
        ),
        (
            "06",
            "decision_log_id: int | None = Field(default=None, exclude=True)",
            "decision_log_id: int | None = Field(default=None)",
            "decision_log_id leaks into payload",
        ),
        ("07", "        TriggerCode.DEVICE_OFFLINE,\n    }", "    }", "DEVICE_OFFLINE no longer blocking"),
        (
            "08",
            "    code: TriggerCode\n    message: str\n    severity: Severity = Severity.INFO",
            "    code: TriggerCode\n    message: str\n    severity: Severity = Severity.WARNING",
            "Reason default severity",
            0,
            "Reason is only ever built by IrrigationDecision.add_reason, which always passes severity explicitly; "
            "Reason is not part of any OpenAPI schema",
        ),
        (
            "09",
            "                duration_delta=duration_delta,\n                interval_delta=interval_delta,",
            "                duration_delta=interval_delta,\n                interval_delta=duration_delta,",
            "add_reason swaps deltas",
        ),
    ],
)
add(
    "sensors",
    "logic",
    CORE + "logic/sensors.py",
    [
        (
            "01",
            "if r.light is not None and r.light > 15:",
            "if r.light is not None and r.light >= 15:",
            "night-light floor > -> >=",
        ),
        (
            "02",
            "min_soil_moisture=min(all_soil) if all_soil else None",
            "min_soil_moisture=statistics.mean(all_soil) if all_soil else None",
            "snapshot min soil -> mean (invariant #2)",
        ),
        (
            "03",
            "water_warnings=sorted(set(water_warnings)),",
            "water_warnings=water_warnings,",
            "water warnings not de-duplicated",
        ),
        (
            "04",
            "clean_readings(db.get_recent_readings(sensor.id, hours=hours))",
            "db.get_recent_readings(sensor.id, hours=hours)",
            "snapshot reads raw rows (invariant #10)",
        ),
        (
            "05",
            "max_soil_moisture=max(all_soil) if all_soil else None",
            "max_soil_moisture=statistics.mean(all_soil) if all_soil else None",
            "snapshot max soil -> mean",
        ),
    ],
)
_ISS = CORE + "learning/issues.py"
_PROF = CORE + "learning/profiling.py"
add(
    "learning",
    "learning",
    _ISS,
    [
        (
            "01",
            "if not profile or profile.response_count < LEARNING_MIN_EVENTS:",
            "if not profile or profile.response_count <= LEARNING_MIN_EVENTS:",
            "min events < -> <=",
        ),
        (
            "02",
            "profile.efficiency_score < LEARNING_MIN_EFFICIENCY\n            and profile.avg_absorption",
            "profile.efficiency_score < LEARNING_MIN_EFFICIENCY\n            or profile.avg_absorption",
            "blocked drip and -> or",
        ),
        (
            "03",
            "if avg_lux is not None and avg_lux > bright_threshold:",
            "if avg_lux is not None and avg_lux < bright_threshold:",
            "light-accelerated drainage comparison flipped",
        ),
        (
            "04",
            "if max_recent < target_min and profile.response_count >= 5:",
            "if max_recent < target_min and profile.response_count >= 3:",
            "chronic underwatering min responses 5 -> 3",
        ),
        ("05", "    if len(profiles) >= 2:", "    if len(profiles) > 2:", "conflict check needs > 2 profiles"),
        ("06", "if moisture < target_min - 5:", "if moisture < target_min - 10:", "dry-sensor margin 5 -> 10"),
        (
            "07",
            "statistics.mean(moisture_values[:3])",
            "statistics.mean(moisture_values[-3:])",
            "latest-3 -> oldest-3 (DESC bug)",
        ),
        (
            "08",
            "if projected_wet > LEARNING_OVER_WATER_THRESHOLD:",
            "if projected_wet < LEARNING_OVER_WATER_THRESHOLD:",
            "projected over-water comparison flipped",
        ),
        ("09", "        if len(lux_vals) < 5:", "        if len(lux_vals) < 3:", "low-light min samples 5 -> 3"),
        (
            "10",
            "if avg_lux_7d < seasonal_min_lux * 0.5:",
            "if avg_lux_7d < seasonal_min_lux * 0.4:",
            "low-light factor 0.5 -> 0.4",
        ),
        (
            "11",
            "if avg_env_hum < ideal_hum_min - 15:",
            "if avg_env_hum < ideal_hum_min - 20:",
            "low env humidity margin 15 -> 20",
        ),
        (
            "12",
            "if profile.avg_drainage_per_hour < LEARNING_RAPID_DRAINAGE_THRESHOLD:",
            "if profile.avg_drainage_per_hour <= LEARNING_RAPID_DRAINAGE_THRESHOLD:",
            "rapid drainage boundary < -> <=",
        ),
        (
            "13",
            '            alert_type="blocked_drip",',
            '            alert_type="blocked",',
            "alert_type string changed",
        ),
        ("14", "        if len(hum_vals) < 5:", "        if len(hum_vals) < 3:", "low-humidity min samples 5 -> 3"),
        (
            "15",
            "            if not dry_profile or dry_profile.avg_absorption_per_minute <= 0:",
            "            if not dry_profile:",
            "non-positive absorption guard removed",
        ),
    ],
)
add(
    "learning",
    "learning",
    _PROF,
    [
        (
            "16",
            "(r.timestamp - event.timestamp) >= MIN_POST_DELAY_SEC",
            "(r.timestamp - event.timestamp) > MIN_POST_DELAY_SEC",
            "post delay boundary >= -> >",
        ),
        ("17", "    pre = pre_moisture_readings[-1]", "    pre = pre_moisture_readings[0]", "pre reading = oldest"),
        (
            "18",
            "    post = max(post_moisture_readings, key=lambda r: r.soil_moisture)",
            "    post = min(post_moisture_readings, key=lambda r: r.soil_moisture)",
            "post reading = min, not peak",
        ),
        (
            "19",
            "    duration = event.duration_minutes or 2",
            "    duration = event.duration_minutes or 1",
            "default duration 2 -> 1",
        ),
        (
            "20",
            "irrigation_events = [e for e in all_events if e.action == EVENT_ACTION_START and e.timestamp >= cutoff]",
            "irrigation_events = [e for e in all_events if e.timestamp >= cutoff]",
            "profile counts non-start events",
        ),
        (
            "21",
            "positive_responses = sum(1 for d in deltas if d > 2)",
            "positive_responses = sum(1 for d in deltas if d > 0)",
            "efficiency threshold 2% -> 0%",
        ),
        (
            "22",
            "if delta < 0 and 0.1 < hours < 12:",
            "if delta < 0 and 0.1 < hours < 24:",
            "drainage gap limit 12h -> 24h",
        ),
        (
            "23",
            "delta_per_minute=delta / duration if duration > 0 else 0",
            "delta_per_minute=delta * duration if duration > 0 else 0",
            "delta per minute * instead of /",
        ),
        (
            "24",
            "before_seconds=RESPONSE_PRE_WINDOW_SECONDS,",
            "before_seconds=RESPONSE_PRE_WINDOW_SECONDS * 2,",
            "pre window 30 -> 60 min",
        ),
        (
            "25",
            "    return statistics.mean(declines)  # Negative value",
            "    return min(declines)  # Negative value",
            "drainage mean -> min",
        ),
    ],
)
add(
    "learning",
    "learning",
    CORE + "learning/report.py",
    [
        (
            "26",
            "        if profile.efficiency_score < 0.5:",
            "        if profile.efficiency_score < 0.6:",
            "report low-efficiency 0.5 -> 0.6",
        ),
        (
            "27",
            'lines.append(f"   [{alert.severity.upper()}] {alert.message}")',
            'lines.append(f"   [{alert.severity}] {alert.message}")',
            "report severity not upper-cased",
        ),
        ("28", '        return "No sensors in cluster."', '        return ""', "report empty-cluster text"),
    ],
)
add(
    "learning",
    "learning",
    CORE + "learning/learner.py",
    [
        (
            "29",
            "    def get_plant_profile(self, sensor: Sensor, days: int = 30)",
            "    def get_plant_profile(self, sensor: Sensor, days: int = 7)",
            "learner profile default days 30 -> 7",
        ),
        (
            "30",
            "        return detect_issues(self.db, self.plant_db, cluster_id)",
            "        return []",
            "learner.detect_issues returns nothing",
        ),
    ],
)

_GW = CORE + "devices/gateway.py"
_IK = CORE + "devices/irrigators/ik10pw.py"
_TG = CORE + "devices/irrigators/tuya_generic.py"
add(
    "devices",
    "devices",
    _GW,
    [
        (
            "01",
            'self.region = region or os.environ.get("TUYA_REGION", "eu")',
            'self.region = region or os.environ.get("TUYA_REGION", "us")',
            "default Tuya region eu -> us",
        ),
        (
            "02",
            "if not self.client_id or not self.client_secret:",
            "if not self.client_id and not self.client_secret:",
            "credential check or -> and",
        ),
        (
            "03",
            'f"/v2.0/cloud/thing/{device_id}/shadow/properties"',
            'f"/v1.0/cloud/thing/{device_id}/shadow/properties"',
            "shadow endpoint version",
        ),
        (
            "04",
            'if result is not None and result.get("success"):',
            "if result is not None:",
            "v2 success flag ignored (no v1 fallback on failure)",
        ),
        (
            "05",
            '        result = self._cloud.getstatus(device_id)\n        if not result.get("success"):\n            raise RuntimeError',
            '        result = self._cloud.getstatus(device_id)\n        if result.get("success"):\n            raise RuntimeError',
            "v1 fallback success check negated",
        ),
        (
            "06",
            "            evtype=7,  # Status report events",
            "            evtype=1,  # Status report events",
            "getdevicelog evtype 7 -> 1",
        ),
        (
            "07",
            "since_ms = int((time.time() - hours * 3600) * 1000)\n\n        result",
            "since_ms = int((time.time() - hours * 60) * 1000)\n\n        result",
            "default log window hours*3600 -> hours*60",
        ),
        ("08", '        parsed.sort(key=lambda x: x["timestamp_ms"])\n', "", "device logs not sorted"),
        (
            "09",
            'if abs(log["timestamp_ms"] - current_ts) > tolerance_ms:',
            'if abs(log["timestamp_ms"] - current_ts) >= tolerance_ms:',
            "grouping tolerance > -> >=",
        ),
        (
            "10",
            "    if len(current_group) > 1:  # Has",
            "    if len(current_group) > 0:  # Has",
            "trailing empty group kept",
        ),
        (
            "11",
            '            return True, "Command succeeded"',
            '            return True, "OK"',
            "send_command success message",
        ),
        (
            "12",
            '            cfg_key = _coerce_config(config).get("local_key") if config is not None else None\n            if cfg_key:\n                return cfg_key\n',
            "",
            "config local_key ignored (Cloud lookup every time)",
        ),
        ("13", "            self._key_cache[device_id] = key\n", "", "discovered key not cached"),
        (
            "14",
            "        device.set_version(protocol_version)\n",
            "        device.set_version(3.3)\n",
            "local protocol forced to 3.3",
        ),
        ("15", "        device.set_socketTimeout(LOCAL_TIMEOUT)\n", "", "local socket timeout not set"),
        (
            "16",
            'raise ConnectionError(f"No device_ip in config for irrigator {irrigator.tuya_device_id}.")',
            'raise ValueError(f"No device_ip in config for irrigator {irrigator.tuya_device_id}.")',
            "missing IP error type",
        ),
        (
            "17",
            '"temp_current": lambda v: ("temperature", float(v) / 10.0),',
            '"temp_current": lambda v: ("temperature", float(v)),',
            "temperature DP scale /10 dropped",
        ),
        (
            "18",
            '"illumiance": lambda v: ("light", int(v)),',
            '"illumiance": lambda v: ("lux", int(v)),',
            "illuminance canonical key",
        ),
        (
            "19",
            '                except (ValueError, TypeError):\n                    entry["key"] = code\n                    entry["value"] = raw_value\n',
            "                except (ValueError, TypeError):\n                    continue\n",
            "unparseable log entry dropped",
        ),
        (
            "20",
            '            if d.get("id") == device_id:\n                    return d.get("key")',
            '            if d.get("id") != device_id:\n                    return d.get("key")',
            "getdevices key match negated",
        ),
        (
            "21",
            '                except Exception:\n                    logger.exception("local_key persistence hook failed',
            '                except KeyError:\n                    logger.exception("local_key persistence hook failed',
            "key hook failure not swallowed",
        ),
    ],
)
add(
    "devices",
    "devices",
    _IK,
    [
        ("22", "KEEP_ALIVE_INTERVAL = 20", "KEEP_ALIVE_INTERVAL = 30", "keep-alive interval 20 -> 30 s"),
        (
            "23",
            "        if minutes is None:\n            return self.on(irrigator)\n",
            "",
            "start(None) no longer plain switch-on",
        ),
        (
            "24",
            "        duration_seconds = int(minutes * 60)",
            "        duration_seconds = int(minutes)",
            "duration DP in minutes, not seconds",
        ),
        (
            "25",
            'result = device.set_value(self.profile.dp("duration"), seconds)',
            'result = device.set_value(self.profile.dp("interval"), seconds)',
            "duration written to DP 103 instead of 102",
        ),
        (
            "26",
            '            if result and result.get("Error"):\n                return False',
            '            if result and not result.get("Error"):\n                return False',
            "local Error check negated",
        ),
        ("27", "        if dur_ok:\n", "        if not dur_ok:\n", "duration-ok branch negated"),
        (
            "28",
            "        return self._start_keepalive(irrigator, minutes)",
            "        return False, dur_msg",
            "keep-alive fallback removed",
        ),
        (
            "29",
            "                if elapsed < total_seconds and not interrupted:\n                    try:\n                        self.on(irrigator)",
            "                if elapsed <= total_seconds and not interrupted:\n                    try:\n                        self.on(irrigator)",
            "extra keep-alive pulse at the end",
        ),
        (
            "30",
            "            signal.signal(signal.SIGTERM, prev_handler)\n            try:\n                self.off(irrigator)\n            except Exception:\n                pass\n",
            "            signal.signal(signal.SIGTERM, prev_handler)\n",
            "keep-alive never switches off",
        ),
        (
            "31",
            "        bitmask = self.profile.alarm_bitmask or 0x01",
            "        bitmask = self.profile.alarm_bitmask or 0x02",
            "alarm bitmask default",
        ),
        (
            "32",
            'alarm_raw = dps.get(str(self.profile.dp("alarm")))',
            'alarm_raw = dps.get(str(self.profile.dp("work_status")))',
            "alarm read from DP 106 instead of 105",
        ),
        ("33", "    if isinstance(value, bool):\n        return value\n", "", "bool alarm handled as int"),
        (
            "34",
            "            return bool(int(text) & bitmask)",
            "            return bool(int(text))",
            "string alarm ignores bitmask",
        ),
        (
            "35",
            '                offline=True,\n                alarms=frozenset(),\n                raw={"error": f"local read failed: {exc}"},',
            '                offline=False,\n                alarms=frozenset(),\n                raw={"error": f"local read failed: {exc}"},',
            "local read failure not offline",
        ),
        (
            "36",
            "sleep_for = min(KEEP_ALIVE_INTERVAL, total_seconds - elapsed)",
            "sleep_for = KEEP_ALIVE_INTERVAL",
            "keep-alive overshoots the requested time",
        ),
        (
            "37",
            '                "source": "local",\n            },\n        )\n\n\n__all__',
            '                "source": "cloud",\n            },\n        )\n\n\n__all__',
            "health raw source label",
        ),
    ],
)
add(
    "devices",
    "devices",
    _TG,
    [
        (
            "38",
            'commands = {"commands": [{"code": "switch", "value": value}]}',
            'commands = {"commands": [{"code": "switch_1", "value": value}]}',
            "cloud switch code",
        ),
        (
            "39",
            "        return self._send_switch(irrigator, False)",
            "        return self._send_switch(irrigator, True)",
            "off() sends ON",
        ),
        (
            "40",
            'if proto is not None and self.profile.has_capability("supports_local_status"):',
            "if proto is not None:",
            "local status ignores capability",
        ),
        (
            "41",
            '                if live and "dps" in live:',
            "                if live:",
            "local status without dps accepted",
            0,
            "a non-empty status without 'dps' then raises KeyError on live['dps'] inside the same try, whose "
            "`except Exception: pass` falls through to the identical Cloud fallback",
        ),
        (
            "42",
            '            elif code == "work_state":',
            '            elif code == "work_status":',
            "cloud work_state code",
        ),
        (
            "43",
            '        return "running" if profile_key == "switch" else profile_key',
            "        return profile_key",
            "switch not renamed running",
        ),
        (
            "44",
            '            return {"error": f"Cloud API error: {result}"}',
            "            return {}",
            "cloud status error dropped",
        ),
    ],
)
add(
    "devices",
    "devices",
    CORE + "devices/sensors/tr301z.py",
    [
        ("45", '    "low": 10,', '    "low": 20,', "battery bucket low 10 -> 20"),
        (
            "46",
            "        if latest.water_warning is True:",
            "        if latest.water_warning:",
            "water_warning truthiness",
            0,
            "water_warning is a nullable Boolean column; True is the only truthy value it can hold",
        ),
        (
            "47",
            "            battery_pct = _BATTERY_STATE_BUCKETS.get(state.lower())",
            "            battery_pct = _BATTERY_STATE_BUCKETS.get(state)",
            "battery state case-sensitive",
        ),
        (
            "48",
            "            last_seen_ts=latest.timestamp,",
            "            last_seen_ts=now,",
            "last_seen = now (never stale)",
        ),
    ],
)
add(
    "devices",
    "devices",
    CORE + "devices/registry.py",
    [
        ("49", '    "tuya_local": "rainpoint.ik10pw",\n', "", "legacy tuya_local alias removed"),
        ("50", '    "temp_humidity": "tuya.tr301z",\n', "", "legacy temp_humidity alias removed"),
        (
            "51",
            '        key = self._resolve_irrigator_key(irrigator.type or "")',
            '        key = irrigator.type or ""',
            "irrigator aliases ignored",
        ),
    ],
)
add(
    "devices",
    "devices",
    CORE + "devices/profiles/ik10pw.json",
    [
        ("52", '"protocol_version": 3.5,', '"protocol_version": 3.3,', "IK10PW protocol 3.5 -> 3.3 (invariant #1)"),
        ("53", '"duration": 102,', '"duration": 103,', "IK10PW duration DP 102 -> 103"),
        ("54", '"alarm": 105,', '"alarm": 104,', "IK10PW alarm DP 105 -> 104"),
    ],
)
add(
    "devices",
    "devices",
    CORE + "sync.py",
    [
        (
            "55",
            "        since_ms = (last_ts - 60) * 1000",
            "        since_ms = last_ts * 1000",
            "sync overlap 60 s removed",
        ),
        (
            "56",
            '        if live and any(k in live for k in ("temperature", "soil_moisture", "humidity", "light")):',
            "        if live:",
            "live reading saved without a metric",
        ),
        ("57", '                water_warning=live.get("water_warning"),\n', "", "live water_warning not persisted"),
        ("58", "        if not ts:\n            continue\n", "", "log reading without timestamp not skipped"),
        (
            "59",
            '                    parts.append(f"{new} new from logs")',
            '                    parts.append(f"{new} new")',
            "sync log text",
        ),
        (
            "60",
            '                stats["errors"].append(f"{sensor.name}: {e}")',
            '                stats["errors"].append(str(e))',
            "sync error string drops sensor name",
        ),
        (
            "61",
            "            if result is not None:\n                live_saved = 1",
            "            live_saved = 1",
            "duplicate live reading counted",
        ),
    ],
)

_SCH = SRV + "scheduler.py"
add(
    "scheduler",
    "scheduler",
    _SCH,
    [
        (
            "01",
            '_JOB_DEFAULTS = {"misfire_grace_time": None, "coalesce": True, "max_instances": 1}',
            '_JOB_DEFAULTS = {"misfire_grace_time": 1, "coalesce": True, "max_instances": 1}',
            "misfire grace None -> 1 s",
        ),
        ("02", "    scheduler.remove_all_jobs()\n", "", "init_scheduler no longer clears pending jobs"),
        (
            "03",
            '        minutes=15,\n        id="sensor_anomaly",',
            '        minutes=30,\n        id="sensor_anomaly",',
            "anomaly interval 15 -> 30 min",
        ),
        (
            "04",
            "        hour=_resolve_check_cron_hours(settings),\n        minute=0,",
            "        hour=_resolve_check_cron_hours(settings),\n        minute=5,",
            "check_all minute 0 -> 5",
        ),
        (
            "05",
            "        hour=0,\n        minute=30,",
            "        hour=1,\n        minute=30,",
            "plant health snapshot 00:30 -> 01:30",
        ),
        (
            "06",
            '_TZ_BOUND_CRON_JOBS = ("check_all", "plant_health_snapshot")',
            '_TZ_BOUND_CRON_JOBS = ("check_all",)',
            "health snapshot not re-bound on tz change",
        ),
        (
            "07",
            "    if paused:\n        apply_persisted_pause(True)",
            "    if paused:\n        pass",
            "pause lost on timezone reschedule",
        ),
        ("08", '    return f"*/{n}"', '    return f"0/{n}"', "legacy interval translation"),
        (
            "09",
            "    if settings.check_cron_hours_explicit:",
            "    if not settings.check_cron_hours_explicit:",
            "explicit cron precedence inverted",
        ),
        (
            "10",
            "        sync_svc.sync_all_sensors(hours=6)",
            "        sync_svc.sync_all_sensors(hours=24)",
            "sync job window 6h -> 24h",
        ),
        (
            "11",
            "        irrigation_svc.check_all_clusters()\n        session.commit()",
            "        irrigation_svc.check_all_clusters()",
            "check job never commits",
        ),
        (
            "12",
            '    return hasattr(job, "next_run_time") and job.next_run_time is None',
            '    return hasattr(job, "next_run_time") and job.next_run_time is not None',
            "_is_paused inverted",
        ),
        (
            "13",
            '                "core": job.id in _CORE_JOB_IDS,',
            '                "core": False,',
            "core flag dropped from job listing",
        ),
        (
            "14",
            "    if job_id in _CORE_JOB_IDS:\n        raise CoreJobError(",
            "    if job_id in ():\n        raise CoreJobError(",
            "core jobs deletable",
        ),
        ("15", "    repo.update_preferences(scheduler_paused=paused)\n", "", "pause not persisted"),
        (
            "16",
            "    if paused:\n        scheduler.pause_job(CHECK_ALL_JOB_ID)\n    else:\n        scheduler.resume_job(CHECK_ALL_JOB_ID)",
            "    if not paused:\n        scheduler.pause_job(CHECK_ALL_JOB_ID)\n    else:\n        scheduler.resume_job(CHECK_ALL_JOB_ID)",
            "pause/resume swapped",
        ),
        ("17", "    if not persisted_paused:\n        return\n", "", "persisted 'not paused' still pauses"),
        (
            "18",
            '        next_run = getattr(job, "next_run_time", None) if running else None',
            '        next_run = getattr(job, "next_run_time", None)',
            "next_run shown while stopped",
        ),
        (
            "19",
            '    except ZoneInfoNotFoundError:\n        return ZoneInfo("UTC")',
            '    except ZoneInfoNotFoundError:\n        return ZoneInfo("Europe/Rome")',
            "bad tz fallback",
        ),
        (
            "20",
            "        monitor.bind_repo(repo)\n        monitor.poll_all()",
            "        monitor.poll_all()",
            "health job does not bind repo",
        ),
        ("21", "            monitor.backfill_from_history()\n", "", "startup backfill skipped"),
        (
            "22",
            '    if (tz_name or "UTC") == get_display_timezone():\n        return\n',
            "",
            "tz preference re-applied when unchanged",
        ),
        (
            "23",
            "        if monitor is not None:\n            monitor.bind_repo(repo)\n        irrigation_svc",
            "        irrigation_svc",
            "check job does not bind health monitor repo",
        ),
        (
            "24",
            "    _shutdown_event.set()\n    if scheduler.running:",
            "    if scheduler.running:",
            "stop_scheduler does not signal shutdown",
        ),
    ],
)

_IRR = SRV + "services/irrigation.py"
add(
    "irrigation",
    "irrigation",
    _IRR,
    [
        (
            "01",
            '            if sensor_data and sensor_data.get("temperature") is not None:\n                return sensor_data["temperature"], "sensor", sensor_data',
            '            if False:\n                return sensor_data["temperature"], "sensor", sensor_data',
            "indoor ignores sensor temperature",
        ),
        (
            "02",
            '        return 20.0, "fallback (20C)", sensor_data',
            '        return 18.0, "fallback (20C)", sensor_data',
            "fallback temperature 20 -> 18",
        ),
        (
            "03",
            "        sensor_data = None if no_sync else self._sync.ensure_fresh_and_read(cluster_id)",
            "        sensor_data = self._sync.ensure_fresh_and_read(cluster_id)",
            "no_sync ignored",
        ),
        (
            "04",
            '            triggered_by=TRIGGERED_BY_MANUAL if force else TRIGGERED_BY_AUTO,',
            '            triggered_by=TRIGGERED_BY_AUTO,',
            "force no longer logs manual trigger",
        ),
        (
            "05",
            "            bypass_quiet_hours=force,\n        )",
            "            bypass_quiet_hours=False,\n        )",
            "force no longer bypasses quiet hours",
        ),
        (
            "06",
            '        if dry_run or decision.action.value == "skip":\n            if not dry_run:',
            '        if dry_run or decision.action.value == "skip":\n            if dry_run:',
            "skip activity only on dry run",
        ),
        (
            "07",
            '        if self._registry is None:\n            result["action"] = "error"\n            result["reason"] = "no device registry"',
            '        if self._registry is None:\n            result["action"] = "error"\n            result["reason"] = "no registry"',
            "no-registry reason text",
        ),
        (
            "08",
            "            if blocked:\n                primary = blocking_alarms[0]",
            "            if False:\n                primary = blocking_alarms[0]",
            "device-health gate disabled",
        ),
        (
            "09",
            "                decision.action = Action.SKIP\n                self._repo.add_activity_event(",
            "                self._repo.add_activity_event(",
            "health block keeps decision action",
            0,
            "after the health block the decision object is discarded: result['action'] is set to 'skip' "
            "independently and the decision is not re-persisted (that is bug B-6), so decision.action is never read",
        ),
        (
            "10",
            '            action=EVENT_ACTION_START if success else EVENT_ACTION_ATTEMPTED,',
            '            action=EVENT_ACTION_START,',
            "failed start recorded as start",
        ),
        (
            "11",
            "            if decision.decision_log_id is not None:\n                self._repo.set_decision_actuated(decision.decision_log_id)\n",
            "",
            "decision log never flipped to actuated (invariant #6)",
        ),
        (
            "12",
            "            _schedule_leak_check(cluster_id, started_at)\n",
            "",
            "leak check not scheduled after auto start",
        ),
        (
            "13",
            '            schedule_pump_watcher(irrigator.id, duration, started_at)\n            result["action"] = "irrigated"',
            '            result["action"] = "irrigated"',
            "pump watcher not scheduled",
        ),
        (
            "14",
            '            raise_alert(\n                self._repo,\n                source="irrigation",\n                code="actuation_failed",',
            '            (lambda *a, **k: None)(\n                self._repo,\n                source="irrigation",\n                code="actuation_failed",',
            "actuation failure raises no alert",
        ),
        (
            "15",
            "            elif latest_soil < t_min - 15:",
            "            elif latest_soil < t_min - 10:",
            "monitor very_dry margin 15 -> 10",
        ),
        (
            "16",
            "            elif latest_soil > t_max + 10:",
            "            elif latest_soil > t_max:",
            "monitor wet margin removed",
        ),
        (
            "17",
            '            if not effective["auto_run"]["value"]:',
            '            if effective["auto_run"]["value"] is False and False:',
            "auto_run=false ignored",
        ),
        (
            "18",
            "                if stale is not None:\n                    self._repo.resolve_alert(stale.id)\n",
            "",
            "check_failed alert never resolved",
        ),
        (
            "19",
            "                result = self.check_cluster(cluster_id)\n                stale = self._repo.get_active_alert(CHECK_FAILED_ALERT_CODE, cluster_id=cluster_id)\n                if stale is not None:\n                    self._repo.resolve_alert(stale.id)\n                session.commit()",
            "                result = self.check_cluster(cluster_id)\n                stale = self._repo.get_active_alert(CHECK_FAILED_ALERT_CODE, cluster_id=cluster_id)\n                if stale is not None:\n                    self._repo.resolve_alert(stale.id)",
            "per-cluster commit removed (isolation)",
        ),
        (
            "20",
            '                    severity="error",\n                    entity_type="cluster",',
            '                    severity="warning",\n                    entity_type="cluster",',
            "check_failed severity",
        ),
        (
            "21",
            "    due = started_at + LEAK_CHECK_DELAY_SECONDS\n    scheduler.add_job(",
            "    due = started_at\n    scheduler.add_job(",
            "leak check due immediately (not +30 min)",
        ),
        (
            "22",
            '                if event.action != "start" or event.triggered_by != "auto":',
            '                if event.action != "start":',
            "manual starts re-armed for leak checks",
        ),
        (
            "23",
            "                _add_leak_check_job(irrigator.cluster_id, event.timestamp, run_at=max(due, now))",
            "                _add_leak_check_job(irrigator.cluster_id, event.timestamp, run_at=due)",
            "re-arm schedules in the past",
        ),
        (
            "24",
            "        if event.code not in _LEAK_CHECK_DONE_CODES or not event.payload_json:",
            "        if event.code != LEAK_CHECK_ACTIVITY_CODE or not event.payload_json:",
            "leak_hold no longer marks check done",
        ),
        (
            "25",
            "    if duration_minutes <= 0:\n        return False",
            "    if duration_minutes < 0:\n        return False",
            "watcher scheduled for 0 minutes",
        ),
        (
            "26",
            "        if settings is not None and not settings.pump_watcher_enabled:",
            "        if settings is not None and settings.pump_watcher_enabled is None:",
            "pump_watcher_enabled ignored",
        ),
        (
            "27",
            "        duration_seconds = int(duration_minutes * 60)\n\n        def _run",
            "        duration_seconds = int(duration_minutes)\n\n        def _run",
            "watcher duration minutes as seconds",
        ),
        (
            "28",
            '                if result["outcome"] == "interrupted":',
            '                if result["outcome"] == "tripped":',
            "interrupted policy keyed on wrong outcome",
        ),
        (
            "29",
            '    if triggered_by == "auto":\n        stop_msg = ""',
            '    if triggered_by != "manual" and False:\n        stop_msg = ""',
            "auto cycles not stopped on shutdown",
        ),
        (
            "30",
            '                triggered_by="shutdown",',
            '                triggered_by="auto",',
            "shutdown stop event trigger",
        ),
        (
            "31",
            '            "auto",\n                lambda: self._notifier.notify_irrigation(',
            '            "manual",\n                lambda: self._notifier.notify_irrigation(',
            "auto push gated by the manual preference",
        ),
        (
            "32",
            '                message=f"irrigated for {duration}min (confidence={decision.confidence:.0%})",',
            '                message=f"irrigated for {duration}min",',
            "irrigated activity message",
        ),
        (
            "33",
            "            readings = clean_readings_desc(self._repo.get_recent_readings(sensor.id, hours=2))",
            "            readings = self._repo.get_recent_readings(sensor.id, hours=2)",
            "monitor reads raw readings",
        ),
        (
            "34",
            '        is_indoor = cluster.environment == "indoor"',
            '        is_indoor = cluster.environment != "outdoor"',
            "non-outdoor envs treated as indoor for temperature",
        ),
    ],
)

_LK = SRV + "services/leak.py"
add(
    "leak",
    "leak",
    _LK,
    [
        (
            "01",
            "        if len(after) < LEAK_MIN_AFTER_SAMPLES:\n            return None",
            "        if len(after) < LEAK_MIN_AFTER_SAMPLES - 1:\n            return None",
            "min after samples 3 -> 2",
        ),
        (
            "02",
            "all(v > LEAK_PINNED_THRESHOLD for v in tail)",
            "all(v >= LEAK_PINNED_THRESHOLD for v in tail)",
            "pinned threshold > -> >=",
        ),
        (
            "03",
            "all(v > LEAK_PINNED_THRESHOLD for v in tail)",
            "any(v > LEAK_PINNED_THRESHOLD for v in tail)",
            "pinned all -> any",
        ),
        (
            "04",
            "        if len(before) < LEAK_MIN_BEFORE_SAMPLES:\n            return None",
            "        if len(before) < 1:\n            return None",
            "min before samples 2 -> 1",
        ),
        (
            "05",
            "        baseline = statistics.median(before)",
            "        baseline = min(before)",
            "baseline median -> min",
        ),
        (
            "06",
            "still_climbing = last_after >= peak_after - LEAK_SETTLE_TOLERANCE",
            "still_climbing = last_after >= peak_after",
            "settle tolerance dropped from still_climbing",
        ),
        (
            "07",
            "rose_through_window = last_after - first_after > LEAK_SETTLE_TOLERANCE",
            "rose_through_window = last_after - first_after >= 0",
            "rose_through_window threshold",
        ),
        (
            "08",
            "far_above_baseline = last_after - baseline >= LEAK_RISING_DELTA",
            "far_above_baseline = last_after - baseline > LEAK_RISING_DELTA",
            "rising delta >= -> >",
        ),
        (
            "09",
            "        if still_climbing and rose_through_window and far_above_baseline:",
            "        if still_climbing and far_above_baseline:",
            "rose_through_window not required",
        ),
        (
            "10",
            "            if reason is None:\n                self._clear_sensor(cluster_id, sensor)\n                continue",
            "            if reason is None:\n                continue",
            "settled sensor no longer releases the hold",
        ),
        (
            "11",
            '            code=LEAK_ALERT_CODE,\n            severity="critical",',
            '            code=LEAK_ALERT_CODE,\n            severity="warning",',
            "leak alert severity critical -> warning",
        ),
        (
            "12",
            '            if alert.code != LEAK_ALERT_CODE or alert.status == "resolved":',
            "            if alert.code != LEAK_ALERT_CODE:",
            "resolved alerts re-resolved",
        ),
        (
            "13",
            "            if alert.entity_type != ENTITY_SENSOR or alert.entity_id != sensor.id:\n                continue\n",
            "",
            "clearing one sensor resolves every leak alert",
        ),
        (
            "14",
            '                    "hold_until": now + LEAK_HOLD_HOURS * SECONDS_PER_HOUR,',
            '                    "hold_until": now + LEAK_HOLD_HOURS * 60,',
            "hold_until in minutes",
        ),
        (
            "15",
            "        if alerts:\n            now = int(time.time())",
            "        if alerts is not None:\n            now = int(time.time())",
            "leak_hold activity written even with no findings",
        ),
        (
            "16",
            '        message = f"{sensor.name}: soil moisture {reason} (latest={latest:.1f}%)"',
            '        message = f"{sensor.name}: soil moisture {reason}"',
            "alert message drops latest value",
        ),
    ],
)
_MC = SRV + "services/manual_control.py"
add(
    "manual",
    "manual",
    _MC,
    [
        (
            "01",
            "        if total_starts >= config.max_events_per_day:",
            "        if total_starts > config.max_events_per_day:",
            "max events >= -> >",
        ),
        (
            "02",
            "        if minutes_used + requested > config.daily_cap_minutes:",
            "        if minutes_used + requested >= config.daily_cap_minutes:",
            "daily cap > -> >=",
        ),
        (
            "03",
            '        minutes_used = sum(e.duration_minutes or 0 for e in recent if e.action == EVENT_ACTION_START)',
            "        minutes_used = sum(e.duration_minutes or 0 for e in recent)",
            "daily cap counts non-start events",
        ),
        (
            "04",
            '    if registry is None:  # checked before the caps, as the API always has\n        raise ManualActionError(503, "No device registry (missing Tuya credentials)")\n    check_rate_limits',
            "    check_rate_limits",
            "caps checked before registry (409 instead of 503)",
        ),
        (
            "05",
            "    if not success:\n        raise ManualActionError(502, output)\n\n    started_at",
            "    started_at",
            "failed start still recorded",
        ),
        (
            "06",
            '    if minutes:\n        schedule_pump_watcher(irrigator.id, minutes, started_at, triggered_by=TRIGGERED_BY_MANUAL)',
            "    if minutes:\n        schedule_pump_watcher(irrigator.id, minutes, started_at)",
            "manual watcher marked auto (stopped on shutdown)",
        ),
        (
            "07",
            '        action="off",\n        triggered_by="manual",',
            '        action="stop",\n        triggered_by="manual",',
            "manual stop event action off -> stop",
        ),
        (
            "08",
            '        notes=notes or f"Manual ({minutes} min)",',
            '        notes=notes or "Manual",',
            "manual log default note",
        ),
        (
            "09",
            "    check_rate_limits(repo, irrigator.cluster_id, irrigator.id, minutes)\n    event_id",
            "    event_id",
            "manual log skips caps",
        ),
        (
            "10",
            '        notes=f"Manual start via {via} ({minutes} min)" if minutes else f"Manual start via {via}",',
            '        notes=f"Manual start via {via}",',
            "manual start note drops minutes",
        ),
        ("11", "    requested = minutes or 0", "    requested = minutes or 1", "None minutes counts as 1"),
    ],
)
_SS = SRV + "services/sync.py"
add(
    "svcsync",
    "sync",
    _SS,
    [
        (
            "01",
            "now - latest[s.id].timestamp > SENSOR_READING_STALE_SECONDS",
            "now - latest[s.id].timestamp >= SENSOR_READING_STALE_SECONDS",
            "stale boundary > -> >=",
        ),
        (
            "02",
            "                    sync_single_sensor(self._repo, self._gateway, sensor, hours=6)",
            "                    sync_single_sensor(self._repo, self._gateway, sensor, hours=24)",
            "freshness sync window 6h -> 24h",
        ),
        (
            "03",
            '            "soil_moisture": min(soils) if soils else None,',
            '            "soil_moisture": statistics.mean(soils) if soils else None,',
            "snapshot soil min -> mean (invariant #2)",
        ),
        (
            "04",
            '            "light": max(lights) if lights else None,',
            '            "light": min(lights) if lights else None,',
            "snapshot light max -> min",
        ),
        (
            "05",
            "            self._repo.flush()\n",
            "",
            "no flush after freshness sync",
            0,
            "every write sync_single_sensor makes goes through repo.add_sensor_reading, which already calls "
            "session.flush() after each insert (and the session autoflushes before the snapshot SELECT)",
        ),
        (
            "06",
            "            readings = clean_readings_desc(self._repo.get_recent_readings(sensor.id, hours=SNAPSHOT_LOOKBACK_HOURS))",
            "            readings = self._repo.get_recent_readings(sensor.id, hours=SNAPSHOT_LOOKBACK_HOURS)",
            "snapshot uses raw readings",
        ),
        (
            "07",
            "        if stale and self._gateway is not None:\n            for sensor in stale:",
            "        if self._gateway is not None:\n            for sensor in sensors:",
            "every sensor force-synced (not only stale)",
        ),
        (
            "08",
            '            return {"total_synced": 0, "total_new": 0, "total_live": 0, "errors": ["No cloud connection"]}',
            '            return {"total_synced": 0, "total_new": 0, "total_live": 0, "errors": []}',
            "no-cloud error dropped",
        ),
    ],
)
_HM = SRV + "services/health_monitor.py"
add(
    "health",
    "health",
    _HM,
    [
        (
            "01",
            "            if state.battery_pct < self._battery_critical_pct:",
            "            if state.battery_pct <= self._battery_critical_pct:",
            "battery critical < -> <=",
        ),
        (
            "02",
            "            elif state.battery_pct < self._battery_low_pct:",
            "            elif state.battery_pct <= self._battery_low_pct:",
            "battery low < -> <=",
        ),
        (
            "03",
            "            if now - state.last_seen_ts > self._offline_after_seconds:",
            "            if now - state.last_seen_ts >= self._offline_after_seconds:",
            "offline staleness > -> >=",
        ),
        (
            "04",
            "if alarm in (HealthAlarm.NO_WATER, HealthAlarm.RAIN_DETECTED, HealthAlarm.DEVICE_OFFLINE)",
            "if alarm in (HealthAlarm.NO_WATER, HealthAlarm.RAIN_DETECTED)",
            "offline no longer blocks actuation",
        ),
        (
            "05",
            "        for alarm in disappeared:\n            self._resolve_health_alert(entity_type=entity_type, entity_id=entity_id, alarm=alarm)\n",
            "",
            "cleared alarms never resolve their alert",
        ),
        (
            "06",
            '    return f"health:{entity_type}:{entity_id}:{alarm.value}"',
            '    return f"health:{entity_id}:{alarm.value}"',
            "dedup key drops entity type",
        ),
        ("07", '    HealthAlarm.NO_WATER: "critical",', '    HealthAlarm.NO_WATER: "warning",', "no-water severity"),
        (
            "08",
            "            recent = readings[:window]",
            "            recent = readings[-window:]",
            "backfill reads oldest window (DESC)",
        ),
        (
            "09",
            "            if all(r.water_warning is True for r in recent):",
            "            if any(r.water_warning is True for r in recent):",
            "sensor fault backfill all -> any",
        ),
        (
            "10",
            "if state.signal_quality is not None and state.signal_quality < self._signal_loss_threshold:",
            "if state.signal_quality is not None and state.signal_quality <= self._signal_loss_threshold:",
            "signal loss < -> <=",
        ),
        (
            "11",
            "            notify_if_new_alert(self._repo, self._notifier, alert)\n",
            "",
            "health alerts never notified",
        ),
        (
            "12",
            "        self._offline_after_seconds = offline_after_minutes * 60",
            "        self._offline_after_seconds = offline_after_minutes * 30",
            "offline window halved",
        ),
        (
            "13",
            '    return raw.strip().lower() == "low"',
            '    return raw == "low"',
            "low battery state case/space-sensitive",
        ),
        (
            "14",
            "        if state.offline:\n            derived.add(HealthAlarm.DEVICE_OFFLINE)\n        elif",
            "        if False:\n            derived.add(HealthAlarm.DEVICE_OFFLINE)\n        elif",
            "explicit offline flag ignored",
        ),
    ],
)
_PW = SRV + "services/pump_watcher.py"
add(
    "pump",
    "pump",
    _PW,
    [
        (
            "01",
            "if HealthAlarm.NO_WATER in state.alarms and now >= warmup_until:",
            "if HealthAlarm.NO_WATER in state.alarms:",
            "warm-up window ignored",
        ),
        ("02", "            if now >= deadline:", "            if now > deadline:", "deadline >= -> >"),
        (
            "03",
            "                if consecutive_failures >= self._max_read_failures:",
            "                if consecutive_failures > self._max_read_failures:",
            "abandon threshold >= -> >",
        ),
        (
            "04",
            "            if read_error or state.offline:",
            "            if read_error:",
            "offline reads not counted as failures",
        ),
        ("05", "            stop_ok, stop_msg = adapter.stop(irrigator)\n", "", "trip does not stop the pump"),
        (
            "06",
            "                action=EVENT_ACTION_ABORTED,",
            '                action="stop",',
            "aborted event action",
        ),
        ("07", "            if self._stop_requested():\n", "            if False:\n", "shutdown signal ignored"),
        (
            "08",
            "        self._poll = max(0.1, float(poll_seconds))",
            "        self._poll = float(poll_seconds)",
            "poll floor removed",
        ),
        ("09", "                consecutive_failures = 0\n", "", "failure counter not reset on good read"),
        (
            "10",
            "                monitor.record(\n",
            "                (lambda *a, **k: None)(\n",
            "trip not routed through health monitor",
        ),
        (
            "11",
            '            self._repo.commit()\n        except Exception:\n            logger.exception("Failed to commit pump dry-run',
            '            pass\n        except Exception:\n            logger.exception("Failed to commit pump dry-run',
            "trip side effects not committed",
        ),
        ("12", '            "alarm_dp": 105,', '            "alarm_dp": 102,', "payload alarm DP"),
    ],
)

_REPO = CORE + "repository.py"
add(
    "repository",
    "repository",
    _REPO,
    [
        (
            "01",
            "                .where(SensorReading.sensor_id == sensor_id, SensorReading.timestamp >= cutoff)\n                .order_by(SensorReading.timestamp.desc())",
            "                .where(SensorReading.sensor_id == sensor_id, SensorReading.timestamp >= cutoff)\n                .order_by(SensorReading.timestamp.asc())",
            "get_recent_readings DESC -> ASC",
        ),
        (
            "02",
            "                .where(SensorReading.sensor_id == sensor_id, SensorReading.timestamp >= cutoff)",
            "                .where(SensorReading.sensor_id == sensor_id, SensorReading.timestamp > cutoff)",
            "readings cutoff >= -> >",
        ),
        (
            "03",
            "                .where(IrrigationEvent.irrigator_id == irrigator_id, IrrigationEvent.timestamp >= cutoff)",
            "                .where(IrrigationEvent.irrigator_id == irrigator_id, IrrigationEvent.timestamp > cutoff)",
            "events cutoff >= -> > (cooldown edge)",
        ),
        (
            "04",
            "                .order_by(IrrigationEvent.timestamp.desc())\n            )\n        )\n\n    def irrigator_consumption_liters",
            "                .order_by(IrrigationEvent.timestamp.asc())\n            )\n        )\n\n    def irrigator_consumption_liters",
            "get_recent_events DESC -> ASC",
        ),
        (
            "05",
            "                IrrigationEvent.action == EVENT_ACTION_START,\n                IrrigationEvent.timestamp >= since,",
            "                IrrigationEvent.timestamp >= since,",
            "consumption counts non-start events",
        ),
        (
            "06",
            "            select(VacationWindow).where(VacationWindow.starts_at <= now, VacationWindow.ends_at >= now)",
            "            select(VacationWindow).where(VacationWindow.starts_at <= now, VacationWindow.ends_at > now)",
            "active vacation end inclusive -> exclusive",
        ),
        (
            "07",
            '            .where(Alert.code == code, Alert.status != "resolved")',
            '            .where(Alert.code == code, Alert.status == "open")',
            "acknowledged alerts no longer active (leak hold)",
        ),
        (
            "08",
            "            stmt = stmt.where(Alert.last_seen_at >= since)\n        return self.session.scalar(stmt)",
            "            stmt = stmt.where(Alert.last_seen_at > since)\n        return self.session.scalar(stmt)",
            "active alert since >= -> >",
        ),
        ("09", "            existing.occurrence_count += 1\n", "", "upsert does not bump occurrence_count"),
        (
            "10",
            '            if existing.status == "resolved":\n                existing.status = "open"\n                existing.resolved_at = None\n',
            "",
            "resolved alert not reopened on upsert",
        ),
        (
            "11",
            '        if alert.status == "open":\n            alert.status = "acknowledged"',
            '        if alert.status != "acknowledged":\n            alert.status = "acknowledged"',
            "resolved alerts can be acknowledged",
        ),
        (
            "12",
            "                .order_by(DecisionLog.evaluated_at.desc())",
            "                .order_by(DecisionLog.evaluated_at.asc())",
            "decision logs oldest first",
        ),
        (
            "13",
            '            if cluster_value is not None:\n                out[field] = {"value": cluster_value, "source": "cluster"}',
            '            if cluster_value:\n                out[field] = {"value": cluster_value, "source": "cluster"}',
            "cluster config 0/False falls through to global",
        ),
        (
            "14",
            '            if global_value is not None:\n                out[field] = {"value": global_value, "source": "global"}\n                continue\n',
            "",
            "global config layer skipped",
        ),
        (
            "15",
            "            payload_json=json.dumps(payload, default=str),\n            triggered_by=triggered_by,",
            "            payload_json=json.dumps(payload, default=str, sort_keys=True),\n            triggered_by=triggered_by,",
            "decision payload keys re-ordered",
        ),
        (
            "16",
            '            .on_conflict_do_nothing(index_elements=["sensor_id", "timestamp"])\n        )\n        result = self.session.execute(stmt)\n        self.session.flush()\n        if result.rowcount > 0:',
            '            .on_conflict_do_nothing(index_elements=["sensor_id", "timestamp"])\n        )\n        result = self.session.execute(stmt)\n        self.session.flush()\n        if result.rowcount >= 0:',
            "duplicate reading reported as inserted",
        ),
        (
            "17",
            "            .order_by(SensorReading.timestamp.desc())\n            .limit(1)",
            "            .order_by(SensorReading.timestamp.asc())\n            .limit(1)",
            "get_latest_reading returns oldest",
        ),
        (
            "18",
            "                    SensorReading.timestamp <= timestamp,\n                )",
            "                    SensorReading.timestamp < timestamp,\n                )",
            "readings_around before excludes the event instant",
        ),
        (
            "19",
            "        stmt = select(Alert).order_by(Alert.last_seen_at.desc(), Alert.id.desc()).limit(limit)",
            "        stmt = select(Alert).order_by(Alert.last_seen_at.asc(), Alert.id.desc()).limit(limit)",
            "alert inbox oldest first",
        ),
        (
            "20",
            '        return self.session.scalar(select(func.count()).select_from(Alert).where(Alert.status == "open")) or 0',
            '        return self.session.scalar(select(func.count()).select_from(Alert).where(Alert.status != "resolved")) or 0',
            "open-alert badge counts acknowledged",
        ),
        ("21", "            existing.severity = severity\n", "", "upsert keeps old severity"),
    ],
)
add(
    "constants",
    "engine",
    CORE + "constants.py",
    [
        ("01", "MIN_COOLDOWN_HOURS = 6", "MIN_COOLDOWN_HOURS = 5", "cooldown 6 -> 5 h"),
        ("02", "LEAK_HOLD_HOURS = 24", "LEAK_HOLD_HOURS = 12", "leak hold 24 -> 12 h"),
        ("03", "VERY_DRY_MARGIN = 10", "VERY_DRY_MARGIN = 15", "very-dry margin 10 -> 15"),
    ],
)

add(
    "auth",
    "auth",
    CORE + "auth.py",
    [
        (
            "01",
            '    if not plain:\n        raise ValueError("password must be non-empty")\n',
            "",
            "empty password accepted",
        ),
        (
            "02",
            "    except (VerifyMismatchError, InvalidHashError):\n        return False",
            "    except VerifyMismatchError:\n        return False",
            "malformed hash raises instead of False",
        ),
        (
            "03",
            "    except InvalidHashError:\n        return True",
            "    except InvalidHashError:\n        return False",
            "invalid hash needs no rehash",
        ),
        (
            "04",
            "    return session.scalars(select(User).where(User.username == username)).first()",
            "    return session.scalars(select(User).where(User.username.ilike(username))).first()",
            "username lookup case-insensitive",
        ),
        (
            "05",
            "        is_active=True,\n        created_at",
            "        is_active=False,\n        created_at",
            "new users inactive",
        ),
        ("06", "    user.last_login_at = int(time.time())\n", "    pass\n", "login not stamped"),
    ],
)
_SA = SRV + "auth.py"
add(
    "auth",
    "auth",
    _SA,
    [
        (
            "07",
            "    exp = iat + settings.auth_token_ttl_minutes * 60",
            "    exp = iat + settings.auth_token_ttl_minutes * 3600",
            "token TTL minutes -> hours",
        ),
        (
            "08",
            '            options={"require": ["sub", "iat", "exp", "aud"]},',
            '            options={"require": ["sub"]},',
            "required JWT claims relaxed",
        ),
        (
            "09",
            "            audience=JWT_AUDIENCE,\n            options",
            "            options",
            "audience not verified",
        ),
        (
            "10",
            "    if creds and creds.credentials:\n        return creds.credentials\n    cookie",
            "    cookie",
            "bearer header ignored (cookie only)",
        ),
        (
            "11",
            '    if user is None or not user.is_active:\n        raise AuthError("User no longer active")',
            '    if user is None:\n        raise AuthError("User no longer active")',
            "inactive users accepted",
        ),
        (
            "12",
            "    return hmac.compare_digest(token, expected)",
            "    return token.startswith(expected)",
            "MCP token prefix match",
        ),
        (
            "13",
            "    if _is_mcp_token(token, settings):\n        return AuthenticatedUser(id=MCP_USER_ID",
            "    if False:\n        return AuthenticatedUser(id=MCP_USER_ID",
            "MCP token not accepted on /api/v1",
        ),
        (
            "14",
            "    if not settings.auth_enabled:\n        return AuthenticatedUser(id=SYSTEM_USER_ID, username=SYSTEM_USER_NAME, is_system=True)\n    token = _extract_token",
            "    token = _extract_token",
            "auth-disabled mode still requires a token",
        ),
        (
            "15",
            '    if request.url.query:\n        next_url += "?" + request.url.query',
            '    if False:\n        next_url += "?" + request.url.query',
            "login redirect drops query string",
        ),
        (
            "16",
            '    if request.headers.get("HX-Request", "").lower() == "true":',
            '    if request.headers.get("HX-Request", "") == "True":',
            "HX redirect detection case-sensitive",
        ),
        (
            "17",
            '        httponly=True,\n        samesite="lax",',
            '        httponly=False,\n        samesite="lax",',
            "session cookie not HttpOnly",
        ),
        (
            "18",
            "    if not verify_password(password, user.hashed_password):\n        return None\n",
            "",
            "authenticate skips password check",
        ),
        (
            "19",
            "    if user is None or not user.is_active:\n        return None\n    if not verify_password",
            "    if user is None:\n        return None\n    if not verify_password",
            "authenticate accepts inactive users",
        ),
        ("20", "        if existing is not None:\n            return\n", "", "bootstrap runs even when users exist"),
    ],
)
add(
    "auth",
    "auth",
    SRV + "app.py",
    [
        (
            "21",
            "    if settings.mcp_token is None:\n        raise HTTPException(",
            "    if False:\n        raise HTTPException(",
            "MCP fail-open when token unset",
        ),
        (
            "22",
            "    if creds is None or creds.credentials != settings.mcp_token:",
            "    if creds is None:",
            "wrong MCP token accepted",
        ),
        (
            "23",
            '            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,\n            detail="MCP auth not configured",',
            '            status_code=status.HTTP_401_UNAUTHORIZED,\n            detail="MCP auth not configured",',
            "unset MCP token 503 -> 401",
        ),
    ],
)

_FIL = SRV + "web/filters.py"
add(
    "web",
    "web",
    _FIL,
    [
        (
            "01",
            '    if delta < 60:\n        return f"{delta}s ago"',
            '    if delta <= 60:\n        return f"{delta}s ago"',
            "age_seconds 60 s boundary",
        ),
        ("02", "_AGE_STALE_SECONDS = 7 * 86400", "_AGE_STALE_SECONDS = 30 * 86400", "stale age 7d -> 30d"),
        ("03", '    cleaned = re.sub(r"\\s*;\\s*", "; ", cleaned)\n', "", "strip_emoji keeps ' ; ' spacing"),
        (
            "04",
            "    if value is None or lo is None or hi is None or hi <= lo:",
            "    if value is None or lo is None or hi is None or hi < lo:",
            "stat_position degenerate band",
        ),
        (
            "05",
            '    if target_min is not None and value < target_min:\n        return "low"',
            '    if target_min is not None and value <= target_min:\n        return "low"',
            "moisture_badge low boundary",
        ),
        (
            "06",
            '    return {"critical": "danger", "warning": "warning", "info": "info"}.get((severity or "").lower(), "muted")',
            '    return {"critical": "danger", "warning": "warning", "info": "info"}.get(severity or "", "muted")',
            "severity_class case-sensitive",
        ),
        ("07", '    return f"{h}h {m}m" if m else f"{h}h"', '    return f"{h}h {m}m"', "format_minutes whole hours"),
        ("08", '    "cooldown": "clock",', '    "cooldown": "info",', "cooldown icon"),
        (
            "09",
            '    if has_irrigator:\n        tier = "operational"',
            '    if has_irrigator and has_sensors:\n        tier = "operational"',
            "cluster tier requires sensors",
        ),
        (
            "10",
            '    if not has_irrigator:\n        missing.append("irrigator")\n    if not has_sensors:\n        missing.append("sensors")',
            '    if not has_sensors:\n        missing.append("sensors")\n    if not has_irrigator:\n        missing.append("irrigator")',
            "missing-capability order",
        ),
    ],
)
add(
    "web",
    "web",
    SRV + "routes/operations.py",
    [
        (
            "11",
            '    if result.get("action") == "error" and result.get("reason") == "cluster not found":\n        raise HTTPException(status_code=404',
            '    if result.get("action") == "error" and result.get("reason") == "not found":\n        raise HTTPException(status_code=404',
            "irrigate 404 mapping broken",
        ),
        (
            "12",
            "        force=request.force,\n    )\n    if result.get",
            "        force=False,\n    )\n    if result.get",
            "API force flag dropped",
        ),
        (
            "13",
            '    has_alerts = any(r.get("alerts") or r.get("maintenance") or r.get("needs_water") for r in results)',
            '    has_alerts = any(r.get("alerts") for r in results)',
            "has_alerts ignores maintenance/needs_water",
        ),
        (
            "14",
            "    require_cluster(repo, cluster_id)\n    result = irrigation_svc.check_cluster(cluster_id)",
            "    result = irrigation_svc.check_cluster(cluster_id)",
            "check_single skips 404 guard",
        ),
    ],
)
add(
    "web",
    "web",
    SRV + "routes/vacation.py",
    [
        (
            "15",
            "    if effective_start >= effective_end:",
            "    if effective_start > effective_end:",
            "vacation update allows start == end",
        ),
        (
            "16",
            "    effective_end = request.ends_at if request.ends_at is not None else row.ends_at",
            "    effective_end = request.ends_at if request.ends_at else row.ends_at",
            "ends_at=0 treated as absent",
        ),
    ],
)
add(
    "web",
    "web",
    SRV + "routes/scheduler.py",
    [
        (
            "17",
            "    except sched.CoreJobError as exc:\n        raise HTTPException(status_code=409",
            "    except sched.CoreJobError as exc:\n        raise HTTPException(status_code=403",
            "core job delete 409 -> 403",
        ),
        ("18", '        verb = "pause" if paused else "resume"', '        verb = "pause"', "resume error verb"),
    ],
)
add(
    "web",
    "web",
    SRV + "web/routes/operations.py",
    [
        (
            "19",
            '    forced = force.strip().lower() in ("true", "on", "1")',
            '    forced = force.strip() == "true"',
            "web force accepts only 'true'",
        ),
        (
            "20",
            '    has_alerts = any(r.get("alerts") for r in results)\n    return templates.TemplateResponse(\n        request, "partials/_check_result.html"',
            '    has_alerts = False\n    return templates.TemplateResponse(\n        request, "partials/_check_result.html"',
            "web check_all never flags alerts",
        ),
        (
            "21",
            "        no_sync=bool(no_sync),\n        force=forced,",
            "        no_sync=True,\n        force=forced,",
            "web irrigate always no_sync",
        ),
    ],
)
add(
    "web",
    "web",
    SRV + "web/context.py",
    [
        (
            "22",
            '            theme = prefs.theme or "auto"',
            '            theme = prefs.theme or "light"',
            "theme fallback",
        ),
        (
            "23",
            '    return request.headers.get("HX-Request", "").lower() == "true"',
            '    return "HX-Request" in request.headers',
            "is_hx presence-only",
        ),
        ("24", "            active_vacation = repo.get_active_vacation()\n", "", "vacation banner never shown"),
    ],
)

_CL = CLI + "client.py"
add(
    "cli",
    "cli",
    _CL,
    [
        (
            "01",
            '    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")',
            '    base = str(Path.home() / ".config")',
            "XDG_CONFIG_HOME ignored",
        ),
        ("02", "        return env_token.strip() or None", "        return env_token", "env token not stripped"),
        ("03", "    os.chmod(path, 0o600)", "    os.chmod(path, 0o644)", "token file world-readable"),
        (
            "04",
            "        resolved = token if token is not None else load_stored_token()",
            "        resolved = token or load_stored_token()",
            "empty token no longer disables stored token",
        ),
        (
            "05",
            "        self.http = httpx.Client(base_url=base_url, timeout=30.0, headers=headers, **kwargs)",
            "        self.http = httpx.Client(base_url=base_url, timeout=10.0, headers=headers, **kwargs)",
            "client timeout 30 -> 10 s",
        ),
        ("06", "        if resp.status_code >= 400:", "        if resp.status_code > 400:", "400 not treated as error"),
        (
            "07",
            '                detail = resp.json().get("detail", resp.text)',
            "                detail = resp.text",
            "error detail not extracted from JSON",
        ),
        (
            "08",
            '        if resp.headers.get("content-type", "").startswith("text/csv"):\n            return {"csv": resp.text}\n',
            "",
            "CSV responses parsed as JSON",
        ),
        (
            "09",
            '        body = {"temp_override": temp_override, "dry_run": dry_run, "no_sync": no_sync, "force": force}',
            '        body = {"temp_override": temp_override, "dry_run": dry_run, "no_sync": no_sync}',
            "irrigate drops force",
        ),
        (
            "10",
            '            return self._request("POST", "/api/v1/check")',
            '            return self._request("POST", "/api/v1/clusters/None/check")',
            "check-all path",
        ),
        (
            "11",
            '        return self._request("POST", f"/api/v1/irrigators/{irrigator_id}/start", json={"minutes": minutes})',
            '        return self._request("POST", f"/api/v1/irrigators/{irrigator_id}/start", params={"minutes": minutes})',
            "start minutes sent as query param",
        ),
        (
            "12",
            "        params.update({k: v for k, v in filters.items() if v is not None})",
            "        params.update(filters)",
            "activity filters not None-filtered",
        ),
        (
            "13",
            '        return self._request("GET", f"/api/v1/clusters/{cluster_id}/history", params={"hours": hours, "limit": limit})',
            '        return self._request("GET", f"/api/v1/clusters/{cluster_id}/history", params={"hours": hours})',
            "history drops limit",
        ),
    ],
)
add(
    "cli",
    "cli",
    CLI + "commands/_helpers.py",
    [
        (
            "14",
            '    server = ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", "http://localhost:8000")',
            '    server = os.environ.get("IRRIGATION_SERVER_URL") or ctx.obj or "http://localhost:8000"',
            "env URL beats --server",
        ),
        (
            "15",
            '        typer.echo(f"Error: {e.detail}", err=True)\n        raise typer.Exit(1) from None',
            '        typer.echo(f"Error: {e.detail}", err=True)\n        raise typer.Exit(2) from None',
            "server error exit code 1 -> 2",
        ),
        (
            "16",
            '        typer.echo(f"Error: {e.detail}", err=True)',
            '        typer.echo(f"Error: {e.detail}")',
            "server error printed to stdout",
        ),
        (
            "17",
            "    print_json(json.dumps(data, default=str))",
            "    print_json(json.dumps(data, default=str, sort_keys=True))",
            "JSON output keys sorted",
        ),
    ],
)
_OPS = CLI + "commands/operations.py"
add(
    "cli",
    "cli",
    _OPS,
    [
        (
            "18",
            '        if data.get("action") == "error":\n            raise typer.Exit(1)\n\n    @app.command()\n    def check(',
            '        if data.get("action") == "error":\n            raise typer.Exit(0)\n\n    @app.command()\n    def check(',
            "irrigate error exits 0",
        ),
        (
            "19",
            '            if data.get("has_alerts"):\n                raise typer.Exit(2)\n            if data.get("action") == "error":\n                raise typer.Exit(1)',
            '            if data.get("action") == "error":\n                raise typer.Exit(1)\n            if data.get("has_alerts"):\n                raise typer.Exit(2)',
            "check exit-code precedence swapped",
        ),
        (
            "20",
            '        if data.get("needs_water"):\n            raise typer.Exit(2)',
            '        if data.get("needs_water"):\n            raise typer.Exit(1)',
            "monitor needs_water exit 2 -> 1",
        ),
        (
            "21",
            "        data = call(ctx, lambda c: c.check() if all_clusters else c.check(cluster))",
            "        data = call(ctx, lambda c: c.check(cluster) if cluster else c.check())",
            "--all no longer takes precedence over a cluster id",
        ),
        (
            "22",
            '        typer.echo("Error: provide a cluster ID or --all", err=True)\n            raise typer.Exit(1)',
            '        typer.echo("Error: provide a cluster ID or --all", err=True)\n            raise typer.Exit(2)',
            "missing target exit code",
        ),
    ],
)
add(
    "cli",
    "cli",
    CLI + "commands/irrigators.py",
    [
        (
            "23",
            '    if device_ip:\n        config["device_ip"] = device_ip',
            '    if device_ip is not None:\n        config["device_ip"] = device_ip',
            "empty --device-ip sent",
        ),
        (
            "24",
            "            config=config if config else None,",
            "            config=config,",
            "empty config sent as {}",
        ),
    ],
)
add(
    "cli",
    "cli",
    CLI + "commands/plants.py",
    [
        (
            "25",
            '            if plants:\n                output({"cluster": cl["name"], "plants": plants})',
            '            output({"cluster": cl["name"], "plants": plants})',
            "empty clusters printed in plant list",
        ),
    ],
)

_TUI = CLI + "tui/"
add(
    "tui",
    "tui",
    _TUI + "model.py",
    [
        (
            "01",
            "    summary.min_moisture = min(moistures) if moistures else None",
            "    summary.min_moisture = _mean(moistures)",
            "card moisture min -> mean (driest plant)",
        ),
        (
            "02",
            '    return (reference if reference is not None else now()) < last_event["timestamp"] + minutes * 60',
            '    return (reference if reference is not None else now()) <= last_event["timestamp"] + minutes * 60',
            "is_watering end inclusive",
        ),
        (
            "03",
            "            merged[ts] = min(value, merged.get(ts, value))",
            "            merged[ts] = max(value, merged.get(ts, value))",
            "sparkline min -> max",
        ),
        (
            "04",
            "        summary.driest = min(with_data, key=lambda p: p.moisture)",
            "        summary.driest = max(with_data, key=lambda p: p.moisture)",
            "driest plant = wettest",
        ),
        (
            "05",
            '    if not last_event or last_event.get("action") != "start":',
            "    if not last_event:",
            "is_watering ignores action",
        ),
        (
            "06",
            "    summary.newest_reading_at = max(stamps) if stamps else None",
            "    summary.newest_reading_at = min(stamps) if stamps else None",
            "newest reading = oldest",
        ),
    ],
)
add(
    "tui",
    "tui",
    _TUI + "sprites.py",
    [
        ("07", "WILT_MARGIN = 10.0", "WILT_MARGIN = 5.0", "wilt margin 10 -> 5"),
        (
            "08",
            "    if moisture >= lo + (hi - lo) / 2:",
            "    if moisture > lo + (hi - lo) / 2:",
            "thriving boundary >= -> >",
        ),
        ("09", "FALLBACK_MOISTURE_MIN = 40.0", "FALLBACK_MOISTURE_MIN = 45.0", "fallback band min 40 -> 45"),
        (
            "10",
            '    return key if key in _PLANTS else "generic"',
            "    return key",
            "unknown category not mapped to generic",
        ),
    ],
)
add(
    "tui",
    "tui",
    _TUI + "formatting.py",
    [
        (
            "11",
            '    return f"in {_span(-delta)}" if delta < 0 else f"{_span(delta)} ago"',
            '    return f"{_span(abs(delta))} ago"',
            "future timestamps shown as past",
        ),
        (
            "12",
            '    if mask == 127:\n        return "every day"',
            '    if mask == 126:\n        return "every day"',
            "every-day mask",
        ),
        ("13", '    letters = "MTWTFSS"', '    letters = "SMTWTFS"', "weekday letters start Sunday"),
        ("14", "    if isinstance(value, int) or digits == 0:", "    if digits == 0:", "ints formatted with decimals"),
    ],
)
add(
    "tui",
    "tui",
    _TUI + "screens/base.py",
    [
        (
            "15",
            "        if result is not None:\n            self.notify(done(result) if callable(done) else done)\n            self.reload()",
            "        if result is not None:\n            self.notify(done(result) if callable(done) else done)",
            "no reload after an action",
        ),
        (
            "16",
            "        def _after(ok: bool | None) -> None:\n            if ok:",
            "        def _after(ok: bool | None) -> None:\n            if ok is not None:",
            "cancelled confirm still actuates",
        ),
        (
            "17",
            "        if self.AUTO_REFRESH and self.gh.refresh_seconds > 0:",
            "        if self.gh.refresh_seconds > 0:",
            "auto-refresh on every screen",
        ),
    ],
)
add(
    "tui",
    "tui",
    _TUI + "screens/cluster.py",
    [
        (
            "18",
            '                self.act(lambda c: c.start_irrigator(irrigator_id, minutes or None), lambda r: r.get("message", "")),',
            '                self.act(lambda c: c.start_irrigator(irrigator_id, minutes), lambda r: r.get("message", "")),',
            "water-now 0 minutes sent as 0",
        ),
        (
            "19",
            '            f"Stop [b]{name}[/b] now?", lambda c: c.stop_irrigator(irrigator_id), lambda r: r.get("message", ""), "Stop"',
            '            f"Stop [b]{name}[/b] now?", lambda c: c.start_irrigator(irrigator_id), lambda r: r.get("message", ""), "Stop"',
            "stop key starts the pump",
        ),
        (
            "20",
            "            lambda c: c.check(self.cluster_id),",
            "            lambda c: c.check(),",
            "cluster check runs check-all",
        ),
        (
            "21",
            "lambda r: f\"{'Dry run' if opts['dry_run'] else 'Irrigate'}: {r.get('action')} — {r.get('reason')}\",",
            "lambda r: f\"Irrigate: {r.get('action')} — {r.get('reason')}\",",
            "dry-run toast label",
        ),
    ],
)
add(
    "tui",
    "tui",
    _TUI + "screens/modals.py",
    [
        (
            "22",
            "        self.dismiss(int(raw) if raw else 0)",
            "        self.dismiss(int(raw) if raw else None)",
            "empty minutes cancels water-now",
        ),
        (
            "23",
            "        if username and password:\n            self.dismiss((username, password))",
            "        if username:\n            self.dismiss((username, password))",
            "login submits empty password",
        ),
    ],
)
add(
    "tui",
    "tui",
    _TUI + "screens/system.py",
    [
        (
            "24",
            '        if self.paused:\n            self.run_worker(self.act(lambda c: c.scheduler_resume(), "Scheduler resumed"))',
            '        if not self.paused:\n            self.run_worker(self.act(lambda c: c.scheduler_resume(), "Scheduler resumed"))',
            "toggle inverted",
        ),
        (
            "25",
            "        if job_id in self.core_jobs:\n            self.notify(",
            "        if False:\n            self.notify(",
            "core-job delete guard removed",
        ),
    ],
)
add(
    "tui",
    "tui",
    _TUI + "widgets.py",
    [
        (
            "26",
            "    table.move_cursor(row=min(max(target, 0), table.row_count - 1))",
            "    table.move_cursor(row=0)",
            "refill resets cursor",
        ),
        (
            "27",
            '        if s.driest:\n            lines.append(f"  {s.driest.species}", style="italic dim")',
            '        if False:\n            lines.append(f"  {s.driest.species}", style="italic dim")',
            "card hides driest species",
        ),
        (
            "28",
            '                ("running", "#7ed957") if health.get("scheduler_running") else ("stopped", "#ff5f5f"),',
            '                ("running", "#7ed957") if health.get("status") else ("stopped", "#ff5f5f"),',
            "banner scheduler state keyed on status",
        ),
    ],
)
add(
    "tui",
    "tui",
    _TUI + "screens/forms.py",
    [
        (
            "29",
            '        if f.required:\n            raise ValueError(f"{f.label} is required")',
            '        if False:\n            raise ValueError(f"{f.label} is required")',
            "required fields accept blanks",
        ),
        (
            "30",
            '        if f.kind == "float":\n            return float(text)',
            '        if f.kind == "float":\n            return int(float(text))',
            "float fields truncated",
        ),
    ],
)
add(
    "tui",
    "tui",
    _TUI + "app.py",
    [
        (
            "31",
            "            if e.status_code == 401:\n                self.prompt_login()",
            "            if e.status_code == 403:\n                self.prompt_login()",
            "401 no longer prompts login",
        ),
        ("32", "            elif not quiet:\n", "            elif True:\n", "quiet flag ignored"),
        (
            "33",
            "            return await asyncio.to_thread(fn, self.client)",
            "            return fn(self.client)",
            "API call blocks the event loop",
        ),
    ],
)

# CATALOGUE-END

_BY_ID = {m.id: m for m in CATALOGUE}

if __name__ == "__main__":
    sys.exit(main())

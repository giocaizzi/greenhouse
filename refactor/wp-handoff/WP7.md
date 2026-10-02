# WP7 hand-off — scheduler + irrigation pipeline (HIGH RISK)

Worktree `/home/user/gh-wp7`, branch `refactor/wp7-pipeline`, based on `c05e847` (integration gate after WP3).
Not pushed, not merged, not rebased. Assigned files: `greenhouse_server/scheduler.py`,
`greenhouse_server/services/irrigation.py`, `greenhouse_server/config.py` (+ `refactor/mypy-strict.txt`).
Every pytest run: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …` (mutation runs hold the
lock in chunks of ≤ 200 s + one mutant). Raw logs: scratchpad `wp7/tests.log`, mutation JSONL under
`refactor/wp-handoff/wp7-mutation/`.

**Status:** 19 of the plan's WP7 rows done. T7.12 was dropped by the plan. T7.0b's `leak.py` half and T7.20/T7.21 are
out of scope: `leak.py` is not in my assigned files. **T7.22 is blocked**: the design contradicts the code (see
"Stop-and-report").

## Commits (every production commit is G+ → **two reviewers each**, one adversarial)

| Task | Commit | Subset (all + `tests/server/test_contract_wp7_gaps.py`) | Result |
|---|---|---|---|
| test | `d58cede` | characterization commit (below) | 79 passed ×3 (‑n 2; TZ=America/New_York; seed 12345) |
| T7.0a **G+** | `a8d07c7` | `$SCHED $CORE C(gs.scheduler)` | 1009 passed |
| T7.0b **G+** | `2b7961b` | `$PIPE $SCHED $RENDER C(gs.services.irrigation)` | 1357 passed |
| T7.1 **G+** | `dbbd7d0` | `$SCHED $CORE C(gs.scheduler)` | 1009 passed |
| T7.2 **G+** | `d99ad91` | `$SCHED $CORE C(gs.scheduler)` | 1009 passed |
| T7.3 **G+** | `959cab1` | `$SCHED C(gs.scheduler)` | 964 passed |
| T7.4 **G+** | `2d4bb1b` | `$SCHED $RENDER C(gs.scheduler)` | 1271 passed |
| T7.5 **G+** | `f17d59d` | `$SCHED test_contract_sync_service C(gs.scheduler)` | 979 passed |
| T7.6 **G+** | `5da583c` | `$SCHED test_health_monitor test_contract_health_monitor C(gs.scheduler)` | 1004 passed |
| T7.7 **G+** | `fd24c9c` | `$SCHED $PIPE C(gs.scheduler)` | 1050 passed |
| T7.8 **G+** | `377fb1a` | `$PIPE $SCHED $SETTINGS $CORE C(gs.services.irrigation) C(gs.config)` | 1183 passed |
| T7.9 **G+** | `d0b3ad1` | `$PIPE $RENDER C(gs.services.irrigation)` | 1299 passed |
| T7.10 **G+** | `d000715` | `$PIPE $RENDER tests/cli/test_tui.py C(gs.services.irrigation)` | 1299 passed |
| T7.11 **G+** | `9e6369a` | `$PIPE $RENDER C(gs.services.irrigation)` | 1299 passed |
| T7.13 **G+** | `fffdb1d` | `$PIPE $RENDER test_contract_pump_watcher C(gs.services.irrigation)` | 1321 passed |
| T7.14 **G+** | `3a90e5f` | `$PIPE $RENDER test_contract_weather C(gs.services.irrigation)` | 1321 passed |
| T7.15 **G+** | `29638fe` | `$PIPE $RENDER C(gs.services.irrigation)` | 1299 passed |
| T7.16 **G+** | `33166c5` | `$PIPE $RENDER test_alerts C(gs.services.irrigation)` | 1299 passed |
| T7.17 **G+** | `36f5ad0` | `$PIPE $SCHED C(gs.services.irrigation)` | 1050 passed |
| T7.18 **G+** | `139a5af` | `$SCHED test_pump_watcher C(gs.services.irrigation)` | 964 passed |
| T7.19 **G+** | `ed7a34f` | `$SCHED $PIPE test_pump_watcher C(gs.services.irrigation)` | 1050 passed |

Every commit: `ruff check` + `ruff format --check`, `ruff --select ERA,PGH,SLF,PLE` clean, `uv run lint-imports` 10 kept,
`make typecheck` green, `git status --porcelain tests/golden` empty. No red run, no rerun. Note: `tests.log` has two
T7.17 entries. The first one ran on unchanged T7.16 code, because the edit script aborted. The second one is the real
evidence.

## WP gate (on `ed7a34f`)

- Union of all task subsets + `$CORE` + the gap file: **1150 passed** (5m40s).
- `FULL` (seed 0, `-n 2`): **2919 passed** (9m37s), which is 2840 + 79 new tests.
- `FULL_SEED2` was dropped by the orchestrator (sprint mode).
- Whole-file DoD, all three files:
  - ruff `C90/PLR0911/PLR0912/PLR0915` at 8 with `--isolated`: clean.
  - mypy strict: clean (all three files are in the strict list).
  - `sizecheck` lists only `IrrigationService.run_irrigation_pipeline` (proposed EXC) and `rearm_leak_checks` (T7.22,
    blocked). Both files over 400 lines keep their approved entries: scheduler 560 → 564 lines, irrigation 760 → 959.

## Characterization commit `d58cede` (pre-authorized, test-only)

`tests/server/test_contract_wp7_gaps.py` has 79 tests. They cover two things.

**Coverage gaps** (G.1 precondition, per-task subsets computed from a `--cov-context=test` run):
- scheduler 348;
- irrigation 117‑118, 188→190, 363‑364, 417→420, 453, 504→531, 570→572, 614, 638‑639, 677.

**Design seams** (target §3.1/§3.8):
- job-session commit/rollback/close order;
- which errors escape (a None `_app`, or a raising `session_factory`);
- pump-watcher **schedule-time captures vs run-time reads**;
- the temperature-source matrix, including `get_current`/sync call counts;
- `started_at` read after `adapter.start`;
- `check_cluster` call order and result shape per branch;
- the partial count when `rearm_leak_checks` fails mid-scan.

**Proof that the tests can fail:**
- 28 temporary in-place mutations, each restored with `git checkout --` (libs/ clean before and after): **28/28 killed**,
  with the named test failing (`wp7-mutation/gap-proofs.txt`).
- Every M-pre survivor was re-run against this commit: **35/35 killed** (`*-gaps.jsonl`).

## Mutation (refactor/gate1/mutate.py, identity-matched)

**M-pre** ran on unmodified `c05e847` in the detached scratch worktree `/home/user/gh-wp7-mut`. It covered only the
functions WP7 restructures, with the plan's per-module sample rule (irrigation sampled to 150, seed 0):

| Group | Mutants | Killed | Survived |
|---|---|---|---|
| scheduler (5 jobs, `init_scheduler`, `_add_tz_bound_cron_jobs`) | 75 | 64 | 11 |
| irrigation watcher group (`handle_watcher_interrupted`, `schedule_pump_watcher` (+`._run`), `rearm_leak_checks`, `_leak_check_done`; sample 60) | 60 | 53 | 7 |
| irrigation pipeline group (`_resolve_temperature`, `run_irrigation_pipeline`, `monitor_cluster`, `check_cluster`, `check_all_clusters`; sample 90) | 90 | 73 | 17 |

All 35 survivors were non-equivalent. Each got a characterization test in `d58cede`: re-run, **35/35 killed**. So no
equivalence proofs are needed.

**M-post** ran on final `ed7a34f`. It covers every post mutant whose identity translates (through
`wp7-mutation/fmap.json`) from an M-pre identity, using the same test sets plus the gap file:
Mutants were selected by scratchpad `wp7/postsel.py` and run with `wp7/chunked.py` (thin wrappers around `mutate.py`'s own generator/runner). Test sets: a fast contract subset plus
both gap files first. Any fast-set survivor was re-run with the full M-pre set; exactly 1 needed it (`duration_minutes
<= 1` in `schedule_pump_watcher`), and the full set killed it.

| Group | Post mutants (identity-matched) | Killed | Survived | Killed-in-pre now surviving | Pre identities with no post counterpart (rewritten) |
|---|---|---|---|---|---|
| scheduler | 31 | 31 | 0 | **0** | 32 (literal → constant, scaffolding moved into `_job_session`) |
| irrigation watcher | 47 | 47 | 0 | **0** | 13 (literal → constant, `_run` body → `_run_pump_watcher`/`_watcher_tuning`) |
| irrigation pipeline | 53 | 53 | 0 | **0** | 33 (mostly dict-literal/ladder → helper forms) |

- **Rewritten scheduler code** (the 12 post mutants in `_job_session`, `_build_irrigation_service`, the constant-ized
  triggers and the new job bodies that no pre identity maps to): **12/12 killed** (`sched-post-rewritten.jsonl`).
- **M-post verdict: PASS** for each group: kill rate ≥ 75 %, and no killed-in-pre identity survives.
- **Unmatched identities:** the reviewer should spot-check the listed ones with
  `mutate.py --summary --compare … --map fmap.json`.

## Function map (old → new; also `wp7-mutation/fmap.json`)

- **Scheduler jobs:**
  - `_sync_job`, `_health_snapshot_job`, `_anomaly_job`, `_health_monitor_job` → the same function + `_job_session`;
  - `_check_job` → `_check_job` + `_job_session` + `_build_irrigation_service`.
- **Watcher:**
  - `handle_watcher_interrupted` → + `_stop_auto_cycle`, `_left_running_message`;
  - `schedule_pump_watcher._run` → thin `_run` + `_run_pump_watcher` + `_watcher_tuning`.
- `IrrigationService.run_irrigation_pipeline` gained these helpers:
  - `_error_result`, `_decision_result`;
  - `IrrigationService._decide`, `IrrigationService._log_decision_skip`, `IrrigationService._actuation_target`;
  - `_with_error`;
  - `IrrigationService._actuate`, `_soil_note`, `_event_notes`;
  - `IrrigationService._on_started`, `IrrigationService._notify_auto_irrigation`, `IrrigationService._on_start_failed`.
- **Other `IrrigationService` methods:**
  - `_resolve_temperature` → + `_indoor_temperature`, `_outdoor_temperature`;
  - `monitor_cluster` → + `_latest_soil`, `_monitor_target_band`, `_soil_status`;
  - `check_cluster` → + `_check_result`;
  - `check_all_clusters` → + `_resolve_stale_check_alert`, `_record_check_failure`.

## Stop-and-report: T7.22 not done (design contradicts code)

Target §3.1 specifies `_rearm_from_events(repo, now) -> int`, which returns the count, while `rearm_leak_checks` keeps
the `try/except/finally`.

Today the count is a local that the loop increments. If the scan raises after k jobs were added, the function today:
- logs the exception;
- logs `"Re-armed k …"`;
- returns **k**.

With a helper that *returns* the count, the exception discards it, so the function returns 0 and skips the info line.
`test_rearm_failure_mid_scan_keeps_partial_count_and_logs_both_lines` pins the current behavior; it was killed by
exactly this mutation.

**Proposal (needs orchestrator OK):** make the helper a generator, `_rearm_from_events(repo, now) -> Iterator[None]`,
that yields once per scheduled job. The caller then writes `for _ in _rearm_from_events(repo, now): scheduled += 1`
inside the existing `try`. This keeps the partial count and gives nesting ≤ 3.

`rearm_leak_checks` stays at nesting 4 and stays listed by sizecheck.

## Deviations (for reviewers / REFACTOR_NOTES)

1. **T7.0b and T7.20/T7.21 (`leak.py`)** are not done, because the file is outside WP7's assigned files. That also
   leaves `leak.py` without an M-pre run.
2. **Narrowly scoped `type: ignore` comments.** Each carries a code and a reason; none is blanket.
   - `scheduler.py`: 5 × `[union-attr]` on unguarded `_app.state` reads (job bodies + `_job_session`). A None `_app` escaping as an
     AttributeError is pinned behavior, so no assert was added.
   - `scheduler.py`: 1 × `[arg-type]` at `_build_irrigation_service(_app, …)`.
   - `irrigation.py`: `sleep=…  # type: ignore[arg-type]`. `pump_watcher.py` (WP4) types `sleep` as `-> None`, while
     the scheduler passes a `-> bool` callable. **I1 could widen it to `Callable[[float], object]`.**
   - `irrigation.py`: the notifier lambda `[union-attr]` (`maybe_notify` returns before calling it when the notifier is
     None).
3. **T7.7:** `_check_job` keeps its two lazy imports *before* the session (`# noqa: F401`), so an import failure still
   escapes the job. The helper re-imports the same names.
4. **T7.13:** the soil note is built by `_soil_note(sensor_data)` *before* the clock read and passed to
   `_event_notes(act, soil_note)`. This keeps the original formatting-then-clock order, where the §3.1 sketch has
   `_event_notes(act)`.
5. **T7.16:** to fit 40 lines, the three branch returns became one `_check_result` call after a single
   `sync_cluster_alerts`.
   - Only `.get()` reads on the branch's own local plain dict moved before `sync_cluster_alerts`. That call never sees
     those dicts.
   - `cluster.name`, the only ORM read, is still read after it.
6. **T7.17:** `_record_check_failure` takes the loop's `session` explicitly (§3.1 had `(self, cluster_id, cluster_name,
   exc)`), so the rollback/commit target is the exact object the loop captured.
7. **Small removals inside restructuring commits, each with its proof in the commit body:**
   - T7.4: the redundant inner `IrrigationRepository` import (planned).
   - T7.14: the dead `weather = None`.
   - T7.19: a runtime `from greenhouse_server.config import Settings` that only fed an unevaluated local annotation.
   - T7.15/T7.19 also have formatting-only compaction (one-line return dict, blank lines, `job_id` inlined) to meet
     the DoD.
8. **T7.3:** log records from `_job_session` carry `funcName/lineno` of the helper. The logger name, message, level
   and exc_info are unchanged, and no production formatter prints funcName.
9. **Process (contention):** M-pre used `--function` narrowing plus the plan's seeded sampling, through a chunked
   driver that wraps `mutate.py`'s own selection and runner and holds the lock ≤ ~4.5 min per chunk (scratchpad
   `wp7/chunked.py`). Another WP held the lock for whole-module mutation runs for long stretches.

## For the reviewers — look hardest at

- **T7.13 `_actuate`:**
  - clock read after `start`;
  - event-row kwargs order (`irrigator.id` first);
  - success path: activity, then `set_decision_actuated`, then leak check, then watcher, then `result["action"]`, then
    notify (with `get_preferences` evaluated as an argument).
- **T7.17:** `_record_check_failure` is called inside `except`, so `logger.exception` keeps exc_info. Order: rollback,
  log, upsert, commit.
- **T7.19:** what the closure captures (`_app`, registry, hooks at schedule time) versus what is read at run time
  (settings tuning, health monitor).
- **T7.16:** the read-movement argument in deviation 5.
- **T7.3:** `_job_session` generator semantics. A body exception is thrown in at `yield`, inside the `try`; a
  BaseException propagates after `close()`.

## Size-exception proposals (`path::qualname — reason`)

- `libs/greenhouse-server/greenhouse_server/services/irrigation.py::IrrigationService.run_irrigation_pipeline` — 59 body
  lines (CC ≤ 8, nesting 2). The device-health gate stays inline and verbatim by design: T7.12 was dropped (Rev-1 m5)
  because testing `if alarms:` instead of `if blocked:` would diverge on `(True, [])`. Every other block is already a
  helper. (§3.12 estimated ≈ 45; the formatter-expanded add_reason/add_activity_event calls account for the rest.)
- `rearm_leak_checks` is **not** proposed. It waits on the T7.22 decision.

## Strict list / ratchet edits for the integrator

- **Strict-list additions:**
  - `libs/greenhouse-server/greenhouse_server/scheduler.py`;
  - `libs/greenhouse-server/greenhouse_server/services/irrigation.py`;
  - `config.py` was already listed.
- **pyproject ratchet (I1):** `services/irrigation.py` no longer needs `"C901", "PLR0911", "PLR0915"` in
  `per-file-ignores`. It is clean at max-complexity 8 with `--isolated`.

## REFACTOR_NOTES requests (I4)

- Record deviations 1–9 and the T7.22 stop.
- `scheduler._app` stays a rebinding global: no `runtime.py`, per D7.
- The `scheduler ⇢ services.irrigation` lazy cycle is unchanged.
- No new observed bugs. Already-recorded bugs were re-pinned and preserved:
  - a cluster crashing after actuating loses its start event;
  - `force=true` is recorded as auto;
  - `/monitor` uses a 2 h slice;
  - B‑23 (leak-check scan limit 500).
- **Dead code:** none found beyond the three removals above. Vulture hits on these files are false positives: string
  annotations, TypedDict fields, Settings fields.

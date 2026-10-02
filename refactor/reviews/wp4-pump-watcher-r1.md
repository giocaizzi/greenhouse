# WP4 review: Reviewer 1 (behavior-preservation lens), T4.10 `6a06507` + T4.12 `931ad9b`

File: `libs/greenhouse-server/greenhouse_server/services/pump_watcher.py`. Its content at HEAD (`0d9c1cf`) is the same as at `931ad9b`.

**Verdict: APPROVE.** No blocker or major findings. There are two nits and one pre-existing latent issue, which is not a regression.

## Evidence gathered

1. **Line-by-line diff read** of both commits, old vs new bodies.
2. **AST check (T4.12).** Every statement in `_stop_pump`, `_log_aborted_event`, `_log_trip_activity`, `_record_trip_state` and the `try` of `_commit_trip` is `ast.dump`-identical to a statement of the old `_handle_trip`. The only differences:
   - `_stop_pump`'s added `return`.
   - In `_commit_trip`, `irrigator.id` became `irrigator_id`.
   - The `_trip_payload` dict literal is `ast.dump`-identical to the old `payload` literal, with the same key order and the same evaluation order.
   - In `_handle_trip`, `alarm_raw`, `logger.critical` and `elapsed_estimate = int(time.time()) - started_at` are identical and keep their positions.
3. **Differential harness** (`scratchpad/r1/diff_harness.py`). It loads `6a06507^` and `931ad9b` side by side and runs 20,000 seeded random scenarios with a shared RNG, so any change in call order shifts every later random draw and shows up as a mismatch.
   - It traces every `clock()`, `stop_requested()`, `sleep()`, adapter `read_health`/`stop`, `add_irrigation_event`, `add_activity_event`, `monitor.record`, `commit`, `rollback`, and every log record (logger name, level, rendered message, `exc_info`).
   - It also compares the full returned items list in order, and any raised exception's repr.
   - Each step fails randomly 20% of the time.
   - Inputs cover: `error` values `"boom"`, `""`, `0`, `None`; offline; a non-dict `raw`; non-JSON `alarm_raw`; durations -5, 0, 3, 10, 30 and 7.5 (this includes the negative-duration quirk); warm-up 0 or 4; max failures 1 or 3.
   - **Result: 0 mismatches.** Outcome counts: completed 11028, tripped 4462, abandoned 3200, interrupted 1310.
4. **Guarding tests, run under the lock** (`PYTHONHASHSEED=0 flock … pytest -q -n 2` over the 5 files):
   - `6a06507` (from a `git archive` snapshot): 131 passed
   - `931ad9b` (from a snapshot): 131 passed
   - HEAD in `/home/user/gh-wp4`: 131 passed
5. **Limits at HEAD:**
   - `refactor/scripts/sizecheck.py` prints nothing (rc 0).
   - `ruff --isolated --select C90,PLR0911,PLR0912,PLR0915 max-complexity=8` passes.
   - CC: `watch` 7, `_poll_step` 2, `_stop_pump` 2, `_log_aborted_event` 2, `_log_trip_activity` 2, `_record_trip_state` 3, `_commit_trip` 3.
   - `mypy --strict` passes, `ruff check` passes, `ruff format --check` passes.
   - Every helper has full annotations. (PLR0913 fires on `_trip_payload`, but that rule is excluded by target §11.)

## Checklist results

- **Side-effect order:** identical in both commits (harness and reading).
  - T4.10: two clock reads before the loop. Each iteration then does the stop check, then a clock read for `now`.
  - Abandon path: warning, then clock read.
  - Trip path: `_handle_trip`, then `alarm_raw`, then clock read.
  - Each exit evaluates `(deadline - duration_seconds)` there; it is not hoisted, so the negative-duration quirk is unchanged.
- **Exceptions:** the same `try`/`except Exception` scopes and the same `logger.exception` texts. The rollback-in-except is still swallowed. No new early returns.
- **`_poll_step` classification:** equivalent. `read_error or state.offline` gives the message `read_error or "device offline"`, which is never `None` on a failure, so the `is not None` test matches the old truthiness branch.
- **Counters:**
  - Renaming `consecutive_failures` to `failures` shadows nothing.
  - `completed` and `tripped` still report `read_failures` 0.
  - Removing `last_failure_msg` is safe: it was always assigned right before its only read.
- **Return value:** `WatchOutcome` is a TypedDict, so at runtime it is a plain dict. Key order is still outcome, polls, read_failures, alarm_raw, elapsed_seconds. The only caller, `irrigation.py:200`, is unchanged.
- **Logger:** the same module `logger`, called at call time from the staticmethod, so `monkeypatch.setattr(pump_watcher, "logger", …)` still works. The joined literal renders the same text (harness compares rendered messages).
- **Shutdown semantics (REFACTOR_NOTES):** the interrupted path still makes no actuation. Neither commit touches `irrigation.py` or `handle_watcher_interrupted`.
- **`SOURCE_PUMP` import move:** no import-time difference.
  - `health_monitor.py:45` imports `greenhouse_server.services.alerts` at top level, and `pump_watcher.py:45` imports `health_monitor` at top level, so the module is always already in `sys.modules`.
  - `alerts` does not import `pump_watcher`, so there is no partially initialised module.
  - The statement is still outside any `try`, as it was before. If it could ever fail, the new order is safer, because the pump is already stopped by then.
- **Default args:** none added or changed.

## Findings

### nit 1: T4.10 bundles several transformations (`6a06507`)
**Where:** `pump_watcher.py:143-226`

**What:** Besides the typed result and `_poll_step` that target §3.6 prescribes, the commit also:
- extracts `_log_abandoned`;
- renames `consecutive_failures` to `failures`;
- deletes `last_failure_msg`;
- re-wraps a comment.

Each change is behavior-neutral and listed in the commit message and in the hand-off deviations. Strictly, though, it is more than one transformation.

**Fix:** none required. Accept, since all of it is documented.

### nit 2: `LogRecord.funcName` / `lineno` change for the abandon warning (`6a06507`)
**Where:** `pump_watcher.py:217-225`

**What:** The warning is now emitted from `_log_abandoned`, so records carry `funcName='_log_abandoned'` instead of `'watch'`. Nothing in `libs/` or `tests/` formats or asserts on `funcName` or `lineno` (grep). The same applies to the extracted `logger.exception` calls in T4.12.

**Fix:** none.

### minor, pre-existing, not a regression: the trip "best-effort" chain can still raise (`931ad9b`, `_commit_trip`)
**Where:** `pump_watcher.py:348-357`, together with `health_monitor.py:360`

**What:**
- If a flush inside `monitor.record` fails, for example `database is locked` on SQLite, the monitor swallows the error. SQLAlchemy has already rolled back the root transaction and expired the identity map, so `irrigator` is expired and the session is deactivated.
- Old code: `commit()` raised `PendingRollbackError`, and then the except handler raised again while evaluating `irrigator.id`.
- New code: it raises at `self._commit_trip(irrigator.id)`, before the commit.
- Both versions propagate the same exception type out of `_handle_trip`. In both, the rollback is never reached and the pump is already off. The caller's `_run` handles it with rollback plus "Pump watcher job failed".
- The only difference is the traceback's `__context__`. Every other step's except handler already reads `irrigator.id`, so it has the same latent behaviour.

**Fix (outside this refactor):** add a REFACTOR_NOTES entry. One option: capture `irrigator_id = irrigator.id` once at the top of `_handle_trip` and pass it to the handlers. That would be a behavior change, so it belongs in a follow-up, not here.

## Verdict
**APPROVE.** Both commits preserve behavior: AST identity, 0 mismatches across 20,000 differential scenarios, and guarding tests green at each commit (131/131/131). Size, CC and nesting are within limits and the helpers are fully typed.

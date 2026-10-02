# WP4 review R2: behavior-breaker adversary, pump_watcher T4.10 (6a06507) + T4.12 (931ad9b)

**Verdict: REJECT (T4.12).** T4.12 changes behavior in a way you can reproduce, and its commit message gives a reason for that change that is false. **T4.10: APPROVE.** No differences found.

## Harness (scratchpad `wp4-diff/`)
- `pw_old.py` = `6a06507^`, `pw_mid.py` = `6a06507`, `pw_new.py` = `931ad9b` (byte-identical to the worktree). Each one is exec'd into a fresh module named `greenhouse_server.services.pump_watcher`, so all three get the same logger name.
- `harness.py` + `run_diff.py`: recording fakes for every collaborator: irrigator (`id`/`name`/`cluster_id` reads), registry, adapter (read raises / dict / None / list raw, error values `None ""  0 "str" 42 ["e"]`, offline, NO_WATER, alarm_raw int/str/bool/None/list/float/odd repr/raising repr), `stop()` ok/fail/raise, registry raising on the Nth call, repo events raising, monitor ok / raising / lazy / lazy-ctor-raising, commit/rollback raising, scripted clock (zero, negative and huge steps), scripted `stop_requested` (before / during / after the deadline, or raising), sleep raising, poll/warmup/max_fail including 0, negative and huge values, duration from -1e12 to 1e12, a bad `%d` id, `irrigator.id` raising on its k-th read, and a broken `alerts` import. The harness compares the full ordered call log, the return value (type, key order, values) or the exception (type + message), and log records (logger, level, message, exc type+msg).
- Hypothesis `derandomize=True`: 6000 general + 2500 trip-biased scenarios plus 45 hand-picked edges, each run on old, mid and new.
- `realstack.py`: real SQLite, `IrrigationRepository`, `DeviceHealthMonitor` and `FakeIrrigatorAdapter`. It compares every SQL statement and its params, the final `irrigation_events`/`activity_events`/`alerts` rows, logs and results across 432 combinations. `realistic_locked.py`: "database is locked" injected on each write statement of a trip, with and without a pre-existing alert (12 scenarios).

## Results
- **T4.10 (old vs mid): 0 differences** in 6045 general scenarios (3101 completed / 949 interrupted / 262 abandoned / 67 tripped / 1666 exception) and 2500 trip-biased ones, under the strictest comparison (every attribute read, call, arg, return key order, exception, log record). Real stack: 0/432 + 0/12.
- **T4.12 (mid vs new):**
  - Every trip differs by one extra `irrigator.id` read just before `commit` (66 general + 1524 trip-biased scenarios). Calls, results, logs and SQL are otherwise identical.
  - Three hand-picked edges differ in result or log: `trip_id_raise_at_6`, `trip_commit_fail_id_raise_at_6` (same exception, different call log) and `alerts_import_broken`. See Finding 2.
  - **Real stack: 72/432 differ, all of them the commit-time-flush-failure case** (Finding 1).
- Totals: 8545 fuzzed + 45 edge + 444 real-stack scenarios on three versions.

## Finding 1 (T4.12, real SQLAlchemy): a flush failure inside `commit()` now gives a different result
- Old `_handle_trip` read `irrigator.id` inside the commit `except` handler. New code reads it before the commit, as the argument to `_commit_trip(irrigator.id)`.
- When the commit fails during its flush, SQLAlchemy rolls back the root transaction and **expires the whole identity map**. The old handler's `irrigator.id` then lazy-loads on the failed session and raises `PendingRollbackError` out of `watch()`. As a result there is no "Failed to commit pump dry-run side effects…" log, no `rollback()`, and no `tripped` result. New code logs, rolls back and returns `{"outcome": "tripped", …}`.
- Reproducer: `.venv/bin/python <scratchpad>/wp4-diff/repro_commit_flush.py` (run from `/home/user/gh-wp4`). Output:
  - old: `CRITICAL …` then `RAISED PendingRollbackError`
  - new: `CRITICAL …`, `ERROR Failed to commit …`, `returned {'outcome': 'tripped', …}`
  - In both, `adapter.stop` was called and the DB ends up empty after the caller's rollback.
- This is all 72 differing cases in `realstack.py`: every trip × commit-flush failure combination. A DBAPI-level COMMIT failure (an OperationalError from the `commit` event) does not expire the identity map, and old and new behave the same there.
- Reachability: low today. Every repository write in the trip path flushes eagerly, so a commit-time flush has nothing pending. Injecting "database is locked" on each real write statement (`realistic_locked.py`) gives the same behavior in old and new (0/12 differ). It needs dirty or pending ORM state left unflushed in the session at commit time.
- The commit message states "the attribute is loaded and the value identical". That is false after a failed flush. This is a behavior change (arguably a fix: the "never raise" commit handler could raise before). Either declare it under "Bugs touched", or keep the `irrigator.id` read inside the handler to preserve old behavior exactly.

## Finding 2 (T4.12, needs a contrived input): the `irrigator.id` read and the `SOURCE_PUMP` import moved
- New code reads `irrigator.id` one extra time, before commit, on every trip (the harness sees `irr.id #6` before `commit`). In production this is a plain `__dict__` read with no SQL; the SQL streams in `realstack.py` are identical when the commit succeeds. If that read raises (fake id raising on read #6, edge `trip_id_raise_at_6`), new code **skips the commit** and `watch()` raises `Boom`. Old code commits and returns `tripped`.
- `from greenhouse_server.services.alerts import SOURCE_PUMP` moved from the top of `_handle_trip` to `_log_trip_activity`. With `sys.modules['greenhouse_server.services.alerts'] = None` (edge `alerts_import_broken`): old raises `ModuleNotFoundError` **before `adapter.stop()`**, so the pump is not stopped. New calls stop, logs CRITICAL, adds the aborted event, then raises (no activity row, no monitor record, no commit). Not reachable in normal operation: `alerts` is already in `sys.modules` through `health_monitor`, which I confirmed.

## Not behavioral (noted)
- `LogRecord.funcName`/`lineno` change for the moved log calls (`_log_abandoned`, `_stop_pump`, `_log_*`, `_commit_trip`), and tracebacks gain frames (`_poll_step`, `_commit_trip`). Logger name, level and message are identical.
- Pre-existing latent bug, preserved by both versions: any failed flush during a trip (for example "database is locked" on the event/activity/alert INSERT) expires `irrigator`. The watcher's own `except` handlers then call `irrigator.id` and raise `PendingRollbackError` out of `watch()`, so the commit/rollback steps never run. The pump is already stopped by then.

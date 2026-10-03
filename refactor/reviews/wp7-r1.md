# WP7 review — Reviewer 1 (behavior-preservation lens)

Worktree `/home/user/gh-wp7`, `c05e847..8ffa9d6`. Production files: `scheduler.py`, `services/irrigation.py`, `config.py`.

## Verdict: **APPROVE** (all 20 G+ commits: a8d07c7 2b7961b dbbd7d0 d99ad91 959cab1 2d4bb1b f17d59d 5da583c fd24c9c
377fb1a d0b3ad1 d000715 9e6369a fffdb1d 3a90e5f 29638fe 33166c5 36f5ad0 139a5af ed7a34f)

No blocker, no major, no minor. 4 nits, none requiring a change.

## Tests run

`PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 <requested set>` on 8ffa9d6:
**318 passed**, 3 warnings (pydantic third-party), 101.98 s. Exit 0. Worktree clean.

## Evidence by area

### Scheduler (a8d07c7, dbbd7d0, d99ad91, 959cab1, 2d4bb1b, f17d59d, 5da583c, fd24c9c)
- **Constants:** values checked in `greenhouse_core/constants.py` (pre-existing at the base; core is unchanged by WP7):
  ANOMALY_SCAN_INTERVAL_MINUTES=15, HEALTH_SNAPSHOT_HOUR=0, HEALTH_SNAPSHOT_MINUTE=30, SYNC_JOB_BACKFILL_HOURS=6. The int
  and float types match the literals they replace.
- **CHECK_ALL_JOB_ID hoist:** the same string, used in `_TZ_BOUND_CRON_JOBS` and in the cron `id=`. The job ids,
  triggers, defaults and `_add_core_job` registration order are unchanged; the diff only swaps literals.
- **`_job_session` semantics match the inline pattern.**
  - `session_factory()` is called before the `try`, so a None app or a raising factory escapes. A None app raises the
    same `AttributeError` from `__enter__`, because contextlib does not wrap it.
  - `IrrigationRepository(session)` is built inside the `try`.
  - A body exception is thrown in at `yield`, inside the `try`. It triggers `rollback()`, then
    `logger.exception(msg)` (exc_info is the thrown exception), then `close()`. The generator then returns normally, so
    the exception is suppressed, exactly like the old `except Exception`.
  - A `commit()` failure is caught by the same handler.
  - A BaseException is not caught: the session is closed, then the original exception propagates through
    `__exit__`'s re-raise.
  - The messages are verbatim per job, and the logger is the same (`scheduler` module `__name__`).
- **`_app` is read lazily.** Every job passes the module global `_app` at call time, and nothing binds `_app` at import.
  Pre-session reads stay before the session: `_sync_job`'s `registry`, `_check_job`'s `cloud`/`registry`, and
  `_health_monitor_job`'s None-check and `monitor`.
- **`_check_job` / `_build_irrigation_service`:**
  - The two lazy imports stay before the session. The helper's re-import is only a `sys.modules` lookup.
  - Inside the transaction the order is unchanged: `SyncService(...)`, then the `health_monitor` getattr, then
    `bind_repo`, then the `IrrigationService(...)` kwargs (weather_client, plant_db, notifier, read in the same order),
    then `check_all_clusters()`, then commit.
- **Dead removal in 2d4bb1b:** the inner `from greenhouse_core.repository import IrrigationRepository` imported the same
  object the module already imports at the top. It was truly dead.

### Pipeline / watcher (2b7961b, 377fb1a, d0b3ad1, d000715, 9e6369a, fffdb1d, 3a90e5f, 29638fe, 33166c5, 36f5ad0, 139a5af, ed7a34f)
- **Constants:** FALLBACK_TEMPERATURE_C=20.0 (float, so JSON is unchanged), DEFAULT_SOIL_MOISTURE_TARGET="45-65",
  MONITOR_LOOKBACK_HOURS=2, MONITOR_VERY_DRY_MARGIN=15, MONITOR_WET_MARGIN=10, LEAK_CHECK_ACTIVITY_SCAN_LIMIT=500, and
  PUMP_WATCHER_* = 2.0 / 5.0 / 5. The Settings defaults keep their types. These preserve the `/monitor` 2 h slice and
  B‑23's limit of 500.
- **AST comparisons** (`scratchpad/astcmp2.py`, `ast.dump` without attributes):
  - **Device-health gate in `run_irrigation_pipeline`:** identical. This preserves B‑6: no decision_logs write and no
    `if alarms:` rewrite.
  - **Old `_run` body vs `_run_pump_watcher`** (renaming `_app`→`app`, `wait_for_shutdown`→`sleep`,
    `shutdown_requested`→`stop_requested`): the lazy imports, `session_factory`, the except handler and the finally
    are identical. Every try-body statement is identical except the settings ladder, which became `_watcher_tuning`.
    That ladder still runs after `get_irrigator` and before the `health_monitor` read, with the same three attribute
    reads in the same order.
  - **`check_all_clusters` except body vs `_record_check_failure`:** rollback, `logger.exception`, `upsert_alert`, then
    commit, all identical (only `e`→`exc` is renamed). It is still called inside `except`, so exc_info is kept. The
    result dict is assigned after the commit, as before.
  - **`_stop_auto_cycle`:** `stop_msg=""`, the try/except around `adapter.stop`, and the success branch (warning,
    `add_irrigation_event` with one `_time.time()`) are all identical. `irrigator.name` is still read after the event
    write. In the failure branch, `logger.error` still comes before the message.
- **`_actuate` (fffdb1d):** the order is unchanged:
  1. `adapter.start`;
  2. `_soil_note` (formatting before the clock);
  3. a single `int(_time.time())`;
  4. `add_irrigation_event`, with kwargs evaluated in the same order (`irrigator.id` first, notes last, and the f-string
     reads temp, source, soil, confidence and reason_text in the same order);
  5. on success: `add_activity_event`, then `set_decision_actuated` (guarded by None), then `_schedule_leak_check`,
     then `schedule_pump_watcher`, then `result["action"]="irrigated"`, then `maybe_notify(...)`, with
     `get_preferences()` still evaluated as an argument. The lambda still reads `irrigator.name`/`confidence` lazily;
  6. on failure: `add_activity_event`, then `raise_alert`, then action/reason, in that order.

  `triggered_by="auto"` is kept on the event (the force=true-recorded-as-auto bug is preserved). The leak check is still
  scheduled for force runs.
- **`_actuation_target` / `_with_error` (9e6369a):** the three error strings are identical. `_with_error` sets action
  before reason, so the key positions are unchanged. The `UnknownDeviceModel` message is still formatted inside
  `except`.
- **`_resolve_temperature` (3a90e5f):** the branch matrix is equivalent, with the same `get_current`/sync call counts
  (indoor reads weather only when there is no sensor temperature; outdoor reads it exactly once). `weather = None` was
  dead: every path assigns it before reading it, and nothing reads it after the if/else.
- **`check_cluster` (33166c5, deviation 5):**
  - The calls run in the same order: `get_cluster`, then `get_irrigator_for_cluster`, then
    `collect_learning_alerts`, then `collect_maintenance_alerts`, then the branch (`monitor_cluster` /
    `get_effective_config` / pipeline), then a single `sync_cluster_alerts`, then the `cluster.name` read.
  - The only reads moved before `sync_cluster_alerts` are `.get()` calls on the branch's own local plain dicts. No
    reference to those dicts is passed to `sync_cluster_alerts`, and the values are the same objects.
  - The key order is identical per branch. `if not irrigator` has the same truthiness as the old `if irrigator`.
- **`check_all_clusters` (36f5ad0):** each cluster is still isolated by its own commit and rollback, and `session` is the
  same object the loop captured. The pinned bug "a cluster that crashes after actuating loses its start event" is
  preserved, because the rollback still runs first.
- **`schedule_pump_watcher` (ed7a34f):**
  - **Schedule-time captures are unchanged:** `_app` and `scheduler`/`shutdown_requested`/`wait_for_shutdown` (all
    imported at schedule time), `registry`, `irrigator_id`, `duration_seconds`, `started_at`, `triggered_by`.
  - **Run-time reads are unchanged:** the settings tuning and the health monitor.
  - The job id, name, trigger, `run_date` and `replace_existing` are identical.
  - **Dead removal:** the runtime `from greenhouse_server.config import Settings` only fed a local-variable annotation,
    which Python never evaluates. The scheduler import inside the same `try` already loads config, so no failure mode
    changed.
- `rearm_leak_checks`, `_run_leak_check`, `_add_leak_check_job` and `_schedule_leak_check` are byte-identical apart from
  the scan-limit constant.
- **TypedDicts and `cast` (d0b3ad1, 33166c5):** runtime plain dicts, and `cast` is a no-op.

### Declared deviations (§3.1 / hand-off §Deviations)
| # | Judgement |
|---|---|
| 1 `leak.py` out of scope | Process only; no behavior. |
| 2 Scoped `type: ignore`s | Neutral. No asserts or guards added; the None-`_app` AttributeError escape is preserved. |
| 3 T7.7 lazy imports kept before the session | Preserves the import-failure escape. Neutral. |
| 4 T7.13 `_soil_note` before the clock | Preserves the original order. Neutral (it is closer to the source than the §3.1 sketch). |
| 5 T7.16 single `sync_cluster_alerts` | Neutral (see above). The only ORM read, `cluster.name`, is still after the sync. |
| 6 T7.17 explicit `session` param | Neutral. It is the exact captured object. |
| 7 Three dead removals | All three verified dead (see above). |
| 8 `_job_session` funcName/lineno | Neutral. `grep -rn "funcName\|lineno\|%(module" libs/` finds nothing, so no formatter prints them. |
| 9 Mutation process | Process only. |

The T7.22 stop is correct. A count-returning helper would lose the partial count on a mid-scan raise. The generator
proposal is behavior-neutral if each `yield` comes **after** `_add_leak_check_job` and the `for` loop stays inside the
existing `try`.

## Findings

- **nit — fffdb1d, `services/irrigation.py:700,721`.** `_on_started` and `_notify_auto_irrigation` re-read
  `act.decision.duration_minutes`, where the old code captured one local before `start`. This is a pure Pydantic
  attribute, and nothing between `start` and the notification mutates the decision, so it is neutral. Fix (optional):
  carry `duration` on `_Actuation`.
- **nit — 2d4bb1b, `scheduler.py:350`.** `_health_snapshot_job` now resolves `IrrigationRepository` through the
  scheduler module global (inside `_job_session`) instead of a fresh `from greenhouse_core.repository import`. It is the
  same object in production; the two would differ only if a test patched `greenhouse_server.scheduler.IrrigationRepository`
  (no test does). No fix needed.
- **nit — fd24c9c, `scheduler.py:328-330,381-382`.** The double import (`_check_job` plus the helper) is redundant but
  intentional and harmless. Keep it as documented.
- **nit — 959cab1.** Job-failure tracebacks gain contextlib frames, and the record's funcName is `_job_session`. This is
  cosmetic, and deviation 8 already covers it.

# Review — CONS-W3 (`refactor/consistency-3`, base `971aa93` → head `2035e38`)

Reviewer: independent second reviewer for A14 + spot-check of the lane. Read-only; no edits or commits in
`/home/user/gh-cons3` (`git status --porcelain` stays empty).
Artefacts: `scratchpad/w3rev/` (harness copy, scenario generators, compare scripts, `out/*.jsonl`, `summary.txt`,
pytest logs). The scratch trees (`git archive` of `971aa93` and `2035e38`) were deleted afterwards. No git worktrees were added.

## Verdict: **REJECT (narrow)**: one undeclared observable difference in `faab47d` (A14). Everything else checks out.

The fix is small, and the rest of the lane can be approved once it lands (see "Required" below).

---

## 1. A14 (`faab47d`): `_job_session` → `services/_session.job_session`; `_run_leak_check` uses it

### Method
- I copied the WP7 harness to `w3rev/harness.py`. Old tree = `971aa93`, new tree = `2035e38`. Each tree runs in its own
  process, selected by `PYTHONPATH` (`gh-cons3/.venv`), with `PYTHONHASHSEED=0`. The tests dir is the same for both
  (conftest/fake_devices are unchanged on the branch).
- Additions to the WP7 harness:
  - Session-level ORM event recording (`after_begin/commit/rollback/soft_rollback/transaction_end`). It is kept as a
    separate per-op `sess` key, so the declared Session-API difference can be checked on its own.
  - Direct ops `leak_direct` (calls `_run_leak_check` directly, including a repeat for the "already done" path),
    `leak_noapp`, `leak_nosf`, and `job_noapp` / `job_nosf` for all five scheduler jobs.
  - py-fault owner `leak.` (the module that holds `LeakDetectionService`).
  - **A new `rollback` DB-fault kind**: the k-th engine ROLLBACK raises `OperationalError`.
- What is compared: SQL trace (statements, params, COMMIT, ROLLBACK), DB rows, log records (logger, level, message,
  exception type and message), return values and exceptions per op, adapter/gateway/notifier calls, and jobs.

### Results (`w3rev/summary.txt`)
| batch | scenarios | differ (excl. `sess`, excl. B5 DEBUG records) | notes |
|---|---|---|---|
| `scenarios_leak.json` (new: 500 Hypothesis + 287 edges; leak_direct, already-done, no-app/no-sf, job bodies, rearm, run_jobs; py/write/commit faults) | 787 | **0** | 522 already-done ops: `sess` differs only by the declared `+after_rollback,+after_soft_rollback,+after_commit,+after_transaction_end` |
| `scenarios_rearm.json` (WP7) | 796 | **0** | |
| WP7 `scenarios.json` restricted to job/rearm/run_jobs scenarios | 710 | **0** | |
| `scenarios_rb.json` (rollback/write/commit faults × 3 op sequences + 151 leak-*flagging* variants, 278 flagged leak checks) | 295 | **10** | all 10 are the finding below |

Harness errors: 0 in every batch. The only other raw differences are the extra DEBUG records from B5 (`3444d9a`, 266
records filtered by exact logger and message). Those are labeled.

### The declared difference
On the "already done" early return, the new code calls an explicit `session.rollback()`, then `job_session` runs a
`commit()` with no transaction, then `close()`. The old code went straight to `close()`.
- I confirmed the engine sees the same statements: `SELECT activity_events … → ROLLBACK` in both trees. The old
  `close()` rolled back inside `Transaction.close()`, so ROLLBACK appeared once on each side. No COMMIT is emitted in
  the new tree. Logs and rows are identical.
- Only the Session-event layer differs. No session listener exists in `libs/`, so this is not observable in the app.

### FINDING (undeclared): a failing ROLLBACK on the already-done path is now swallowed instead of raised
- Repro: `scenarios_rb.json` `rb_{a,b,c}_rollback_{1,2}_{1,1000}`. The fault hits the ROLLBACK of an already-done
  leak check.
  - Old (`971aa93`): the error is raised from `close()` inside `finally`, so `OperationalError(... [SQL: ROLLBACK])`
    **escapes** `_run_leak_check`. Logs: only the DEBUG "already done" record. Under APScheduler that becomes
    `apscheduler.executors.*` ERROR `Job "…" raised an exception` plus `EVENT_JOB_ERROR`.
  - New (`2035e38`): the explicit `session.rollback()` raises *inside* the `with` body. `job_session` catches it, rolls
    back again, logs `greenhouse_server.services.irrigation` ERROR `"Leak check job failed for cluster %d"` with the
    traceback, and the job **returns None**.
  - Same SQL, same rows. What differs: return vs exception, plus the log record's logger and message.
  - All 10 differing scenarios belong to exactly this class (checked by script: op = `leak_direct`, old result =
    ROLLBACK exception, the logs contain "already done", new result = `{'ret': None}` + "Leak check job failed", SQL
    equal, DB equal).
- Impact: very low. It needs a failing ROLLBACK of a read-only SQLite transaction. Both versions still log at ERROR
  with a traceback, and the app registers no APScheduler listeners. But the commit body says "Behavior: none at the
  database / log level… Same order for every path", and this path contradicts that. Under the OD2/brief rule
  ("behavior-preserving stays `refactor(...)`"), it has to be either removed or declared.
- The main failure path is identical in both trees, including a failing ROLLBACK there (it escapes in both). The five
  scheduler jobs are identical under every fault (write/commit/rollback/py, no-app, no-session_factory).

### Required (pick one)
1. **Preferred:** keep `_run_leak_check` on its own inline scaffolding, as already done for `_run_pump_watcher` and
   `rearm_leak_checks` ("different shape": a read-only early return). The scheduler-side move of `job_session` stays.
   That restores exact behavior.
2. Or keep the code, but declare the edge in the `faab47d` body (rewrite the commit, as done before for `3bf9cd5` and
   `310d90b`) and in the REFACTOR_NOTES text: "a ROLLBACK failure on the leak check's already-done path is now logged
   by the job and swallowed instead of escaping to APScheduler". Only valid if the orchestrator accepts it as
   immaterial; strictly it is still a behavior change inside a `refactor` commit.

Informational, no action needed: log records from job failures now carry `module`/`funcName`/`pathname` =
`_session`/`job_session`. Before, they carried `scheduler`/`_job_session`, or `irrigation`/`_run_leak_check` for the
leak check. No formatter in `libs/` uses these fields. The logger name and the msg/args split are preserved.

## 2. Behavior-preserving commits (spot-check)
- `a251e99`: `repo.session.commit/rollback/flush` → `repo.commit/rollback/flush`. These are one-line delegates
  (`repository.py:93-103`). Placement is unchanged; only OD2 docstring notes are added.
- `ced4b55`: `check_all_clusters` / `_record_check_failure` now go through `self._repo`. The old code cached
  `session = self._repo.session` once; `repo.session` is only assigned in `__init__` (`repository.py:87`), so the two
  are equivalent. The harness `check_job` / `check_all_http` / `svc check_all` ops under faults are identical.
- `022bdd4`: the repository methods are verbatim copies of the removed queries. A SQL-echo comparison
  (`w3rev/sqlcmp.py`) over `search()` ×3, `score_cluster`, `build_heatmap_payload` and
  `_build_plant_sensor_datasets` (which uses `list_sensors_by_ids`) gives the same statements, params, order and
  results in both trees. `get_open_alert_by_key` is covered by the harness monitor ops.
- `ea6f450`, `bfaee66`, `0682d6a`, `81fe2a8`, `78aaee9`: I ran a constant-inlined AST comparison (`w3rev/astlit.py`).
  The output is identical, except for names the old code imported from `services.alerts`. I checked those by hand:
  the old `SOURCE_LEAK/ANOMALY/PUMP` = `"leak"/"anomaly"/"pump"`, which equal the `models` values. Every new constant
  has the old literal's value and type (e.g. `ANOMALY_Z_THRESHOLD 4.0`, `DATA_QUALITY_STALE_SECONDS 86400`).
- `36a8d71`, `e353da9`, `37ff66e`, `5a71a27`: an annotation- and docstring-insensitive AST comparison
  (`w3rev/astann.py`) is identical. The only module-level differences are the two new package docstrings.
  `145b936` and `310d90b` are pure renames (no external `._cloud` users; `SyncService`'s `cloud=` kwarg is kept).
  `f153ba6`: `ensure_object(dict)` was immediately overwritten by `ctx.obj = server`, so it was dead.
- `493f20c` (leak decomposition): covered by `scenarios_rb.json` with 278 flagged leak checks (alerts raised, the
  `leak_hold` payload, evidence key order). They are identical in both trees.
- `56efc48`: `StopAllResult` / `PlantSyncResult` are NamedTuples, and every caller tuple-unpacks them. The
  TypedDicts are runtime dicts.
- `0d8ecda`: `server_url(ctx)` is the same expression as the three inlined copies.

## 3. Labeled changes
- **D10b `1f44fd2`**: only charts `_water_needs_band` and the new test change. The test matches the declaration
  (`40-60-80` → 40/60 water_needs; `None`/`""`/`"55"` → default). Nit: other malformed targets like `"40--60"` or
  `"-50"` also change (to the default band labelled `water_needs:*`). They fall under "only malformed targets
  differ".
- **OD3 pump `80dcb7b`**: removes the method, the constant, `__all__` and the scheduler hook with its INFO log.
  `init_health_monitor` still runs backfill → commit / rollback. New test
  `test_init_health_monitor_leaves_old_pump_dry_run_alerts_alone`; `TestLegacyMigration` removed. Nothing else moved.
- **OD3 interval `787c471`**: the config validator, the property and the shim are removed, and
  `_resolve_check_cron_hours` returns `settings.check_cron_hours`. `extra="ignore"` keeps the old variable harmless.
  README and `.env.example` are updated. Goldens: removal-only. Tests: replaced by "ignored" tests. No remaining
  references in libs or plugin docs.
- **B5 `3444d9a`**: three `except` blocks gain one `logger.debug(..., exc_info=True)` each, plus two module loggers.
  Return values and control flow are unchanged. The harness confirms this: every difference attributed to B5 is
  exactly those added DEBUG records.
- **B7 `4615c84`**: one placeholder string, plus the 2 matching `tui/surface.json` lines.

## 4. Golden edits (`git diff 971aa93 2035e38 -- tests/golden`)
- They are removal-only, apart from:
  - the `imports.json` `field_validator` line, re-emitted without its trailing comma;
  - the `tcss_tokens_without_a_mounted_widget: []` collapse;
  - `env_reads.json`: the default `"<expr: DEFAULT_SERVER_URL>"`, with the reads in `auth`/`tui` removed;
  - `surface.json`: the B7 placeholders.
- The `env_reads` value is still pinned by behavior: `tests/server/test_contract_settings.py::
  test_cli_server_url_env_and_default` (line 370) asserts that `get_client` and `_login_client` default to
  `http://localhost:8000`, honour the env var, and honour the `--server` flag.

## 5. Tests (under `/tmp/greenhouse-tests.lock`, `PYTHONHASHSEED=0 -n 2`, head `2035e38`)
- `test_contract_scheduler`, `test_contract_pipeline`, `test_contract_check_all`, `test_contract_wp7_gaps`,
  `test_leak_*`, `test_contract_settings`, `test_contract_health_monitor`: **352 passed** (68.6 s).
- `tests/cli/test_contract_json_output.py`: **276 passed**. `tests/test_contract_imports.py`: **23 passed**.
- Extra: `test_web_charts`, `test_scheduler`, `test_scheduler_settings`, `test_contract_scheduler_registry`,
  `test_health_monitor`, `test_pump_watcher`: **65 passed**.

# FP-S — second review (high-risk lens: auth/app wiring, pipeline, scheduler jobs)

Reviewed: `git log 8a76a3f..refactor/fp-s` (34 commits, head `ea32c9c`), differentially against base `8a76a3f`
using two detached worktrees (`gh-fps-base`, `gh-fps-head`, each with its own `uv sync`), real SQLAlchemy, probes in
the session scratchpad. Targeted suites on head (`-n 1`): auth_session, auth, contract_auth, mcp, contract_mcp,
contract_openapi, notify, web_emergency_notify, contract_scheduler(+_registry), contract_wp7_gaps, leak_rearm,
contract_pipeline, test_repository_users, test_contract_imports → **404 passed**. Coordinator reports the merged
preview (`176085a`) at 3058 passed.

**Overall: APPROVE.** No blocking findings. The only observable deltas are the two labeled ones (`d44ab8f`
fix(consistency), `cfb423f` fix(drift) D20), plus one INFO side effect of D20 (F1). It belongs to a class of issue
that already exists in the web manual start/stop paths.

---

## 1. Auth / sessions: PASS

Probe `probe_auth.py`: the same 24-request script runs against both trees. It wraps `app.state.session_factory` to
count every opened and closed session and hooks `after_commit` / `after_rollback`.

| case | base opened/closed | head opened/closed | status / body / headers |
|---|---|---|---|
| API 200, 404, 201 POST, 422, MCP-token-as-bearer, auth disabled | 2/2 | 1/1 | identical |
| API 500 (handler writes, then raises) | 2/2, 0 commits | 1/1, 0 commits | identical. The write is not persisted in either tree (the next list shows 1 row) |
| API 401 bad token, login 401/200, /me, logout | 1/1 | 1/1 | identical |
| web anon 303, web login 401/303 | same | same | identical (`location` header equal) |
| web page / web 404 | 3/3 | 2/2 | identical |
| `/mcp` wrong/missing token → 401 "Invalid MCP token"; token unset → 503 "MCP auth not configured" | 0/0 | 0/0 | identical |

- Opened always equals closed, on every path including 401, 422 and 500, so no session leaks.
- The only change is the session count (2 → 1 per authenticated request). That is exactly the documented
  REFACTOR_NOTES entry.
- Rollback/commit coupling: `require_user` / `require_web_user` only read (`repo.get_user`). They never write
  (no last-login or token rows; `record_login` exists only in the login routes, which were already single-session).
  So a handler rollback cannot undo auth writes, and a handler commit carries no auth state.
  - `AuthenticatedUser` is a detached dataclass copy, so ORM expiry after a handler commit/rollback cannot reach it.
  - The engine is plain pysqlite (`database.py:15`), which emits no BEGIN on SELECT. The earlier auth SELECT
    therefore does not move the handler's transaction start.
- `WWW-Authenticate`: absent on JSON 401s in both trees. This is the known pre-existing drop
  (REFACTOR_NOTES "Observed bugs" #1); FP-S did not change it.
- MCP path: `require_mcp_token` changed only `Depends(_get_settings)` → `Depends(state.get_settings)`, which has the
  same body. Settings are still read every request; the 503/401 logic is byte-identical.
- Test overrides: nothing in `tests/` overrides `_session_from_app` / `get_session`. Note that an override of
  `deps.get_session` now also reaches auth (same callable); that is intended.
- `42f9860`: in the login route, `RepoDep` now resolves before `settings`. Neither has request params, so 422
  ordering cannot change.
  - Repository delegates call the same core functions.
  - `has_users()` runs the same `select(User).limit(1)`.
  - `bootstrap_admin` keeps `Session(engine)`.
- `jobs.py` / `read_session`: see §3. App startup reads (`_startup_timezone`, `_restore_persisted_scheduler_pause`)
  keep the same shape. An open failure is still inside the outer `try`; a close failure is still swallowed by the
  outer `except`.

## 2. Pipeline (`24210ca` health-gate extraction + flattened dry-run/skip): PASS

Probe `probe_pipe.py`: 16 scenarios with a patched `decide_for_cluster` that persists a real `DecisionLog`. It
records, in one ordered trace:
- every `IrrigationRepository` public call with its arguments
- session flush (object classes), commit and rollback
- health-monitor calls, notifier payloads, and `_schedule_leak_check` / `schedule_pump_watcher` calls

It also records the returned result dict (or the exception) and the final rows (activity, events, decision_logs,
alerts).

Scenarios:
- healthy, with and without a monitor
- health-blocked (NO_WATER + OFFLINE)
- monitor raises
- dry-run irrigate (with a blocking monitor), dry-run skip
- engine skip (with a blocking monitor)
- `start` returns failure, `start` raises
- no irrigator, no registry, unknown model
- notifier raises, no notifier
- `add_irrigation_event` raises after the pump started
- blocked with no notifier

**Base and head output: byte-identical (16/16).**

- `_actuate` has a single `return result`, and `_with_error` mutates in place, so discarding `_actuate`'s return
  value is safe.
- `decision.action is Action.SKIP` is equivalent to `.value == "skip"`: `IrrigationDecision` has
  `validate_assignment=True`, a string `"skip"` coerces to the member (verified), and `libs/` never calls
  `model_construct`.

## 3. Scheduler jobs (`jobs.py`, `job_session(commit=…)`, `read_session`, `irrigation_jobs.py`): PASS

Probe `probe_jobs.py`: a `Session` subclass records commit/rollback/close and can inject a failure into each. Logs
are captured with logger name, level, message and exc_info.

Scenarios:
- leak check: first run, "already done" early return, early return with close failing, check raises, commit fails,
  commit+rollback fail, no app
- pump watcher: completed, interrupted, raises, no irrigator, interrupted with commit failing, close failing,
  `app=None`
- snapshot job: ok, raises, commit fails
- anomaly job
- every job with `_app=None`
- sync job without a gateway, `check_ok`
- `init_health_monitor`, including close failing
- `rearm_leak_checks`, including close failing

**Base and head output: identical** (the only diff is the unfrozen `time.time()` in the rearm job id).
- Early-return close failures still escape.
- A rollback failure still escapes.
- A `None` app still escapes as the same `AttributeError`.
- Log records keep the `greenhouse_server.services.irrigation` / `greenhouse_server.scheduler` names.

Registry golden (`scheduler_jobs.json`) is unchanged: func_refs, ids and names, test green.

INFO only:
- Dynamic leak-check jobs now carry `func_ref` `greenhouse_server.services.irrigation_jobs:_run_leak_check`.
  Not observable: the default MemoryJobStore persists nothing, and no API exposes `func_ref`
  (`SchedulerJobResponse` has id, name, trigger, next_run_time, paused, core).
- `logger.exception` records from the pump watcher and leak check now come from `jobs.py:job_session`. Logger name
  and message are unchanged; `funcName` and `lineno` differ, which is the same as the five scheduler jobs since WP7.

## 4. `not_found_as_404` / `services/errors.py`: PASS

Probe `probe_404.py` enumerates **every** parametrized APIRoute, API and web (111 method/path pairs), via
`_iter_routes_with_context`. Each id param becomes `999999` (job ids become `nosuchjob`). JSON bodies are built from
the OpenAPI request schemas; web POSTs send an empty form.
- Status and JSON body are identical across all 111. Web HTML is compared by normalized length plus the extracted
  "… not found" detail, also identical.
- Result counts: 93×404, 12×422, 4×200, 1×500, 1×503.
- The one 500 is the web `POST /clusters/999/irrigate`. It is the same in base and is a known pre-existing bug
  (REFACTOR_NOTES line 100).
- Every replaced `return None` / `return {}` fired only on a missing row; each was checked at its definition.
- Unwrapped callers are all behind a prior existence check:
  - `_cluster_chart_payloads`: after `get_cluster_status`
  - plant dashboard: `require_plant_in_cluster`
  - `cluster_learn`: `require_cluster`
  - `dashboard_hero`: ids from `list_clusters` on the same session
- `irrigate`: `require_cluster` before the pipeline is equivalent, because the pipeline's first statement was the
  same lookup and nothing ran before it.

## 5. D20 web emergency stop (`cfb423f`): PASS (labeled change), one INFO

Probe `probe_d20.py` covers 8 cases × {API, web}: notify on, `notify_emergency=False`, no notifier, notifier raises,
device errors, zero irrigators, no prefs row ×2.
- API (base vs head): byte-identical, including the SQL trace (commit, then `SELECT user_preferences`).
- Head web: the payload equals the API payload in every case:
  `{"triggered_by":"emergency","irrigator_name":"N irrigator(s)","detail":"kill switch[, k error(s)]"}`.
  It respects the preference toggle and swallows a notifier failure.
- Web status, HTML body, events and the prefs row count are otherwise unchanged.
- `tests/server/test_web_emergency_notify.py` pins web == API.

**F1 (INFO, not blocking): web kill switch can stall about 5 s when the singleton `user_preferences` row is missing.**
- Cause: `maybe_notify(notifier, repo.get_preferences(), …)` evaluates `get_preferences()` after
  `stop_all_irrigators`' `repo.commit()` (`services/bulk.py:69-72`). With no row, that INSERTs uncommitted on the
  request session and holds the SQLite write lock. The chrome read in `web/context.py:_preference_flags` (its own
  session) then tries its own seeding INSERT, waits out the busy timeout, and falls back to defaults.
- Reproducer: `probe_d20_lock.py`, a file-backed SQLite DB. After lifespan, delete the prefs row, then
  `POST /bulk/stop-all` with `HX-Request`. Base: 200 in 0.0 s. Head: 200 in 5.1 s.
- Impact: the stops and events are already committed, so this delays only the response. It is reachable only
  without a prefs row, and startup's `_startup_timezone` seeds and commits that row.
- Same pattern already exists in the web manual start/stop paths (`manual_control.py:137-150`: commit →
  `get_preferences()` → `base_context`), so it is not a new class of issue.
- Fix (optional), either:
  - (a) one line in `bulk_stop_all_web`: call `repo.session.rollback()` after `stop_all_irrigators(...)`. This
    releases the lock and discards the uncommitted seed row, so the net DB effect equals the API.
  - (b) add a sentence to the D20 REFACTOR_NOTES entry.

## 6. `tests/server/test_notify.py` (4 callbacks, zero-arg → one-arg): PASS, not weakened

- The diff is only `lambda:` → `lambda _client:` and `def boom():` → `def boom(_client):`. Every assertion is
  byte-identical, and the four behaviors (None notifier, category disabled, fires, swallows) are still exercised.
- Risk check: a zero-arg callback would now raise `TypeError`, and the fail-silent `except` would swallow it. All 6
  production call sites use `lambda n:` (irrigation ×1, bulk ×1, manual_control ×3, alerts ×1). That is the same
  count as base (5 + the bulk call moved from the route), and mypy strict enforces `Callable[[NtfyClient], object]`.
- Optional hardening: in `test_fires_when_enabled`, assert the callback receives the notifier instance.

## 7. Doc contract (`23d239d`): PASS

- With every `"description"` key stripped recursively, `tests/golden/contracts/openapi.json` and `mcp_tools.json`
  are equal between base and head (raw files differ).
- The only text change: `start_irrigator` / `stop_irrigator` now say "through its device adapter" instead of
  "over the Tuya local protocol". This is more accurate, since the switch pulse goes through the Cloud per CLAUDE.md.
- Both fingerprints were re-recorded, and `test_contract_openapi` / `test_contract_mcp` are green.
- No capability changed, so no SKILL.md / plugin.json update is required.

## Findings summary

| # | Sev | Where | What | Fix |
|---|---|---|---|---|
| F1 | INFO | `services/bulk.py:69-72` + `web/routes/analytics.py:192` | Web stop-all can stall about 5 s on the SQLite lock when no prefs row exists (same as the existing web manual start/stop) | `repo.session.rollback()` after `stop_all_irrigators` in the web route, or note it under D20 |
| I1 | INFO | `services/irrigation_jobs.py` | Leak-check job `func_ref` module changed; not observable (memory jobstore, not exposed) | none |
| I2 | INFO | `services/jobs.py:52` | Watcher/leak failure records' `funcName`/`lineno` now `job_session` (name/message same) | none |
| I3 | INFO | `tests/server/test_notify.py` | Callback-argument identity is not asserted | optional `assert client is notifier` |

Worktrees `gh-fps-base` / `gh-fps-head` removed after the review.

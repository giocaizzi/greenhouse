# Refactor notes

Running log for the behavior-preserving refactor on `claude/focused-hawking-7to7o3` (baseline `main` @ `a1b2622`).

## Owner decisions

- **No `src/` layout.** The owner wants to keep the `libs/` uv workspace (`libs/greenhouse-{core,server,cli}/greenhouse_*`).
  The PyPA src-layout recommendation (§1.1) is deliberately not applied; hatch package paths and pytest `pythonpath`
  stay as they are. Restructuring happens inside each package.
- **Method-level cleanup is in scope.** The owner wants methods made slick and clear with clean interfaces, not only
  module reorganization. The plan includes a per-method pass (extract function, guard clauses, naming, typed explicit
  parameters, parameter objects, CQS). Frozen public contracts still bound it; internal signatures may change when all
  call sites move in the same commit.
- **Non-functional fixes may be queued freely; doc-contract text may change, reviewed** (2026-10-02): see
  `refactor/BRIEF.md` owner decisions. This relaxes the golden policy for exactly one case: docstring-only commits in
  the lint-ratchet stage may regenerate the OpenAPI / MCP-tool / CLI-help goldens, with a reviewer confirming the
  golden diff is description text only.
- **Branch:** work lands on `claude/focused-hawking-7to7o3` (the session's designated branch) instead of
  `refactor/clean-structure`.

## Observed bugs (not fixed)

1. **`WWW-Authenticate` header dropped on JSON 401s.** `greenhouse_server/web/exception_handlers.py:41-44` — the global
   `HTTPException` handler returns `JSONResponse({"detail": exc.detail}, status_code=exc.status_code)` without
   `headers=exc.headers`, so the `WWW-Authenticate: Bearer` header raised for 401s on `/api/v1` and `/mcp` never reaches
   the client. Found by the contract extractor (probe script in the scratchpad). To be pinned by a characterization test
   in Phase 1 (asserting the header is **absent**). Not fixed.

2. **SAFETY — `dry_run_global` is never enforced.** The preference (labelled "Global dry-run (never actuate)" in the TUI,
   `tui/resources.py:113`) is stored (`models.py:422`), editable via API/web/CLI/TUI and shown in the web context
   (`web/context.py:49`), but no actuation path reads it (`grep -rn dry_run_global libs/` — only storage/display hits).
   Verified by the orchestrator. Not fixed (behavior change); recommend a dedicated fix PR.
3. **SAFETY — IK10PW keep-alive fallback can leave the pump on until the device's own auto-off.**
   `devices/irrigators/ik10pw.py:160-177`: `self.on()` runs first, then `signal.signal(SIGTERM, …)` is called *outside*
   the `try`. `signal.signal` raises `ValueError` when called off the main thread (APScheduler worker / FastAPI
   threadpool), so the `finally` that sends `off()` never runs and the error propagates with the pump ON. Bounded by the
   firmware auto-off timer the keep-alive is meant to refresh. Verified by reading; to be pinned by a characterization
   test. Not fixed; recommend a dedicated fix PR.

Further suspected bugs (B-3…B-25: offline flag after every sync, caps never checked in the automatic pipeline,
re-raised alerts not re-notified, `water_warning` meaning differs between engine and health monitor, 500 on duplicate
device id in web create routes, vacation end < start accepted by the API, `local_key` returned in plain text, …) are
listed with file:line evidence in `refactor/00-smells.md` ("Observed bugs"). They are recorded, not fixed; each one that a
refactored module touches gets a characterization test pinning current behavior.

CLI (pinned in `tests/cli/test_contract_json_output.py`, details in `refactor/10-safety-cli.md`):
- a whitespace-only `GREENHOUSE_API_TOKEN` disables the token file fallback;
- only connection errors are turned into a clean error — a timeout or an empty 2xx body crashes with a traceback (exit 1);
- a 422 validation error is printed as a Python repr;
- id `0` is treated as "not given" by `check`, `plant list`, `sensor list`;
- `stats --export` with a non-CSV response writes an empty file and reports success;
- scheduler job ids are interpolated into the URL path unescaped;
- empty `--device-ip` on `irrigator add` is dropped while empty `--local-key` on `irrigator update` is sent;
- each call opens an `httpx` client that is never closed.

Devices / ingress / auth (pinned; details in `refactor/10-safety-ingress-devices.md`):
- B-2 confirmed by test: `test_keepalive_off_main_thread_current_behavior_leaves_pump_on`,
  `test_start_off_main_thread_current_behavior_raises_after_switching_on` (ON sent, `ValueError`, OFF never sent).
- B-3 reproduced: a healthy sensor is flagged offline 31 min after every sync; the alert re-opens and only notifies the
  first time (`test_sensor_offline_flap_current_behavior_flags_offline_31min_after_every_sync`).
- B-21: a humidity-only live environment reading is dropped.
- `verify_password` raises `VerificationError` on a truncated stored hash instead of returning `False` (login → 500).
- The weather forecast cache ignores `hours` within its 600 s window.

Settings / schema (pinned; details in `refactor/10-safety-contracts-static.md`):
- `GREENHOUSE_*`-aliased settings (MCP token, ntfy, auth secret/admin) are also read from the bare field name and from
  `IRRIGATION_<FIELD>` (e.g. `MCP_TOKEN` or `IRRIGATION_AUTH_SECRET_KEY` configure the server).
- The migrated schema differs from `Base.metadata.create_all`: migrations add four server defaults and two named unique
  constraints, so `tmp_db`-based tests run on a slightly different schema than production.
- Test infra: every `create_app` replaces root log handlers, so `caplog` sees nothing afterwards.
- Pre-existing pytest quirk: `tests/server/X tests/<core file> tests/server/Y` on one command line → "fixture 'client'
  not found" for Y; group test paths by directory.

Decision engine (pinned; details in `refactor/10-safety-engine.md`):
- a decision can end with zero reasons (temperature-only data): skip, confidence 0.5, `primary_code` NULL in the log;
- critical stress keys on the **average** soil moisture, not the driest plant (contrast with invariant #2);
- `constants.py` values are bound by name at import (`from … import`), so patching `constants.X` does not affect the
  engine — only patching the name inside `engine.py` does (relevant to how invariant #5 can be tested);
- cleaning readings twice can drop more values than once (not idempotent);
- with < 5 readings a spike is not filtered and can trigger very-dry irrigation;
- `parse_moisture_target` does no validation; 0 °C / 0 % plant bounds are treated as missing;
- cooldown (6 h) and leak hold (24 h) are inclusive at the exact edge;
- critical stress, water warning and the no-sensor fallback bypass vacation rationing;
- light thresholds use the UTC month while seasons use the preferences timezone.

Orchestration (pinned; details in `refactor/10-safety-orchestration.md`):
- B-1 (`dry_run_global` ignored), B-4 (caps not checked in the automatic pipeline; global caps ignored), B-5 (re-raised
  alert not re-notified), B-6 (device-health block not written to `decision_logs`), B-8 (vacation end < start accepted)
  — each pinned by a `test_*_current_behavior_*` test.
- **SAFETY:** a cluster that crashes *after* actuating in `check_all_clusters` is rolled back including its `start`
  event, so the cooldown can't see that pump run (a second irrigation can follow sooner than 6 h).
- `/monitor` cleans only a 2 h slice — too few samples for the spike filter — so a single spike reports `very_dry`.
- `force=true` records the start event and push as `auto` and schedules a leak check, while the decision log says manual.

Web UI (pinned; details in `refactor/10-safety-web.md`):
- bulk stop-all reports "Every device is now off." even when a device reports a failed stop (`services/bulk.py`);
- ack/resolve of a missing alert returns 200 with a success toast instead of 404;
- bare `int()`/`float()` on form fields → unhandled plain-text 500 (config, global config, plants, `temp_override`,
  plants/sync);
- `POST /clusters/999/irrigate` → 500 (decision panel template crashes);
- deleting a populated cluster leaves orphan windows, decision logs, alerts and sensor assignments;
- unknown paths and 405s return JSON even to browsers: the handler picks HTML vs JSON by path, not by `Accept` as its
  docstring says;
- `now_text` uses the process-local timezone (stable in tests only because `clean_env` sets `TZ=UTC`).

TUI (pinned; details in `refactor/10-safety-tui.md`):
- on a 401 the dashboard shows "Cannot reach the server" behind the sign-in dialog;
- `app.tcss` `#global-config` matches no widget (dead CSS);
- the Activity table cursor jumps to the top row on every refresh (harmless: no row actions);
- the `ConfirmScreen` docstring / CLAUDE.md claim every actuating key confirms — see the actuation golden for the real
  table (`S`, `P`, `H`, alert `k`/`v`/`y`, scheduler resume run without a dialog);
- TUI renders are tied to the locked Textual / plotext / rich versions — a dependency bump regenerates those goldens in
  its own commit; the `system` render reads the process-wide scheduler.
- **Overlapping dashboard reloads can crash the TUI**: when a load takes longer than the refresh period, two loads
  interleave in `DashboardScreen._render_cards` and the app dies with `WorkerFailed: NoMatches('#cluster-card-2')`
  (`.first()` at `tui/screens/dashboard.py:68`). Found while hardening the TUI tests; not fixed.
- One TUI contract test failed once under heavy machine load (4 cores shared by several agents) and passed in 6
  subsequent runs; watched at Gate 1.

Safety-net hygiene: test session JWTs in `set-cookie` goldens are stored as `<JWT sha256=…>` (still exact) so
gitleaks stays clean; gitleaks' pre-commit hook only scans staged changes, so the branch was also scanned with
`gitleaks detect --log-opts=a1b2622..HEAD` (no leaks).

Mutation campaign (Gate 1; details in `refactor/gate1/mutation.md`):
- the spike filter drops the first sample of a genuine step change (e.g. 40,40,40,60,60) — pinned as
  `…_current_behavior`;
- equivalent mutants exposed dead/redundant code: `StressIndicators.any_critical()` has no caller; the
  `start == end` guard in `is_within_quiet_hours` duplicates `_hour_in_range`. Candidates for dead-code removal in
  dedicated, test-backed commits.
- mutmut 3 cannot run on this layout (it keys mutants by file path `libs.greenhouse-core.…` while tests import
  `greenhouse_core.…` via pytest `pythonpath`); the scripted runner `refactor/gate1/mutate.py` (473-mutant catalogue)
  is the reusable mutation tool for Phase 3/5.

Pump watcher (found by the WP4 reviewers; pre-existing, not fixed):
- if any DB write fails during a trip, the watcher's own except handlers raise `PendingRollbackError` (expired ORM
  state), so the commit/rollback steps never run (the pump is already stopped);
- if the health monitor swallows a failed flush (e.g. SQLite "database is locked"), `_handle_trip` still raises.

Process note: Reviewer 2's differential harness (real SQLAlchemy) caught a behavior difference in WP4's first T4.12
attempt (an `irrigator.id` read moved before `commit()`); T4.12 was reverted and redone. See
`refactor/reviews/wp4-pump-watcher-r2.md`.

TUI (wave C):
- search-table column widths only ever grow: an early partial query can leave the final table wider than its rows
  need (root cause of the old `search_citrus` flake);
- `SpriteView._animate` shadows a Textual attribute (pre-existing; one targeted `type: ignore`).

Consistency audit (2026-10-03):
- **B-N1:** `GET /clusters/{id}/monitor` syncs stale sensors from the Cloud but never commits, so the synced rows are
  discarded and every call hits the Cloud again; the web monitor skips the sync. Tracked as drift pair D15.

- **B-N2:** `GET /api/v1/clusters/{id}/stats` for a cluster without an irrigator → 500 (`StatsResponse(**{"error": …})`
  raises `ValidationError`). Queued as D19 in the consistency track.
- **OD3 applied (core):** pre-Alembic database repair removed — a pre-Alembic DB now fails at startup with "table already
  exists" (SQLite DDL is not transactional: tables created before the failure remain plus an empty `alembic_version`;
  such a DB must be stamped manually). See `refactor/wp-handoff/CONS-W1.md`.

- **S6 (architecture review A1) reproduced — shared `DeviceHealthMonitor` rebind during a pump watch.** The single
  `app.state.health_monitor` is re-pointed by every job via `bind_repo` (`services/health_monitor.py:117`; callers
  `scheduler.py:327,381`, `services/irrigation.py:201`), and its alert cache is a plain dict with no lock. When another
  job rebinds it while the minutes-long pump watcher runs, the watcher's NO_WATER trip (`pump_watcher.py:343`) writes
  the alert into the *other* job's session; the watcher commits only its own session (aborted event durable, alert
  missing). If that session rolls back or is closed without commit, the alert is lost, yet the cache already marks
  NO_WATER as raised: actuation stays blocked with no inbox alert, and the alert is never raised again (later NO_WATER
  reads are not transitions). On one shared SQLite file the rebound write fails with "database is locked" instead
  (swallowed by the monitor) — same loss, same cache state. Pinned by
  `test_pump_watcher_trip_current_behavior_alert_written_through_rebound_repo_and_cache_suppresses_reraise` (`tests/server/test_health_monitor.py`). Not fixed; fix = repo-per-call monitor + lock (A1).

## Labeled behavior changes landed (drift + consistency; details in refactor/wp-handoff/DRIFT.md, CONS-W1.md)
- API/MCP/CLI: reversed vacation create → 400; plant sync for unknown cluster → 404; sensor update with another
  cluster's plant → 404; `/monitor` commits the readings it refreshes (fewer Cloud calls) and the web monitor now
  refreshes stale sensors + 404s unknown clusters; efficacy `days` ≤ 365 (422 above); manual stop recorded as `stop`
  (old rows keep `off`, so history may show both); malformed / `"null"` stored device config returned as `{}`
  (was 500); `irrigator add --device-ip ""` sends the empty value.
- Web/TUI: web irrigator/sensor create handles duplicates and cross-cluster plants like the API; unified window and
  404 messages; check-all banner uses the API rule; vacation times parsed + shown in the timezone preference
  everywhere; plant dashboard shows "never" / real age; TUI config tables use repository field order.
- Removed: pre-Alembic DB repair (OD3); dead `export_csv`, `print_stats_report`, and other dead code.
- Consistency W2/W3: sensor-age web cells show the real age (D18); `/clusters/{id}/stats` without irrigator returns
  zero totals instead of 500 (D19); chart soil band uses the shared moisture-target parser (D10b, malformed targets
  change); silent `except` blocks now log at DEBUG (B5); TUI config form hint (B7); **OD3:** startup migration of old
  `pump_dry_run` alerts removed and the deprecated `IRRIGATION_CHECK_INTERVAL_HOURS` setting removed (old `.env` files
  using it are no longer translated to the cron setting).
- Merge note: `refactor/integration/after-drift.txt` — 3053 passed after merging drift on top of consistency W1.

## Golden-test policy (orchestrator decision)

- OpenAPI, routes, MCP tools, settings, DDL, scheduler registry, package data, web HTML, CLI help/output, TUI renders,
  decision grid: **strict** equality.
- Public import surfaces, `constants.py` values and logger names: **compatibility (superset)** — every golden name must
  still resolve with an equal value (loggers: same name for modules that still exist); additions are allowed because
  they break no consumer, and rule 8 forbids editing goldens after Gate 1. Enum members (`TriggerCode`, …) stay strict.
- `tests/golden/` is excluded from the pre-commit whitespace fixers so snapshots stay byte-exact; goldens stay < 400 KB.

## Test-suite hazards found at baseline (recorded)

- **Real network in the existing suite.** `get_weather_client` is never overridden in test fixtures, so ~19 tests call the
  live Open-Meteo API through `services/weather.py:WeatherClient` (or fall back to 20 °C after a timeout when offline).
  Their assertions are loose enough to pass either way. New safety-net tests are hermetic (weather stubbed).
- **Wall clock everywhere.** The engine reads `time.time()` directly (`logic/engine.py:129`); the baseline migration
  seeds quiet hours 00:00–05:00 UTC into every app. Snapshot/golden tests freeze the clock.
- `test_cli.py` can read the real CLI token from the user config dir; `Settings` reads `.env` / `IRRIGATION_*` /
  `GREENHOUSE_*` from the environment.

## Doc/code mismatches and dead code noticed (not changed)

- `DEFAULT_QUIET_START_HOUR` / `DEFAULT_QUIET_END_HOUR` in `constants.py` are unused; the live 00–05 default is a
  literal inside the baseline Alembic migration (out of scope: migrations are frozen).
- CLAUDE.md says every actuating TUI key goes through `ConfirmScreen`; in code `i` (irrigate) and `w` (water now) open
  their own dialogs, and scheduler resume (`p`) / sync (`S`) run without confirmation. Current behavior is pinned as is.

## Baseline warnings (recorded, not fixed)

Gate 0 run on `a1b2622`, Python 3.11.15: **1182 passed, 2 warnings, 1168 s** (slowed by concurrent recon agents);
total coverage 90% (line+branch). Both warnings are pre-existing and left as is:

1. `PydanticDeprecatedSince211`: `BaseModel.__get_pydantic_core_schema__` deprecated — raised from inside pydantic's
   schema generation (dependency path), not from repo code.
2. `RuntimeWarning: coroutine 'ClusterScreen._load_plant_health' was never awaited` in
   `tests/cli/test_tui.py::TestClusterCrud::test_plant_add_edit_move_delete` (Textual cache path).

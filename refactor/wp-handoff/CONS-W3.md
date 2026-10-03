# CONS-W3 hand-off — consistency group W3 (services / scheduler / config / CLI / TUI)

Worktree `/home/user/gh-cons3`, branch `refactor/consistency-3`, based on `971aa93` (integration branch after drift +
W1). Not pushed, not merged, not rebased onto other lanes. Scope: the W3 rows of `refactor/46-consistency-audit.md` §3
plus the appended W3 items, OD2/OD1/OD3 for this lane's files. No route / web / deps / app / auth (W2) and no engine /
timing / devices / core sync (WP8) file was edited.

Every pytest run: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock … pytest -q -n 2`, ≤ 5-min holds, grouped by test
directory (mixing `tests/` and `tests/server/` in one invocation trips the recorded "fixture not found" quirk). Subset
and gate runs used snapshots of the commit under test (`git archive <rev>` into the scratchpad, `PYTHONPATH` pointing at
the snapshot so subprocess-based tests import the snapshot too) so edits in the worktree could not leak into a run.
Helpers: scratchpad `w3/trun.sh`, `w3/snap.sh`.

## Commits (behavior-preserving first, labeled behavior changes last)

| # | Audit | Commit | Notes |
|---|---|---|---|
| 1 | C-TX-2, C-REPO-4 (A10) | `a251e99` refactor(services): repo.commit/rollback/flush in services + `set_check_all_paused` | "commits because …" docstring notes (OD2); placement unchanged |
| 2 | C-TX-2 (pipeline) | `ced4b55` refactor(pipeline): `check_all_clusters` / `_record_check_failure` via repo | **G+** |
| 3 | C-REPO-1/2/3 (A10) | `022bdd4` refactor(services): repository methods `search_{clusters,plants,sensors,irrigators}`, `list_start_events_since`, `list_events_since`, `list_sensors_by_ids`, `get_open_alert_by_key` | queries moved verbatim (repository.py additions only) |
| 4 | C-CONST-6 (A12) | `ea6f450` refactor(services): SOURCE_/EVENT_ACTION_/TRIGGERED_BY_/ENTITY_ vocabulary | `SOURCE_PUMP` re-exported from alerts (test import); `SOURCE_HEALTH`, `EVENT_ACTION_ABORTED` now the models constants |
| 5 | C-DEAD-6 | `81fe2a8` refactor(services): remove dead code — SOURCE_DECISION, SOURCE_SYSTEM | |
| 6 | C-CONST-1/5 (A12) | `bfaee66` refactor(services): threshold + SECONDS_PER_* constants | weather keeps `_FORECAST_CACHE_TTL` (test-pinned) as an alias |
| 7 | C-CONST-4 (A13) | `0682d6a` refactor(app): Settings weather defaults = DEFAULT_LATITUDE/LONGITUDE | settings golden unchanged |
| 8 | C-LOG-1 (A15) | `145b936` refactor: `log` → `logger` in database.py / notify.py | imports.json: − `database.log` (approved removal-only) |
| 9 | C-NAME-2 private (A15) | `310d90b` refactor(scheduler): cloud → gateway locals / `SyncService._gateway` | `_get_cloud` KEPT (test-pinned, see deviations) |
| 10 | C-TX-3 (A14) | `faab47d` refactor(scheduler): `_job_session` → `services/_session.job_session`; `_run_leak_check` uses it | **HIGH RISK — two reviewers**, evidence below |
| 11 | T7.20/T7.21 | `493f20c` refactor(services): leak check `_check_sensor`, `_record_hold`, `_soil_series`, `_never_settled_reason` | coverage 100 % before; sizecheck clean; **flag** (leak hold) |
| 12 | C-RES-1/4/8 (A11, OD1) | `56efc48` refactor(services): ClusterStatus / ClusterHistory / JobInfo TypedDicts; PlantSyncResult / StopAllResult NamedTuples | scheduler touched (**flag**) |
| 13 | C-TYPE-2 (A21) | `36a8d71` refactor(types): strict-clean alerts, leak, manual_control, notify, sync, weather | |
| 14 | C-ENV-1, C-DUP-1, C-CONST-3 (A16) | `0d8ecda` refactor(cli): `server_url` helper; `greenhouse_cli/constants.py` (DEFAULT_SERVER_URL, ALL_WEEKDAYS) | env_reads golden diff (below) |
| 15 | C-STALE-11 | `f153ba6` refactor(cli): remove dead `ctx.ensure_object(dict)` | |
| 16 | C-CONST-7 (CLI/TUI) | `78aaee9` refactor(cli): HTTPStatus for 400 / 401 | |
| 17 | C-DEAD-11 (A17) | `3bf9cd5` refactor(tui): remove dead `#global-config` tcss rule (+ golden entry) | rule-4 removal in `tui/ids_and_selectors.json` |
| 18 | C-DEAD-2 | `743c3fd` refactor(core): remove dead `stats.format_duration` (+ 4 tests, 1 golden name) | rule 4 |
| 19 | §1.10, C-STALE-3 | `37ff66e` refactor(services): docstrings + stale PumpWatcher comment | |
| 20 | C-TYPE-2 (TUI) | `e353da9` refactor(types): strict-clean TUI app, resources, alerts/base/forms/modals screens (+ constants.py) | |
| 21 | D10b | `1f44fd2` **fix(drift)**: chart soil band reads targets via `parse_moisture_target` | new test |
| 22 | OD3 / C-LEG-1 | `80dcb7b` **fix(consistency)**: drop the legacy `pump_dry_run` alert migration | new test; **flag** (scheduler startup) |
| 23 | tooling | `ed63fdb` chore(refactor): mutate.py snippets for the CLI/TUI constant commits | |
| 24 | OD3 / C-LEG-3 | `787c471` **fix(consistency)**: remove deprecated `IRRIGATION_CHECK_INTERVAL_HOURS` | 4 goldens removal-only; **flag** (scheduler/config) |
| 25 | C-LOG-3 / C-ERR-4 (B5) | `3444d9a` **fix(consistency)**: DEBUG log on silent swallows (weather, maintenance, pump watcher) | |
| 26 | C-STALE-10 (B7) | `4615c84` **fix(consistency)**: TUI irrigator config hint `device_ip` / `local_key` | `tui/surface.json` 2 lines |
| 27 | docs | `e711743` docs(plugin): LOGIC.md chart-band sentence (D10b) | |
| 28 | sizecheck | `5a71a27` refactor(services): tighten three charts docstrings (file back to 400 lines) | docstrings only |

Per-commit evidence (commands + results) is in each commit body. Every commit: `ruff check` + `ruff format --check`,
`uv run lint-imports` (10 kept), `make typecheck`, `refactor/gate1/mutate.py --check` (INVALID set kept equal to the
base's 78, snippets updated in the same commit where a line they anchor on changed — except the four CLI/TUI ones,
fixed in `ed63fdb`; `scheduler-08/09` were dropped with the code they mutated in `787c471`, so 392/470 apply).

## Gates

- Union subset of commits 1–3 (snapshot `022bdd4`, 80 files: `$PIPE $CHECK $SCHED $CORE $RENDER $DEV` + D-maps of every
  touched service + search/efficacy/heatmap/inventory tests): all green (the two `test_contract_imports` failures in that
  run were the pre-PYTHONPATH snapshot artefact — they imported the live worktree mid-edit; green on rerun).
- FULL #1 at `ff9c287` (pre-rebase A17 commit): 3,052 passed, 1 failed — `test_ids_and_selectors_golden`: the TUI
  selector golden pins the dead `#global-config` rule. Red → the A17 commit was rewritten (not fixed forward) to carry
  the rule-4 golden removal (`3bf9cd5`); history after it was re-applied unchanged.
- Targeted runs after FULL #1 (each in the commit body): D10b 151 passed; OD3 pump 261 passed; OD3 interval 180 + 23
  passed; B5 856 + 23 passed; B7 120 passed; format_duration 28 passed.
- **End gate: FULL (seed 0) at `e711743`** — see "End gate results" at the bottom (`5a71a27` after it is docstrings-only,
  re-checked with ruff / format / typecheck / sizecheck). FULL contains the union of every task subset, `$CORE`, `$SCHED`,
  `$PIPE`, `$CLI`, `$TUI`.
- Static at HEAD: `ruff check libs tests refactor` OK; `ruff format --check` OK (322 files); `lint-imports` 10 kept;
  `make typecheck` 144 files OK; `git status --porcelain tests/golden` empty.
- `make sizecheck` at HEAD: 10 entries, none new — the two leak functions are gone; remaining: repository/schemas/
  scheduler/irrigation/health_monitor exceptions, `bulk.stop_all_irrigators` (excepted), engine `_apply_soil_moisture_rule`
  and core sync ×2 (WP8), `web/routes/clusters.py::cluster_detail`, `plant_dashboard` (W2).
- C90@8 on every touched module: only the pre-existing `commands/auth.py::register` (9) and
  `tui/screens/forms.py::parse_value` (11), both already in the per-file ignores, logic untouched.

## A14 (`faab47d`) — reviewer evidence

- Order preserved for all five scheduler jobs (same helper body; logger and message passed in, record name/msg/args
  identical; the scheduler imports it as `_job_session` so its frozen `dir()` surface is unchanged).
- `_run_leak_check`: one intentional Session-API difference on the "already done" early return — it now calls
  `session.rollback()` before returning so the helper's `commit()` finds no transaction (SQLAlchemy 2 emits no COMMIT).
  Before: `close()` rolled back the read-only transaction. **DB-level trace identical.**
- `scratchpad/w3/diff/leakrepro.py` (old = `HEAD^` tree, new = commit tree): five paths (marker missing then present, no
  app, failing check, failing commit, missing session_factory) — engine events (SQL verbs, COMMIT, ROLLBACK) + greenhouse
  log records byte-identical; only the spy on Session methods differs on the early return (`close` → `rollback, commit,
  close`).
- WP7 differential harness (`scratchpad/w3/diff`, copy of `wp7-diff/harness.py` with the tests path as env var): 1,026
  scenarios with `run_jobs` / `rearm` from `scenarios.json` + `scenarios_rearm.json`: `compared=1026 differ=0`
  (18 harness_errors, identical in both trees).

## Golden diffs (each inspected, each in its own commit)

- `contracts/imports.json`: − `greenhouse_core.database.log` (`145b936`); − `greenhouse_core.stats.format_duration`
  (`743c3fd`); − `greenhouse_server.config.model_validator` (`787c471`). Removal-only.
- `contracts/env_reads.json` (`0d8ecda`): the `IRRIGATION_SERVER_URL` reads in `commands.auth` and `commands.tui` are
  gone (moved into `_helpers.server_url`); the remaining read's recorded default is `"<expr: DEFAULT_SERVER_URL>"`
  instead of the literal — the value stays pinned by `test_cli_server_url_env_and_default` and the CLI help golden.
- `tui/ids_and_selectors.json` (`3bf9cd5`): `#global-config` out of the tcss token list and of
  `tcss_tokens_without_a_mounted_widget` (now `[]`).
- `contracts/settings_schema.json`, `contracts/settings_validation_errors.json`, `contracts/scheduler_jobs.json`
  (`787c471`): the `check_interval_hours` field, the six `legacy=*` validation cases, the `legacy_interval` scenario.
- `tui/surface.json` (`4615c84`): the two irrigator-form placeholder lines.
- `contracts/loggers.json` still lists the attribute key `"log"` for database/notify; it is checked by logger *name*
  (superset) so it passes — INT may regenerate it cosmetically.

## Tests edited / added (labeled commits and rule-4 removals only)

- `tests/test_stats.py`: − `TestFormatDuration` (rule 4).
- `tests/server/test_web_charts.py`: + `test_plant_chart_soil_band_uses_the_shared_target_parser` (D10b).
- `tests/server/test_health_monitor.py`: − `TestLegacyMigration`; `tests/server/test_contract_scheduler.py`:
  + `test_init_health_monitor_leaves_old_pump_dry_run_alerts_alone` (fails on the parent).
- `tests/server/test_contract_settings.py`, `test_scheduler_settings.py`, `test_scheduler.py`,
  `test_contract_scheduler_registry.py`: legacy-interval cases removed / replaced by "the old variable is ignored" tests.

## Strict-list additions (`refactor/mypy-strict.txt`, union + sort)

`greenhouse_server/services/{_session,alerts,leak,manual_control,notify,sync,weather}.py`,
`greenhouse_cli/{constants,tui/app,tui/resources,tui/screens/alerts,tui/screens/base,tui/screens/forms,
tui/screens/modals}.py`. Every module in this lane's file set is now on the strict list.

## Deviations / not done (and why)

- **C-NAME-2 `_get_cloud`**: the first rename commit renamed it and `test_contract_scheduler::test_get_cloud_reads_app_state`
  calls it by name → red; the commit was rewritten (not fixed forward) to keep `_get_cloud` (`310d90b`). Renaming it needs
  a test edit (owner/INT).
- **A11 partial**: nested status/history rows stay `dict[str, Any]` and `ClusterStatus.cluster` is `Any`, because
  `routes/operations.py` (W2) types its mappers `dict[str, Any]` and passes the row straight into `ClusterResponse`;
  narrowing breaks that module's strict typing. After W2 (C-RES-6 `model_validate`): type `cluster: Cluster`, add
  `SensorStatusRow` / `IrrigatorStatus` / history-row TypedDicts and retype the route mappers.
  `SyncService` results (core `sync_sensor_data` returns a bare `dict`) and `WeatherClient` (`WeatherNow` /
  `ForecastWindow` would clash with WP8's planned `RainForecast` Protocol if it declares `dict[str, Any]`) wait for WP8.
- **C-ERR-1** (`app.py:77`), **C-DUP-5** (`getattr(app.state, …)` accessors — needs a module shared with deps/auth),
  **B2 / C-TX-4** (auth + handlers share one session) and **B9 / C-404-9** (services raise NotFoundError, one handler)
  touch W2 files → not done here.
- **TUI method docstrings**: ~90 `D102` on Textual screens/widgets (`compose`, `action_*`, `on_*`, helpers) left for
  the 40-lint `D` queue; Typer command docstrings and `Settings`' class docstring are doc-contract text (help / settings
  schema `description`) → doc-contract stage.
- `check_failed`'s `severity="error"` stays a literal (no `Severity` member; W1 left the decision to W3 — keep, it is
  the stored value the web inbox renders).
- `services/irrigation._run_pump_watcher`, `rearm_leak_checks`, `scheduler.init_health_monitor` keep their own session
  scaffolding (conditional commit / no commit / silent wiring) — different shapes than `job_session`.

## For the integrator (ratchet edits, other lanes)

- `refactor/size-exceptions.txt`: `services/health_monitor.py` is 384 lines now → its file-length exception (line 26)
  can go. `services/leak.py` needs no entry (both functions within limits).
- `pyproject.toml` `ignore_imports`: unchanged; `services/_session.py` imports nothing from the scheduler.
- W2: web `HTTPStatus` 503 (C-CONST-7 web part); silent swallows in `web/context.py` and `app.py` (B5 web part);
  `cluster_detail` / `plant_dashboard` sizes.
- WP8 / phase D: silent swallows in core `sync.py`, `devices/*`, `utils.py`, `logic/plant_needs.py` (B5 rest);
  `SyncStats` TypedDict shared by core + `SyncService` (C-RES-2); weather TypedDicts once the engine Protocol exists.
- After W2's C-RES-6: tighten `ClusterStatus` as described above.

## Reviewer focus (hardest first)

1. `faab47d` (A14) — scheduler + pipeline job scaffolding; evidence above.
2. `ced4b55` — check_all per-cluster transaction (pinned by `test_contract_check_all` tx_log).
3. `493f20c` — leak decomposition: per-sensor order, evidence key order, leak_hold payload.
4. `787c471` / `80dcb7b` — OD3 removals (scheduler/config startup), golden diffs.
5. `56efc48` — `JobInfo` in scheduler.py; `ClusterStatus.cluster: Any` interim.

## REFACTOR_NOTES-ready text

> **OD3 — legacy pump dry-run alert migration removed** (`fix(consistency)`, `80dcb7b`). `init_health_monitor` no
> longer resolves open alerts with the pre-unification code `pump_dry_run` (dedup key `pump::pump_dry_run::…`) at
> startup; `DeviceHealthMonitor.migrate_legacy_pump_alerts` and `health_monitor.LEGACY_PUMP_DRY_RUN_CODE` are gone. A
> database that still has such an open alert keeps it until it is resolved by hand (`POST /api/v1/alerts/{id}/resolve`,
> the web inbox or `greenhouse alerts resolve`). The pump watcher's `pump_dry_run` *activity* code is unchanged.

> **OD3 — `IRRIGATION_CHECK_INTERVAL_HOURS` removed** (`fix(consistency)`, `787c471`). The deprecated variable is no
> longer read (Settings ignores unknown variables): a deployment that relied on it now checks hourly (`*`) with no
> warning, and an unsupported N no longer stops the server. Set `IRRIGATION_CHECK_CRON_HOURS=*/N` instead.
> `Settings.check_interval_hours` and `Settings.check_cron_hours_explicit` are gone; `scheduler._resolve_check_cron_hours`
> returns `settings.check_cron_hours`.

> **D10b — chart soil band** (`fix(drift)`, `1f44fd2`). The plant chart's water-needs threshold band now reads
> `soil_moisture_target` through `parse_moisture_target`: `"40-60-80"` → 40–60 (was the default band, source `default`);
> a non-numeric `"x-y"` → the default band but labelled `water_needs:<level>`. Well-formed targets (all of
> `plant_database.json`) are unchanged.

> **B5 (partial)** (`3444d9a`): weather (current / forecast), `collect_learning_alerts` and the pump watcher's
> rollback-after-failed-commit now log their swallowed exception at DEBUG with a traceback (two new module loggers:
> `greenhouse_server.services.weather`, `greenhouse_server.services.maintenance`).

> **B7** (`4615c84`): the TUI irrigator form's config hint reads `{"device_ip": "…", "local_key": "…"}`.

> Doc/code mismatches resolved: `#global-config` dead TUI rule (REFACTOR_NOTES entry) removed (`3bf9cd5`);
> `stats.format_duration` removed (`743c3fd`), so `stats` has no dead public helpers left.

## End gate results

FULL, seed 0, `-n 2`, snapshot of `e711743`, 12 directory chunks covering all 138 test files (30 core, 93 server,
12 cli, 3 devices): 325 + 323 + 32 + 246 + 653 + 242 + 177 + 118 + 97 + 84 + 597 + 135 = **3,029 passed, 0 failed**
(base 3,053; the difference is the removed `TestFormatDuration`, `TestLegacyMigration` and legacy-interval
tests/parametrizations, net of the new D10b / OD3 tests). This run contains
the union of all task subsets, `$CORE`, `$SCHED`, `$PIPE`, `$CLI` and `$TUI`. `5a71a27` (docstrings only) re-checked
with ruff, `ruff format --check`, `make typecheck` and sizecheck. Seed-12345 and `TZ=America/New_York` runs are left to
the final gate (sprint mode).

# Refactor notes

Notes for PR #108 on `claude/focused-hawking-7to7o3`: the behavior-preserving refactor (baseline `main` @ `a1b2622`,
safety net frozen at the local tag `refactor-gate1`, refactor complete at `01692d4`) and the **bug-fix fold-in** that
followed it in the same PR, which fixed the bugs the refactor had recorded. This file is self-contained. The short ids
in parentheses (B-n, S-n, D-n, OD-n) are labels kept only because test docstrings and commit bodies use them.

## Owner decisions

- **No `src/` layout.** The `libs/` uv workspace stays (`libs/greenhouse-{core,server,cli}/greenhouse_*`). Hatch
  package paths and the pytest `pythonpath` are unchanged. Restructuring happens only inside each package.
- **Method-level cleanup is in scope.** This covers small single-purpose functions, guard clauses, names, typed
  explicit parameters, parameter objects and command–query separation. Frozen contracts still bound it: route names,
  docstrings and `response_model`s, public import paths, Pydantic class and field names, Textual `action_*`/`on_*`/ids,
  and CLI options. An internal signature may change only when every call site moves in the same commit.
- **Non-functional fixes may be queued freely** (2026-10-02).
- **Doc-contract text may change after review** (2026-10-02). This covers route docstrings (MCP tool descriptions),
  schema docstrings (OpenAPI descriptions) and Typer help. Each edit is a dedicated `docs(api|cli)` commit. It
  regenerates only the affected goldens, and a reviewer confirms the diff is description text only.
- **Prune dead code along the way** (2026-10-02). Evidence comes first: no references across libs, tests, templates,
  `app.tcss`, `plugin/` or entry points, and no dynamic access. Each removal group gets its own
  `refactor(<area>): remove dead code — …` commit. A dead name on a pinned import surface is removed together with its
  golden entry, and the diff must be removal-only. Never treated as dead: handlers, Textual actions, scheduler jobs,
  and models that OpenAPI references.
- **Remove drift everywhere** (2026-10-02). Divergent duplicate copies are unified in labeled `fix(drift): …` commits,
  one pair per commit. By default the canonical side is the stricter, validated, API side.
- **Land a clean, consistent end state** (2026-10-02). An observable change is a labeled `fix(consistency): …` commit
  with reviewed test and golden updates.
- **OD1** Seven services keep returning core read models. Services that return dicts get TypedDict results.
- **OD2** Handlers commit CRUD. A service commits only when a side effect must follow a durable write. There is one
  `repo.commit()/rollback()` API, and jobs go through one session helper.
- **OD3** Remove all legacy compatibility code (pump-dry-run alert migration, `tuya_cloud`/`tuya_local` device-type
  aliases, pre-Alembic DB repair, `IRRIGATION_CHECK_INTERVAL_HOURS`) as labeled commits.
- **OD4** A manual stop records action `"stop"`. Existing rows are unchanged.
- **OD5** The `refactor/` working folder was deleted at the end; only `docs/wip/REFACTOR_NOTES.md` and `docs/wip/REFACTOR_REPORT.md` stay. The
  strict list moves to `[tool.mypy] files`, sizecheck moves to `scripts/`, and the Makefile is updated.
- **Drift confirmations** (2026-10-03):
  - D8: vacation times are parsed and displayed in the `timezone` preference on every interface.
  - D7: the plant dashboard shows "never" or the real age, never "stale".
  - D2: a cross-cluster plant on sensor create or update is a 404 everywhere.
- **Branch.** Work lands on `claude/focused-hawking-7to7o3`, not on `refactor/clean-structure`.

### Bug-fix fold-in decisions (2026-10-11)

- **Fix every recorded bug in the same PR.** One bug per commit, labeled `fix(<area>): <what> (<id>)`; the body names
  the bug, the fix and the test/golden change. The `*_current_behavior*` test that pinned a bug becomes a test of the
  fixed behavior (renamed); an unpinned bug gets a new test that is red before and green after. Goldens move only for
  the cases a fix affects.
- **S1 global dry run.** With `dry_run_global` on, **no start actuates**: automatic runs, `/irrigate` (`force`
  included), manual `POST /irrigators/{id}/start` and the web/TUI/CLI starts all evaluate and log (decision log,
  activity, a `dry_run_global` code or a 409 refusal) but never switch a pump on. Stops, stop-all and shutdown stops
  always work.
- **Engine quirks: fix all.** Critical stress keys on the driest plant (invariant #2); light thresholds use the
  preferences-timezone month; a spike guard for series too short for Hampel; validated soil targets; 0 °C / 0 % are
  real values; every decision carries at least one reason; the other engine bugs too.
- **The soil rule judges current readings** (second D review): each sensor's newest cleaned reading against the band
  of the plant it is linked to, not the 24 h minimum of the pooled cluster.
- **Emergency runs obey windows and vacation budgets** (critical stress, water warning, the no-sensor fallback).
- **Time edges are exclusive** (cooldown 6 h, leak hold 24 h, a vacation ending exactly now is over).
- **TUI:** scheduler resume and the plant-DB sync (`P`) ask for confirmation too.
- **Secret env names:** aliased secrets (MCP token, ntfy, auth secret/admin) are read from `GREENHOUSE_X` only; bare
  `X` and `IRRIGATION_X` are no longer read (a startup WARNING names any that are still set).
- **`local_key` is masked** in API/MCP irrigator **and sensor** responses exactly like the web; writes still accept a
  key, and sending the mask back keeps the stored key.
- **Default bind 127.0.0.1.** The Docker image and `docker-compose.yml` opt in to `0.0.0.0`.
- **A forced run (`force=true`) is a manual run:** decision log, event and push say `manual`; it keeps the leak check
  and the dry-run watcher, and it is stopped at shutdown like an automatic run.
- **Every real start gets the post-watering leak check** (automatic, forced, manual with a duration); a hand-logged
  watering gets none.
- **The IK10PW keep-alive fallback is removed.** A bounded start without the local Duration DP is refused before any
  ON is sent.
- **Package `__init__` re-exports resolve lazily** (`greenhouse_core/_lazy.py`), so importing a leaf module stays cheap.

## Labeled behavior changes landed

Two batches. The refactor's 34 `fix(drift|consistency)` commits (`a1b2622..01692d4`) come first; the bug-fix fold-in's
`fix(<area>)` commits (`01692d4..HEAD`) follow under "Bug-fix fold-in". Each commit body has before/after details and
the exact test and golden diffs.

### Drift (`fix(drift)`)

| Change | Where (shared home) | Pinned by | Commit |
|---|---|---|---|
| D1 One irrigator registration path. The web form answers a duplicate device id with 409 instead of 500 (fixes B-7, part 1). | `services/inventory.create_irrigator` | `tests/server/test_contract_web_mutations.py` (`create_*__duplicate_device_id`) | `c54e86a` |
| D2 Sensor create and update share the plant-in-cluster rule and duplicate-id handling. API `PUT` now also rejects another cluster's plant (404 `Plant N not found in cluster`) (fixes B-7, part 2). | `services/inventory.{ensure_plant_in_cluster,create_sensor}` | `tests/server/test_sensors.py`, `test_contract_web_mutations.py`, OpenAPI/MCP goldens | `abbe4a9` |
| D3 One irrigation-window validator, using the API wording. The web loses "Select at least one weekday." | `services/windows.validate_window` | web goldens `web/mutations/*window*` | `fe9bf91` |
| D3 follow-up: web window parse errors drop their trailing "." | web window routes | 2 web goldens | `acc9f91` |
| D4 The web check badge uses the API `has_alerts` rule (alerts ∨ maintenance ∨ needs_water) (fixes B-13). | `services/irrigation.check_has_alerts` | goldens `web/mutations/check_{all,single}.json` | `e8af945` |
| D5 Every vacation write path checks `starts_at < ends_at`. API/MCP/CLI `POST` now returns 400 (fixes B-8). | `services/vacation.validate_vacation_range` | `tests/server/test_contract_pipeline.py`, OpenAPI/MCP goldens | `76bc13e` |
| D6 `deps.require_*` lookups give one 404 wording per entity in the API and the web. | `deps.require_{cluster,irrigator,plant,sensor,…}` | `tests/test_refactor_guards.py`, web `*__404` goldens | `63c93a5` |
| D7 One relative-time formatter. The plant dashboard shows "never" or the real age ("12d ago"). | `web/filters.relative_age` | `tests/server/test_contract_wp5_gaps.py` | `eaeb534`, `831183d` |
| D8 Vacation times use the `timezone` preference in the web and TUI. API/CLI stay in Unix seconds. | `tui/screens/forms.py`, `web/routes/vacation._preference_zone` | `tests/cli/test_tui.py`, `tests/server/test_web_vacation.py` | `f849e79` → `e2ee93f` |
| D9 CLI `irrigator add/update` use one `is not None` rule. `--device-ip ""` is now sent. | `commands/irrigators._device_config` | `tests/cli/test_contract_json_output.py` | `8faca8b` |
| D10 Every soil-target reader goes through `parse_moisture_target` (monitor, check, learning issues). | `logic/plant_needs.moisture_target_range` | `tests/test_moisture_target_range.py`, wp6/wp7 gap tests | `a77a7aa` |
| D10b The plant chart's soil band uses the same parser. `"40-60-80"` → 40–60; non-numeric → default band labelled `water_needs:<level>`. | `services/charts` | `tests/server/test_web_charts.py` | `1f44fd2` |
| D12 Plant-DB sync of an unknown cluster returns 404 in the API and the web, instead of `synced=0` (fixes B-16). | `services/cluster.ClusterNotFoundError` | `test_contract_wp5_gaps.py`, OpenAPI/MCP goldens | `4034fb6` |
| D13 TUI config tables list fields in repository order. | TUI `render` config rows | goldens `tui/screens/{cluster_1_config,settings}.txt` | `5e0d9b0` |
| D14 The dead `stats.export_csv` copy is deleted; the route CSV stays. | `services/cluster.cluster_events_csv` | `tests/test_contract_stats.py` | `45481f7` |
| D15 The API and web monitors share one path that commits its freshness sync. Repeated calls make fewer Cloud calls, and the web monitor now refreshes stale sensors (fixes B-N1). | `IrrigationService.monitor_cluster` | `test_contract_wp5_gaps.py`, OpenAPI/MCP goldens | `046a39a` |
| D15 follow-up: the web monitor 404s an unknown cluster. | web monitor route | golden `monitor__404` | `77a5bb6` |
| D16 One lenient device-config parser. Malformed, non-object or `"null"` stored config reads as `{}` instead of HTTP 500. | `greenhouse_core.models.parse_device_config` | `tests/test_contract_repository_gaps.py` | `4c914e8` |
| D17 Efficacy `days` is bounded to 1..365 on the API too (422 above). | `services/efficacy.EFFICACY_{DEFAULT,MAX}_DAYS` | `tests/server/test_efficacy.py`, OpenAPI/MCP goldens | `cf8dafe` |
| D20 The web kill switch (`POST /bulk/stop-all`) sends the same ntfy emergency push as the API. | `services/bulk.stop_all_irrigators` | `tests/server/test_web_emergency_notify.py` | `cfb423f` |
| D20 follow-up: the web route rolls back the notify gate's uncommitted preferences seed. Without it, the page waited out SQLite's 5 s busy timeout. | web bulk route | same file (red without the fix) | `a0e504b` |

### Consistency (`fix(consistency)`)

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| OD4 A manual stop records action `stop`. Old databases keep their `off` rows, so history and `stats.events_by_type` show both. The TUI colours `stop`. | `services/manual_control.manual_stop` | OpenAPI/MCP goldens, `orchestration/pipeline/manual_start_stop_log.json` | `8053a89` |
| OD3 Pre-Alembic DB repair removed. A DB with tables but no `alembic_version` now fails at startup ("table … already exists"). SQLite DDL is not transactional, so partial tables and an empty `alembic_version` are left behind. Remedy: back up, add the missing columns, then `alembic stamp head`. | `greenhouse_core.database.init_db` | `tests/test_migrations.py` | `1a6e30e` |
| OD3 The startup migration of open `pump_dry_run` alerts is removed. Such alerts stay open until resolved by hand. The pump watcher's `pump_dry_run` activity code is unchanged. | `scheduler.init_health_monitor`, `services/health_monitor` | `tests/server/test_health_monitor.py`, `test_contract_scheduler.py` | `80dcb7b` |
| OD3 `IRRIGATION_CHECK_INTERVAL_HOURS` is no longer read. Use `IRRIGATION_CHECK_CRON_HOURS=*/N`; old `.env` files silently fall back to hourly. | `config.Settings`, `scheduler._resolve_check_cron_hours` | `test_contract_settings.py`, `test_scheduler_settings.py`, registry golden | `787c471` |
| OD3 Legacy device-type aliases removed: the registry matches the exact `vendor.model` key; `tuya_cloud` / `tuya_local` / `""` (irrigators) and `soil_moisture` / `temp_humidity` / `light` / `""` (sensors) no longer resolve. Web add/edit forms offer `rainpoint.ik10pw` / `tuya.tr301z`; CLI `--type` help names the model key. A row with an unknown type is refused, never actuated (`registry.get_irrigator` logs an ERROR and raises `UnknownDeviceModel`: manual start → 503, automatic runs → `no adapter for irrigator: …`, stop-all → listed in `errors`); such a sensor syncs but gets no health monitoring. Since the fold-in an unknown type is also refused on write (422, `fdef0bc`). | `devices/registry.py`, web irrigator/sensor forms, CLI help | `tests/server/test_legacy_device_types.py`, registry/adapter contract tests, CLI help + web goldens | `2eaca6b`, `3d7544a`, `dc64396`, `2258a20` |
| OD3 follow-up (owner decision 2026-10-03): data-only Alembic revision `a1d3f5b7c902` rewrites leftover legacy type values (and `""`) to the model keys on upgrade (idempotent, no-op downgrade), so a migrated database has none. | `migrations/versions/a1d3f5b7c902_rewrite_legacy_device_types.py` | `tests/server/test_legacy_device_types.py::test_upgrade_rewrites_leftover_legacy_types_to_model_keys`, Alembic head pin in `tests/test_contract_schema.py` | `8355fad` |
| D18 Sensor-age cells (cluster detail, live status, sensors table, health page) show the real age. They showed "stale" for every age because an age was piped into a timestamp filter. Filter contract: `age_seconds` takes seconds, `time_ago` takes a timestamp. | `web/filters`, templates | `tests/server/test_web_filters.py` | `ef2e49f` |
| D19 `GET /clusters/{id}/stats` on a cluster without an irrigator returns zero totals instead of 500 (fixes B-N2). | `routes/operations`, `stats.get_irrigation_stats` | `tests/server/test_contract_cons_w2_gaps.py`, OpenAPI/MCP goldens | `37bc66f` |
| Silent `except` blocks now log at DEBUG with a traceback: weather, `collect_learning_alerts`, and the pump watcher's rollback after a failed commit. Adds two loggers: `greenhouse_server.services.{weather,maintenance}`. | those services | no test change (DEBUG records only) | `3444d9a` |
| The TUI irrigator form hint shows the keys the server reads: `{"device_ip": …, "local_key": …}` (half of B-19). | `tui/resources.py` | golden `tui/surface.json` | `4615c84` |
| An authenticated `/api/v1` request or web page opens one DB session instead of two. Auth reuses the route's `get_session` / `get_settings` providers. | `greenhouse_server.auth`, `state.py` | `tests/server/test_auth_session.py` | `d44ab8f` |
| `SpriteView` keeps its animation flag in `_animated`, not in Textual's `Widget._animate` slot, so `Widget.animate()` on a sprite works instead of raising `TypeError` (B-U1). No caller today. | `tui/sprites.py` | `tests/cli/test_tui.py::TestSpriteView` | `589b176` |

`ea6116e fix(scheduler)` is a correction with **no net behavior change**. Commit `faab47d` had moved `_run_leak_check`
onto `job_session`, which changed one rollback-failure path. `ea6116e` restores the original scaffolding, and the
reviewer's harness shows 0 differences against `971aa93`.

Behavior-preserving but worth knowing: `cc0d37e` (refactor) makes the CLI close its `httpx.Client` after each call.
The TUI still keeps one client per session.

D11 (web cluster quiet flag through `logic.timing.active_quiet_window`, `60829bf`) and D16b (gateway config through
`models.parse_device_config`, `5addfe0`) landed as behavior-preserving refactors: the second reviewer found zero
differences (725,200 quiet-hour cases; 38 config input classes).

The test suite is hermetic since `13fcac7`: an autouse fixture in `tests/conftest.py` blocks `urllib.request.urlopen`
and non-loopback sockets. Before it, 21 tests reached the real Open-Meteo API through the app's weather client
(already at baseline `a1b2622`), so results could depend on live weather.

### Bug-fix fold-in (`fix(<area>)`, 2026-10-11)

Fix packages A (safety + actuation pipeline), B (devices, ingress, sync, weather), C (API, web, auth/security, schema),
D (engine, CLI, TUI, test infrastructure), E (dependencies, CI) and F (cleanup) are all merged.
Every high-risk package had a second, differential review; review follow-ups are listed with the bug they complete.
Test files are under `tests/` (`server/` = `tests/server/`).

#### Safety and actuation

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| **S1 / B-1 `dry_run_global` is enforced.** An IRRIGATE decision becomes a logged skip with the new `TriggerCode.DRY_RUN_GLOBAL` ("global dry run: would have irrigated N min …"); `/irrigate` with `force` included. A manual start (API, web, TUI, CLI) answers 409 `global dry run is on: irrigator not started` and writes a `dry_run_global` activity row. Stops, stop-all and shutdown stops are unaffected. | `services/actuation_gates._dry_run_block`, `services/manual_control._refuse_if_dry_run` | `server/test_contract_pipeline.py::test_dry_run_global_never_actuates_but_stops_still_work`, constants + OpenAPI/MCP goldens | `e04ce9e` |
| **B-4 Per-day caps apply to automatic runs, with the global fallback.** `max_events_per_day` / `daily_cap_minutes` resolve cluster → global (`services/caps.py`); the manual start, a hand log (409) and the automatic pipeline (`/irrigate` incl. `force`, `/check`, the scheduler: skip with `daily_cap_hit`) share one verdict. Gate order before the pump: device health → caps → global dry run → start. | `services/caps.daily_cap_violation`, `services/actuation_gates.first_gate_block` | `server/test_contract_pipeline.py::test_caps_checked_by_automatic_pipeline`, `::test_global_caps_enforced_by_manual_and_automatic_starts`, golden `caps_reached_automatic` | `5a31855`, `f86ae95` (gates moved to `actuation_gates.py`, refactor) |
| **B-6 A device-health block is written back to `decision_logs`.** The gate's reason leads the trail, so the row's `action` is `skip` and `primary_code` the gate code (`device_no_water`, …); the same rewrite applies to the caps and dry-run gates. | `repository.rewrite_decision_log`, `IrrigationService._skip_run` | `server/test_contract_pipeline.py::test_device_health_block_is_written_to_decision_log`, golden `device_health_block` | `89ea6de` |
| **S2 / B-2 The keep-alive fallback can no longer leave the pump ON**, then is removed. First the SIGTERM handler was installed only on the main thread and one `try/finally` after the first ON sent one OFF; then (second review) the loop was dropped: a bounded IK10PW start whose local Duration DP write fails is refused **before any ON** (pipeline: `attempted` event + `actuation_failed` alert; manual start: 502). `KEEP_ALIVE_INTERVAL` and the profile's `needs_keepalive_fallback` capability are gone. | `devices/irrigators/ik10pw.py::IK10PWAdapter.start` | `devices/test_contract_adapters.py`, `devices/test_ik10pw_adapter.py`, `server/test_ik10pw_refusal.py` (`test_bounded_start_without_local_duration_is_refused_before_any_on`, `test_keepalive_loop_is_gone`) | `c56bd8f`, `cc58fa1`, `5cafc94` |
| **S3 An automatic start is committed the moment the device accepts it** (`start` event, `irrigated` activity, decision `actuated`), before the leak check, the watcher, the push and the rest of a `check_all` cluster. A later failure rolls back only what followed. | `IrrigationService._record_started` | golden `check_all_crash_after_writes.json` (`server/test_contract_check_all.py`) | `a980635` |
| **Compensating stop.** If the start cannot be recorded after the device accepted it, the pump is stopped again and the error surfaces: `/irrigate` 500, `check_all` (scheduler, `check --all`) reports the cluster as `error` and raises `check_failed`, a manual start answers 500 `the start could not be recorded, so the irrigator was stopped again`. | `services/irrigation.stop_unrecorded_start` | `server/test_contract_pipeline.py`, OpenAPI/MCP goldens | `a3128d7` |
| **A forced run is manual everywhere.** `force=true` records its decision log, event and push as `manual`; it keeps the leak check (with a `leak_check_armed` activity marker) and the dry-run watcher, and the watcher stops it at shutdown like an automatic run. The irrigate tool description says so. | `IrrigationService.run_irrigation_pipeline`, `services/pump_watcher_jobs.handle_watcher_interrupted` | `server/test_contract_pipeline.py::test_forced_run_is_recorded_manual_and_keeps_leak_check_and_watcher`, `server/test_contract_pump_watcher.py`, golden `pipeline/quiet_hours_force.json` | `6088984`, `a982946`, `60e5926` |
| A manual start of 0 or negative minutes is refused (API 422 by schema, web 422). | `schemas`, `manual_control.manual_start` | `server/test_web_irrigator_actions.py`, OpenAPI/MCP goldens | `ccf2c4b` |
| **Stop-all reports a device that refuses the stop.** `adapter.stop()` → `(False, msg)` is an error: not counted, no `stop/emergency` event; the web says "N did not confirm the stop and may still be running" instead of "Every device is now off." | `services/bulk.stop_all_irrigators` | `server/test_bulk_stop.py`, `server/test_contract_web_mutations.py::test_bulk_stop_all_reports_a_device_that_refuses_the_stop` | `d2399c7` |
| An exhausted vacation budget is the skip's `primary_code` (`vacation_budget_exhausted` leads the trail; it stayed `sensor_dry`). | `logic/engine._apply_vacation_ration` | `tests/test_vacation_rationing.py`, golden `engine/vacation.jsonl`, `pipeline/vacation_budget_exhausted.json` | `f4188bc` |

#### Leak check and pump watcher

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| **Every manual start gets the post-watering leak check** (marker `leak_check_armed`, committed with the event, so a restart re-arms it); a hand log gets none. | `manual_control._record_leak_check_plan`, `irrigation_jobs.record_leak_check_armed` | `server/test_contract_pipeline.py::test_manual_start_schedules_watcher_and_leak_check`, golden `web/mutations/start_irrigator.json` | `3d7f906` |
| A manual start's check waits for the run to end and settle: due `start + max(LEAK_CHECK_DELAY_SECONDS, minutes × 60 + LEAK_CHECK_SETTLE_SECONDS)` (30 min floor, 25 min settle). A manual start without minutes gets no check and a `leak_check_skipped` activity row says why. | `irrigation_jobs.leak_check_after_seconds` | `server/test_leak_detection.py`, `server/test_leak_rearm.py` | `31cb427` |
| **B-23** The "check already done" scan is bounded by time (markers since the start), not by the newest 500 activity rows; `LEAK_CHECK_ACTIVITY_SCAN_LIMIT` is gone. | `irrigation_jobs._leak_check_done` | `server/test_contract_wp7_gaps.py::test_leak_check_done_sees_a_marker_behind_many_newer_leak_rows` | `e145090` |
| **S5** A failed write during a pump-watcher trip: the pump is stopped first, each record commits on its own, a failure is rolled back alone and logged, and a lost alert is re-queued. The watcher never raises `PendingRollbackError` out of a trip. | `services/pump_watcher.py` | `server/test_contract_pump_watcher.py::test_trip_after_a_failed_flush_rolls_back_and_still_records_the_rest` | `fe57ad8` |
| A negative watch duration completes at once with zero elapsed time (was negative). | `services/pump_watcher.py` | `server/test_contract_services_gaps.py::test_watch_negative_duration_completes_at_once_with_zero_elapsed` | `b14be8d` |

#### Device health and alerts

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| **B-3 Sensors no longer flap offline after every sync.** A sensor is judged on its latest persisted reading, so its offline window is the sync interval + `SENSOR_OFFLINE_GRACE_MINUTES` (30): 210 min by default (`DEFAULT_SYNC_INTERVAL_MINUTES` = 180 for monitors built without settings). Irrigators keep `OFFLINE_AFTER_MINUTES` (30, live local read). | `services/health_monitor.py`, `scheduler.init_health_monitor`, `constants.py` | `server/test_contract_health_monitor.py::test_sensor_stays_online_between_syncs_and_goes_offline_after_a_missed_sync`, `server/test_health_monitor.py` | `c13b1a0` |
| **S6 Each job writes through its own view of the shared health monitor** (`for_repo(repo)`); the alert cache is shared under an `RLock`. A failed alert write stays cached as owed and is retried on the next observation, so actuation stays blocked and the alert reaches the inbox. Alarm vocabulary moved to `services/health_alarms.py`. | `services/health_monitor.py`, `services/health_alarms.py` | `server/test_health_monitor.py::test_pump_watcher_trip_writes_its_alert_through_its_own_view`, `::test_failed_alert_write_still_blocks_and_retries_on_next_observation` | `4a33bb0` |
| Review follow-ups to S5/S6: the health job commits per device and marks an alert lost to a rollback as unraised (re-raised next poll); `record()` updates the cache before any DB write, so a poisoned session cannot keep an alarm out of the cache. | `DeviceHealthMonitor.poll_all`, `.record`, `health_alarms.derive_alarms` | `server/test_contract_health_monitor.py`, `server/test_health_monitor.py` | `df518dd`, `00eccfc` |
| **B-9** At startup the monitor cache is rebuilt from open health alerts (`restore_from_open_alerts`): an open NO_WATER / OFFLINE alert blocks actuation at once and resolves when the next observation clears it. | `DeviceHealthMonitor.restore_from_open_alerts`, `scheduler.init_health_monitor` | `server/test_health_monitor.py::test_init_health_monitor_restores_blocks_from_open_alerts` | `28dfcaf` |
| **B-5** A re-opened (re-raised) alert notifies again. | `services/alerts.py` | `server/test_contract_pipeline.py::test_reraised_alert_is_renotified`, `server/test_notify.py` | `a2fb694` |
| **B-10 `water_warning` means "soil dry" everywhere.** The TR301Z no longer raises `SENSOR_FAULT` ("check probe placement") for DP 111; the engine's WATER_WARNING stays. Open `sensor_fault` alerts from before the fix are resolved at startup. `HealthAlarm.SENSOR_FAULT` stays in the enum for that cleanup. | `devices/sensors/tr301z.py`, `services/health_monitor.py` | `devices/test_contract_adapters.py`, `server/test_contract_health_monitor.py::test_backfill_never_raises_sensor_fault_from_water_warning`, `server/test_contract_services_gaps.py` | `6920994`, `8a57601` |

#### Devices, ingress, sync, weather

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| Silent swallows logged: IK10PW pulse / SIGTERM OFF (WARNING), final OFF (exception), `tuya_generic` local read (DEBUG, new logger), core sync live read (WARNING). | `devices/irrigators/{ik10pw,tuya_generic}.py`, `sync.py` | `devices/test_contract_adapters.py`, `tests/test_contract_sync.py` | `8b02de9` |
| The SIGTERM path of the keep-alive cycle sent OFF twice; it sent OFF once until the whole keep-alive path was removed (`cc58fa1`). | `ik10pw.py` | `devices/test_contract_adapters.py::test_sigterm_during_cycle_switches_off_once_and_reports_interrupted` | `951e657` |
| A DP the parser rejects is logged and skipped, not fatal to the whole reading (v1 and v2 reads). | `devices/gateway.py` | `devices/test_contract_adapters.py::test_v2_parser_error_skips_only_that_dp` | `b61fd0e` |
| **New bug:** the DP 111 parser read `"false"` (as `getdevicelog` reports it) as True; it is now strict (bool, 0/1, `true`/`false`). | `devices/datapoints._parse_bool_dp` | `devices/test_contract_adapters.py` | `1634816` |
| Rows backfilled from `getdevicelog` store `water_warning`. | `devices/gateway.py`, `sync.py` | `tests/test_contract_sync.py::test_log_water_warning_is_stored_only_as_a_parsed_bool` | `73ee467` |
| **B-21** A humidity-only (`env_humidity`) live reading is persisted. | `sync._store_live_reading` | `tests/test_contract_sync.py::test_env_humidity_only_live_reading_is_persisted` | `08a66d7` |
| **B-20** `getdevicelog` backfill pages through the whole window (`SENSOR_LOG_PAGE_SIZE` = 100, `SENSOR_LOG_MAX_PAGES` = 50, WARNING at the cap); paging stops on a repeated cursor or a `success: false` page (WARNING) and pins the window end. | `DeviceGateway.get_device_logs` | `devices/test_contract_adapters.py::test_pages_through_the_window_with_the_row_key_cursor`, `::test_page_cap_bounds_the_requests_and_warns` | `b79190e`, `aff73a1` |
| A non-positive forecast window returns no forecast and makes no request (it sliced from the end). | `services/weather.py` | `server/test_contract_weather.py::test_non_positive_hours_returns_none_without_a_request` | `6d20838` |
| The forecast cache is keyed by the requested hours. | `services/weather.py` | `server/test_contract_weather.py::test_forecast_cache_is_keyed_by_hours` | `3770bb8` |
| `/monitor` cleans over `SNAPSHOT_LOOKBACK_HOURS` and then takes the 2 h slice, so the spike filter applies (one glitch no longer reads `very_dry`). | `services/irrigation._monitor_readings` | `server/test_contract_raw_vs_clean.py::test_monitor_cleans_over_a_window_long_enough_for_the_spike_filter`, goldens `orchestration/raw_vs_clean.json`, `tui/screens/cluster_1_insights.txt` | `0bb8944` |
| **A stale `local_key` after a re-pair is recovered.** A local call answered with tinytuya error 914 ("Check device key or version") makes ONE Cloud `getdevices` lookup; only a different Cloud key retries the local call, once (IK10PW Duration DP 102 write, DP 105 health read, generic status read — all through `DeviceGateway.run_local`; the Cloud switch ON is still sent once, after a successful Duration write). At most one lookup per device per `LOCAL_KEY_REFRESH_INTERVAL_SECONDS` (1 h; a cold lookup counts), so steady state stays Cloud-free. The new key supersedes the stale config value in-process (an operator-entered new key still wins) and is persisted to `config['local_key']` by the now-wired `on_key_discovered` hook (`services/device_keys`, own session on a background thread; cold lookups are persisted too). Responses keep the mask. WARNING on the refresh, INFO on a new key / its persistence, never the key. | `devices/gateway.run_local`, `devices/local_keys.py`, IK10PW / generic adapters, `services/device_keys.py`, `app._init_tuya`, `constants.py` | `devices/test_gateway_key_refresh.py`, `server/test_local_key_persistence.py` | `380dbcd` |

#### API

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| **B-11 `local_key` is masked** as `••••••••` in irrigator and sensor responses (API/MCP); sending the mask back keeps the stored key (API, TUI JSON form, web edit form); a new key replaces it. | `schemas.LOCAL_KEY_MASK`, `services/inventory.keep_masked_local_key` | `server/test_irrigators.py::test_every_irrigator_response_masks_the_local_key`, `server/test_sensors.py`, `server/test_web_irrigators.py`, OpenAPI/MCP goldens | `578929e`, `a859ecd`, `ac8bcc1` |
| **B-15 PATCH tells an absent field from an explicit null** (`exclude_unset`): null clears a nullable field (cluster location, plant notes, vacation notes/email, prefs `default_cluster_id`, sensor `plant_id`), is ignored for a required one, and `config: null` clears the whole config, `local_key` included; blanking a web edit field clears it. | `repository._patch_fields`, routes | `server/test_patch_nulls.py`, web mutation goldens, OpenAPI/MCP goldens | `7836e76`, `78abbc5` (docs) |
| A `default_cluster_id` that names no cluster is refused (404). | `routes/preferences.py` | `server/test_patch_nulls.py` | `5eb5a90` |
| **B-22** Cluster status reports the real age of a reading older than 24 h (was `None`). | `services/cluster._sensor_status_rows` | `server/test_clusters.py::test_status_reports_the_real_age_of_a_reading_older_than_a_day` | `4c4e833` |
| Deleting a cluster removes the rows that point at it (windows, decision logs, alerts, sensor assignments) and clears a `default_cluster_id` that named it; activity rows are kept. Deleting a plant or cluster removes the plants' `plant_health_daily` snapshots. | `repository.delete_cluster`, `.delete_plant` | `server/test_clusters.py::test_delete_removes_rows_that_point_at_the_cluster`, `tests/test_contract_repository_gaps.py` | `4bc7823`, `c8e91f7` |
| **Unknown device types are refused on write**: API 422 listing the registered keys (`Unknown irrigator type 'x' (known: rainpoint.ik10pw)`), web 422 re-render; forms offer the registry's types, keep a stored unknown type selectable and flag it. A type is validated only when it changes, so a legacy row's other fields stay editable. | `services/inventory.require_known_device_type` | `server/test_device_type_validation.py`, web form goldens, TUI actuation golden, OpenAPI/MCP goldens | `fdef0bc`, `a27d209` |
| NaN / Infinity are refused in measurement and threshold inputs (API 422, web 400); a 422 body renders a non-finite input as text. | request schemas, `web/forms.parsed_or_400` | `server/test_non_finite_numbers.py` | `51994c7` |

#### Web

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| Ack / resolve of a missing alert is a 404 like the API (was a 200 success toast). | web alert routes | `server/test_contract_web_mutations.py::test_alert_action_on_missing_alert_is_404` | `3662b49` |
| A non-numeric form value is a 400 page naming the field (config, global config, plants, `temp_override`, plants/sync); a non-integer default cluster in preferences is a 400 with no partial save. | `web/forms.py`, web routes | `server/test_contract_web_mutations.py::test_non_numeric_form_value_is_a_400_error_page` | `f0570bb` |
| Irrigating an unknown cluster is a 404 (was a template 500). | web irrigate route | `::test_irrigate_missing_cluster_is_404` | `293ceaf` |
| Error pages follow `Accept`, router-level 404/405 included: `/api`, `/mcp`, `/.well-known` → JSON; an HX request → partial; `text/html` → page; JSON clients → JSON; `*/*` → HTML. The HTML 405 page keeps `Allow`. | `web/exception_handlers.py` | `server/test_contract_web_errors.py::test_error_format_follows_accept_on_web_paths`, error goldens | `fdd0c3a`, `40a1df9` |
| The footer clock (`now_text`) uses the timezone preference (UTC for an unknown zone), not the process zone. | `web/context.py` | `server/test_startup_logging.py::test_now_text_uses_the_timezone_preference_not_the_process_zone` | `ba44321` |
| Anonymous pages (login, router-level errors) no longer show the dry-run / vacation / paused banners. | `web/context.py`, `_signed_in_user` dependency | `server/test_auth_session.py`, routes goldens | `9140079` |
| The plant add/edit form is read through one FastAPI dependency. Status codes are unchanged; a doubly invalid JSON request now lists the body error before the path error in the 422 `detail`. | `web/routes/plants.py` | `server/test_web_plant_pages.py` | `aac74ba` |

#### Auth, security, settings

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| **B-12** The MCP token is compared in constant time (`auth.is_mcp_token`, `hmac.compare_digest` over UTF-8 bytes), for the `/mcp` gate and the MCP principal alike. | `auth.is_mcp_token`, `app.require_mcp_token` | `server/test_contract_auth.py::test_mcp_gate_compares_the_token_in_constant_time` | `fb8617f` |
| An empty or whitespace-only `GREENHOUSE_MCP_TOKEN` is unset (503, was 401); the MCP token is accepted as a bearer header only (in the cookie it is refused, 401 "Invalid session"). | `config._blank_mcp_token_is_unset`, `auth.require_user` | `server/test_contract_auth.py`, `server/test_contract_settings.py` | `6910279`, `3216b6d` |
| `WWW-Authenticate: Bearer` is kept on JSON 401s (`/api/v1`, `/mcp`). | `web/exception_handlers.py` | `server/test_contract_auth.py::test_api_401_carries_www_authenticate_header`, `server/test_contract_mcp.py`, `server/test_contract_web_errors.py`, golden `anonymous_api__json_401.html` | `440bfeb` |
| `verify_password` returns False on a truncated or non-ASCII corrupt hash (login 401, not 500); an unknown or inactive username runs a dummy argon2 verify (no timing oracle). | `greenhouse_core.auth` | `server/test_contract_auth.py::test_verify_password_returns_false_on_truncated_hash` | `6d1a1a6`, `67e3520`, `2792a7a` |
| **Aliased secrets are read from `GREENHOUSE_X` only** (MCP token, ntfy server/topic/token, auth secret/admin username/password); bare `X` and `IRRIGATION_X` are ignored, and a startup WARNING names each ignored legacy variable (name only). | `config.Settings`, `config.ignored_legacy_secret_env_names` | `server/test_contract_settings.py::test_aliased_fields_read_only_the_greenhouse_env_name`, settings golden, `server/test_startup_logging.py` | `e269c39`, `f3df4e8` |
| The ntfy server URL must be `http(s)://` with a host; anything else fails at config load naming `GREENHOUSE_NTFY_SERVER_URL`, with credentials redacted from the error. | `config.Settings` | `server/test_contract_settings.py::test_ntfy_server_url_accepts_http_https_and_unset` | `db2b659`, `66dca03` |
| **The default bind host is `127.0.0.1`.** The Dockerfile sets `IRRIGATION_HOST=0.0.0.0` and `docker-compose.yml` sets it explicitly. | `config.Settings.host`, `Dockerfile`, `docker-compose.yml` | `server/test_contract_settings.py`, settings golden | `b388380` |
| Swallowed failures logged: persisted-pause restore, startup timezone, Tuya init (WARNING), chrome preference read (DEBUG); new loggers `greenhouse_server.app`, `greenhouse_server.web.context`. Missing Tuya credentials log one WARNING line without a traceback. | `app.py`, `web/context.py` | `server/test_startup_logging.py` | `87fcc6d`, `5d86105` |

#### Schema and database

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| The models declare the migrations' server defaults and named uniques, so `Base.metadata.create_all` and the migrated schema agree; `schema_drift.json` is deleted and `PHASE0_DDL_SHA256` re-recorded. | `greenhouse_core/models.py` | `tests/test_contract_schema.py::test_migrations_and_create_all_produce_the_same_schema`, DDL / ORM goldens | `6758219` |
| In-process migrations no longer run Alembic's `fileConfig`, so `create_app` leaves the host's logging configuration (and `caplog`) alone; the `alembic` command line still configures logging. | `greenhouse_core/database.py` | `tests/test_migrations.py::test_init_db_leaves_the_host_logging_configuration_alone` | `0159ef9` |

#### Engine, CLI, TUI, test infrastructure (package D)

Engine rules (all in `greenhouse_core/logic/`; engine goldens under `tests/golden/engine/`):

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| **Critical stress keys on the driest plant** (invariant #2), no longer on the cluster average. | `stress._water_stress` | `test_invariants_engine.py::test_inv2_stress_rule_keys_on_the_driest_plant`, `engine/driest.jsonl` | `7bffac8` |
| **Every decision carries a reason.** A run no rule touched (e.g. temperature-only readings in range) gets the new `TriggerCode.NO_ACTION_NEEDED`, so `primary_code` is never NULL; the web shows it with its own icon. | `engine._ensure_reason`, `decision.TriggerCode`, `web/filters.py` | `test_invariants_engine.py::test_inv5_inline_literals_and_explicit_no_action_reason`, `server/test_web_filters.py`, constants golden | `f20ebff`, `69a3ba1` |
| **Short-series spike guard.** A series of 3–4 points (`CLEANING_SHORT_SERIES_MIN_READINGS` = 3) gets a neighbour test: an interior point far from both neighbours, on the same side of both, is dropped. End points are not judged (a newest-end high glitch is kept on purpose, documented in `f371269`). | `cleaning._short_series_outlier_mask` | `test_cleaning.py::test_short_series_rejects_a_lone_spike`, `test_invariants_engine.py::test_inv10_short_series_spike_is_filtered` | `2632a54` |
| **Hampel keeps a confirmed step** at the newest end (40, 40, 40, 60, 60): a point whose right context is truncated survives when every later sample backs it as the new level; a glitch beside a real level change does not count as confirmed. | `cleaning._is_hampel_outlier`, `_confirms_level` | `test_contract_mutation_gaps.py::test_hampel_keeps_a_step_change_confirmed_by_the_following_samples`, `test_properties_logic.py::test_step_confirmation_only_spares_points_their_followers_back` | `f294cb5`, `3cc9d9e` |
| **`parse_moisture_target` validates.** An inverted band is swapped (`"65-45"` → 45–65); a bound outside 0–100 %, NaN or infinite → default band 45–65. | `plant_needs.parse_moisture_target` | `test_properties_logic.py::test_parse_moisture_target_normalises_inverted_and_rejects_out_of_range` | `4a195d3` |
| **B-18** 0 °C / 0 % are real values in the plant temperature / humidity ranges and the temperature trend. | `plant_needs._widest_range`, `trends._temperature_trend` | `test_properties_logic.py::test_plant_needs_zero_is_a_real_bound`, `test_contract_mutation_gaps.py::test_zero_celsius_readings_count_in_the_temperature_trend` | `dd1395f` |
| **B-14** `interval_delta` / `duration_delta` report the step actually applied after clamping (0 at a bound). | `engine._shift_interval`, `_lengthen_duration` | `test_logic.py::test_clamped_adjustments_report_the_applied_delta_not_the_nominal_step`, soil/season goldens | `081e94e` |
| **B-24** With no sensor data, a global row that sets `mode` / `duration_minutes` / `interval_hours` drives the config fallback even without a cluster row. | `fallback._has_configured_schedule` | `test_logic.py::test_fallback_uses_a_global_schedule_without_a_cluster_config_row` | `9885cb9` |
| Light thresholds (stress `low_light` and the light adjustment) take the season from the month in the preferences timezone, like the seasonal multiplier. | `engine._gather_inputs`, `stress._low_light` | `test_logic.py::test_light_thresholds_use_the_preferences_timezone_month` | `7175723` |
| The light season outside the engine follows too: the learning issue heuristics and drainage profiling, and the maintenance `low_light` check, take the month in the preferences timezone (UTC when unset). | `learning/clock.light_month`, `learning/issues.py`, `learning/profiling.py`, `services/maintenance.py` | `test_learning.py::…::test_light_month_follows_the_preferences_timezone`, `::test_drainage_and_low_light_use_the_local_month`, `server/test_maintenance_light_season.py` | `53645c4`, `1b6efc3` |
| **One outdoor rule** (`timing.environment_for`: only `"outdoor"` is outdoor) for the rain skip, the seasonal table, the forecast's rain skip and the pipeline's temperature source. | `logic/timing.py`, `services/forecast.py`, `services/irrigation.py` | `test_logic.py::test_outdoorness_is_one_rule_for_rain_and_seasons`, `server/test_forecast.py`, `server/test_contract_mutation_gaps.py` | `d08edde`, `94e34ed` |
| **Emergency runs obey irrigation windows and vacation budgets** (water warning, critical stress, the no-sensor fallback): an emergency changes the dosage, not the operator's limits. | `engine._evaluate_rules` | `test_logic.py::test_emergency_triggers_obey_irrigation_windows`, `::test_emergency_triggers_obey_the_vacation_budget`, window / vacation goldens | `0c43ca8` |
| **Exclusive edges:** a start exactly 6 h ago no longer blocks, a leak alert last seen exactly 24 h ago no longer holds, a vacation whose end equals now is over (API / web banner too). | `engine._enforce_cooldown`, `_enforce_leak_hold`, `repository.get_active_vacation` | `test_invariants_engine.py::test_inv3_cooldown_boundary_is_six_hours_exclusive`, `::test_inv11_hold_window_edge_is_exclusive`, cooldown / leak / vacation goldens | `e081eb0` |
| **Current readings.** The soil rule and critical stress judge each sensor's newest cleaned soil reading (`PerSensorSnapshot.latest_soil_moisture`, not persisted), not the 24 h pool; a reading older than `SENSOR_READING_STALE_SECONDS` (4 h) is not current, and when every soil reading is stale the no-data fallback decides. | `logic/sensors.py`, `engine._base_or_emergency` | `test_logic.py::test_stale_readings_neither_veto_nor_trigger`, `orchestration/raw_vs_clean.json`, series goldens | `3801e38`, `7f45387`, `b0157b7` |
| **Per-plant bands.** Each sensor is judged against the band of the plant it is linked to (unlinked or unknown plant → the cluster's widest band): dry / very dry, the conflict's wet side, and critical stress, whose threshold is the plant minimum − `CRITICAL_STRESS_BELOW_TARGET_MARGIN` (15), capped at 30 % (the steep-decline threshold moves with it, +10). Adequate vs wet still uses the current mean against the widest band. | `plant_needs.current_soil`, `stress._critical_thresholds`, `engine._apply_soil_moisture_rule` | `engine/driest.jsonl`, `engine/soil/*.jsonl`, `test_contract_wp6_gaps.py` | `3f72506`, `3db76a6`, `b97567f` |
| **Over-watering vetoes.** Water stress is dropped when over-watering is detected or any plant reads above its band. The device soil-dry alarm is a terminal `over_watering` skip (alarm recorded) only when over-watering is detected; when another sensor merely reads above its band the alarm is noted after the soil rule and critical stress / the soil rule decide. | `stress.detect_stress_conditions`, `engine._apply_emergency_rules`, `_note_deferred_alarm` | `test_logic.py::test_over_watered_soil_vetoes_the_device_soil_dry_alarm`, `::test_stuck_high_probe_does_not_veto_another_plants_alarm`, `::test_device_soil_dry_alarm_on_wet_soil` | `7fc196e`, `ae2182c`, `aba2cfa` |
| **Water warning is a state.** Only each sensor's newest reading that carries the flag counts (a row without it neither raises nor clears it), and a flag last reported more than 4 h ago is ignored. | `sensors._latest_water_warning` | `test_logic.py::test_water_warning_reads_only_the_newest_reading_per_sensor`, `::test_stale_water_warning_does_not_trigger` | `61905d9`, `e16f4a6`, `f0c7662` |

Server, CLI, TUI and test infrastructure:

| Change | Where | Pinned by | Commit |
|---|---|---|---|
| The pre-decision freshness sync dates a soil sensor by its newest soil reading, so a fresh humidity-only row cannot hide a stale soil value. | `services/sync.SyncService._freshness_reading` | `server/test_sync_snapshot.py::test_freshness_is_judged_on_the_newest_soil_reading` | `8c74cc6` |
| Scheduler job delete routes take `{job_id:path}` (API `DELETE /scheduler/jobs/{job_id}` and the web delete, whose template URL-encodes the id), so an id with `/` works. | `routes/scheduler.py`, `web/routes/analytics.py` | `server/test_scheduler_jobs.py`, routes goldens | `a8493d5`, `0c7bb22` |
| CLI: a blank `GREENHOUSE_API_TOKEN` falls back to the stored token. | `client.load_stored_token` | `cli/test_contract_json_output.py` (`token.blank_env_falls_back_to_file`) | `fd2d431` |
| CLI **B-17**: a timeout, any transport error, and an empty or non-JSON 2xx body print `Error: …` and exit 1 (no traceback). | `client.IrrigationClient._send`, `_decode_json` | `error.read_timeout_exits_1`, `error.empty_200_body_exits_1`, … | `2eabaa4` |
| CLI: a 422 detail prints as `field: message; …` (an empty one as "The server rejected the request without details"; other non-string details as JSON). | `client._format_detail` | `error.422_detail_list_field_messages`, `error.422_empty_detail_list` | `213fb69`, `7c70c58` |
| CLI: cluster id `0` is an id for `check`, `plant list --cluster`, `sensor list --cluster`. | `commands/{operations,plants,sensors}.py` | `check.cluster_zero_is_an_id`, … | `5f0c659` |
| CLI: `stats --export` with a non-CSV response is an error, not an empty file. | `client.stats_export` | `stats.export_json_response_is_an_error` | `264deca` |
| CLI: scheduler job ids are percent-encoded as one path segment. | `client.delete_scheduler_job` | `delete_scheduler_job.path_segment_escaped` | `9048809` |
| CLI: a bare command group prints its help and exits 0 (was 2). | `commands/_helpers.HelpOnBareGroup` | `cli/test_contract_help.py::test_group_without_args_prints_help_and_exits_0` | `5d4fbf2` |
| **TUI S4:** overlapping dashboard reloads no longer crash — one card rebuild at a time (lock + shield), no-op once the screen is closing (`DataScreen.showing`, public Textual API), a failed orphaned rebuild is logged. | `tui/screens/dashboard.py`, `screens/base.py` | `cli/test_tui.py::test_overlapping_dashboard_reloads_do_not_crash`, `::test_failed_shielded_card_rebuild_is_logged_with_traceback` | `dbfb304`, `8da8421` |
| TUI: a 401 says "Sign in to see your clusters — the server needs a login." instead of "Cannot reach the server". | `tui/app.py`, `screens/dashboard.py` | `cli/test_contract_tui_runtime.py::test_401_dashboard_says_sign_in_needed` | `f1478d3` |
| TUI: an Activity refresh keeps the cursor on the selected event. | `screens/activity.py` | `::test_activity_cursor_stays_on_the_selected_event_across_refresh` | `580a73a` |
| TUI (B-19, second half): the add-plant note says blank care fields stay blank — press `P` to fill them. | `screens/cluster.py` | golden `tui/actuation.json` | `85c181a` |
| TUI: search result columns are sized to the latest results. | `screens/search.py` | `cli/test_tui.py::test_search_columns_fit_the_latest_results` | `1a642f3` |
| TUI: the app closes its HTTP clients (the retired signed-out one too) on exit. | `tui/app.py` | `cli/test_tui.py::test_app_closes_its_http_clients_on_exit` | `363cce7` |
| TUI: scheduler resume and the plant-DB sync (`P`, system and cluster screens) ask for confirmation. | `screens/system.py`, `screens/cluster.py` | golden `tui/actuation.json`, `cli/test_contract_tui_actuation.py` | `61d45a2` |
| TUI: a table painted before its columns were measured no longer keeps a narrow render (the recurring golden flake). Every TUI table is `tui/table.DataTable`, which overrides Textual's two idle-time measurements to bump the render-cache key; `SHADOWING_ALLOWED` in `tests/test_refactor_guards.py` registers that exception. | `greenhouse_cli/tui/table.py` | `cli/test_tui.py::TestTableColumnWidths` | `9767895` |
| Tests: server fixtures are a global plugin (`tests/server/fixtures.py`, registered by `tests/conftest.py`), so mixing directories on one command line works. | `tests/conftest.py`, `tests/server/fixtures.py` | `test_refactor_guards.py::test_server_fixtures_resolve_when_directories_are_mixed_on_one_command_line` | `450eded` |
| Tests: no test reads the developer's CLI token (`GREENHOUSE_API_TOKEN` unset, `XDG_CONFIG_HOME` in a temp dir), and `Settings` ignores a local `.env` unless the test is marked `dotenv`. | `tests/conftest.py` | `test_refactor_guards.py::test_cli_token_resolution_is_isolated_from_the_developer`, `::test_settings_ignore_a_developer_dotenv` | `2a4d6bf`, `6b78e0b` |

#### Not behavior changes (same batch)

- `fix(deps)` `8f79282` / `build(deps)` `364e284` / `ci` `677986c`: dependency upgrades, declared dependencies and the
  CI `static` job — see `docs/wip/REFACTOR_REPORT.md` → "Bug-fix fold-in".
- `refactor` `f86ae95` (gates → `services/actuation_gates.py`), `ea0726a` (DP parsers and log folding →
  `devices/datapoints.py`, re-exported from `gateway`), `0ff00ba` (lazy package re-exports, `greenhouse_core/_lazy.py`:
  `import greenhouse_core.constants` from ~650 ms to 15–25 ms), `d3d0ec3` (pump-watcher jobs →
  `services/pump_watcher_jobs.py`; logger and job ids unchanged).
- Dead code removed: `a00ee93` (`refresh=` of `resolve_local_key` / `open_local`), `8011987` (charts `plant_db`
  parameter), `f88e0dc` (`STATUS_READINGS_LOOKBACK_HOURS`), `5cafc94` (`needs_keepalive_fallback`), `dc7d932`
  (test-only leak-check re-exports), `da9cc6a` (`fallback.temperature_based_decision`'s unused `temp_range`),
  `81ec7e3` (the duplicate `start == end` guard in `timing.is_within_quiet_hours`).
- `docs(engine)` `f371269`: why the short-series guard keeps a lone high glitch at the newest end (see "Not bugs").

## Observed bugs not fixed (open)

Every bug the refactor recorded is fixed and listed under "Labeled behavior changes landed" above (package D's
engine, CLI, TUI and test-infrastructure items included, the stale-`local_key` follow-up, `380dbcd`, and the
`tr301z.py` sensor-staleness docstring, `8da0aa7`). What remains are the notes below.

### Follow-ups (outside every fix package)

- **A manual start without `minutes`** switches the pump on with the device's own timer as the only bound: no dry-run
  watcher (it needs a deadline) and no leak check (`leak_check_skipped` says why). Documented design, listed so it is
  not mistaken for a gap.
- Package D's own follow-ups (temperature source and forecast on `environment_for`, an icon for `no_action_needed`,
  `{job_id:path}` on the scheduler job routes) were done before it merged (`94e34ed`, `69a3ba1`, `a8493d5`, `0c7bb22`).

## Not bugs (by design)

- **Thresholds are bound at import.** Patching `constants.X` does not steer the engine; patch the name where it is
  used (`logic.timing.is_within_quiet_hours`, `logic.plant_needs.parse_moisture_target`). Convention. *Pinned:*
  `tests/test_invariants_engine.py::test_inv5_thresholds_are_bound_at_import_current_behavior`.
- **Cleaning is not idempotent** (a Hampel property: removing a spike changes the neighbours' medians). *Pinned:*
  `tests/test_properties_logic.py::test_clean_readings_is_not_idempotent_current_behavior`.
- **The wall clock is read at call time** (`int(time.time())` in the engine and services; tests freeze it). The
  baseline migration seeds quiet hours 00:00–05:00 into every app, so golden tests use `FROZEN_INSTANT` outside them.
- **The server `app` fixture does not stub the weather client.** Since the hermetic guard (`13fcac7`) such tests get
  the offline 20 °C fallback instead of live weather; contract tests use `install_offline_weather`.
- **A lone high glitch at the newest end of a short series is kept** (`10, 10, 99`), so a dry plant can skip one
  cycle. Dropping a newest-end rise is not safe either: a genuine rise (rain outdoors, an unlogged manual watering)
  looks the same. The next reading settles it — a revert makes the 99 an interior spike, which the guard drops.
  Documented in `cleaning._short_series_outlier_mask` (`f371269`); covered by
  `tests/test_cleaning.py::test_short_series_keeps_steps_trends_and_unconfirmed_end_points`.
- **Over-watering is still judged on the 24 h average** (> 70 % with a high watering frequency or a rising trend),
  while the soil rule and critical stress use current readings; package D left this rule as it was.

## Dead-code and cleanup candidates (not removed yet)

- The IK10PW profile lists three capabilities nothing reads (`has_local_duration_dp`, `has_water_shortage_dp`,
  `supports_cloud_switch`); kept as pinned profile data.
- Kept on purpose: `HealthAlarm.SENSOR_FAULT` (the startup cleanup of pre-B-10 alerts reads it) and
  `DeviceRegistry.registered_{irrigator,sensor}_keys` (production use since the device-type validation, `fdef0bc`).

Removed during the refactor (one commit per group, grep evidence in each body; `git log --grep="dead code" a1b2622..HEAD`):
`any_critical`, `DEFAULT_QUIET_*`, `print_stats_report`, `stats.export_csv`/`format_duration`, `#global-config` CSS,
`set_plant_database`, `database.logger`, `AuthUserDep`, `_sensor_row.html`, `EVENT_ACTION_OFF`,
`CreateSchedulerJobRequest`, `is_within_preferred_hours`, `DEFAULT_PREFERRED_WATER_HOURS`, `DeviceGateway.invalidate_key`,
sensor-adapter `read_live`, `pump_watcher.ALERT_CODE`, three unused re-exports, two unreachable branches, and unused
test helpers. `DEVICE_BLOCKING_CODES` is now the source of `is_actuation_blocked` instead of a dead duplicate.
Removed during the fold-in: the `refresh=` parameter of `resolve_local_key` / `open_local`, the charts `plant_db`
parameter, `STATUS_READINGS_LOOKBACK_HOURS`, `LEAK_CHECK_ACTIVITY_SCAN_LIMIT`, `KEEP_ALIVE_INTERVAL`, the
`needs_keepalive_fallback` capability, the test-only leak-check re-exports of `services/irrigation.py`, the unused
`temp_range` parameter of `fallback.temperature_based_decision` and the duplicate `start_hour == end_hour` guard in
`timing.is_within_quiet_hours`.

## Golden and test policy (for future contributors)

- **Kit:** `tests/golden.py`.
  - `FROZEN_INSTANT` = 2026-04-15 10:00 UTC, outside the seeded quiet hours; also `FROZEN_TS`.
  - `OfflineWeather` + `install_offline_weather(app)`.
  - `ENV_PREFIXES` (`IRRIGATION_`, `GREENHOUSE_`, `TUYA_`), cleared by `clean_env`.
  - `to_canonical_json` (sorted keys).
  - `assert_golden(name, text)` / `assert_golden_json(name, value)` compare with `tests/golden/<name>`. A missing
    golden fails.
- **`GOLDEN_UPDATE=1 uv run pytest <test>`** rewrites goldens. Use it only in:
  - the commit that creates a golden;
  - a labeled `fix(...)` commit that intentionally changes pinned behavior;
  - a `docs(api)`/`docs(cli)` commit whose diff is description text only;
  - a dead-code removal whose golden diff is removal-only.
  Review the diff so only the intended lines move. Never regenerate a golden to make a refactor pass.
- **Comparison.** Strict equality for OpenAPI, routes, MCP tools, settings, DDL, scheduler registry, package data, web
  HTML, CLI help/output, TUI renders and the decision grid. Supersets for public import surfaces, `constants.py`
  values and logger names: additions are allowed, and every golden name must still resolve with an equal value. Enum
  members stay strict.
- **Fingerprints.** `PHASE0_OPENAPI_SHA256` / `PHASE0_MCP_TOOLS_SHA256` (`tests/server/test_contract_{openapi,mcp}.py`)
  are re-recorded only in reviewed behavior-change or doc-contract commits.
- **Frozen safety net.** Changes to `tests/**/test_contract_*.py`, `tests/golden/**`, `tests/engine_grid.py`,
  `tests/test_invariants_engine.py` or `tests/test_properties_logic.py` need a written justification and a second
  reviewer.
- **Hygiene.** `tests/golden/` is excluded from the whitespace fixers (byte-exact), and each golden stays under
  400 KB. A Textual/plotext/rich bump regenerates the TUI render goldens in its own commit. Session JWTs in
  `set-cookie` goldens are stored as `<JWT sha256=…>` so gitleaks stays clean. gitleaks' pre-commit hook scans only
  staged changes, so scan branches with `gitleaks detect --log-opts=<base>..HEAD`.
- **Mutation testing.** mutmut 3 cannot key this layout: it sees `libs.greenhouse-core.…` paths, while tests import
  `greenhouse_core.…`. The scripted runner used during the refactor (a 473+ mutant catalogue) lived under
  `refactor/gate1/mutate.py`.

## Baseline warnings (recorded, not fixed)

Gate 0 on `a1b2622`, Python 3.11.15: **1182 passed, 2 warnings, 1168 s**; total coverage 90% (line+branch).

1. `PydanticDeprecatedSince211`: `BaseModel.__get_pydantic_core_schema__`, raised inside pydantic's schema generation
   (dependency path).
2. `RuntimeWarning: coroutine 'ClusterScreen._load_plant_health' was never awaited` in TUI tests. A worker is
   cancelled at teardown (Textual cache path).

Added by the safety net, on purpose: `InsecureKeyLengthWarning` from `tests/server/test_contract_auth.py` (short test
HMAC key). Current runs report 9–10 warnings in total; see the `docs/wip/REFACTOR_REPORT.md` final gate. Since the server
fixtures became a global plugin (`450eded`), a core-only run imports `TestClient` too, so it also shows Starlette's
httpx deprecation warning once per worker.

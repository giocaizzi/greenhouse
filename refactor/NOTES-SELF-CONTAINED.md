# Refactor notes

Notes for the behavior-preserving refactor on `claude/focused-hawking-7to7o3`. Baseline: `main` @ `a1b2622`.
The safety net is frozen at the local tag `refactor-gate1`. This file is self-contained. The short ids in
parentheses (B-n, S-n, D-n, OD-n) are labels kept only because test docstrings and commit bodies use them.

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
- **OD5** The `refactor/` folder is deleted at the end; only `REFACTOR_NOTES.md` and `REFACTOR_REPORT.md` stay. The
  strict list moves to `[tool.mypy] files`, sizecheck moves to `scripts/`, and the Makefile is updated.
- **Drift confirmations** (2026-10-03):
  - D8: vacation times are parsed and displayed in the `timezone` preference on every interface.
  - D7: the plant dashboard shows "never" or the real age, never "stale".
  - D2: a cross-cluster plant on sensor create or update is a 404 everywhere.
- **Branch.** Work lands on `claude/focused-hawking-7to7o3`, not on `refactor/clean-structure`.

## Labeled behavior changes landed

These come from `git log --oneline --grep='^fix(' a1b2622..HEAD` (34 commits). Each commit body has before/after
details and the exact test and golden diffs.

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

**TODO (post-WP8, in flight):** OD3 `tuya_cloud`/`tuya_local` device-type alias removal, done together with the web
irrigator form options and CLI `--type` help. D11: one quiet-hours "active now" helper in `logic.timing` for the web
`cluster_detail`; expected no visible change. D16b: `devices/gateway._coerce_config` → `parse_device_config`;
expected no visible change. Add the commits here when they land.

## Observed bugs not fixed (open)

Each entry is pinned as current behavior unless it says otherwise. A fix is a separate labeled PR that flips the
pinning test.

### Safety

- **dry_run_global is never enforced (S1, B-1).** The "Global dry-run (never actuate)" preference is stored, editable
  (API, web, CLI, TUI) and shown as a banner. No actuation path reads it: not the pipeline, not manual start, not the
  scheduler. *Where:* `models.py` (preference), `web/context.py` (display only). *Pinned:*
  `tests/server/test_contract_pipeline.py::test_dry_run_global_current_behavior_still_actuates` and golden
  `dry_run_global_preference`.
- **The IK10PW keep-alive fallback can leave the pump ON (S2, B-2).** `self.on()` runs first. Then
  `signal.signal(SIGTERM, …)` raises `ValueError` off the main thread (scheduler worker, FastAPI threadpool), before
  the `try/finally` that sends `off()`. The error propagates with the pump ON, bounded only by the firmware auto-off.
  *Where:* `devices/irrigators/ik10pw.py::IK10PWAdapter._start_keepalive`. *Pinned:*
  `tests/devices/test_contract_adapters.py::test_keepalive_off_main_thread_current_behavior_leaves_pump_on`,
  `::test_start_off_main_thread_current_behavior_raises_after_switching_on`.
- **Cooldown blind spot after a crash (S3).** In `check_all_clusters`, if a cluster crashes after `adapter.start`, its
  `start` event is rolled back. The pump ran, but the 6 h cooldown cannot see it. *Where:*
  `services/irrigation.py::check_all_clusters`. *Pinned:* orchestration golden `check_all_crash_after_writes.json`.
- **Overlapping dashboard reloads crash the TUI (S4).** When a load outlasts `refresh_seconds`, two loads interleave
  in `DashboardScreen._render_cards` and the app dies with `WorkerFailed: NoMatches('#cluster-card-2')`. The same race
  hit once at teardown (`test_auto_refresh_polls_again` → `NoMatches('#cluster-grid')`, 0/33 on reruns). Suggested
  fix: reload no-ops once the screen is unmounting. *Where:* `tui/screens/dashboard.py`. *Pinned:* not pinned (race).
- **Pump watcher error handling during a trip (S5).** If a DB write fails during a trip, the watcher's own except
  handlers raise `PendingRollbackError`, so commit and rollback never run (the pump is already stopped). If the health
  monitor swallows a failed flush ("database is locked"), `_handle_trip` still raises. *Where:*
  `services/pump_watcher.py`. *Pinned:* not pinned (found by WP4 reviewers).
- **Shared `DeviceHealthMonitor` is rebound during a pump watch (S6).** Every job re-points the one
  `app.state.health_monitor` via `bind_repo`, and its alert cache is an unlocked dict. A NO_WATER trip during a
  minutes-long watch writes its alert into another job's session. If that session rolls back, the alert is lost, but
  the cache marks NO_WATER as raised. Actuation stays blocked with no inbox alert, and the alert never re-fires. On one
  shared SQLite file the write fails with "database is locked" (swallowed), with the same result. Fix: a repo-per-call
  monitor plus a lock. *Where:* `services/health_monitor.py::bind_repo`, `scheduler.py`, `services/pump_watcher.py`.
  *Pinned:*
  `tests/server/test_health_monitor.py::test_pump_watcher_trip_current_behavior_alert_written_through_rebound_repo_and_cache_suppresses_reraise`.

### Orchestration / API

- **Sensors flap offline after every sync (B-3).** The 30-min offline threshold is applied to the latest *persisted*
  reading, but readings are persisted only by the 180-min sync. A healthy sensor goes offline about 31 min after each
  sync; the alert re-opens and notifies only the first time. *Where:* `services/health_monitor.py`,
  `constants.py`. *Pinned:*
  `tests/server/test_contract_health_monitor.py::test_sensor_offline_flap_current_behavior_flags_offline_31min_after_every_sync`.
- **Caps are not checked by the automatic pipeline, and global caps are ignored (B-4).** `check_rate_limits` reads
  only the raw cluster row. `/irrigate` and the scheduler never check caps, and `TriggerCode.DAILY_CAP_HIT` is never
  emitted. *Where:* `services/manual_control.check_rate_limits`. *Pinned:* `test_contract_pipeline.py::test_caps_current_behavior_not_checked_by_automatic_pipeline`, `::test_global_caps_current_behavior_ignored_by_manual_start`, golden `caps_reached_automatic`.
- **A re-raised alert is not re-notified (B-5).** `upsert_alert` re-opens a resolved row with `occurrence_count` 2,
  but `notify_if_new_alert` requires `== 1`. *Where:* `repository.upsert_alert`, `services/alerts.py`. *Pinned:*
  `test_contract_pipeline.py::test_reraised_alert_current_behavior_is_not_renotified`.
- **A device-health block is not written to `decision_logs` (B-6).** The response says `skip`/`device_no_water`, but
  the log row keeps `irrigate`. *Where:* the health gate in `services/irrigation.py`. *Pinned:*
  `test_contract_pipeline.py::test_device_health_block_current_behavior_not_written_to_decision_log`, golden
  `device_health_block`.
- **The health-monitor cache is memory-only (B-9).** After a restart, an open NO_WATER/OFFLINE alert does not block
  actuation until the next poll. If the condition cleared while the server was down, the alert is never auto-resolved.
  The same applies to `backfill_from_history`. *Where:* `services/health_monitor.py`. *Pinned:* not pinned.
- **`water_warning` has two meanings (B-10).** The engine reads it as "soil dry → irrigate" (WATER_WARNING, critical).
  The health monitor raises it as SENSOR_FAULT ("cross-check probe placement") with a push. *Where:*
  `logic/engine.py`, `devices/sensors/tr301z.py`, `services/health_monitor.py`. *Pinned:* not by a dedicated test.
- **The API returns `local_key` in plain text (B-11).** Irrigator `config` goes out verbatim on API/MCP; the web masks
  it. *Where:* `schemas.py` irrigator response. *Pinned:* not by a dedicated test.
- **The reason `interval_delta` claims the nominal step when the interval was clamped (B-14).** This affects the
  humidity, light and trend rules. *Where:* `logic/engine.py` interval rules. *Pinned:* not by a dedicated test.
- **PATCH cannot clear nullable fields (B-15).** Repository `update_*` skip `None` (vacation notes/email, cluster
  location, plant notes, prefs `default_cluster_id`). *Where:* `repository._patch_fields`. *Pinned:*
  `tests/test_contract_repository_gaps.py::test_update_plant_ignores_unknown_keys_and_none_current_behavior`,
  `tests/test_repository_patch_fields.py`.
- **`getdevicelog` is not paginated (B-20).** It uses `max_records=100`, so long gaps can be silently truncated.
  *Where:* `devices/gateway.py::DeviceGateway.get_device_logs`. *Pinned:* not pinned.
- **A humidity-only live reading is dropped (B-21).** The persistence guard checks `"humidity"`, never a canonical key.
  *Where:* `greenhouse_core/sync.py::_store_live_reading`. *Pinned:*
  `tests/test_contract_sync.py::test_env_humidity_only_live_reading_current_behavior_is_dropped`.
- **Cluster status hides readings older than 24 h (B-22).** `reading_age_seconds` becomes `None`, not the real age.
  *Where:* `services/cluster.py::_sensor_status_rows`. *Pinned:* not by a dedicated test.
- **The leak-check scan is limited to 500 rows (B-23).** `_leak_check_done` inspects only the newest
  `LEAK_CHECK_ACTIVITY_SCAN_LIMIT` (500) rows, so older completed checks inside 24 h could be re-armed. *Where:*
  `services/irrigation_jobs.py::_leak_check_done`. *Pinned:* not pinned.
- **The fallback ignores a global schedule without a cluster config row (B-24).** With no sensor data and no
  `irrigation_config` row, the decision is NO_DATA. *Where:* `logic/fallback.py`. *Pinned:* not by a dedicated test; an in-code "known quirk"
  comment marks it.
- **`/monitor` defeats the spike filter.** It cleans only a 2 h slice; at a 30-min cadence that is under 5 samples, so
  Hampel is skipped and one spike reports `very_dry`/`needs_water`. *Pinned:*
  `tests/server/test_contract_raw_vs_clean.py::test_monitor_current_behavior_short_window_defeats_spike_filter`.
- **`force=true` is recorded as automatic.** The decision log says `manual`, but the `IrrigationEvent` and push say
  `auto`, and a leak check is scheduled (manual starts never get one). *Pinned:* golden `pipeline/quiet_hours_force.json`.
- **An exhausted vacation budget keeps `primary_code`.** The decision becomes `skip` but `primary_code` stays
  `sensor_dry`. *Pinned:* golden `pipeline/vacation_budget_exhausted.json`.
- **The forecast cache ignores `hours`** inside its 600 s window. *Pinned:*
  `tests/server/test_contract_weather.py::test_forecast_cache_ignores_hours_current_behavior`.
- **A negative watch duration reports negative elapsed time.** *Pinned:*
  `tests/server/test_contract_services_gaps.py::test_watch_negative_duration_current_behavior_reports_negative_elapsed`.

### Web UI

- **Bulk stop-all always reports success.** It says "Every device is now off." even when `adapter.stop()` returns
  `(False, msg)`, and it still logs `stop/emergency` events. *Where:* `services/bulk.stop_all_irrigators`. *Pinned:*
  `tests/server/test_contract_web_mutations.py::test_bulk_stop_all_current_behavior_reports_success_when_device_stop_fails`.
- **Ack/resolve of a missing alert returns 200** with a success toast; the API returns 404. *Pinned:*
  `::test_alert_action_on_missing_alert_current_behavior_returns_200_success_toast`.
- **Non-numeric form fields cause a plain-text 500.** Bare `int()`/`float()` fails in config, global config, plants,
  `temp_override` and plants/sync. *Pinned:* `::test_non_numeric_form_value_current_behavior_is_unhandled_500`.
- **`POST /clusters/999/irrigate` → 500.** `_decision_panel.html` crashes on the error dict. *Pinned:*
  `::test_irrigate_missing_cluster_current_behavior_500_from_template`.
- **Deleting a populated cluster leaves orphans.** Windows, decision logs, alerts and sensor assignments remain
  (SQLite FKs are off). *Pinned:* `::test_delete_cluster_current_behavior_leaves_orphan_rows`.
- **Error pages are chosen by path, not by `Accept`.** Router-level 404/405 return JSON even to browsers, contrary to
  the handler docstring. *Where:* `web/exception_handlers.py`. *Pinned:*
  `tests/server/test_contract_web_errors.py::test_unknown_path_current_behavior_returns_json_404_to_browsers`.
- **`now_text` uses the process-local timezone** (`time.strftime` in `web/context.py`). It is stable in tests only
  because `clean_env` sets `TZ=UTC`. *Pinned:* web goldens.

### Auth / security / settings

- **`WWW-Authenticate` is dropped on JSON 401s.** The global `HTTPException` handler rebuilds the response without
  `exc.headers`, which affects `/api/v1` and `/mcp`. *Where:* `web/exception_handlers.py`. *Pinned:*
  `tests/server/test_contract_auth.py::test_api_401_current_behavior_drops_www_authenticate_header`,
  `tests/server/test_contract_mcp.py::test_mcp_401_current_behavior_drops_www_authenticate_header`,
  `tests/server/test_contract_web_errors.py::test_json_401_current_behavior_drops_www_authenticate`.
- **The MCP token check is not constant-time (B-12).** `require_mcp_token` compares with `!=`; shared auth uses
  `hmac.compare_digest`. *Where:* `app.py`. *Pinned:* not pinned.
- **`verify_password` raises on a truncated hash.** It raises `VerificationError` instead of returning `False`, so
  login returns 500. *Pinned:* `test_contract_auth.py::test_verify_password_current_behavior_raises_on_truncated_hash`.
- **MCP token edge cases.** `mcp_token=""` gives 401, not 503. The MCP token is also accepted via cookie. *Pinned:*
  ingress golden (plain behavior).
- **Aliased settings are read from three env names.** The `GREENHOUSE_X` fields (MCP token, ntfy, auth secret/admin)
  are also read from bare `X` and `IRRIGATION_X`. Precedence: `GREENHOUSE_X` > `X` > `IRRIGATION_X`. *Pinned:*
  `tests/server/test_contract_settings.py::test_aliased_fields_current_behavior_read_three_env_names`.
- **ntfy URL scheme is not checked (ruff S310).** `services/notify.py::_publish` accepts `file:` and other schemes
  from operator config. *Pinned:* not pinned.
- **Default bind host is `0.0.0.0` (S104).** This is intended for Docker; document it next to the MCP-token warning.
- **Silent swallows remain** (`try/except/pass`, S110). Sites: `app._apply_persisted_pause`,
  `web/context._preference_flags`, `ik10pw` ×3, `devices/irrigators/tuya_generic`, core `sync`.

### Engine (pinned quirks — documented behavior, change only as labeled fixes)

- A decision can end with **zero reasons** (temperature-only, in range): skip, confidence 0.5, `primary_code` NULL.
  *Pinned:* `tests/test_invariants_engine.py::test_inv5_inline_literals_current_behavior`.
- **Critical stress keys on the average** soil moisture, not the driest plant (contrast with invariant #2). *Pinned:*
  `::test_inv2_stress_rule_keys_on_average_current_behavior`.
- **Thresholds are bound at import.** Patching `constants.X` does not steer the engine; patch the name where it is
  used. Since WP8, `engine.is_within_quiet_hours` / `engine.parse_moisture_target` no longer exist; patch
  `logic.timing.is_within_quiet_hours` / `logic.plant_needs.parse_moisture_target`. *Pinned:*
  `::test_inv5_thresholds_are_bound_at_import_current_behavior`.
- **Cleaning is not idempotent.** *Pinned:*
  `tests/test_properties_logic.py::test_clean_readings_is_not_idempotent_current_behavior`.
- **With fewer than 5 readings a spike is not filtered** and can trigger very-dry irrigation. *Pinned:*
  `::test_inv10_short_series_spike_is_not_filtered_current_behavior`.
- **The spike filter drops the first sample of a genuine step change** (40,40,40,60,60). *Pinned:*
  `tests/test_contract_mutation_gaps.py::test_hampel_window_is_centered_with_full_left_context_current_behavior`.
- **`parse_moisture_target` does no validation** ("65-45" stays inverted). *Pinned:*
  `::test_parse_moisture_target_inverted_and_negative_current_behavior`.
- **0 °C / 0 % bounds are treated as missing (B-18).** Trends also ignore a 0.0 °C reading. *Pinned:*
  `::test_plant_needs_zero_is_treated_as_missing_current_behavior`,
  `tests/test_contract_mutation_gaps.py::test_zero_celsius_readings_are_ignored_by_the_temperature_trend_current_behavior`.
- **Grid-pinned edges** (`tests/engine_grid.py`, `tests/golden/engine/`):
  - cooldown (6 h) and leak hold (24 h) are inclusive at the exact edge;
  - a vacation ending exactly now is still active;
  - critical stress, water warning and the no-sensor fallback bypass vacation rationing, and the fallback bypasses
    windows;
  - any `environment` other than "indoor" gets the weather rule but the indoor seasonal table;
  - light thresholds use the UTC month, while seasons use the preferences timezone.

### Devices / ingress

- The SIGTERM path sends OFF twice.
- v2 parser errors are uncaught.
- Log rows never store `water_warning`.
- Negative `hours` slices weather from the end.

All of these are pinned in the ingress goldens.

### CLI (pinned in `tests/cli/test_contract_json_output.py` golden cases)

- A whitespace-only `GREENHOUSE_API_TOKEN` disables the token-file fallback (`token.blank_env_current_behavior_ignores_file`).
- Only `httpx.ConnectError` becomes a clean error (B-17). A read timeout or an empty/non-JSON 2xx body crashes with a
  traceback, exit 1 (`error.read_timeout_current_behavior_uncaught`, `error.empty_200_body_current_behavior_uncaught`).
- A 422 `detail` list is printed as a Python repr (`error.422_detail_list_python_repr`).
- Id `0` means "not given" for `check`, `plant list --cluster`, `sensor list --cluster`.
- `stats --export` with a non-CSV response writes an empty file and reports success.
- Scheduler job ids are put into the URL path unescaped (`client/delete_scheduler_job.path_not_escaped`).
- Bare groups exit 2, not 0.

### TUI

- **A 401 shows "Cannot reach the server"** behind the sign-in dialog. *Pinned:*
  `tests/cli/test_contract_tui_runtime.py::test_401_current_behavior_dashboard_says_cannot_reach_server`.
- **The Activity table cursor jumps to the top on refresh.** Harmless, because rows have no actions. *Pinned:*
  `::test_activity_cursor_current_behavior_resets_to_top_on_refresh`.
- **Wrong cluster-form copy** (other half of B-19): "Blank care fields are filled from the plant DB…" — the API does
  not auto-fill; that needs `/plants/sync`. *Where:* `tui/screens/cluster.py`. *Pinned:* TUI golden.
- **Search-table column widths only grow.** An early partial query can leave the table wider than needed. The test
  side was fixed (`search_citrus`); the widget is unchanged.
- **Not every actuating key confirms.** `i`/`w` open their own dialogs. `S`, `P`, `H`, alert `k`/`v`/`y` and scheduler
  resume run without one. Pinned by the actuation golden. The docs now say this (AGENTS.md, `ConfirmScreen`
  docstring, `tui --help`).

### Schema / tests infrastructure

- **The migrated schema differs from `Base.metadata.create_all`.** Migrations add server defaults
  (`irrigation_windows.weekday_mask='127'`, `user_preferences.scheduler_paused=0`, `notify_*=1`,
  `users.is_active=1`) and named uniques (`uq_irrigators_cluster_id`, `uq_users_username`). `tmp_db` tests therefore
  run on a slightly different schema. *Pinned:*
  `tests/test_contract_schema.py::test_schema_drift_current_behavior_migrations_differ_from_create_all`.
- **Every `create_app` replaces the root log handlers** (Alembic `fileConfig`), so `caplog` sees nothing afterwards.
  Attach a handler to the module logger instead.
- **Mixed directories on one pytest command line break fixtures.** `tests/server/X tests/<core> tests/server/Y` →
  "fixture 'client' not found" for Y. Group paths by directory.
- **Some tests use the real network.** The default server `app` fixture (`tests/server/conftest.py`) does not stub the
  weather client, so older tests can call Open-Meteo (or fall back to 20 °C offline). Contract tests use
  `install_offline_weather`.
- **The wall clock is read directly** (`time.time()` in the engine and services). The baseline migration seeds quiet
  hours 00:00–05:00 into every app. Golden tests freeze the clock.
- `test_cli.py` can read the real CLI token from the user config dir. `Settings` reads `.env`, `IRRIGATION_*` and
  `GREENHOUSE_*`.

### Dead-code and cleanup candidates (not removed yet)

- The `start == end` guard in `logic/timing.is_within_quiet_hours` duplicates `_hour_in_range`.
- `logic/timing.is_within_preferred_hours` is used only by `tests/test_timing.py`.
- `DeviceRegistry.registered_{irrigator,sensor}_keys` are used only by tests.
- `schemas.CreateSchedulerJobRequest` (pinned import name).
- `services/charts._threshold_for_cluster(plant_db)` has an unused parameter that cascades into route dependencies.
- `fallback.temperature_based_decision(temp_range)` is unused but passed by the engine and frozen tests.
- `CONFIDENCE_BASELINE` dead check (post-WP8).
- Literal `90` (plant-health history days) appears in three modules.

Already removed: `any_critical`, `DEFAULT_QUIET_*`, `print_stats_report`, `stats.export_csv`/`format_duration`,
`#global-config` CSS, `set_plant_database`, `database.logger`, `AuthUserDep`, `_sensor_row.html`,
`EVENT_ACTION_OFF`, and others (`git log --grep="dead code" a1b2622..HEAD`, 18 commits).

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
HMAC key). Current runs report 9–10 warnings in total; see the `REFACTOR_REPORT.md` final gate.

# 00 — Smell inventory (Phase 0, read-only)

Author: Smell hunter. Scope: `libs/**` (excluding `migrations/versions`, static assets, JSON data).
Method: full read of every hotspot named in the brief plus grep/AST sweeps across all 3 packages
(23.8k LOC, 120 modules). No source or test file was modified; pytest was not run.

Severity legend: **H** high maintenance cost or a correctness/safety trap · **M** medium · **L** low/cosmetic.

Frozen-contract tags used on every recommendation:

| Tag | Meaning |
|---|---|
| `[MCP]` | `/api/v1` route function name (= MCP tool name / operationId) or route docstring (= tool description) |
| `[OAPI]` | Pydantic class name, field order, field type/default, **class docstring** (→ OpenAPI `description`) |
| `[TUI]` | Textual `BINDINGS`, `action_*` / `on_*` names, widget ids/classes (coupled to `app.tcss`) |
| `[CLI]` | Typer command names, options, command docstrings (= `--help`), JSON output, exit codes |
| `[HTML]` | Web template names, context keys, rendered HTML, HTTP status / detail strings |
| `[IMP]` | Public import path — needs a re-export shim if moved |
| `[PATCH]` | A test monkeypatches this name in this module's namespace (moving it breaks the test even with a shim) |

Test patch tripwires found in `tests/` (these constrain *how* code may move):
`irrigation_mod._time` (services/irrigation.py must keep `import time as _time`), `engine_mod.time`,
`repo_mod.time`, `greenhouse_core.logic.engine.season_for`, `greenhouse_core.learning.issues.seasonal_light_factor`
and `.effective_light_threshold`, `sched_mod.logger`, `IrrigationService.check_cluster`,
`leak_mod.LeakDetectionService.check_after_irrigation`, `adapter._set_duration_local`, `adapter._start_keepalive`,
`adapter._gateway.open_local`, `greenhouse_core.devices[.gateway].tinytuya[.Cloud|.OutletDevice]`, `greenhouse_cli.tui.run`.

---

## A. Hotspot verdicts

### A1. `greenhouse_core/logic/engine.py` (936 LOC) — rule pipeline in one class

**Verdict: not a god class, but `decide_for_cluster` is a 161-line orchestrator (engine.py:106-266) with
inline terminal gates, a nested `_finalize` closure and hidden I/O.** The pure rule functions
(engine.py:600-933) are already well factored.

Exact pipeline order and short-circuit semantics of `IrrigationLogic.decide_for_cluster`:

| # | Step (engine.py line) | Kind | Short-circuit | Persisted via | Quiet-override reason added? |
|---|---|---|---|---|---|
| 0 | `db.get_cluster` (125) | lookup | returns `None` if missing (no log row) | — | — |
| 1 | `evaluated_at = int(time.time())` (129) | clock | — | — | — |
| 2 | no plants → `_decision_with_reason(NO_PLANTS)` (131-144) | terminal | yes | `_persist` direct | no |
| 3 | `_enforce_leak_hold` (149) → `LEAK_HOLD`, CRITICAL, duration 0, interval `LEAK_HOLD_HOURS` | terminal | yes | `_persist` direct | no |
| 4 | `_enforce_cooldown` (155) → `COOLDOWN` (only `action=="start"` events within `MIN_COOLDOWN_HOURS`) | terminal | yes | `_persist` direct | no |
| 5 | `_resolve_quiet_window` (166); if inside window **and not** `bypass_quiet_hours` → `QUIET_HOURS` | terminal | yes | `_persist` direct | no |
| 6 | `_apply_weather_skip_rule` (196) — only if weather client set and `environment != "indoor"`; >2.0 mm in 6 h → `WEATHER_SKIP` | terminal | yes | `_finalize` | yes |
| 7 | inputs: `get_recent_sensor_data(hours=24)`, `analyze_historical_trends`, `detect_stress_conditions`, `_attach_learning_alerts` (best-effort), plant care, temp/humidity ranges, water needs (200-208) | I/O | — | — | — |
| 8 | no sensors **or** `not snapshot.has_data` → `temperature_based_decision` fallback (210-223) | terminal | yes | `_finalize` | yes |
| 9 | build base decision: SKIP, `DEFAULT_*`, confidence **0.5** (225-235) | — | — | — | — |
| 10 | `_apply_water_warning_rule` → IRRIGATE `WATER_WARNING` (237) | terminal (bool) | yes | `_finalize` | yes |
| 11 | `_apply_critical_stress_rule` → IRRIGATE `WATER_STRESS` or SKIP `OVER_WATERING` (239) | terminal (bool) | yes | `_finalize` | yes |
| 12 | `_apply_window_rule` (244) — only when the cluster has `IrrigationWindow` rows; outside → **new** decision `OUTSIDE_WINDOW` (drops snapshot/stress/trends) | terminal | yes | `_finalize` | yes |
| 13 | `_apply_soil_moisture_rule` (248) — sets action: CONFLICT / SENSOR_VERY_DRY / SENSOR_DRY / SENSOR_ADEQUATE / SENSOR_WET | mutating | no | | |
| 14 | `_apply_temperature_adjustment` (249) | mutating | no | | |
| 15 | `_apply_humidity_adjustment` (250) | mutating | no | | |
| 16 | `_apply_light_adjustment` (251) — reads wall-clock month via `seasonal_light_factor()` | mutating | no | | |
| 17 | `_apply_water_needs_adjustment` (252) | mutating | no | | |
| 18 | `_apply_trend_adjustment` (253) | mutating | no | | |
| 19 | `_apply_seasonal_multiplier` (258) — divides interval, clamps, SEASONAL_HOLD/BOOST | mutating | no | | |
| 20 | `_apply_vacation_budget` (264) — VACATION_ACTIVE always; may trim (RATIONING) or flip to SKIP (BUDGET_EXHAUSTED) | mutating (may flip action) | no | | |
| 21 | `_finalize` (266) | — | — | `_persist` (best-effort, swallows) | yes |

Consequences worth pinning in characterization tests (behaviour, not refactor targets): steps 10/11/12 and the
fallback (8) bypass vacation rationing (20); the fallback bypasses windows (12); step 12 returns a decision whose
`sensor_snapshot` is `None`; stress (step 7/11) keys on **average** soil moisture (stress.py:42-50) while step 13 keys
on the minimum.

Smells:
- **M** Hidden clock: `time.time()` (129) and wall-clock month in `seasonal_light_factor()` (813 → utils.py:40, UTC month,
  not `evaluated_at`/local tz). `[PATCH]` tests patch `engine_mod.time` and `engine.season_for`.
- **M** Magic numbers (invariant 5): `hours=24` (200), confidence `0.5` (231), `get_forecast(hours=6)` (557), `precip <= 2.0` (562),
  `86400` (407, 424, 425), `3600` (499), `"45-65"` default (684), `"open-meteo"` (578).
- **M** Duplicated tz lookup `prefs = self.db.get_preferences(); tz_name = prefs.timezone if prefs else None` ×3 (281-282, 306-307, 334-335)
  and again in web `clusters.py:165-178`.
- **L** `_apply_window_rule(self, cluster, cluster_id, evaluated_at, decision)` — `cluster` and `decision` unused (292).
- **L** `_apply_seasonal_multiplier`: `isinstance(care, dict)` defensive dead branch (351).
- **L** Lazy import of `IrrigationLearner` inside `_attach_learning_alerts` (585) to dodge a cycle.
- **L** `_decision_with_reason` has 12 params; `sensor_snapshot/stress_indicators/trends` are never passed by any caller.
- **L** Humidity/light/trend adjustments report the *nominal* step as `interval_delta` even when clamped (789, 797, 805, 823…; see bug B-14),
  whereas temp (760, 769) and water-needs (880-881) compute the real delta — inconsistent idiom.

Direction (YAGNI): keep the explicit, readable sequence — do **not** introduce a rule-registry/strategy pattern. Safe steps, after a
golden test over `decision.model_dump()` for a matrix of fixtures: (1) `_tz_name()` helper; (2) move literals to `constants.py`
(new names, identical values); (3) extract `_gather_inputs()` (step 7) and `_terminal(decision)` helper replacing the four
`if persist: self._persist(...); return x` blocks; (4) drop unused params of `_apply_window_rule` only if no test calls it with
positional args (tests mention it only in docstrings). Risk: engine is high-risk (two reviewers). Contracts: none public;
`IrrigationLogic.decide_for_cluster` signature frozen; `__all__` frozen; `TriggerCode` frozen.

### A2. `greenhouse_core/repository.py` (1309 LOC, ~80 methods) — god object?

**Verdict: god object by breadth (12 aggregates: clusters, plants, irrigators, sensors, assignments, readings, events, configs,
decision logs, activity, alerts, health, vacation, windows, prefs), but every method is shallow. The cost is copy-paste, not complexity.**

- **M** 7 copy-pasted patch loops `for key, value in fields.items(): if value is None: continue; if hasattr(row, key): setattr(...)`:
  `update_vacation_window` (963-967), `update_irrigation_window` (1017-1021), `update_preferences` (1048-1050), `update_cluster`
  (1154-1156), `update_plant` (1174-1176), `update_sensor` (1266-1272, + JSON + plant_id routing), `update_irrigator` (1292-1298, + JSON).
  `hasattr` lets callers set *any* attribute incl. `id` (latent).
- **M** 6 copy-pasted delete-by-id bodies (971-978, 1025-1031, 1160-1167, 1180-1193 variant, 1278-1285, 1302-1309).
- **M** 3 identical cursor-pagination queries `list_all_sensors/irrigators/plants` (1056-1137).
- **M** Hidden clock: 24 `int(time.time())` defaults; `timestamp or int(time.time())` treats `0` as "now" (317, 459, 677, 738, 899, 949).
  `[PATCH]` `repo_mod.time` is patched → keep `import time` + `time.time()` call shape.
- **M** Missing read methods force 17 bypasses via `repo.session.get/scalar(s)` in services/routes (health_monitor.py:254,266,292,364;
  charts.py:50,117,323,353; search.py:36-108; efficacy.py:42; routes/vacation.py:84; routes/charts.py:37; web/plant_dashboard.py:29),
  and O(n) scans (`web/routes/vacation.py:90`, `routes/plants.py:221-228`, `web/routes/operations.py:117-124`,
  `routes/sensors.py:92`, `routes/plants.py:93`).
- **L** Stringly-typed statuses `"open"/"acknowledged"/"resolved"` (746, 833, 848, 859, 866), severity `"info"`, action `"start"` (497).
- **L** `bulk_add_sensor_readings` takes an 8-tuple positional (364-375) — primitive obsession; no lib caller found except sync? (keep).
- **L** `get_irrigation_window`, `add_irrigation_window`, `update_irrigation_window`, `delete_irrigation_window` lack docstrings.

Direction: private helpers `_patch(row, fields, *, json_fields=())`, `_delete(model, id) -> bool`, `_paginate(stmt, model, limit, after_id)`
applied **only** where semantics are byte-identical (note `set_irrigation_config`/`update_global_irrigation_config` deliberately allow
`None` — do not fold them). Add additive read methods (`get_vacation_window`, `get_open_alert_by_dedup_key`,
`list_unresolved_alerts(code)`) to retire session bypasses. Splitting into a `repository/` package of mixins is optional and
cosmetic — defer. Contracts: `[IMP]` `greenhouse_core.repository` (incl. `SameClusterMoveError`, `IrrigatorExistsError`); all public
method signatures frozen.

### A3. `greenhouse_core/schemas.py` (999 LOC, ~110 classes) — dumping module?

**Verdict: no. It is a cohesive DTO module, sectioned by resource with `# ---` banners; size comes from breadth.** Real smells:
- **L** Duplicate `parse_config` validator (`IrrigatorResponse` 115-120, `SensorResponse` 180-185).
- **L** Config field set repeated 4× (`SetConfigRequest` 292, `ConfigResponse` 315, `GlobalConfigResponse` 333, `UpdateGlobalConfigRequest` 355)
  plus `repository._CONFIG_PATCHABLE_FIELDS` (506) and TUI `resources.config_fields` — but field **order differs** between them, so
  mixins would reorder OpenAPI properties.
- **L** Naming: `AlertResponse` (418) is a *learning* alert, `AlertSummary` (619) is the inbox alert.
- **L** Response models living outside: `routes/plants.py:251 SnapshotResponse`, `routes/auth.py:25-44` (4 classes).
- **L** ~45 classes without docstrings.

Direction: leave as is. At most hoist the JSON validator into a module function referenced by both classes (no schema impact).
**Contract trap:** adding a class docstring changes the OpenAPI `description` `[OAPI]`; mixins/inheritance changes field order `[OAPI]`;
splitting into a package changes `__module__` (FastAPI only uses it on name collisions — none today — but risky) and needs `[IMP]` shims.

### A4. `greenhouse_core/utils.py` (117 LOC)

**Verdict: `utils`-style dumping ground mixing two unrelated concerns plus a mutable global.**
- **M** Seasonal light domain logic + threshold table (11-24) and `NIGHT_LUX_THRESHOLD = 15` (28) live outside `constants.py` (invariant 5);
  `logic/sensors.py:46` re-hardcodes `> 15`.
- **M** Hidden clock: `datetime.now(tz=UTC).month` (40) — UTC month, ignores user tz and `evaluated_at`.
- **M** Module-level mutable global `_display_timezone` + `set_display_timezone` (74-84) written by app startup and `scheduler.apply_timezone_preference`;
  `os.getenv("IRRIGATION_TZ")` read on every call (95).
- **L** Broad `except Exception` fallback to UTC (114). **L** Shebang on a library module (1).

Direction: move light helpers to `greenhouse_core/logic/light.py` (constants → `constants.py`), keep `utils.py` re-exporting them
`[IMP]`. `[PATCH]` `learning.issues` must keep importing `seasonal_light_factor`/`effective_light_threshold` *by name* into its namespace.
Leave display-tz global (YAGNI) but document it.

### A5. `greenhouse_core/models.py` (433 LOC)

**Verdict: healthy.** Declarative ORM, docstrings present. Smells: **L** JSON-in-`String` `config` columns (Irrigator 74, Sensor 96)
→ `json.loads`/`json.dumps` spread across repository, schemas, gateway (`_coerce_config`), web irrigators (`_parse_config`);
**L** stringly status/severity/action columns; **L** `ENTITY_*` constants live here (odd home) and some callers use the literal
`"cluster"` (irrigation.py:748, repository.py:725 default). DDL frozen → leave; only centralise the JSON (de)coding helper.

### A6. `greenhouse_server/services/irrigation.py` (760 LOC)

**Verdict: mixed-concern module + one god function.** It holds (a) scheduler job plumbing (`handle_watcher_interrupted` 31-119,
`schedule_pump_watcher` 122-223, leak-check scheduling/rearm 226-369) and (b) `IrrigationService`.
- **H** `run_irrigation_pipeline` 187 lines (422-608): temperature resolution, engine call, result-dict assembly, dry-run/skip branch,
  irrigator/registry/adapter guards, device-health gate, actuation, event + activity rows, leak/watcher scheduling, notify, failure alert.
  Untyped `dict` result rebuilt into `IrrigateResponse(**result)`; error signalled by string (`routes/operations.py:149`
  matches `reason == "cluster not found"`).
- **H** Service locator via private global: `from greenhouse_server.scheduler import _app, scheduler, ...` inside functions (151, 263, 299, 321, 345).
- **M** Stale comment: "the decision is re-persisted so decision_logs reflects the block" (497-501) — no re-persist happens (bug B-6).
- **M** Duplicated knowledge: `monitor_cluster` re-implements moisture-target parsing (635-639) with its own fallback `45.0, 65.0`
  and magic `-15`/`+10` (643, 647), `hours=2` (628); passes no `category` to `get_care_data` (634) unlike the engine.
- **M** Magic: `20.0, "fallback (20C)"` (420); watcher defaults `2.0/5.0/5` (180-182) duplicate `Settings` (config.py:156-163) and
  `PumpWatcherService.__init__` defaults (pump_watcher.py:59-61).
- **M** `check_cluster` builds the same result dict 3× (687-716); `collect_learning_alerts` runs here (680) **and** again inside
  `sync_cluster_alerts` (alerts.py:96) **and** inside the engine (`_attach_learning_alerts`) → learner runs 3× per cluster per check.
- **M** Session boilerplate (open/try/commit/rollback/log/close) repeated in `_run` (172-210), `_run_leak_check` (268-295), `rearm_leak_checks` (351-366).
- **L** `_leak_check_done` scans only the newest 500 activity rows (241-243).
- **L** Activity codes as literals (`"decision_skip"` ×2, `"irrigated"`, `"actuation_failed"` ×2) next to declared constants.

Direction: (1) extract `_skip_result/_error_result` helpers and split the pipeline into `_decide()`, `_health_gate()`, `_actuate()`
private methods keeping result-dict keys identical; (2) move job plumbing to `services/irrigation_jobs.py` **with re-exports**, but
note `manual_control.py` imports `schedule_pump_watcher` from here and tests patch `irrigation_mod._time` `[PATCH]` — moving the functions
that call `_time` would break that test; keep `_time` users in place or move last. (3) shared `background_session()` context manager (see A7).

### A7. `greenhouse_server/scheduler.py` (560 LOC)

**Verdict: mixed concerns, acceptable size.** Job registry + tz/cron rebuild + pause persistence + weather-client rebuild + process-wide
mutable globals (`scheduler` 28, `_app` 30, `_shutdown_event` 37, `_CORE_JOB_IDS` 80).
- **M** Five jobs repeat identical session scaffolding (`_sync_job` 292-312, `_health_snapshot_job` 315-331, `_check_job` 334-364,
  `_anomaly_job` 367-380, `_health_monitor_job` 383-409) + `init_health_monitor` (428-443). Inconsistent `_app is None` guard
  (only `_health_monitor_job` and indirectly `_sync_job` check it).
- **M** Magic schedule values: `minutes=15` (184), `hour=0, minute=30` (216-217), `sync_all_sensors(hours=6)` (306).
- **M** `"check_all"` literal (72, 210) while `CHECK_ALL_JOB_ID` is declared later (446).
- **M** `_resolve_zoneinfo` (102-113) duplicates `logic.timing._resolve_tz` (timing.py:30-37) — comment admits it.
- **L** `apply_timezone_preference` (252-279) rebuilds `WeatherClient` — weather concern in the scheduler module; lazy imports.
- **L** Redundant inner import of `IrrigationRepository` (321).
- **L** `_restore_persisted_scheduler_pause` in app.py swallows every exception silently (app.py:296-297): a failed read silently
  re-enables auto-irrigation after restart.

Direction: `@contextmanager def _job_session(label)` yielding `repo`; keep each job's log message text identical (`sched_mod.logger` is
patched `[PATCH]`). Replace literals with constants. Scheduler is high-risk (two reviewers). `[IMP]` `greenhouse_server.scheduler` frozen
(`apply_timezone_preference`, `delete_job`, `set_check_all_paused`, `CoreJobError`, `JobNotRegisteredError` imported by routes/web).

### A8. `greenhouse_server/services/health_monitor.py` (431 LOC)

**Verdict: sound design (cache + diff + typed alerts), moderate smells.**
- **M** Bypasses the repository with raw SQL (`_open_alert_stmt` 372-378, `migrate_legacy_pump_alerts` 286-295) and lazy
  `sqlalchemy`/`models` imports inside methods.
- **M** In-memory-only cache: not seeded from open alerts on startup → see bug B-9.
- **L** `backfill_from_history` duplicates the "check open alert / raise" block twice (252-274); `hours=24 * 7` magic (244).
- **L** `_infer_cluster_id` / `_infer_label` (400-416) each re-fetch the same row; called on every `record()` even with no transitions.
- **L** Blocking-alarm set inline (230); `_ALARM_SEVERITY` stringly.

Direction: additive repo methods; one `_raise_if_not_open(alarm, sensor)` helper; `_lookup_entity(entity_type, id)` returning
`(cluster_id, label)`. Contracts: `[IMP]` `HEALTH_ALARM_TO_TRIGGER`, `SOURCE_HEALTH`, `LEGACY_PUMP_DRY_RUN_CODE`, class public API.

### A9. `greenhouse_server/services/pump_watcher.py` (308 LOC)

**Verdict: fine; local smells only.**
- **M** `watch()` returns four near-identical outcome dicts (119-178), nesting depth 4.
- **L** Mixed clocks: monotonic `clock` for deadline, `time.time()` for `started_at` default (109) and `elapsed_estimate` (233).
- **L** `"alarm_dp": 105` magic (238) duplicating the profile DP map; defaults duplicated with Settings (see A6).
- **L** `except Exception: pass` on rollback (296-297). `_lazy_monitor` typed `-> DeviceHealthMonitor | None` but never returns None (299-308).
- **L** Naming: `ACTIVITY_CODE = "pump_dry_run"` (48) equals `LEGACY_PUMP_DRY_RUN_CODE` (an *alert* code) — confusing.
- **L** `"completed"` outcome reports `read_failures: 0` even after earlier transient failures (131).

Direction: `_outcome(kind, polls, failures, alarm_raw, now)` helper. `[IMP]` `ALERT_CODE`, `EVENT_ACTION_ABORTED`, `ACTIVITY_CODE`.

### A10. `routes/*` vs `web/routes/*`

**Verdict: genuine knowledge duplication, and the copies have drifted (several drifted copies are bugs, §E).** The two layers rightly have
different I/O shapes; the duplication is in *rules*, which belong in services/deps.

| Rule | API | Web | Drift |
|---|---|---|---|
| Plant-DB sync (plant/cluster/all) | `routes/plants.py:219-248` | `web/routes/operations.py:111-141` | verbatim, incl. O(n) plant scan |
| Irrigator create + capacity follow-up | `routes/irrigators.py:83-105` | `web/routes/irrigators.py:83-115` | web lacks `IntegrityError` → 500 |
| Sensor create | `routes/sensors.py:66-85` | `web/routes/sensors.py:59-70` | web lacks plant-in-cluster check and `IntegrityError` |
| Window validation (hours/differ/mask) | `routes/windows.py:23-33` | `web/routes/windows.py:36-42` | messages differ (`…0..23` vs `…0..23.`) |
| Weekday bits/labels/format | — | `web/routes/clusters.py:92-100`, `configs.py:13-22`, `windows.py:20,76` (3×) | identical |
| Quiet-hours "active now" | engine.py:268-290 | `web/routes/clusters.py:165-179` | same logic re-implemented |
| `check_all` `has_alerts` | `routes/operations.py:200` (alerts ∨ maintenance ∨ needs_water) | `web/routes/operations.py:86` (alerts only) | drifted |
| Vacation `starts < ends` | PUT only (`routes/vacation.py:87-90`); POST none | create+edit (`web/routes/vacation.py:76-77,115-116`) | API POST unvalidated |
| 404 lookups | `"Cluster not found"` inline 20× despite `deps.require_cluster`; `"Cluster has no irrigator"` 4×; `"Irrigator not found"` 4× | | punctuation differs per layer |
| Relative time | — | `web/filters.py:28-40` `age_seconds` vs `web/routes/plant_dashboard.py:115-124` `_relative_time` | different outputs |

Also: **M** transaction management lives in routes (68 `session.commit()` calls); **M** service results are untyped dicts rebuilt into
Pydantic in routes (`routes/operations.py:60-106, 298-316`); **L** CSV building inline in a route (`routes/operations.py:355-389`) while a
near-duplicate dead `stats.export_csv` exists (stats.py:101-134, different time format).

Direction: service-level functions (`ClusterService.sync_plants`, `create_irrigator(...)`, `validate_window(...) -> code`), shared
weekday helper module, `deps.require_*` lookups taking the detail string as a parameter. Contracts: `[MCP]` route function names and
docstrings, `[HTML]` detail strings/status codes per layer, template context keys.

### A11. `greenhouse_cli/client.py` + `commands/*`

**Verdict: thin and mostly fine.**
- **M** `IRRIGATION_SERVER_URL` resolution duplicated 3× (`commands/_helpers.py:14`, `commands/auth.py:26`, `commands/tui.py:35`) plus the
  default URL in `IrrigationClient.__init__` (client.py:78).
- **L** `"/api/v1"` prefix repeated 79×; `{k: v ... if v is not None}` 9×; 74 public methods without docstrings (safe to add — not `--help`).
- **L** `_request` maps only `httpx.ConnectError` (88-89) — see bug B-17.
- **L** Device-config dict building duplicated in `irrigator_add` (29-33) and `irrigator_update` (111-117) **with different semantics**
  (truthy vs `is not None`) — do not merge naively.
- **L** `check` treats cluster id `0` as missing (`commands/operations.py:42`).

Direction: `resolve_server_url(ctx)` helper; `_drop_none(**kw)`; docstrings on client methods. `[CLI]` frozen: command docstrings, options,
exit codes, JSON output.

### A12. `greenhouse_core/devices/*`

**Verdict: well structured (gateway + registry + profile + adapters); a few concrete issues, one serious (B-2).**
- **H** `ik10pw._start_keepalive` installs a SIGTERM handler via `signal.signal` (177) — only legal on the main thread (bug B-2); also
  blocks the caller for the whole run.
- **M** Three `except Exception: pass` in the keep-alive loop (173-174, 186-187, 194-195); `tuya_generic.status` swallows local errors (76-77).
- **M** `gateway.get_live_reading` duplicates the DP-parse loop for v2/v1 (164-169, 178-182); v2 path does not catch parser
  `ValueError` while `get_device_logs` does (226-232) — inconsistent.
- **L** `os.environ` read in a core constructor (gateway.py:116-118). Magic `evtype=7` (205), `0x01` default bitmask duplicated
  (ik10pw.py:36, 235), `max_records=100` (190).
- **L** Dead/stale: `SensorProfile.dp_parsers` is never consulted (adapter calls the gateway's global `DATAPOINT_PARSERS`), yet
  docstrings at `sensors/tuya_generic.py:3,24`, `profile.py:81-83`, `tr301z.py:3` claim it is; `read_live` and adapter `status()` have no
  lib callers.
- **L** `_send_switch` hardcodes the `"switch"` code rather than the profile (tuya_generic.py:34).

Direction: `_parse_dps(items)` helper (tiny); fix stale docstrings. Do **not** restructure — high-risk area, low payoff.
`[PATCH]` `_set_duration_local`, `_start_keepalive`, `_gateway.open_local`, `gateway.tinytuya` are patched; `[IMP]` `greenhouse_core.devices` `__all__`.

### A13. `greenhouse_cli/tui/screens/cluster.py` (800 LOC)

**Verdict: god screen.** One class renders 8 tabs (overview, charts, plants, sensors, insights, decisions, history, windows/config) and
dispatches CRUD by `if/elif` on the active tab id three times (`action_new` 584-626, `action_edit` 628-678, `action_delete` 680-720,
nesting depth 5). Payload→row transformations (279-305, 327-353, 355-376, 378-399, 401-429, 431-495) are pure but trapped in widget methods.
- **M** `_selected(table, rows)` + "Select a row first" duplicated in `alerts.py:90`, `settings.py:128`.
- **L** Magic: `RANGES`, `STATS_DAYS` are fine; `hours=24 * 30, limit=200` (379), `limit=100` (356), `72` (316-317), `127` (609), `30` days export (792).
- **L** Help text is stale: "Blank care fields are filled from the plant DB" (594) — API does not auto-fill (bug B-19).
- **L** 21 public methods without docstrings.

Direction: move row builders to pure functions in `tui/model.py` (already the "pure payload → view" home per CLAUDE.md) and unit-test them;
hoist `_selected` into `DataScreen`. A table-driven CRUD dispatch is optional (YAGNI). `[TUI]` frozen: `BINDINGS`, every `action_*`/`on_*`
name, widget ids (`#plants-table`, `#garden`, …), column headers.

### A14. `greenhouse_cli/tui/sprites.py` (386 LOC)

**Verdict: cohesive data module, fine.** **L** `mood_for` (66-89) is display logic with its own fallback band 40/70 (30-31) distinct from
server defaults 45/65 — documented as display-only. **L** Sprite width `16` literal (382). **L** Colour hex literals shared with
formatting/widgets/screens (`"#7ed957"` ×12, `"#e0c341"` ×9, `"#4fb3ff"` ×6 across 5 files). Leave; optionally a palette module.

### A15. `greenhouse_cli/tui/widgets.py` (342 LOC)

**Verdict: fine.** **L** `MetricChart.show_payload`/`show_overlay` duplicate x-axis/tick/event-vline code (205-218 vs 241-252);
`SERIES_COLORS[i % 7]` (203) hardcodes the list length; `Heatmap.DAYS` (275) is yet another weekday-label copy; `PlantTile` stores a 6-tuple
`_args` (166); broad `except Exception` in `refill` (339) is justified. `[TUI]` widget class names/CSS are frozen.

---

## B. Per-module smell catalogue (beyond §A)

### greenhouse-core
| Sev | Smell | Evidence |
|---|---|---|
| M | Knowledge duplication: `"45-65"` default + target parsing in 7 places | engine.py:684; services/irrigation.py:635-639; services/health.py:52; services/forecast.py:65; learning/issues.py:102-106, 190; plant_db.py:113 |
| M | Magic numbers (invariant 5) | stress.py:28 (`-20`), 36 (`0.4`), 49 (`-10`), 54 (`+5`); trends.py:21 (`48`), 54 (`7*24`), 64 (`<1`, `<2`), 66 (`>3`); fallback.py:89 (`-4`), 91 (`+6`); sensors.py:46 (`>15`) |
| M | Stringly vocabularies: config `mode` `"manual"` | fallback.py:60 (`"schedule"` mode offered by TUI `resources.py:15` has no behaviour) |
| M | Recomputed plant care (stress re-queries plants + care data the engine already has) | stress.py:20-21 vs engine.py:205 |
| L | Truthiness drops `0` values | trends.py:41-42 (`if r.temperature`), plant_needs.py:10-11, 19-20 |
| L | Broad `except Exception` returning defaults | plant_needs.py:31; utils.py:114; sync.py:112-113 (`pass`) |
| L | Unused constants | `DEFAULT_QUIET_START_HOUR/END_HOUR` (constants.py:20-21), `DEFAULT_LATITUDE/LONGITUDE` (215-216; literal `45.464/9.189` re-typed in config.py:23-24 and weather.py:13) |
| L | Dead public code (frozen import paths → deprecate, don't delete) | stats.print_stats_report (67-98, uses `print`), stats.export_csv (101-134, tests only), plant_db singleton `get/set/reset_plant_database` (162-179, tests only), `PlantDatabase.get_light_needs_info` (141), `StressIndicators.any_critical` (decision.py:173), `TriggerCode.DAILY_CAP_HIT` (45, frozen member) |
| L | Module-level env read at import | plant_db.py:9-13 (`PLANT_DB_PATH`) |
| L | Stale key in live-read guard | sync.py:98 (`"humidity"` is never a canonical key) |

### greenhouse-server
| Sev | Smell | Evidence |
|---|---|---|
| M | Untyped dict contracts between services and routes | services/cluster.py:30-84, 86-124; services/irrigation.py:455-608; services/sync.py:41-45 |
| M | Activity/alert source literals beside declared constants | services/leak.py:115 (`"leak"` vs `SOURCE_LEAK`); irrigation.py:470, 514, 558, 588, 597 (`"irrigation"`) |
| M | Event action vocabulary inconsistent | `"off"` manual_control.py:179 vs `"stop"` irrigation.py:79 / bulk.py:44; `"attempted"` irrigation.py:546; `"aborted"` pump_watcher.py:47 |
| M | Three dedup-key schemes | alerts.py:44 (`src::code::cid::head`), health_monitor.py:91 (`health:type:id:alarm`), irrigation.py:742 (`check_failed:cluster:id`) |
| M | Security-sensitive duplication: MCP token compared two ways | app.py:115 (`!=`) vs auth.py:176-185 (`hmac.compare_digest`) |
| M | `web/context.base_context` opens its own DB session per render and swallows errors | web/context.py:25-56 |
| L | Long functions | app.py:123 `create_app` (132 lines); data_quality.py:13 (124); search.py:13 (118); anomaly.py:50 (115); forecast.py:35 (112); health.py:20 (100) |
| L | Long parameter lists | alerts.py:151 `raise_alert` (11); web plants `create_plant`/`update_plant` (12-13 form params — inherent to forms) |
| L | Silent `except Exception: return []` | maintenance.py:18 (engine's equivalent at engine.py:593 logs) |
| L | `time.strftime` server-local clock in UI chrome | web/context.py:69 (ignores display tz) |

### greenhouse-cli
| Sev | Smell | Evidence |
|---|---|---|
| M | Weekday/vocabulary copies the CLI cannot import from core (accepted, but keep in one TUI place) | tui/formatting.py:132-136, tui/widgets.py:275, tui/resources.py:12-15, 91 |
| L | Stale/wrong placeholder | tui/resources.py:66 (`{"ip": …, "version": "3.5"}` vs gateway keys `device_ip`/`local_key`) |
| L | Datetime semantics differ from web | tui/screens/forms.py:56 (naive local) vs web/routes/vacation.py:32 (UTC midnight) |

---

## C. Do-not-do list (contract traps discovered)

1. Do not add/alter docstrings on Pydantic classes in `schemas.py` or route modules — they become OpenAPI `description` `[OAPI]`.
2. Do not edit `/api/v1` route docstrings even when stale (e.g. `routes/windows.py:49-50` says "falls back to the global default preferred hours",
   contradicting invariant 9) — record in REFACTOR_NOTES instead `[MCP]`.
3. Do not merge config schemas via mixins (field order) `[OAPI]`.
4. Do not "unify" API/web error strings — they differ in punctuation and are asserted `[HTML]`.
5. Do not rename `import time as _time` in `services/irrigation.py`, or replace `time.time()` with `from time import time` in
   engine/repository `[PATCH]`.
6. Do not move `seasonal_light_factor`/`effective_light_threshold` without keeping them importable by name in `learning/issues.py` and `utils.py` `[PATCH][IMP]`.
7. Do not delete "dead" public functions/constants/`TriggerCode` members — import paths and constants are frozen.

---

## D. Prioritised refactorings (top 25, ranked by maintenance-cost-removed ÷ risk)

| # | Refactoring | Removes | Risk | Contract flags |
|---|---|---|---|---|
| 1 | Shared server-side weekday helper (`WEEKDAY_BITS`, `WEEKDAY_LABELS`, `FULL_WEEK_MASK`, `format_weekday_mask`) used by `web/routes/{clusters,configs,windows}.py` | 3 identical copies | very low (private names) | none; `[HTML]` output identical |
| 2 | `ClusterService.sync_plants(plant_id, cluster_id) -> (synced, errors)` used by `routes/plants.sync_plants` and `web/routes/operations.sync_plants` | verbatim duplicate + 2 O(n) scans | low | `[MCP]` keep route name/docstring; `[HTML]` keep 404 detail strings |
| 3 | Repository private helpers `_patch` / `_delete` / `_paginate` (only byte-identical bodies) | ~120 copy-pasted lines | low | `[IMP]` none moved; signatures unchanged; `[PATCH]` keep `time.time()` |
| 4 | Background `_job_session(label)` context manager for 5 scheduler jobs + 3 jobs in services/irrigation.py + `init_health_monitor` | ~9 copies of session scaffolding | medium (scheduler = high-risk) | `[PATCH]` `sched_mod.logger` messages must stay identical |
| 5 | `moisture_target_range(care)` helper wrapping `parse_moisture_target(care.get("soil_moisture_target", "45-65"))` | 7 copies | low-medium — `monitor_cluster` and `issues.py` parse malformed strings differently (3-part strings); fold only proven-equivalent sites, pin the rest | none |
| 6 | Move engine/stress/trends/fallback/sensors/scheduler/monitor literals into new `constants.py` names (identical values) | invariant-5 violations (~25 literals) | low (engine reviewers) | constants values frozen → add names only |
| 7 | Engine `_tz_name()` helper + shared `quiet_window_now(repo, cluster_id, now)` used by engine and `web/routes/clusters.cluster_detail` | 4 tz copies + re-implemented quiet logic | medium (engine) | `[HTML]` `quiet_active_now` key unchanged |
| 8 | `validate_window(start, end, mask) -> error_code | None` shared by API and web, each layer mapping codes to its existing message | duplicated validation | low | `[HTML]`/`[MCP]` messages byte-identical |
| 9 | `deps.require_irrigator`, `require_cluster_irrigator`, `require_*_in_cluster(detail=...)` lookups | ~30 inline 404 blocks | low | detail strings per layer preserved |
| 10 | Split `IrrigationService.run_irrigation_pipeline` into `_decide` / `_health_gate` / `_actuate` + result helpers | 187-line function | medium (actuation path; golden on result dicts + DB rows first) | result-dict keys frozen via `IrrigateResponse` / templates |
| 11 | `check_cluster` result builder + pass already-collected learning/maintenance findings into `sync_cluster_alerts` (behaviour-neutral only if outputs proven identical; otherwise just the builder) | 3× dict build; 3× learner run | medium | `CheckClusterResponse` fields frozen |
| 12 | Additive repository reads (`get_vacation_window`, `get_open_alert_by_dedup_key`, `list_unresolved_alerts(code)`, use `get_plant`) replacing 17 `repo.session` bypasses | repository leak | low-medium | new public methods only |
| 13 | `health_monitor`: `_raise_if_not_open` + `_lookup_entity` helpers; drop lazy imports | duplicated backfill blocks | low | `[IMP]` module `__all__` |
| 14 | TUI: move ClusterScreen row builders into pure functions in `tui/model.py`; hoist `_selected` into `DataScreen` | 800-line god screen; 3 `_selected` copies | low-medium | `[TUI]` ids, actions, columns unchanged |
| 15 | `pump_watcher.watch` `_outcome(...)` helper | 4 duplicated dicts, depth 4 | low | outcome dict keys frozen (tests) |
| 16 | CLI `resolve_server_url(ctx)` helper | 3 copies of env/default resolution | very low | `[CLI]` behaviour identical |
| 17 | `utils.py` split: light helpers → `logic/light.py` (+ `NIGHT_LUX_THRESHOLD`, light table to constants) with re-export shim | utils dumping ground; `>15` duplicate | low | `[IMP]` shim; `[PATCH]` issues namespace |
| 18 | `scheduler.py`: replace `"check_all"` literals with `CHECK_ALL_JOB_ID` (move declaration up); reuse one tz resolver | literal drift, `_resolve_zoneinfo` duplicate | low (scheduler) | `[IMP]` keep `_resolve_zoneinfo` name if tests import it (they import `_resolve_check_cron_hours` only) |
| 19 | Module constants for event actions / alert statuses / activity sources (plain `str` constants, not enums) | stringly vocabularies across ~15 files | low | DB values identical |
| 20 | Shared `create_irrigator_with_capacity(repo, ...)` used by API and web create | duplicated two-step create | low-medium | per-layer error mapping preserved (web currently 500s on IntegrityError — keep!) |
| 21 | Gateway `_parse_dps(items)` helper | duplicated v1/v2 loop | low technically, but devices = high-risk → low priority | `[PATCH]` gateway `tinytuya` |
| 22 | CLI `client.py`: `_drop_none(**kw)` + docstrings on 74 methods | 9 copies; doc gaps | very low | none (client docstrings not in `--help`) — *mostly cosmetic* |
| 23 | Fix stale comments/docstrings that are **not** contracts (irrigation.py:497-501, devices `dp_parsers` docs, TUI help text cluster.py:594, resources.py:66 placeholder) | misleading docs | very low | TUI hint text is rendered → `[TUI]` golden check; route docstrings excluded |
| 24 | `widgets.MetricChart` shared axis/event helper; TUI palette constants | duplicated plotting & hex literals | low | *cosmetic* |
| 25 | `schemas.py` shared JSON-config validator function; use unused `DEFAULT_LATITUDE/LONGITUDE` in config/weather defaults | 2 + 3 duplicates | low | `[OAPI]` defaults identical — *cosmetic* |

Deliberately **not** recommended (YAGNI / risk > benefit): rule-registry pattern for the engine; splitting `schemas.py` or `repository.py`
into packages; typed service-result dataclasses (would ripple into templates); merging CLI/TUI formatting with server filters (package
boundary forbids it); unit-of-work refactor of route commits (behavioural).

---

## E. Observed bugs (suspected, not to be fixed)

Each needs a characterization test pinning current behaviour before any nearby refactor.

| ID | Sev | Suspected bug | Evidence |
|---|---|---|---|
| B-1 | H | `dry_run_global` ("While on, no irrigator ever actuates") is never enforced — only rendered as a banner; pipeline, manual start, scheduler all ignore it. | web/templates/preferences.html:72; only read at web/context.py:49; no reference in services/irrigation.py, manual_control.py, scheduler.py |
| B-2 | H | IK10PW keep-alive fallback calls `signal.signal(SIGTERM, …)` outside the main thread (FastAPI threadpool / APScheduler worker) → `ValueError` raised *after* the pump was switched on and *before* the `try/finally` that switches it off; run is bounded only by the device's ~30 s auto-off, and the error propagates. | devices/irrigators/ik10pw.py:160-177 (on at 160, handler at 177, `finally: off` at 190-195) |
| B-3 | M | Sensors flap DEVICE_OFFLINE: offline threshold 30 min is applied to `last_seen_ts` = latest *persisted* reading, but readings are only persisted by the 180-min sync → every sensor goes "offline" ~30 min after each sync until the next one. | constants.py:199; health_monitor.py:314-317; sensors/tr301z.py:95; config.py:31 |
| B-4 | M | Cluster-level caps only: `check_rate_limits` reads the raw cluster row, so global `daily_cap_minutes`/`max_events_per_day` (settable via global config) are never enforced; automatic pipeline never checks caps at all (`TriggerCode.DAILY_CAP_HIT` unused). | manual_control.py:52-54; repository.py:511-512 (global fields); decision.py:45 |
| B-5 | M | Re-opened alerts never notify again: `upsert_alert` re-opens a resolved row and increments `occurrence_count`; `notify_if_new_alert` requires `occurrence_count == 1` → a recurring NO_WATER/leak after resolution is silent. | repository.py:740-748; services/alerts.py:67 |
| B-6 | M | Device-health block is not re-persisted: the comment says decision_logs reflects the block, but the persisted row keeps the engine's IRRIGATE action and reasons (audit gap vs invariant 6). | services/irrigation.py:497-529 (no `add_decision_log`/`_persist`) |
| B-7 | M | Web create irrigator/sensor: no `IntegrityError` handling (duplicate Tuya id → 500), and web sensor create/update accepts a `plant_id` from another cluster (API rejects). | web/routes/irrigators.py:90-114; web/routes/sensors.py:59-70 vs routes/sensors.py:66-84 |
| B-8 | M | API `POST /vacation` does not validate `starts_at < ends_at` (PUT and web do) → reversed windows via API/MCP/CLI. | routes/vacation.py:33-59 vs 87-90; web/routes/vacation.py:76-77 |
| B-9 | M | Health-monitor cache is memory-only and never seeded from open alerts: after restart an open NO_WATER/OFFLINE alert does not block actuation until the next poll, and if the condition cleared while down, the alert is never auto-resolved (no transition is seen). Same for alerts raised by `backfill_from_history`. | health_monitor.py:125, 189-212, 224-232, 236-274 |
| B-10 | M | `water_warning` has two meanings: the engine treats it as "soil dry → irrigate" (WATER_WARNING, CRITICAL), the health monitor raises it as SENSOR_FAULT ("cross-check the probe placement") with a push. | engine.py:631-646; sensors/tr301z.py:88-89; health_monitor.py:396-397; gateway.py:52 |
| B-11 | L | API/MCP returns irrigator `config` including `local_key` verbatim (web masks it) — conflicts with "treat as credential" guidance. | schemas.py:96-120; web/routes/irrigators.py:143-151 |
| B-12 | L | MCP bearer check uses non-constant-time `!=`, while the shared auth path uses `hmac.compare_digest`. | app.py:115 vs auth.py:185 |
| B-13 | L | Web `check_all` `has_alerts` ignores maintenance and needs_water (API includes them). | web/routes/operations.py:86 vs routes/operations.py:200 |
| B-14 | L | Reason `interval_delta` is the nominal step even when the interval was clamped (humidity, light, trend), so the audit trail can claim a change that did not happen. | engine.py:784-805, 815-847, 898-921 vs 760-776 |
| B-15 | L | PATCH semantics cannot clear nullable fields: repo `update_*` skip `None` (vacation notes/email, cluster location, plant notes, prefs `default_cluster_id`); API vacation docstring promises "fields present are modified". | repository.py:963-967, 1048-1050, 1154-1156; routes/vacation.py:66-69, 91; web/routes/preferences.py:56-66 |
| B-16 | L | `sync_plants` with an unknown `cluster_id` silently returns `synced=0` (no 404). | routes/plants.py:236-240; web/routes/operations.py:130-133 |
| B-17 | L | CLI maps only `httpx.ConnectError`; a read timeout (30 s; keep-alive fallback blocks the request for the whole run) surfaces as a traceback instead of `Error: …` + exit 1. | client.py:83-89 |
| B-18 | L | Trend analysis drops genuine 0 °C temperatures (truthiness), unlike the moisture branch fixed for the same reason; `get_ideal_temp_range` ignores a 0 °C ideal min. | trends.py:41-42 (cf. comment 164-165 in the same file); plant_needs.py:10-11 |
| B-19 | L | TUI copy is wrong: "Blank care fields are filled from the plant DB" (API does not auto-fill; requires `/plants/sync`); irrigator config placeholder uses keys `ip`/`version` the gateway never reads (`device_ip`/`local_key`). | tui/screens/cluster.py:594; routes/plants.py:64-67; tui/resources.py:66; gateway.py:322, 277 |
| B-20 | L | `getdevicelog` fetched with `max_records=100` and no pagination → long gaps (outage, multi-DP reports) can be truncated silently. | gateway.py:190, 201-207; sync.py:73 |
| B-21 | L | Live-read persistence guard checks `"humidity"`, never a canonical key; an env-humidity-only reading is dropped. | sync.py:98 |
| B-22 | L | Cluster status hides a sensor's last reading if it is older than 24 h (`reading_age_seconds=None` instead of the real age). | services/cluster.py:44-46 |
| B-23 | L | `_leak_check_done` inspects only the newest 500 leak activity rows; older completed checks inside the 24 h horizon would be re-armed. | services/irrigation.py:241-243 |
| B-24 | L | Fallback (no sensor data) honours effective/global config only when a cluster config row exists; with no row, a global schedule is ignored (NO_DATA). | logic/fallback.py:52-62 |
| B-25 | L | Stale API docstring contradicts invariant 9 ("engine then falls back to the global default preferred hours"). Frozen `[MCP]` — note only. | routes/windows.py:47-50 |

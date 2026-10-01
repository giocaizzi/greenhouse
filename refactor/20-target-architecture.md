# 20 — Target architecture (synthesis of proposals A / B / C)

Author: Synthesizer, Phase 2. Inputs: `BRIEF.md`, `CLAUDE.md`, `REFACTOR_NOTES.md` (incl. "Golden-test policy"),
`00-{map,smells,contracts,baseline,tests}.md`, the three proposals, the Phase-1 contract tests and their goldens under
`tests/golden/`. Contested claims were checked against the code at `db38192` (evidence is cited inline and in §13).
The executable task list is `refactor/20-plan.md`.

## 0. Thesis

1. **Decompose in place.** Most smells here are long functions in modules that are already in the right place. So we
   extract private helpers *inside the same module*. That keeps logger names, monkeypatch seams and import paths stable
   for free (proposal A is the base).
2. **A few new modules, each removing a concrete smell.** These are `web/weekdays.py`, `tui/render.py` and
   `logic/rules.py`. We add **no packages, no facades and no shims**: no public name changes module. The only new
   public names are additions, which the superset goldens allow.
3. **Typed interfaces without changing anything at runtime:** `TypedDict` results, frozen-dataclass parameter objects,
   keyword-only parameters, `cast`-typed CLI wrappers and a single `Protocol` (`RainForecast`). Pydantic schemas,
   templates and serialized dicts stay untouched.
4. **Merge only byte-identical duplicates.** Drifted copies stay separate (they are listed in §7.3), and observed bugs
   stay unfixed.
5. **Risk order:** tooling → data/types → repository → services/scheduler → route/web glue → CLI → TUI → logic →
   engine → devices.

## 1. Frozen surfaces that drive every decision (recap)

| Constraint | Consequence in this design |
|---|---|
| OpenAPI / MCP / routes golden (strict) | Route function names, docstrings, `response_model`s, Pydantic classes, field order and router include order are all untouched. `create_app` keeps include order via an ordered tuple. |
| `tests/golden/web/routes.json` pins each web endpoint's `module.qualname` | No web route function moves, and none is renamed. `web/routes/analytics.py` is **not** split. New code goes in module-private helpers or new non-route modules. |
| `scheduler_jobs.json` / `orchestration/scheduler_registry*.json` pin `func_ref = greenhouse_server.scheduler:<job>` | All five core job functions stay in `scheduler.py` with the same names. |
| TUI `surface.json` (strict) walks **every** module under `greenhouse_cli.tui` | We add no new `DOMNode` subclass, no method matching `^(action_\|on_\|_on_\|watch_)`, no UPPERCASE class attribute, and no public UPPERCASE module-level assignment in any `tui` module (old or new). |
| Imports / loggers / constants goldens are **superset** | New names, constants and modules are allowed. Existing names must keep resolving with equal values. Code that logs today keeps logging under the same logger name, so it either stays in its module or logs through that module's logger. |
| Patched attribute paths (`tests/test_contract_imports.py::PATCHED_PATHS`) | These must keep resolving, **and must still reach production code**: `services.irrigation._time`, `repository.time`, `logic.engine.time`, `logic.engine.season_for`, `logic.engine.seasonal_light_factor` (resolve-only), `learning.issues.{seasonal_light_factor,effective_light_threshold}`, `scheduler.{logger,_resolve_check_cron_hours,_app}`, `services.irrigation.IrrigationService.check_cluster`, `services.leak.LeakDetectionService.check_after_irrigation`, `devices[.gateway].tinytuya`, `greenhouse_cli.tui.run`. Phase-1 tests also monkeypatch `greenhouse_server.scheduler.{scheduler,_app,shutdown_requested,wait_for_shutdown}` and `services.pump_watcher.PumpWatcherService`, and they call `services.irrigation.{_add_leak_check_job,_run_leak_check,rearm_leak_checks,schedule_pump_watcher}` by path. |
| Bugs B-1…B-25 + CLI bugs | Each stays as it is. Any helper that touches one keeps the buggy semantics verbatim (B-6, B-7, B-8, B-13, B-14, B-15, B-16, B-18, B-19, B-23 are called out where relevant). |

## 2. Target tree (only what changes)

Legend: `+` new module · `~` edited in place (private helpers; public signatures unchanged) · `=` deliberately untouched.

```
libs/greenhouse-core/greenhouse_core/
  constants.py              ~ + new names only (values/types copied verbatim from call sites, §9)
  utils.py                  ~ NIGHT_LUX_THRESHOLD and the month table are now *imported* from constants
                              (`_SEASONAL_LIGHT_FACTOR = SEASONAL_LIGHT_FACTOR_BY_MONTH`); all names stay here
  schemas.py                ~ one module function `_parse_json_config(v)` used by both `parse_config` validators
  repository.py             ~ private `_patch_fields` / `_patch_fields_hasattr_first` / `_delete_by_id` / `_page`
                              + additive `get_vacation_window(window_id)`; public methods unchanged
  logic/plant_needs.py      ~ + moisture_target_range(care) (wraps the 3 byte-identical call sites)
  logic/timing.py           ~ + active_quiet_window(effective, *, now_unix, tz_name) (moved verbatim from engine)
  logic/engine.py           ~ IrrigationLogic only: decide_for_cluster decomposed into gates/inputs/rules/finish;
                              RainForecast Protocol; keeps `import time`, `season_for`, `seasonal_light_factor`
  logic/rules.py            + pure decision mutators moved out of engine.py (soil/temp/humidity/light/water-needs/
                              trend/water-warning/critical-stress + one_reason_decision); no logging, no repo, no clock
  logic/{trends,stress,sensors,fallback}.py   ~ functional core + thin I/O shell, same public signatures
  learning/issues.py        ~ detect_issues / detect_conflicts split into per-check private helpers (same module)
  devices/**                ~ stale docstrings only (`dp_parsers` claims); no code change
  models.py, plant_db.py, sync.py, stats.py, database.py, auth.py   =

libs/greenhouse-server/greenhouse_server/
  app.py                    ~ create_app → _make_lifespan/_new_fastapi/_init_state/_init_background/
                              _include_api_routers/_mount_web/_mount_mcp; _OPENAPI_TAGS, _PROTECTED_API_ROUTERS
  config.py                 ~ pump-watcher Field defaults reference constants (identical values)
  scheduler.py              ~ _job_session(app, failure_message) context manager for the 5 core jobs;
                              _build_irrigation_service(repo, registry, cloud); literals → constants;
                              CHECK_ALL_JOB_ID declared before first use; `_app` stays a plain module global
  services/irrigation.py    ~ pipeline / temperature / monitor / check / check_all / watcher plumbing decomposed
                              IN PLACE; PipelineResult, MonitorResult, CheckResult TypedDicts; _Actuation dataclass
  services/cluster.py       ~ + PlantNotFoundError, ClusterService.sync_plants(...), cluster_events_csv(...)
  services/{health,forecast,maintenance,data_quality,charts,insights,pump_watcher,health_monitor}.py  ~ per-check helpers
  routes/{plants,operations,vacation,charts}.py   ~ bodies delegate to the shared helpers (names/docstrings untouched)
  routes/*.py, web/routes/*.py   ~ exact-form "Cluster not found" blocks → deps.require_cluster (optional, low value)
  web/weekdays.py           + WEEKDAY_BITS, WEEKDAY_LABELS, format_weekday_mask (3 identical copies today)
  web/routes/{clusters,configs,windows,operations,analytics,plants,plant_dashboard}.py   ~ use shared helpers
  deps.py, auth.py, web/context.py, web/filters.py, web/exception_handlers.py   =

libs/greenhouse-cli/greenhouse_cli/
  client.py                 ~ JSONObject alias; _object/_array typed wrappers over the unchanged _request;
                              module function _drop_none(fields); docstrings on undocumented methods
  commands/_helpers.py      ~ + resolve_server_url(ctx) (3 copies today)
  commands/{auth,tui}.py    ~ use resolve_server_url
  tui/render.py             + pure payload → rows / Rich Text builders (imports rich + tui.formatting only)
  tui/screens/cluster.py    ~ builders moved to render.py; CRUD if/elif chains → per-tab private methods + dict dispatch
  tui/screens/{system,settings}.py   ~ load() feeds render.py builders
  tui/model.py              ~ summarize() gets 2 private helpers
  tui/widgets.py            ~ MetricChart private axis/event helpers (in-class duplicate removed)
```

### 2.1 Responsibilities of new or changed modules

| Module | One responsibility |
|---|---|
| `logic/rules.py` (new) | Pure `IrrigationDecision` mutators and terminal checks, in the frozen rule vocabulary. No repository, no clock, no logging. Imports only constants, `logic.decision`, `logic.plant_needs` and `utils.seasonal_light_factor`. |
| `logic/engine.py` | Orchestrates one evaluation: reads inputs, runs gates and rules in the frozen order, persists. Holds the repo-reading rules (`_enforce_leak_hold`, `_enforce_cooldown`, `_apply_window_rule`, `_apply_seasonal_multiplier`, `_apply_vacation_budget`, `_apply_weather_skip_rule`). CLAUDE.md invariants 9 and 11 cite these names. |
| `logic/timing.py` (+1 fn) | Local-time gating. `active_quiet_window` is exactly that concern, and the engine and web `cluster_detail` share it. |
| `logic/plant_needs.py` (+1 fn) | Interprets plant care data. `moisture_target_range` belongs here. |
| `web/weekdays.py` (new) | Weekday bitmask vocabulary and label formatting for web templates (web-only presentation). |
| `services/cluster.py` (+3) | Cluster status/history plus the per-cluster plant-DB sync and events CSV, which the API and web share. |
| `tui/render.py` (new) | Turns API payload dicts into `refill()`-ready rows `(key, cells)` and Rich `Text`. No widgets, no I/O, no Textual import. |
| `commands/_helpers.py` (+1) | Shared CLI helpers (server-URL resolution). |

### 2.2 Re-export shims

**None.** No public name changes module. Two deliberate "keep-alive" imports:
- `logic/engine.py` keeps `from greenhouse_core.utils import seasonal_light_factor` even after the light rule moves to
  `rules.py`, because `test_contract_imports.py` pins `greenhouse_core.logic.engine.seasonal_light_factor`. Mark it
  `# noqa: F401 — test-pinned attribute path`.
- `services/forecast.py` keeps `_WEATHER_PRECIP_THRESHOLD_MM` as an alias of the new constant.

Private web helper names (`_WEEKDAY_BITS`, `_WEEKDAY_LABELS`, `_format_weekday_mask`, `_FULL_MASK`,
`_FULL_WEEKDAY_MASK`) are not referenced by tests (grep), so they are **replaced** by imports from `web/weekdays.py`
and get no alias.

## 3. Method-level decomposition of the worst offenders

Rules for every row:
- **Order.** Every repository, plant-DB, weather, device, notifier and clock call happens in the original order.
- **Assignment order.** Every assignment to a `validate_assignment` Pydantic field (`IrrigationDecision`, `Trends`,
  `StressIndicators`) happens in the original order, and every `add_reason` too.
- **Dict keys.** Every result dict keeps its key insertion order.
- **Short-circuits.** These stay exactly as they are (`or` may replace two sequential `if … return` only when both
  sides are calls with no side effects before the test, as noted).
- **Naming.** New helpers are `_private` unless stated otherwise.

### 3.1 `IrrigationService.run_irrigation_pipeline` (services/irrigation.py:422-608, 187 lines, CC 21)

The public signature and return shape stay as they are. The return annotation becomes `PipelineResult`, which is still
a plain `dict` at runtime: `IrrigateResponse(**result)` and `partials/_decision_panel.html` consume it unchanged.

```python
class PipelineResult(TypedDict, total=False):            # module level
    action: Required[str]; reason: Required[str]; confidence: Required[float]
    duration_minutes: int; interval_hours: int; stress_indicators: dict[str, Any]
    reasons: list[dict[str, Any]]; temperature: float; temperature_source: str; blocking_alarms: list[str]

@dataclass(frozen=True, slots=True)
class _Actuation:                                          # parameter object for the actuation half
    cluster_id: int; irrigator: Irrigator; adapter: AbstractIrrigatorAdapter
    decision: IrrigationDecision; temp: float; source: str; sensor_data: Mapping[str, Any] | None
```

| Helper | Signature | Content (verbatim order) |
|---|---|---|
| `_error_result` (module) | `(reason: str) -> PipelineResult` | `{"action": "error", "reason": reason, "confidence": 0}`, for the two early exits. `routes/operations.py` string-matches `"cluster not found"`. |
| `_decision_result` (module) | `(decision: IrrigationDecision, temp: float, source: str) -> PipelineResult` | The 9-key dict of L455-465, with the same key order. |
| `IrrigationService._decide` | `(self, cluster_id: int, temp: float, *, force: bool) -> IrrigationDecision \| None` | Builds `IrrigationLogic(self._repo, self._plant_db, weather_client=self._weather)` and calls `.decide_for_cluster(cluster_id, current_temp=temp, persist=True, triggered_by="manual" if force else "auto", bypass_quiet_hours=force)`. |
| `IrrigationService._log_decision_skip` | `(self, cluster_id: int, decision: IrrigationDecision) -> None` | The info `decision_skip` activity row of L468-476 (this call has no `payload` kwarg). |
| `IrrigationService._actuation_target` | `(self, cluster_id: int) -> tuple[Irrigator, AbstractIrrigatorAdapter] \| str` | **Query.** Checks in order: irrigator, then registry, then adapter. Returns the exact error reason (`"no irrigators found"` / `"no device registry"` / `f"no adapter for irrigator: {exc}"`). |
| `_with_error` (module) | `(result: PipelineResult, reason: str) -> PipelineResult` | Sets `result["action"]="error"; result["reason"]=reason` in place and returns the same object (key positions unchanged). |
| `IrrigationService._blocking_alarms` | `(self, irrigator: Irrigator) -> list[HealthAlarm]` | `[]` when there is no monitor or it is not blocked, else the alarms (L502-503). |
| `IrrigationService._apply_health_block` | `(self, cluster_id: int, irrigator: Irrigator, decision: IrrigationDecision, alarms: list[HealthAlarm], result: PipelineResult) -> PipelineResult` | L505-528 in order: CRITICAL reason, then `decision.action = SKIP`, then the warning activity row **with** payload (message = `decision.reason_text`, read *after* the reason), then `result` keys `action`, `reason`, `reasons`, `blocking_alarms`. Bug B-6 (no re-persist) is kept. The misleading comment at L497-501 is rewritten to say so (comment only). |
| `IrrigationService._actuate` | `(self, act: _Actuation, result: PipelineResult) -> PipelineResult` | `adapter.start(irrigator, duration)` → `started_at = int(_time.time())` (**after** start; `_time` seam) → `add_irrigation_event(... notes=_event_notes(act))` → `_on_started` then `result["action"]="irrigated"` then `_notify_auto_irrigation`, **or** `_on_start_failed` then `result` `action`/`reason`. |
| `_event_notes` (module) | `(act: _Actuation) -> str` | The soil note and notes f-string of L536-553, verbatim. |
| `IrrigationService._on_started` | `(self, act: _Actuation, started_at: int) -> None` | `irrigated` activity → `set_decision_actuated` (if a log id is present) → `_schedule_leak_check` → `schedule_pump_watcher`. |
| `IrrigationService._notify_auto_irrigation` | `(self, act: _Actuation) -> None` | `maybe_notify(self._notifier, self._repo.get_preferences(), "auto", lambda: …)`. `get_preferences()` is still evaluated as an argument, because it may insert the row. |
| `IrrigationService._on_start_failed` | `(self, act: _Actuation, output: str) -> None` | `actuation_failed` activity, then `raise_alert(...)`. |

Resulting body (≈25 lines):
```python
cluster = self._repo.get_cluster(cluster_id)
if not cluster:
    return _error_result("cluster not found")
temp, source, sensor_data = self._resolve_temperature(cluster_id, cluster.environment == "indoor", temp_override, no_sync)
decision = self._decide(cluster_id, temp, force=force)
if not decision:
    return _error_result("no data for decision")
result = _decision_result(decision, temp, source)
if dry_run or decision.action.value == "skip":
    if not dry_run:
        self._log_decision_skip(cluster_id, decision)
    return result
target = self._actuation_target(cluster_id)
if isinstance(target, str):
    return _with_error(result, target)
irrigator, adapter = target
alarms = self._blocking_alarms(irrigator)
if alarms:
    return self._apply_health_block(cluster_id, irrigator, decision, alarms, result)
return self._actuate(_Actuation(cluster_id, irrigator, adapter, decision, temp, source, sensor_data), result)
```

`_resolve_temperature` (CC 12) is split into
`_indoor_temperature(self, sensor_data) -> tuple[float, str] | None` and
`_outdoor_temperature(self, sensor_data) -> tuple[float, str] | None`:
- `ensure_fresh_and_read` runs first;
- `self._weather.get_current()` is called on exactly the same paths;
- the final fallback is `FALLBACK_TEMPERATURE_C` with the **literal** label `"fallback (20C)"`.

**Sibling methods (same module):**
- **`monitor_cluster`:**
  - `_latest_soil(readings: list[SensorReading]) -> float | None`;
  - `_monitor_target_band(care: Mapping[str, Any]) -> tuple[float, float]`. This keeps its own strict parse: the 2-tuple
    unpack of `float(x) for x in raw.split("-")`, with `except Exception` → `(45.0, 65.0)`. It is **not**
    `moisture_target_range`, because 3-part strings diverge;
  - `_soil_status(latest: float | None, t_min: float, t_max: float) -> str`, a pure 5-way ladder using
    `MONITOR_VERY_DRY_MARGIN` / `MONITOR_WET_MARGIN`.

  `MonitorResult` is a TypedDict. The loop and the dict literal stay in the method.
- **`check_cluster`:**
  `_check_result(cluster_id: int, cluster_name: str, action: str, *, detail_key: Literal["notes", "needs_water"], detail: object, alerts: list[dict[str, Any]], maintenance: list[dict[str, Any]]) -> CheckResult`
  builds `cluster_id, cluster_name, action, <detail_key>, alerts, maintenance` in that order. The call order stays:
  learning alerts, then maintenance, then effective config, then pipeline or monitor, then `sync_cluster_alerts`, then
  the builder. The learner running 3× is not changed.
- **`check_all_clusters`:**
  - `_resolve_stale_check_alert(self, cluster_id: int) -> None`;
  - `_record_check_failure(self, cluster_id: int, cluster_name: str, exc: Exception) -> CheckResult`. Call it **from
    inside** the `except` block so `logger.exception` still sees `sys.exc_info()`. The order is rollback, then log,
    then upsert, then commit.

  `self.check_cluster(...)` is still called through `self` (tests patch the class attribute).
- **`handle_watcher_interrupted`:**
  - `_stop_auto_cycle(repo, registry, irrigator) -> tuple[bool, str]` (the same two log calls and the stop event row);
  - `_left_running_message(irrigator: Irrigator, triggered_by: str) -> str` (logs its warning).

  The log text stays byte-identical, and the activity write stays in the public function.
- **`schedule_pump_watcher`:** its **closure is kept**. It does a lazy `from greenhouse_server.scheduler import _app, scheduler, shutdown_requested, wait_for_shutdown`
  at call time, captures app/registry at *schedule* time, and resolves `PumpWatcherService` through
  `services.pump_watcher` (patched by tests). Only `_watcher_tuning(settings: Settings | None) -> tuple[float, float, int]`
  is extracted (fallbacks → `PUMP_WATCHER_*` constants).
- **`_run_leak_check`, `_add_leak_check_job`, `_schedule_leak_check`, `rearm_leak_checks`:** unchanged. In
  `_leak_check_done`, `limit=500` → `LEAK_CHECK_ACTIVITY_SCAN_LIMIT` (bug B-23 preserved).

### 3.2 `IrrigationLogic.decide_for_cluster` (logic/engine.py:106-266, CC 18) — high risk, two reviewers

The signature, `__all__`, every Reason code/message/severity and their order stay the same. The 22-step table in
00-smells §A1 is the spec.

```python
class RainForecast(Protocol):                        # engine.py, consumer side; 3 structural impls exist
    def get_forecast(self, hours: int = 6) -> Mapping[str, Any] | None: ...

@dataclass(frozen=True, slots=True)
class _EngineInputs:                                  # step-7 outputs, parameter object
    snapshot: SensorSnapshot; trends: Trends; stress: StressIndicators
    plant_care: list[dict[str, Any]]
    temp_range: tuple[float, float] | None; humidity_range: tuple[float, float] | None
    water_needs: str
```

| Member | Signature | Covers / order guarantee |
|---|---|---|
| `__init__` | `(self, db: IrrigationRepository, plant_db: PlantDatabase, *, weather_client: RainForecast \| None = None)` | Annotation only. The name and default stay. |
| `_record` | `(self, decision: IrrigationDecision, *, persist: bool, triggered_by: str) -> IrrigationDecision` | Replaces the four `if persist: self._persist(...); return x` blocks. It never adds the override reason. |
| `_pre_gates` | `(self, cluster_id: int, evaluated_at: int, plants: list[Plant]) -> IrrigationDecision \| None` | Steps 2-4, first non-None wins: `not plants` → NO_PLANTS decision (confidence `0.0`), then `_enforce_leak_hold`, then `_enforce_cooldown`. Later gates are **not** evaluated once one fires. |
| `_quiet_hours_skip` (module) | `(cluster_id: int, evaluated_at: int, window: tuple[int, int]) -> IrrigationDecision` | The literal decision of L168-178. |
| `_resolve_quiet_window` | `(self, cluster_id: int, evaluated_at: int) -> tuple[int, int] \| None` | `effective = self.db.get_effective_config(cluster_id)`, then `return active_quiet_window(effective, now_unix=evaluated_at, tz_name=self._tz_name())`. Config is read before prefs, exactly as today. |
| `_tz_name` | `(self) -> str \| None` | `prefs = self.db.get_preferences(); return prefs.timezone if prefs else None`. It is called at the same 3 points (it is **not cached**, because `get_preferences` may insert the row). |
| `_finish` | `(self, decision: IrrigationDecision, *, override_window: tuple[int, int] \| None, persist: bool, triggered_by: str) -> IrrigationDecision` | The former `_finalize` closure: appends `MANUAL_OVERRIDE_QUIET_HOURS` iff `override_window is not None`, then `_record`. |
| `_evaluate_rules` | `(self, cluster: Cluster, cluster_id: int, plants: list[Plant], evaluated_at: int, current_temp: float \| None) -> IrrigationDecision` | Steps 6-20 (body below). `cluster_id` is passed explicitly, never re-derived from `cluster.id`. |
| `_gather_inputs` | `(self, cluster_id: int, plants: list[Plant]) -> _EngineInputs` | Order: `get_recent_sensor_data(self.db, cluster_id, hours=SNAPSHOT_LOOKBACK_HOURS)`, then `analyze_historical_trends`, then `detect_stress_conditions`, then `_attach_learning_alerts`, then care data, then temp range, then humidity range, then water needs. |
| `_fallback_decision` | `(self, cluster_id: int, evaluated_at: int, current_temp: float \| None, inputs: _EngineInputs) -> IrrigationDecision` | `temperature_based_decision(...)`. `get_irrigation_config` is still evaluated as an argument, so it runs only on this path. |
| `_base_decision` (module) | `(cluster_id: int, evaluated_at: int, inputs: _EngineInputs) -> IrrigationDecision` | SKIP, `DEFAULT_*`, `confidence=CONFIDENCE_BASELINE`, snapshot/stress/trends. |
| `_apply_adjustments` | `(self, cluster: Cluster, decision: IrrigationDecision, inputs: _EngineInputs, evaluated_at: int) -> None` | Order: soil, temperature, humidity, light, water-needs, trend, `self._apply_seasonal_multiplier`, `self._apply_vacation_budget`. |
| `_apply_window_rule` | `(self, cluster_id: int, evaluated_at: int) -> IrrigationDecision \| None` | Drops the unused `cluster` and `decision` params. The name is kept. |

```python
# decide_for_cluster
cluster = self.db.get_cluster(cluster_id)
if not cluster:
    return None
evaluated_at = int(time.time())                                   # engine_mod.time seam
plants = self.db.get_plants_in_cluster(cluster_id)
gate = self._pre_gates(cluster_id, evaluated_at, plants)
if gate is not None:
    return self._record(gate, persist=persist, triggered_by=triggered_by)
quiet_window = self._resolve_quiet_window(cluster_id, evaluated_at)
if quiet_window is not None and not bypass_quiet_hours:
    return self._record(_quiet_hours_skip(cluster_id, evaluated_at, quiet_window), persist=persist, triggered_by=triggered_by)
decision = self._evaluate_rules(cluster, cluster_id, plants, evaluated_at, current_temp)
override = quiet_window if bypass_quiet_hours else None
return self._finish(decision, override_window=override, persist=persist, triggered_by=triggered_by)

# _evaluate_rules
weather_skip = self._apply_weather_skip_rule(cluster, cluster_id, evaluated_at)
if weather_skip is not None:
    return weather_skip
inputs = self._gather_inputs(cluster_id, plants)
sensors = self.db.get_sensors_in_cluster(cluster_id)              # still AFTER plant care
if not sensors or not inputs.snapshot.has_data:
    return self._fallback_decision(cluster_id, evaluated_at, current_temp, inputs)
decision = _base_decision(cluster_id, evaluated_at, inputs)
if apply_water_warning_rule(decision) or apply_critical_stress_rule(decision):   # == two sequential ifs
    return decision
window_skip = self._apply_window_rule(cluster_id, evaluated_at)
if window_skip is not None:
    return window_skip
self._apply_adjustments(cluster, decision, inputs, evaluated_at)
return decision
```

These consequences are preserved and must stay pinned (decision grid):
- terminal steps 6/8/10/11/12 bypass vacation rationing;
- the fallback bypasses windows;
- OUTSIDE_WINDOW returns a fresh decision without a snapshot;
- stress keys on the average;
- the light rule calls `seasonal_light_factor()` **only** when `avg_light is not None`. The light rule is not made pure
  over a `light_factor` param.

The lazy `IrrigationLearner` import in `_attach_learning_alerts` stays lazy (it guards against an import cycle).

**Other engine functions:**
- **`_apply_vacation_budget`:**
  - pure `_vacation_days_left(vac: VacationWindow, now: int) -> int`;
  - pure `_binding_max_minutes(*, reservoir_l: float, flow_rate_l_per_min: float, starts_at: int, ends_at: int, now: int, spent_l: float) -> int`.

  Guard order is kept: `get_active_vacation`, then VACATION_ACTIVE reason, then `get_irrigator_for_cluster`, then
  capacity guard, then IRRIGATE guard, then `irrigator_consumption_liters` (only after the guards), then trim/exhaust.
- **`_apply_seasonal_multiplier`:** stays a method, so `season_for` is still looked up as an engine module global. Add a
  pure `_seasonal_overrides(plant_care, season_key) -> tuple[Any, Any]` and keep the dead `isinstance` guard.
- **`_decision_with_reason`:** becomes `rules.one_reason_decision`. It drops the three params that callers never pass
  (`sensor_snapshot`, `stress_indicators`, `trends`); the body still builds `StressIndicators()` / `Trends()` and
  `sensor_snapshot=None`. All 6 call sites are in `engine.py` (grep).

### 3.3 `rules.apply_soil_moisture_rule` (was `engine._apply_soil_moisture_rule`, L678-752, CC 21)

```python
def apply_soil_moisture_rule(decision: IrrigationDecision, plant_care: list[dict[str, Any]]) -> None:
    snapshot = decision.sensor_snapshot
    if snapshot is None or snapshot.avg_soil_moisture is None:
        return
    band = _cluster_target_band(plant_care)
    soil = _soil_extremes(snapshot)
    if _is_conflict(soil, band):
        _apply_conflict(decision, snapshot, soil, band)
        return
    _apply_soil_level(decision, snapshot.avg_soil_moisture, soil, band)
```

| Helper | Signature | Content |
|---|---|---|
| `_cluster_target_band` | `(plant_care: list[dict[str, Any]]) -> tuple[float, float]` | `min` of `moisture_target_range(d)[0]` and `max` of `[1]` over all care dicts. An empty list raises exactly as today (unreachable). |
| `_soil_extremes` | `(snapshot: SensorSnapshot) -> tuple[float, float]` | min/max, each falling back to avg. |
| `_is_conflict` | `(soil: tuple[float, float], band: tuple[float, float]) -> bool` | `soil[0] < band[0] and soil[1] > band[1] - CONFLICT_WET_MARGIN`. |
| `_sensor_names` | `(snapshot: SensorSnapshot, keep: Callable[[float], bool]) -> list[str]` | Names of sensors whose avg is not None and passes `keep`, in per-sensor order. |
| `_apply_conflict` | `(decision, snapshot, soil, band) -> None` | The CONFLICT branch. The message f-string is verbatim, including the `or '?'` fallback. |
| `_apply_soil_level` | `(decision: IrrigationDecision, avg_soil: float, soil: tuple[float, float], band: tuple[float, float]) -> None` | The VERY_DRY → DRY → ADEQUATE → WET ladder, with its f-strings verbatim. VERY_DRY and DRY test the **driest** value (`soil[0]`); **ADEQUATE tests `avg_soil <= target_max`**; the messages use driest/wettest. |

The field-assignment sequence in each branch stays verbatim (action → duration → interval → confidence, skipping fields
the branch never set). We add **no** generic `set_dosage` helper: two of the branches skip different fields, and a
shared helper would have to prove equivalence for every branch.

The temperature, humidity, light, water-needs and trend adjustments move to `rules.py` **unchanged**. These are flat
ladders, and bug B-14's nominal `interval_delta` must not be "fixed" by a shared clamp.

### 3.4 `learning/issues.detect_conflicts` (L154-304, CC 36) and `detect_issues` (L21-151, CC 24)

All helpers stay in `issues.py`, because `seasonal_light_factor` and `effective_light_threshold` are module-global
patch targets. Public signatures are frozen.

```python
# detect_conflicts(db, plant_db, cluster_id, profiles, plant_care)
sensors = db.get_sensors_in_cluster(cluster_id)
moisture = _latest_moisture_by_sensor(db, sensors)
if len(moisture) < 2:
    return []                       # quirk preserved: low-light / low-humidity checks are skipped too
alerts = _overwater_conflict_alerts(sensors, moisture, profiles, plant_care)
plants_by_id = {p.id: p for p in db.get_plants_in_cluster(cluster_id)}
alerts.extend(_low_light_alerts(db, plant_db, sensors, plants_by_id))
alerts.extend(_low_env_humidity_alerts(db, plant_db, sensors, plants_by_id))
return alerts
```

| Helper | Signature | Note |
|---|---|---|
| `_latest_moisture_by_sensor` | `(db: IrrigationRepository, sensors: list[Sensor]) -> dict[int, float]` | Mean of the newest `LEARNING_LATEST_SAMPLES` cleaned DESC values over `LEARNING_CONFLICT_LOOKBACK_HOURS`. |
| `_conflict_band` | `(plant_care: Mapping[int \| None, Mapping[str, Any]], plant_id: int \| None) -> tuple[float, float]` | L188-195 verbatim. Catches **only** `(ValueError, IndexError)`, default `(45.0, 65.0)`. Not `parse_moisture_target`. |
| `_split_dry_wet` | `(sensors, moisture, plant_care) -> tuple[list[_SensorReading], list[_SensorReading]]` | `_SensorReading = tuple[Sensor, float, float]`; `LEARNING_CONFLICT_DRY_MARGIN`. |
| `_overwater_conflict_alerts` | `(sensors, moisture, profiles, plant_care) -> list[Alert]` | Nested dry×wet loop with the same `continue` guards, message and data dict. |
| `_low_light_alerts` / `_low_env_humidity_alerts` | `(db, plant_db, sensors: list[Sensor], plants_by_id: dict[int, Plant]) -> list[Alert]` | They stay two separate passes, so the DB call order is unchanged (all 168 h reads, then all 48 h reads). |

`detect_issues` gets:
- `_learned_profiles(db, sensors) -> dict[int, PlantProfile]`;
- per sensor: `_blocked_drip_alert(sensor, profile) -> Alert | None`;
- `_drainage_alert(db, sensor, profile) -> Alert | None`, which calls the module-global `seasonal_light_factor()`;
- `_chronic_underwatering_alert(db, sensor, profile, care) -> Alert | None`, which keeps its own
  `float(target.split("-")[0])` parse.

Per-sensor append order stays blocked → drainage → chronic. Conflicts are appended last when `len(profiles) >= 2`.

### 3.5 Other core logic

| Function | Decomposition |
|---|---|
| `trends.analyze_historical_trends` (CC 29) | **Shell:** `sensors` → `_pooled_clean_readings(db, sensors, hours=TREND_LOOKBACK_HOURS) -> list[SensorReading]` (clean **per sensor** before pooling; an empty sensor list yields `[]`) → `if len(readings) >= TREND_MIN_READINGS: _apply_reading_trends(trends, readings)` → irrigator → `_apply_cadence_flags(trends, events)`. **Pure:** `_moisture_trend(first, second) -> tuple[float, str] \| None` (`is not None` filter; the shell assigns `soil_moisture_delta` **before** `soil_moisture_trend`) and `_temperature_trend(first, second) -> str \| None`, which **keeps the truthiness filter `if r.temperature`** (B-18, written as its own comprehension). `CADENCE_WINDOW_DAYS` covers both `hours=7*24` and `/ 7`. We add no generic `_classify_delta`: the two ladders test opposite signs first. |
| `stress.detect_stress_conditions` (CC 27) | **Shell:** plants → care (re-query kept) → detectors. **Pure detectors** `-> str \| None`: `_water_warning(snapshot)`, `_low_env_humidity(snapshot, care)`, `_low_light(snapshot, care)` (still calls `effective_light_threshold(min_lux)` **before** testing `min_lux > 0`), `_water_stress(snapshot, trends)` (builds the `" + declining"` suffix in one expression; same final value), `_heat_stress(snapshot, trends, care)`, `_over_watering(snapshot, trends)`. Assign `stress.<field>` **only when non-None**, in the original field order, so `model_fields_set` is unchanged. |
| `sensors.get_recent_sensor_data` (CC 19) | `_mean_or_none(values: Sequence[float]) -> float \| None` replaces 7 copies. `> 15` → `> NIGHT_LUX_THRESHOLD`. The loop stays as it is. |
| `fallback.temperature_based_decision` (CC 13) | `_config_fallback(db, cluster_id, base) -> IrrigationDecision`; `_temperature_interval(temp: float, water_needs: str) -> int` using the `FALLBACK_*_INTERVAL_STEP` constants. Bug B-24 is preserved. |

### 3.6 Server services

| Function | Decomposition |
|---|---|
| `health.PlantHealthService.compute_score` (CC 29) | `HealthScore` TypedDict (6 keys in order). `_empty_score() -> HealthScore` returns a fresh dict. `_HealthBands` is a frozen dataclass (`soil: tuple[float, float]`, `temp: tuple[float \| None, float \| None]`, `humidity: …`) with `@classmethod from_care(care)` (soil via `moisture_target_range`). `_pooled_clean_readings(self, sensors, days) -> list[SensorReading]`; `_in_band_pct(values: Sequence[float], lo: float, hi: float) -> float \| None`; `_band_percentages(readings, bands) -> tuple[float \| None, float \| None, float \| None]` (returns all-None early when `readings` is empty; temp/humidity only when both bounds are not None); `_first_profile_efficiency(self, sensors, days) -> float \| None` (first non-None, then `break`); `_composite_score(components: Sequence[float]) -> float \| None` (`float(max(0.0, min(100.0, round(mean))))`). Signature default `days: int = HEALTH_SCORE_WINDOW_DAYS`. |
| `forecast.ForecastService.predict_next_irrigation` (CC 25) | `now = int(time.time())` stays **before** the sensor loop. `_forecast_sensor(self, sensor, plant_map, learner) -> _SensorForecast \| None` (the profile lookup runs only when current moisture exists); `_no_data_forecast(cluster_id) -> ForecastResponse` (returned **before** any weather call); `_confidence_for(profiled_count: int) -> float`; `_rain_outlook(self, cluster) -> tuple[bool, str \| None, float \| None]`. The driver stays stable `sorted(...)[0]`. `weather_client: RainForecast \| None` (annotation only). |
| `maintenance.collect_maintenance_alerts` (CC 23) | `_battery_alert`, `_stale_alert(sensor, readings, now)`, `_humidity_alert(sensor, readings, plant, plant_db)`, `_light_alert(...)`, each `-> dict[str, Any] \| None`. Per-sensor append order is kept; `MAINTENANCE_*` constants. |
| `data_quality.build_report` (124 lines) | `_sensor_issues(repo, sensors, now) -> tuple[list[DataQualityIssue], set[int]]` (the per-sensor interleaving of `sensor_without_plant` and `stale_sensor` is kept), `_plant_issues`, `_cluster_issues`, `_duplicate_device_issues`, `_count_by_code`. The four passes keep their order. |
| `charts._threshold_for_cluster` (CC 20) | `_aggregate_band(mins: list[float], maxs: list[float]) -> dict[str, Any] \| None` for the identical temp/humidity blocks. Also `repo.session.get(Plant, id)` → `repo.get_plant(id)` (byte-identical body, 2 sites). |
| `insights.cluster_insights` (CC 14) | `_insight_from_alert(alert: Mapping[str, Any]) -> CareInsight` removes the in-file clone (both lists are collected before iteration, as today). |
| `pump_watcher.watch` (CC 13) | `_outcome(kind: str, *, polls: int, failures: int, alarm_raw: Any, elapsed: float) -> WatchOutcome` (TypedDict). The **caller computes `elapsed` at exactly the same point** (the injected clock is call-count sensitive). `completed` keeps `read_failures: 0`. `_handle_trip` is untouched. |
| `health_monitor.backfill_from_history` | `_raise_if_not_open(self, alarm, sensor) -> None` for the duplicated L252-274 block. `hours=24 * 7` → `SENSOR_HEALTH_BACKFILL_HOURS`. |
| `services/search.search`, `anomaly.scan`, `leak.*`, `efficacy.score_cluster`, `system_health.pulse`, `manual_control.*`, `bulk.*` | **Leave.** These are linear, actuation-adjacent, or deliberately explicit SQL. |

### 3.7 `create_app` (app.py:123-254, 132 lines)

```python
def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    if settings is None:                 # literal `is None` (not `or`)
        settings = Settings()
    if engine is None:
        engine = create_db_engine(settings.db_url)
    init_db(engine)
    app = _new_fastapi(_make_lifespan(settings))
    tz_name = _init_state(app, settings, engine)   # settings, session_factory, _init_tuya, tz + set_display_timezone, weather, ntfy, plant_db
    _init_background(app, settings, tz_name)       # init_scheduler → init_health_monitor → _restore_persisted_scheduler_pause
    bootstrap_admin(engine, settings)
    _include_api_routers(app)                      # auth unprotected → _PROTECTED_API_ROUTERS w/ Depends(require_user) → well_known
    _mount_web(app)                                # /static, web_router, register_web_exception_handlers
    _mount_mcp(app)                                # MUST stay last; sets app.state.mcp
    return app
```

- `_OPENAPI_TAGS: tuple[dict[str, str], ...]` holds the 14 tags in order and is passed as `list(_OPENAPI_TAGS)`.
- `_PROTECTED_API_ROUTERS: tuple[APIRouter, ...]` holds the 21 routers in today's include order (that order is the
  OpenAPI path order and the MCP tool order).
- The lifespan closure references the module globals `start_scheduler`, `rearm_leak_checks` and `stop_scheduler` at
  call time.
- `require_mcp_token` is untouched (B-12). `_restore_persisted_scheduler_pause`'s silent `except: pass` stays. Every
  current top-level import of `app.py` stays (imports golden).

### 3.8 Scheduler job bodies (scheduler.py:292-409)

```python
@contextmanager
def _job_session(app: FastAPI, failure_message: str) -> Iterator[IrrigationRepository]:
    session = app.state.session_factory()      # outside try: a None app / missing state escapes, as today
    try:
        yield IrrigationRepository(session)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception(failure_message)      # module-global logger (sched_mod.logger patch, caplog name)
    finally:
        session.close()
```

- **Where it applies.** Exactly the 5 core jobs, each with its text verbatim: "Sync job failed", "Plant health snapshot
  job failed", "Check job failed", "Anomaly scan job failed", "Device health monitor job failed". Each job writes
  `with _job_session(_app, "...") as repo:` and passes the **current `_app` global at call time**: never a default
  argument, never captured at import.
- **Guards.** Pre-session guards stay before the `with`, in their current order: `_get_cloud() is None` in `_sync_job`,
  and `_app is None` / `monitor is None` in `_health_monitor_job`. The inconsistent `_app` guarding is preserved.
  Statements that are inside today's `try` go inside the `with`; for example, `_check_job` reads
  `_app.state.weather_client` there, so an AttributeError is still logged.
- **`_check_job`.** It additionally uses
  `_build_irrigation_service(repo: IrrigationRepository, registry: DeviceRegistry | None, cloud: DeviceGateway | None) -> IrrigationService`,
  which also does the `monitor.bind_repo(repo)` step in its original position.
- **Imports.** Lazy service imports stay inside the job functions (they guard an import cycle). The redundant inner
  `IrrigationRepository` import in `_health_snapshot_job` is dropped.
- **Untouched.** `init_health_monitor` (nested try; it assigns `app.state.health_monitor` even on failure) and
  `_resolve_zoneinfo`.
- **Literals.** `minutes=15` → `ANOMALY_SCAN_INTERVAL_MINUTES`; `hour=0, minute=30` →
  `HEALTH_SNAPSHOT_HOUR/MINUTE`; `hours=6` → `SYNC_JOB_BACKFILL_HOURS`; the `"check_all"` literals (L72, L210) →
  `CHECK_ALL_JOB_ID`, whose declaration moves above `_TZ_BOUND_CRON_JOBS` (same value).

### 3.9 Repository copy-paste (repository.py:954-1309)

```python
def _patch_fields(self, row: Base, fields: Mapping[str, Any], *, json_fields: frozenset[str] = frozenset()) -> None:
    """None-first PATCH: skip None, JSON-encode dict values of `json_fields`, set only existing attributes."""
    for key, value in fields.items():
        if value is None:
            continue
        if key in json_fields and isinstance(value, dict):
            setattr(row, key, json.dumps(value))
        elif hasattr(row, key):
            setattr(row, key, value)

def _patch_fields_hasattr_first(self, row: Base, fields: Mapping[str, Any]) -> None:
    """hasattr-first PATCH (`hasattr(row, key) and value is not None`) — kept separate: hasattr runs even for None."""
    for key, value in fields.items():
        if hasattr(row, key) and value is not None:
            setattr(row, key, value)

def _delete_by_id(self, model: type[Base], row_id: int) -> bool: ...   # get → None→False → delete → flush → True

@staticmethod
def _page(stmt: Select[Any], id_column: InstrumentedAttribute[int], *, limit: int | None, after_id: int | None) -> Select[Any]: ...
```

- **`_patch_fields`:** `update_vacation_window`, `update_irrigation_window`, `update_irrigator`
  (`json_fields=frozenset({"config"})`), and `update_sensor` after its `plant_id` pop (the `reassign_sensor_to_plant`
  routing stays in the method).
- **`_patch_fields_hasattr_first`:** `update_preferences`, `update_cluster`, `update_plant`. Bug B-15 is preserved.
- **`_delete_by_id`:** the five delete methods (`delete_plant` keeps its assignment-closing body).
- **`_page`:** `list_all_sensors/irrigators/plants`. Caller-specific `where` clauses come first, so the SQL `WHERE`
  order is unchanged.
- **Not folded:** `set_irrigation_config` / `update_global_irrigation_config` (they allow `None`), and the
  `timestamp or int(time.time())` defaults (the `repo_mod.time` seam; `0` means now).
- **Additive:** `get_vacation_window(self, window_id: int) -> VacationWindow | None` = `self.session.get(VacationWindow, window_id)`.
  It replaces the bypass at `routes/vacation.py:84`. The 2 `session.get(Plant, …)` sites in `routes/charts.py:37` and
  `web/routes/plant_dashboard.py:29` switch to the existing `get_plant` (same body). Other `repo.session` bypasses
  (search, efficacy, health_monitor, charts scalars) stay.

### 3.10 `ClusterScreen` (tui/screens/cluster.py, 800 lines, MI 1.08) — see §8.

### 3.11 CLI client (client.py; 79 of the 176 default-mode mypy errors)

```python
JSONObject = dict[str, Any]

def _drop_none(fields: Mapping[str, Any]) -> JSONObject:          # module function, insertion order kept
    return {k: v for k, v in fields.items() if v is not None}

class IrrigationClient:
    def _object(self, method: str, path: str, **kwargs: Any) -> JSONObject:
        return cast(JSONObject, self._request(method, path, **kwargs))
    def _array(self, method: str, path: str, **kwargs: Any) -> list[JSONObject]:
        return cast(list[JSONObject], self._request(method, path, **kwargs))
```

- Each public method swaps `self._request(` for `_object`/`_array`, following its *current* `-> dict` / `-> list`
  annotation, and tightens that annotation.
- `_request` keeps its name and signature. `tests/cli/tui_fixtures.py` replaces it per instance; the wrappers look it
  up through `self`, so they still hit the replacement.
- `_drop_none` replaces the 9 `kwargs.items()` comprehensions and the `filters` one at L294. It is **not** used in the
  `irrigator add` (truthy) / `update` (`is not None`) config building.
- Public method signatures are unchanged (the TUI calls several positionally).
- Docstrings are added where missing; they never appear in `--help`.
- Bug B-17 and the CLI bugs in REFACTOR_NOTES are preserved.

`resolve_server_url(ctx: typer.Context) -> str` =
`ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", "http://localhost:8000")`. It is used by `get_client`,
`commands/auth._login_client` and `commands/tui`.

### 3.12 Remaining long functions — verdicts

| Function | Verdict |
|---|---|
| `routes/operations.cluster_status` (64 lines) | Module helpers `_status_sensor(s) -> ClusterStatusSensorResponse`, `_status_irrigator(i) -> ClusterStatusIrrigatorResponse \| None`, `_status_decision(d) -> IrrigateResponse \| None`. The route name/docstring/response_model are untouched. |
| `web/routes/clusters.cluster_detail` (96 lines) | `_rationale_reasons(repo, cluster_id) -> list[dict[str, Any]]`, `_window_rows(repo, cluster_id) -> list[dict[str, Any]]`. Later, the quiet flag goes via `active_quiet_window(...) is not None` (integration task, after the engine). Context keys are untouched. |
| `web/routes/plants.create_plant` / `update_plant` (jscpd clone) | `_plant_form_fields(*, species: str, category: str, water_needs: str, light_needs: str, ideal_temp_min: str, ideal_temp_max: str, ideal_humidity_min: str, ideal_humidity_max: str, notes: str) -> dict[str, Any]`, with keys in the current kwarg order. Then `repo.add_plant(cluster_id=cluster_id, **fields)` / `repo.update_plant(plant_id, **fields)`. |
| `web/routes/plant_dashboard.plant_dashboard`, `web/filters.cluster_caps`, `web/context.base_context`, `commands/operations.register`, `commands/auth.register`, `plant_db.get_care_data`, `profiling.get_plant_profile`, `cleaning.clean_readings`, `stats.*`, `repository.move_plant/upsert_alert/bulk_add_sensor_readings`, `pump_watcher._handle_trip`, devices (`gateway.get_live_reading/get_device_logs`, `ik10pw._start_keepalive/read_health`, `tr301z.read_health`, `tuya_generic.status`) | **Leave.** They are low payoff, frozen `--help` text, carriers of invariants 9/10, or actuation/device paths (B-2/B-3 live there). Devices get stale-docstring fixes only. |

## 4. Ports / Protocols that survive

| Port | Verdict | Evidence |
|---|---|---|
| `RainForecast` (`logic/engine.py`) | **KEEP** (typing only, not `runtime_checkable`) | Core cannot name the server's `WeatherClient` (layering), so `IrrigationLogic(weather_client=None)` and `ForecastService(weather_client=None)` are untyped today. **Three** structural implementations exist: `services/weather.WeatherClient`, `tests/golden.py::OfflineWeather`, `tests/engine_grid.py::FakeForecastWeather`. `ForecastService` imports it from the engine module (server → core is allowed). |
| `EngineRepository` / `ReadingsSource` / `LearningRepository` … (B §2.1) | REJECT | One implementation. The stated goal (an enforceable "domain doesn't import persistence" contract) would also need the logic modules' runtime `repository` imports moved under `TYPE_CHECKING`, which shifts import order inside `greenhouse_core/__init__` for zero runtime benefit. The 16-method dependency surface is documented in the decision log instead. |
| `WeatherSource` (B §2.3) | REJECT | `IrrigationService` already annotates the concrete `WeatherClient`; tests pass fakes duck-typed and are not type-checked. No present smell. |
| `Clock` | REJECT (B agrees) | A coherent fake clock would need threading through the repository (≈50 construction sites); time-machine already freezes all clocks. |
| `JobScheduler`, `PostStartHooks`, `ActuationGate`, `Notifier`, `PlantCareSource`, `GreenhouseApi`, service-level repo Protocols | REJECT | One implementation each, and no test seam benefit. |
| Existing device ports (`AbstractIrrigatorAdapter`, sensor base, `DeviceRegistry`) | Kept as is | `FakeDeviceWiring` is the second implementation. |

## 5. Repository / schemas split — decision: **no split**

- `repository.py` stays one module. The real cost is copy-paste, which §3.9 removes.
- A mixin split, whether a private `_repo/` package (B) or a `repository/` package facade (C), would need:
  - exception `__module__` workarounds (B pins `__module__` back; C relies on partial-package-init imports);
  - an MRO disjointness guard;
  - self-typed cross-mixin calls;
  - and every commit would run the 72–91-file test map.

  That is churn on the most-imported module for navigation value only. If the owner later wants it, B's layout
  (`repository.py` facade module + private `_repo/<aggregate>.py` mixins) is the preferred shape. It is **not**
  scheduled.
- `schemas.py` stays one module (00-smells A3: cohesive DTO module; OpenAPI risk). The only change is the shared
  `_parse_json_config(v: object) -> object` body. Both classes keep a `parse_config` method with identical decorators,
  so validator metadata and the OpenAPI document are unchanged.

## 6. `scheduler._app` — decision: **keep the module global; no `runtime.py`**

B's `runtime.py` (`bind_app/current_app`, scheduler object moved, `_app` served by `__getattr__`) is **refuted by the
Phase-1 safety net**:
- `tests/server/test_contract_pump_watcher.py:101-104` does `monkeypatch.setattr(scheduler_module, …)` on
  `"scheduler"`, `"_app"`, `"shutdown_requested"` and `"wait_for_shutdown"`.
- `tests/server/test_contract_scheduler.py:404,455` does `monkeypatch.setattr(sched, "_app", None)`.

These tests expect `services.irrigation`'s call-time `from greenhouse_server.scheduler import …` and the job bodies'
global reads to observe the patched module attributes. A `runtime.current_app()` reader would ignore them. Also,
`PATCHED_PATHS` pins `greenhouse_server.scheduler._app` as a plain attribute.

So:
1. `_app` stays a rebinding module global in `scheduler.py`.
2. Job bodies read it at call time and pass it to `_job_session(_app, …)`.
3. `services/irrigation.py` keeps its function-level lazy imports from `greenhouse_server.scheduler` (no hoisting).
4. `schedule_pump_watcher` keeps capturing app/registry at schedule time.
5. The `scheduler ⇢ services.irrigation` lazy cycle is accepted and pinned by an import-linter `ignore_imports` entry.

## 7. API / web shared rules

### 7.1 Rules
1. **Where shared code lives.** Code shared by `routes/` and `web/` goes in `services/*` (application) or
   `greenhouse_core.logic.*` (pure). `routes` and `web` never import each other (import-linter independence).
   Web-only presentation vocabulary lives in `web/` (e.g. `web/weekdays.py`).
2. **What merges.** Only logic that is byte-identical in behavior *and* side effects. Each layer keeps its own I/O
   mapping: status code, detail string and punctuation, template, context keys, form parsing (`int(x) if x.strip()`),
   truthiness of ids (`if request.plant_id` / `if pid`; id 0 = absent), and **commit placement**. Transactions stay in
   the routes; there is no unit-of-work change.
3. **Errors.** Shared helpers raise domain exceptions (e.g. `PlantNotFoundError(LookupError)`), never `HTTPException`.
   Each route translates with its own exact detail string.
4. **Frozen route parts.** Route function name, docstring, `response_model`, params and decorators are never edited.
   Extracted helpers are module-private (`_name`) or live in a service.
5. **404 lookups.** Only the exact form `x = repo.get_cluster(id); if not x: raise HTTPException(404, "Cluster not found")`
   folds into the existing `deps.require_cluster`. Sites where `None` comes from another call stay.

### 7.2 Merged (byte-identical, verified)

| Duplicate | Shared home | Per-layer residue |
|---|---|---|
| Plant-DB sync (`routes/plants.py:219-248` ≡ `web/routes/operations.py:114-141`) | `ClusterService.sync_plants(self, *, plant_id: int \| None, cluster_id: int \| None) -> tuple[int, list[str]]`, which raises `PlantNotFoundError(plant_id)`. The O(n) cluster scan is **moved verbatim**, not replaced by `get_plant`: SQLite FK enforcement is off, so an orphan plant would be found by `get_plant` but not by the scan. `self._repo` is the same request-scoped repo the route holds (FastAPI dependency cache). | API: `except PlantNotFoundError: raise HTTPException(status_code=404, detail=f"Plant {request.plant_id} not found")` and `SyncPlantsResponse`. Web: `raise HTTPException(404, f"Plant {pid} not found")` and the `_sync_result.html` context. Both keep `repo.session.commit()`. Bug B-16 (unknown cluster → 0, no 404) is kept. |
| Events CSV (`routes/operations.py:355-389` ≡ `web/routes/analytics.py:79-107`) | `cluster_events_csv(repo: IrrigationRepository, cluster_id: int, *, days: int) -> str` in `services/cluster.py` | Each route keeps `require_cluster` and builds its own `StreamingResponse` (identical headers). The dead `stats.export_csv` is untouched. |
| Weekday vocabulary (`web/routes/clusters.py:92-100` ≡ `configs.py:13-22` ≡ `windows.py:20,76`) | `web/weekdays.py`: `WEEKDAY_BITS`, `WEEKDAY_LABELS`, `format_weekday_mask(mask: int) -> str` (mask bound = `FULL_WEEKDAY_MASK` from constants) | Context keys `weekday_bits` / `weekday_labels` get the same tuples. |
| Quiet-hours "active now" (`engine.py:278-290` vs `web/routes/clusters.py:165-179`) | `logic.timing.active_quiet_window` | Web: keep `prefs = repo.get_preferences()` first, then `quiet_active_now = active_quiet_window(effective_config, now_unix=int(time.time()), tz_name=prefs.timezone if prefs else None) is not None`. `is_within_quiet_hours` always returns a real `bool` (verified: `_hour_in_range` returns comparisons, the guards return `False`), so the template value is identical. |
| `session.get(Plant, id)` (`routes/charts.py:37`, `web/routes/plant_dashboard.py:29`, `services/charts.py:50,353`) | existing `repo.get_plant` | — |
| Web plant form mapping (`web/routes/plants.py` create ≡ update) | web-private `_plant_form_fields(...)` | — |
| CLI server URL (3 copies) | `commands/_helpers.resolve_server_url` | — |

### 7.3 Must stay separate (drifted — merging changes behavior)

Window validation (messages, punctuation and parse order differ); irrigator create and sensor create (web lacks
`IntegrityError` / the plant check, B-7); `check_all` `has_alerts` (B-13); vacation `starts < ends` (API POST
unvalidated, B-8); relative time (`filters.age_seconds` vs `plant_dashboard._relative_time`); moisture-target parsing
in `monitor_cluster` and both `issues.py` sites; the CLI `irrigator_add` vs `irrigator_update` config dict; the TUI
copies of weekday vocabulary (the package boundary forbids sharing); the TUI `_selected` variants (cluster/alerts/
settings return different types and messages, verified).

## 8. TUI decomposition

- **`tui/render.py`** holds pure builders. `Row = tuple[str | None, list[RenderableType | str]]`; this alias name is
  not UPPERCASE, so it stays out of the module-constants golden. Builders:
  - `plant_rows(plants: list[dict[str, Any]]) -> list[Row]`
  - `sensor_rows(status: dict[str, Any]) -> list[Row]`
  - `decision_rows(payload: dict[str, Any] | None) -> list[Row]`
  - `history_rows(payload: dict[str, Any] | None) -> list[Row]` (same sort key/reverse)
  - `config_rows(effective: dict[str, Any] | None) -> list[tuple[str, Text]]`
  - `window_rows(windows: list[dict[str, Any]]) -> list[Row]`
  - `irrigator_info(summary: ClusterSummary) -> Text`
  - `decision_panel(decision: dict[str, Any]) -> Text`
  - `forecast_rows(forecast: dict[str, Any]) -> list[tuple[str, str | Text]]` (`_next_water` moves with it)
  - `insights_text(insights, monitor) -> Text`
  - `stats_rows(stats) -> list[tuple[str, str]]`
  - `efficacy_rows(payload) -> list[Row]`
  - `learn_report(learn) -> Text`
  - `job_rows`, `device_rows`, `quality_rows`, `scheduler_panel_rows` (SystemScreen)
  - `account_rows`, `preference_rows`, `global_config_rows`, `vacation_rows` (SettingsScreen)

  Colours, styles, column order and placeholder strings stay byte-identical. `render.py` imports `rich` and
  `greenhouse_cli.tui.formatting` / `.model` only, and assigns **no** public UPPERCASE names.
- **Screen methods** become "fetch → build → `refill` / `update` / `show`", each ≤ 15 lines. Widget ids, column
  headers, `refill` call order, `KeyValue.show(..., title=...)` arguments, worker groups (`"load"`, `"act"`,
  `"plant-health"`, `"insights"`) and `asyncio.gather` order are all unchanged.
- **CRUD dispatch.** `action_new` / `action_edit` / `action_delete` keep their names. Each becomes
  `handler = {"tab-plants": self._new_plant, …}.get(self.active_tab)`; if `handler is None`, it shows the same
  `notify(...)` text and returns; otherwise it calls `handler()`. `active_tab` is read once per action.

  Per-tab handlers:
  - `_new_plant/_new_sensor/_new_window/_attach_irrigator`
  - `_edit_plant/_edit_sensor/_edit_window/_edit_irrigator/_edit_config`
  - `_delete_plant/_delete_sensor/_delete_window/_detach_irrigator`

  Each handler keeps the same lambdas and `form_then` / `confirm_then` arguments, including the stale help text (B-19).
- **Name rule:** no new name may start with `action_`, `on_`, `_on_`, `watch_`, `_watch_`, `compute_` or `validate_`.
  Add no UPPERCASE class attributes (e.g. no `TABS = {...}`) and no new `DOMNode` subclasses.
- **Rejected:**
  - C's per-tab controller objects (back-references into the screen, indirection for 9 tabs);
  - B's `DataScreen._selected` hoist (the variants are not duplicates);
  - sprite data/render split (`sprites` constants are pinned under their module name).
- **Small helpers:**
  - `model.summarize` → `_band(plants) -> tuple[float | None, float | None]` and `_plant_views(status) -> list[PlantView]`.
  - `MetricChart.show_payload/show_overlay` → private `_draw_event_lines(self, events, xs)` and
    `_set_x_ticks(self, ...)`. These are not handler-prefixed.
  - `forms.parse_value`, `FormScreen.compose`, `Heatmap.show`, `formatting.bar`: leave.

## 9. Constants additions (`constants.py`; new names only, same value **and** type)

Rule: copy the literal exactly from the cited site (an `int` stays an `int`, a `float` stays a `float`). Grep for a name
collision before adding a name. Never reuse an existing constant whose value merely coincides: for example,
`DEFAULT_SOIL_MOISTURE_MIN/MAX` and `SENSOR_HEALTH_BACKFILL_WINDOW` (= 5 readings) are different concepts. User-facing
strings that embed a number (`"fallback (20C)"`, `"in next 6h"`) keep their literal text.

| Name | Value | Sites |
|---|---|---|
| `SECONDS_PER_HOUR` / `SECONDS_PER_DAY` | `3600` / `86400` | engine 407,424,425,499,504,537; maintenance 45-46; forecast 119 |
| `SNAPSHOT_LOOKBACK_HOURS` | `24` | engine 200 |
| `CONFIDENCE_BASELINE` | `0.5` | engine 231 |
| `WEATHER_FORECAST_HOURS` / `WEATHER_SKIP_PRECIP_MM` | `6` / `2.0` | engine 557,562; forecast 14,127 |
| `DEFAULT_SOIL_MOISTURE_TARGET` | `"45-65"` | engine 684; health 52; forecast 65; irrigation 635; issues 122,190 |
| `STRESS_HUMIDITY_DEFICIT`, `STRESS_LOW_LIGHT_FRACTION`, `STRESS_STEEP_DECLINE_DELTA`, `STRESS_HEAT_OFFSET_C` | `20`, `0.4`, `-10`, `5` | stress 28,36,49,54 |
| `TREND_LOOKBACK_HOURS`, `CADENCE_WINDOW_DAYS` | `48`, `7` | trends 21,54,62 |
| `CADENCE_LOW_EVENTS_PER_DAY`, `CADENCE_LOW_AVG_MINUTES`, `CADENCE_HIGH_EVENTS_PER_DAY` | `1`, `2`, `3` | trends 64,66 |
| `FALLBACK_HIGH_NEEDS_INTERVAL_STEP`, `FALLBACK_LOW_NEEDS_INTERVAL_STEP` | `4`, `6` | fallback 89,91 |
| `NIGHT_LUX_THRESHOLD` | `15` | utils 28 (now imported), sensors 46 |
| `SEASONAL_LIGHT_FACTOR_BY_MONTH` | the 12-entry `dict[int, float]` (same order) | utils 11-24 (aliased as `_SEASONAL_LIGHT_FACTOR`) |
| `LEARNING_CONFLICT_LOOKBACK_HOURS`, `LEARNING_DRAINAGE_LUX_LOOKBACK_HOURS`, `LEARNING_WEEK_HOURS` | `6`, `48`, `168` | issues 82,129,171,253,284 |
| `LEARNING_LATEST_SAMPLES`, `LEARNING_CHRONIC_MIN_RESPONSES`, `LEARNING_CONFLICT_DRY_MARGIN`, `LEARNING_MIN_ENV_SAMPLES`, `LEARNING_HUMIDITY_DEFICIT` | `3`, `5`, `5`, `5`, `15` | issues 132,174,197,255,286,289 |
| `LOW_LIGHT_ALERT_FRACTION` | `0.5` | issues 259; maintenance 83 |
| `MAINTENANCE_LOOKBACK_HOURS`, `MAINTENANCE_STALE_SECONDS`, `MAINTENANCE_MIN_SAMPLES`, `MAINTENANCE_HUMIDITY_DEFICIT` | `24`, `3 * SECONDS_PER_HOUR` (=10800 int), `3`, `10` | maintenance 30,45,58,64,75 |
| `MONITOR_LOOKBACK_HOURS`, `MONITOR_VERY_DRY_MARGIN`, `MONITOR_WET_MARGIN` | `2`, `15`, `10` | irrigation 628,643,647 |
| `FALLBACK_TEMPERATURE_C` | `20.0` | irrigation 420 |
| `PUMP_WATCHER_POLL_SECONDS`, `PUMP_WATCHER_WARMUP_SECONDS`, `PUMP_WATCHER_MAX_READ_FAILURES` | `2.0`, `5.0`, `5` | irrigation 180-182; pump_watcher 59-61 defaults; config 156-163 `Field(default=…)` (Settings schema golden proves it) |
| `LEAK_CHECK_ACTIVITY_SCAN_LIMIT` | `500` | irrigation 242 |
| `ANOMALY_SCAN_INTERVAL_MINUTES`, `HEALTH_SNAPSHOT_HOUR`, `HEALTH_SNAPSHOT_MINUTE`, `SYNC_JOB_BACKFILL_HOURS` | `15`, `0`, `30`, `6` | scheduler 184,216-217,306 |
| `SENSOR_HEALTH_BACKFILL_HOURS` | `24 * 7` (=168 int) | health_monitor 244 |
| `FORECAST_CONFIDENCE_HIGH/MEDIUM/LOW`, `FORECAST_HIGH_CONFIDENCE_PROFILES` | `0.7`, `0.4`, `0.2`, `3` | forecast 105-111 |
| `HEALTH_SCORE_WINDOW_DAYS` | `14` | health 20 (signature default) |
| `WINDOW_HOUR_MAX`, `FULL_WEEKDAY_MASK` | `23`, `127` | routes/windows 24,32; web/routes/windows 37,41; repository 999 default; web/weekdays |

Line numbers are at `a1b2622`. If the literal at a cited site differs from this table, **stop and report**; do not
"fix" the table. Deliberately not moved: `"open-meteo"` (a label), `"alarm_dp": 105` (device profile), TUI literals
(CLI cannot import core), and `DEFAULT_QUIET_*` (already exist, dead). `plugin/` docs are out of scope; no value
changes, so `LOGIC.md` stays correct (note it in REFACTOR_NOTES).

## 10. Typing and interface standards for internal methods

1. **Explicit typed parameters.** No new `**kwargs` / dict bags in internal signatures. Exceptions:
   - pass-through wrappers that forward to an API that takes kwargs (`_object` / `_array` → `httpx`);
   - repository `update_*(**fields)`, which are public and frozen.
2. **Keyword-only (`*`)** for any parameter after the first two whose meaning isn't obvious at the call site:
   booleans, flags, counts, units, windows (`force=`, `persist=`, `triggered_by=`, `days=`, `limit=`, `after_id=`,
   `json_fields=`, `override_window=`).
3. **Parameter objects** (frozen dataclasses, `slots=True`, module-private) once a helper needs ≥ 5 related values that
   travel together: `_EngineInputs`, `_Actuation`, `_HealthBands`, `_SensorForecast`. Use them only inside one module,
   never in a serialized contract.
4. **Result types:**
   - `TypedDict` for dicts that cross a boundary and are consumed as dicts (templates, `Response(**result)`, JSON):
     `PipelineResult`, `MonitorResult`, `CheckResult`, `HealthScore`, `WatchOutcome`. They are runtime-identical, so
     key order is still decided by the literal that builds them. TypedDicts are **never** used as FastAPI annotations.
   - Small `tuple[...]` returns are allowed for ≤ 3 values with an obvious order.
   - We add no Pydantic models and no dataclasses where a serialized dict is the contract.
5. **Precise return types.** Use `X | None` rather than a bare `Optional` without annotation, and no `-> dict` / `-> list`
   without type parameters in touched code. `-> None` for commands.
6. **Command–Query Separation.** A helper either computes and returns a value, or performs effects and returns `None`.
   Two sanctioned exceptions, both mirroring today's identity semantics: `_with_error` / `_apply_health_block`, which
   mutate `result` in place and return it, and `_record` / `_finish`, which persist and return the decision.
7. **Size targets** for new and touched functions: ≤ 40 lines (aim for 20), CC ≤ 8, nesting ≤ 3. Use guard clauses
   instead of `else` after `return`.
8. **Names reveal intent.** Use verb phrases for commands (`_apply_health_block`), noun phrases for queries
   (`_blocking_alarms`). Don't encode the type (`_get_dict`). Private (`_`) unless another module imports the name.
9. **Docstrings say *why*** (one line; Google style only on public functions). **Never** add or alter docstrings on
   route functions, Pydantic classes or Typer commands; those are contracts.
10. **Patch seams:**
    - keep `import time` with `time.time()` attribute calls (engine, repository, every new helper);
    - keep `import time as _time` in `services/irrigation.py`;
    - **never** write `from time import time`;
    - a helper that calls a patched module global (`season_for`, `seasonal_light_factor` in issues) must live in that
      module.
11. **Annotations under `from __future__ import annotations`** are allowed in any module that is not a Pydantic or
    FastAPI module. Do not add it to `schemas.py`, route modules or `config.py`, where it changes annotation
    evaluation.

## 11. Tooling — import-linter contracts and ratchets

```toml
[tool.importlinter]
root_packages = ["greenhouse_core", "greenhouse_server", "greenhouse_cli"]
include_external_packages = true

[[tool.importlinter.contracts]]
name = "CLI is HTTP-only"
type = "forbidden"
source_modules = ["greenhouse_cli"]
forbidden_modules = ["greenhouse_core", "greenhouse_server", "sqlalchemy", "fastapi"]

[[tool.importlinter.contracts]]
name = "Core never imports server or CLI"
type = "forbidden"
source_modules = ["greenhouse_core"]
forbidden_modules = ["greenhouse_server", "greenhouse_cli"]

[[tool.importlinter.contracts]]
name = "Server layers: app > routes|web > deps|auth > scheduler > services > config"
type = "layers"
layers = [
  "greenhouse_server.app",
  "greenhouse_server.routes | greenhouse_server.web",
  "greenhouse_server.deps | greenhouse_server.auth",
  "greenhouse_server.scheduler",
  "greenhouse_server.services",
  "greenhouse_server.config",
]
ignore_imports = [
  "greenhouse_server.services.irrigation -> greenhouse_server.scheduler",     # lazy; _app trap (§6)
  "greenhouse_server.services.system_health -> greenhouse_server.scheduler",
]

[[tool.importlinter.contracts]]
name = "API routes and web routes are independent"
type = "independence"
modules = ["greenhouse_server.routes", "greenhouse_server.web"]

[[tool.importlinter.contracts]]
name = "Devices do not depend on decision logic, learning or persistence"
type = "forbidden"
source_modules = ["greenhouse_core.devices"]
forbidden_modules = ["greenhouse_core.logic", "greenhouse_core.learning", "greenhouse_core.repository",
                     "greenhouse_core.schemas", "greenhouse_core.sync"]

[[tool.importlinter.contracts]]
name = "Data definitions stay below behaviour"
type = "forbidden"
source_modules = ["greenhouse_core.models", "greenhouse_core.schemas", "greenhouse_core.constants"]
forbidden_modules = ["greenhouse_core.logic", "greenhouse_core.learning", "greenhouse_core.repository",
                     "greenhouse_core.devices", "greenhouse_core.sync"]

[[tool.importlinter.contracts]]
name = "Learning builds on logic; engine's advisory hook is the only way back"
type = "layers"
layers = ["greenhouse_core.learning", "greenhouse_core.logic"]
ignore_imports = ["greenhouse_core.logic.engine -> greenhouse_core.learning"]

[[tool.importlinter.contracts]]
name = "Inside logic: engine orchestrates; rules/values never import it"
type = "layers"
layers = [
  "greenhouse_core.logic.engine",
  "greenhouse_core.logic.fallback | greenhouse_core.logic.sensors | greenhouse_core.logic.stress | greenhouse_core.logic.trends",
  "greenhouse_core.logic.rules | greenhouse_core.logic.timing | greenhouse_core.logic.cleaning | greenhouse_core.logic.plant_needs",
  "greenhouse_core.logic.decision",
]

[[tool.importlinter.contracts]]
name = "Logic never drives devices"
type = "forbidden"
source_modules = ["greenhouse_core.logic"]
forbidden_modules = ["greenhouse_core.devices", "greenhouse_core.sync"]

[[tool.importlinter.contracts]]
name = "TUI view-model modules stay widget-free and I/O-free"
type = "forbidden"
source_modules = ["greenhouse_cli.tui.render", "greenhouse_cli.tui.model", "greenhouse_cli.tui.formatting"]
forbidden_modules = ["textual", "greenhouse_cli.tui.screens", "greenhouse_cli.tui.widgets", "greenhouse_cli.client", "httpx"]
```

- **Verifying the contracts.** Against `refactor/baseline/import-graph.txt`, every contract has zero violating edges
  other than the listed ignores. Task T0.2 must confirm this with `lint-imports` (grimp also sees `TYPE_CHECKING`
  imports).
- **Pending modules.** `logic.rules` and `tui.render` do not exist yet, and import-linter rejects unknown modules in
  `layers` / `source_modules`. So T0.2 leaves those two entries out. The integrator adds each one (plan I1) right
  after the WP that creates the module merges; `pyproject.toml` is integrator-owned.
- **Fallback.** If the `layers` contract for the server fails on an edge not seen in the graph, replace it with the
  equivalent forbidden contract (services must not import routes/web/app/deps) and record why. Never change code to
  satisfy a contract in Phase 0.

**Ruff ratchet.** Add `extend-select = ["C90", "PLR0911", "PLR0912", "PLR0915"]` with `max-complexity = 10`.
`PLR0913` is not enabled: 61 hits, mostly frozen route and Typer signatures.
- Per-file ignores list today's offenders, from `refactor/baseline/ruff-complexity-rules.txt`.
- The integrator deletes an ignore once its file is clean (`uv run ruff check --select C90,PLR0911,PLR0912,PLR0915 --isolated <file>`).
- Not enabled:
  - `TID251` on `time.time`: verified that ruff flags *every* `time.time()` call, not just `from time import time`;
  - `PLC0415`: lazy imports are deliberate;
  - `D`: would force docstrings on Pydantic classes;
  - `ARG`: FastAPI dependency params are unused by design;
  - `PLR2004`.

**Mypy ratchet.** Add `mypy` to the dev group.
- `[tool.mypy]` sets `strict = true`, `ignore_missing_imports = true`, `follow_imports = "silent"`,
  `python_version = "3.11"`, and `files = [<strict list>]`.
- The strict list is seeded with the modules that are clean at baseline (see the plan, T0.4) and grows by one entry per
  finished module. It never shrinks.
- `make typecheck` = `uv run mypy` must pass.
- The default-mode error count over all three packages (baseline 176) is report-only and may only go down.

**Guard tests** (new, test-only, `tests/test_refactor_guards.py`):
1. Importing `greenhouse_cli.main` does not put `textual` into `sys.modules`.
2. An AST scan of `libs/**/greenhouse_*/**/*.py` finds no `from time import time`.
3. An AST scan of `greenhouse_cli/tui/**/*.py` finds no module-level public UPPERCASE assignment outside the names
   already in `tests/golden/tui/surface.json` (it fails fast, before the golden does).

## 12. Seam and invariant checklist (reviewers tick this per commit)

- [ ] No logging call changed module, and log message text is unchanged.
- [ ] Clock reads stay at the same points:
  - engine `evaluated_at` comes once, before `get_plants_in_cluster`;
  - irrigation `started_at` comes after `adapter.start`;
  - forecast and maintenance `now` stay before their loops;
  - the pump-watcher `elapsed` is computed by the caller.
- [ ] Repo/plant-DB/weather call order and count are unchanged:
  - `get_preferences` is not cached;
  - `get_sensors_in_cluster` still comes after plant care;
  - `get_irrigation_config` is still only on the fallback path;
  - `detect_conflicts` keeps its two passes.
- [ ] Result-dict key order is unchanged, and `_with_error` mutates in place.
- [ ] No route function, route docstring, `response_model`, Pydantic class/field or web endpoint `module.qualname`
  changed.
- [ ] No TUI handler-prefixed name, UPPERCASE class attribute, `DOMNode` subclass, id or class changed or was added.
- [ ] `tests/golden/` diff is empty (`git status --porcelain tests/golden` prints nothing).
- [ ] No bug fixed; any touched bug site is listed in the commit body with its B-number.

## 13. Decision log (A / B / C disagreements)

| # | Question | A | B | C | **Chosen** | Why / why not the others |
|---|---|---|---|---|---|---|
| D1 | Overall shape | decompose in place | ports + layers | feature slices behind facades | **A as base, plus selected B/C items** | Smells are long functions in correct homes (00-smells verdicts). B's ports and C's facades add structure without a present smell (§4, §5). |
| D2 | Repository split | no | mixins in private `_repo/` | `repository/` package + mixins | **No split; helpers only** | Copy-paste is the cost (00-smells A2). Splits need exception-`__module__` hacks (B pins it, C uses partial-init imports), an MRO guard, and near-full-suite runs per commit. Deferred, with B's shape preferred if ever done. |
| D3 | PATCH helper | one `_patch` (hasattr order argued equivalent) | three helpers | None-first + separate hasattr-first | **Two helpers** (`_patch_fields`, `_patch_fields_hasattr_first`) | Keeps the exact evaluation order with no equivalence argument. The JSON variant is a keyword of the None-first helper (same order). |
| D4 | `schemas.py` | keep | keep | private slice package | **Keep; share the `parse_config` body only** | OpenAPI risk; cohesive DTO module (00-smells A3). |
| D5 | Light helpers in `utils.py` | constants imported into utils | new `_light.py` + re-export | — | **A** | 117-line module. B's move needs re-exports, with no smell removed. Moving under `logic/` would cause an import cycle (B §7.2). |
| D6 | Protocol ports | none | Engine/Learning repo ports, `RainForecast`, `WeatherSource` | none | **`RainForecast` only** | The only port with ≥ 2 implementations (3) *and* a type core can't otherwise name. The repo ports have 1 impl and need TYPE_CHECKING import moves; `WeatherSource` has no smell. |
| D7 | `scheduler._app` | keep global, pass at call time | `runtime.py` + `current_app()` + `__getattr__` | keep global | **Keep** | B is refuted by Phase-1 monkeypatches of `scheduler.{scheduler,_app,shutdown_requested,wait_for_shutdown}` (§6). |
| D8 | Job session helper | `_job_session(app, msg)` yields repo, commits | `runtime.job_session(factory, log=, failure=, args=)` yields session; caller commits | `_job_session(msg)` reads `_app` | **A** | Explicit app parameter (no hidden global read inside the helper). Byte-identical scaffolding for exactly the 5 jobs. B's generality targets the irrigation jobs, whose commit semantics differ. |
| D9 | Leak/pump-watcher lifecycle location | stay in `irrigation.py` | stay | move to `leak.py`/`pump_watcher.py` with re-exports + a logger alias | **Stay** | C needs `getLogger("…services.irrigation")` inside other modules (a confusing logger alias) and re-exports of private names that tests call by path. No smell removed that in-place helpers don't remove. |
| D10 | Engine pure rules | stay in `engine.py` | move to `logic/_rules.py` | stay | **Move to public `logic/rules.py`, after the in-place decomposition** | Engine (936 lines) mixes the orchestrator class with ~340 lines of pure mutators. The rules don't log, aren't patched, and the decision grid pins them. Public module because engine imports them (no cross-module private imports). Done as a move commit, then a rename commit. `engine.seasonal_light_factor` is kept alive (pinned path). |
| D11 | Engine gates | `_pre_gates` (no quiet) + quiet in body | `_pre_evaluation_gates -> _Gate(terminal, quiet_window)` | `_pre_gate` + quiet in body | **A/C** | The quiet window is needed later for the override reason. Keeping it a local avoids a two-field NamedTuple. |
| D12 | Light rule gets `light_factor` param | no | yes | no | **No** | B's version calls `seasonal_light_factor()` unconditionally (today: only when `avg_light is not None`), which changes the call pattern. |
| D13 | Trim `_decision_with_reason` unused params | — | yes | yes | **Yes** | All 6 callers are in engine and none pass them (grep); the body is identical. |
| D14 | `_evaluate_rules` cluster id | `cluster.id` | explicit | explicit | **Explicit `cluster_id`** | No equivalence reasoning needed. |
| D15 | Pipeline skip-activity helper | `_record_skip(severity=, payload=)` | `_log_decision_skip(cluster_id, decision)` | same as B | **B/C** | Avoids A's "omit payload when None" kwargs juggling. The health block keeps its own explicit call. |
| D16 | Pipeline actuation params | 9-param `_actuate` | 7 params + result | `_Actuation` dataclass | **C's `_Actuation`** | Parameter-object standard (§10.3). |
| D17 | Shared quiet-window helper name | `quiet_window_at` | — | `active_quiet_window` | **`active_quiet_window`** | Reads as what it returns (the window if active, else None). |
| D18 | Breadth of API/web dedupe | sync_plants, CSV, weekdays, quiet, exact `require_cluster` | sync_plants, CSV | + window_violation enum, vacation_range_is_valid, create_irrigator_with_capacity, require_found + in-cluster lookups, additive repo reads, web plant form mapper | **A's set + C's web plant form mapper + the exact `session.get` reads** | The rest merges drifted code (B-7/B-8) or adds ceremony bigger than the duplicate (6-line validators, one-line range check). New in-cluster lookups change SQL shape. |
| D19 | Weekday vocabulary home | `web/weekdays.py` | — | `services/windows.py` | **`web/weekdays.py`** | Labels and formatting are web presentation. Bounds go to `constants.py`. |
| D20 | `sync_plants` lookup + error | verbatim scan, `LookupError` | — | `get_plant` + `get_cluster`, `PlantNotFoundError` | **Verbatim scan + `PlantNotFoundError(LookupError)`** | FK enforcement is off, so the scan and `get_plant` differ on orphan plants (A). A named exception reads better (C). |
| D21 | Fold CSV export | yes | — | only if bytes equal | **Yes** | Verified line-identical logic (`routes/operations.py:355-389` vs `web/routes/analytics.py:79-107`). |
| D22 | TUI shape | `render.py` + per-tab methods | `rows.py` + `_selected` hoist | `rows.py` + per-tab controller objects | **A** (`tui/render.py`, per-tab private methods, dict dispatch) | `_selected` variants differ (verified). Controllers add a screen↔controller object graph and worker-ownership indirection for no second variant. |
| D23 | CLI client typing | `cast` wrappers | `cast` wrappers | `_request -> Any` | **Wrappers** | Strict mypy `warn_return_any` rejects returning `Any` from typed methods. |
| D24 | `_drop_none` signature | `**fields` | `Mapping` | `Mapping` | **`Mapping`, module function** | Explicit parameter; also covers the `filters` site. |
| D25 | Literal → constant home | `constants.py` | module-private until golden policy decided | `constants.py` | **`constants.py`** | Policy settled: the constants golden is superset (CLAUDE.md invariant 5). |
| D26 | import-linter set | 8 forbidden contracts | layers + domain purity | — | **Union minus domain purity** (§11) | Domain purity needs the rejected port refactor. B's server/logic layers verified against the baseline graph. |
| D27 | Ban `from time import time` via ruff TID251 | yes | — | — | **No; AST guard test instead** | Verified: TID251 on `time.time` flags every legitimate `time.time()` call. |
| D28 | mypy gate | strict overrides + count | strict overrides + count | — | **`files = strict list`, pass/fail; default count report-only** | A per-module list is a deterministic pass/fail gate. |
| D29 | Devices | docstrings only | + `_parse_dps` helper | none | **Docstrings only** | v1/v2 error handling differs (`ValueError`), so a merged helper needs a flag. High risk, low payoff. |
| D30 | Additive repository reads | none | several (search, alerts, charts…) | several in-cluster lookups | **Only exact `session.get` equivalents** (`get_vacation_window`; use `get_plant`) | Identical SQL by construction. The others need per-site SQL-equivalence proofs. |
| D31 | `web/routes/analytics.py` split | no | — | optional | **No** | Blocked by `web/routes.json` (strict). |
| D32 | Hoist lazy imports in `services/irrigation.py` | no | yes (after runtime.py) | no | **No** | Follows from D7. |
| D33 | `compute_score` bands | inline + `moisture_target_range` | `_HealthBands` dataclass | `_Bands` | **`_HealthBands` frozen dataclass** | 5 related care values travel together (§10.3). |
| D34 | `check_cluster` builder | `**detail` | optional `notes`/`needs_water` (omit None) | same as B | **Explicit `detail_key: Literal[...]`, `detail: object`** | Typed, no kwargs bag, and no "omit-if-None" semantics a `None` reason could trip. |
| D35 | Soil-rule dosage setter | none | `set_dosage` | `_set_irrigate`/`_set_skip` | **None** | Branches differ in which fields they set; verbatim per-branch assignment needs no equivalence proof. |
| D36 | Learner 3× per check, stress re-query, `get_preferences` dedupe | no | no | no | **No** (unanimous) | Call-count or perf change. |

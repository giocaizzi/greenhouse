# 20 — Target architecture (synthesis of proposals A / B / C)

Author: Synthesizer, Phase 2. Inputs: `BRIEF.md`, `CLAUDE.md`, `REFACTOR_NOTES.md` (incl. "Golden-test policy"),
`00-{map,smells,contracts,baseline,tests}.md`, the three proposals, the Phase-1 contract tests and their goldens under
`tests/golden/`. Contested claims were checked against the code at `db38192` (evidence is cited inline and in §13).
The executable task list is `refactor/20-plan.md`.

> **Revision 1** (after `refactor/50-adversary-plan-critic.md`, Gate 2 round 1: REVISE). Every change is listed in
> §13 "Revision 1". In short:
> - no `logic/rules.py`; the engine rules are decomposed inside `engine.py`;
> - no `resolve_server_url`;
> - an enforceable Definition of Done (≤ 40 body lines, CC ≤ 8, nesting ≤ 3, strict mypy on every touched module);
> - a verdict for every long function and large file (§3.12);
> - a tighter test-subset rule and 2-worktree concurrency (plan §0).
>
> **Revision 2** (Gate 2 round 2): pinned hash seeds and a strict flaky policy (plan §0.2); a nesting column and five
> new verdicts in §3.12; register approval by the integrator; mutant-identity comparison; `sync.py` added to the
> mutation list; the in-place probe. See §13 "Revision 2".

## 0. Thesis

1. **Decompose in place.** Most smells here are long functions in modules that are already in the right place. So we
   extract private helpers *inside the same module*. That keeps logger names, monkeypatch seams and import paths stable
   for free (proposal A is the base).
2. **Two new modules, each removing a concrete smell.** These are `web/weekdays.py` and `tui/render.py`. We add
   **no packages, no facades and no shims**: no public name changes module. The only new public names are additions,
   which the superset goldens allow. *(Rev 1: `logic/rules.py` was dropped, see D10.)*
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
  logic/engine.py           ~ decide_for_cluster decomposed into gates/inputs/rules/finish; the pure rule
                              functions STAY in this module and are split into private helpers there (Rev 1, D10);
                              RainForecast Protocol; keeps `import time`, `season_for`, `seasonal_light_factor` and
                              all ≥ 50 constant imports (pinned by tests/test_invariants_engine.py:236)
  logic/{trends,stress,sensors,fallback}.py   ~ functional core + thin I/O shell, same public signatures
  learning/issues.py        ~ detect_issues / detect_conflicts split into per-check private helpers (same module)
  learning/profiling.py     ~ compute_sensor_response / get_plant_profile phase helpers (Rev 1)
  stats.py                  ~ get_irrigation_stats / print_stats_report helpers (Rev 1; public names unchanged)
  sync.py                   ~ sync_single_sensor phase helpers (Rev 1; logger `greenhouse_core.sync` unchanged)
  devices/sensors/{tuya_generic,tr301z}.py, devices/profile.py   ~ stale docstrings + strict annotations only
  models.py, plant_db.py, database.py, auth.py, devices/{gateway,registry,irrigators/*}   =

libs/greenhouse-server/greenhouse_server/
  app.py                    ~ create_app → _make_lifespan/_new_fastapi/_init_state/_init_background/
                              _include_api_routers/_mount_web/_mount_mcp; _OPENAPI_TAGS, _PROTECTED_API_ROUTERS
  config.py                 ~ pump-watcher Field defaults reference constants (identical values)
  scheduler.py              ~ _job_session(app, failure_message) context manager for the 5 core jobs;
                              _build_irrigation_service(app, repo, registry, cloud); literals → constants;
                              CHECK_ALL_JOB_ID declared before first use; `_app` stays a plain module global
  services/irrigation.py    ~ pipeline / temperature / monitor / check / check_all / watcher plumbing decomposed
                              IN PLACE; PipelineResult, MonitorResult, CheckResult TypedDicts; _Actuation dataclass
  services/cluster.py       ~ + PlantNotFoundError, ClusterService.sync_plants(...), cluster_events_csv(...)
  services/{health,forecast,maintenance,data_quality,charts,insights,pump_watcher,health_monitor,
            search,anomaly,efficacy,system_health}.py  ~ per-check / per-phase helpers
  services/leak.py          ~ check_after_irrigation / _evaluate_sensor helpers (Rev 1; invariant 11 path, G+)
  routes/{plants,operations,vacation,charts}.py   ~ bodies delegate to the shared helpers (names/docstrings untouched)
  routes/*.py, web/routes/*.py   ~ exact-form "Cluster not found" blocks → deps.require_cluster (optional, low value)
  web/weekdays.py           + WEEKDAY_BITS, WEEKDAY_LABELS, format_weekday_mask (3 identical copies today)
  web/routes/{clusters,configs,windows,operations,analytics,plants,plant_dashboard}.py   ~ use shared helpers
  web/context.py            ~ base_context helpers (Rev 1; context keys unchanged)
  deps.py, auth.py, web/filters.py, web/exception_handlers.py   =

libs/greenhouse-cli/greenhouse_cli/
  client.py                 ~ JSONObject alias; _object/_array typed wrappers over the unchanged _request;
                              module function _drop_none(fields); docstrings on undocumented methods
  commands/**               = (Rev 1: no resolve_server_url — the 3 env reads are pinned per module by
                              tests/golden/contracts/env_reads.json)
  tui/render.py             + pure payload → rows / Rich Text builders (imports rich + tui.formatting only)
  tui/screens/cluster.py    ~ builders moved to render.py; CRUD if/elif chains → per-tab private methods + dict dispatch
  tui/screens/{system,settings}.py   ~ load() feeds render.py builders
  tui/model.py              ~ summarize() gets 2 private helpers
  tui/widgets.py            ~ MetricChart private axis/event helpers (in-class duplicate removed)
```

### 2.1 Responsibilities of new or changed modules

| Module | One responsibility |
|---|---|
| `logic/engine.py` | The rule pipeline: the `IrrigationLogic` orchestrator (gates, inputs, persistence, and the repo-reading rules `_enforce_leak_hold`, `_enforce_cooldown`, `_apply_window_rule`, `_apply_seasonal_multiplier`, `_apply_vacation_budget`, `_apply_weather_skip_rule`, which CLAUDE.md invariants 9 and 11 cite) **plus** the pure module-level rule functions and their new private helpers. It is one cohesive module and stays > 400 lines as a documented exception (§3.12). |
| `logic/timing.py` (+1 fn) | Local-time gating. `active_quiet_window` is exactly that concern, and the engine and web `cluster_detail` share it. |
| `logic/plant_needs.py` (+1 fn) | Interprets plant care data. `moisture_target_range` belongs here. |
| `web/weekdays.py` (new) | Weekday bitmask vocabulary and label formatting for web templates (web-only presentation). |
| `services/cluster.py` (+3) | Cluster status/history plus the per-cluster plant-DB sync and events CSV, which the API and web share. |
| `tui/render.py` (new) | Turns API payload dicts into `refill()`-ready rows `(key, cells)` and Rich `Text`. No widgets, no I/O, no Textual import. |

### 2.2 Re-export shims

**None.** No public name changes module. One deliberate alias: `services/forecast.py` keeps
`_WEATHER_PRECIP_THRESHOLD_MM` as an alias of the new constant. *(Rev 1: the `engine.seasonal_light_factor` keep-alive
import is no longer needed, because the light rule stays in `engine.py`.)*

Private web helper names (`_WEEKDAY_BITS`, `_WEEKDAY_LABELS`, `_format_weekday_mask`, `_FULL_MASK`,
`_FULL_WEEKDAY_MASK`) are not referenced by tests (grep), so they are **replaced** by imports from `web/weekdays.py`
and get no alias.

## 3. Method-level decomposition of the worst offenders

Rules for every row:
- **Order.** Every repository, plant-DB, weather, device, notifier and clock call happens in the original order.
- **Assignment order.** Every assignment to a `validate_assignment` Pydantic field (`IrrigationDecision`, `Trends`,
  `StressIndicators`) happens in the original order, and every `add_reason` too.
- **Dict keys.** Every result dict keeps its key insertion order.
- **Short-circuits.** These stay exactly as they are. *(Rev 1: sequential `if … return` blocks are **not**
  collapsed into `or`, even when equivalent by short-circuit, because the operands mutate state.)*
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
| ~~`_blocking_alarms` / `_apply_health_block`~~ | **dropped in Rev 1 (old T7.12)** | The device-health gate (L497-528) stays **inline and verbatim**, keeping `blocked, blocking_alarms = self._health_monitor.is_actuation_blocked(irrigator)` and `if blocked:`. Testing `if alarms:` instead would be equivalent only through `is_actuation_blocked` returning `bool(blocking), blocking` (`health_monitor.py:232`, a module WP4 owns): with `(True, [])`, today's code raises IndexError and the new code would actuate. So the pipeline body ends ≈ 45 lines: a size exception (§3.12). |
| `IrrigationService._actuate` | `(self, act: _Actuation, result: PipelineResult) -> PipelineResult` | `adapter.start(irrigator, duration)` → `started_at = int(_time.time())` (**after** start; `_time` seam) → `add_irrigation_event(... notes=_event_notes(act))` → `_on_started` then `result["action"]="irrigated"` then `_notify_auto_irrigation`, **or** `_on_start_failed` then `result` `action`/`reason`. |
| `_event_notes` (module) | `(act: _Actuation) -> str` | The soil note and notes f-string of L536-553, verbatim. |
| `IrrigationService._on_started` | `(self, act: _Actuation, started_at: int) -> None` | `irrigated` activity → `set_decision_actuated` (if a log id is present) → `_schedule_leak_check` → `schedule_pump_watcher`. |
| `IrrigationService._notify_auto_irrigation` | `(self, act: _Actuation) -> None` | `maybe_notify(self._notifier, self._repo.get_preferences(), "auto", lambda: …)`. `get_preferences()` is still evaluated as an argument, because it may insert the row. |
| `IrrigationService._on_start_failed` | `(self, act: _Actuation, output: str) -> None` | `actuation_failed` activity, then `raise_alert(...)`. |

Resulting body (≈ 45 lines with the inline health gate → size exception, §3.12):
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
if self._health_monitor is not None:                    # device-health gate: L497-528 verbatim (B-6 kept)
    blocked, blocking_alarms = self._health_monitor.is_actuation_blocked(irrigator)
    if blocked:
        ...                                              # 20 verbatim lines: reason → SKIP → activity → result keys
        return result
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
  `_check_result(cluster_id: int, cluster_name: str, action: str, *, detail_key: Literal["notes", "needs_water"], detail: str | list[str], alerts: list[dict[str, Any]], maintenance: list[dict[str, Any]]) -> CheckResult`
  builds `cluster_id, cluster_name, action, <detail_key>, alerts, maintenance` in that order and returns
  `cast(CheckResult, {...})`. Documented reason, written as a comment at the cast: "a TypedDict literal cannot carry a
  computed key; the runtime object is the same plain dict" (Rev 1, m1). The call order stays:
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
- **`schedule_pump_watcher`:** it keeps its call-time lazy
  `from greenhouse_server.scheduler import _app, scheduler, shutdown_requested, wait_for_shutdown`, and it keeps a
  **thin closure** as the APScheduler job function. That closure captures app/registry at *schedule* time.
  - Extract `_watcher_tuning(settings: Settings | None) -> tuple[float, float, int]` (fallbacks → `PUMP_WATCHER_*`
    constants).
  - *(Rev 1)* Move the closure body into the module function
    `_run_pump_watcher(app: Any, registry: DeviceRegistry, *, irrigator_id: int, duration_seconds: int, started_at: int, triggered_by: str, sleep: Callable[[float], bool], stop_requested: Callable[[], bool]) -> None`.
    The closure becomes
    `def _run() -> None: _run_pump_watcher(app, registry, …, sleep=wait_for_shutdown, stop_requested=shutdown_requested)`.
  - **Captured at schedule time, as today:** `_app`, `registry`, `wait_for_shutdown` and `shutdown_requested`. These
    are the values the outer function's lazy import bound, so Phase-1 monkeypatches of those scheduler attributes still
    take effect.
  - **Read at run time, as today:** inside `_run_pump_watcher`, the order stays: open the session → `get_irrigator` →
    return if None → `_watcher_tuning(getattr(app.state, "settings", None))` → `health_monitor` bind →
    `PumpWatcherService(...)` → `watch` → interrupted ⇒ `handle_watcher_interrupted` + commit; except → rollback +
    `logger.exception("Pump watcher job failed for irrigator %d", …)`; finally → close.
  - The lazy `IrrigationRepository` / `PumpWatcherService` imports stay inside `_run_pump_watcher`, so they are still
    resolved through `services.pump_watcher`, which tests patch.
  - Both functions must end ≤ 40 body lines.
- **`_run_leak_check`, `_add_leak_check_job`, `_schedule_leak_check`:** unchanged. In `_leak_check_done`,
  `limit=500` → `LEAK_CHECK_ACTIVITY_SCAN_LIMIT` (bug B-23 preserved).
- **`rearm_leak_checks`** *(Rev 2, T7.22; nesting 4 → ≤ 3)*. Extract `_rearm_from_events(repo: IrrigationRepository, now: int) -> int`,
  which holds the irrigator/event double loop with its two `continue` guards, `_add_leak_check_job(...)`, and the
  count. What stays in `rearm_leak_checks`:
  - the lazy `from greenhouse_server.scheduler import _app, scheduler`, and the running / `_app` guard;
  - `now = int(_time.time())` (`_time` seam);
  - session open, `try` / `except` (`logger.exception` text) / `finally: close`;
  - the `logger.info` line.
  
  Tests call it by path 10×, and its name and signature are unchanged.

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
if _apply_water_warning_rule(decision):                          # two sequential ifs kept (Rev 1 nit)
    return decision
if _apply_critical_stress_rule(decision):
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
- **`_decision_with_reason`** keeps its name and stays in `engine.py`. It drops the three params that callers never pass
  (`sensor_snapshot`, `stress_indicators`, `trends`); the body still builds `StressIndicators()` / `Trends()` and
  `sensor_snapshot=None`. All 6 call sites are in `engine.py` (grep).
- **All module-level rule functions stay in `engine.py`** *(Rev 1, B1)*. That covers `_apply_water_warning_rule`,
  `_apply_critical_stress_rule`, `_apply_soil_moisture_rule`, `_apply_temperature_adjustment`,
  `_apply_humidity_adjustment`, `_apply_light_adjustment`, `_apply_water_needs_adjustment` and
  `_apply_trend_adjustment`. Their new helpers are private functions in the same module. Every constant import stays.
  `tests/test_invariants_engine.py:232-238` requires ≥ 50 UPPERCASE names in `vars(engine)` that mirror `constants`;
  today there are exactly 50, and T8.1 only adds more.

### 3.3 `engine._apply_soil_moisture_rule` (L678-752, 75 lines) — helpers stay in `engine.py`

```python
def _apply_soil_moisture_rule(decision: IrrigationDecision, plant_care: list[dict[str, Any]]) -> None:
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

The temperature, humidity, light, water-needs and trend adjustments stay **unchanged** (all ≤ 40 body lines, CC ≤ 8).
These are flat ladders, and bug B-14's nominal `interval_delta` must not be "fixed" by a shared clamp.

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
| `sensors.get_recent_sensor_data` (CC 19) | `_mean_or_none(values: Sequence[float]) -> float \| None` replaces 7 copies. `> 15` → `> NIGHT_LUX_THRESHOLD`. *(Rev 1)* Plus `_per_sensor_snapshot(sensor, readings) -> PerSensorSnapshot` for the loop body, so the function ends ≤ 40 / CC ≤ 8. Per-sensor cleaning order and the sorted warnings are unchanged. |
| `fallback.temperature_based_decision` (CC 13) | `_config_fallback(db, cluster_id, base) -> IrrigationDecision`; `_temperature_interval(temp: float, water_needs: str) -> int` using the `FALLBACK_*_INTERVAL_STEP` constants. Bug B-24 is preserved. |
| `profiling.compute_sensor_response` (49 lines) / `get_plant_profile` (51 lines) *(Rev 1)* | Phase helpers in `profiling.py`: the before/after moisture series, the response metrics, and the per-event aggregation. Each helper returns a value (no I/O beyond the existing repo reads, in the same order). Cleaned-view reads (invariant 10) stay exactly where they are. |
| `stats.get_irrigation_stats` (46 lines) / `print_stats_report` (CC 9) *(Rev 1)* | Aggregation helpers (`_event_totals`, `_daily_breakdown`, or equivalent) and one section-printing helper. Public names, signatures and printed text are unchanged (`greenhouse_core.stats` is a frozen import surface; superset). |
| `sync.sync_single_sensor` (56 lines) *(Rev 1)* | `_sync_window_start(last_ts: int \| None, hours: int, now: int) -> int`, `_store_history(...) -> int` (new-row count), and `_store_live_reading(...) -> bool`. The order is unchanged: window → `getdevicelog` pull → per-reading insert → live read inside its `try` (exception swallowed exactly as today). This carries invariant 8: still one live read per sensor and no extra Cloud call. Logger `greenhouse_core.sync` is unchanged. |

### 3.6 Server services

| Function | Decomposition |
|---|---|
| `health.PlantHealthService.compute_score` (CC 29) | `HealthScore` TypedDict (6 keys in order). `_empty_score() -> HealthScore` returns a fresh dict. `_HealthBands` is a frozen dataclass (`soil: tuple[float, float]`, `temp: tuple[float \| None, float \| None]`, `humidity: …`) with `@classmethod from_care(care)` (soil via `moisture_target_range`). `_pooled_clean_readings(self, sensors, days) -> list[SensorReading]`; `_in_band_pct(values: Sequence[float], lo: float, hi: float) -> float \| None`; `_band_percentages(readings, bands) -> tuple[float \| None, float \| None, float \| None]` (returns all-None early when `readings` is empty; temp/humidity only when both bounds are not None); `_first_profile_efficiency(self, sensors, days) -> float \| None` (first non-None, then `break`); `_composite_score(components: Sequence[float]) -> float \| None` (`float(max(0.0, min(100.0, round(mean))))`). Signature default `days: int = HEALTH_SCORE_WINDOW_DAYS`. |
| `forecast.ForecastService.predict_next_irrigation` (CC 25) | `now = int(time.time())` stays **before** the sensor loop. `_forecast_sensor(self, sensor, plant_map, learner) -> _SensorForecast \| None` (the profile lookup runs only when current moisture exists); `_no_data_forecast(cluster_id) -> ForecastResponse` (returned **before** any weather call); `_confidence_for(profiled_count: int) -> float`; `_rain_outlook(self, cluster) -> tuple[bool, str \| None, float \| None]`. The driver stays stable `sorted(...)[0]`. `weather_client: RainForecast \| None` (annotation only). |
| `maintenance.collect_maintenance_alerts` (CC 23) | `_battery_alert`, `_stale_alert(sensor, readings, now)`, `_humidity_alert(sensor, readings, plant, plant_db)`, `_light_alert(...)`, each `-> dict[str, Any] \| None`. Per-sensor append order is kept; `MAINTENANCE_*` constants. |
| `data_quality.build_report` (124 lines) | `_sensor_issues(repo, sensors, now) -> tuple[list[DataQualityIssue], set[int]]` (the per-sensor interleaving of `sensor_without_plant` and `stale_sensor` is kept), `_plant_issues`, `_cluster_issues`, `_duplicate_device_issues`, `_count_by_code`. The four passes keep their order. |
| `charts._threshold_for_cluster` (CC 20) | `_aggregate_band(mins: list[float], maxs: list[float]) -> dict[str, Any] \| None` for the identical temp/humidity blocks. Also `repo.session.get(Plant, id)` → `repo.get_plant(id)` (byte-identical body, 2 sites). |
| `insights.cluster_insights` (CC 14) | `_insight_from_alert(alert: Mapping[str, Any]) -> CareInsight` removes the in-file clone (both lists are collected before iteration, as today). |
| `pump_watcher.watch` (CC 13, 96 lines) | `_outcome(kind: str, *, polls: int, failures: int, alarm_raw: Any, elapsed: float) -> WatchOutcome` (TypedDict). The **caller computes `elapsed` at exactly the same point** (the injected clock is call-count sensitive). `completed` keeps `read_failures: 0`. *(Rev 1)* Plus one `_poll_step`-style helper for the per-iteration read/classify, until ≤ 40 body lines. Every `self._clock()` / `self._stop_requested()` / sleep call keeps its position and count. |
| `health_monitor.backfill_from_history` | `_raise_if_not_open(self, alarm, sensor) -> None` for the duplicated L252-274 block. `hours=24 * 7` → `SENSOR_HEALTH_BACKFILL_HOURS`. |
| `pump_watcher._handle_trip` (105 lines) *(Rev 1, G+)* | One private method per best-effort step, in the same order: `_stop_pump(irrigator) -> tuple[bool, str]`, `_trip_payload(...) -> dict[str, Any]`, `_log_aborted_event(...)`, `_log_trip_activity(...)`, `_record_trip_state(...)`, `_commit_trip(irrigator_id)`. Each keeps its own `try/except` + `logger.exception` text verbatim, so each step is still its own failure domain. The `logger.critical` line and `int(time.time())` keep their positions. |
| `search.search` (118 lines) *(Rev 1)* | `_cluster_hits(repo, pattern) -> list[SearchHit]`, `_plant_hits`, `_sensor_hits`, `_irrigator_hits`, then the existing fair-trim tail. SQL statements are moved verbatim (same `where`/`limit`). This is not a registry: four explicit calls in today's order. |
| `anomaly.SensorAnomalyService.scan` (115 lines) *(Rev 1)* | `_stale_alert(self, sensor, timestamps_asc, median_interval, now) -> Alert \| None` and `_drift_alert(self, sensor, window, median_interval) -> Alert \| None`. Both keep their `raise_alert` + `logger.warning` text. The stale check still runs before the drift check, and the `continue` guards become `None` returns. |
| `efficacy.score_cluster` (63 lines) *(Rev 1)* | `_event_items(repo, event, sensors, …) -> list[EfficacyItemResponse]` for the per-event body. Cleaned-view reads stay put. |
| `system_health.SystemHealthService.pulse` (57 lines) *(Rev 1)* | `_sensor_devices(sensors, now) -> tuple[list[SystemHealthDevice], list[int], int]` and `_overall_status(cloud_reachable, stale_count, open_alerts) -> str`. The `scheduler.running` read stays where it is. |
| `charts.build_overlay_payload` (60 lines, CC 11) *(Rev 1)* | `_bucket_readings(repo, sensors, hours) -> tuple[dict, dict, dict]` and `_overlay_datasets(...) -> list[OverlayDataset]` (the nested `_series` moves with them). Bucket and dataset order are unchanged. |
| `leak.LeakDetectionService._evaluate_sensor` (62 lines) / `check_after_irrigation` (63 lines) *(Rev 1, WP7, G+; invariant 11)* | `_evaluate_sensor`: `_moisture_series(before_rows, after_rows) -> tuple[list[float], list[float]]`, `_pinned_high(tail) -> bool`, `_still_rising(before, after) -> bool`, with rule order, thresholds (`LEAK_*`) and message strings verbatim. `check_after_irrigation`: a per-sensor verdict helper plus `_record_hold(...)` for the hold activity row. Alert raise/resolve order and the 1-alert-per-sensor rule are unchanged. `LeakDetectionService.check_after_irrigation` stays a class attribute (test patch target). |
| `services/cluster.ClusterService.get_cluster_status` (56 lines) *(Rev 1, WP5)* | `_sensor_status_rows(sensors) -> list[dict[str, Any]]` and `_irrigator_status(irrigator) -> dict[str, Any] \| None`. Dict key order and repo read order are unchanged. |
| `web/context.base_context` (46 lines) / `web/routes/plant_dashboard.plant_dashboard` (77 lines) *(Rev 1, WP5)* | `base_context`: `_preference_flags(request) -> tuple[bool, VacationWindow \| None, bool, str]` (keeps the `try/except/finally: session.close()` and the swallow) and `_auth_enabled(request) -> bool`. Returned key order is verbatim. `plant_dashboard`: context-section helpers; route name/params untouched; context keys verbatim (`web/template_context.json` golden). |
| `manual_control.*`, `weather.get_forecast`, `alerts.sync_cluster_alerts`, `sync._cluster_snapshot` | Already ≤ 40 body lines, CC ≤ 8 and nesting ≤ 3: **OK**, no task. *(Rev 2: `bulk.stop_all_irrigators` was wrongly listed here; it has nesting 4 and is a register entry, §3.12.)* |

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
  `_build_irrigation_service(app: FastAPI, repo: IrrigationRepository, registry: DeviceRegistry | None, cloud: DeviceGateway | None) -> IrrigationService`.
  *(Rev 1 nit)* `app` is passed explicitly: the caller passes the `_app` global it read, so the helper does no hidden
  global read. The helper also does the `monitor.bind_repo(repo)` step in its original position, and reads
  `app.state.weather_client` / `plant_db` / `ntfy_notifier` in today's argument order.
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
- *(Rev 1, B2)* There is **no** `resolve_server_url`. The three `os.environ.get("IRRIGATION_SERVER_URL", …)` reads in
  `commands/_helpers`, `commands/auth` and `commands/tui` are pinned **per module** by the strict
  `tests/golden/contracts/env_reads.json` (`test_contract_settings.py:319-343` AST-scans `libs/`).

### 3.12 Definition of Done, and a verdict for every long function and large file *(rewritten in Rev 1)*

**Metrics (measured by `refactor/scripts/sizecheck.py`, plan T0.9):**
- **Body lines.** Lines from the first statement after the docstring to the end of the function. The `def` line,
  decorators, signature and docstring are not counted: route and Typer docstrings and signatures are frozen contract
  text.
- **CC.** McCabe complexity as ruff `C90` computes it.
- **Nesting.** The maximum depth of nested compound statements (`if`/`for`/`while`/`with`/`try`/`match`) inside the
  body.

**Per-task DoD.** Every function the task decomposes, and every helper it creates, ends with ≤ 40 body lines,
`ruff C90 max-complexity=8` silent, and nesting ≤ 3. Otherwise the commit message gives the reason and the task appends
a line to the exception register `refactor/size-exceptions.txt` (format `path::qualname — reason`).
*(Rev 2)* Implementers only **propose** register entries, in the hand-off note. Only the integrator commits them, after
orchestrator approval (`… — reason — approved: <orchestrator>`). I1 rejects any unapproved line.

**Per-WP DoD.** Every file the WP touched passes all of the following, or has register entries:
- `sizecheck` silent on the whole file (every function);
- `ruff check --isolated --select C90 --config "lint.mccabe.max-complexity=8"` silent;
- `mypy` strict-clean, with the file listed in `refactor/mypy-strict.txt`.

**Final DoD (I5).** `make sizecheck` over all of `libs/` (excluding `migrations/versions/`) is silent except register
entries. Every register entry carries a reason, and REFACTOR_NOTES lists it.

**Every production function > 40 total lines, or CC > 8, or nesting > 3** *(nesting column and 5 verdicts added in
Rev 2)*. There are 90 rows (91 with the nested `schedule_pump_watcher._run` added at T0.9): 82 functions over 40 total lines (the nested `register.login` is listed separately), 4
more with CC > 8 only, and 4 more with nesting > 3 only. Size is total/body lines. The data comes from an AST scan at
`6994bc0`; nesting uses the definition above, with `elif` at the same depth as its `if` and nested `def`s measured
separately.

Verdicts:
- `OK` = already within the DoD by all three metrics;
- `T…` = the plan task that brings it within the DoD;
- `EXC` = a register entry with this reason.

**Re-scan for nesting > 3 (Rev 2).** Exactly 14 functions exceed it. Each has a task (T2.x/T4.5/T5.7/T5.8/T6.5/T6.8/
T6.9/T6.12/T7.22/T8.18) or an EXC row (`ClusterScreen.compose`, `FormScreen.compose`, `_start_keepalive`,
`TuyaIrrigatorAdapter.status`, `bulk.stop_all_irrigators`). So T0.9's seeded checker lists only functions with a task.

| Function | Size | CC | Nesting | Verdict | How / why |
|---|---|---|---|---|---|
| `commands/auth.py::register` | 68/64 | 9 | 0 | **EXC** | Typer declarative registration block: CC/length come from the nested command defs, whose decorators/params/docstrings are the frozen `--help` contract; each nested command ≤ 20 body lines, CC ≤ 4; module untouched (B2) |
| `commands/auth.py::register.login` | 41/17 | ≤8 | 1 | **OK** | body ≤ 40 |
| `commands/operations.py::register` | 109/105 | 19 | 0 | **EXC** | same as `auth.register` (10 nested commands, each ≤ 12 body lines, CC ≤ 5); module untouched |
| `tui/model.py::summarize` | 69/61 | 12 | 3 | **T2.10** | `_band`, `_plant_views` (+ further private helpers until ≤ 40 / CC ≤ 8) |
| `tui/screens/cluster.py::ClusterScreen._load_insights` | 63/62 | ≤8 | 2 | **T2.4** | builders → `render.py` |
| `tui/screens/cluster.py::ClusterScreen._render_overview` | 63/62 | 11 | 3 | **T2.3** | builders → `render.py` |
| `tui/screens/cluster.py::ClusterScreen.action_delete` | 41/40 | 9 | 2 | **T2.7** | dict dispatch + per-tab handlers |
| `tui/screens/cluster.py::ClusterScreen.action_edit` | 51/50 | 10 | 2 | **T2.6** | dict dispatch + per-tab handlers |
| `tui/screens/cluster.py::ClusterScreen.action_new` | 43/42 | ≤8 | 2 | **T2.5** | dict dispatch + per-tab handlers |
| `tui/screens/cluster.py::ClusterScreen.compose` | 50/49 | ≤8 | 4 | **EXC** | single declarative widget tree, CC 1; order = focus order + `app.tcss` selectors + screen goldens; splitting scatters the DOM picture (C §8.2) |
| `tui/screens/forms.py::FormScreen.compose` | ≤40 | ≤8 | 5 | **EXC** | nesting 5 from nested `with` widget containers: declarative Textual tree whose order is focus order + tcss + form-screen goldens; module untouched (Rev 2) |
| `tui/screens/forms.py::parse_value` | ≤40 | 11 | 3 | **EXC** | ≤ 40 lines; CC 11 is a flat type-dispatch ladder over field kinds; module untouched by any WP |
| `tui/screens/settings.py::SettingsScreen.load` | 55/54 | ≤8 | 2 | **T2.9** | builders → `render.py` |
| `tui/screens/system.py::SystemScreen.load` | 80/79 | ≤8 | 1 | **T2.8** | builders → `render.py` |
| `tui/widgets.py::show_payload` | ≤40 | 9 | 2 | **T2.11** | `_draw_event_lines` / `_set_x_ticks` |
| `devices/gateway.py::DeviceGateway.get_device_logs` | 56/43 | ≤8 | 3 | **EXC** | devices = high risk; v1/v2 DPS parsing differs in `ValueError` handling (D29) — a merged helper is a behaviour fork; module untouched |
| `devices/gateway.py::DeviceGateway.get_live_reading` | 43/28 | ≤8 | 3 | **OK** | body ≤ 40 |
| `devices/irrigators/ik10pw.py::IK10PWAdapter._start_keepalive` | 51/43 | 10 | 4 | **EXC** | actuation safety path holding REFACTOR_NOTES safety bug #3 (`signal.signal` off main thread); any restructuring belongs to the dedicated fix PR |
| `devices/irrigators/ik10pw.py::IK10PWAdapter.read_health` | 45/30 | ≤8 | 1 | **OK** | body ≤ 40 |
| `devices/irrigators/tuya_generic.py::status` | ≤40 | 10 | 5 | **EXC** | ≤ 40 lines; CC 10 and nesting 5: DPS-decoding ladder in a device adapter (high risk, untouched) |
| `devices/sensors/tr301z.py::TR301ZAdapter.read_health` | 44/31 | ≤8 | 1 | **OK** | body ≤ 40 |
| `learning/issues.py::detect_conflicts` | 151/143 | 26 | 4 | **T6.8** | per-check helpers |
| `learning/issues.py::detect_issues` | 131/115 | 16 | 4 | **T6.9** | per-sensor alert helpers |
| `learning/profiling.py::compute_sensor_response` | 49/43 | ≤8 | 1 | **T6.10** | phase helpers (before/after series, response metrics) |
| `learning/profiling.py::get_plant_profile` | 51/42 | ≤8 | 2 | **T6.10** | phase helpers (event responses, aggregate profile) |
| `logic/cleaning.py::clean_readings` | 46/32 | ≤8 | 3 | **OK** | body ≤ 40 |
| `logic/engine.py::IrrigationLogic._apply_seasonal_multiplier` | 57/44 | ≤8 | 2 | **T8.10** | `_seasonal_overrides` (+ `_scaled_interval`) |
| `logic/engine.py::IrrigationLogic._apply_vacation_budget` | 77/55 | ≤8 | 1 | **T8.10** | `_vacation_days_left`, `_binding_max_minutes` |
| `logic/engine.py::IrrigationLogic._enforce_leak_hold` | 42/20 | ≤8 | 1 | **OK** | body ≤ 40 |
| `logic/engine.py::IrrigationLogic.decide_for_cluster` | 161/142 | 18 | 2 | **T8.2–T8.7** | §3.2 |
| `logic/engine.py::_apply_light_adjustment` | 41/40 | ≤8 | 1 | **OK** | body = 40 |
| `logic/engine.py::_apply_soil_moisture_rule` | 75/73 | ≤8 | 1 | **T8.9** | §3.3 (helpers stay in engine.py) |
| `logic/fallback.py::temperature_based_decision` | 80/67 | ≤8 | 2 | **T6.7** | `_config_fallback`, `_temperature_interval` |
| `logic/sensors.py::get_recent_sensor_data` | 62/56 | 9 | 3 | **T6.4** | `_mean_or_none` + `_per_sensor_snapshot` |
| `logic/stress.py::detect_stress_conditions` | 56/48 | 17 | 3 | **T6.6** | six pure detectors |
| `logic/trends.py::analyze_historical_trends` | 59/57 | 14 | 4 | **T6.5** | functional core |
| `plant_db.py::PlantDatabase.get_care_data` | 59/34 | ≤8 | 2 | **OK** | body ≤ 40 |
| `repository.py::IrrigationRepository.bulk_add_sensor_readings` | 42/22 | ≤8 | 1 | **OK** | body ≤ 40 |
| `repository.py::IrrigationRepository.move_plant` | 57/28 | ≤8 | 1 | **OK** | body ≤ 40 |
| `repository.py::IrrigationRepository.upsert_alert` | 55/33 | ≤8 | 2 | **OK** | body ≤ 40 |
| `stats.py::export_csv` | ≤40 | ≤8 | 4 | **T6.12** | nesting 4 → `_csv_event_row(event, irrigator) -> list[object]` + `_write_event_rows(writer, events, irrigator, cutoff)`; printed text verbatim (Rev 2) |
| `stats.py::get_irrigation_stats` | 46/44 | ≤8 | 2 | **T6.11** | aggregation helpers |
| `stats.py::print_stats_report` | ≤40 | 9 | 2 | **T6.11** | section helper |
| `sync.py::sync_sensor_data` | ≤40 | ≤8 | 4 | **T8.18** | nesting 4 → `_sync_logged(db, cloud, sensor, hours, stats) -> None` (the per-sensor try/except) + `_sync_summary(new, live) -> str`; log texts verbatim (Rev 2) |
| `sync.py::sync_single_sensor` | 56/52 | ≤8 | 3 | **T8.17** | `_sync_window_start`, `_store_history`, `_store_live_reading` |
| `app.py::create_app` | 132/130 | ≤8 | 1 | **T5.15** | §3.7 |
| `routes/auth.py::login` | 47/26 | ≤8 | 1 | **OK** | body ≤ 40 (frozen docstring not counted) |
| `routes/clusters.py::get_cluster_detail` | 42/14 | ≤8 | 0 | **OK** | body ≤ 40 |
| `routes/irrigators.py::add_irrigator` | 44/24 | ≤8 | 2 | **OK** | body ≤ 40 |
| `routes/operations.py::cluster_status` | 63/51 | ≤8 | 1 | **T5.9** | response mappers |
| `routes/operations.py::irrigate` | 43/11 | ≤8 | 1 | **OK** | body ≤ 40 |
| `routes/operations.py::stats_export` | 50/35 | ≤8 | 2 | **T5.5** | body → `cluster_events_csv` |
| `routes/plants.py::sync_plants` | 50/31 | 11 | 4 | **T5.7** | body → `ClusterService.sync_plants` |
| `scheduler.py::init_scheduler` | 49/37 | ≤8 | 0 | **OK** | body ≤ 40 |
| `services/alerts.py::sync_cluster_alerts` | 46/34 | ≤8 | 1 | **OK** | body ≤ 40 |
| `services/anomaly.py::SensorAnomalyService.scan` | 115/101 | ≤8 | 2 | **T4.14** | `_stale_alert`, `_drift_alert` |
| `services/bulk.py::stop_all_irrigators` | 46/27 | ≤8 | 4 | **EXC** | nesting 4 (for→try→if→try): emergency kill-switch actuation path, each irrigator isolated in its own try so one failure cannot stop the others; B-4 neighbourhood; module untouched (Rev 2 — was wrongly OK) |
| `services/charts.py::build_overlay_payload` | 60/49 | 11 | 3 | **T4.17** | `_bucket_readings`, `_overlay_datasets` |
| `services/cluster.py::ClusterService.get_cluster_status` | 56/54 | ≤8 | 1 | **T5.16** | `_sensor_status_rows`, `_irrigator_status` |
| `services/data_quality.py::build_report` | 124/114 | 13 | 2 | **T4.6** | per-entity collectors |
| `services/efficacy.py::score_cluster` | 63/53 | ≤8 | 3 | **T4.15** | `_event_items` |
| `services/forecast.py::ForecastService.predict_next_irrigation` | 112/105 | 11 | 3 | **T4.4** | §3.6 |
| `services/health.py::PlantHealthService.compute_score` | 100/81 | 12 | 3 | **T4.3** | §3.6 |
| `services/insights.py::InsightsService.cluster_insights` | 61/52 | ≤8 | 2 | **T4.9** | `_insight_from_alert` (+ helpers until ≤ 40) |
| `services/irrigation.py::IrrigationService.check_all_clusters` | 43/33 | ≤8 | 3 | **OK** | body ≤ 40 (T7.17 still extracts for CC/readability) |
| `services/irrigation.py::IrrigationService.check_cluster` | 44/42 | ≤8 | 2 | **T7.16** | `_check_result` |
| `services/irrigation.py::IrrigationService.monitor_cluster` | 62/60 | 10 | 2 | **T7.15** | `_latest_soil`, `_monitor_target_band`, `_soil_status` |
| `services/irrigation.py::IrrigationService.run_irrigation_pipeline` | 187/172 | 12 | 2 | **T7.10–T7.14 + EXC** | helpers per §3.1; the health-gate block stays inline (Rev-1 m5: T7.12 dropped), so the body ends ≈ 45 lines → register entry |
| `services/irrigation.py::_run_leak_check` | 42/33 | ≤8 | 2 | **OK** | body ≤ 40 |
| `services/irrigation.py::handle_watcher_interrupted` | 89/56 | ≤8 | 2 | **T7.18** | `_stop_auto_cycle`, `_left_running_message` |
| `services/irrigation.py::rearm_leak_checks` | ≤40 | ≤8 | 4 | **T7.22** | nesting 4 → `_rearm_from_events(repo, now) -> int` (the two loops + guards); try/except/finally, `now = int(_time.time())` and both log lines stay in `rearm_leak_checks` (Rev 2) |
| `services/irrigation.py::schedule_pump_watcher` | 102/77 | 12 | 2 | **T7.19** | `_watcher_tuning` + `_run_pump_watcher`; thin closure kept |
| `services/irrigation.py::schedule_pump_watcher._run` | 43/42 | ≤8 | 2 | **T7.19** | nested closure, measured separately; its body moves into `_run_pump_watcher`, the thin closure that stays must be ≤ 40 body lines (added at T0.9: the WP0 `sizecheck` found it missing from this table — orchestrator decision) |
| `services/leak.py::LeakDetectionService._evaluate_sensor` | 62/50 | ≤8 | 1 | **T7.21** | `_moisture_series`, `_pinned_high`, `_still_rising` |
| `services/leak.py::LeakDetectionService.check_after_irrigation` | 63/42 | ≤8 | 2 | **T7.20** | `_hold_activity` / per-sensor verdict helpers |
| `services/maintenance.py::collect_maintenance_alerts` | 71/69 | 11 | 5 | **T4.5** | per-check helpers |
| `services/manual_control.py::manual_log` | 47/21 | ≤8 | 0 | **OK** | body ≤ 40 |
| `services/manual_control.py::manual_start` | 62/33 | ≤8 | 1 | **OK** | body ≤ 40 |
| `services/manual_control.py::manual_stop` | 46/23 | ≤8 | 1 | **OK** | body ≤ 40 |
| `services/pump_watcher.py::PumpWatcherService._handle_trip` | 105/87 | ≤8 | 2 | **T4.12** | one private method per best-effort step |
| `services/pump_watcher.py::PumpWatcherService.watch` | 96/73 | ≤8 | 3 | **T4.10** | `_outcome` + `_poll_step` (caller keeps clock reads) |
| `services/search.py::search` | 118/103 | ≤8 | 1 | **T4.13** | `_cluster_hits`, `_plant_hits`, `_sensor_hits`, `_irrigator_hits` |
| `services/sync.py::SyncService._cluster_snapshot` | 49/26 | ≤8 | 3 | **OK** | body ≤ 40 |
| `services/system_health.py::SystemHealthService.pulse` | 57/50 | ≤8 | 2 | **T4.16** | `_sensor_devices`, `_overall_status` |
| `services/weather.py::WeatherClient.get_forecast` | 45/39 | ≤8 | 2 | **OK** | body ≤ 40 |
| `web/context.py::base_context` | 46/45 | ≤8 | 2 | **T5.17** | `_preference_flags`, `_auth_enabled` |
| `web/filters.py::cluster_caps` | 65/39 | ≤8 | 1 | **OK** | body ≤ 40 |
| `web/routes/clusters.py::cluster_detail` | 95/87 | ≤8 | 2 | **T5.10 + I2** | `_rationale_reasons`, `_window_rows` (+ helpers until ≤ 40) |
| `web/routes/irrigators.py::create_irrigator` | 46/34 | ≤8 | 1 | **OK** | body ≤ 40 |
| `web/routes/operations.py::sync_plants` | 43/36 | 11 | 4 | **T5.8** | body → `ClusterService.sync_plants` |
| `web/routes/plant_dashboard.py::plant_dashboard` | 77/68 | ≤8 | 3 | **T5.18** | context-section helpers |

**Files > 400 lines** (all become register entries; none is split; the reason is given per file):

| File | Lines (baseline) | Reason it stays > 400 |
|---|---|---|
| `greenhouse_core/repository.py` | 1309 | One persistence facade with ~80 shallow methods. The cost was copy-paste (removed by T3.x). A mixin split is rejected (D2): exception `__module__` hacks, MRO guard, near-full-suite runs per commit. |
| `greenhouse_core/schemas.py` | 999 | Cohesive DTO module. A split risks OpenAPI (`__module__`, docstrings, field order) for navigation only (D4). |
| `greenhouse_core/logic/engine.py` | 936 | One cohesive rule pipeline (orchestrator + rules). `tests/test_invariants_engine.py:232-238` pins ≥ 50 constant imports in `vars(engine)` (exactly 50 today, 39 used only by the rule functions), and tests patch `engine.time`, `engine.season_for` and resolve `engine.seasonal_light_factor`. Moving the rules out would turn a frozen test red (B1). |
| `greenhouse_cli/tui/screens/cluster.py` | 800 (≈ 560 after WP2) | One Screen = one `compose` tree, plus 15 `action_*` and 3 `on_*` handlers pinned per class by `tui/surface.json`. Per-tab controller objects were rejected (D22). |
| `greenhouse_server/services/irrigation.py` | 760 | The logger name (`test_migrations.py:149`, loggers golden), the `_time` seam, the lazy scheduler seams and four names tests call by path pin the pipeline and job plumbing here (D9). |
| `greenhouse_server/scheduler.py` | 560 | `func_ref` goldens pin the 5 jobs here. The job registry, tz rebuild and pause policy are one concern, and the public surface (`dir()`) is frozen. |
| `greenhouse_cli/client.py` | 464 | One thin method per `/api/v1` endpoint (≈ 80), 1:1 with OpenAPI. Per-slice mixins were rejected (proposal C §11). |
| `greenhouse_core/models.py` | 433 | Declarative ORM; DDL frozen. |
| `greenhouse_server/services/health_monitor.py` | 431 | One `DeviceHealthMonitor` class (poll/diff/record/backfill) plus `HEALTH_ALARM_TO_TRIGGER`, imported by the pipeline. Its logger name is pinned, so moving parts would rename it. |

## 4. Ports / Protocols that survive

| Port | Verdict | Evidence |
|---|---|---|
| `RainForecast` (`logic/engine.py`) | **KEEP** (typing only, not `runtime_checkable`) | Core cannot name the server's `WeatherClient` (layering), so `IrrigationLogic(weather_client=None)` and `ForecastService(weather_client=None)` are untyped today. **Three** structural implementations exist: `services/weather.WeatherClient`, `tests/golden.py::OfflineWeather`, `tests/engine_grid.py::FakeForecastWeather`. `ForecastService` imports it from the engine module (server → core is allowed). *(Rev 1)* Until WP8 lands, WP4 makes `forecast.py` strict-clean with an explicit `weather_client: Any = None`, which strict mypy allows; I3 tightens it to `RainForecast \| None`. |
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

### 7.3 Must stay separate (drifted — merging changes behavior)

Window validation (messages, punctuation and parse order differ); irrigator create and sensor create (web lacks
`IntegrityError` / the plant check, B-7); `check_all` `has_alerts` (B-13); vacation `starts < ends` (API POST
unvalidated, B-8); relative time (`filters.age_seconds` vs `plant_dashboard._relative_time`); moisture-target parsing
in `monitor_cluster` and both `issues.py` sites; the CLI `irrigator_add` vs `irrigator_update` config dict; the TUI
copies of weekday vocabulary (the package boundary forbids sharing); the TUI `_selected` variants (cluster/alerts/
settings return different types and messages, verified). *(Rev 1, B2)* Also the three CLI
`os.environ.get("IRRIGATION_SERVER_URL", …)` reads: each module's env read is pinned by the strict
`contracts/env_reads.json`. **General rule:** never move, merge or add an `os.environ` / `os.getenv` read. Any task
touching a file that contains one runs `tests/server/test_contract_settings.py`.

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
- **Name rule:** no new name may start with `action_`, `on_`, `_on_`, `watch_`, `_watch_`, `compute_`, `validate_` or
  *(Rev 1)* `key_`. No new method may shadow a name defined on its Textual base: guard test #4 (§11) checks each TUI
  `DOMNode` subclass against `dir()` of `textual.app.App` / `textual.screen.Screen` / `textual.widget.Widget`, with
  the baseline overlaps as the allow-list. That rules out new `render*` / `_render` / `refresh*` collisions.
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
12. *(Rev 1)* **Strict typing is a gate, not a goal.** Every module a task touches must be `mypy`-clean under the
    project config (`strict = true`) and listed in `refactor/mypy-strict.txt` by the end of that task. So the first
    task that touches a module in a WP is its "add type annotations" task.
13. *(Rev 1)* **Framework-boundary typing profile.** Some function signatures are introspected by a framework:
    FastAPI route functions (`greenhouse_server.routes.*`, `greenhouse_server.web.routes.*`) and Typer commands
    (`greenhouse_cli.commands.*`, `greenhouse_cli.main`). Their parameter annotations, defaults and **return
    annotations** feed OpenAPI, `response_model` inference (web routes have no explicit `response_model`, so a return
    annotation would become one), or `--help`.
    - Those modules get a mypy override (T0.4): `disallow_untyped_defs = false`, `disallow_incomplete_defs = false`
      and `disallow_any_generics = false`. Everything else stays strict.
    - Route and command signatures are **never** edited. Every helper in those modules is fully annotated (review
      rule). The rule is strengthened by `check_untyped_defs = true`, which `strict` already sets.
13b. *(Rev 2 nit)* **Annotation-only changes must not change import-time behaviour.** Put an import added only for
    typing under `if TYPE_CHECKING:` and use a string annotation, or use `from __future__ import annotations` (not in
    Pydantic/FastAPI modules, §10.11). Example: `Settings` stays lazily imported in `services/irrigation.py`, so
    `_watcher_tuning(settings: "Settings | None")` uses a string annotation with a `TYPE_CHECKING` import.
14. *(Rev 1)* **When strictness collides with a frozen contract** (a Pydantic field type, a route signature, a Textual
    override), do not change the contract. Use a narrowly scoped `# type: ignore[<code>]  # contract: <what is frozen>`
    (never a bare `type: ignore`). Each one is listed in the commit body.
15. *(Rev 1)* **Size and complexity** follow the Definition of Done in §3.12 (≤ 40 body lines, CC ≤ 8 by ruff C90,
    nesting ≤ 3), enforced per task and per WP. Exceptions go only through `refactor/size-exceptions.txt`, with a
    reason.

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
name = "Inside logic: engine orchestrates; helpers/values never import it"
type = "layers"
layers = [
  "greenhouse_core.logic.engine",
  "greenhouse_core.logic.fallback | greenhouse_core.logic.sensors | greenhouse_core.logic.stress | greenhouse_core.logic.trends",
  "greenhouse_core.logic.timing | greenhouse_core.logic.cleaning | greenhouse_core.logic.plant_needs",
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
- **Verified (Rev 1, M1).** As written above, minus the pending `tui.render` entry, the 10 contracts **pass on
  today's code**: the critic ran them, with 10 kept, 0 broken, and the 2 + 1 listed ignores. `|` siblings are
  independent, and there are no edges among `timing`/`cleaning`/`plant_needs` or among
  `fallback`/`sensors`/`stress`/`trends` (baseline graph). `logic.rules` no longer exists in the design.
- **Pending module.** `tui.render` does not exist yet, and import-linter rejects unknown modules in `source_modules`.
  So T0.2 leaves it out, and the integrator adds it (plan I1) after WP2 merges; `pyproject.toml` is integrator-owned.
- **Fallback.** If the `layers` contract for the server fails on an edge not seen in the graph, replace it with the
  equivalent forbidden contract (services must not import routes/web/app/deps) and record why. Never change code to
  satisfy a contract in Phase 0.

**Ruff ratchet** *(Rev 1: threshold 8, tests exempt)*. Add `extend-select = ["C90", "PLR0911", "PLR0912", "PLR0915"]`
with **`max-complexity = 8`**. `PLR0913` is not enabled: 61 hits, mostly frozen route and Typer signatures.
- Per-file ignores list today's `libs/` offenders **at threshold 8**: the 27 C901 hits listed in §3.12, plus the
  PLR0911/12/15 hits from `refactor/baseline/ruff-complexity-rules.txt`.
- `"tests/**" = ["C90", "PLR0911", "PLR0912", "PLR0915"]` (M2). The Phase-1 files `tests/engine_grid.py`,
  `tests/test_invariants_engine.py` and `tests/cli/test_contract_tui.py` exceed the limits, and tests are frozen.
- A WP must leave every file it touched free of these rules **without** its per-file ignore (verify with
  `uv run ruff check --isolated --select C90,PLR0911,PLR0912,PLR0915 --config "lint.mccabe.max-complexity=8" <file>`),
  except register entries. The integrator deletes the ignore in I1.
- Not enabled:
  - `TID251` on `time.time`: verified that ruff flags *every* `time.time()` call, not just `from time import time`;
  - `PLC0415`: lazy imports are deliberate;
  - `D`: would force docstrings on Pydantic classes;
  - `ARG`: FastAPI dependency params are unused by design;
  - `PLR2004`.

**Mypy ratchet** *(Rev 1: a per-task gate)*. Add `mypy` to the dev group.
- `[tool.mypy]` in `pyproject.toml` sets `strict = true`, `ignore_missing_imports = true`, `follow_imports = "silent"`
  and `python_version = "3.11"`, plus the framework-boundary override (§10.13). It has **no** `files` key.
- The strict list lives in **`refactor/mypy-strict.txt`**: one source path per line, sorted, append-only.
  - It is seeded with the 35 modules that are clean at baseline.
  - Each "add type annotations" task appends the modules it made clean, in the same commit.
  - When two worktrees both append, resolve the merge conflict with **union + sort**.
- `make typecheck` = `uv run mypy $(grep -v '^#' refactor/mypy-strict.txt)` must pass.
- **Per-task gate:** `uv run mypy <every .py file the task touched>` must be clean. The strict list never shrinks.
- The default-mode error count over all three packages (baseline 176) is report-only and may only go down.

**Size checker** *(Rev 1)*. `refactor/scripts/sizecheck.py` (plan T0.9) is AST-based and reports functions over 40
body lines or nesting deeper than 3 in the given files. It skips entries listed in `refactor/size-exceptions.txt` and
prints them as "excepted: reason". `make sizecheck FILES=…` defaults to all of `libs/`. *(Rev 2)* T0.9 asserts that
the seeded register plus the task list cover every hit: after seeding, `make sizecheck` lists only functions that have
a task in the plan (T0.9 checks the list against §3.12).

**Mutation testing** *(Rev 1)*. `mutmut` is added to the dev group (T0.1) and configured in T0.10 for the
decision-critical modules: `logic/{engine,stress,trends,sensors,fallback}.py`, `learning/issues.py`,
`services/{irrigation,leak,pump_watcher}.py`, `scheduler.py` and *(Rev 2)* `greenhouse_core/sync.py` (invariant 8;
targets in `10-safety-ingress-devices.md`).
- **Two runs per module.** One on the **pre-refactor** module, before the WP touches it; one after the WP. Each run
  uses the WP's task subsets and is restricted to the "mutation targets" lines in `refactor/10-safety-*.md`.
- **Threshold.** At least **75 % killed on touched code**, and no mutant killed in M-pre survives in M-post.
  *(Rev 2)* Mutants are compared by **identity**, not line number. The identity is (mutation operator, original
  snippet → replacement snippet, enclosing function qualname). The WP records a function map (old qualname → new
  qualname(s)) in its hand-off note, so an identity whose code moved into an extracted helper is matched through the
  map.
  Surviving baseline mutants are killed by new characterization tests (orchestrator-owned) or justified in writing
  before the WP starts.
- **Budget.** ≤ 45 min wall per module per run (`--max-children 2`, under the test lock). Over budget, use a seeded
  sample of 150 mutants from the target lines.
- **Fallback** if mutmut cannot run against the `libs/` workspace layout (T0.10 decides on `logic/stress.py`):
  `refactor/scripts/mutate_probe.py`. It applies a fixed catalogue of single-token mutations, one at a time: comparison
  flip, `and`↔`or`, ±1 on numeric literals, `True`↔`False`, statement deletion. *(Rev 2)* It mutates **in place, in
  the implementer's own worktree**: the editable install points at `libs/`, so temp copies would never be imported.
  It restores each file with `git checkout -- <file>` in a `finally`, and refuses to start if `git status --porcelain`
  for the target file is not clean. It runs the subset with `-x`, `PYTHONHASHSEED=0`, under the test lock, and reports
  killed/survived per identity. The same thresholds apply.

**Guard tests** (new, test-only, `tests/test_refactor_guards.py`):
1. Importing `greenhouse_cli.main` does not put `textual` into `sys.modules`. This runs **in a subprocess**
   (`sys.executable -c …`), because in-process other tests have already imported `textual` (Rev 1 nit).
2. An AST scan of `libs/**/greenhouse_*/**/*.py` finds no `from time import time`.
3. An AST scan of `greenhouse_cli/tui/**/*.py` finds no module-level public UPPERCASE assignment outside the names
   already in `tests/golden/tui/surface.json` (it fails fast, before the golden does).
4. *(Rev 1)* No TUI `DOMNode` subclass defines a name that shadows `dir()` of its Textual base class (`App`, `Screen`,
   `ModalScreen`, `Widget`), beyond an allow-list hard-coded from the baseline overlaps.
5. *(Rev 2 nit)* Every `_`-prefixed function in the boundary-profile modules (`greenhouse_server.routes.*`,
   `greenhouse_server.web.routes.*`, `greenhouse_cli.commands.*`) is fully annotated (all params + return). This turns
   §10.13's review rule into a gate. Helpers already unannotated at baseline go in a hard-coded allow-list that only
   shrinks.

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
- [ ] *(Rev 1)* DoD: every function the task decomposed and every new helper is ≤ 40 body lines, has CC ≤ 8 (ruff
  C90 @ 8 silent) and nesting ≤ 3, or has a register entry with a reason.
- [ ] *(Rev 1)* `uv run mypy <touched files>` is clean, the touched modules are in `refactor/mypy-strict.txt`, and any
  `type: ignore` carries a `contract:` reason.
- [ ] *(Rev 1)* The test subset included every strict golden that renders the touched code (plan §0.3 rule) and,
  for env-reading files, `test_contract_settings.py`.

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
| D10 | Engine pure rules | stay in `engine.py` | move to `logic/_rules.py` | stay | ~~Move to public `logic/rules.py`~~ → **Rev 1: stay in `engine.py`**, decomposed in place | Reversed after the critic's B1. `tests/test_invariants_engine.py:232-238` requires ≥ 50 constants mirrored in `vars(engine)`. There are exactly 50 today, and 39 are used only by the rule functions, so moving them would turn a frozen test red. Keeping the names alive with `# noqa: F401` would game the test. `engine.py` > 400 lines becomes a documented exception (§3.12). |
| D11 | Engine gates | `_pre_gates` (no quiet) + quiet in body | `_pre_evaluation_gates -> _Gate(terminal, quiet_window)` | `_pre_gate` + quiet in body | **A/C** | The quiet window is needed later for the override reason. Keeping it a local avoids a two-field NamedTuple. |
| D12 | Light rule gets `light_factor` param | no | yes | no | **No** | B's version calls `seasonal_light_factor()` unconditionally (today: only when `avg_light is not None`), which changes the call pattern. |
| D13 | Trim `_decision_with_reason` unused params | — | yes | yes | **Yes** | All 6 callers are in engine and none pass them (grep); the body is identical. |
| D14 | `_evaluate_rules` cluster id | `cluster.id` | explicit | explicit | **Explicit `cluster_id`** | No equivalence reasoning needed. |
| D15 | Pipeline skip-activity helper | `_record_skip(severity=, payload=)` | `_log_decision_skip(cluster_id, decision)` | same as B | **B/C** | Avoids A's "omit payload when None" kwargs juggling. The health block keeps its own explicit call. |
| D16 | Pipeline actuation params | 9-param `_actuate` | 7 params + result | `_Actuation` dataclass | **C's `_Actuation`** | Parameter-object standard (§10.3). |
| D17 | Shared quiet-window helper name | `quiet_window_at` | — | `active_quiet_window` | **`active_quiet_window`** | Reads as what it returns (the window if active, else None). |
| D18 | Breadth of API/web dedupe | sync_plants, CSV, weekdays, quiet, exact `require_cluster` | sync_plants, CSV | + window_violation enum, vacation_range_is_valid, create_irrigator_with_capacity, require_found + in-cluster lookups, additive repo reads, web plant form mapper | **A's set + C's web plant form mapper + the exact `session.get` reads** (Rev 1: minus the CLI server-URL helper, R2) | The rest merges drifted code (B-7/B-8) or adds ceremony bigger than the duplicate (6-line validators, one-line range check). New in-cluster lookups change SQL shape. |
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

### Revision 1 (Gate 2 critique `refactor/50-adversary-plan-critic.md`; orchestrator decisions B1/B2/M1–M5/minors)

| # | Finding | Change made (this doc / plan) |
|---|---|---|
| R1 | **B1:** `logic/rules.py` vs `test_invariants_engine.py` ≥ 50 constants | `logic/rules.py` is **dropped** (§0, §2, §2.1, §2.2, §3.2, §3.3, §11, D10). The rule functions stay in `engine.py` and get private helpers there. Old plan T8.13/T8.14 are dropped. `engine.py` > 400 lines is a register entry (§3.12). No frozen test is edited. |
| R2 | **B2:** `resolve_server_url` vs strict `env_reads.json` | It is **dropped** (§2, §3.11, §7.2). Added to §7.3 as "env reads never move". Old T1.1 is dropped. The plan adds `test_contract_settings.py` to `$CLI`, and to every task that touches a file with an env read. |
| R3 | **M1:** import-linter contracts | `rules` is removed from the "Inside logic" layers. The 10 T0.2 contracts are as the critic verified them (10 kept). Only `tui.render` stays pending for I1 (§11). |
| R4 | **M2:** ruff ratchet fails on `tests/` | Per-file ignore `"tests/**" = [C90, PLR0911, PLR0912, PLR0915]` (§11, plan T0.3). |
| R5 | **M3:** golden-blind subsets | Plan §0.3 adds a subset-construction rule: importers **plus** every strict golden whose route or screen renders the code. It defines `$RENDER` and `$CHECK` aliases. **All** tasks were re-audited (§12 checklist item). |
| R6 | **M4:** Definition of Done not enforceable | §3.12 rewritten: metric definitions, per-task / per-WP / final DoD, the `sizecheck` script + exception register, and an explicit verdict for **all 82 functions > 40 lines and all 27 CC > 8 hits (4 of them short)**, plus all 9 files > 400 lines. Ruff threshold 8 (§11). Strict mypy is a per-task gate, with a per-module "add type annotations" task first in every WP (§10.12–14, §11). New decomposition tasks: `search`, `anomaly.scan`, `efficacy.score_cluster`, `system_health.pulse`, `build_overlay_payload`, `_handle_trip`, `watch`, `get_cluster_status`, `base_context`, `plant_dashboard`, `leak.*`, `profiling.*`, `stats.*`, `sync.sync_single_sensor`, and a full `schedule_pump_watcher` split. |
| R7 | **M5:** concurrency | At most 2 active worktrees (disjoint, never both high-risk). Every test run goes through `flock /tmp/greenhouse-tests.lock` with `-n 2`. Gates are re-timed, and there is a flaky-red rule (plan §0, §1). |
| R8 | **m1:** T7.16 dynamic TypedDict key | `cast(CheckResult, …)` with the reason written as a comment (§3.1). |
| R9 | **m2:** P0 | The orchestrator tags `refactor-gate1`; the plan only references it. |
| R10 | **m3:** mutation passes undefined | `mutmut` goes in the dev group (T0.1) and is configured and validated in T0.10. Pre-/post-WP runs, ≥ 75 % killed on touched lines, a 45-min budget with seeded sampling, and the scripted `mutate_probe.py` fallback (§11). |
| R11 | **m4:** T5.15 auth wiring | G+ with a full-suite run at the end of T5.15. |
| R12 | **m5:** T7.12 `if alarms:` vs `if blocked:` | **T7.12 is dropped.** The health gate stays inline and verbatim. `run_irrigation_pipeline` ends ≈ 45 body lines, which is a register entry (§3.1, §3.12). |
| R13 | Nits | The two sequential terminal `if`s are kept, with no `or` (§3 rules, §3.2). `_build_irrigation_service(app, …)` takes `app` explicitly (§3.8). Guard #1 runs in a subprocess, and guard #4 (Textual shadowing) and the `key_` prefix are added (§8, §11). The CLAUDE.md-vs-BRIEF plugin-docs conflict is flagged for the PR body (plan I4). |
| R14 | Found while revising | `schedule_pump_watcher` reads its tuning at **run** time and captures `wait_for_shutdown` / `shutdown_requested` at **schedule** time. `_run_pump_watcher` preserves both (§3.1). The forecast `weather_client` is typed `Any` until I3 (§4). |

### Revision 2 (Gate 2 round 2 — `refactor/50-adversary-plan-critic.md` "Round 2")

| # | Finding | Change made |
|---|---|---|
| R2-1 | **M-R2-1:** "rerun once" can launder a real regression; hash seed unpinned | Plan §0.2/§0.3/§0.4: every `t` / `FULL` / gate run has `PYTHONHASHSEED=0`. WP gates and the integration gate add one extra `FULL` with `PYTHONHASHSEED=12345`. A red is re-run with the **identical** command (same subset, `-n`, seed), never isolated. It counts as flaky only if the test is on the Gate-1 flaky list (orchestrator-owned, initially empty) or the same failure reproduces on the parent commit with the same command. Otherwise revert. |
| R2-2 | **M-R2-2:** no nesting axis in §3.12 | §3.12 gets a Nesting column and a full re-scan (14 functions with nesting > 3, all covered). New verdicts: `rearm_leak_checks` → T7.22 (§3.1); `sync.sync_sensor_data` → T8.18; `stats.export_csv` → T6.12; `bulk.stop_all_irrigators` → EXC (the wrong OK is fixed, §3.6); `FormScreen.compose` → EXC. T0.9 asserts the seeded checker lists only functions with a task. |
| R2-3 | m-R2-1: self-certifying register | Implementers only propose entries; the integrator commits them after orchestrator approval; I1 rejects unapproved lines (§3.12). |
| R2-4 | m-R2-2: M-post compared by line | Mutant identity = (operator, original → replacement snippet, enclosing function qualname), matched through the WP's function map (§11). |
| R2-5 | m-R2-3: subset slips | Plan T5.18, T7.20/T7.21 and T8.17 now include `D(<module>)`, following the §0.3 rule. |
| R2-6 | m-R2-4: `sync.py` mutation | Added to the mutation list (§11, plan §0.5). |
| R2-7 | m-R2-5: probe temp copies | The probe mutates in place in the implementer's own worktree and restores via `git checkout -- <file>` in `finally` (§11). |
| R2-8 | Nits | §10.13b: imports added only for typing go under `TYPE_CHECKING` or future annotations. Guard test 5 checks annotations on boundary-module helpers (§11). Wave B: T5.15 gets its own two reviewers in addition to WP7's (plan §1). |


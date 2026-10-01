# 20 — Proposal A: Minimalist (YAGNI-first)

Author: Architect A. Inputs: `BRIEF.md` (owner decisions binding: keep `libs/` workspace, method-level cleanup is
first-class), `CLAUDE.md`, `00-map.md`, `00-smells.md`, `00-contracts.md`, `00-baseline.md`, `00-tests.md`, and the
hotspot code read for this proposal. Read-only on code; nothing was run except `grep`/`sed` over the tree.

## 0. Thesis in five lines

1. **Decompose in place.** Almost every smell in this repo is a *long function*, not a misplaced function. Extract
   private helpers **inside the same module**. That keeps logger names (`getLogger(__name__)`), monkeypatch seams
   (`irrigation_mod._time`, `engine_mod.time`, `repo_mod.time`, `issues.seasonal_light_factor`, `sched_mod.logger`)
   and import paths stable for free.
2. **Zero public import paths move → zero shims.** Two new modules only (`web/weekdays.py`, `tui/render.py`), both
   hold private code from 3+ copies or from a god screen. No new layers, packages, base classes or registries.
3. **Merge only byte-identical duplicates.** Drifted copies (00-smells §A10, bugs B-7/B-8/B-13) stay separate:
   merging them is a behavior change.
4. **Typed interfaces without runtime change:** `TypedDict` for the service dicts, a frozen `dataclass`
   parameter object inside the engine, `cast`-based typed wrappers in the CLI client. Pydantic schemas untouched.
5. **Engine, scheduler and devices last**; devices get docstring fixes only.

---

## 1. Target tree (only what changes)

Legend: `~` = edited in place (private helpers added, public signatures unchanged), `+` = new module, `=` = untouched
but named because a reader might expect it to move.

```
libs/greenhouse-core/greenhouse_core/
  constants.py              ~  + ~45 new names (values copied verbatim from call sites, §4)
  utils.py                  =  NOT split. Only change: `NIGHT_LUX_THRESHOLD` and the month table now *imported*
                               from constants (same objects; utils keeps both names → surface unchanged)
  repository.py             ~  private _patch/_delete/_page helpers; public methods unchanged
  logic/engine.py           ~  decide_for_cluster split into private methods; soil rule split; _RuleInputs dataclass
  logic/timing.py           ~  + public quiet_window_at() (pure) used by engine AND web cluster_detail
  logic/plant_needs.py      ~  + public moisture_target_range(care) (wraps the 3 byte-identical call sites)
  logic/trends.py           ~  private helpers
  logic/stress.py           ~  private helpers
  logic/sensors.py          ~  _mean_or_none helper; `> 15` → NIGHT_LUX_THRESHOLD
  logic/fallback.py         ~  constants only (+ _base_interval helper)
  learning/issues.py        ~  detect_issues / detect_conflicts split into private per-check helpers (same module!)
  devices/**                =  docstring corrections only (stale `dp_parsers` claims); no code change in this plan
  schemas.py, models.py     =  untouched (OpenAPI/DDL frozen)

libs/greenhouse-server/greenhouse_server/
  app.py                    ~  create_app → 6 private helpers; module constants _OPENAPI_TAGS, _PROTECTED_API_ROUTERS
  scheduler.py              ~  private _job_session(app, failure_message) context manager for the 5 job bodies;
                               literals → constants; CHECK_ALL_JOB_ID declared before first use
  services/irrigation.py    ~  pipeline/monitor/check split into private methods; TypedDict results.
                               Job plumbing STAYS here (logger name + `_time` seam + lazy-import cycle)
  services/cluster.py       ~  + ClusterService.sync_plants(...), + cluster_events_csv(...)
  services/health.py        ~  compute_score helpers
  services/forecast.py      ~  predict_next_irrigation helpers
  services/maintenance.py   ~  per-check helpers
  services/data_quality.py  ~  per-entity issue collectors
  services/charts.py        ~  _threshold_for_cluster de-duplicated branch
  services/insights.py      ~  _insight_from_alert (removes the in-file jscpd clone 43-69)
  services/pump_watcher.py  ~  _outcome(...) dict builder
  services/health_monitor.py~  _raise_if_not_open (backfill duplicate block)
  routes/plants.py          ~  sync_plants body → ClusterService.sync_plants (name/docstring/response_model untouched)
  routes/operations.py      ~  stats_export body → cluster_events_csv
  web/weekdays.py           +  WEEKDAY_BITS, WEEKDAY_LABELS, FULL_WEEKDAY_MASK, format_weekday_mask (3 identical copies)
  web/routes/{clusters,configs,windows}.py  ~ import from web/weekdays (private aliases kept, see §1.2)
  web/routes/operations.py  ~  sync_plants body → ClusterService.sync_plants
  web/routes/analytics.py   ~  cluster_stats_export body → cluster_events_csv
  web/routes/clusters.py    ~  quiet_active_now via logic.timing.quiet_window_at (after the engine step)

libs/greenhouse-cli/greenhouse_cli/
  client.py                 ~  _object()/_array() typed wrappers over self._request; _drop_none(); docstrings
  commands/_helpers.py      ~  + resolve_server_url(ctx) (3 copies of env/default resolution)
  commands/{auth,tui}.py    ~  use resolve_server_url
  tui/render.py             +  pure payload → Rich renderables (table rows / panel Text) for ClusterScreen + SystemScreen
  tui/screens/cluster.py    ~  row/text builders moved to tui/render.py; CRUD if/elif → per-tab private methods
  tui/screens/system.py     ~  load() uses tui/render.py builders
  tui/model.py              =  stays "dataclass summaries" (summarize gets 2 small helpers, §2.13)
```

Single responsibility of each new/changed module:

| Module | One job |
|---|---|
| `web/weekdays.py` (new, ~20 LOC) | Weekday bitmask vocabulary + label formatting for web templates. Pure constants/functions, no imports beyond typing. |
| `tui/render.py` (new, ~250 LOC) | Turn API payload dicts into `refill()`-ready rows `(key, cells)` and Rich `Text` panels. No widgets, no I/O, no Textual import. |
| `logic/timing.py` (+1 fn) | Already "local-time gating"; `quiet_window_at` is exactly that concern. |
| `logic/plant_needs.py` (+1 fn) | Already "plant care data interpretation"; `moisture_target_range` belongs there. |
| `services/cluster.py` (+2) | Already "cluster status and history services"; plant-DB sync per cluster and events CSV are cluster history/maintenance. |
| `commands/_helpers.py` (+1) | Already "shared CLI helpers". |

### 1.1 Re-export shims

**None needed.** No public name changes module. Explicitly:

- `greenhouse_core.utils`: keeps `NIGHT_LUX_THRESHOLD`, `seasonal_light_factor`, `effective_light_threshold`,
  `daytime_lux_readings`, `format_timestamp`, `get/set_display_timezone`, `UTC` — definitions stay in `utils.py`;
  only the two literals become `from greenhouse_core.constants import NIGHT_LUX_THRESHOLD, SEASONAL_LIGHT_FACTOR_BY_MONTH`
  and `_SEASONAL_LIGHT_FACTOR = SEASONAL_LIGHT_FACTOR_BY_MONTH`. `learning/issues.py` keeps
  `from greenhouse_core.utils import daytime_lux_readings, effective_light_threshold, seasonal_light_factor`
  (by-name import → the `mock.patch("greenhouse_core.learning.issues.seasonal_light_factor")` seam keeps working
  because every new helper in `issues.py` resolves the global at call time).
- `greenhouse_core.schemas`, `repository`, `models`, `plant_db`, `sync`, `stats`, `database`, `auth`: no moves.
- `greenhouse_server.{app,config,deps,auth,scheduler}`: no moves. `app.py` keeps every current module-level import
  (`activity`, `alerts`, …, `Depends`, `FastAPI`…) because the Phase-1 import-surface golden (G10) snapshots `dir()`.
- `greenhouse_server.services.irrigation`: `handle_watcher_interrupted`, `schedule_pump_watcher`, `rearm_leak_checks`,
  `IrrigationService`, `CHECK_FAILED_ALERT_CODE`, `WATCHER_SHUTDOWN_ACTIVITY_CODE`, `LEAK_CHECK_ACTIVITY_CODE` stay.

### 1.2 Names patched/imported by tests at their attribute path (must keep resolving)

| Seam | Constraint this plan honours |
|---|---|
| `services/irrigation.py: import time as _time` (`irrigation_mod._time`) | `started_at = int(_time.time())` stays in `irrigation.py` inside the new `_actuate` helper; `rearm_leak_checks` untouched. |
| `engine_mod.time`, `repo_mod.time` | `import time` + `time.time()` attribute calls kept; ruff `TID251` bans `from time import time` (§8). |
| `greenhouse_core.logic.engine.season_for` | `_apply_seasonal_multiplier` stays in engine.py and keeps calling the module-global `season_for`. |
| `learning.issues.{seasonal_light_factor,effective_light_threshold}` | New helpers live in `issues.py`, call the module globals. |
| `sched_mod.logger` | `_job_session` lives in `scheduler.py` and calls `logger.exception(...)` through the module global; messages byte-identical. |
| `IrrigationService.check_cluster` (class attr) | `check_all_clusters` keeps calling `self.check_cluster(...)`. |
| `leak_mod.LeakDetectionService.check_after_irrigation` | `_run_leak_check` untouched. |
| `client._request` replaced on the instance (`tests/cli/tui_fixtures.py:189-197`) | New `_object`/`_array` call `self._request(...)` (instance lookup) — the serialising wrapper still intercepts every call. |
| `greenhouse_cli.tui.run`, `httpx.Client.__init__` | untouched. |
| Logger `greenhouse_server.services.irrigation` read by `tests/test_migrations.py:149` | No logging code leaves `irrigation.py`. |
| `ik10pw_module.time` (SimpleNamespace(sleep,time)), `gateway.tinytuya`, `devices.tinytuya` | devices code untouched. |
| Private web helpers `_format_weekday_mask`, `_WEEKDAY_BITS`, `_WEEKDAY_LABELS` | No test references them (grep), but keep module-private aliases (`_format_weekday_mask = format_weekday_mask`, etc.) for one commit-free safety margin; delete only if a reviewer prefers. |

---

## 2. Method-by-method decomposition

Notation: `→` new private function/method; every signature is typed; "order" notes how short-circuit/I/O order is
preserved. All new helpers are private (`_`-prefixed) unless stated.

### 2.1 `IrrigationService.run_irrigation_pipeline` (services/irrigation.py:422-608, 187 lines, CC 21)

Public signature, return *shape*, key insertion order and all side effects unchanged. Return annotation becomes the
`PipelineResult` TypedDict (runtime: still a plain `dict`).

```python
class PipelineResult(TypedDict, total=False):   # module-level, irrigation.py
    action: Required[str]; reason: Required[str]; confidence: Required[float]
    duration_minutes: int; interval_hours: int; stress_indicators: dict[str, Any]
    reasons: list[dict[str, Any]]; temperature: float; temperature_source: str; blocking_alarms: list[str]
```

| New helper | Signature | One line |
|---|---|---|
| `_error_result` (module fn) | `(reason: str) -> PipelineResult` | `{"action": "error", "reason": reason, "confidence": 0}` — the two early exits (L439, L453). |
| `_decide` | `(self, cluster_id: int, temp: float, *, force: bool) -> IrrigationDecision \| None` | Build `IrrigationLogic(self._repo, self._plant_db, weather_client=self._weather)` and call `decide_for_cluster(..., persist=True, triggered_by="manual" if force else "auto", bypass_quiet_hours=force)`. |
| `_decision_result` (module fn) | `(decision: IrrigationDecision, temp: float, source: str) -> PipelineResult` | The 9-key dict at L455-465, same key order. |
| `_record_skip` | `(self, cluster_id: int, message: str, *, severity: str = "info", payload: dict \| None = None) -> None` | The `decision_skip` activity row (used at L469 and L513; the device-health call passes `severity="warning"` and its payload). `payload=None` must be *omitted* from the repo call when None to stay byte-identical with L469 — implement as `**({"payload": payload} if payload is not None else {})` or two explicit calls; reviewer picks, tests prove. |
| `_resolve_adapter` | `(self, cluster_id: int) -> tuple[Irrigator, AbstractIrrigatorAdapter] \| str` | L480-495: returns the pair or the exact error reason string (`"no irrigators found"`, `"no device registry"`, `f"no adapter for irrigator: {exc}"`). Query only. |
| `_with_error` (module fn) | `(result: PipelineResult, reason: str) -> PipelineResult` | Mutates `action`/`reason` in place and returns the same dict (keeps identity + key order exactly like L482-484). |
| `_blocking_alarms` | `(self, irrigator: Irrigator) -> list[HealthAlarm]` | L502-503 query: `[]` when no monitor or not blocked. |
| `_apply_health_block` | `(self, cluster_id: int, irrigator: Irrigator, decision: IrrigationDecision, alarms: list[HealthAlarm], result: PipelineResult) -> PipelineResult` | L505-529 in the same order: add Reason → set SKIP → activity row (message = `decision.reason_text` computed **after** the reason was added) → update `result` keys. |
| `_actuate` | `(self, cluster_id: int, irrigator: Irrigator, adapter: AbstractIrrigatorAdapter, decision: IrrigationDecision, result: PipelineResult, *, temp: float, source: str, sensor_data: dict \| None) -> PipelineResult` | L531-608: `adapter.start` → `started_at = int(_time.time())` (after start, as today) → event row → success/failure branch. |
| `_event_notes` (module fn) | `(temp: float, source: str, sensor_data: dict \| None, decision: IrrigationDecision) -> str` | L536-553 soil-note + notes f-string verbatim. |
| `_on_started` | `(self, cluster_id: int, irrigator: Irrigator, decision: IrrigationDecision, started_at: int) -> None` | L557-585: activity `irrigated` → `set_decision_actuated` → `_schedule_leak_check` → `schedule_pump_watcher` → `maybe_notify`. (`result["action"] = "irrigated"` stays in `_actuate`.) |
| `_on_start_failed` | `(self, cluster_id: int, irrigator: Irrigator, output: str) -> None` | L587-604: activity `actuation_failed` + `raise_alert`. |

Resulting body (≈25 lines, CC ≈ 6):

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
        self._record_skip(cluster_id, decision.reason_text)
    return result
target = self._resolve_adapter(cluster_id)
if isinstance(target, str):
    return _with_error(result, target)
irrigator, adapter = target
alarms = self._blocking_alarms(irrigator)
if alarms:
    return self._apply_health_block(cluster_id, irrigator, decision, alarms, result)
return self._actuate(cluster_id, irrigator, adapter, decision, result, temp=temp, source=source, sensor_data=sensor_data)
```

Order preserved: the guard order (cluster → temperature → engine → dry-run/skip → irrigator → registry → adapter →
health → start) and every repo/adapter/notifier call happen in the original sequence. The stale comment at L497-501
(bug B-6) is rewritten to say the block is **not** re-persisted (comment only; recorded in REFACTOR_NOTES). The string
error signalled to `routes/operations.py:149` (`"cluster not found"`) is unchanged.

`_resolve_temperature` (L393, CC 12): split into `_indoor_temperature(sensor_data) -> tuple[float, str] | None` and
`_outdoor_temperature(sensor_data) -> tuple[float, str] | None`; order kept: `ensure_fresh_and_read` first, weather
only when needed exactly as today; final fallback `FALLBACK_TEMPERATURE_C` with the literal label `"fallback (20C)"`.

### 2.2 `IrrigationService.monitor_cluster` (L610, CC 19), `check_cluster` (L673), `check_all_clusters` (L718)

- `monitor_cluster` → `_latest_soil(readings: list[SensorReading]) -> float | None`,
  `_monitor_target(care: dict) -> tuple[float, float]` (**keeps its own parse**: `(float(x) for x in raw.split("-"))`
  with broad `except Exception` → `(45.0, 65.0)`; NOT folded into `moisture_target_range`, because "45-65-70" parses
  differently — drifted copy), `_soil_status(latest: float | None, t_min: float, t_max: float) -> str` (pure, the
  5-way ladder with `MONITOR_VERY_DRY_MARGIN`, `MONITOR_WET_MARGIN`). Loop body stays in the method; dict key order kept.
- `check_cluster`: guard-clause reorder only is **not** allowed if it changes call order — today `collect_learning_alerts`
  and `collect_maintenance_alerts` run before the irrigator branch; keep that. Extract
  `_check_result(cluster_id: int, name: str, action: str, alerts: list[dict], maintenance: list[dict], **detail: Any) -> CheckResult`
  building `{"cluster_id","cluster_name","action", <detail keys>, "alerts","maintenance"}` — insertion order identical to
  the three literals (L687-716). `CheckResult` = TypedDict. The learner-runs-3× smell is **not** fixed (output-equivalence
  unproven; behavior risk).
- `check_all_clusters`: → `_resolve_stale_check_alert(cluster_id: int) -> None` (L734-736) and
  `_record_check_failure(cluster_id: int, cluster_name: str, exc: Exception) -> CheckResult` (L739-758, including the
  rollback/upsert/commit order). Loop and per-cluster commit stay in the method.

### 2.3 Job plumbing in services/irrigation.py (L31-369)

Stays in this module (moving renames the logger, breaks `irrigation_mod.*` seams and touches the lazy cycle).

- `handle_watcher_interrupted` (89 lines incl. 25-line docstring): → `_stop_auto_cycle(repo, registry, irrigator) -> tuple[bool, str]`
  (returns `(stop_ok, message)` and does the two log calls + `stop` event row, L65-95) and
  `_left_running_message(triggered_by: str, irrigator) -> str` (L97-106, includes its `logger.warning`). Activity write
  stays in the public function. Log text byte-identical.
- `schedule_pump_watcher` (102 lines): **closure kept** — it captures `_app`/`registry` at *schedule* time; turning it
  into a module function that imports `_app` at run time would bind a *later* app (the `scheduler._app` trap).
  Only extract `_watcher_tuning(settings: Settings | None) -> tuple[float, float, int]` (L178-186, fallback values →
  `PUMP_WATCHER_*` constants). Lines 148-223 are uncovered (00-tests §5.2): this step waits for the Phase-1 I-iii /
  pump-watcher characterization.
- `_run_leak_check`, `_add_leak_check_job`, `_schedule_leak_check`, `rearm_leak_checks`: unchanged (the early `return`
  inside `_run_leak_check`'s try means a session helper would add a commit; not byte-identical). `_leak_check_done`:
  `limit=500` → `LEAK_CHECK_ACTIVITY_SCAN_LIMIT` (bug B-23 preserved).

### 2.4 `IrrigationLogic.decide_for_cluster` (logic/engine.py:106-266, CC 18) — high risk, two reviewers

Signature, return type, `__all__`, every Reason code/message/order unchanged. The 22-step table in 00-smells §A1 is the
spec; the decomposition maps 1:1 onto it.

| New member | Signature | Covers steps |
|---|---|---|
| `_RuleInputs` (module, `@dataclass(frozen=True, slots=True)`) | `snapshot: SensorSnapshot; trends: Trends; stress: StressIndicators; plant_care: list[dict]; temp_range: tuple[float, float] \| None; humidity_range: tuple[float, float] \| None; water_needs: str` | parameter object for step 7 outputs |
| `_record` | `(self, decision: IrrigationDecision, *, persist: bool, triggered_by: str) -> IrrigationDecision` | replaces the four `if persist: self._persist(...); return x` blocks (L142-181) |
| `_pre_gates` | `(self, cluster_id: int, evaluated_at: int, plants: list[Plant]) -> IrrigationDecision \| None` | steps 2-4 in order: no-plants → leak hold → cooldown (first non-None wins) |
| `_quiet_hours_skip` (module fn) | `(cluster_id: int, evaluated_at: int, window: tuple[int, int]) -> IrrigationDecision` | step 5 decision literal (L168-178) |
| `_finalize` (method, replaces the closure) | `(self, decision: IrrigationDecision, *, override_window: tuple[int, int] \| None, persist: bool, triggered_by: str) -> IrrigationDecision` | step 21: append `MANUAL_OVERRIDE_QUIET_HOURS` iff `override_window` is not None, then `_record` |
| `_evaluate_rules` | `(self, cluster: Cluster, plants: list[Plant], evaluated_at: int, current_temp: float \| None) -> IrrigationDecision` | steps 6-20; returns the first terminal decision or the fully adjusted one |
| `_gather_inputs` | `(self, cluster_id: int, plants: list[Plant]) -> _RuleInputs` | step 7, same call order: `get_recent_sensor_data(hours=SNAPSHOT_LOOKBACK_HOURS)` → `analyze_historical_trends` → `detect_stress_conditions` → `_attach_learning_alerts` → plant care → ranges → water needs |
| `_fallback_decision` | `(self, cluster_id: int, evaluated_at: int, current_temp: float \| None, inputs: _RuleInputs) -> IrrigationDecision` | step 8 call (L212-222) |
| `_base_decision` (module fn) | `(cluster_id: int, evaluated_at: int, inputs: _RuleInputs) -> IrrigationDecision` | step 9 (`confidence=CONFIDENCE_BASELINE`) |
| `_apply_adjustments` | `(self, cluster: Cluster, decision: IrrigationDecision, inputs: _RuleInputs, evaluated_at: int) -> None` | steps 13-20 in the original order |
| `_tz_name` | `(self) -> str \| None` | the 3 copies of `prefs = self.db.get_preferences(); prefs.timezone if prefs else None` (L281-282, 306-307, 334-335). **Not cached** — `get_preferences()` may create the row; call count stays identical. |

Resulting `decide_for_cluster` (≈20 lines):

```python
cluster = self.db.get_cluster(cluster_id)
if not cluster:
    return None
evaluated_at = int(time.time())
plants = self.db.get_plants_in_cluster(cluster_id)
gate = self._pre_gates(cluster_id, evaluated_at, plants)
if gate is not None:
    return self._record(gate, persist=persist, triggered_by=triggered_by)
quiet_window = self._resolve_quiet_window(cluster_id, evaluated_at)
if quiet_window is not None and not bypass_quiet_hours:
    return self._record(_quiet_hours_skip(cluster_id, evaluated_at, quiet_window), persist=persist, triggered_by=triggered_by)
decision = self._evaluate_rules(cluster, plants, evaluated_at, current_temp)
override = quiet_window if bypass_quiet_hours else None
return self._finalize(decision, override_window=override, persist=persist, triggered_by=triggered_by)
```

`_evaluate_rules` body (order = steps 6→20):

```python
cluster_id = cluster.id                                     # == the id decide_for_cluster looked up
weather_skip = self._apply_weather_skip_rule(cluster, cluster_id, evaluated_at)
if weather_skip is not None: return weather_skip
inputs = self._gather_inputs(cluster_id, plants)
sensors = self.db.get_sensors_in_cluster(cluster_id)        # still AFTER plant-care, as at L210
if not sensors or not inputs.snapshot.has_data: return self._fallback_decision(...)
decision = _base_decision(cluster_id, evaluated_at, inputs)
if _apply_water_warning_rule(decision) or _apply_critical_stress_rule(decision): return decision   # `or` short-circuits like L237-240
window_skip = self._apply_window_rule(cluster_id, evaluated_at)
if window_skip is not None: return window_skip
self._apply_adjustments(cluster, decision, inputs, evaluated_at)
return decision
```

Preserved consequences (must stay pinned, 00-smells §A1): terminals 6/8/10/11/12 bypass vacation rationing; the
fallback bypasses windows; OUTSIDE_WINDOW returns a fresh decision without snapshot; stress keys on the average.
`_apply_window_rule` drops its unused `cluster`/`decision` params (private; no test calls it). The lazy
`IrrigationLearner` import in `_attach_learning_alerts` stays lazy (cycle guard). `_resolve_quiet_window` becomes
`effective = self.db.get_effective_config(cluster_id); return quiet_window_at(effective, now_unix=evaluated_at, tz_name=self._tz_name())`.

`quiet_window_at` (new, logic/timing.py): `(effective: Mapping[str, Mapping[str, Any]], *, now_unix: int, tz_name: str | None) -> tuple[int, int] | None`
— body moved verbatim from L278-290 (the `int(...) if ... is not None else None` conversions included).

### 2.5 `_apply_soil_moisture_rule` (engine.py:678-752, CC 21)

```python
def _apply_soil_moisture_rule(decision: IrrigationDecision, plant_care: list[dict]) -> None:
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

| Helper | Signature | One line |
|---|---|---|
| `_cluster_target_band` | `(plant_care: list[dict]) -> tuple[float, float]` | `min` of target mins, `max` of target maxes via `moisture_target_range` (raises on empty list exactly like L685 — unreachable, plants ≥ 1). |
| `_soil_extremes` | `(snapshot: SensorSnapshot) -> tuple[float, float]` | min/max falling back to avg (L688-689). |
| `_is_conflict` | `(soil: tuple[float, float], band: tuple[float, float]) -> bool` | `min < tmin and max > tmax - CONFLICT_WET_MARGIN`. |
| `_sensor_names` | `(snapshot: SensorSnapshot, keep: Callable[[float], bool]) -> list[str]` | per-sensor names whose avg is not None and satisfies `keep` (dry/wet lists L693-700, order kept). |
| `_apply_conflict` | `(decision, snapshot, soil, band) -> None` | CONFLICT branch, message f-string verbatim (`or '?'` included). |
| `_apply_soil_level` | `(decision, avg_soil: float, soil, band) -> None` | VERY_DRY / DRY / ADEQUATE (`avg <= tmax`) / WET ladder verbatim — note ADEQUATE keys on **avg**, not max. |

Other engine functions (brief): `_apply_vacation_budget` (77 lines) → `_vacation_headroom_minutes(vac, irr, now) -> int`
(pure math L424-432, `SECONDS_PER_DAY`) — guard order kept (vac → VACATION_ACTIVE reason → irrigator → capacity → action);
`_apply_seasonal_multiplier` (CC 13) → `_seasonal_overrides(plant_care, season_key) -> tuple[Any, Any]` (L348-358,
keeps the dead `isinstance` branch — removing it is safe but is a separate, reviewed commit);
`_apply_light_adjustment` / `_apply_humidity_adjustment` / `_apply_trend_adjustment`: **unchanged** (flat ladders;
bug B-14 nominal `interval_delta` must not be "fixed" by a shared clamp helper).

### 2.6 `learning/issues.detect_conflicts` (issues.py:154-304, CC 36) and `detect_issues` (L21, CC 24)

Public names/signatures frozen (`detect_conflicts(db, plant_db, cluster_id, profiles, plant_care)`). Everything stays in
`issues.py` (patch seams).

`detect_conflicts` →

```python
sensors = db.get_sensors_in_cluster(cluster_id)
moisture = _latest_moisture_by_sensor(db, sensors)
if len(moisture) < 2:
    return []                                  # quirk preserved: low_light / low_env_humidity are ALSO skipped here
alerts = _unresolvable_conflicts(sensors, moisture, profiles, plant_care)
plants_by_id = {p.id: p for p in db.get_plants_in_cluster(cluster_id)}
alerts.extend(_low_light_alerts(db, plant_db, sensors, plants_by_id))
alerts.extend(_low_env_humidity_alerts(db, plant_db, sensors, plants_by_id))
return alerts
```

| Helper | Signature | One line |
|---|---|---|
| `_latest_moisture_by_sensor` | `(db, sensors: list[Sensor]) -> dict[int, float]` | mean of the newest `LEARNING_LATEST_SAMPLES` cleaned DESC readings over `LEARNING_CONFLICT_LOOKBACK_HOURS` (L166-174). |
| `_conflict_band` | `(plant_care: dict, plant_id: int \| None) -> tuple[float, float]` | L188-195 verbatim: default `(45.0, 65.0)`, catches only `(ValueError, IndexError)` (a `None` target still raises `AttributeError` as today — drifted from `parse_moisture_target`, so not folded). |
| `_split_dry_wet` | `(sensors, moisture, plant_care) -> tuple[list[tuple[Sensor, float, float]], list[tuple[Sensor, float, float]]]` | L180-200 (`LEARNING_CONFLICT_DRY_MARGIN`). |
| `_unresolvable_conflicts` | `(sensors, moisture, profiles, plant_care) -> list[Alert]` | nested dry×wet projection L202-240, iteration order kept. |
| `_conflict_alert` | `(dry: Sensor, dry_m: float, dry_target: float, wet: Sensor, wet_m: float, wet_target: float, needed_minutes: float, projected: float) -> Alert` | the Alert literal (8 params; acceptable for a pure message builder — alternative is two small NamedTuples, rejected as ceremony). |
| `_low_light_alerts` | `(db, plant_db, sensors, plants_by_id) -> list[Alert]` | L243-273; separate pass kept so DB/plant-DB call order is unchanged. |
| `_low_env_humidity_alerts` | `(db, plant_db, sensors, plants_by_id) -> list[Alert]` | L276-302. |

`detect_issues` → `_learned_profiles(db, sensors) -> dict[int, PlantProfile]` (L42-46);
per sensor: `_blocked_drip_alert(sensor, profile) -> Alert | None`, `_drainage_alert(db, sensor, profile) -> Alert | None`
(the light-correlated variant; calls module-global `seasonal_light_factor()`), `_chronic_underwatering_alert(db, sensor, profile, care) -> Alert | None`
(keeps its own `float(target.split("-")[0])` parse — "50" parses here but not in `parse_moisture_target`: drifted).
Per-sensor append order blocked → drainage → chronic kept; conflicts appended last when `len(profiles) >= 2`.

### 2.7 `PlantHealthService.compute_score` (services/health.py:20, CC 29)

| Helper | Signature | One line |
|---|---|---|
| `_empty_score` (module) | `() -> HealthScore` | the 6-key None dict (L41-48). `HealthScore` = TypedDict. |
| `_pooled_clean_readings` | `(self, sensors: list[Sensor], days: int) -> list[SensorReading]` | per-sensor `clean_readings` then pool (L59-64). |
| `_in_band_pct` (module) | `(values: list[float], lo: float, hi: float) -> float \| None` | `None` on empty, else `in_band / len * 100` (3 copies L73-88). |
| `_first_profile_efficiency` | `(self, sensors: list[Sensor], days: int) -> float \| None` | first non-None profile (L90-95). |
| `_composite_score` (module) | `(components: list[float \| None]) -> float \| None` | mean of present components, rounded and clamped (L97-110). |

The redundant `if all_readings:` guard (L72) disappears: every list it guards is empty when `all_readings` is empty, so
each `_in_band_pct` returns None — equivalence argued in the commit message and proven by `test_plants_health.py`.
Temp/humidity still gated on `lo is not None and hi is not None`. `soil_min, soil_max = moisture_target_range(care)`.

### 2.8 `logic/trends.analyze_historical_trends` (CC 29)

```python
trends = Trends()
readings = _pooled_clean_readings(db, cluster_id)          # per-sensor clean, TREND_LOOKBACK_HOURS
if len(readings) >= TREND_MIN_READINGS:
    readings.sort(key=lambda r: r.timestamp)
    first, second = readings[: len(readings) // 2], readings[len(readings) // 2 :]
    _set_moisture_trend(trends, first, second)
    _set_temperature_trend(trends, first, second)
_set_irrigation_cadence(trends, db, cluster_id)
return trends
```

`_trend_label(delta: float, threshold: float, *, up: str, down: str) -> str` (pure; the two ladders are mutually
exclusive for threshold > 0 so check order is irrelevant). `_set_temperature_trend` keeps the **truthiness** filter
`if r.temperature` (bug B-18, pinned). `_set_irrigation_cadence` uses `CADENCE_WINDOW_DAYS` for both `hours=7*24` and
`/ 7`. No-sensors path: empty pool → threshold check fails → identical.

### 2.9 `logic/stress.detect_stress_conditions` (CC 27)

Six pure helpers returning `str | None`: `_water_warning(snapshot)`, `_low_env_humidity(snapshot, plant_care)`,
`_low_light(snapshot, plant_care)`, `_water_stress(snapshot, trends)`, `_heat_stress(snapshot, trends, plant_care)`,
`_over_watering(snapshot, trends)`. Body:
`found = {"water_warning": ..., ...}; return StressIndicators(**{k: v for k, v in found.items() if v is not None})` —
only non-None values are set, so `model_fields_set` matches today's attribute-assignment behaviour (no consumer reads
it, but keep it exact). The `plant_care` query stays first (it re-queries plants; the "engine already has them" smell is
NOT fixed — the signature is public-ish and the extra query is invisible).

### 2.10 `ForecastService.predict_next_irrigation` (services/forecast.py:35, CC 25)

| Helper | Signature | One line |
|---|---|---|
| `_forecast_sensor` | `(self, sensor: Sensor, plant_map: dict[int, Plant], learner: IrrigationLearner) -> _SensorForecast \| None` | L52-88 per-sensor projection (None when no current moisture). |
| `_no_data_forecast` (module) | `(cluster_id: int) -> ForecastResponse` | L91-99. |
| `_confidence_for` (module) | `(profiled_count: int) -> float` | 0.7 / 0.4 / 0.2 ladder (constants). |
| `_weather_outlook` | `(self, cluster: Cluster \| None) -> tuple[bool, str \| None, float \| None]` | L121-133 (`weather_skip, weather_reason, precipitation`). |

`now = int(time.time())` stays before the sensor loop. Driver: keep `sort(...)[0]` (stable) rather than `min()` to be
literal. `_WEATHER_PRECIP_THRESHOLD_MM = WEATHER_SKIP_PRECIP_MM` (alias keeps the private name).

### 2.11 `create_app` (app.py:123-254, 132 lines)

| Helper | Signature | One line |
|---|---|---|
| `_OPENAPI_TAGS` (module const) | `tuple[dict[str, str], ...]` | the 14 tags, same order; passed as `list(_OPENAPI_TAGS)` (fresh list per app). |
| `_PROTECTED_API_ROUTERS` (module const) | `tuple[ModuleType, ...]` | `(clusters, plants, irrigators, sensors, configs, operations, scheduler, charts, alerts, activity, decisions, forecast, preferences, vacation, search, bulk, insights, health, quality, efficacy, windows)` — **include order is OpenAPI path order**. |
| `_make_lifespan` | `(settings: Settings) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]` | the closure L132-140; references the module globals `start_scheduler`/`rearm_leak_checks`/`stop_scheduler` at call time. |
| `_init_state` | `(app: FastAPI, settings: Settings, engine: Engine) -> str` | L170-187 in order (settings, session factory, `_init_tuya`, `_startup_timezone` + `set_display_timezone`, weather, ntfy, plant_db); returns `tz_name`. |
| `_init_background` | `(app: FastAPI, settings: Settings, tz_name: str) -> None` | `init_scheduler` → `init_health_monitor` → `_restore_persisted_scheduler_pause` (L189-191). |
| `_include_api_routers` | `(app: FastAPI) -> None` | auth router unprotected, then the tuple with `dependencies=[Depends(require_user)]`, then `well_known.router` (L199-228). |
| `_mount_web` | `(app: FastAPI) -> None` | static mount, `web_router`, `register_web_exception_handlers` (L231-234). |
| `_mount_mcp` | `(app: FastAPI) -> FastApiMCP` | L241-252; `app.state.mcp = ...` stays inside. |

Body keeps `if settings is None` / `if engine is None` literally (not `or`), then: `init_db` → `FastAPI(...)` →
`_init_state` → `_init_background` → `bootstrap_admin` → `_include_api_routers` → `_mount_web` → `_mount_mcp` → return.
`require_mcp_token` is **not** touched (non-constant-time compare is bug B-12; fixing it is a behavior change).
`_restore_persisted_scheduler_pause`'s silent `except: pass` stays (documented in REFACTOR_NOTES).

### 2.12 Scheduler job bodies (scheduler.py:292-443)

```python
@_contextmanager                                 # `from contextlib import contextmanager as _contextmanager` (keeps dir() surface)
def _job_session(app: FastAPI, failure_message: str) -> "Iterator[IrrigationRepository]":   # Iterator under TYPE_CHECKING only
    session = app.state.session_factory()        # evaluated at call time; AttributeError on None app escapes as today
    try:
        yield IrrigationRepository(session)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception(failure_message)        # module-global logger → sched_mod.logger patch still sees it
    finally:
        session.close()
```

(`Iterator` is imported under `if TYPE_CHECKING:` and the annotation is a string, so neither `Iterator` nor `annotations` becomes a new public attribute of the frozen `greenhouse_server.scheduler` surface — see R7.)

Applied to exactly the five jobs whose scaffolding is byte-identical: `_sync_job` ("Sync job failed"),
`_health_snapshot_job` ("Plant health snapshot job failed"; the redundant inner `IrrigationRepository` import is dropped),
`_check_job` ("Check job failed"), `_anomaly_job` ("Anomaly scan job failed"), `_health_monitor_job`
("Device health monitor job failed"). Each job passes **the current `_app` global** at call time
(`with _job_session(_app, "...") as repo:`) — never a default argument, never captured at import. Pre-session guards
(`_get_cloud() is None`, `_app is None`, monitor None) stay before the `with`, in their current order, so the
inconsistent `_app is None` guarding (only two jobs check it) is preserved. Lazy service imports stay inside the job
functions (cycle). `init_health_monitor` is **not** converted (its commit/rollback is nested differently and it assigns
`app.state.health_monitor` in the outer `try`). `_check_job` → additionally `_build_irrigation_service(repo, registry, cloud) -> IrrigationService`.

Literals: `minutes=15` → `ANOMALY_SCAN_INTERVAL_MINUTES`; `hour=0, minute=30` → `HEALTH_SNAPSHOT_HOUR/MINUTE`;
`hours=6` → `SYNC_JOB_BACKFILL_HOURS`; `"check_all"` literals (L72, L210) → `CHECK_ALL_JOB_ID`, whose declaration moves
from L446 to the top of the module (same name/value; module attribute order is not a contract). `_resolve_zoneinfo`
stays (it intentionally mirrors `timing._resolve_tz`; importing a private core function would add coupling for 6 lines).

### 2.13 Repository patch / delete / pagination copies (repository.py:954-1309)

```python
def _patch(self, row: Base, fields: Mapping[str, Any], *, json_fields: frozenset[str] = frozenset()) -> None:
    for key, value in fields.items():
        if value is None:
            continue
        if key in json_fields and isinstance(value, dict):
            setattr(row, key, json.dumps(value))
        elif hasattr(row, key):
            setattr(row, key, value)

def _delete(self, model: type[Base], row_id: int) -> bool:
    row = self.session.get(model, row_id)
    if row is None:
        return False
    self.session.delete(row)
    self.session.flush()
    return True

@staticmethod
def _page(stmt: Select, id_column: InstrumentedAttribute[int], *, limit: int | None, after_id: int | None) -> Select:
    if after_id is not None:
        stmt = stmt.where(id_column > after_id)
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt
```

- `_patch` applies to: `update_vacation_window` (963-967), `update_irrigation_window` (1017-1021), `update_preferences`
  (1048-1050), `update_cluster` (1154-1156), `update_plant` (1174-1176), `update_irrigator` (1292-1298, `json_fields={"config"}`),
  `update_sensor` (1266-1272, `json_fields={"config"}`; the `plant_id` pop + `reassign_sensor_to_plant` stays in the method).
  Equivalence note for prefs/cluster/plant (`hasattr` checked *before* `value is None` today): for column attributes
  `hasattr` is side-effect-free and the final row state is identical; only a relationship-named key with value `None`
  could differ (a lazy-load SELECT), and no caller passes one. Called out for the reviewer; `test_db.py` + API PATCH tests
  prove it. Bug B-15 (cannot clear nullable fields) preserved.
  NOT folded: `set_irrigation_config` / `update_global_irrigation_config` (they deliberately allow `None`).
- `_delete` applies to `delete_vacation_window`, `delete_irrigation_window`, `delete_cluster`, `delete_sensor`,
  `delete_irrigator` (`if not row` vs `is None`: ORM rows define no `__bool__/__len__`, identical). `delete_plant` keeps
  its own body (assignment-closing variant).
- `_page` applies to `list_all_sensors/irrigators/plants`: caller-specific `where`s first (cluster, category), then
  `_page` — compiled SQL `WHERE` order unchanged.
- Hidden clock (`timestamp or int(time.time())`) untouched (`repo_mod.time` seam; `0`-as-now quirk preserved).

### 2.14 `ClusterScreen` (tui/screens/cluster.py, 800 LOC, MI 1.08)

Pure builders move to `tui/render.py`. Type alias `Row = tuple[str | None, list[RenderableType | str]]`.

| New function (tui/render.py) | Signature | Replaces |
|---|---|---|
| `plant_rows` | `(plants: list[dict]) -> list[Row]` | `_render_plants` L281-303 (the `_plants_loaded` worker trigger stays in the screen) |
| `sensor_rows` | `(status: dict) -> list[Row]` | `_render_sensors` L329-353 |
| `decision_rows` | `(payload: dict \| None) -> list[Row]` | `_load_decisions` L358-376 |
| `history_rows` | `(payload: dict \| None) -> list[Row]` | `_load_history` L381-399 (sort key/reverse identical) |
| `config_rows` | `(effective: dict \| None) -> list[tuple[str, Text]]` | `_load_config` L408-413 |
| `window_rows` | `(windows: list[dict]) -> list[Row]` | `_load_config` L416-429 |
| `irrigator_info` | `(summary: ClusterSummary) -> Text` | `_render_overview` L203-220 |
| `decision_panel` | `(decision: dict) -> Text` | `_render_overview` L223-240 |
| `forecast_rows` | `(forecast: dict) -> list[tuple[str, str \| Text]]` | `_load_forecast` L249-259 (`_next_water` moves with it) |
| `insights_text` | `(insights: dict \| None, monitor: dict \| None) -> Text` | `_load_insights` L442-452 |
| `stats_rows` | `(stats: dict \| None) -> list[tuple[str, str]]` | `_load_insights` L455-467 |
| `efficacy_rows` | `(payload: dict \| None) -> list[Row]` | `_load_insights` L471-491 |
| `learn_report` | `(learn: dict \| None) -> Text` | `_load_insights` L489-492 |
| `job_rows`, `device_rows`, `quality_rows`, `scheduler_panel_rows` | `(...) -> list[Row]` / `list[tuple[str, str \| Text]]` | `SystemScreen.load` (CC 24) |

Screen methods become "fetch → build → `refill`/`update`/`show`", each ≤ 15 lines; widget ids, column headers,
`refill` call order and `KeyValue.show(..., title=...)` arguments unchanged.

CRUD dispatch (`action_new` L584-626, `action_edit` L628-678, `action_delete` L680-720, nesting 5): per-tab private
methods + a literal dict lookup — not a registry:

```python
def action_new(self) -> None:
    handler = {"tab-plants": self._new_plant, "tab-sensors": self._new_sensor,
               "tab-windows": self._new_window, "tab-overview": self._attach_irrigator}.get(self.active_tab)
    if handler is None:
        self.notify("Nothing to add here — try the Plants, Sensors, Windows or Overview tab.")
        return
    handler()
```

Same for `_edit_plant/_edit_sensor/_edit_window/_edit_irrigator/_edit_config` and
`_delete_plant/_delete_sensor/_delete_window/_detach_irrigator` (`_delete_cluster` already exists — pick
`_detach_irrigator` to avoid a clash). **Textual name rule:** no new name may start with `action_`, `on_`, `_on_`,
`watch_`, `_watch_`, `compute_`, `validate_` (Textual dispatches on those prefixes). `active_tab` is read once per
action, as today. Stale help text "Blank care fields are filled from the plant DB" (L594, bug B-19) is rendered UI copy
→ left verbatim. `_selected` is **not** hoisted to `DataScreen` (the alerts/settings variants differ in message and
return type; not a duplicate).

`model.summarize` (CC 18): extract `_band(plants: list[dict]) -> tuple[float | None, float | None]` and
`_plant_views(status) -> list[PlantView]`; stays in `model.py`. `SettingsScreen.load` (CC 19): builders
`account_rows`, `preference_rows`, `global_config_rows`, `vacation_rows` into `tui/render.py`.
`MetricChart.show_payload/show_overlay` (CC 19/14): `_draw_event_lines(self, events, xs)` and `_set_x_ticks(self, ...)`
private methods on the widget (removes the in-class duplicate); `SERIES_COLORS[i % len(SERIES_COLORS)]` (same value: list
has 7 items). `formatting.bar`, `forms.parse_value`, `FormScreen.compose`, `Heatmap.show`: leave (CC 11-15, linear).

### 2.15 CLI client return types (client.py, 78 mypy `return-value` errors)

```python
JSONObject = dict[str, Any]

def _object(self, method: str, path: str, **kwargs: Any) -> JSONObject:
    return cast(JSONObject, self._request(method, path, **kwargs))

def _array(self, method: str, path: str, **kwargs: Any) -> list[JSONObject]:
    return cast(list[JSONObject], self._request(method, path, **kwargs))

@staticmethod
def _drop_none(**fields: Any) -> JSONObject:
    return {k: v for k, v in fields.items() if v is not None}
```

Every public method swaps `self._request(` for `self._object(` / `self._array(` according to its *current* annotation
(`-> dict` / `-> list`), annotations tightened to `JSONObject` / `list[JSONObject]`. `_request` keeps its name and
signature (the TUI fixture replaces it per instance). `cast` is a runtime no-op → zero behavior change. `_drop_none`
replaces the 9 identical comprehensions **only** where the source is `kwargs.items()` with `v is not None`;
`irrigator_add`/`irrigator_update` config building (truthy vs `is not None`) stays separate. 74 method docstrings
added (not `--help`; safe). Bug B-17 (only `ConnectError` mapped) preserved.

`resolve_server_url(ctx: typer.Context) -> str` in `_helpers.py`: `ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", "http://localhost:8000")`;
used by `get_client`, `commands/auth.py:26`, `commands/tui.py:35`. The `IrrigationClient.__init__` default stays.

### 2.16 Remaining > 40-line / CC ≥ C functions (brief verdicts)

| Function | Verdict |
|---|---|
| `services/maintenance.collect_maintenance_alerts` (D 23) | → `_battery_alert`, `_stale_alert(sensor, readings, now)`, `_humidity_alert(sensor, readings, plant, plant_db)`, `_light_alert(...)`, each `-> dict \| None`; per-sensor append order kept. Constants `MAINTENANCE_*`. |
| `services/data_quality.build_report` (C 20, 124 lines) | → `_sensor_issues(repo, sensors, now) -> tuple[list[DataQualityIssue], set[int]]` (per-sensor interleaving of `sensor_without_plant`/`stale_sensor` kept), `_plant_issues`, `_cluster_issues`, `_duplicate_device_issues`, `_count_by_code`. Order of the four passes kept. |
| `services/charts._threshold_for_cluster` (C 20) | → `_aggregate_band(mins: list[float], maxs: list[float]) -> dict \| None` for the identical temp/humidity blocks; metric-to-attribute pairs as a local dict. |
| `logic/sensors.get_recent_sensor_data` (C 19) | → `_mean_or_none(values) -> float \| None` (7 copies); `> 15` → `NIGHT_LUX_THRESHOLD`; loop unchanged. |
| `logic/fallback.temperature_based_decision` (C 13) | → `_config_fallback(db, cluster_id, base) -> IrrigationDecision`, `_temperature_interval(temp, water_needs) -> int` (constants `FALLBACK_*_STEP`). Bug B-24 preserved. |
| `services/insights.cluster_insights` (C 14) | → `_insight_from_alert(alert: dict) -> CareInsight`; one loop over `[*maintenance, *learning]` (both collected in the same order before iterating; body is pure). |
| `services/pump_watcher.watch` (C 13) | → `_outcome(kind: str, polls: int, failures: int, alarm_raw: Any, elapsed: float) -> WatchOutcome` (TypedDict). **Elapsed is computed by the caller at exactly the same point** (`self._clock()` calls, or `now` in the `completed` case): injected clocks in tests are call-count sensitive. `completed` keeps `read_failures: 0`. |
| `pump_watcher._handle_trip` (105) | leave in this plan (actuation safety path; only constants). |
| `services/health_monitor.backfill_from_history` | → `_raise_if_not_open(alarm, sensor)` for the duplicated block (L252-274); `hours=24*7` → `SENSOR_HEALTH_BACKFILL_HOURS`. `_infer_cluster_id/_infer_label` merge rejected (changes query count; no smell worth it). |
| `services/search.search` (118) | leave — four parallel query blocks are intentionally explicit SQL; a loop over specs is a registry in disguise. |
| `services/anomaly.scan` (115), `leak._evaluate_sensor`, `leak.check_after_irrigation`, `efficacy.score_cluster`, `system_health.pulse`, `sync._cluster_snapshot` | split only into named phases inside the same class if reviewers ask; not required to remove a present smell (CC ≤ 15, linear). |
| `manual_control.manual_start/stop/log`, `bulk.stop_all_irrigators` | leave (actuation paths; bug B-4 nearby). |
| `web/routes/clusters.cluster_detail` (96) | → `_rationale_reasons(repo, cluster_id) -> list[dict]`, `_window_rows(repo, cluster_id) -> list[dict]`; quiet flag via `quiet_window_at(...) is not None` (after engine step). Context keys untouched. |
| `web/routes/plant_dashboard.plant_dashboard` (78) | → `_plant_context(...)` pieces only if covered (46 % branch coverage today → wait for G3). `_relative_time` stays (drifted from `filters.age_seconds`). |
| `routes/operations.cluster_status` (64) | → `_status_sensor(s) -> ClusterStatusSensorResponse`, `_status_irrigator(i) -> ... \| None`, `_status_decision(d) -> IrrigateResponse \| None` module helpers (route name/docstring/response_model untouched). |
| `routes/plants.sync_plants`, `web/routes/operations.sync_plants` | body → `ClusterService.sync_plants` (§3). |
| `routes/operations.stats_export`, `web/routes/analytics.cluster_stats_export` | body → `cluster_events_csv` (§3). |
| `commands/operations.register` (109), `commands/auth.register` (68) | leave: length is Typer decorators + `--help` docstrings (frozen). |
| `plant_db.get_care_data` (59), `profiling.get_plant_profile` (C 14), `cleaning.clean_readings` (C 11), `stats.get_irrigation_stats` | leave (well-commented, invariant-9/10 carriers; low payoff vs. risk). |
| `repository.move_plant`, `upsert_alert`, `bulk_add_sensor_readings` | leave (linear; docstring-heavy). |
| `web/filters.cluster_caps` (65), `web/context.base_context` (46) | leave (`base_context` swallowing errors is behavior). |
| devices: `gateway.get_live_reading/get_device_logs`, `ik10pw._start_keepalive/read_health`, `tr301z.read_health`, `tuya_generic.status` | **not touched** except stale docstrings (`dp_parsers` claims). `_parse_dps` helper deferred: v2 path doesn't catch `ValueError` while logs path does — merging would change behavior. Bug B-2 lives here. |

---

## 3. Duplication between `routes/*` and `web/routes/*` (and CLI/TUI)

Rule: merge **only** what is byte-identical in logic and side effects; each layer keeps its own I/O mapping (status code,
detail string, template, context keys). Route function names, docstrings, `response_model`s, template names and context
keys are not touched.

### 3.1 Merge (byte-identical today)

| Duplicate | Evidence | Shared home | Per-layer residue |
|---|---|---|---|
| Plant-DB sync (plant / cluster / all) | `routes/plants.py:219-248` ≡ `web/routes/operations.py:114-141` (incl. O(n) scan, `if not cluster: continue`, `f"{species}: {e}"`) | `ClusterService.sync_plants(self, *, plant_id: int \| None, cluster_id: int \| None) -> tuple[int, list[str]]`; raises `LookupError(plant_id)` when the plant is not found | API: `except LookupError: raise HTTPException(status_code=404, detail=f"Plant {request.plant_id} not found")`; web: `raise HTTPException(404, f"Plant {pid} not found")`; both `repo.session.commit()` then their own response. The O(n) scan is **moved verbatim**, not replaced by `repo.get_plant` (SQLite FK enforcement is off — no `PRAGMA foreign_keys` — so an orphan plant would be found by `get_plant` but not by the scan). Truthiness `if request.plant_id` / `if pid` preserved (id 0 = absent, B-16 neighbour). |
| Events CSV export | `routes/operations.py:355-389` ≡ `web/routes/analytics.py:79-107` (the pylint R0801 hit) | `cluster_events_csv(repo: IrrigationRepository, cluster_id: int, *, days: int) -> str` in `services/cluster.py` | Each route keeps `require_cluster`, builds its own `StreamingResponse` (identical headers). `stats.export_csv` (dead, different format) untouched. |
| Weekday vocabulary | `web/routes/clusters.py:92-100` ≡ `configs.py:13-22` ≡ `windows.py:20,76` | `web/weekdays.py` | private aliases in each module for one release of safety (§1.2). |
| Quiet-hours "active now" | `engine.py:278-290` vs `web/routes/clusters.py:165-179` — same effective-config resolution and `is_within_quiet_hours` call | `logic.timing.quiet_window_at` | web: `quiet_active_now = quiet_window_at(effective_config, now_unix=int(time.time()), tz_name=prefs.timezone if prefs else None) is not None` (same `get_preferences` call, same clock read). Done **after** the engine step. |
| `"Cluster not found"` 404 | 20 inline sites | existing `deps.require_cluster` | Replace **only** sites of the exact form `x = repo.get_cluster(id); if not x: raise HTTPException(404, "Cluster not found")`. Sites where `None` comes from another call (`delete_cluster`, `get_cluster_status`, insights service, e.g. `routes/operations.py:58`, `web/routes/clusters.py:87,118`) stay. Optional, low value. |
| CLI server URL | `_helpers.py:14` ≡ `auth.py:26` ≡ `tui.py:35` | `resolve_server_url(ctx)` | — |

### 3.2 Must stay separate (drifted — merging changes behavior)

| Pair | Drift | Why it stays |
|---|---|---|
| Window validation (`routes/windows.py:23-33` vs `web/routes/windows.py:36-42`) | messages differ (`…0..23` vs `…0..23.`; mask: `"weekday_mask must be 1..127 …"` vs `"Select at least one weekday."`), web parses mask first | Six lines; a shared `validate_window -> code` + two message maps adds more code than it removes. Only the bounds become constants (`WINDOW_HOUR_MAX`, `FULL_WEEKDAY_MASK`). |
| Irrigator create (`routes/irrigators.py:83-105` vs `web/routes/irrigators.py:83-115`) | web lacks `IntegrityError` → 500 (B-7), different try scope, web re-renders form on 409 | 4 duplicated lines; per-layer error mapping is the bulk. Rejected. |
| Sensor create | web lacks plant-in-cluster check + `IntegrityError` (B-7) | behavior differs. |
| `check_all` `has_alerts` | API: alerts ∨ maintenance ∨ needs_water; web: alerts only (B-13) | behavior differs. |
| Vacation `starts < ends` | API POST unvalidated (B-8) | behavior differs. |
| Relative time (`web/filters.age_seconds` vs `plant_dashboard._relative_time`) | different outputs ("Ns ago"/"stale" vs "never"/minutes) | different rendered HTML. |
| Moisture-target parsing in `monitor_cluster` and both `issues.py` sites | 3-part strings, single-number strings, `None` targets parse differently | only the 3 byte-identical `parse_moisture_target(care.get("soil_moisture_target", "45-65"))` sites (engine:684, health:52, forecast:65) use `moisture_target_range`. |
| CLI `irrigator_add` vs `irrigator_update` config dict | truthy vs `is not None` | behavior differs. |
| TUI weekday/vocabulary copies vs server | package boundary (CLI must not import core) | accepted duplication. |
| TUI `_selected` variants | message/return type differ | not a duplicate. |

---

## 4. Constants to move into `constants.py` (values and literal types unchanged)

Rule: same value **and** same literal type (`int` stays `int`, `float` stays `float`). User-facing strings that embed a
number (`"in next 6h"`, `"fallback (20C)"`) keep their literal text — never re-render them from a constant.

| New name | Value | Call sites |
|---|---|---|
| `SECONDS_PER_HOUR` | `3600` | engine.py:499, 504, 537; maintenance.py:45-46; forecast.py:119 |
| `SECONDS_PER_DAY` | `86400` | engine.py:407, 424, 425 |
| `SNAPSHOT_LOOKBACK_HOURS` | `24` | engine.py:200 |
| `CONFIDENCE_BASELINE` | `0.5` | engine.py:231 |
| `WEATHER_FORECAST_HOURS` | `6` | engine.py:557; forecast.py:127 |
| `WEATHER_SKIP_PRECIP_MM` | `2.0` | engine.py:562; forecast.py:14 (`_WEATHER_PRECIP_THRESHOLD_MM` becomes an alias) |
| `DEFAULT_SOIL_MOISTURE_TARGET` | `"45-65"` | engine.py:684; health.py:52; forecast.py:65; irrigation.py:635; issues.py:122, 190 |
| `STRESS_HUMIDITY_DEFICIT` | `20` | stress.py:28 |
| `STRESS_LOW_LIGHT_FRACTION` | `0.4` | stress.py:36 |
| `STRESS_STEEP_DECLINE_DELTA` | `-10` | stress.py:49 |
| `STRESS_HEAT_OFFSET_C` | `5` | stress.py:54 |
| `TREND_LOOKBACK_HOURS` | `48` | trends.py:21 |
| `CADENCE_WINDOW_DAYS` | `7` | trends.py:54 (`hours=CADENCE_WINDOW_DAYS * 24`), :62 (`/ CADENCE_WINDOW_DAYS`) |
| `CADENCE_LOW_EVENTS_PER_DAY`, `CADENCE_LOW_AVG_MINUTES`, `CADENCE_HIGH_EVENTS_PER_DAY` | `1`, `2`, `3` | trends.py:64, 66 |
| `FALLBACK_HIGH_NEEDS_INTERVAL_STEP`, `FALLBACK_LOW_NEEDS_INTERVAL_STEP` | `4`, `6` | fallback.py:89, 91 |
| `NIGHT_LUX_THRESHOLD` | `15` | utils.py:28 (now imported), sensors.py:46 |
| `SEASONAL_LIGHT_FACTOR_BY_MONTH` | the 12-entry dict | utils.py:11-24 (now imported, aliased) |
| `LEARNING_CONFLICT_LOOKBACK_HOURS`, `LEARNING_DRAINAGE_LUX_LOOKBACK_HOURS`, `LEARNING_WEEK_HOURS` | `6`, `48`, `168` | issues.py:82, 129, 171, 253, 284 |
| `LEARNING_LATEST_SAMPLES` | `3` | issues.py:174 |
| `LEARNING_CHRONIC_MIN_RESPONSES` | `5` | issues.py:132 |
| `LEARNING_CONFLICT_DRY_MARGIN` | `5` | issues.py:197 |
| `LEARNING_MIN_ENV_SAMPLES` | `5` | issues.py:255, 286 |
| `LEARNING_HUMIDITY_DEFICIT` | `15` | issues.py:289 |
| `LOW_LIGHT_ALERT_FRACTION` | `0.5` | issues.py:259; maintenance.py:83 |
| `MAINTENANCE_LOOKBACK_HOURS`, `MAINTENANCE_STALE_SECONDS`, `MAINTENANCE_MIN_SAMPLES`, `MAINTENANCE_HUMIDITY_DEFICIT` | `24`, `10800` (`3 * 3600` → keep as expression `3 * SECONDS_PER_HOUR`), `3`, `10` | maintenance.py:30, 45, 58, 64, 75 |
| `MONITOR_LOOKBACK_HOURS`, `MONITOR_VERY_DRY_MARGIN`, `MONITOR_WET_MARGIN` | `2`, `15`, `10` | irrigation.py:628, 643, 647 |
| `FALLBACK_TEMPERATURE_C` | `20.0` | irrigation.py:420 |
| `PUMP_WATCHER_POLL_SECONDS`, `PUMP_WATCHER_WARMUP_SECONDS`, `PUMP_WATCHER_MAX_READ_FAILURES` | `2.0`, `5.0`, `5` | irrigation.py:180-182; pump_watcher.py:59-61 defaults; `config.py:156-163` `Field(default=...)` (identical default → identical `Settings` JSON schema, checked by G8) |
| `LEAK_CHECK_ACTIVITY_SCAN_LIMIT` | `500` | irrigation.py:242 |
| `ANOMALY_SCAN_INTERVAL_MINUTES` | `15` | scheduler.py:184 |
| `HEALTH_SNAPSHOT_HOUR`, `HEALTH_SNAPSHOT_MINUTE` | `0`, `30` | scheduler.py:216-217 |
| `SYNC_JOB_BACKFILL_HOURS` | `6` | scheduler.py:306 |
| `SENSOR_HEALTH_BACKFILL_HOURS` | `168` (`24 * 7`) | health_monitor.py:244 |
| `FORECAST_CONFIDENCE_HIGH/MEDIUM/LOW`, `FORECAST_HIGH_CONFIDENCE_PROFILES` | `0.7`, `0.4`, `0.2`, `3` | forecast.py:105-111 |
| `HEALTH_SCORE_WINDOW_DAYS` | `14` | health.py:20 signature default (`days: int = HEALTH_SCORE_WINDOW_DAYS` — introspected default value identical) |
| `WINDOW_HOUR_MAX`, `FULL_WEEKDAY_MASK` | `23`, `127` | routes/windows.py:24, 32; web/routes/windows.py:37, 41; repository.py:999 default; web/weekdays.py |

Deliberately NOT moved: `"open-meteo"` (data-source label, not a threshold), `"alarm_dp": 105` (device-profile
knowledge; devices untouched), TUI literals (CLI cannot import core; display-only), `DEFAULT_QUIET_*` (already exist,
dead; the live default is in a frozen migration — note only). `_FALLBACK_DRAINAGE_PER_HOUR` already named.

---

## 5. Pattern choices

### 5.1 Chosen (each tied to a present smell)

| Pattern | Smell it removes | Where |
|---|---|---|
| Extract Function / Extract Method (private, same module) | god functions (CC 18-36, 100-187 lines) | everywhere in §2 |
| Guard clauses | nesting depth 4-5 | pipeline, CRUD dispatch, detect_conflicts |
| Parameter Object (`@dataclass(frozen=True, slots=True) _RuleInputs`) | 7 loose locals threaded through the engine | engine only |
| `TypedDict` result types (`PipelineResult`, `CheckResult`, `MonitorResult`, `HealthScore`, `WatchOutcome`) | untyped `dict` contracts (00-smells B "untyped dict contracts") — without touching templates or Pydantic | services |
| Context manager (`_job_session`) | 5 copies of session open/commit/rollback/log/close | scheduler only |
| Small private repo helpers (`_patch/_delete/_page`) | ~120 copy-pasted lines | repository |
| Literal dict dispatch (`{"tab-x": self._handler}.get(...)`) | 3× if/elif on tab id, nesting 5 | ClusterScreen |
| Pure render module (`tui/render.py`) | 800-line god screen mixing payload transformation with widget I/O | TUI |
| Named constants | invariant 5 violations | §4 |
| `typing.cast` wrappers | 78 mypy `return-value` errors from a `dict \| list` helper | CLI client |

### 5.2 Considered and REJECTED

| Pattern | Why rejected |
|---|---|
| Rule registry / Strategy / Chain of Responsibility for the engine | The explicit 22-step sequence *is* the documentation of priority; a registry hides ordering and short-circuit semantics in data. 00-smells §A1 agrees. |
| Pipeline / Command objects for `run_irrigation_pipeline` | One caller path; methods suffice. |
| Typed result dataclasses / Pydantic models for service results | Ripple into Jinja templates (`result.get(...)` in Python, attribute access in templates) and route mappings; TypedDict gives the typing at zero runtime cost. |
| Splitting `repository.py` into a `repository/` package of mixins | Cosmetic; changes `__module__`, needs shims, MRO surprises; the cost is copy-paste, solved by 3 helpers. |
| Splitting `schemas.py` | OpenAPI risk (`__module__`, class docstrings, field order); cohesive DTO module. |
| Splitting `utils.py` into `logic/light.py` | 117 lines; requires shims + patch-seam care for zero behavior benefit. Only the two thresholds go to constants. |
| Moving job plumbing to `services/irrigation_jobs.py` | Renames loggers (`tests/test_migrations.py:149`), breaks `irrigation_mod` seams, re-exports needed, and touches the `scheduler`↔`services.irrigation` lazy cycle. |
| Repository Protocol / ports-and-adapters for `logic/*` | No second implementation; tests already use in-memory SQLite. YAGNI. |
| Unit of Work replacing route `session.commit()` calls | Behavioral (transaction boundaries move). |
| Service locator replacement (`_app` global → DI container) | Process-global scheduler is a frozen surface; the trap is managed by passing `_app` explicitly at call time. |
| `deps.require_*` family (irrigator/sensor/window with detail param) | Detail strings and punctuation differ per layer and per route; only the exact `require_cluster` sites are folded. |
| Shared `validate_window` | Messages differ by design; more code than it saves. |
| Hoisting `_selected` into `DataScreen` | Variants are not duplicates. |
| Device `_parse_dps` helper | v1/v2 error handling differs (behavior); devices are high-risk/low-payoff. |
| Palette module for TUI hex colours | Cosmetic; not a present maintenance cost. |
| Enum-ising status/severity/action strings | DB values + many call sites; the stringly vocabulary is real but would be a broad change; plain-string module constants could come later, not in this plan. |
| Merging `stats.export_csv` with the route CSV | Different time format (dead public function; frozen import path). |

---

## 6. Ordered migration steps

Each line = one small green commit, one concern (moves, extractions, constant replacements, formatting never mixed).
"Tests" = targeted subset from `refactor/baseline/module-tests-map-direct.json` plus the Phase-1 goldens that guard the
surface (`tests/**/test_contract_*.py`). For every server commit also run `tests/server/test_mcp.py`
`tests/server/test_contract_openapi.py` `tests/server/test_contract_mcp.py`; for every CLI/TUI commit
`tests/cli/test_contract_help.py` `tests/cli/test_contract_json_output.py`. High-risk modules (engine, scheduler, devices)
use the **conservative** map instead of the direct one. Red → revert.

**Phase 0 — gates (no production code)**

| # | Commit | Tests |
|---|---|---|
| 0.1 | `build(dev): add mypy and import-linter to the dev group` (`uv.lock` dev-only) | `uv run lint-imports`, `uv run mypy` on an empty strict list |
| 0.2 | `chore: add import-linter contracts encoding today's layering` (§8.3) — must pass on baseline | `uv run lint-imports` |
| 0.3 | `chore(ruff): enable C90/PLR091x/TID251 with per-file ignores for current offenders` (§8.2) | `uv run ruff check libs/ tests/` |

**Phase 1 — CLI (lowest risk, no server)**

| # | Commit | Tests |
|---|---|---|
| 1 | extract `resolve_server_url` | `tests/cli/test_cli.py tests/cli/test_completeness.py tests/cli/test_tui.py -k TuiCommand` |
| 2 | client `_object/_array` typed wrappers (annotation + call rename only) | `tests/cli/test_completeness.py tests/cli/test_cli.py tests/cli/test_tui.py` |
| 3 | client `_drop_none` (only the `kwargs.items()` / `is not None` sites) | same |
| 4 | client method docstrings | `ruff` only + `test_completeness.py` |

**Phase 2 — server dedupe of byte-identical copies**

| # | Commit | Tests |
|---|---|---|
| 5 | add `web/weekdays.py`; point `web/routes/{clusters,configs,windows}.py` at it | `tests/server/test_web_windows.py test_web_config_pages.py test_web_cluster_pages.py test_web_redirects.py test_contract_web_html.py` |
| 6 | add `cluster_events_csv`; use in `routes/operations.stats_export` | `tests/server/test_operations.py test_mcp.py test_contract_openapi.py` |
| 7 | use `cluster_events_csv` in `web/routes/analytics.cluster_stats_export` | `tests/server/test_web_analytics.py test_contract_web_html.py` |
| 8 | add `ClusterService.sync_plants`; use in `routes/plants.sync_plants` | `tests/server/test_plants.py test_mcp.py test_contract_openapi.py` |
| 9 | use it in `web/routes/operations.sync_plants` | `tests/server/test_web_operations.py test_web_redirects.py test_contract_web_html.py` |
| 10 | (optional) exact-form `require_cluster` sites, one router per commit | that router's `test_<resource>.py` / `test_web_*` |

**Phase 3 — repository**

| # | Commit | Tests |
|---|---|---|
| 11 | add `_delete`; apply to 5 delete methods | `tests/test_db.py tests/server/test_clusters.py test_sensors.py test_irrigators.py test_vacation.py test_irrigation_windows.py test_web_crud_actions.py` + `test_contract_*` schema/DDL |
| 12 | add `_patch`; apply to vacation/window/prefs | `tests/test_db.py tests/server/test_vacation.py test_irrigation_windows.py test_preferences.py test_web_preferences.py test_web_vacation_edit.py test_web_windows.py` |
| 13 | apply `_patch` to cluster/plant/sensor/irrigator | `tests/test_db.py tests/server/test_clusters.py test_plants.py test_sensors.py test_irrigators.py test_sensor_assignments.py test_web_irrigator_capacity.py` |
| 14 | add `_page`; apply to `list_all_*` | `tests/server/test_sensors.py test_irrigators.py test_plants.py test_system_health.py test_anomaly.py test_data_quality.py` |

**Phase 4 — constants (one area per commit; values identical)**

| # | Commit | Tests |
|---|---|---|
| 15 | add core constants (stress/trends/fallback/learning/time units) — *definitions only* | `test_contract_*` constants golden (see risk R7) |
| 16 | use them in `logic/{stress,trends,fallback,sensors}.py` + `utils.py` imports | `tests/test_logic.py tests/test_cleaning.py tests/test_utils.py tests/test_engine_timing.py` |
| 17 | use them in `learning/issues.py` | `tests/test_learning.py` |
| 18 | use them in `logic/engine.py` (engine reviewers) | conservative engine list (`tests/test_logic.py test_engine_timing.py test_leak_hold.py test_vacation_rationing.py tests/server/test_operations.py test_decisions.py test_clusters.py test_contract_pipeline.py …`) |
| 19 | server constants in `services/{maintenance,forecast,health,health_monitor,irrigation,pump_watcher}.py`, `config.py` | `tests/server/test_alerts.py test_insights.py test_forecast.py test_plants_health.py test_health_monitor.py test_pump_watcher.py test_operations.py test_contract_settings.py` |
| 20 | `moisture_target_range` + adopt at engine:684, health:52, forecast:65 | `tests/test_logic.py tests/server/test_plants_health.py test_forecast.py` + engine conservative |

**Phase 5 — pure core decompositions**

| # | Commit | Tests |
|---|---|---|
| 21 | `logic/sensors` `_mean_or_none` | `tests/test_cleaning.py tests/test_logic.py tests/server/test_sync_snapshot.py` |
| 22 | `logic/trends` helpers | `tests/test_logic.py` + engine conservative |
| 23 | `logic/stress` helpers | `tests/test_logic.py` + engine conservative (stress has no direct test → Phase-1 pipeline golden is the guard) |
| 24 | `logic/fallback` helpers | `tests/test_logic.py tests/test_engine_timing.py` |
| 25 | `issues.detect_conflicts` helpers | `tests/test_learning.py tests/server/test_insights.py test_alerts.py` |
| 26 | `issues.detect_issues` helpers | same |

**Phase 6 — server services**

| # | Commit | Tests |
|---|---|---|
| 27 | `health.compute_score` | `tests/server/test_plants_health.py test_web_plant_hero.py test_web_plant_pages.py` |
| 28 | `forecast.predict_next_irrigation` | `tests/server/test_forecast.py test_web_insights.py` |
| 29 | `maintenance.collect_maintenance_alerts` | `tests/server/test_alerts.py test_insights.py test_web_alerts.py` |
| 30 | `data_quality.build_report` | `tests/server/test_data_quality.py test_web_quality.py` |
| 31 | `charts._threshold_for_cluster` | `tests/server/test_web_charts.py test_charts_*.py` |
| 32 | `insights._insight_from_alert` | `tests/server/test_insights.py test_web_insights.py` |
| 33 | `pump_watcher._outcome` | `tests/server/test_pump_watcher.py test_scheduler_shutdown.py` |
| 34 | `health_monitor._raise_if_not_open` | `tests/server/test_health_monitor.py` |
| 35 | `routes/operations.cluster_status` mapping helpers | `tests/server/test_clusters.py test_operations.py test_mcp.py test_contract_openapi.py` |
| 36 | `web/routes/clusters.cluster_detail` helpers (not the quiet flag yet) | `tests/server/test_web_cluster_pages.py test_web_decision_rationale.py test_web_windows.py test_contract_web_html.py` |

**Phase 7 — TUI**

| # | Commit | Tests |
|---|---|---|
| 37 | add `tui/render.py` with plant/sensor rows; use in ClusterScreen | `tests/cli/test_tui.py` (+ Phase-1 TUI golden if present) |
| 38 | decisions/history/config/window rows | same |
| 39 | overview/forecast/insights builders | same |
| 40 | CRUD per-tab methods + dict dispatch (`action_new`, then `action_edit`, then `action_delete` — 3 commits) | `tests/cli/test_tui.py -k "Crud or ExactRequests"` then full file |
| 41 | SystemScreen / SettingsScreen builders | `tests/cli/test_tui.py` |
| 42 | `MetricChart` axis/event helper; `summarize` helpers | `tests/cli/test_tui.py -k "Model or Chart"` then full |

**Phase 8 — app factory, typing, scheduler (high risk)**

| # | Commit | Tests |
|---|---|---|
| 43 | `create_app` → `_OPENAPI_TAGS`, `_PROTECTED_API_ROUTERS`, helpers | `tests/server/test_mcp.py test_auth.py test_scheduler.py test_scheduler_jobs.py test_scheduler_pause.py test_scheduler_shutdown.py test_contract_openapi.py test_contract_mcp.py test_contract_settings.py` |
| 44 | TypedDicts on service return annotations (annotation-only) | `mypy` strict list + `tests/server/test_operations.py` |
| 45 | scheduler constants + `CHECK_ALL_JOB_ID` hoist | the 5 scheduler files + `test_leak_rearm.py` |
| 46 | scheduler `_job_session` (one job per commit: anomaly → snapshot → sync → monitor → check) | `tests/server/test_scheduler*.py test_scheduler_jobs.py` (job-body I-iii tests must exist first) |

**Phase 9 — irrigation service (high risk; actuation path)**

| # | Commit | Tests (conservative list of `services.irrigation`) |
|---|---|---|
| 47 | `_error_result`, `_decision_result`, `_record_skip` | `tests/server/test_operations.py test_decisions.py test_irrigators.py test_leak_rearm.py test_notify.py test_health_monitor.py test_web_operations.py test_web_decision_rationale.py tests/cli/test_tui.py tests/test_migrations.py test_contract_pipeline.py` |
| 48 | `_resolve_adapter` + `_with_error` | same |
| 49 | `_blocking_alarms` + `_apply_health_block` | same |
| 50 | `_actuate`, `_event_notes`, `_on_started`, `_on_start_failed` | same + `test_leak_rearm.py` (`_time` seam) |
| 51 | `_resolve_temperature` split | same |
| 52 | `monitor_cluster` helpers | `tests/server/test_operations.py test_web_operations.py tests/cli/test_tui.py` |
| 53 | `check_cluster` / `check_all_clusters` helpers | `tests/server/test_operations.py -k CheckAll` then same list |
| 54 | `handle_watcher_interrupted` helpers | `tests/server/test_scheduler_shutdown.py test_pump_watcher.py` |
| 55 | `_watcher_tuning` (only after lines 148-223 are characterised) | same |

**Phase 10 — engine (last; two reviewers)**

| # | Commit | Tests (conservative engine list) |
|---|---|---|
| 56 | `_tz_name` | engine conservative + `test_contract_pipeline.py` |
| 57 | `_record` (replaces 4 persist blocks) | same |
| 58 | closure `_finalize` → method | same |
| 59 | `_pre_gates` + `_quiet_hours_skip` | same + `tests/test_leak_hold.py test_engine_timing.py` |
| 60 | `_RuleInputs` + `_gather_inputs` | same |
| 61 | `_evaluate_rules` + `_base_decision` + `_fallback_decision` + `_apply_adjustments`; drop unused `_apply_window_rule` params | same |
| 62 | soil-rule helpers | same |
| 63 | `_vacation_headroom_minutes`, `_seasonal_overrides` | `tests/test_vacation_rationing.py test_engine_timing.py` + same |
| 64 | `quiet_window_at` in `logic/timing.py`; engine uses it | `tests/test_timing.py` + same |
| 65 | web `cluster_detail` uses `quiet_window_at` | `tests/server/test_web_cluster_pages.py test_contract_web_html.py` |

**Phase 11 — devices**: stale docstrings only (`sensors/tuya_generic.py:3,24`, `profile.py:81-83`, `tr301z.py:3`).
Tests: `tests/devices/ tests/test_cloud.py`.

Per-commit ratchet: after each decomposition commit, drop the function's entry from the ruff `C901` per-file-ignores and
add the module to the mypy strict list (§8).

---

## 7. Risks and deliberate non-goals

| # | Risk | Mitigation in this plan |
|---|---|---|
| R1 | **Contract breaks (OpenAPI/MCP)** | No route function, docstring, `response_model`, Pydantic class or field touched. `create_app` keeps tag order and router include order (tuple literal in the same order). G1/G2 golden on every server commit. |
| R2 | **Import-time side effects** | New modules are pure (constants/functions); no env reads, no I/O at import. `textual` stays lazily imported (`tui/render.py` imports only `rich`; `commands/tui.py` keeps the lazy import). |
| R3 | **Lazy-import cycle `scheduler` ↔ `services.irrigation`** | No import is hoisted. `_job_session` lives in `scheduler.py`; `services/irrigation.py` keeps its function-level `from greenhouse_server.scheduler import ...`. import-linter contract pins the two allowed `services → scheduler` edges so no new one appears. |
| R4 | **`scheduler._app` trap** | Job bodies pass the *current* global to `_job_session(_app, ...)`; `schedule_pump_watcher` keeps its closure (captures app/registry at schedule time); no default-argument or module-level capture of `_app`. |
| R5 | **Logger names = module names** | No logging code changes module. Moved-module code (CSV, sync_plants, TUI builders, weekday helpers) does not log. |
| R6 | **Textual name coupling** | No `action_*`/`on_*`/ids/classes/`BINDINGS`/`CSS_PATH` change; new private names avoid Textual's dispatch prefixes; `refill` keys/cells identical. |
| R7 | **Goldens vs additive surface** — adding constants (§4) or new imports to frozen modules changes the G10 (`dir()`) / G11 (constants) snapshots | Decision needed from the orchestrator *before* Phase 4: either (a) G10/G11 assert *golden ⊆ actual with equal values* plus a reviewed allow-list of added names, or (b) each constants commit regenerates the golden and the reviewer verifies the diff is **add-only**. Until decided, new imports in frozen modules are underscore-aliased (`from contextlib import contextmanager as _contextmanager`). BRIEF rule 8 forbids silently editing goldens — this must be an explicit policy. |
| R8 | **OpenAPI drift from Pydantic docstrings/order** | `schemas.py` and the route-local models (`routes/plants.SnapshotResponse`, `routes/auth.*`) are untouched. TypedDicts are never used as FastAPI annotations. |
| R9 | **Dict key order / identity** in service results consumed by templates | Builders preserve insertion order; `_with_error` mutates in place like today. |
| R10 | **Clock call order** (engine `evaluated_at`, irrigation `started_at` after `adapter.start`, pump-watcher injected clock, maintenance/forecast `now`) | Each clock read stays at the same point; `_outcome` receives a precomputed elapsed. |
| R11 | **DB/plant-DB call order** (mock-sensitive) | Separate passes in `detect_conflicts`, `_gather_inputs` order, `get_sensors_in_cluster` after plant care, `get_preferences` not cached. |
| R12 | **Low coverage before touching** (`scheduler.py` 56 % lines, `services/irrigation.py` 148-223, `plant_dashboard` 46 % br, `logic/stress` no direct test) | Those commits are gated on the Phase-1 characterization items (I-iii, pipeline golden, G3). |
| R13 | **Mixed-concern commits** | One transformation per commit; constants definitions separate from adoption; formatting (`ruff --fix` RET505 etc.) its own commit. |

**Deliberately NOT done:** no `src/` layout; no package splits (`repository`, `schemas`, `utils`); no job-plumbing move;
no rule registry; no typed result dataclasses or Pydantic service results; no Unit of Work; no DI container; no device
restructuring (`_parse_dps`, keep-alive); no merging of drifted copies (window validation, irrigator/sensor create,
`has_alerts`, vacation validation, relative time, moisture-target parsers); no learner-3× de-duplication; no
health-monitor cache seeding; **no bug fixes** (B-1…B-25 stay, each pinned and listed in REFACTOR_NOTES); no stale route
docstring edits (B-25); no Pydantic class docstrings added; no enum-isation of stringly vocabularies.

---

## 8. Tooling ratchet

### 8.1 mypy (strict on touched modules only)

Add `mypy` to the dev group. Root `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
exclude = ["migrations/versions/"]
files = ["libs/greenhouse-core/greenhouse_core", "libs/greenhouse-server/greenhouse_server", "libs/greenhouse-cli/greenhouse_cli"]

[[tool.mypy.overrides]]
module = [   # grows one entry per finished decomposition commit; never shrinks
  "greenhouse_cli.client", "greenhouse_cli.commands._helpers", "greenhouse_cli.tui.render",
  "greenhouse_server.web.weekdays",
  "greenhouse_core.logic.trends", "greenhouse_core.logic.stress", "greenhouse_core.logic.sensors",
  "greenhouse_core.logic.plant_needs", "greenhouse_core.logic.timing",
]
strict = true
```

`make check` gains `uv run mypy` (Makefile only; `.github/workflows` is out of scope). Default-mode baseline (176 errors)
is **not** gated globally — the gate is "strict modules are clean", and `refactor/baseline/mypy-default.txt` count may
only go down (a one-line script comparing counts can live in `refactor/`).

### 8.2 ruff additions that pass today (via per-file ignores of current offenders)

```toml
[tool.ruff.lint]
extend-select = ["C90", "PLR0911", "PLR0912", "PLR0915", "TID251"]

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"time.time".msg = "import the module (`import time`) and call time.time(): tests monkeypatch the module attribute"

[tool.ruff.lint.per-file-ignores]
# ratchet: delete a line when its function is decomposed (00-baseline: C901 18, PLR0912 7, PLR0915 6, PLR0911 5)
"libs/greenhouse-core/greenhouse_core/learning/issues.py" = ["C901", "PLR0912", "PLR0915"]
"libs/greenhouse-server/greenhouse_server/services/irrigation.py" = ["C901", "PLR0911", "PLR0912", "PLR0915"]
# … one line per current offender from refactor/baseline/ruff-complexity-rules.txt
```

`TID251` on `time.time` bans `from time import time`, encoding the patch-seam rule (do-not-do #5). Later, once Phase 5-10
land: `RET` (after a separate `--fix` formatting commit for the 4 hits) and `SIM` excluding `SIM105` (try/except/pass →
`suppress` adds imports to frozen modules). Not proposed: `PLC0415` (lazy imports are deliberate), `D` (would force
docstrings on Pydantic classes → OpenAPI), `ARG` (FastAPI dependency params are intentionally unused), `PLR2004`
(tests-heavy; constants commits cover libs).

### 8.3 import-linter contracts (encode today's layering; verified against `refactor/baseline/import-graph.txt`)

```toml
[tool.importlinter]
root_packages = ["greenhouse_core", "greenhouse_server", "greenhouse_cli"]

[[tool.importlinter.contracts]]
name = "CLI talks HTTP only"
type = "forbidden"
source_modules = ["greenhouse_cli"]
forbidden_modules = ["greenhouse_core", "greenhouse_server"]

[[tool.importlinter.contracts]]
name = "Core is a leaf"
type = "forbidden"
source_modules = ["greenhouse_core"]
forbidden_modules = ["greenhouse_server", "greenhouse_cli"]

[[tool.importlinter.contracts]]
name = "API routes and web routes are independent"
type = "independence"
modules = ["greenhouse_server.routes", "greenhouse_server.web"]

[[tool.importlinter.contracts]]
name = "Services never reach up into HTTP layers"
type = "forbidden"
source_modules = ["greenhouse_server.services"]
forbidden_modules = ["greenhouse_server.routes", "greenhouse_server.web", "greenhouse_server.app", "greenhouse_server.deps"]

[[tool.importlinter.contracts]]
name = "Only the two known services touch the scheduler"
type = "forbidden"
source_modules = ["greenhouse_server.services"]
forbidden_modules = ["greenhouse_server.scheduler"]
ignore_imports = [
  "greenhouse_server.services.irrigation -> greenhouse_server.scheduler",      # lazy, documented cycle
  "greenhouse_server.services.system_health -> greenhouse_server.scheduler",
]

[[tool.importlinter.contracts]]
name = "Devices do not depend on decision logic or persistence"
type = "forbidden"
source_modules = ["greenhouse_core.devices"]
forbidden_modules = ["greenhouse_core.logic", "greenhouse_core.learning", "greenhouse_core.repository"]

[[tool.importlinter.contracts]]
name = "Data definitions stay below behaviour"
type = "forbidden"
source_modules = ["greenhouse_core.models", "greenhouse_core.schemas", "greenhouse_core.constants"]
forbidden_modules = ["greenhouse_core.logic", "greenhouse_core.learning", "greenhouse_core.repository", "greenhouse_core.devices"]

[[tool.importlinter.contracts]]
name = "Decision logic does not drive devices; learning only via the engine's lazy hook"
type = "forbidden"
source_modules = ["greenhouse_core.logic"]
forbidden_modules = ["greenhouse_core.devices", "greenhouse_core.learning"]
ignore_imports = ["greenhouse_core.logic.engine -> greenhouse_core.learning"]
```

All eight correspond to zero violating edges in the baseline graph (checked with `grep` over `import-graph.txt`; step
0.2 must confirm with `lint-imports`, since grimp also sees `TYPE_CHECKING` imports). Plus one tiny test (Phase 0,
test-only): importing `greenhouse_cli.main` must not put `textual` in `sys.modules` (keeps CLI startup at ~0.2 s).

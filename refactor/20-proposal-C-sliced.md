# 20 — Proposal C: feature/domain-sliced structure

Author: Architect C (vertical-slice lens). Phase 2, read-only on code. Inputs: `BRIEF.md` (owner decisions binding: keep
`libs/` uv workspace, no `src/`; method-level cleanup is first-class), `CLAUDE.md`, `00-map.md`, `00-smells.md`,
`00-contracts.md`, `00-baseline.md`, `00-tests.md`, the Phase-1 contract tests already in the tree
(`tests/server/test_contract_{openapi,mcp,web_html,pipeline,scheduler,settings,sync_service}.py`,
`tests/test_contract_{decision_grid,sync}.py`, `tests/cli/test_contract_{help,json_output,tui}.py`,
`tests/devices/test_contract_adapters.py`) and their goldens under `tests/golden/`. Line numbers are at `a1b2622`.

---

## 0. Verdict of the lens (read this first)

**The repo is already sliced at file level, so the slice lens does not mean big moves.** Each package is layer-first
(`routes/`, `web/routes/`, `services/`, `commands/`), but within each layer there is already one module per resource
(`routes/irrigators.py` ↔ `web/routes/irrigators.py` ↔ `commands/irrigators.py`). A new contributor who greps `irrigator`
already finds the pieces. What the slice lens actually fixes is narrower and concrete:

1. **Slice knowledge sits in the wrong layer.** Rules that both interfaces need (window validation, weekday vocabulary,
   plant-DB sync, irrigator create + capacity, in-cluster lookups, the vacation range rule) live *inside route handlers* and
   are copied between `routes/` and `web/routes/`. Several copies have drifted (00-smells §A10, bugs B-7/B-8/B-13/B-16).
   Fix: each slice gets a **service-layer home** for its shared rules. Routes keep only the I/O mapping (status codes, detail
   strings, templates). This is the main payoff and it needs **no file moves**, only new slice modules.
2. **Three god modules mix slices.** `schemas.py` (100 DTOs from 17 slices), `repository.py` (13 aggregates) and
   `services/irrigation.py` (pipeline + pump-watcher lifecycle + leak-check lifecycle). Each splits along slice lines
   **behind an unchanged facade**: same import path, same `dir()`, same class.
3. **One god screen.** `ClusterScreen` (800 LOC) runs 9 tabs and three `if/elif` CRUD dispatch chains. It splits into
   per-tab controllers (plain objects, not widgets) plus pure row builders.

What the lens must **not** do in this repo, and why (evidence below):
- Do not create cross-layer feature packages (`features/clusters/{api,web,service}.py`). The uv package boundary already
  splits slices across three distributions (owner decision). A feature package would rename loggers. It would also break
  `tests/golden/web/routes.json`, which pins `endpoint.__module__.__qualname__` for all 85 web routes
  (`test_contract_web_html.py:466`), and it removes no smell.
- Do not move a module only to rename it (`services/cluster.py` → `clusters.py`, `routes/operations.py` → several
  modules). Neither move removes a cost.

Every move below names the cost it removes. Every new name is chosen to stay out of the Phase-1 goldens.

---

## 1. Slice index (where a contributor looks for a feature)

This is the target end state. **Bold** = new module or changed responsibility. Everything else already exists and stays put.

| Slice | core: DTOs | core: data access | server: shared rules (service layer) | API glue | web glue | CLI / TUI |
|---|---|---|---|---|---|---|
| clusters | **`schemas/_clusters.py`** | **`repository/_clusters.py`** | `services/cluster.py` (status/history) | `routes/clusters.py` | `web/routes/clusters.py` | `commands/clusters.py`, `tui/screens/cluster.py` |
| plants (+ plant-DB sync, plant health) | **`schemas/_plants.py`** | **`repository/_plants.py`**, **`_plant_health.py`** | `services/cluster.py` **`+ sync_plants`**, `services/health.py` | `routes/plants.py` | `web/routes/plants.py`, `plant_dashboard.py` | `commands/plants.py`, **`cluster_tabs.PlantsTab`** |
| sensors & readings | **`schemas/_sensors.py`** | **`repository/_sensors.py`**, **`_readings.py`** | `services/sync.py` | `routes/sensors.py` | `web/routes/sensors.py` | `commands/sensors.py`, **`SensorsTab`** |
| irrigators & events (manual control) | **`schemas/_irrigators.py`** | **`repository/_irrigators.py`** | **`services/irrigators.py`** (new), `manual_control.py`, `bulk.py` | `routes/irrigators.py`, `bulk.py` | `web/routes/irrigators.py` | `commands/irrigators.py`, **`OverviewTab`** |
| configs | **`schemas/_configs.py`** | **`repository/_configs.py`** | — | `routes/configs.py` | `web/routes/configs.py` | `commands/configs.py`, **`ConfigTab`** |
| irrigation windows | **`schemas/_windows.py`** | **`repository/_windows.py`** | **`services/windows.py`** (new: weekday vocabulary + window rule) | `routes/windows.py` | `web/routes/windows.py` | `commands/windows.py`, **`WindowsTab`** |
| vacation | **`schemas/_vacation.py`** | **`repository/_vacation.py`** | `services/vacation.py` **`+ vacation_range_is_valid`** | `routes/vacation.py` | `web/routes/vacation.py` | `commands/vacation.py` |
| preferences | **`schemas/_preferences.py`** | **`repository/_preferences.py`** | — | `routes/preferences.py` | `web/routes/preferences.py` | `commands/preferences.py` |
| alerts | **`schemas/_alerts.py`** | **`repository/_alerts.py`** | `services/alerts.py` | `routes/alerts.py` | `web/routes/alerts.py` | `commands/alerts.py` |
| activity | **`schemas/_activity.py`** | **`repository/_activity.py`** | — | `routes/activity.py` | `web/routes/activity.py` | `tui/screens/activity.py` |
| decisions (engine) | **`schemas/_decisions.py`**, `logic/decision.py` | **`repository/_decisions.py`** | `logic/engine.py` (decomposed) | `routes/decisions.py` | `web/routes/decisions.py` | **`DecisionsTab`** |
| operations (pipeline) | **`schemas/_operations.py`** | — | `services/irrigation.py` (**pipeline only**) | `routes/operations.py` | `web/routes/operations.py` | `commands/operations.py` |
| leak detection | — | — | `services/leak.py` **+ leak-check lifecycle** (moved in) | — | — | — |
| pump watcher | — | — | `services/pump_watcher.py` **+ watcher lifecycle** (moved in) | — | — | — |
| scheduling | **`schemas/_system.py`** | — | `scheduler.py` (**`_job_session`**) | `routes/scheduler.py` | `web/routes/analytics.py` (scheduler part, see §7.3) | `commands/scheduler.py` |
| charts / analytics | **`schemas/_charts.py`**, **`_analytics.py`** | — | `services/charts.py`, `forecast.py`, `efficacy.py`, `insights.py`, `data_quality.py` | `routes/charts.py` … | `web/routes/…` | **`ChartsTab`**, **`InsightsTab`** |
| search | **`schemas/_search.py`** | — | `services/search.py` | `routes/search.py` | — | `tui/screens/search.py` |
| auth | (`routes/auth.py` models, server-only) | `core/auth.py` | `server/auth.py` | `routes/auth.py` | `web/routes/auth.py` | `commands/auth.py` |

Optional: add this table as the docstring of the empty package markers `greenhouse_server/services/__init__.py` and
`greenhouse_server/routes/__init__.py`. It is code, so it lives next to the files and is not a new doc file.

---

## 2. Target trees (only what changes)

### 2.1 `greenhouse_core`
```
greenhouse_core/
  schemas.py              → schemas/__init__.py   facade; verbatim original import block; re-exports 100 classes (no __all__)
  schemas/_common.py      NEW  SuccessResponse; _parse_json_config(v) (shared body of the 2 duplicate validators)
  schemas/_clusters.py    NEW  …one private module per slice, exact grouping in §3
  schemas/_plants.py, _irrigators.py, _sensors.py, _windows.py, _configs.py, _operations.py, _system.py,
          _charts.py, _alerts.py, _activity.py, _decisions.py, _analytics.py, _vacation.py, _preferences.py, _search.py
  repository.py           → repository/__init__.py  facade; verbatim original import block; errors; IrrigationRepository(mixins…)
  repository/_base.py     NEW  _RepositoryCore: session holder + _patch/_delete/_paginate helpers
  repository/_clusters.py NEW  …one private mixin module per slice, exact grouping in §4
  repository/_plants.py, _plant_health.py, _irrigators.py, _sensors.py, _readings.py, _configs.py, _decisions.py,
             _activity.py, _alerts.py, _vacation.py, _windows.py, _preferences.py
  logic/engine.py         same file; decide_for_cluster + _apply_soil_moisture_rule decomposed (§5.2)
  logic/timing.py         + active_quiet_window(...)   (shared by engine and web cluster_detail, §6)
  logic/trends.py, logic/stress.py, learning/issues.py   decomposed in place (patch targets stay in place)
```
**Why the slice modules are private (`_name.py`).** `tests/test_contract_imports.py` (G10) treats
`contracts/imports.json` as a *lower bound*, so a public submodule attribute (`greenhouse_core.schemas.clusters`) would
not break it. The reason for privacy is design: one public import path per surface. Without it, consumers would start
importing `greenhouse_core.schemas.clusters`, and that path would become a new de-facto contract that freezes the slicing.
Private names also keep `dir()` of the frozen modules exactly as it is today.

### 2.2 `greenhouse_server`
```
greenhouse_server/
  app.py                  create_app decomposed into ordered private steps; _API_ROUTERS tuple (order = OpenAPI order)
  deps.py                 + require_found(obj, detail) and slice 404 translators (require_plant_in_cluster, …) next to require_cluster
  scheduler.py            + _job_session(failure_message) context manager; the 5 job functions stay HERE (func_ref golden)
  services/windows.py     NEW  WEEKDAY_BITS, WEEKDAY_LABELS, FULL_WEEK_MASK, format_weekday_mask(), WindowViolation, window_violation()
  services/irrigators.py  NEW  create_irrigator_with_capacity()
  services/cluster.py     + ClusterService.sync_plants(), PlantNotFoundError
  services/vacation.py    + vacation_range_is_valid()
  services/irrigation.py  pipeline only: run_irrigation_pipeline decomposed; PipelineResult/CheckResult/MonitorResult TypedDicts;
                          keeps `import time as _time`; re-exports the moved lifecycle names (incl. 2 private ones tests call)
  services/leak.py        + _leak_check_done, _run_leak_check, _add_leak_check_job, schedule_leak_check, rearm_leak_checks,
                          LEAK_CHECK_ACTIVITY_CODE   (moved from irrigation.py; logger name preserved, §10.1)
  services/pump_watcher.py + schedule_pump_watcher, handle_watcher_interrupted, WATCHER_SHUTDOWN_ACTIVITY_CODE (moved; logger preserved)
  services/health.py, services/forecast.py   decomposed in place
  routes/*, web/routes/*  glue only — call slice services; NO module moves (web route modules are pinned by golden)
```

### 2.3 `greenhouse_cli`
```
greenhouse_cli/
  client.py               _request -> Any; JSONObject/JSONList aliases; precise return types; _drop_none(); _API prefix
  commands/_helpers.py    + resolve_server_url(ctx)   (used by get_client, commands/auth.py:26, commands/tui.py:35)
  tui/rows.py             NEW  pure payload → table rows / Rich Text builders (no widgets, no I/O, no UPPERCASE names)
  tui/screens/base.py     + DataScreen.selected_row(table_id, rows)
  tui/screens/cluster.py  ClusterScreen keeps compose/BINDINGS/all action_*/on_* and delegates to tab controllers
  tui/screens/cluster_tabs.py NEW  plain (non-DOMNode) controllers: OverviewTab, ChartsTab, PlantsTab, SensorsTab,
                          InsightsTab, DecisionsTab, HistoryTab, WindowsTab, ConfigTab
```

---

## 3. `schemas.py` → `schemas/` facade + private slice modules

### 3.1 Exact grouping (100 classes; every class moves byte-identically)

| Private module | Classes (original order kept within each module) | Imports from sibling slices |
|---|---|---|
| `_common.py` | `SuccessResponse` (+ private `_parse_json_config`) | — |
| `_clusters.py` | `ClusterBase`, `CreateClusterRequest`, `ClusterResponse`, `ClusterDetailResponse`, `UpdateClusterRequest` | `_plants.PlantResponse`, `_sensors.SensorResponse`, `_irrigators.IrrigatorResponse`, `_configs.ConfigResponse`, `_windows.IrrigationWindowResponse` |
| `_plants.py` | `PlantBase`, `CreatePlantRequest`, `PlantResponse`, `PlantListResponse`, `SyncPlantsRequest`, `MovePlantRequest`, `SyncPlantsResponse`, `UpdatePlantRequest`, `PlantHealthDailyResponse`, `PlantHealthResponse` | — |
| `_irrigators.py` | `IrrigatorBase`, `CreateIrrigatorRequest`, `IrrigatorResponse`, `IrrigatorListResponse`, `StartIrrigatorRequest`, `LogManualRequest`, `IrrigatorActionResponse`, `LogManualResponse`, `UpdateIrrigatorRequest`, `IrrigationEventResponse`, `StopAllResponse` | `_common._parse_json_config` |
| `_sensors.py` | `SensorBase`, `CreateSensorRequest`, `SensorResponse`, `SensorListResponse`, `SensorAssignmentResponse`, `SensorAssignmentListResponse`, `UpdateSensorRequest`, `SensorReadingResponse` | `_common._parse_json_config` |
| `_windows.py` | `IrrigationWindowBase`, `CreateIrrigationWindowRequest`, `UpdateIrrigationWindowRequest`, `IrrigationWindowResponse`, `IrrigationWindowListResponse` | — |
| `_configs.py` | `SetConfigRequest`, `ConfigResponse`, `GlobalConfigResponse`, `UpdateGlobalConfigRequest`, `ResolvedConfigField`, `EffectiveConfigResponse` | — |
| `_operations.py` | `IrrigateRequest`, `AlertResponse`, `ReasonResponse`, `IrrigateResponse`, `SensorStatusResponse`, `MonitorResponse`, `CheckClusterResponse`, `CheckAllResponse`, `SyncRequest`, `SyncResponse`, `LearnResponse`, `SensorHistoryResponse`, `IrrigatorHistoryResponse`, `HistoryResponse`, `StatsResponse`, `ClusterStatusSensorResponse`, `ClusterStatusIrrigatorResponse`, `ClusterStatusResponse` | `_clusters.ClusterResponse`, `_configs.ConfigResponse`, `_plants.PlantResponse`, `_sensors.SensorReadingResponse`, `_irrigators.IrrigationEventResponse` |
| `_system.py` | `SchedulerJobResponse`, `CreateSchedulerJobRequest`, `SchedulerStateResponse`, `HealthResponse`, `SystemHealthDevice`, `SystemHealthResponse`, `DataQualityIssue`, `DataQualityReport` | — |
| `_charts.py` | `ChartDatasetResponse`, `ChartEventResponse`, `ChartThresholdResponse`, `ChartPayloadResponse`, `OverlayDataset`, `MultiMetricOverlayResponse`, `HeatmapCell`, `HeatmapResponse`, `PlantHealthTimelineResponse` | — |
| `_alerts.py` | `AlertSummary`, `AlertListResponse` | — |
| `_activity.py` | `ActivityEventResponse`, `ActivityListResponse` | — |
| `_decisions.py` | `DecisionLogResponse`, `DecisionLogListResponse` | — |
| `_analytics.py` | `ForecastResponse`, `CareInsight`, `ClusterInsightsResponse`, `EfficacyItemResponse`, `EfficacyListResponse` | — |
| `_vacation.py` | `VacationCreateRequest`, `UpdateVacationWindowRequest`, `VacationResponse`, `VacationListResponse` | — |
| `_preferences.py` | `PreferencesResponse`, `PreferencesUpdateRequest` | — |
| `_search.py` | `SearchHit`, `SearchResponse` | — |

Count: 1+5+10+11+8+5+6+18+8+9+2+2+2+5+4+2+2 = 100. Grouping rule: a DTO lives with the slice whose route module returns or
accepts it. The `ClusterStatus*` classes go to `_operations.py` because `routes/operations.py:cluster_status` builds them.
The import graph between slice modules is a DAG (leaves: plants, sensors, irrigators, configs, windows; then clusters;
then operations).

### 3.2 Concrete costs removed
- The forward-reference hack goes away: the `"PlantResponse"`-style string annotations plus the module-tail
  `ClusterDetailResponse.model_rebuild()` (schemas.py:27-28, 995-999). `_clusters.py` imports its five dependencies, so the
  (unchanged) string annotations resolve when the class is created. The `model_rebuild()` call becomes redundant and is
  dropped in a separate commit, with G1 as proof.
- The duplicated `parse_config` validator (schemas.py:115-120 ≡ 180-185; also a jscpd clone) gets one body,
  `_common._parse_json_config`. Both classes keep a method named `parse_config` with the same decorator, so the validator
  metadata is unchanged.
- Navigation: `routes/vacation.py` ↔ `schemas/_vacation.py` ↔ `repository/_vacation.py`. Today it is one 999-line file
  with 17 banner sections.

### 3.3 Re-export and identity rules
- `schemas/__init__.py` keeps the **verbatim** original header (`import json`, `from pydantic import BaseModel, ConfigDict,
  Field, field_validator`). Those names are part of today's `dir(greenhouse_core.schemas)`. It then re-exports each class
  with the explicit form `from greenhouse_core.schemas._plants import PlantResponse as PlantResponse`. That satisfies ruff
  F401 **without adding `__all__`**. G10 would accept a new `__all__` (lower bound), but an `__all__` would itself become
  a new list to keep in sync, which is not needed here.
- Class bodies move **byte-for-byte**: no docstring added, no field reordered, no annotation changed. A docstring becomes
  the OpenAPI `description` (00-smells §C1). Field order is the OpenAPI property order.
- FastAPI names components by `__name__`. It falls back to module-qualified names only when two *distinct* classes share
  a name. There are no collisions today (`routes/auth.py` `LoginRequest`/…, `routes/plants.py` `SnapshotResponse` are
  unique), and a re-export is the same object. So `__module__` changes do not reach OpenAPI.
- Evidence per commit:
  1. `test_contract_openapi.py::test_openapi_document_matches_golden` and `::test_openapi_matches_phase0_baseline_fingerprint`
     (sha256 `f983a5a8…`).
  2. `test_contract_mcp.py::test_mcp_surface_matches_golden` and `::…baseline_fingerprint` (`dc15b6ab…`).
  3. The G10 import-surface golden (`tests/test_contract_imports.py`, `contracts/imports.json`, lower bound).
  4. A **new** guard test `tests/test_contract_schema_classes.py`, added before the first move. It snapshots
     `{name: (cls.__doc__, list(cls.model_fields), repr(cls.model_config), json.dumps(cls.model_json_schema(), sort_keys=True))}`
     for all 100 names.
  5. A refactor-evidence script (in the commit message, not a test) that compares `ast.dump()` of every `ClassDef`
     before and after.

---

## 4. `repository.py` → `repository/` facade + slice mixins

### 4.1 Exact grouping (every method; `→` = cross-slice call)

| Private module / mixin | Methods |
|---|---|
| `_base.py` `_RepositoryCore` | `__init__(session)`; private helpers `_patch`, `_delete`, `_paginate` (§5.9) |
| `_clusters.py` `_ClusterRepo` | `add_cluster`, `get_cluster`, `list_clusters`, `update_cluster`, `delete_cluster` |
| `_plants.py` `_PlantRepo` | `add_plant`, `get_plants_in_cluster`, `get_plant`, `list_all_plants`, `update_plant`, `delete_plant` (→ `_close_open_sensor_assignment`), `move_plant` (→ `add_activity_event`), **additive** `get_plant_in_cluster(cluster_id, plant_id)` |
| `_plant_health.py` `_PlantHealthRepo` | `upsert_plant_health`, `list_plant_health_history` |
| `_irrigators.py` `_IrrigatorRepo` | `add_irrigator`, `get_irrigator`, `get_irrigator_for_cluster`, `list_all_irrigators`, `update_irrigator`, `delete_irrigator`, `add_irrigation_event`, `get_recent_events`, `irrigator_consumption_liters` |
| `_sensors.py` `_SensorRepo` | `add_sensor`, `_open_sensor_assignment`, `_close_open_sensor_assignment`, `reassign_sensor_to_plant` (→ `add_activity_event`), `sensor_assignments_for_plant`, `list_sensor_assignments`, `get_sensors_in_cluster`, `get_sensor`, `list_all_sensors`, `update_sensor`, `delete_sensor`, **additive** `get_sensor_in_cluster(cluster_id, sensor_id)` |
| `_readings.py` `_ReadingRepo` | `readings_for_plant`, `add_sensor_reading`, `get_last_reading_timestamp`, `get_latest_reading`, `bulk_add_sensor_readings`, `get_recent_readings`, `get_readings_around` |
| `_configs.py` `_ConfigRepo` | `_CONFIG_PATCHABLE_FIELDS` (class attr), `set_irrigation_config`, `get_irrigation_config`, `get_global_irrigation_config`, `update_global_irrigation_config`, `get_effective_config`; module constant `_GLOBAL_CONFIG_DEFAULTS` |
| `_decisions.py` `_DecisionRepo` | `add_decision_log`, `set_decision_actuated`, `list_decision_logs` |
| `_activity.py` `_ActivityRepo` | `add_activity_event`, `list_activity_events` |
| `_alerts.py` `_AlertRepo` | `upsert_alert`, `list_alerts`, `get_alert`, `get_active_alert`, `acknowledge_alert`, `resolve_alert`, `count_open_alerts` |
| `_vacation.py` `_VacationRepo` | `add_vacation_window`, `list_vacation_windows`, `get_active_vacation`, `update_vacation_window`, `delete_vacation_window`, **additive** `get_vacation_window(window_id)` (retires `routes/vacation.py:82-84` session bypass) |
| `_windows.py` `_WindowRepo` | `list_irrigation_windows`, `get_irrigation_window`, `add_irrigation_window`, `update_irrigation_window`, `delete_irrigation_window`, **additive** `get_window_in_cluster(cluster_id, window_id)` |
| `_preferences.py` `_PreferencesRepo` | `get_preferences`, `update_preferences` |

`repository/__init__.py`:
```python
"""Database operations for the irrigation system (replaces IrrigationDB)."""   # original docstring
import json                                   # ┐ verbatim original import block:
import time                                   # │ keeps today's dir() (func, select, Cluster, DEFAULT_*, ENTITY_*, …)
from sqlalchemy import func, select           # │ and keeps `repo_mod.time` resolvable (tests/server/test_scheduler.py:158-164)
...                                           # ┘ (# noqa: F401 where now unused)
class SameClusterMoveError(ValueError): ...   # stay defined HERE: same __module__/qualname as today
class IrrigatorExistsError(ValueError): ...
from greenhouse_core.repository._clusters import _ClusterRepo   # mixins import the errors from the package (see below)
...
class IrrigationRepository(_ClusterRepo, _PlantRepo, _PlantHealthRepo, _IrrigatorRepo, _SensorRepo, _ReadingRepo,
                           _ConfigRepo, _DecisionRepo, _ActivityRepo, _AlertRepo, _VacationRepo, _WindowRepo,
                           _PreferencesRepo, _RepositoryCore):
    """<original docstring>"""
```
Import-order detail: the two error classes are defined **before** the mixin imports. `_plants.py` and `_irrigators.py`
then do `from greenhouse_core.repository import SameClusterMoveError` (resp. `IrrigatorExistsError`) against a partially
initialised package, which is legal because both names already exist. That keeps the exceptions' `__module__` at
`greenhouse_core.repository`. The cleaner alternative, `_errors.py`, would change the exception reprs; it is rejected
because exception reprs in tracebacks and logs would change.

### 4.2 Typing the mixins (no stubs, no protocols)
- Every mixin subclasses `_RepositoryCore`, so `self.session: Session` is typed.
- The three cross-slice calls (`delete_plant` → `_close_open_sensor_assignment`, `move_plant` → `add_activity_event`,
  `reassign_sensor_to_plant` → `add_activity_event`) use a **self-type** annotation,
  `def move_plant(self: IrrigationRepository, …)`, with `IrrigationRepository` imported under `TYPE_CHECKING`. mypy accepts
  self-types that are subtypes of the mixin, and the runtime is unaffected.
- Guard (new test, added before the split): every public name in today's `dir(IrrigationRepository)` still exists with an
  identical `inspect.signature`, and the method-name sets of the 13 mixins are pairwise disjoint, so the MRO cannot shadow
  anything. The test checks a superset, so the additive lookups are allowed.

### 4.3 Clock and patch constraints
- Every mixin module does `import time` and calls `time.time()` by attribute (24 sites, including the `timestamp or
  int(time.time())` shape that treats `0` as "now"; keep it). `tests/server/test_scheduler.py:164` patches
  `repo_mod.time.time`, which means the global `time` module. That reaches every submodule as long as nobody writes
  `from time import time`.
- `IrrigationRepository(session)` construction, all 80 public method signatures and `_CONFIG_PATCHABLE_FIELDS` resolution
  (via the MRO) are unchanged. No consumer import changes (40 prod + 27 test importers).

### 4.4 Concrete costs removed
The 1309-line breadth becomes about 13 modules of 40-180 lines, each the data access of one slice. The copy-paste
(7 patch loops, 6 delete bodies, 3 paginators) is folded in `_base.py`, and **only** where the semantics are byte-identical
(§5.9). The new home for additive reads retires 4 `repo.session` bypasses in routes (`routes/vacation.py:82-84`;
`web/routes/vacation.py:90` scan; `routes/plants.py:221-228` and `web/routes/operations.py:117-124` scans, §6).

---

## 5. Method-by-method decomposition

General rules for every row:
- An extraction stays **in the same module** whenever a test patches a name in that module's namespace
  (`irrigation_mod._time`, `engine_mod.time`, `engine.season_for`, `issues.seasonal_light_factor`/`effective_light_threshold`).
- Call order of every repository, plant-DB, weather and device call is preserved.
- Result-dict **key insertion order** is preserved (templates may iterate).
- New helpers are keyword-only where call sites would be ambiguous.

### 5.1 `IrrigationService.run_irrigation_pipeline` (services/irrigation.py:422-608, 187 lines, CC 21) — stays in `services/irrigation.py`
```python
class PipelineResult(TypedDict, total=False):   # runtime = plain dict → zero ripple into IrrigateResponse(**r)/templates
    action: str; reason: str; confidence: float; duration_minutes: int; interval_hours: int
    stress_indicators: dict[str, Any]; reasons: list[dict[str, Any]]; temperature: float; temperature_source: str
    blocking_alarms: list[str]

@dataclass(frozen=True)
class _Actuation:                                # parameter object for the actuation half
    cluster_id: int; irrigator: Irrigator; adapter: AbstractIrrigatorAdapter
    decision: IrrigationDecision; temp: float; source: str; sensor_data: dict | None
```
| New function | Signature | Responsibility |
|---|---|---|
| `run_irrigation_pipeline` (kept, same params) | `(...) -> PipelineResult` | Orchestrates. Order: cluster lookup → `_resolve_temperature` → `_decide` → result → skip/dry-run branch → target → health gate → actuate. |
| `_error_result` | `(reason: str) -> PipelineResult` | `{"action": "error", "reason": reason, "confidence": 0}`, the two early exits ("cluster not found", "no data for decision"). `routes/operations.py:149` string-matches "cluster not found", so the literal stays. |
| `_decide` | `(self, cluster_id, temp, *, force: bool) -> IrrigationDecision \| None` | Builds `IrrigationLogic` and calls `decide_for_cluster(..., persist=True, triggered_by="manual" if force else "auto", bypass_quiet_hours=force)`. |
| `_decision_result` | `(decision, temp, source) -> PipelineResult` | The 9-key dict, same key order. |
| `_record_skip` | `(self, cluster_id, decision) -> None` | `decision_skip` activity, severity `info`. Called only when `not dry_run` (branch kept: `if dry_run or decision.action.value == "skip": if not dry_run: …`). |
| `_resolve_target` | `(self, cluster_id) -> tuple[Irrigator, Adapter] \| str` | In order: no irrigator → `"no irrigators found"`; `registry is None` → `"no device registry"`; `UnknownDeviceModel` → `f"no adapter for irrigator: {exc}"`. The caller sets `result["action"]="error"; result["reason"]=…` (mutation keeps key positions). |
| `_health_block` | `(self, result, decision, cluster_id, irrigator) -> PipelineResult \| None` | Only when `health_monitor` is set. Appends the CRITICAL reason, flips to SKIP, writes the `decision_skip` warning activity with payload, sets `action/reason/reasons/blocking_alarms` in today's order. Bug B-6 (no re-persist) is kept, and the stale comment at 497-501 is fixed (comment only). |
| `_actuate` | `(self, act: _Actuation, result) -> PipelineResult` | `adapter.start` **first**, then `soil_note`, then `started_at = int(_time.time())` (**must stay in this module**: `test_leak_rearm.py:184` patches `irrigation_mod._time`), then the event row. |
| `_on_started` | `(self, act, started_at) -> None` | `irrigated` activity → `set_decision_actuated` → `schedule_leak_check` → `schedule_pump_watcher` → `maybe_notify`. Order kept. |
| `_on_start_failed` | `(self, act, output) -> None` | `actuation_failed` activity, then `raise_alert(...)`. |
| `_soil_note` | `(sensor_data) -> str` | `", soil=…% (driest)"` or `""`. |

`check_cluster` (687-716): `_check_result(cluster, *, action, alerts, maintenance, notes=None, needs_water=None) -> CheckResult`
builds the three dicts with today's key order (`notes` for skipped/irrigate, `needs_water` for monitored). Call order kept:
learning alerts, then maintenance, then `get_effective_config`, then pipeline or monitor, then `sync_cluster_alerts`. The
learner running 3× per check (smell A6) is **not** changed; that would be a performance change and needs proof of identical
outputs. `check_all_clusters` keeps calling `self.check_cluster`, because tests patch that method
(`test_operations.py:260`, `test_contract_check_all.py:74`).

`monitor_cluster` (610-671): extract `_sensor_status(sensor, plant, readings) -> SensorStatus` and
`_monitor_band(care) -> tuple[float, float]`. The latter keeps the **local** parse semantics: `float(x) for x in split("-")`
plus 2-tuple unpack and `except Exception → (45.0, 65.0)`, so a 3-part target string falls back here while
`parse_moisture_target` would accept it. Do **not** fold it into `parse_moisture_target`; pin it instead.

### 5.2 `IrrigationLogic.decide_for_cluster` (engine.py:106-266, CC 18) and `_apply_soil_moisture_rule` (678-752, CC 21) — stays in `logic/engine.py`
```python
@dataclass(frozen=True)
class _EngineInputs:            # parameter object for step 7 (00-smells A1 table)
    snapshot: SensorSnapshot; trends: Trends; stress: StressIndicators; plant_care: list[dict]
    temp_range: tuple[float, float] | None; humidity_range: tuple[float, float] | None; water_needs: str
```
| New function | Signature | Responsibility / preserved semantics |
|---|---|---|
| `decide_for_cluster` (signature frozen) | unchanged | Order: `get_cluster` (None → return None, no log row) → `evaluated_at = int(time.time())` (`engine_mod.time` patched) → `get_plants_in_cluster` → `_pre_gate` → quiet gate → `_evaluate` → `_finalize`. |
| `_pre_gate` | `(self, cluster_id, evaluated_at, *, has_plants: bool) -> IrrigationDecision \| None` | First match wins: NO_PLANTS (confidence 0.0) → `_enforce_leak_hold` → `_enforce_cooldown`. No quiet-hours lookup happens for an empty cluster (as today). |
| `_quiet_skip` | `(cluster_id, evaluated_at, window) -> IrrigationDecision` | The QUIET_HOURS skip (message format unchanged). |
| `_emit` | `(self, decision, *, persist, triggered_by) -> IrrigationDecision` | Replaces the four `if persist: self._persist(...); return x` blocks. Early gates go through `_emit` and **never** get the override reason. |
| `_evaluate` | `(self, cluster, cluster_id, plants, evaluated_at, current_temp) -> IrrigationDecision` | Steps 6-20. Every path out of it ends in `_finalize`, which is exactly today's set of `_finalize` callers. |
| `_gather_inputs` | `(self, cluster_id, plants) -> _EngineInputs` | Order: `get_recent_sensor_data(hours=24)` → `analyze_historical_trends` → `detect_stress_conditions` → `_attach_learning_alerts` → care data → ranges → water needs. `get_sensors_in_cluster` stays **after** it, in `_evaluate`. |
| `_base_decision` | `(cluster_id, evaluated_at, inputs) -> IrrigationDecision` | SKIP, `DEFAULT_*`, confidence 0.5 (the literal may become a new constant in its own commit, §10.6). |
| terminal rules | `if _apply_water_warning_rule(d) or _apply_critical_stress_rule(d): return d` | Short-circuit `or` = today's sequential ifs. Fallback (8) and the window rule (12) keep bypassing vacation rationing (pinned behavior). |
| `_apply_adjustments` | `(self, decision, cluster, inputs, evaluated_at) -> None` | Soil → temp → humidity → light → water-needs → trend → seasonal (`season_for` via the engine global) → vacation. Fixed sequence, **no registry**. |
| `_finalize` | `(self, decision, *, quiet_window, bypass, persist, triggered_by) -> IrrigationDecision` | The former closure, now a method: adds MANUAL_OVERRIDE_QUIET_HOURS only when `quiet_window is not None and bypass`, then persists best-effort. |
| `_tz_name` | `(self) -> str \| None` | Replaces 3 copies of `prefs = self.db.get_preferences(); prefs.timezone if prefs else None` (281, 306, 334). It is called at the same points, so the call count does not change. |
| `_decision_with_reason` | drop the 3 never-passed params (`sensor_snapshot`, `stress_indicators`, `trends`) | All callers pass keywords; the defaults built today (`StressIndicators()`, `Trends()`) stay. |
| `_apply_window_rule` | `(self, cluster_id, evaluated_at)` (unused `cluster`, `decision` dropped) | Tests mention it only in docstrings (00-smells A1). |

`_apply_soil_moisture_rule(decision, plant_care)`, same signature:
| New helper | Signature | Responsibility |
|---|---|---|
| `_cluster_moisture_band` | `(plant_care) -> tuple[float, float]` | `min` of target mins, `max` of target maxes. The `"45-65"` default literal stays. |
| `_soil_extremes` | `(snapshot) -> tuple[float, float]` | driest/wettest, each falling back to the average. |
| `_conflict_reason` | `(snapshot, band, lo, hi) -> str \| None` | CONFLICT test `(lo < tmin) and (hi > tmax - CONFLICT_WET_MARGIN)` plus the dry/wet name lists. |
| `_set_irrigate` | `(decision, *, duration, interval, confidence) -> None` | Shared by water warning, stress, conflict, very-dry and dry (5 copies of the 4-line setter). |
| `_set_skip` | `(decision, *, confidence, interval=None) -> None` | Adequate, wet, over-watering. |
| `_moisture_outcome` | `(decision, snapshot, band, lo, hi) -> None` | The if/elif ladder in the same order: very dry `lo < tmin - VERY_DRY_MARGIN` → dry → **adequate tests `avg <= tmax` (average, not wettest)** → wet. |

### 5.3 `detect_conflicts` (learning/issues.py:154-304, 151 lines, CC 36) — stays in `issues.py` (patch targets)
| New function | Responsibility |
|---|---|
| `detect_conflicts(db, plant_db, cluster_id, profiles, plant_care)` (kept) | `sensors` → `_latest_moisture_by_sensor` → **early `return []` when fewer than 2 sensors have moisture**. This also skips the low-light and humidity checks; that is today's behavior and must be pinned. Then conflict alerts → `get_plants_in_cluster` → low-light → low-humidity, concatenated in that order. |
| `_latest_moisture_by_sensor(db, sensors) -> dict[int, float]` | Cleaned DESC series, mean of the newest 3. |
| `_target_band(sensor, plant_care) -> tuple[float, float]` | Local semantics: catches only `(ValueError, IndexError)`, defaults `45.0, 65.0`. Do not swap in `parse_moisture_target`, which catches everything. |
| `_split_dry_wet(sensors, moisture, plant_care) -> tuple[list, list]` | `< tmin - 5` dry, `> tmax` wet. |
| `_conflict_alerts(dry, wet, profiles) -> list[Alert]` | Nested projection, `LEARNING_OVER_WATER_THRESHOLD`. Message text byte-identical. |
| `_low_light_alerts(db, plant_db, sensors, plants_by_id) -> list[Alert]` | Calls `effective_light_threshold` **through the module global** (tests patch `greenhouse_core.learning.issues.effective_light_threshold`). |
| `_low_humidity_alerts(db, plant_db, sensors, plants_by_id) -> list[Alert]` | 48h cleaned, `< ideal - 15`. |

The same pattern applies to `detect_issues` (21-152, CC 24): one `_<issue>_alerts(...)` per numbered block, concatenated
in the original order.

### 5.4 `PlantHealthService.compute_score` (services/health.py:20-119, CC 29)
`PlantHealthScore(TypedDict)` with today's 6 keys in order. Helpers:
- `_empty_score() -> PlantHealthScore`
- `_care_bands(care) -> _Bands` (soil via `parse_moisture_target(… "45-65")`, temp/hum optional)
- `_pooled_clean_readings(sensors, days) -> list` (per-sensor cleaning **before** pooling)
- `_in_band_pct(values, lo, hi) -> float | None`
- `_first_efficiency(sensors, days) -> float | None` (first non-None profile, then `break`)
- `_composite(components) -> float | None` (`float(max(0, min(100, round(mean))))`)

Preserved guards: temp and humidity percentages are computed only when **both** bounds are non-None **and** readings exist;
soil is computed whenever readings exist.

### 5.5 `analyze_historical_trends` (logic/trends.py:11-69, CC 29)
- `_pooled_readings(db, sensors, hours=48)`
- `_halves(readings) -> tuple[list, list]` (sorts by timestamp, split at `len // 2`)
- `_moisture_trend(first, second) -> tuple[float | None, str | None]` (`is not None` filter)
- `_temperature_trend(first, second) -> str | None` (**truthiness filter `if r.temperature` kept**: bug B-18, pinned)
- `_cadence_flags(db, cluster_id) -> tuple[bool, bool]` (only `action == "start" and duration_minutes`; `<1/day and <2 min` → low, `>3/day` → high)

Call order: sensors → readings → irrigator → events.

### 5.6 `detect_stress_conditions` (logic/stress.py:11-66, CC 27)
One pure function per indicator, each returning `str | None`: `_water_warning`, `_low_env_humidity`, `_low_light` (still
calls `effective_light_threshold(min_lux)` **before** testing `min_lux > 0`, as today), `_water_stress` (keys on
**average** soil, unlike the engine's minimum; pinned), `_heat_stress`, `_over_watering`. The orchestrator re-queries
plants and care exactly as today. Passing the engine's `plant_care` in instead would change the DB query sequence, so it is
listed as a non-goal.

### 5.7 `ForecastService.predict_next_irrigation` (services/forecast.py:35-146, CC 25)
- `_forecast_for_sensor(sensor, plant_map, learner) -> _SensorForecast | None`: `learner.get_plant_profile` runs **only** when current moisture exists; drainage ≥ 0 → fallback.
- `_no_data_response(cluster_id) -> ForecastResponse`
- `_driver(forecasts) -> _SensorForecast`: stable `sorted(...)[0]` kept, which preserves the tie order.
- `_confidence(profiled: int) -> float` (3+ → 0.7, 1+ → 0.4, else 0.2)
- `_rain_outlook(cluster) -> tuple[bool, str | None, float | None]`: outdoor and a client → `get_forecast(hours=6)`.

`now = int(time.time())` is still taken before the sensor loop.

### 5.8 `create_app` (app.py:123-254, 132 lines) — order is the contract
```python
_API_ROUTERS: tuple[APIRouter, ...] = (clusters.router, plants.router, irrigators.router, sensors.router, configs.router,
    operations.router, scheduler.router, charts.router, alerts.router, activity.router, decisions.router, forecast.router,
    preferences.router, vacation.router, search.router, bulk.router, insights.router, health.router, quality.router,
    efficacy.router, windows.router)   # == OpenAPI path order == MCP tool order (golden routes.json)
```
| Step | Signature | Content (verbatim order) |
|---|---|---|
| `create_app` (frozen) | `(settings=None, engine=None) -> FastAPI` | `Settings()` / engine / `init_db`, then the steps below. |
| `_make_lifespan` | `(settings) -> Callable` | Start scheduler + `rearm_leak_checks()` / stop. Keep the name `rearm_leak_checks` imported in `app` (G10 lists it in `greenhouse_server.app`). |
| `_new_fastapi` | `(settings) -> FastAPI` | Title, description, version `"1.0.0"`, `openapi_tags` (14, same order), `generate_unique_id_function`. |
| `_init_state` | `(app, settings, engine) -> str` | settings → session_factory → `_init_tuya` → tz → `set_display_timezone` → weather → ntfy → plant_db; returns `tz_name`. |
| `_init_background` | `(app, settings, tz_name) -> None` | `init_scheduler` → `init_health_monitor` → `_restore_persisted_scheduler_pause`. |
| (inline) | | `bootstrap_admin(engine, settings)`, still **after** background init and **before** routers. |
| `_include_api` | `(app) -> None` | `auth_routes.router` unprotected, then `_API_ROUTERS` with `Depends(require_user)`, then `well_known.router`. |
| `_mount_web` | `(app) -> None` | `/static`, `web_router`, `register_web_exception_handlers`. |
| `_mount_mcp` | `(app) -> None` | `FastApiMCP(...)` (description verbatim), `mount_http()`, `app.state.mcp`. |

All new names are `_`-prefixed, so `dir(greenhouse_server.app)` is unchanged. `require_mcp_token` and its `!=` comparison (B-12) are untouched.

### 5.9 Repository copy-paste helpers (`repository/_base.py`)
| Helper | Signature | Applied to (byte-identical semantics only) | Not applied to (and why) |
|---|---|---|---|
| `_delete` | `(self, model: type[Base], row_id: int) -> bool` | `delete_vacation_window`, `delete_irrigation_window`, `delete_cluster`, `delete_sensor`, `delete_irrigator` | `delete_plant` (closes assignments first) |
| `_patch` | `(self, row, fields: Mapping[str, object], *, json_fields: tuple[str, ...] = ()) -> None`, None-first: `if value is None: continue; if key in json_fields and isinstance(value, dict): setattr(json.dumps) elif hasattr(row, key): setattr` | `update_vacation_window`, `update_irrigation_window` (None-first); `update_irrigator` (`json_fields=("config",)`); `update_sensor` **after** its `plant_id` pop (routing via `reassign_sensor_to_plant` stays in the method) | `update_cluster`, `update_plant`, `update_preferences` evaluate `hasattr` **before** the `None` test. On a lazy relationship key that triggers a load (and autoflush). Fold them only in a separate commit whose message lists the call-site keys (all columns). Otherwise keep a second helper. `set_irrigation_config` / `update_global_irrigation_config` deliberately allow `None` → never folded. |
| `_paginate` | `(self, stmt, model, *, after_id, limit) -> list` | `list_all_sensors`, `list_all_irrigators`, `list_all_plants` (caller applies its own filters first, so the WHERE order and SQL text stay identical) | — |

### 5.10 Scheduler job bodies (scheduler.py:292-409) — functions stay in `scheduler.py`
`tests/golden/contracts/scheduler_jobs.json` pins `func_ref = "greenhouse_server.scheduler:_sync_job"` (and the other 4),
so the names and the module are fixed.
```python
@contextmanager
def _job_session(failure_message: str) -> Iterator[IrrigationRepository]:
    session = _app.state.session_factory()      # reads the module global at call time (no early binding)
    try:
        yield IrrigationRepository(session)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception(failure_message)       # exact texts: "Sync job failed", "Plant health snapshot job failed",
    finally:                                    # "Check job failed", "Anomaly scan job failed", "Device health monitor job failed"
        session.close()
```
- Applied to the 5 jobs. Guards stay **before** the `with`: the `cloud is None` debug return in `_sync_job`, and the
  `_app is None` / `monitor is None` returns in `_health_monitor_job`. The inconsistent `_app is None` guard is **not**
  normalised, because adding guards changes the failure mode.
- `_check_job` becomes `_build_irrigation_service(repo) -> IrrigationService` plus a `with` body.
- Not applied to `init_health_monitor` (nested try that assigns the monitor even on failure) or to the irrigation
  lifecycle jobs (different commit/rollback semantics: `_run` commits only on interrupt, `_run_leak_check` returns early
  without commit, `rearm_leak_checks` never commits).
- The redundant inner import (321) is removed.
- `"check_all"` literals become `CHECK_ALL_JOB_ID`, with the definition moved above `_TZ_BOUND_CRON_JOBS`.

### 5.11 CLI client return types (client.py, 80 methods; mypy default: 83 `return-value` errors)
- `JSONObject = dict[str, Any]`, `JSONList = list[JSONObject]`. `_request(...) -> Any` (body unchanged, including the CSV
  branch returning `{"csv": text}` and the ConnectError-only mapping, B-17).
- Each method is annotated with what the endpoint returns (`-> JSONObject` / `-> JSONList`). There are no runtime
  `isinstance` checks, so behavior is unchanged.
- `_drop_none(mapping) -> JSONObject` replaces the 9 `{k: v for … if v is not None}` copies. It is **not** used in
  `commands/irrigators.py` add (truthy) vs update (`is not None`), which differ on purpose.
- `resolve_server_url(ctx) -> str` (`ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", "http://localhost:8000")`) is
  used by `_helpers.get_client`, `commands/auth.py:26` and `commands/tui.py:35`. `--help` strings are untouched.
- Rejected: per-endpoint TypedDicts in the CLI. They would duplicate OpenAPI schemas across a package boundary the CLI
  must not cross.

### 5.12 `ClusterScreen` → see §8.

---

## 6. API/web duplication: where each shared rule lives, and what must NOT be merged

| Rule (00-smells §A10) | Shared home (slice service layer) | API glue keeps | Web glue keeps | Drift that must NOT be merged |
|---|---|---|---|---|
| Weekday bits/labels/format (×3: `web/routes/clusters.py:92-100`, `configs.py:13-22`, `windows.py:20,76`) | `services/windows.py`: `WEEKDAY_BITS`, `WEEKDAY_LABELS` (same tuples), `FULL_WEEK_MASK = 127`, `format_weekday_mask(mask) -> str` | — | context keys `weekday_bits` / `weekday_labels` get the same tuples | The TUI copies (`tui/formatting.py:131`, `widgets.Heatmap.DAYS`): the package boundary forbids sharing. |
| Window validation (`routes/windows.py:23-33` vs `web/routes/windows.py:36-42`) | `services/windows.py`: `class WindowViolation(StrEnum): HOURS_RANGE, HOURS_EQUAL, MASK_RANGE`; `window_violation(start, end, mask) -> WindowViolation \| None` (checks in today's order) | message map: `"start_hour and end_hour must be 0..23"` / `"…must differ"` / `"weekday_mask must be 1..127 (Mon=1, Sun=64)"`; partial-update **effective** values computed in the route | message map with trailing periods / `"Select at least one weekday."`; `_parse_weekday_mask` still runs **before** validation (its own 400s first) | Do not unify messages (asserted, `[HTML]`/`[MCP]`). |
| In-cluster lookups / 404s (plants, sensors, cluster irrigator, irrigator, windows) | Query: repository slice (`get_plant_in_cluster`, `get_sensor_in_cluster`, `get_window_in_cluster`). HTTP translation: `deps.py` next to `require_cluster`: `require_found(obj, detail) -> T` plus `require_plant_in_cluster(repo, cid, pid)`, `require_sensor_in_cluster`, `require_cluster_irrigator`, `require_irrigator` (default details = today's identical strings) | detail strings unchanged | `web/routes/windows.py` passes `detail="Window not found in cluster."` (period) | API vs web window punctuation; `"Cluster not found"` in `deps` stays. |
| Plant-DB sync (`routes/plants.py:219-248` ≡ `web/routes/operations.py:111-141`) | `ClusterService.sync_plants(*, plant_id: int \| None, cluster_id: int \| None) -> tuple[int, list[str]]`, raises `PlantNotFoundError(plant_id)`. Plant lookup = `get_plant` **and** `get_cluster(plant.cluster_id) is not None`, which is exactly equivalent to the old two-level scan. | `if request.plant_id` truthiness (0 → all), commit, `SyncPlantsResponse` | `int(x) if x.strip()` parsing, commit, `partials/_sync_result.html` with `kind="plants"` | Unknown `cluster_id` → `synced=0`, no 404 (B-16, pinned). Per-plant exceptions caught only in the bulk branch. |
| Irrigator create + capacity (`routes/irrigators.py:83-105` vs `web/routes/irrigators.py:83-115`) | `services/irrigators.py`: `create_irrigator_with_capacity(repo, *, cluster_id, tuya_device_id, name, irrigator_type, config, reservoir_l, flow_rate_l_per_min) -> int` (add + optional `update_irrigator`, **no commit, no exception mapping**) | `try` around call + commit: `IrrigatorExistsError` → 409 "Cluster already has an irrigator", `IntegrityError` → 409 "Device ID already exists" | catches **only** `IrrigatorExistsError` → re-render 409; commit outside the `try` | Web keeps the 500 on a duplicate Tuya id (B-7). `update_irrigator` cannot raise `IrrigatorExistsError`, so widening the web `try` to cover it is a no-op. |
| Sensor create (`routes/sensors.py:66-85` vs `web/routes/sensors.py:59-70`) | Only the plant-membership query (`get_plant_in_cluster`) is shared | plant-in-cluster check → 404 `f"Plant {id} not found in cluster"`; `IntegrityError` → 409 | unchanged: no check, no `IntegrityError` handling | **Do not give web the plant check** (B-7 pinned). |
| Vacation range (`routes/vacation.py:87-90` PUT; `web/routes/vacation.py:76-77,115-116`) | `services/vacation.py`: `vacation_range_is_valid(starts_at: int, ends_at: int) -> bool` (`starts < ends`) | PUT: 400 `"starts_at must be < ends_at"` on the effective values | 400 `"ends_at must be after starts_at."` | **API POST stays unvalidated** (B-8). |
| Quiet hours "active now" (`engine.py:268-290` vs `web/routes/clusters.py:165-179`) | `logic/timing.py`: `active_quiet_window(effective: Mapping[str, Mapping[str, object]], *, now_unix: int, tz_name: str \| None) -> tuple[int, int] \| None` (int conversion and None handling identical) | engine `_resolve_quiet_window` = `active_quiet_window(self.db.get_effective_config(cid), now_unix=…, tz_name=self._tz_name())` | `quiet_active_now = active_quiet_window(effective_config, now_unix=int(time.time()), tz_name=…) is not None`, reusing the already-fetched `effective_config` (no extra query) | — (an engine-touching step, so it is scheduled late). |
| `check_all` `has_alerts` (`routes/operations.py:200` vs `web/routes/operations.py:86`) | **none** | alerts ∨ maintenance ∨ needs_water | alerts only | **Drifted; merging changes behavior** (B-13). |
| Relative time (`web/filters.py:28-40` vs `web/routes/plant_dashboard.py:115-124`) | **none** | — | both stay | Different outputs; do not merge. |
| Moisture target parsing (7 sites) | `parse_moisture_target` is already the shared home | — | — | Only proven-equivalent sites may be folded. `monitor_cluster` (3-part → fallback) and `issues.py` (narrow `except`) differ → keep and pin. |
| CSV export (`routes/operations.py:355-389` ≈ `web/routes/analytics.py:86-116`) | Tempting home: `services/cluster.py: stats_csv_rows(...)` | — | — | Fold only if the golden CSV bytes of both endpoints are identical. Neither uses the dead `stats.export_csv` (different time format), which must stay untouched (frozen import path). |
| Web plant create/update form mapping (jscpd clone `web/routes/plants.py:46-58 ≡ 86-98`) | web-private `_plant_fields(*, category, water_needs, …) -> dict` in `web/routes/plants.py` (web-only knowledge, so it does not go to a service) | — | `Form(...)` params stay in the signatures | Rejected: a `Depends()` dataclass form, which risks the 422 `loc` shape. |

Transactions stay in routes (68 commits). A unit-of-work refactor is behavioral (00-smells D) and out of scope.

---

## 7. Server slice moves (with re-export shims)

### 7.1 `services/irrigation.py` → pipeline only; lifecycles move to their slices
| Name | New home | Shim in `services/irrigation.py` | Why the shim is needed |
|---|---|---|---|
| `schedule_pump_watcher`, `handle_watcher_interrupted`, `WATCHER_SHUTDOWN_ACTIVITY_CODE` | `services/pump_watcher.py` | re-export | `manual_control.py:20`, `test_scheduler_jobs.py:12`, `test_scheduler_shutdown.py:26,43`, `test_contract_scheduler.py:129,529-538` |
| `rearm_leak_checks`, `LEAK_CHECK_ACTIVITY_CODE`, `_leak_check_done`, `_run_leak_check`, `_add_leak_check_job`, `_schedule_leak_check` → public `schedule_leak_check` | `services/leak.py` | re-export, **including the private names** `_run_leak_check`, `_add_leak_check_job` (and `_schedule_leak_check` as an alias) | `app.py` imports `rearm_leak_checks`; `test_leak_rearm.py` (`irrigation_mod.rearm_leak_checks` ×10); `test_contract_scheduler.py:126,277,292,478-501` calls the two private names by path |

Concrete costs removed: the 760-line module stops mixing three slices. `IrrigationService` no longer touches scheduler
internals, because it calls `schedule_leak_check()` / `schedule_pump_watcher()`. The leak re-arm logic (B-23) lives next
to `LeakDetectionService`.

Preserved:
- The lazy `from greenhouse_server.scheduler import _app, scheduler, …` stays **inside the functions** (the late-binding
  `_app` trap, §10.3).
- `irrigation.py` imports `leak` and `pump_watcher` eagerly. That is safe because neither imports `irrigation`. The lazy
  cycle becomes `scheduler ⇢ irrigation → leak ⇢ scheduler`: still lazy-only, and the import-time graph stays a DAG.
- The moved code keeps logging under the logger name `greenhouse_server.services.irrigation` (§10.1).
- `handle_watcher_interrupted` switches from `_time.time()` to `time.time()`. No test patches it (`irrigation_mod._time`
  is patched only around the pipeline).

### 7.2 New slice service modules (no moves): `services/windows.py`, `services/irrigators.py`
They are new homes for knowledge that today lives in route handlers (§6). Neither module has a logger, and they introduce
no public name on a frozen path.

### 7.3 `web/routes/analytics.py`: optional, gated
It mixes four slices: cluster history/stats/learn, the scheduler page plus pause/resume/delete, and bulk stop-all. The
slice-consistent target is `web/routes/scheduler.py` + `web/routes/bulk.py`, included at the **same slot** in
`web/router.py`, right after `analytics.router`. The handlers are already in that order inside the module, so the route
order is unchanged. **It is blocked** by `tests/golden/web/routes.json` (pins `endpoint.__module__`) and by
`test_contract_scheduler.py:216` (patches `web_analytics.set_check_all_paused`). Do it only if the orchestrator classifies
`endpoint.__module__` as non-contract and regenerates that golden in a dedicated, reviewed commit. Otherwise skip it. It is
the only slice move in this proposal that a golden pins.

---

## 8. TUI: `ClusterScreen` → per-tab controllers + pure rows

### 8.1 Constraints discovered (all pinned by `tests/cli/test_contract_tui.py` → `tests/golden/tui/surface.json`)
- `_tui_classes()` walks **every module** under `greenhouse_cli.tui` and records every `DOMNode` subclass. A new
  `TabPane`/`Widget` subclass anywhere adds a golden entry. So **tab components must be plain objects, not widgets.**
- Per class it records `bases`, own `BINDINGS`, own UPPERCASE attributes, and handler names matching
  `^(action_|on_|_on_|watch_)`. `ClusterScreen` must keep exactly its 15 `action_*` methods plus `on_mount`,
  `on_data_table_row_highlighted` and `on_tabbed_content_tab_activated`. Rules:
  - add no method whose name starts with `action_`, `on_`, `_on_` or `watch_`;
  - add no UPPERCASE class attribute (so no `TABS = {...}`).
- Module constants: only UPPERCASE names *assigned* in a module are recorded. `cluster.py` keeps `METRIC_ORDER`, `RANGES`
  and `STATS_DAYS`. The new modules (`rows.py`, `cluster_tabs.py`) must assign **no** public UPPERCASE names, which keeps
  their entries empty and therefore absent.
- `test_tui.py` reads `screen.summary`, `.metric`, `.hours`, `.plant_chart`, `.cluster_id` and queries by id
  (`#plants-table`, `#decision-panel`, `#config-panel`, …), sets `TabbedContent.active = "tab-…"`, and presses keys.
  State stays on the screen and ids stay unchanged.

### 8.2 Design
- **`compose()` stays in `ClusterScreen`, unchanged.** It is the one readable picture of the DOM that `app.tcss` styles
  (`#garden`, `#overview-panels`, `#metric-chart`, `#plants-table`, `#plant-chart`, `#insights-row`, `#insights-panel`).
  Splitting it across nine classes would hurt reading and risk widget order, which drives focus order and the screen-text
  golden.
- `tui/screens/cluster_tabs.py`:
  ```python
  class ClusterTab:                       # plain object; screen-owned workers, screen-owned state
      pane_id: str = ""
      def __init__(self, screen: ClusterScreen) -> None: self.screen = screen
      def setup_columns(self) -> None: ...          # DataTable headers (byte-identical lists)
      def add(self) -> bool: return False           # True = handled (incl. "already has an irrigator" warning)
      def edit(self) -> bool: return False
      def delete(self) -> bool: return False
  ```
  | Controller (pane id) | Owns (moved from ClusterScreen) |
  |---|---|
  | `OverviewTab` (`tab-overview`) | `show(status)`: garden mount, can sprite, `#irrigator-info`, `#decision-panel`; `load_forecast()`; add = attach irrigator; edit = irrigator; delete = detach |
  | `ChartsTab` (`tab-charts`) | `load_chart(payload)`, `load_heatmap()` |
  | `PlantsTab` (`tab-plants`) | columns; `show(status)`, which starts the plant-health worker on `self.screen` with group `"plant-health"`; `load_health(plant_id)`; add/edit/delete (help text `"Blank care fields are filled from the plant DB…"` kept verbatim, B-19) |
  | `SensorsTab` (`tab-sensors`) | columns; `show(status)`; add/edit/delete |
  | `InsightsTab` (`tab-insights`) | efficacy columns; `load()` (the 5-call gather) |
  | `DecisionsTab` (`tab-decisions`) | columns; `load()` (`limit=100`) |
  | `HistoryTab` (`tab-history`) | columns; `load()` (`hours=24*30, limit=200`) |
  | `WindowsTab` (`tab-windows`) | columns; `show(detail)`; add/edit/delete (`v["weekday_mask"] or 127`) |
  | `ConfigTab` (`tab-config`) | `load() -> dict`: effective + detail, fills `#config-panel`, returns `detail` for `WindowsTab.show`; edit = config |
- `ClusterScreen` keeps state (`status`, `detail`, `summary`, `metric`, `hours`, `plant_chart`, `_plant_id`,
  `_plants_loaded`) and `self._cluster_tabs: dict[str, ClusterTab]`. The attribute is named to avoid Textual internals
  and does not match the handler regex. `load()` keeps today's sequence: `gather(status, soil)` → overview (awaited) →
  sensors → plants → `gather(forecast, chart, heatmap, decisions, history, config [+ insights if active])`.
- CRUD dispatch, three methods that replace the three `if/elif` chains:
  ```python
  def action_new(self) -> None:
      tab = self._cluster_tabs.get(self.active_tab)
      if tab is None or not tab.add():
          self.notify("Nothing to add here — try the Plants, Sensors, Windows or Overview tab.")
  ```
  `action_edit` / `action_delete` follow the same shape with their exact fallback strings.
- Workers: controllers call `self.screen.run_worker(..., group=...)`, so ownership, `exclusive=True` and the group names
  (`"load"`, `"act"`, `"plant-health"`, `"insights"`) stay screen-scoped. Message handlers stay on the screen: the
  `on_data_table_row_highlighted` body delegates to `PlantsTab`.
- `DataScreen.selected_row(table_id, rows) -> dict | None` absorbs `ClusterScreen._selected` (cluster.py:722). `AlertsScreen._selected` (returns an id) and
  `SettingsScreen._selected_vacation` adopt it only if the outputs are identical. `SearchScreen._selected` is an `@on`
  handler and keeps its name.
- `tui/rows.py` (pure, unit-testable, with `fmt` called for clocks): `plant_rows`, `sensor_rows`, `decision_rows`,
  `history_rows` (sorted newest first), `window_rows`, `efficacy_rows`, `config_rows`, `forecast_rows`, `next_water`
  (moved from cluster.py:34), `irrigator_info(summary) -> Text`, `decision_text(decision) -> Text`,
  `insights_text(insights, monitor) -> Text`, `stats_rows(stats)`, `learn_text(learn) -> Text`,
  `stats_export_filename(name, cluster_id) -> str`. `SystemScreen.load` (CC 24) and `SettingsScreen.load` (CC 19) get the
  same treatment: row builders go to `rows.py` and the screens keep the I/O.
- **Sprites: no data/rendering split.** `sprites.py` is cohesive. Its 14 UPPERCASE constants are pinned under the module
  name `greenhouse_cli.tui.sprites` (module_constants golden), so moving pixel data would change the golden. `mood_for`
  stays (the display-only 40/70 band is documented).

---

## 9. Ordered migration (small green commits; lowest risk first; engine and devices last)

Each step is **one transformation per commit**: moves, extractions and renames go in separate commits.
- Test subset = the `module-tests-map-direct.json` entry for each touched module, ∪ the named Phase-1 contract tests.
- For any server change also run `tests/server/test_mcp.py` + `test_contract_openapi.py` + `test_contract_mcp.py`.
- For CLI/TUI changes also run `tests/cli/test_completeness.py` + `test_contract_help.py` + `test_contract_tui.py`.
- Use the conservative map for high-risk modules.

| # | Commit(s) | Targeted tests |
|---|---|---|
| 0 | Prerequisites (tests only): (a) G10 `test_contract_imports.py` (imports + loggers, lower bound) and G11 `test_contract_constants.py` (superset; enums strict) are green; (b) new `tests/test_contract_schema_classes.py` (§3.3); (c) new repository signature/disjointness guard (§4.2); (d) pins for the drift cases: `monitor_cluster` 3-part target, `detect_conflicts` early return skipping light/humidity, B-7/B-8/B-13/B-16 | own files twice + `-p xdist -n 2` + `TZ=America/New_York` |
| 1 | CLI: `resolve_server_url`; then `_drop_none`; then client typing (`_request -> Any`, return annotations) | `cli/test_cli.py`, `test_completeness.py`, `test_contract_help.py`, `test_contract_json_output.py`, `test_tui.py` |
| 2 | `services/windows.py` weekday vocabulary; switch `web/routes/{clusters,configs,windows}.py` (one commit per module) | `test_web_windows.py`, `test_web_config_pages.py`, `test_web_cluster_pages.py`, `test_web_redirects.py`, `test_contract_web_html.py` |
| 3 | `window_violation` + per-layer message maps (API commit, web commit) | `test_irrigation_windows.py`, `test_clusters.py`, `test_web_windows.py`, `test_contract_openapi.py`, `test_contract_web_html.py`, `cli/test_tui.py` |
| 4 | `vacation_range_is_valid` (PUT + web create/edit) | `test_vacation.py`, `test_web_vacation.py`, `test_web_vacation_edit.py` |
| 5 | Additive repo lookups (`get_plant_in_cluster`, `get_sensor_in_cluster`, `get_window_in_cluster`, `get_vacation_window`), still in `repository.py`; then `deps.require_found` + slice translators; then route adoption, **one slice per commit** | `test_plants.py`, `test_sensors.py`, `test_irrigators.py`, `test_vacation.py`, `test_web_crud_actions.py`, `test_web_sensor_pages.py`, `test_web_plant_pages.py`, `test_web_irrigators.py`, `test_contract_web_html.py` |
| 6 | `ClusterService.sync_plants` + `PlantNotFoundError` (API, then web) | `test_plants.py`, `test_web_operations.py`, `test_mcp.py`, `cli/test_tui.py` |
| 7 | `services/irrigators.create_irrigator_with_capacity` (API, then web) | `test_irrigators.py`, `test_web_irrigators.py`, `test_web_irrigator_capacity.py`, `test_web_irrigator_pages.py` |
| 8 | Web plants form mapper | `test_web_plant_pages.py`, `test_web_crud_actions.py`, `test_contract_web_html.py` |
| 9 | TUI: `rows.py` builders (one tab per commit) → `DataScreen.selected_row` → `cluster_tabs.py` controllers (overview, plants, sensors, windows, config, then the read-only tabs) → CRUD dispatch | `cli/test_tui.py`, `cli/test_contract_tui.py` (surface + screens) |
| 10 | Schemas: (a) `git mv schemas.py schemas/__init__.py` (pure rename); (b) one slice module per commit, leaves first (`_common`, `_plants`, `_sensors`, `_irrigators`, `_configs`, `_windows`, …, `_clusters`, `_operations`); (c) drop `model_rebuild()`; (d) `_parse_json_config` | schema-class guard, `test_contract_openapi.py`, `test_contract_mcp.py`, G10, plus the 63-file schemas map (≈ the server suite) |
| 11 | Repository: (a) `git mv` to `repository/__init__.py`; (b) `_base.py` helpers applied group by group (`_delete`; `_paginate`; `_patch` None-first group; `json_fields` group; the hasattr-first group separately with evidence); (c) one mixin per commit | repository guard, G10, `tests/test_db.py`, `test_scheduler.py` (time patch), plus the 72-file repository map (≈ full) |
| 12 | `services/health.compute_score`, `services/forecast` decomposition (one function per commit) | `test_plants_health.py`, `test_web_plant_hero.py`, `test_forecast.py`, `test_web_insights.py`, `cli/test_tui.py` |
| 13 | Core logic: `trends.py`, `stress.py`, `issues.py` (`detect_conflicts`, then `detect_issues`) | `test_logic.py`, `test_learning.py`, `test_contract_decision_grid.py`, conservative map for `logic/*` |
| 14 | `create_app` steps; then scheduler `_job_session` + `CHECK_ALL_JOB_ID` (**two reviewers**) | `test_contract_openapi.py` (`test_route_table_matches_golden`), `test_mcp.py`, `test_scheduler*.py`, `test_contract_scheduler.py`, `test_auth.py` |
| 15 | `services/irrigation.py`: pipeline decomposition + TypedDicts; then the lifecycle moves to `leak.py` / `pump_watcher.py` with shims | `test_contract_pipeline.py`, `test_operations.py`, `test_leak_rearm.py`, `test_leak_detection.py`, `test_scheduler_shutdown.py`, `test_scheduler_jobs.py`, `test_contract_scheduler.py`, `test_migrations.py`, `test_notify.py`, `test_health_monitor.py` |
| 16 | **Engine (last, two reviewers)**: `_tz_name` → `_decision_with_reason` trim → `_emit` → `_pre_gate` → `_gather_inputs`/`_EngineInputs` → `_evaluate` → `_finalize` method → soil-rule helpers → `active_quiet_window` in `timing.py` + engine adoption → web `cluster_detail` adoption | `test_contract_decision_grid.py` (+ `tests/golden/engine/*`), `test_logic.py`, `test_engine_timing.py`, `test_leak_hold.py`, `test_vacation_rationing.py`, `test_timing.py`, conservative map for `logic.engine` (41 files) |
| 17 | Devices: **no structural change** from this lens. A `_parse_dps` helper, if anyone wants it, belongs to the devices proposal. | `devices/*`, `test_cloud.py` |
| opt | §7.3 analytics split, only after the orchestrator rules on the golden | `test_web_analytics.py`, `test_web_scheduler_emergency.py`, `test_scheduler_pause.py`, `test_contract_web_html.py` |

---

## 10. Risks and how each is neutralised

1. **Logger names = module names.**
   - All loggers are `getLogger(__name__)`. Moving a function silently renames its log records (`%(name)s`), which is
     observable by operators.
   - Rule: moved code keeps its original logger name explicitly. In `leak.py` / `pump_watcher.py` that is
     `_irrigation_logger = logging.getLogger("greenhouse_server.services.irrigation")  # kept across the move`.
     Record it in REFACTOR_NOTES.
   - `tests/golden/contracts/loggers.json` (lower bound) requires every existing module to keep a logger with its
     golden name. `services/irrigation.py` keeps `logger`. The extra `_irrigation_logger` attribute in `leak.py` /
     `pump_watcher.py` is an allowed addition.
   - `tests/test_migrations.py:149` checks that this logger stays enabled after `init_db`. The pipeline, which is what that
     test protects, stays in `services/irrigation.py`.
   - `tests/server/test_scheduler.py:88,104` patch `sched_mod.logger.warning`. `_resolve_check_cron_hours` and every
     scheduler job stay in `scheduler.py`.
   - The `caplog` assertions ("unprotected", "device unreachable") are logger-name agnostic.
2. **Import-time side effects and registration order.**
   - `_API_ROUTERS` holds the same router objects in the same order, and `include_router` order equals OpenAPI path order,
     which equals MCP tool order (`tests/golden/contracts/routes.json`, `test_route_table_matches_golden`).
   - Web routes: no handler changes module or position (`tests/golden/web/routes.json` pins module.qualname and order).
   - The schema and repository facades import all slice modules at import time, before anything else can import
     `greenhouse_core.schemas`. The class-creation order inside the package changes, but nothing observes it (Pydantic
     keeps no global registry that feeds OpenAPI; FastAPI reads the classes per route).
   - `greenhouse_core/__init__.py` imports `repository`. The mixin modules import only `models`, `constants`, `sqlalchemy`
     and the package's two error classes, so no new eager cycle appears. The eager graph must stay a DAG; re-run the
     import-graph script after steps 10, 11 and 15.
3. **The lazy `scheduler` ↔ `services.irrigation` cycle and the `scheduler._app` trap.**
   - Never hoist `from greenhouse_server.scheduler import _app/scheduler/…` to module level, in either the old or the new
     home. `init_scheduler` rebinds `_app` per `create_app`, and the tests do `monkeypatch.setattr(sched, "_app", None)`
     (`test_contract_scheduler.py:405,456,496`).
   - `_job_session` reads `_app` inside the function.
   - After step 15 the cycle is `scheduler ⇢ irrigation → leak/pump_watcher ⇢ scheduler`: still lazy-only. This proposal
     does **not** claim to remove it.
4. **Textual name coupling** (§8.1): no new DOMNode classes, handler-pattern names, UPPERCASE class attributes or public
   UPPERCASE module constants in `greenhouse_cli.tui`. Ids, classes and compose order are unchanged, workers stay
   screen-owned, and `on_*` handlers stay on the screen.
5. **Facade drift**: G10 enforces the old `dir()` as a lower bound. Private slice submodules keep the public surface
   identical. There is no `__all__` on `schemas`/`repository`, and the verbatim import blocks keep stray names (`json`,
   `func`, `BaseModel`, `DEFAULT_*`, `time`). The exceptions stay defined in `repository/__init__.py`. G10 records names
   and `__all__`, not `__module__`, so re-exported moved objects pass. The `__module__` of a moved object is pinned only
   where a golden says so explicitly: web endpoints (`web/routes.json`), TUI classes (`tui/surface.json`) and scheduler
   core jobs (`scheduler_jobs.json`). This proposal moves none of them.
6. **Invariant 5 and the constants golden.** `test_contract_constants.py` is a *superset* pin (new names allowed; every
   golden name keeps an equal `repr`, so the value **and** the type stay the same). Moving literals into `constants.py`
   is therefore allowed:
   - engine: `0.5`, `hours=24`, `2.0` mm / 6 h, `86400`, `"45-65"`;
   - scheduler: `minutes=15`, `hour=0, minute=30`, `hours=6`;
   - monitor: `-15` / `+10`.

   Do it as separate commits right before the decomposition of the owning function. Keep `int` vs `float` exactly (e.g.
   `0.5` stays a float, `24` an int), and never reuse an existing constant whose value merely coincides (e.g. the
   `"45-65"` default vs `DEFAULT_SOIL_MOISTURE_MIN/MAX`).
7. **Repository mixin MRO** (§4.2 guard): with disjoint method sets the MRO is unobservable. The three self-typed
   cross-slice calls keep mypy honest.
8. **Behavior-adjacent temptations, explicitly refused**:
   - learner runs 3× per check;
   - `detect_stress_conditions` re-querying plants;
   - `_leak_check_done` 500-row horizon (B-23);
   - `has_alerts` drift (B-13);
   - POST vacation validation (B-8);
   - web irrigator/sensor `IntegrityError` and plant-check gaps (B-7);
   - `WWW-Authenticate` dropped by the JSON error handler;
   - `!=` MCP token compare (B-12);
   - the stale TUI help text (B-19), which is rendered text.

   All of these stay as they are and are pinned.

---

## 11. Pattern choices (each tied to its smell) and rejected alternatives

| Pattern | Smell it removes |
|---|---|
| Facade package + private slice modules (`schemas/`, `repository/`) | 999/1309-line breadth-god modules; forward-ref hack; navigation (route slice ↔ DTO slice ↔ data slice) — without touching 40+29 consumers or the public surface |
| Mixins on a `_RepositoryCore` base with self-typed cross-slice calls | Breadth of `IrrigationRepository` while keeping one class for consumers. Composition plus delegation would need 80 one-line forwarders. |
| `_patch` / `_delete` / `_paginate` private helpers | 7 + 6 + 3 copy-pasted bodies (applied only where the semantics are byte-identical) |
| Slice service functions (`services/windows.py`, `services/irrigators.py`, `ClusterService.sync_plants`, `vacation_range_is_valid`) | API/web knowledge duplication (verbatim and drifted copies) |
| Error-code enum (`WindowViolation`) + per-layer message maps | One rule with two message vocabularies. A single message set would change asserted strings. |
| `deps.require_found` + slice 404 translators | About 30 inline lookup/404 blocks across both layers |
| `TypedDict` results (`PipelineResult`, `CheckResult`, `MonitorResult`, `PlantHealthScore`) | Untyped dict contracts; mypy `return-value` noise. They are dicts at runtime, so nothing ripples into templates or `Response(**r)`. |
| Parameter objects (`_EngineInputs`, `_Actuation`) | 161/187-line orchestrators, 7-parameter helpers |
| `_job_session` context manager | 5× session scaffolding in scheduler jobs |
| Plain tab controllers + polymorphic `add/edit/delete` | 3× `if/elif` on tab ids, nesting depth 5, an 800-line god screen |
| Pure row builders (`tui/rows.py`) | Payload → row logic trapped in widget methods (untestable without Textual) |
| Ordered `_API_ROUTERS` tuple | 21 include lines whose order is an implicit contract |

**Rejected**

| Alternative | Why |
|---|---|
| Cross-layer feature packages (`features/<slice>/{api,web,service}.py`) | Breaks the `web/routes.json` golden (module pinned) and renames loggers. The workspace already splits layers by distribution (owner decision). Huge diff, no smell removed. |
| Public slice submodules (`schemas/plants.py`) | G10 would allow them (lower bound), but consumers would start importing them and freeze the slicing as a second public path. |
| Moving or splitting API route modules (`routes/operations.py` by sub-feature) | No smell. The module matches its OpenAPI tag, and router-order bookkeeping adds risk. |
| Renaming `services/cluster.py` → `clusters.py` | Rename only; no cost removed. |
| Splitting `client.py` into per-slice mixins | 80 thin methods. Typing plus `_drop_none` remove the real costs. |
| `TabPane` subclasses per tab | New DOMNode classes enter the TUI surface golden. Worker/message ownership moves from screen to widget. |
| Moving `compose()` into the tabs | Scatters the DOM picture that `app.tcss` depends on; widget order risk. |
| Sprite data vs rendering split | Cohesive module; the module_constants golden pins its 14 constants by module. |
| Rule registry / strategy for the engine | YAGNI. The explicit sequence is the readable spec of the short-circuit semantics. |
| Dataclass service results | Ripple into templates and `**result` call sites. |
| Config-schema mixins | They reorder OpenAPI properties. |
| Merging drifted API/web copies (§6 last column) | Behavioral change. |
| A `Depends()` form dataclass for web plant forms | Risks the 422 error `loc` shape for little gain. |
| `_errors.py` for repository exceptions | Changes the exceptions' `__module__` / repr. |
| Moving the 5 scheduler jobs into their slices | `scheduler_jobs.json` pins `func_ref = greenhouse_server.scheduler:<name>`. |

---

## 12. Deliberately NOT done
- No `src/` layout and no change to package boundaries (owner decision).
- No move of web or API route modules (only the gated optional §7.3).
- No `models.py` split (healthy, DDL frozen).
- No devices restructuring.
- No reuse of coincidentally-equal constants (new names only, §10.6).
- No route docstring, Pydantic class docstring, field order, route function name, CLI help or TUI binding edits.
- No bug fixes (they are pinned and recorded).
- No transaction or unit-of-work changes.
- No removal of "dead" public names (`stats.export_csv`, `print_stats_report`, `set_plant_database`,
  `CreateSchedulerJobRequest`, `TriggerCode.DAILY_CAP_HIT`).

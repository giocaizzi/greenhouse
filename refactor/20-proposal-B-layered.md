# 20 — Proposal B: Layered / Ports-and-Adapters

Author: Architect B (Phase 2). Read-only on code. Baseline `a1b2622`. Inputs: `BRIEF.md` (owner decisions binding:
keep `libs/` uv workspace, no src layout; method-level cleanup is first-class), `CLAUDE.md`, `00-map.md`, `00-smells.md`,
`00-contracts.md`, `00-baseline.md`, `00-tests.md`, the Phase-1 contract tests now in the tree
(`tests/test_contract_decision_grid.py` + `tests/engine_grid.py`, `tests/server/test_contract_pipeline.py`,
`test_contract_openapi.py`, `test_contract_mcp.py`, `test_contract_settings.py`, `test_contract_web_html.py`,
`tests/cli/test_contract_{help,json_output,tui}.py`, `tests/devices/test_contract_adapters.py`), and the code itself.

## 0. Stance in one paragraph

The dependency rule is the organising idea: **domain (`logic/`, `learning/`) ← application (`services/`) ← adapters
(`routes/`, `web/`, scheduler jobs, repository, device gateway, weather/ntfy clients) ← composition root (`create_app`)**,
with dependencies pointing inward only and enforced by import-linter. But the strongest *honest* version of this lens in
Python is not "an interface for everything": it is **structural `typing.Protocol` ports, defined next to the consumer,
that the existing concrete classes already satisfy** — no adapter wrapper classes, no DI container, no runtime cost. A
port earns its place only if it removes a present, evidenced problem (an un-enforceable layering edge, an untyped
cross-package parameter, a lazy-import cycle, a service locator) *and* either has ≥2 real implementations or makes a
contract mechanically checkable. Six candidate ports fail that bar and are rejected below (§2.6) — including the
`Clock` port the brief suggested, because the evidence says it would make things worse.

---

## 1. Target tree (only what changes)

Legend: **N** new · **M** modified in place · **S** keeps a re-export shim · ✱ module whose `dir()` / attribute paths
are pinned by tests (see §7.1).

### 1.1 `libs/greenhouse-core/greenhouse_core/`

```
greenhouse_core/
├── _light.py            N  seasonal light math (moved from utils): _SEASONAL_LIGHT_FACTOR, NIGHT_LUX_THRESHOLD,
│                           seasonal_light_factor, daytime_lux_readings, effective_light_threshold. stdlib only.
├── utils.py             M S ✱ display-time only (set/get_display_timezone, format_timestamp) + re-export of the 5
│                           light names from _light (same names, same objects; keeps `os`, `datetime`, `UTC`, `ZoneInfo`).
├── repository.py        M S ✱ `class IrrigationRepository(<14 private mixins>)` + __init__(session); re-exports
│                           SameClusterMoveError, IrrigatorExistsError; keeps `import time, json` and every
│                           current top-level import (repo_mod.time is patched; dir() is golden-pinned).
├── _repo/               N  provider-side split of the repository by aggregate (see §4)
│   ├── __init__.py         (empty)
│   ├── _base.py            _RepoBase(session holder) + _patch_skip_none_first / _patch_hasattr_first /
│   │                       _patch_with_json_config / _delete_by_id / _list_by_id helpers
│   ├── errors.py           SameClusterMoveError, IrrigatorExistsError (__module__ pinned to "greenhouse_core.repository")
│   ├── clusters.py  plants.py  irrigators.py  sensors.py (+assignments)  readings.py  events.py
│   ├── configs.py (+_GLOBAL_CONFIG_DEFAULTS, _CONFIG_PATCHABLE_FIELDS)  decisions.py  activity.py  alerts.py
│   └── plant_health.py  vacation.py  windows.py  preferences.py
├── logic/
│   ├── _ports.py        N  consumer-side Protocols for the domain (typing only, §2.1–2.2)
│   ├── _rules.py        N  pure decision rules moved out of engine.py (water-warning, critical-stress, soil,
│   │                       temperature, humidity, light, water-needs, trend) + one_reason_decision + set_dosage
│   ├── engine.py        M ✱ IrrigationLogic only: orchestration, gates that read the repo, seasonal (season_for is
│   │                       patched HERE), vacation, persistence. Keeps `import time`, `season_for` binding,
│   │                       `_apply_window_rule`, `_apply_seasonal_multiplier`, `_enforce_leak_hold`, `_enforce_cooldown`
│   │                       (names cited by CLAUDE.md invariants 9 and 11).
│   ├── sensors.py stress.py trends.py fallback.py timing.py   M  annotations → ports; decomposed (§3)
│   └── __init__.py      unchanged (__all__ frozen)
└── learning/
    ├── issues.py        M  decomposed; imports seasonal_light_factor/effective_light_threshold BY NAME from _light
    │                       (patch target `greenhouse_core.learning.issues.seasonal_light_factor` keeps working)
    ├── profiling.py learner.py report.py   M  annotations → ports
    └── __init__.py      unchanged
```

Single responsibility per new/changed module:

| Module | One responsibility |
|---|---|
| `_light.py` | Pure seasonal-light arithmetic (month → factor, lux filtering). No env, no globals. |
| `utils.py` | Display-time formatting and the process-wide display-timezone setting. |
| `logic/_ports.py` | Declare what the domain needs from the outside world (typing only, zero runtime behaviour). |
| `logic/_rules.py` | Pure `IrrigationDecision` mutators/terminal checks — no repo, no clock, no plant DB. |
| `logic/engine.py` | Orchestrate one evaluation: read inputs through the port, run gates and rules in the frozen order, persist. |
| `_repo/<aggregate>.py` | All persistence queries for one aggregate. |
| `repository.py` | The stable public facade name (`IrrigationRepository`) composed from the aggregates. |

### 1.2 `libs/greenhouse-server/greenhouse_server/`

```
greenhouse_server/
├── runtime.py           N  process-wide runtime the services and scheduler share, leaf module:
│                           BackgroundScheduler singleton + _JOB_DEFAULTS, shutdown event, shutdown_requested,
│                           wait_for_shutdown, bind_app/current_app, job_session() context manager
├── scheduler.py         M S ✱ job registry, cron/tz rebuild, pause persistence, job bodies (logger unchanged —
│                           tests patch sched_mod.logger and caplog on "greenhouse_server.scheduler").
│                           Re-exports scheduler/shutdown_requested/wait_for_shutdown from runtime;
│                           `_app` served by module __getattr__ → runtime.current_app()
├── app.py               M ✱ create_app = composition root, split into private _wire_*/_include_*/_mount_* helpers
├── services/
│   ├── irrigation.py    M  run_irrigation_pipeline / check_cluster / monitor_cluster / job plumbing decomposed IN PLACE
│   │                       (keeps `import time as _time`, logger name, schedule_pump_watcher, rearm_leak_checks)
│   ├── weather.py       M  + `WeatherSource` Protocol (consumer = IrrigationService; see §2.3)
│   ├── health.py forecast.py maintenance.py system_health.py   M  decomposed; system_health imports runtime
│   └── (others)         later batches, same recipe
└── deps.py              unchanged (request-scoped composition is already explicit and fine)
```

| Module | One responsibility |
|---|---|
| `runtime.py` | Own the process-global mutable runtime (scheduler object, shutdown signal, the bound app) so services can reach it **without importing the scheduler module**; provide the background-job session scope. |
| `scheduler.py` | Define and (re)register the built-in jobs; pause/tz/cron policy. |
| `app.py` | Build the object graph once (composition root) and mount the interfaces in the frozen order. |

### 1.3 `libs/greenhouse-cli/greenhouse_cli/`

```
greenhouse_cli/
├── client.py            M  _API prefix, _drop_none, typed _object/_array wrappers over unchanged _request
├── commands/_helpers.py M  + resolve_server_url(obj) used by _helpers.get_client, commands/auth.py, commands/tui.py
└── tui/
    ├── rows.py          N  pure payload → rows/Text builders for ClusterScreen (no widgets, no textual import)
    ├── screens/base.py  M  DataScreen._selected(table_id, rows) hoisted (3 copies today)
    └── screens/cluster.py M per-tab CRUD handlers + dict dispatch; renders via rows.py
```

### 1.4 Re-export shims and patch-path tripwires (every one preserved)

| Path tests/consumers use | How it survives |
|---|---|
| `greenhouse_core.utils.{seasonal_light_factor, effective_light_threshold, daytime_lux_readings, NIGHT_LUX_THRESHOLD}` | `from greenhouse_core._light import …` in `utils.py` (same objects). |
| `greenhouse_core.learning.issues.seasonal_light_factor` / `.effective_light_threshold` (monkeypatched, `test_learning.py:446,470`) | `issues.py` binds both names at module level and every helper that uses them **stays in `issues.py`** and looks them up as module globals. |
| `greenhouse_core.logic.engine.season_for` (patched, `test_health_monitor.py:391`, `test_clusters.py:116`) | `_apply_seasonal_multiplier` stays in `engine.py` and calls the module-global `season_for`. |
| `engine_mod.time.time`, `repo_mod.time.time` (`test_scheduler.py:163-164`) | `engine.py` and `repository.py` keep `import time` (documented `# noqa: F401` in repository if no longer used there); all clock reads stay `time.time()` attribute lookups (also in `_repo/*`), so patching the global `time` module still reaches them. |
| `greenhouse_server.services.irrigation._time` (`test_leak_rearm.py:184`) | `run_irrigation_pipeline`'s actuation step stays in `services/irrigation.py` and reads `_time.time()`. |
| `greenhouse_server.scheduler.{scheduler, shutdown_requested, wait_for_shutdown, logger, _resolve_check_cron_hours, …}` | re-exported from `runtime` (same objects); `logger`, `_resolve_check_cron_hours` stay defined here. |
| `from greenhouse_server.scheduler import _app` (only `services/irrigation.py` today) | replaced by `runtime.current_app()`; for any stray reader, `scheduler.__getattr__("_app")` returns `runtime.current_app()` (PEP 562 works for from-imports). |
| `greenhouse_core.devices.tinytuya.Cloud`, `devices.gateway.tinytuya[.OutletDevice]` | devices untouched except one private helper (§6 step G1). |
| `greenhouse_cli.tui.run` | untouched. |
| `greenhouse_core.repository.{IrrigationRepository, SameClusterMoveError, IrrigatorExistsError}` | facade + re-export; method names/signatures/defaults unchanged. |

---

## 2. The ports

All ports live on the **consumer** side, are `typing.Protocol` (not `runtime_checkable`, never instantiated), are
imported only for annotations, and are satisfied **structurally** by today's classes — so there is no adapter wrapper
and no runtime behaviour to verify. Conformance is checked by mypy via a one-line assignment under `TYPE_CHECKING` in
the adapter module (dependency inversion: the adapter knows the port, the domain never knows the adapter):

```python
# repository.py
if TYPE_CHECKING:
    from greenhouse_core.logic._ports import EngineRepository
    def _conforms(r: "IrrigationRepository") -> "EngineRepository": return r   # mypy-checked, never executed
```

### 2.1 Domain read/write model — `logic/_ports.py` (owner: `logic/engine.py`, `logic/*`, `learning/*`)

```python
from __future__ import annotations
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol
if TYPE_CHECKING:
    from greenhouse_core.models import (Alert, Cluster, IrrigationConfig, IrrigationEvent, IrrigationWindow,
                                        Irrigator, Plant, Sensor, SensorReading, UserPreferences, VacationWindow)

class ReadingsSource(Protocol):                       # logic.sensors, learning.issues (+ trends via ClusterReadModel)
    def get_sensors_in_cluster(self, cluster_id: int) -> list[Sensor]: ...
    def get_recent_readings(self, sensor_id: int, hours: int = 24) -> list[SensorReading]: ...   # DESC, wall-clock cutoff

class EventsSource(Protocol):
    def get_irrigator_for_cluster(self, cluster_id: int) -> Irrigator | None: ...
    def get_recent_events(self, irrigator_id: int, hours: int = 24) -> list[IrrigationEvent]: ...  # DESC

class ClusterReadModel(ReadingsSource, EventsSource, Protocol):     # logic.trends, logic.stress, learning.report
    def get_plants_in_cluster(self, cluster_id: int) -> list[Plant]: ...

class LearningRepository(ClusterReadModel, Protocol):               # learning.profiling / issues / learner
    def get_irrigator(self, irrigator_id: int) -> Irrigator | None: ...
    def get_readings_around(self, sensor_id: int, timestamp: int, before_seconds: int = 1800,
                            after_seconds: int = 7200) -> tuple[list[SensorReading], list[SensorReading]]: ...

class EffectiveConfigSource(Protocol):                               # logic.fallback
    def get_effective_config(self, cluster_id: int) -> dict[str, dict[str, object]]: ...

class EngineRepository(LearningRepository, EffectiveConfigSource, Protocol):   # logic.engine (passes self.db to the learner)
    def get_cluster(self, cluster_id: int) -> Cluster | None: ...
    def get_irrigation_config(self, cluster_id: int) -> IrrigationConfig | None: ...
    def get_preferences(self) -> UserPreferences: ...      # NOTE: inserts the singleton row on first call (repository.py:1035-1043)
    def list_irrigation_windows(self, cluster_id: int) -> list[IrrigationWindow]: ...
    def get_active_alert(self, code: str, *, cluster_id: int | None = None, since: int | None = None) -> Alert | None: ...
    def get_active_vacation(self, at: int | None = None) -> VacationWindow | None: ...
    def irrigator_consumption_liters(self, irrigator_id: int, since: int, until: int) -> float: ...
    def add_decision_log(self, cluster_id: int, evaluated_at: int, action: str, duration_minutes: int,
                         interval_hours: int, confidence: float, primary_code: str | None, reason_text: str,
                         payload: dict[str, Any], triggered_by: str = "auto", actuated: bool = False) -> int: ...
```

**Present problem, with evidence.**
- 9 domain modules import the SQLAlchemy-backed repository module at runtime purely to annotate a parameter:
  `logic/{engine:92, fallback:25, sensors:7, stress:7, trends:8}`, `learning/{issues:17, learner:9,
  profiling:9, report}` (00-map §5 "logic/learning -> persistence"). That makes "the decision engine is pure" an
  un-enforceable claim — no import-linter contract can be written today without 9 ignores.
- The engine's real dependency is **16 of ~80** repository methods (grep over `logic/` + `learning/`:
  `get_recent_readings ×8, get_sensors_in_cluster ×7, get_plants_in_cluster ×4, get_irrigator_for_cluster ×4,
  get_preferences ×3, get_recent_events ×3, get_effective_config ×2, get_cluster, get_irrigation_config,
  get_irrigator, get_readings_around, get_active_alert, get_active_vacation, irrigator_consumption_liters,
  list_irrigation_windows, add_decision_log`). Nobody can see that today; the port is the executable documentation.
- A hidden write lives behind a "read": `get_preferences()` inserts the singleton row (`repository.py:1035-1043`) and the
  engine calls it 3× per evaluation (`engine.py:281, 306, 334`). The port docstring makes that visible to reviewers of
  every future engine change.
- `logic/timing.py:23` (`IrrigationWindow`) and `logic/fallback.py:24` (`IrrigationConfig`) import ORM models at runtime
  for annotations only → moved under `TYPE_CHECKING`, so the domain stops importing `sqlalchemy` transitively.

**Implementations.** One production adapter (`IrrigationRepository`, structural). Test seam benefit is modest (the
Phase-1 `engine_grid.run_case` uses a real in-memory repository and should keep doing so). **Kept anyway** because the
value is the *enforced* layering contract (§8) and mypy-checkable surface, at zero runtime cost and zero shim burden.
This is the one port I keep with a single implementation, and I say so explicitly.

### 2.2 `RainForecast` — `logic/_ports.py` (owner: `IrrigationLogic`, `ForecastService`)

```python
class RainForecast(Protocol):
    def get_forecast(self, hours: int = 6) -> Mapping[str, Any] | None: ...
```

- Present problem: `IrrigationLogic.__init__(…, *, weather_client=None)` (`engine.py:101`) and
  `ForecastService.__init__(…, weather_client=None)` (`forecast.py:179`) are **untyped** because core cannot name the
  server's `WeatherClient` (layering) — so today mypy sees `Any` on the only external I/O the engine performs.
- Implementations: **three** already exist — `greenhouse_server.services.weather.WeatherClient`,
  `tests/golden.py::OfflineWeather`, `tests/engine_grid.py::FakeForecastWeather`. Strong keep.
- Change: annotations only (`weather_client: RainForecast | None = None`); parameter names/defaults unchanged
  (`engine_grid.run_case` calls `engine_factory(repo, plant_db, weather_client=…)`).

### 2.3 `WeatherSource` — `services/weather.py` (owner: `IrrigationService`)

```python
class WeatherSource(Protocol):      # structural superset of RainForecast; no inheritance needed
    def get_current(self) -> Mapping[str, Any] | None: ...
    def get_forecast(self, hours: int = 6) -> Mapping[str, Any] | None: ...
```
Used to annotate `IrrigationService.__init__(…, weather_client: WeatherSource, …)` (`irrigation.py:380`). Same three
implementations (OfflineWeather is what `install_offline_weather(app)` injects into `app.state.weather_client`, which
`deps.get_weather_client` hands to the service). `deps.py` annotations stay concrete (`WeatherClientDep`), untouched.

### 2.4 The scheduler service-locator — solved by a leaf runtime module, **not** a Protocol

Problem (evidence): `services/irrigation.py` does `from greenhouse_server.scheduler import _app, scheduler,
shutdown_requested, wait_for_shutdown` inside 5 functions (`:151, :263, :299, :321, :345`) — a private-global service
locator, a hidden runtime cycle `scheduler ↔ services.irrigation` (00-map §5), and a rebinding trap (`init_scheduler`
rebinds `_app` per `create_app`; hoisting the import would freeze `None`). `services/system_health.py:7` imports the
scheduler module at module level just to read `scheduler.running`.

A `JobScheduler` Protocol would have exactly one implementation (APScheduler) and no test seam (tests inspect the real
`bg_scheduler.get_jobs()`, `test_leak_rearm.py:53`, `test_scheduler_jobs.py:141`). So: **no Protocol**. Instead move the
process-wide mutable runtime into a leaf module both sides import at module level:

```python
# greenhouse_server/runtime.py  (imports: threading, contextlib, logging, apscheduler, sqlalchemy.orm.Session, fastapi types)
_JOB_DEFAULTS = {"misfire_grace_time": None, "coalesce": True, "max_instances": 1}
scheduler = BackgroundScheduler(job_defaults=_JOB_DEFAULTS)
_shutdown_event = threading.Event()
_current_app: FastAPI | None = None
def shutdown_requested() -> bool: ...
def wait_for_shutdown(seconds: float) -> bool: ...
def bind_app(app: FastAPI) -> None: ...          # called by scheduler.init_scheduler
def current_app() -> FastAPI | None: ...         # read at CALL time — no stale binding possible
@contextmanager
def job_session(session_factory: Callable[[], Session], *, log: logging.Logger,
                failure: str, args: tuple[object, ...] = ()) -> Iterator[Session]:
    session = session_factory()        # outside try: a missing app/state still raises to APScheduler, as today
    try:
        yield session                  # caller commits explicitly (several callers commit only on some paths)
    except Exception:
        session.rollback()
        log.exception(failure, *args)  # caller's logger ⇒ log record names unchanged
    finally:
        session.close()
```
Effect: `services.irrigation → runtime` and `scheduler → runtime` are plain module-level edges; the cycle disappears; the
private-name import disappears; the `_app` trap disappears because readers call `current_app()`.

### 2.5 Already-existing ports (kept as is)

`devices.irrigators.base.AbstractIrrigatorAdapter` (`start/stop/status/read_health`) and the sensor base are the device
ports; `DeviceRegistry` is their factory; `FakeDeviceWiring` is the second implementation. They are fine. **No new device
ports.**

### 2.6 Ports considered and REJECTED (single implementation, no seam benefit — or worse)

| Candidate | Why rejected (evidence) |
|---|---|
| **`Clock` for the engine** (`engine.py:129` `int(time.time())`) | The engine is not the only clock reader in a decision: `get_recent_readings` / `get_recent_events` compute their cutoff from `time.time()` inside the repository (`repository.py:407, 474`), `get_plant_profile` too (`profiling.py:103`), and `seasonal_light_factor()` reads `datetime.now` (`utils.py:40`). Injecting a fake clock into the engine alone would evaluate `evaluated_at` at the fake instant against data windows cut at the real instant — a silently inconsistent decision, i.e. a *worse* seam. Making it coherent means threading a clock into `IrrigationRepository(session)` (≈50 construction sites) for no production benefit. Meanwhile `time-machine` (Phase-1 kit; `engine_grid.run_case` uses `time_machine.travel(case.at, tick=False)`) already freezes **all** clocks coherently. Instead: push clock reads to the edge of the orchestrator (one line in `decide_for_cluster`; the light factor computed once and passed into the pure rule — §3.2). |
| `Clock` for the repository (24 `int(time.time())` defaults) | Same argument; `repo_mod.time` patch + time-machine cover it. |
| `PlantCareSource` (`get_care_data`) | One implementation, tests use the real `PlantDatabase`. Cheaper to allow `greenhouse_core.plant_db` in the domain contract (it is curated domain data) and note its import-time env read (`plant_db.py:9-13`) in REFACTOR_NOTES. |
| `IssueDetector` (inject the learner into `IrrigationLogic`) | Would remove the lazy import at `engine.py:585` only if every caller injected it; `ClusterService` (`cluster.py:74`) and tests construct `IrrigationLogic(repo, plant_db)` without it, so the default must still lazily import → the cycle dodge stays either way. De-duplicating the 3× learner run per check (00-smells A6) is a perf change, out of scope. |
| `JobScheduler` / `PostStartHooks` for `_schedule_leak_check` + `schedule_pump_watcher` | One implementation; `test_contract_pipeline.py::test_successful_auto_start_schedules_leak_check_and_watcher` asserts against the real scheduler. Solved structurally by `runtime.py` (§2.4). |
| `ActuationGate` (health monitor), `Notifier` (ntfy) | One implementation each; tests drive the real `DeviceHealthMonitor` / patch `urllib`. |
| Repository Protocols for **services** | Services use dozens of methods each and routes own the unit of work (68 `session.commit()` calls). A per-service port is pure ceremony; services are the application layer and may depend on the concrete persistence facade. |
| `GreenhouseApi` Protocol for the TUI's `client_factory` | One implementation (`IrrigationClient`); TUI tests drive the real server through httpx. |
| Credentials port for `DeviceGateway` (`os.environ` at `gateway.py:116-118`) | devices are high-risk/low-payoff; record only. |

---

## 3. Method-by-method decomposition

Conventions for every item: new helpers are private (`_name`), keyword-only where call sites would be ambiguous,
precise return types, Command–Query Separation (helpers either compute a value or perform effects, the orchestrator
sequences them). **Ordering rule:** every repository call, `add_reason`, assignment to a `validate_assignment=True`
`IrrigationDecision` field (`decision.py:76`), dict-key insertion and log call happens in the same order as today; the
"why" for each preserved ordering is noted.

### 3.1 `IrrigationService.run_irrigation_pipeline` (`services/irrigation.py:422-608`, 187 lines, CC 21)

Stays in `services/irrigation.py` (because of `_time` patch and logger name). Result keeps being a plain `dict`
(consumed by `IrrigateResponse(**result)` in `routes/operations.py:149-151` and by the template
`partials/_decision_panel.html` in `web/routes/operations.py:42-52`); it gains a documentation-only type:

```python
class PipelineResult(TypedDict, total=False):   # runtime is still a dict; mypy checks key names
    action: str; reason: str; confidence: float; duration_minutes: int; interval_hours: int
    stress_indicators: dict[str, Any]; reasons: list[dict[str, Any]]; temperature: float
    temperature_source: str; blocking_alarms: list[str]
```

| New function | Signature | Responsibility |
|---|---|---|
| `run_irrigation_pipeline` | unchanged signature, `-> PipelineResult` | Sequence: lookup → temperature → decide → skip/dry-run → target → health gate → actuate. ≤ 25 lines. |
| `_error_result` | `(reason: str) -> PipelineResult` | `{"action": "error", "reason": reason, "confidence": 0}` (exact key order). |
| `_decision_result` | `(decision: IrrigationDecision, temp: float, source: str) -> PipelineResult` | The 9-key dict of `:455-465`, same key order. |
| `IrrigationService._decide` | `(self, cluster_id: int, temp: float, *, force: bool) -> IrrigationDecision \| None` | Construct `IrrigationLogic(self._repo, self._plant_db, weather_client=self._weather)` and call `decide_for_cluster(…, persist=True, triggered_by="manual" if force else "auto", bypass_quiet_hours=force)`. |
| `IrrigationService._log_decision_skip` | `(self, cluster_id: int, decision: IrrigationDecision) -> None` | The `decision_skip` activity row of `:468-476`. |
| `IrrigationService._actuation_target` | `(self, cluster_id: int) -> tuple[Irrigator, AbstractIrrigatorAdapter] \| str` | Query: irrigator → registry → adapter, returning the **error reason string** on the first failing guard (`"no irrigators found"`, `"no device registry"`, `f"no adapter for irrigator: {exc}"`) — same guard order as `:480-495`. |
| `IrrigationService._blocking_alarms` | `(self, irrigator: Irrigator) -> list[HealthAlarm]` | Query: `[]` if no monitor or not blocked, else the alarms (`:502-504`). |
| `IrrigationService._apply_health_block` | `(self, cluster_id, irrigator, decision, alarms, result) -> None` | Command: add CRITICAL reason → `decision.action = SKIP` → activity row → update `result` (`action`, `reason`, `reasons` overwrite existing keys, `blocking_alarms` appended last) — the exact sequence of `:505-528`. Keeps bug B-6 (no re-persist) and its misleading comment is rewritten to say so (comment only). |
| `IrrigationService._actuate` | `(self, cluster_id, irrigator, adapter, decision, result, *, temp: float, source: str, sensor_data: Mapping[str, Any] \| None) -> PipelineResult` | `adapter.start` → `soil_note` → **`started_at = int(_time.time())` after `start`** (as today, `:543`) → `add_irrigation_event(action="start" if success else "attempted", …)` → branch. |
| `_soil_note` | `(sensor_data: Mapping[str, Any] \| None) -> str` | The `", soil=…% (driest)"` suffix. |
| `IrrigationService._on_start_succeeded` | `(self, cluster_id, irrigator, decision, duration: int, started_at: int, result) -> None` | activity `irrigated` → `set_decision_actuated` → `_schedule_leak_check` → `schedule_pump_watcher` → `result["action"]="irrigated"` → `maybe_notify(self._notifier, self._repo.get_preferences(), "auto", …)`. `get_preferences()` stays evaluated **before** `maybe_notify` checks for a notifier (it can insert the prefs row). |
| `IrrigationService._on_start_failed` | `(self, cluster_id, irrigator, output: str, result) -> None` | activity `actuation_failed` → `raise_alert(...)` → result `action`/`reason`. |

Short-circuit semantics preserved: `if dry_run or decision.action.value == "skip"` stays verbatim (dry-run returns the
engine's action, e.g. `"irrigate"`, without activity); error results returned at the same points; `UnknownDeviceModel`
still wins over a health block (target resolved before the gate).

Sibling items in the same module:
- `check_cluster` (`:673-716`): `_check_result(cluster_id: int, cluster_name: str, action: str, *, alerts: list[dict], maintenance: list[dict], notes: str | None = None, needs_water: list[str] | None = None) -> dict` inserting keys in the original order (`cluster_id, cluster_name, action, notes|needs_water, alerts, maintenance`); `sync_cluster_alerts` still called after the pipeline/monitor and before the dict is built.
- `monitor_cluster` (`:610-671`, CC 19): `_latest_soil(readings) -> float | None`, `_monitor_target_band(care) -> tuple[float, float]` (the strict two-part parse with `except Exception` → `45.0, 65.0`; deliberately **not** merged with `parse_moisture_target` or the lenient `issues` parse — they differ on 3-part strings, 00-smells D#5), `_soil_status(latest, t_min, t_max) -> str`.
- `handle_watcher_interrupted` (`:31-119`, 89 lines): `_stop_auto_cycle(repo, registry, irrigator) -> tuple[bool, str]` (returns `stop_ok, message`, logs exactly the same two messages), `_left_running_message(irrigator, triggered_by) -> str` (logs the warning), `_record_watcher_shutdown(repo, irrigator, *, triggered_by, started_at, stop_ok, message) -> None`.
- `schedule_pump_watcher` (`:122-223`, 102 lines): `_watcher_tuning(settings: Settings | None) -> tuple[float, float, int]` (the `2.0, 5.0, 5` fallback), `_pump_watcher_job(app, registry, *, irrigator_id, duration_seconds, started_at, triggered_by) -> Callable[[], None]` (job body using `runtime.job_session(..., log=logger, failure="Pump watcher job failed for irrigator %d", args=(irrigator_id,))`; commits **only** on the `interrupted` outcome, as today). **Subtlety preserved:** today the closure captures the `_app` value *at schedule time*; the new code does `app = runtime.current_app()` once in `schedule_pump_watcher` and closes over `app`. Same for `registry`.
- `_run_leak_check` → `with runtime.job_session(app.state.session_factory, log=logger, failure="Leak check job failed for cluster %d", args=(cluster_id,))`; the "already done" early return still happens without commit. `rearm_leak_checks` keeps its explicit try/finally (it has no rollback; byte-exactness beats uniformity).

### 3.2 `IrrigationLogic.decide_for_cluster` (`engine.py:106-266`, 161 lines, CC 18) — last, two reviewers

```python
class _Gate(NamedTuple):
    terminal: IrrigationDecision | None
    quiet_window: tuple[int, int] | None

@dataclass(frozen=True, slots=True)
class _EngineInputs:
    snapshot: SensorSnapshot; trends: Trends; stress: StressIndicators
    plant_care: list[dict[str, Any]]; temp_range: tuple[float, float] | None
    humidity_range: tuple[float, float] | None; water_needs: str; sensors: list[Sensor]
```

| New method | Signature | Responsibility / order guarantee |
|---|---|---|
| `decide_for_cluster` | unchanged (frozen; `engine_grid` calls it) | `get_cluster` → `None`; `evaluated_at = int(time.time())` (the **single** clock read, stays here); `get_plants_in_cluster`; `gate = self._pre_evaluation_gates(...)`; terminal → `self._record(gate.terminal, ...)`; else `self._finish(self._evaluate(...), quiet_override=..., ...)`. ~20 lines. |
| `_pre_evaluation_gates` | `(self, cluster_id, evaluated_at, *, has_plants: bool, bypass_quiet_hours: bool) -> _Gate` | Query, strictly in order: no-plants → `_enforce_leak_hold` → `_enforce_cooldown` → `_resolve_quiet_window` → quiet SKIP if not bypass. Later gates are **not evaluated** once one fires (preserves which repo calls happen — notably `get_preferences` inside quiet-window resolution, which may insert a row). |
| `_no_plants_decision`, `_quiet_hours_decision` | `(cluster_id, evaluated_at[, window]) -> IrrigationDecision` | The literal decisions of `:132-141` and `:168-178`. |
| `_record` | `(self, decision, *, persist: bool, triggered_by: str) -> IrrigationDecision` | Replaces the four `if persist: self._persist(...); return x` blocks. No override reason (terminal gates never got one). |
| `_finish` | `(self, decision, *, quiet_override: tuple[int, int] \| None, persist, triggered_by) -> IrrigationDecision` | Former `_finalize` closure: add `MANUAL_OVERRIDE_QUIET_HOURS` iff `quiet_override` (computed once as `gate.quiet_window if bypass_quiet_hours else None`), then `_record`. |
| `_evaluate` | `(self, cluster, cluster_id, evaluated_at, plants, current_temp) -> IrrigationDecision` | weather skip → `_gather_inputs` → fallback (no sensors / no data) → base decision → water-warning → critical-stress → window → adjustments. `if apply_water_warning_rule(d) or apply_critical_stress_rule(d): return d` is equivalent to the two sequential `if`s (short-circuit `or`). |
| `_gather_inputs` | `(self, cluster_id, plants) -> _EngineInputs` | Exactly the call order of `:200-210`: snapshot(hours=24) → trends → stress → `_attach_learning_alerts` → plant care → temp range → humidity range → water needs → `get_sensors_in_cluster` (last, as today). |
| `_fallback_decision` | `(self, cluster_id, evaluated_at, current_temp, inputs) -> IrrigationDecision` | `temperature_based_decision(...)`; `get_irrigation_config` still evaluated as the argument, i.e. only on this path. |
| `_base_decision` | `(cluster_id, evaluated_at, inputs) -> IrrigationDecision` | SKIP / defaults / confidence `0.5` (named `_BASE_CONFIDENCE` locally — see §7.6 on constants). |
| `_apply_adjustments` | `(self, cluster, decision, inputs, evaluated_at) -> None` | soil → temperature → humidity → light(`light_factor=seasonal_light_factor()`) → water-needs → trend → `_apply_seasonal_multiplier` → `_apply_vacation_budget`. |
| `_tz_name` | `(self) -> str \| None` | `prefs = self.db.get_preferences(); return prefs.timezone if prefs else None`; called at the same three points as today (call count unchanged). |
| `_apply_window_rule` | `(self, cluster_id: int, evaluated_at: int) -> IrrigationDecision \| None` | Drop the two unused params (`cluster`, `decision`; `engine.py:292`); name kept (CLAUDE.md inv. 9). |
| `_apply_seasonal_multiplier` | unchanged name, in `engine.py`; body split with pure `_seasonal_overrides(plant_care, season_key) -> tuple[dict \| None, dict \| None]` and `_scaled_interval(interval, multiplier) -> int` | `season_for` remains a module-global lookup (patch target). The dead `isinstance(care, dict)` guard is kept (zero risk, noted). |
| `_apply_vacation_budget` (77 lines) | name kept; pure `_vacation_days_left(vac, now) -> int`, `_binding_max_minutes(*, reservoir_l, flow_rate_l_per_min, starts_at, ends_at, now, spent_l) -> int` | Order kept: `get_active_vacation` → VACATION_ACTIVE reason → `get_irrigator_for_cluster` → capacity guard → IRRIGATE guard → `irrigator_consumption_liters` (only after the guards, as today) → trim / exhaust. |
| `_decision_with_reason` → `_rules.one_reason_decision` | drop the three never-passed params (`sensor_snapshot`, `stress_indicators`, `trends`) | behaviour identical (`None`/fresh defaults always). |

`_apply_soil_moisture_rule` (`engine.py:678-752`, 75 lines, CC 21) → `_rules.apply_soil_moisture_rule(decision, plant_care) -> None`:

| Helper (in `_rules.py`) | Signature | Responsibility |
|---|---|---|
| `moisture_band` | `(plant_care: Sequence[Mapping[str, Any]]) -> tuple[float, float]` | min of mins / max of maxes over `parse_moisture_target(d.get("soil_moisture_target", "45-65"))`. |
| `soil_extremes` | `(snapshot: SensorSnapshot) -> tuple[float, float]` | `(min_soil, max_soil)` with the avg fallback. |
| `is_soil_conflict` | `(min_soil, max_soil, target_min, target_max) -> bool` | `min < tmin and max > tmax - CONFLICT_WET_MARGIN`. |
| `sensor_names` | `(snapshot, predicate: Callable[[float], bool]) -> list[str]` | dry / wet sensor names (per-sensor `avg_soil_moisture is not None`). |
| `set_dosage` | `(decision, action: Action, *, duration: int \| None = None, interval: int \| None = None, confidence: float) -> None` | Assigns in the fixed order action → duration → interval → confidence, skipping `None` — matches every branch today (over-watering/adequate/wet skip duration or interval). |
| `apply_conflict` / `classify_soil` | `(decision, …) -> None` | conflict reason (message format byte-identical, incl. `'?'` fallback); the 4-way `if/elif` chain VERY_DRY → DRY → ADEQUATE (`avg <= target_max`) → WET. |

Light rule becomes pure: `apply_light_adjustment(decision, *, light_factor: float)`. The orchestrator calls
`seasonal_light_factor()` unconditionally (today it is called only when `avg_light is not None`); the call is a
side-effect-free clock read, and time-machine freezes it identically — noted for the reviewers.

### 3.3 `detect_conflicts` (`learning/issues.py:154-304`, 151 lines, CC 36) and `detect_issues` (`:21-151`, CC 24)

All helpers stay in `issues.py` (patch targets).

| New function | Signature | Responsibility |
|---|---|---|
| `detect_conflicts` | unchanged | `sensors` → `_latest_moisture_by_sensor` → `if len(...) < 2: return []` (**early return skips the low-light and humidity sections — preserved**) → dry/wet partition → over-water alerts → plants map → `_low_light_alerts` → `_low_humidity_alerts`. |
| `_latest_moisture_by_sensor` | `(db: ReadingsSource, sensors) -> dict[int, float]` | mean of the newest 3 cleaned (DESC) values per sensor, 6h. |
| `_issues_target_band` | `(care: Mapping[str, Any] \| None) -> tuple[float, float]` | lenient `split("-")[0..1]` parse, `45.0, 65.0` fallback on `ValueError/IndexError` (distinct from engine/monitor parse — pinned). |
| `_partition_dry_wet` | `(sensors, moisture, plant_care) -> tuple[list[_Reading], list[_Reading]]` | `moisture < tmin - 5` → dry; `elif > tmax` → wet. `_Reading = tuple[Sensor, float, float]`. |
| `_overwater_conflict_alerts` | `(dry, wet, profiles) -> list[Alert]` | nested loop, same `continue` guards, same message/data dict. |
| `_low_light_alerts` | `(db, plant_db, sensors, plants_by_id) -> list[Alert]` | 168h cleaned readings, `daytime_lux_readings`, `effective_light_threshold(min_lux)` (module-global). |
| `_low_humidity_alerts` | `(db, plant_db, sensors, plants_by_id) -> list[Alert]` | 48h, `< ideal - 15`. Separate loop kept (DB call order: all 168h reads, then all 48h reads). |
| `_blocked_drip_alert`, `_drainage_alert`, `_chronic_underwatering_alert` | `(…, sensor, profile[, care]) -> Alert \| None` | the three per-sensor checks of `detect_issues`, appended in the same order; `seasonal_light_factor()` still looked up in the module namespace. |

### 3.4 `PlantHealthService.compute_score` (`services/health.py:20-119`, CC 29)

| New function | Signature | Responsibility |
|---|---|---|
| `compute_score` | unchanged | plant → `_empty_score()`; care bands; pooled cleaned readings; in-band %; efficiency; `_score_payload`. |
| `_empty_score` | `() -> dict[str, float \| int \| None]` | fresh dict each call (callers may mutate). |
| `_HealthBands` | frozen dataclass `soil: tuple[float, float]; temp: tuple[float \| None, float \| None]; humidity: tuple[...]` + `_HealthBands.from_care(care)` | the 5 care lookups of `:52-56`. |
| `_in_band_pct` | `(values: Sequence[float], lo: float, hi: float) -> float \| None` | `None` on empty; `in_band / len * 100`. |
| `_band_percentages` | `(readings, bands) -> tuple[float \| None, float \| None, float \| None]` | temp/humidity computed only when both bounds are not `None` (as today); empty readings → all `None`. |
| `_first_profile_efficiency` | `(self, sensors, days) -> float \| None` | first non-None `get_plant_profile(...).efficiency_score` (break semantics). |
| `_composite_score` | `(components: Sequence[float]) -> float \| None` | `float(max(0.0, min(100.0, round(mean))))`. |
| `_score_payload` | `(...) -> dict` | key order `score, soil_in_band_pct, temp_in_band_pct, humidity_in_band_pct, efficiency, sample_count`. |

### 3.5 `analyze_historical_trends` (`logic/trends.py:11-69`, CC 29) and `detect_stress_conditions` (`logic/stress.py:11-66`, CC 27)

Both get a **functional core + thin I/O shell**; the public signatures stay.

Trends:
- `analyze_historical_trends(db: ClusterReadModel, cluster_id) -> Trends`: shell — sensors → `_pooled_clean_readings(db, sensors, hours=48)` → `_apply_reading_trends(trends, readings)`; irrigator → `get_recent_events(hours=7*24)` → `_apply_cadence_flags(trends, events)`.
- Pure: `_apply_reading_trends(trends, readings) -> None` (returns early under `TREND_MIN_READINGS`, sorts by timestamp, splits at `mid`), `_moisture_trend(first, second) -> tuple[float, str] | None` (uses `is not None`), `_temperature_trend(first, second) -> str | None` (**keeps the truthiness filter `if r.temperature` — bug B-18, pinned**, written as its own comprehension so nobody "unifies" it by accident), `_cadence_flags(events) -> tuple[bool, bool]` (`start` events with truthy duration; `<1/day and <2 min` → low; `elif >3/day` → high).
- A generic `_classify_delta` is **not** introduced: moisture tests the negative side first, temperature the positive side first; equivalent only while thresholds are ≥ 0 — not worth the reasoning burden.

Stress:
- `detect_stress_conditions(db, plant_db, cluster_id, snapshot, trends) -> StressIndicators`: shell — plants → care → `_stress_from(snapshot, trends, plant_care)`.
- Pure detectors, each `-> str | None`: `_water_warning(snapshot)`, `_low_env_humidity(snapshot, care)`, `_low_light(snapshot, care)` (calls `effective_light_threshold(min_lux)` from `_light`, wall-clock month, as today), `_water_stress(snapshot, trends)` (builds the `" + declining"` suffix directly instead of `+=`), `_heat_stress(snapshot, trends, care)`, `_over_watering(snapshot, trends)`.
- `_stress_from` assigns a field **only when the detector returns non-None** (never assigns an explicit `None`, so `model_fields_set` is unchanged).
- Same recipe for `logic/sensors.get_recent_sensor_data` (CC 19): `_sensor_series(readings) -> _Series`, `_per_sensor_snapshot(sensor, series) -> PerSensorSnapshot`, `_aggregate(series_list, warnings) -> SensorSnapshot`; the literal `> 15` becomes `> NIGHT_LUX_THRESHOLD` (same value, same operator). And `logic/fallback.temperature_based_decision`: `_config_fallback(db, cluster_id, base) -> IrrigationDecision`, `_temperature_interval(temp, water_needs) -> int`.

### 3.6 `ForecastService.predict_next_irrigation` (`services/forecast.py:35-146`, CC 25)

| New function | Signature | Responsibility |
|---|---|---|
| `predict_next_irrigation` | unchanged | lookups → learner → `now = int(time.time())` (**same position: before the sensor loop**) → per-sensor forecasts → no-data return (**before** any weather call) → stable sort → driver → confidence → rain outlook → response. |
| `_forecast_sensor` | `(self, sensor, plant_map, learner) -> _SensorForecast \| None` | newest cleaned moisture, care target, profile drainage. |
| `_effective_drainage` | `(profile: PlantProfile \| None) -> float` | fallback `-2.0` when no profile or `>= 0`. |
| `_hours_until_threshold` | `(current: float, target_min: float, drainage: float) -> float` | `0.0` at/below target. |
| `_confidence_for` | `(profiled_count: int) -> float` | `0.7 / 0.4 / 0.2`. |
| `_no_data_forecast` | `(cluster_id: int) -> ForecastResponse` | the literal fallback response. |
| `_RainOutlook` + `_rain_outlook` | `(self, cluster) -> _RainOutlook(skip: bool, reason: str \| None, precip_mm: float \| None)` | outdoor and client present → `get_forecast(hours=6)`. |

The engine's weather rule (`engine.py:549-580`) and this method share the predicate "precip > 2.0 mm in 6 h" → one pure
helper `rain_precip_mm(forecast: Mapping[str, Any] | None) -> float | None` in `logic/_rules.py`
(`forecast.get("precipitation_mm", 0.0) or 0.0`), used by both. The `2.0`/`6` literals stay literal until the constants
question in §7.6 is settled.

### 3.7 `create_app` (`app.py:123-254`, 132 lines) — the composition root

```python
def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    if settings is None:            # `is None`, not `or`: keep exact semantics
        settings = Settings()
    if engine is None:
        engine = create_db_engine(settings.db_url)
    init_db(engine)
    app = _new_fastapi(_build_lifespan(settings))
    tz_name = _wire_state(app, settings, engine)     # settings, session_factory, tuya, tz+display, weather, ntfy, plant_db
    _wire_background(app, settings, tz_name)         # init_scheduler, init_health_monitor, restore pause
    bootstrap_admin(engine, settings)
    _include_api_routers(app)                        # auth unprotected, then _PROTECTED_API_ROUTERS in frozen order
    _mount_web(app)                                  # well_known, /static, web_router, exception handlers
    app.state.mcp = _mount_mcp(app)                  # MUST stay last: FastApiMCP snapshots routes at construction
    return app
```
`_PROTECTED_API_ROUTERS: tuple[APIRouter, ...]` lists the 21 routers in today's include order (route order = OpenAPI
order; pinned by `tests/golden/contracts/openapi.json` and `routes.json`). All new names are `_`-private and every
current top-level import of `app.py` stays (its `dir()` is golden-pinned; `rearm_leak_checks`, route modules, etc.).
`require_mcp_token` is untouched, including the non-constant-time `!=` (bug B-12, record only).

### 3.8 Scheduler job bodies (`scheduler.py:292-409`)

Each job becomes guard lines + `with runtime.job_session(app.state.session_factory, log=logger, failure="<same text>") as session:`
body + explicit `session.commit()`. Preserved per job:
- `_sync_job`: the `cloud is None` debug-log early return happens **before** the session is opened.
- `_health_snapshot_job`: still no `_app is None` guard (an unset app raises out of the job, as today; the factory call is outside the CM's `try`). The redundant inner `IrrigationRepository` import (`:321`) is removed.
- `_check_job`: `cloud`, `registry` read before the session; `_app.state.weather_client` read inside the block (AttributeError still logged as "Check job failed").
- `_health_monitor_job`: both guards before the session.
- `init_health_monitor`: keeps its explicit nested try (it assigns `app.state.health_monitor` even when the hooks fail); only `_run_startup_hooks(monitor, session) -> None` is extracted.
- Jobs read `runtime.current_app()` instead of the module global; `logger` stays `greenhouse_server.scheduler` (caplog in `test_contract_settings.py:279`, patch in `test_scheduler.py:88`).
- `"check_all"` literals at `:72, :210` → `CHECK_ALL_JOB_ID` with its declaration moved above `_TZ_BOUND_CRON_JOBS` (same value).
- `_resolve_zoneinfo` stays (00-smells A7 suggests reusing `timing._resolve_tz` — that is a private cross-package-layer import; not worth it).

### 3.9 Repository copy-paste — see §4.

### 3.10 `ClusterScreen` (`tui/screens/cluster.py`, 800 lines, MI 1.08)

- Pure builders in `tui/rows.py` (`Row = tuple[str | None, list[str | Text]]`, as consumed by `refill`):
  `plant_rows(status) -> list[Row]` (`:279-305`), `sensor_rows(status)` (`:327-353`), `decision_rows(payload)`,
  `history_rows(payload)`, `window_rows(detail)`, `config_rows(detail)` (`:355-429`), `efficacy_rows(efficacy)`,
  `insights_text(insights, monitor) -> Text`, `stats_rows(stats) -> list[tuple[str, str]]` (from `_load_insights`
  `:431-495`, CC 24), `decision_panel_text(decision) -> Text`, `irrigator_info_text(summary) -> Text`
  (`_render_overview` `:179-246`), `forecast_rows(f) -> list[tuple[str, str | Text]]`. Same colours, styles, column
  order and placeholder strings; unit-testable without a Pilot.
- `_load_insights` becomes: gather (unchanged `asyncio.gather` order) → four `query_one(...).update/show(...)` calls fed by the builders.
- CRUD dispatch: `action_new/edit/delete` (names frozen) become `handler = {"tab-plants": self._new_plant, …}.get(self.active_tab)`; unknown tab → the same `notify` text. `active_tab` is still read exactly once per action. Each handler keeps `cid = self.cluster_id` and the same lambdas/`form_then`/`confirm_then` arguments (incl. the stale "Blank care fields…" note, bug B-19 — rendered text is pinned).
- `_selected(table_id, rows)` moves to `DataScreen` (also used by `alerts.py:90`, `settings.py:128`); message "Select a row first" unchanged.

### 3.11 CLI client (`client.py`, 464 lines, 174 strict mypy errors)

- `_API = "/api/v1"`; `_drop_none(fields: Mapping[str, object]) -> dict[str, object]` replaces 9 comprehensions (insertion order preserved).
- `_object(self, method, path, **kw) -> dict[str, Any]` and `_array(...) -> list[Any]` = `cast(...)` over the **unchanged** `_request` (no runtime `isinstance` check — that would change behaviour on odd payloads; B-17 stays unfixed). Kills the 78 `return-value` errors.
- No signature changes to public methods (TUI calls several positionally, e.g. `c.add_window(cid, v["start_hour"], …)` in `cluster.py:611`). Replacing `update_*(**kwargs)` with explicit keyword-only params is listed as optional, only where every call site's key set is closed and proven.
- `resolve_server_url(obj: str | None) -> str` in `commands/_helpers.py`, used by `get_client`, `commands/auth.py:26`, `commands/tui.py:35`.

### 3.12 Later batches (same recipe, lower priority)

`services/maintenance.collect_maintenance_alerts` (CC 23), `data_quality.build_report` (124 lines), `search.search`
(118), `anomaly.scan` (115), `charts._threshold_for_cluster` (CC 20), `pump_watcher.watch` (`_outcome(kind, …)`),
`web/routes/clusters.cluster_detail` (96), `tui/screens/system.load` (CC 24), `settings.load` (CC 19),
`tui/model.summarize` (CC 18), `widgets.MetricChart.show_payload` (CC 19).

---

## 4. Repository split by aggregate behind the same `IrrigationRepository`

**Choice: mixins composed into the existing class**, living in a private `_repo/` package, with `repository.py` as a
module (not converted into a package).

```python
# repository.py  (module path, class name, __init__, every method name/signature/default unchanged)
from greenhouse_core import _repo as _r   # only a private name is added to dir(repository)
class IrrigationRepository(
    _r.clusters.ClusterQueries, _r.plants.PlantQueries, _r.irrigators.IrrigatorQueries, _r.sensors.SensorQueries,
    _r.readings.ReadingQueries, _r.events.EventQueries, _r.configs.ConfigQueries, _r.decisions.DecisionLogQueries,
    _r.activity.ActivityQueries, _r.alerts.AlertQueries, _r.plant_health.PlantHealthQueries,
    _r.vacation.VacationQueries, _r.windows.WindowQueries, _r.preferences.PreferenceQueries,
):
    """Repository for irrigation system data access."""
    def __init__(self, session: Session):
        self.session = session
```

Why mixins:
- `self.session` must stay a public attribute (17 bypasses + `check_all_clusters` + `set_check_all_paused` use it) — mixins share it for free; a composition facade would need 80 forwarding methods (≈ +400 lines of pure boilerplate, every signature duplicated and liable to drift).
- Cross-aggregate calls are few and explicit: `delete_plant`/`move_plant` → `_close_open_sensor_assignment` (sensors), `update_sensor` → `reassign_sensor_to_plant` (same mixin), `get_effective_config` → its own mixin. They are typed by giving each mixin that needs a sibling a small `Protocol` self-type, or simply by co-locating (assignments live in `sensors.py`).
- `_RepoBase` declares `session: Session` so mypy strict passes on each mixin.

Copy-paste removal — only where bodies are **byte-identical in semantics** (00-smells A2), as `_RepoBase` helpers:

| Helper | Applies to | Why it's split three ways |
|---|---|---|
| `_patch_skip_none_first(row, fields)` | `update_vacation_window` (`:963-967`), `update_irrigation_window` (`:1017-1021`) | `if value is None: continue` evaluated **before** `hasattr` |
| `_patch_hasattr_first(row, fields)` | `update_cluster` (`:1154-1156`), `update_plant` (`:1174-1176`), `update_preferences` (`:1048-1050`) | `hasattr(row, key) and value is not None` — `hasattr` runs first and can lazy-load a relationship (autoflush); keep the evaluation order per group |
| `_patch_with_json_config(row, fields)` | `update_irrigator` (`:1292-1298`) and the post-`plant_id` loop of `update_sensor` (`:1266-1272`) | `config` dict → `json.dumps` |
| `_delete_by_id(model, row_id) -> bool` | `delete_cluster`, `delete_sensor`, `delete_irrigator`, `delete_vacation_window`, `delete_irrigation_window` | (`delete_plant` keeps its assignment-closing variant) — verify no model defines `__bool__`/`__len__` (none today) so `not row` ≡ `row is None` |
| `_list_by_id(model, *, filters: Sequence[ColumnElement[bool]], limit, after_id)` | `list_all_sensors/irrigators/plants` (`:1056-1137`) | WHERE order: filters in declared order, then `id > after_id`, then LIMIT — same SQL text |

**Not folded:** `set_irrigation_config` / `update_global_irrigation_config` (they deliberately accept `None`), the
`timestamp or int(time.time())` defaults (`0` means "now" — kept, it's behaviour).

Additive read methods retire the 17 `repo.session` bypasses one service per commit: `get_vacation_window(id)`,
`get_open_alert_by_dedup_key(key)`, `list_open_alerts(code_prefix)`, `list_sensors_by_ids(ids)` (**no ORDER BY**, to
match `charts.py:117`), `list_events_for_irrigators(...)` (efficacy/charts), `search_*` (search.py). Each must emit the
same SQL predicates and ordering as the inline query it replaces; `get_plant` already exists for the four
`session.get(Plant, …)` sites.

---

## 5. Pattern choices (each tied to a smell) and rejected alternatives

| Pattern | Smell it removes | Rejected alternative (and why) |
|---|---|---|
| Consumer-side structural `Protocol` ports, typing-only | 9 domain→persistence runtime edges; untyped `weather_client`; invisible dependency surface | ABC-based interfaces + adapter classes (runtime overhead, wrappers to maintain, identical behaviour); `runtime_checkable` (isinstance checks change nothing useful); a single giant `RepositoryProtocol` (re-creates the god interface). |
| Leaf `runtime` module | `_app` service locator, lazy cycle, rebinding trap | `JobScheduler` Protocol + DI (one impl, no seam); passing `app` through every service constructor (changes `IrrigationService` construction in `deps.py` and the scheduler for no gain); a DI container (`dependency-injector`, `punq`) — explicitly out of lens. |
| Functional core / imperative shell inside `logic/` (`_rules.py`, pure helpers in trends/stress/sensors/fallback) | CC 21–36 functions mixing queries and judgement; hidden wall-clock month in a rule | Rule-registry / Strategy / Chain-of-Responsibility for the engine (00-smells A1 "do not"): the explicit sequence *is* the specification (frozen order, early returns); a registry hides it. |
| Small value objects (`_EngineInputs`, `_Gate`, `_HealthBands`, `_RainOutlook`) | 12-param helpers, tuples-of-6, re-derived care values | Pydantic models for internal values (validation cost, would tempt OpenAPI reuse); typed service-result dataclasses replacing the dict results (ripples into templates/`IrrigateResponse(**result)`). `TypedDict` gives the typing without changing runtime. |
| Context manager `job_session` | 9 copies of open/try/commit/rollback/log/close | A decorator `@background_job("label")` (hides which jobs commit when; several commit conditionally); a generic Unit-of-Work (behavioural: routes own commits today). |
| Mixins for the repository | 1309-line module, MI 14.48, copy-paste | Composition facade (80 forwarders); splitting into sub-repositories injected separately (changes every constructor and `deps.py`); converting `repository.py` into a package (needs every incidental import re-exported in `__init__` for the `dir()` golden, and changes nothing a reader cares about). |
| Dict dispatch for TUI CRUD | depth-5 `if/elif` ×3 on tab id | Per-tab `Screen` subclasses (changes widget tree/ids → `app.tcss` and Pilot tests); command objects. |
| Composition-root helpers in `create_app` | 132-line factory | App-builder class / plugin registry for routers (indirection without a second variant). |

---

## 6. Ordered migration (one small green commit each; lowest risk first; engine and devices last)

Per-commit test selection = the focused map entry (`refactor/baseline/module-tests-map-direct.json`) ∪
`tests/server/test_mcp.py` (any server change) ∪ `tests/cli/test_completeness.py` (any CLI change) ∪ the Phase-1
contract tests named below. High-risk steps (scheduler, engine, devices) additionally run the conservative map once at
the end of their group. "grid" = `tests/test_contract_decision_grid.py`; "pipeline" = `tests/server/test_contract_pipeline.py`.

**Phase A — guardrails (no production change)**
1. Add `import-linter` and `mypy` to the dev group (`uv.lock` dev-group change is allowed). Add the §8 contracts with today's violations listed explicitly in `ignore_imports` (9 logic/learning→repository edges, `services.irrigation→scheduler`, `services.system_health→scheduler`). Run: `uv run lint-imports`, `uv run mypy` (baseline counts 176 / 769 recorded).
2. Ruff ratchet config: `C901` (max 10) + `PLR0912/0915` enabled with today's 18/7/6 offenders in `per-file-ignores`; `PLC0415` allowed only with `# noqa: PLC0415 — <reason>`. Run: `uv run ruff check libs/ tests/`.

**Phase B — leaf / pure / adapters with strong goldens**
3. CLI `resolve_server_url` + `_drop_none` + `_API`. Tests: `tests/cli/test_cli.py tests/cli/test_completeness.py tests/cli/test_contract_help.py tests/cli/test_contract_json_output.py`.
4. CLI typed `_object/_array` (annotation + cast only). Same tests + `tests/cli/test_tui.py`; mypy strict on `greenhouse_cli.client`.
5. TUI `DataScreen._selected` hoist. Tests: `tests/cli/test_tui.py tests/cli/test_contract_tui.py`.
6. TUI `rows.py` builders (one commit per builder group: plants/sensors, decisions/history, windows/config, insights/stats/efficacy, overview/forecast). Same tests + new unit tests for `rows.py`.
7. TUI CRUD dict dispatch. Same tests.
8. `_light.py` + `utils.py` re-export; `issues.py`/`stress.py`/`engine.py`/`sensors.py` import by name from `_light` (and `> 15` → `NIGHT_LUX_THRESHOLD` in a separate commit). Tests: `tests/test_utils.py tests/test_learning.py tests/test_cleaning.py` + grid.

**Phase C — repository (move-only commits separated from helper commits)**
9. `_RepoBase` helpers in `repository.py` itself (three `_patch_*`, `_delete_by_id`, `_list_by_id`), one helper per commit. Tests: `tests/test_db.py tests/server/test_clusters.py tests/server/test_plants.py tests/server/test_sensors.py tests/server/test_irrigators.py tests/server/test_vacation.py tests/server/test_windows.py tests/server/test_preferences.py tests/server/test_sensor_assignments.py` + pipeline.
10. Move aggregates into `_repo/*` mixins — **one aggregate per commit**, pure moves (no edits). Tests: `tests/test_db.py` + grid + pipeline + `tests/server/test_contract_openapi.py`; conservative map after the last move.
11. Additive read methods + replace `repo.session` bypasses, one service per commit (health_monitor, charts, search, efficacy, routes/vacation, routes/charts, web/plant_dashboard). Tests: the focused map for that module (e.g. `services.charts` → `tests/server/test_charts_*.py tests/server/test_web_charts.py`).

**Phase D — domain ports and pure decomposition**
12. `logic/_ports.py`; annotate `logic/*` and `learning/*` with ports; ORM imports → `TYPE_CHECKING`; drop the 9 `ignore_imports`. Annotations only. Tests: `tests/test_logic.py tests/test_learning.py tests/test_cleaning.py tests/test_engine_timing.py tests/test_timing.py tests/test_leak_hold.py tests/test_vacation_rationing.py` + grid; `lint-imports`; mypy strict on `greenhouse_core.logic._ports`.
13. `RainForecast` / `WeatherSource` annotations. Tests: grid (weather family), `tests/server/test_forecast.py`, pipeline.
14. trends decomposition. Tests: `tests/test_logic.py` + grid (series, soil families).
15. stress decomposition. **No direct tests exist** (`module-tests-map-direct.json` → `[]`): grid only — request a mutation pass on `stress.py` before merging.
16. sensors + fallback decomposition. Tests: `tests/test_cleaning.py` + grid (fallback, driest).
17. `learning/issues` (`detect_issues`, then `detect_conflicts`, separate commits); `profiling` minor. Tests: `tests/test_learning.py` + grid (learning) + `tests/server/test_alerts.py tests/server/test_insights.py`.
18. `PlantHealthService.compute_score`. Tests: `tests/server/test_plants_health.py tests/server/test_web_plant_hero.py tests/server/test_web_plant_pages.py tests/server/test_web_charts.py tests/server/test_search.py tests/cli/test_tui.py`.
19. `ForecastService.predict_next_irrigation` (+ shared `rain_precip_mm`, used by forecast only in this commit). Tests: `tests/server/test_forecast.py tests/server/test_web_analytics.py tests/server/test_web_insights.py tests/server/test_web_redirects.py`.
20. `create_app` composition-root helpers. Tests: `tests/server/test_mcp.py tests/server/test_contract_openapi.py tests/server/test_contract_mcp.py tests/server/test_contract_web_html.py tests/server/test_auth.py tests/server/test_scheduler.py tests/server/test_scheduler_jobs.py tests/server/test_system_health.py`.

**Phase E — runtime / scheduler / pipeline (high risk, two reviewers)**
21. `runtime.py` with `bind_app/current_app` + `job_session`; `init_scheduler` calls `bind_app`; `scheduler.__getattr__("_app")`. Tests: `tests/server/test_scheduler.py tests/server/test_scheduler_jobs.py tests/server/test_scheduler_pause.py tests/server/test_scheduler_shutdown.py tests/server/test_leak_rearm.py tests/server/test_contract_settings.py` (incl. `golden/contracts/scheduler_jobs.json`) + pipeline.
22. Move `scheduler` object, `_JOB_DEFAULTS`, shutdown event/functions into `runtime`, re-export from `scheduler.py`; `services/irrigation.py` and `services/system_health.py` import `runtime` at module level; remove lazy imports and the 2 `ignore_imports`. Same tests + `tests/server/test_system_health.py tests/server/test_web_health.py`.
23. Scheduler job bodies → `job_session` (one job per commit). Same tests as 21.
24. `services/irrigation.py` job plumbing: `handle_watcher_interrupted`, `schedule_pump_watcher`, `_run_leak_check` (one per commit). Tests: `tests/server/test_scheduler_shutdown.py tests/server/test_scheduler_jobs.py tests/server/test_leak_rearm.py tests/server/test_pump_watcher.py` + pipeline.
25. `run_irrigation_pipeline`: (a) `_error_result/_decision_result` + `PipelineResult`; (b) `_decide`, `_log_decision_skip`; (c) `_actuation_target`; (d) health gate pair; (e) `_actuate` + success/failure. Tests per commit: pipeline + `tests/server/test_operations.py tests/server/test_leak_rearm.py tests/server/test_health_monitor.py tests/server/test_notify.py tests/server/test_decisions.py tests/server/test_irrigators.py tests/server/test_rate_limit.py tests/server/test_web_dashboard.py tests/cli/test_tui.py`.
26. `check_cluster` builder, `monitor_cluster` helpers. Same set + `tests/server/test_alerts.py`.

**Phase F — engine (last but one)**
27. `_tz_name`. 28. `_record` + `_finish` (closure → method). 29. `_pre_evaluation_gates` + `_Gate`. 30. `_gather_inputs` + `_EngineInputs` + `_fallback_decision`. 31. `_apply_adjustments` + `_evaluate`. 32. Move pure rules to `logic/_rules.py` (move-only). 33. `apply_soil_moisture_rule` decomposition + `set_dosage`. 34. light rule takes `light_factor`. 35. `_apply_window_rule` drops unused params; `one_reason_decision` drops unused params. 36. `_apply_vacation_budget` / `_apply_seasonal_multiplier` pure helpers; engine weather rule uses `rain_precip_mm`.
Tests every commit: grid (all 14 families) + `tests/test_logic.py tests/test_engine_timing.py tests/test_leak_hold.py tests/test_vacation_rationing.py tests/server/test_clusters.py tests/server/test_decisions.py tests/server/test_health_monitor.py tests/server/test_scheduler.py` (patches `engine_mod.time`) + pipeline; conservative map at 31, 33, 36.

**Phase G — devices (last, minimal)**
37. `gateway._parse_dps(items)` for the duplicated v1/v2 loop **without** adding `ValueError` handling to the v2 path (inconsistency recorded, not fixed). Tests: `tests/devices/ tests/test_cloud.py tests/devices/test_contract_adapters.py`. Nothing else in `devices/`.

---

## 7. Risks, and what this proposal deliberately does NOT do

### 7.1 Golden-pinned `dir()` and new submodules
`00-tests.md` G10 snapshots `sorted(dir(mod))` for frozen modules and packages; **package snapshots include imported
submodule attributes** (`greenhouse_core.logic` "21 names" = 12 `__all__` + 9 submodules). Any new public submodule
(`logic/ports.py`) or new top-level import in a frozen module (`from greenhouse_server import runtime` in
`scheduler.py`) would change those goldens. Mitigation built into this plan: every new module/name inside a snapshotted
namespace is `_`-prefixed (`logic/_ports.py`, `logic/_rules.py`, `_repo/`, `_light.py`, `_PROTECTED_API_ROUTERS`,
`import … as _runtime`), and incidental imports in frozen modules are kept (`import time` in engine/repository,
`import threading` in `scheduler.py` with a `# noqa: F401 — dir() is golden-pinned` if it becomes unused). **Decision
request for the orchestrator:** define G10 as "`__all__` + public non-module attributes" so stdlib module names don't
have to be kept alive artificially.

### 7.2 Import-time side effects and import order
- `plant_db.py:9-13` reads `IRRIGATION_PLANT_DB_PATH` at import; `utils._display_timezone` is a process global. Moving the light helpers to a leaf `_light.py` (not into `logic/`) is deliberate: putting them under `logic/` would make `import greenhouse_core.utils` execute `logic/__init__` → `engine` → `utils` (partially initialised) → **ImportError cycle**, and would also drag `plant_db` (env read) into every `utils` import.
- Domain modules stop importing `repository`/`models` at runtime → import *order* of those modules shifts slightly; neither has import-time side effects beyond SQLAlchemy class registration (models is imported by `database`/`repository` before any mapper use). Verified by running the full suite once after step 12.
- Hoisting the lazy imports in `scheduler.py` job bodies is **not** done (00-baseline: `greenhouse_server.app` import is already 1.78 s; no need to change who-imports-what at startup). Only the `services/irrigation.py` lazy imports are hoisted, after `runtime.py` removes the cycle.

### 7.3 The `scheduler` ↔ `services.irrigation` cycle and the `_app` trap
- Never `from greenhouse_server.runtime import _current_app` (value copy). Readers call `runtime.current_app()`.
- `schedule_pump_watcher`'s job closes over the app/registry captured **at schedule time** (today's semantics, via a local import binding) — preserved explicitly (§3.1).
- `running_scheduler` fixture and `init_scheduler`'s `configure()`/`remove_all_jobs()` operate on the same object, re-exported; identity is asserted by a one-line test (`greenhouse_server.scheduler.scheduler is greenhouse_server.runtime.scheduler`).
- `test_scheduler_jobs.py:139` sets `app.state.device_registry` **after** `create_app`; jobs must keep reading `app.state.*` at run time, never cache at init. `runtime` stores the app, not its state values.

### 7.4 Logger names = module names
All loggers are `getLogger(__name__)`. This plan keeps every log call in its current module: pipeline/job plumbing stay
in `services/irrigation.py` (`test_migrations.py:149` reads that logger; `test_scheduler_shutdown.py` uses caplog),
job bodies stay in `scheduler.py` (`test_contract_settings.py:279` caplog, `test_scheduler.py:88` patch), `job_session`
logs through the **caller's** logger. `_rules.py`/`_light.py`/`_repo/*` do not log. Device loggers untouched
(`test_contract_adapters.py` caplog on `devices.registry`, `devices.gateway`, `devices.irrigators.ik10pw`).

### 7.5 OpenAPI / MCP drift
No route function, route docstring, `response_model`, Pydantic class, field order or class docstring is touched (route
handlers are not decomposed in this proposal beyond calling services). `PipelineResult` is a `TypedDict` used only as a
return annotation on a service method — it never reaches FastAPI. `create_app` keeps router include order. Guarded by
`golden/contracts/openapi.json`, `mcp_tools.json`, `routes.json` on every server commit.

### 7.6 `constants.py` additions
Invariant 5 wants literals like `hours=24`, `0.5`, `2.0 mm`, `6 h`, `-15/+10`, `86400` in `constants.py`. Adding names
changes the G11 constants golden if it is an exact snapshot. **Decision request:** make G11 "every baseline name keeps
its value; new names allowed". Until then this plan names literals as module-private `_UPPER` constants next to their
single use (no value change) and defers promotion to `constants.py`.

### 7.7 Textual coupling
`rows.py` builders return the same `Text`/str cells; widget ids, classes, `BINDINGS`, `action_*`/`on_*` names and
`CSS_PATH` are untouched; `ClusterScreen` stays one screen with the same `compose()` tree. Guarded by
`tests/cli/test_contract_tui.py` and `tests/cli/test_tui.py`.

### 7.8 Reflection-level changes accepted as non-contract
Repository methods' `__qualname__` (`ClusterQueries.get_cluster`), the two error classes' defining file (their
`__module__` is pinned back to `greenhouse_core.repository`), `inspect.signature` annotations of
`IrrigationLogic.__init__`/`IrrigationService.__init__` (names/defaults unchanged). Listed here so reviewers can veto.

### 7.9 Deliberately NOT done
- No `Clock` port; no repository clock injection; no DI framework; no service-layer repository Protocols.
- No rule-registry/strategy for the engine; no typed service-result classes (dicts stay dicts); no `schemas.py` split; no unit-of-work change of route commits.
- No moving of `services/irrigation.py` job plumbing to another module (logger + `_time` tripwires; the layering problem is solved by `runtime.py`).
- No device restructuring beyond `_parse_dps`; no fix to B-2 (`signal.signal` off main thread) or any other §E bug — recorded only.
- No de-duplication of the learner's 3× run per check, of stress re-querying plant care, or of the 3 duplicate `get_preferences` calls (call counts are kept as-is).
- No edits to `plugin/` docs (out of scope per BRIEF) — behaviour is unchanged, so `LOGIC.md` stays correct; file-layout references in it, if any, are noted for a follow-up.

---

## 8. import-linter contracts and tooling ratchet

`pyproject.toml` (root), import-linter ≥ 2.1 (for `|` independent siblings in layers):

```toml
[tool.importlinter]
root_packages = ["greenhouse_core", "greenhouse_server", "greenhouse_cli"]
include_external_packages = true
exclude_type_checking_imports = true

[[tool.importlinter.contracts]]
name = "Packages: server builds on core; core never imports server"
type = "layers"
layers = ["greenhouse_server", "greenhouse_core"]

[[tool.importlinter.contracts]]
name = "CLI is HTTP-only: never imports core, server, ORM or web framework"
type = "forbidden"
source_modules = ["greenhouse_cli"]
forbidden_modules = ["greenhouse_core", "greenhouse_server", "sqlalchemy", "fastapi"]

[[tool.importlinter.contracts]]
name = "Domain is pure: logic/learning never touch persistence, devices, sync, drivers, or the display-tz global"
type = "forbidden"
source_modules = ["greenhouse_core.logic", "greenhouse_core.learning"]
forbidden_modules = [
  "greenhouse_core.repository", "greenhouse_core._repo", "greenhouse_core.database", "greenhouse_core.models",
  "greenhouse_core.devices", "greenhouse_core.sync", "greenhouse_core.auth", "greenhouse_core.stats",
  "greenhouse_core.migrations", "greenhouse_core.utils",
  "sqlalchemy", "tinytuya", "httpx", "urllib.request",
]
ignore_imports = [   # Phase A only — each line deleted by the commit that removes the edge (step 12)
  "greenhouse_core.logic.* -> greenhouse_core.repository",
  "greenhouse_core.learning.* -> greenhouse_core.repository",
  "greenhouse_core.logic.timing -> greenhouse_core.models",
  "greenhouse_core.logic.fallback -> greenhouse_core.models",
  "greenhouse_core.learning.* -> greenhouse_core.models",
  "greenhouse_core.logic.* -> greenhouse_core.utils",       # removed at step 8 (imports move to _light)
  "greenhouse_core.learning.issues -> greenhouse_core.utils",
]

[[tool.importlinter.contracts]]
name = "learning builds on logic, not vice versa (engine's advisory hook is the documented exception)"
type = "layers"
layers = ["greenhouse_core.learning", "greenhouse_core.logic"]
ignore_imports = ["greenhouse_core.logic.engine -> greenhouse_core.learning"]

[[tool.importlinter.contracts]]
name = "Inside logic: engine orchestrates; rules and values never import it"
type = "layers"
layers = [
  "greenhouse_core.logic.engine",
  "greenhouse_core.logic.fallback | greenhouse_core.logic.sensors | greenhouse_core.logic.stress | greenhouse_core.logic.trends",
  "greenhouse_core.logic._rules | greenhouse_core.logic.timing | greenhouse_core.logic.cleaning | greenhouse_core.logic.plant_needs",
  "greenhouse_core.logic.decision | greenhouse_core.logic._ports",
]

[[tool.importlinter.contracts]]
name = "Server: composition root > adapters > request wiring > jobs > services > runtime/config"
type = "layers"
layers = [
  "greenhouse_server.app",
  "greenhouse_server.routes | greenhouse_server.web",
  "greenhouse_server.deps | greenhouse_server.auth",
  "greenhouse_server.scheduler",
  "greenhouse_server.services",
  "greenhouse_server.runtime | greenhouse_server.config",
]
ignore_imports = [   # removed at step 22
  "greenhouse_server.services.irrigation -> greenhouse_server.scheduler",
  "greenhouse_server.services.system_health -> greenhouse_server.scheduler",
]

[[tool.importlinter.contracts]]
name = "TUI view-model modules stay widget-free and I/O-free"
type = "forbidden"
source_modules = ["greenhouse_cli.tui.model", "greenhouse_cli.tui.rows", "greenhouse_cli.tui.formatting"]
forbidden_modules = ["textual", "greenhouse_cli.tui.screens", "greenhouse_cli.tui.widgets", "greenhouse_cli.client", "httpx"]
```

(Exact module-glob syntax in `ignore_imports` is verified in step 1; where a wildcard is unsupported the edges are
listed individually from `refactor/baseline/import-graph.txt`.) Note that import-linter sees function-level imports, so
the engine's lazy learner import and `commands/tui.py`'s lazy TUI import are visible and handled explicitly.

**Ratchet (CI, in `make check`):**
- `uv run lint-imports` — must pass; `ignore_imports` may only shrink.
- mypy: global default mode must not exceed the baseline count (176) — scripted compare on `mypy … | tail -1`;
  `[[tool.mypy.overrides]] strict = true` for every module a commit touches, added in that commit and never removed.
  Initial strict set after Phases B–F: `greenhouse_cli.client`, `greenhouse_cli.tui.rows`, `greenhouse_cli.commands._helpers`,
  `greenhouse_core._light`, `greenhouse_core._repo.*`, `greenhouse_core.logic.*`, `greenhouse_core.learning.*`,
  `greenhouse_server.runtime`, `greenhouse_server.scheduler`, `greenhouse_server.services.{irrigation,health,forecast}`,
  `greenhouse_server.app`.
- ruff: `C901`/`PLR0912`/`PLR0915` per-file ignores may only shrink; target after this plan: no function above CC 10 in
  `logic/`, `learning/`, `services/{irrigation,health,forecast}`, `scheduler.py`, `tui/screens/cluster.py`.
- xenon (report-only) re-run at the end vs `refactor/baseline/xenon.txt`; radon MI for `repository.py` and
  `tui/screens/cluster.py` reported per module.

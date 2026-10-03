# 40 — Architecture review (Phase 4)

Reviewer: architecture. Scope: current `libs/` at `3491e23`. Excluded because WP8 is still in progress: `logic/engine.py`, `logic/timing.py`, `devices/**` and `core/sync.py`.
Method:
- `uv run lint-imports`.
- An AST import graph (`scratchpad/graph.py`). It counts top-level, function-level (lazy) and `TYPE_CHECKING` edges separately, and finds cycles with Tarjan's algorithm.
- A size/method census (`scratchpad/sizes.py`) and `refactor/scripts/sizecheck.py`.
- Greps for `session_factory()`, `commit()`, `sqlalchemy`, `global`, `os.environ` and `HTTPException(404`.
- A temporary import-linter config, run with `lint-imports --config scratchpad/importlinter.toml`.

## Verdict

The package-level layering is sound and enforced:
- core ← server ← cli holds.
- The CLI and TUI are HTTP-only.
- The web layer calls services in-process.
- `routes` and `web` are independent.

`lint-imports` reports **10 kept, 0 broken**. The graph has **no top-level import cycle**. Two cycles exist only through lazy or `TYPE_CHECKING` imports:

1. `scheduler ⇄ services.irrigation`. This cycle is lazy, accepted in §6, and pinned by `ignore_imports`.
2. `tui.app ⇄ tui.screens.{base,search}`. This one is `TYPE_CHECKING`-only and harmless.

The sizecheck reports no violation outside the WP8 files and the approved exceptions.

The real remaining problems are **not** import-direction problems. They are:
- **(a)** One piece of shared mutable state is a latent concurrency bug.
- **(b)** Session/transaction scaffolding still has nine different spellings, and the auth path runs a parallel persistence stack.
- **(c)** Some "dumping-ground" homes and eager package `__init__`s blur where things live.
- **(d)** The current contracts miss several layers that already hold. These can be locked in for free (§ Proposed contracts; all verified KEPT).

Severity: **H** = latent bug or safety relevance · **M** = structural debt that will regress or mislead · **L** = hygiene or navigation · **I** = informational.

## Findings

### A1 — H — The singleton `DeviceHealthMonitor` has a swappable `_repo` and an unlocked cache. Concurrent jobs can rebind it, so a pump-watcher NO_WATER alert can be written into another job's session or lost
- **Evidence:**
  - `services/health_monitor.py:117-124` `bind_repo()` just reassigns `self._repo`.
  - Callers rebind it on every tick or job:
    - `scheduler.py:327` (`_build_irrigation_service`, every check),
    - `scheduler.py:381` (`_health_monitor_job`),
    - `services/irrigation.py:199-201` (pump watcher job, which lives for the whole irrigation, i.e. minutes).
  - The watcher's dry-run trip calls `monitor.record(...)` (`services/pump_watcher.py:343`). That call writes through `self._repo` (`health_monitor.py:309` `upsert_alert` and `:330` `resolve_alert`).
  - The watcher then commits **its own** repo (`pump_watcher.py:363`).
  - APScheduler runs jobs on a thread pool. `max_instances=1` applies per job, not across jobs. So `_health_monitor_job` or `_check_job` can rebind the monitor mid-watch.
  - When that happens, the NO_WATER alert goes into the other job's session. That session either commits it at an arbitrary time or has already closed, in which case the alert is silently never committed.
  - Meanwhile `_cache[key]` (`:115`, a plain dict mutated with no lock) already records the alarm as raised, so the transition is never emitted again.
  - Request-scoped `IrrigationService` (`deps.py:215-235`) uses the same monitor **without** `bind_repo`. `is_actuation_blocked` only reads the cache, so that path is safe today.
- **Fix (behavior change, label `fix(consistency)`; not in pure-refactor scope):**
  - Make the monitor repo-less. It keeps only the cache plus a `threading.Lock`.
  - Pass the repo per call: `record(repo, …)`, `poll_all(repo)`, `backfill_from_history(repo)`. Alternatively, add `monitor.bound(repo)`, which returns a light view that shares the cache.
  - Delete `bind_repo`.
  - Until then, record the issue in `REFACTOR_NOTES.md` "Observed bugs" and pin it with a two-thread characterization test.
- **Behavior impact:** health alerts are always written in the caller's transaction. No API surface changes.

### A2 — M — Session scaffolding has nine spellings, and OD2's "one session helper for jobs" is only partly realised
- **Evidence:** these sites each open a session:
  1. `deps.get_session` (`deps.py:25`)
  2. `auth._session_from_app` (`auth.py:148`)
  3. `services/_session.job_session` (`_session.py:22`), used by the 5 scheduler jobs only
  4. `services/irrigation._run_pump_watcher` (`:192`)
  5. `services/irrigation._run_leak_check` (`:335`)
  6. `services/irrigation.rearm_leak_checks` (`:435`)
  7. `scheduler.init_health_monitor` (`:401`)
  8. `app._startup_timezone` and `_restore_persisted_scheduler_pause` (`app.py:324,343`)
  9. `web/context._repo_from_request` (`:34`)

  Plus `auth.bootstrap_admin`, which uses `Session(engine)` and bypasses the session factory entirely (`auth.py:332`).

  The divergences are documented: early return, conditional commit and read-only. They are still one helper with two flags.
- **Fix:**
  - Extend `job_session` with `commit: bool = True` (read-only callers pass `False` and get only close).
  - Add a `read_session(app)` context manager for the 4 read-only sites (`app.py` ×2, `web/context`, `rearm_leak_checks`).
  - Make `bootstrap_admin` take `session_factory`.
  - Rename `services/_session.py` to `services/jobs.py`. It is imported across packages (`scheduler.py:22`), so the leading underscore misleads.
- **Behavior impact:** none, if the commit/rollback/log order is kept per site. The pump watcher keeps its conditional commit inside the body.

### A3 — M — The auth path is a second persistence stack, and each authenticated request opens two DB sessions *(already C-TX-4; still open)*
- **Evidence:**
  - `require_user` depends on `auth._session_from_app` (`auth.py:148-154,197`). Handlers depend on `deps.get_session`.
  - FastAPI caches dependencies by callable identity, so every protected `/api/v1` request opens **two** sessions. With `auth_enabled=False` it opens one that is never used.
  - The import-linter `deps | auth` siblings rule forces this duplication.
  - User persistence bypasses the repository:
    - `greenhouse_core/auth.py:44-76` runs `session.scalars(select(User)…)`, `session.add` and `flush`.
    - `routes/auth.py:96` and `web/routes/auth.py:87` call `session.commit()` on a raw `Session`.
    - The private names `_session_from_app` and `_get_settings` are imported across modules.
- **Fix:**
  - Create `greenhouse_server/state.py`, a layer below `deps|auth`, holding `get_session` and `get_settings`. Re-export them from `deps` (the import path is frozen). Have `auth` use them.
  - Add `IrrigationRepository.get_user_by_username / get_user / add_user / any_user`. Keep `greenhouse_core.auth` as thin wrappers (pinned surface) and make the auth routes use `RepoDep` plus `repo.commit()`.
- **Behavior impact:** the auth lookup and the handler share one session and identity map. That is a labeled change, as C-TX-4 already states. The repository move by itself has no behavior impact.

### A4 — M — `services/irrigation.py` is a pipeline service *and* the background-job plumbing that reaches up into the scheduler
- **Evidence:**
  - Lines `65-445` (≈380 of 972 lines) are job code, not `IrrigationService`: watcher interruption, `_run_pump_watcher`, `schedule_pump_watcher`, leak-check scheduling, `_run_leak_check` and `rearm_leak_checks`.
  - Every lazy `from greenhouse_server.scheduler import _app, scheduler, …` (`:251,327,366,388,429`) lives in this block, and it is the only reason for the services→scheduler `ignore_imports` entry.
  - `manual_control.py` imports `irrigation` only to get `schedule_pump_watcher`.
- **Fix:**
  - Move the block verbatim into `services/irrigation_jobs.py`, keeping the lazy scheduler imports lazy and the function-level reads of `_app` intact.
  - Re-export the four by-path names (`_add_leak_check_job`, `_run_leak_check`, `rearm_leak_checks`, `schedule_pump_watcher`) plus `handle_watcher_interrupted` from `services.irrigation`. Tests call these by path and do not monkeypatch them; verify that with `PATCHED_PATHS` before moving.
  - Narrow the ignore to `services.irrigation_jobs -> scheduler`.
  - The logger name changes, so the loggers golden decides. If the golden pins `greenhouse_server.services.irrigation` for these records, keep `logger = logging.getLogger("greenhouse_server.services.irrigation")` in the new module.
- **Behavior impact:** none, if the logger name is kept. Otherwise this is a golden decision. If the owner keeps the size exception (D9), at least add a banner comment that splits the module into "job plumbing" and "pipeline".

### A5 — M — `greenhouse_core.utils` is a dumping ground holding a process-global display timezone
- **Evidence:** `utils.py` mixes two unrelated concerns:
  - Seasonal light math (`seasonal_light_factor`, `daytime_lux_readings`, `effective_light_threshold`; domain, used by `logic/stress`, `learning/issues` and `services/maintenance`).
  - A mutable module global `_display_timezone` with set/get, plus an `os.getenv("IRRIGATION_TZ")` read (`:57-78`). `app.py:250` and `scheduler.py:261-270` set it. `web/filters.format_ts`, `web/routes/vacation.py:55` and `format_timestamp` read it.
  - Two `FastAPI` apps in one process (the test suite) share this clock.
- **Fix:**
  - Move the light functions to `logic/light.py` and the display clock to `greenhouse_core/display.py`.
  - Keep `utils` as an explicit re-export shim, since the import path is pinned and `PATCHED_PATHS` patches `learning.issues.{seasonal_light_factor,effective_light_threshold}` at the *consumer*, which stays valid.
  - The env read moves module, so `env_reads.json` changes. That makes this a reviewed golden update; otherwise leave the getter in `utils`.
- **Behavior impact:** none, apart from the env-read golden.

### A6 — M — Eager package `__init__`s make every core import load the whole core, tinytuya and Alembic
- **Evidence:**
  - `greenhouse_core/__init__.py:3-17` re-exports `database`, `devices`, `learning`, `logic`, `models`, `plant_db` and `repository`.
  - `python -X importtime -c "import greenhouse_core.constants"` loads 36 `greenhouse_core` modules, `tinytuya`, `sqlalchemy` and `alembic`, which costs ≈0.55 s.
  - No module in `libs/` uses the root re-exports. `tests/golden/contracts/imports.json` has no `greenhouse_core` root key (it only pins submodules).
  - The re-exports also hollow out the "data definitions stay below behaviour" intent at runtime: importing `constants` executes `devices` and `logic`.
  - `logic/__init__.py` similarly loads `engine`, and with it `repository`, for anyone importing `logic.decision`.
- **Fix:** replace the root re-exports with a lazy `__getattr__` (PEP 562), keeping `__all__`. Alternatively, remove them as dead public names under prune rule 4, with evidence.
- **Behavior impact:** import order and timing only. External `from greenhouse_core import X` keeps working with the lazy variant.

### A7 — M — Not-found is signalled four different ways at the service boundary, and routes re-derive it with ad-hoc 404s
- **Evidence:** 34 inline `HTTPException(404…)` remain in `routes/` and `web/`. The `deps.require_*` cases were folded correctly; these remaining sites translate service sentinels, and the sentinels differ:
  - `None` (`insights.cluster_insights` → `routes/insights.py:35`; `get_cluster_status` → `web/routes/clusters.py:195,242`),
  - a falsy `{}` checked with `if not result` (`routes/operations.py:75`, `web/routes/analytics.py:53`, `routes/charts.py:100,128,156`),
  - a stringly result (`routes/operations.py:169`, `result.get("reason") == "cluster not found"`),
  - typed `LookupError`s (`PlantNotFoundError`, `PlantNotInClusterError`, `JobNotRegisteredError`).
- **Fix:**
  - One rule: services raise a `LookupError` subclass, such as a shared `greenhouse_server.services.errors.ClusterNotFoundError`, and each route keeps its own detail string.
  - Optionally add one app-level `LookupError → 404` handler per surface. That would change detail strings unless the handler maps them, so keep per-route `except`.
- **Behavior impact:** none when the exact detail strings are kept per site. B-16 (unknown cluster → 0) is untouched.

### A8 — L — The repository leaks `IntegrityError`, so a service imports SQLAlchemy
- **Evidence:**
  - `services/inventory.py:13,70-72,118-120` catches `sqlalchemy.exc.IntegrityError` and translates it to `DeviceIdExistsError`.
  - The repository already translates the sibling constraint itself: `IrrigatorExistsError`, at `repository.py:75`.
- **Fix:**
  - Have `add_irrigator` and `add_sensor` catch `IntegrityError`, roll back and raise `DeviceIdExistsError`, defined in `repository.py`.
  - Keep `inventory` re-exporting the name, which preserves its current import path.
  - Drop the sqlalchemy import and the corresponding contract ignore.
- **Behavior impact:** none, if the rollback stays before the raise.

### A9 — L — Transaction rule (OD2) audit: largely compliant, with two notes
- **Compliant:**
  - Every service commit sits before a dependent side effect, with a "commits because" docstring:
    - `manual_control.py:138/192/241` (watcher scheduling or notify),
    - `bulk.py:64` (hardware already stopped; notify follows in the API route),
    - `irrigation.py:936/966` (per-cluster isolation in `check_all`),
    - `pump_watcher.py:363`.
  - CRUD commits happen in handlers (API and web alike). `repo.commit()` and `rollback()` are the only spellings inside services.
- **Note 1:** `scheduler.set_check_all_paused` (`scheduler.py:493-520`) is service logic living in the scheduler module. It mutates the live scheduler **before** `repo.commit()`, so if the commit fails, the in-memory pause and the persisted flag disagree. Keep the order (it is frozen) but add a docstring note.
- **Note 2:** the remaining raw `session.commit()` calls are all the A2/A3 sites. They disappear with those fixes.
- **Drift spotted (not architecture, but untracked):** the web kill switch `web/routes/analytics.py:178-189` does **not** call `maybe_notify`, while the API `routes/bulk.py:28-38` does. Add it to `45-drift-track.md`.

### A10 — L — Navigation: some features live where a newcomer would not look
| Feature | Where it is | Surprise |
|---|---|---|
| Emergency stop and scheduler page/pause (web) | `web/routes/analytics.py:113-189` | The module is named "analytics". `routes.json` pins the qualnames, so the module cannot move. Add a module docstring that lists its three concerns (history/stats/export, scheduler, kill switch). |
| Pump watcher scheduling, leak-check jobs | `services/irrigation.py` | See A4. |
| Display timezone | `greenhouse_core/utils.py` | See A5. |
| Irrigation stats | `greenhouse_core/stats.py` (core), while charts, efficacy, insights and forecast are `services/*` | `stats` is a repo-reading aggregate, just like `services/efficacy`. Leave it there (import path pinned), but add a cross-reference in the `services/__init__` docstring, which should list "where each feature lives". |
| Device registration | `services/inventory.py` | Fine. The name says what it does. |
| Window validation | `services/windows.py` (27 lines; pure rule on constants) | Cohesive. It could live in core (`logic/timing`) because it has no I/O, but it is fine as the shared API/web rule. |

### A11 — L — Redundant function-level imports
`services/irrigation.py:189` and `scheduler.py:310` re-import `IrrigationRepository` inside a function, although the module already imports it at top level. Remove both. Behavior impact: none, because the module is already loaded. The other 19 lazy imports in `scheduler.py` and `irrigation.py` are deliberate (§6 seams, the import-failure-escapes-job ordering at `scheduler.py:341-344`, and the `PumpWatcherService` monkeypatch). Each needs a `# lazy: <why>` tag; most already have one.

### A12 — L — The `plant_db` singleton is production-dead
`plant_db.py:164-184`: `get_plant_database`, `set_plant_database`, `reset_plant_database` and the `_db_instance` global. Production reads `app.state.plant_db` (`deps.get_plant_db`). Only tests call the singleton, and 46-audit keeps it on purpose. Recommend documenting it as a "test convenience" in its docstring, or moving the tests to a fixture and pruning it under rule 4. Behavior impact: none.

### A13 — L — A CLI mirror constant has no drift guard
`greenhouse_cli/constants.py:11` `ALL_WEEKDAYS = 127` mirrors core `FULL_WEEKDAY_MASK`, and no test ties them together. Add a 3-line test in `tests/cli/` that imports both (tests may cross the boundary). Behavior impact: none.

### A14 — I — Cohesion of the new modules
- `tui/render.py` (401 lines, 26 pure builders): cohesive, with no Textual, client or httpx imports (contract-enforced). It sits 1 line over the 400 guideline. If it grows, split it per screen (`render/cluster.py`, `render/system.py`, `render/settings.py`).
- `deps.require_*` (`deps.py:89-168`): one 404 helper per aggregate, used consistently for direct lookups. `deps.py` also holds DI providers, `require_metric` and `MAX_LOOKBACK_HOURS`. These are three sections of one concern ("HTTP-edge resolution"), so acceptable as is.
- `cli/constants.py`: right-sized; see A13.
- `services/_session.py`: see A2 (rename).
- `services/inventory.py` and `services/windows.py`: cohesive shared API/web rules that raise domain errors. Good.

### A15 — I — Hidden global state inventory (the accepted items are listed only so the record is complete)
- `scheduler.scheduler`, `scheduler._app` and `scheduler._shutdown_event`: accepted (§6).
- `utils._display_timezone`: A5.
- `plant_db._db_instance`: A12.
- `DeviceHealthMonitor` (`_repo` and `_cache`): A1.
- `WeatherClient` TTL caches: instance-scoped on `app.state`. Fine.
- `web/context.APP_VERSION`: computed once at import. Fine.

The only direct env reads outside `config.py` are `utils.py:78`, `plant_db.py:17-18` and the CLI reads (all pinned by `env_reads.json`). `devices/gateway.py:116-118` is out of scope.

## Proposed import-linter contract additions

All 10 contracts below were verified **KEPT** alongside the existing 10 (20 kept, 0 broken), using `lint-imports --config scratchpad/importlinter.toml`. A probe (`services.*` independence, which correctly reports BROKEN) confirmed that wildcards expand. Append them to `[tool.importlinter]` in `pyproject.toml`:

```toml
[[tool.importlinter.contracts]]
name = "CLI layers: main > commands > tui > client > constants"
type = "layers"
layers = ["greenhouse_cli.main", "greenhouse_cli.commands", "greenhouse_cli.tui", "greenhouse_cli.client", "greenhouse_cli.constants"]

[[tool.importlinter.contracts]]
name = "TUI layers: app > screens > widgets|render > model > formatting|sprites"
type = "layers"
layers = [
  "greenhouse_cli.tui.app",
  "greenhouse_cli.tui.screens",
  "greenhouse_cli.tui.widgets | greenhouse_cli.tui.render",
  "greenhouse_cli.tui.model",
  "greenhouse_cli.tui.formatting | greenhouse_cli.tui.sprites",
]
ignore_imports = [
  "greenhouse_cli.tui.screens.base -> greenhouse_cli.tui.app",    # TYPE_CHECKING only
  "greenhouse_cli.tui.screens.search -> greenhouse_cli.tui.app",  # TYPE_CHECKING only
]

[[tool.importlinter.contracts]]
name = "Web layers: router > routes|exception_handlers > context|templating > filters|weekdays"
type = "layers"
layers = [
  "greenhouse_server.web.router",
  "greenhouse_server.web.routes | greenhouse_server.web.exception_handlers",
  "greenhouse_server.web.context | greenhouse_server.web.templating",
  "greenhouse_server.web.filters | greenhouse_server.web.weekdays",
]

[[tool.importlinter.contracts]]
name = "API route modules are independent of each other"
type = "independence"
modules = ["greenhouse_server.routes.*"]

[[tool.importlinter.contracts]]
name = "Web route modules are independent of each other"
type = "independence"
modules = ["greenhouse_server.web.routes.*"]

[[tool.importlinter.contracts]]
name = "Services are HTTP-free (no FastAPI/Starlette, no request plumbing)"
type = "forbidden"
source_modules = ["greenhouse_server.services"]
forbidden_modules = ["fastapi", "starlette", "greenhouse_server.deps", "greenhouse_server.auth"]
allow_indirect_imports = true
ignore_imports = ["greenhouse_server.services._session -> fastapi"]  # TYPE_CHECKING annotation only

[[tool.importlinter.contracts]]
name = "SQLAlchemy stays behind the repository"
type = "forbidden"
source_modules = [
  "greenhouse_server.routes", "greenhouse_server.web", "greenhouse_server.services", "greenhouse_server.scheduler",
  "greenhouse_core.logic", "greenhouse_core.learning", "greenhouse_core.stats", "greenhouse_core.plant_db",
  "greenhouse_core.utils", "greenhouse_core.schemas", "greenhouse_core.constants",
]
forbidden_modules = ["sqlalchemy", "alembic", "greenhouse_core.database"]
allow_indirect_imports = true
ignore_imports = [
  "greenhouse_server.routes.auth -> sqlalchemy",        # A3 — drop when auth uses RepoDep
  "greenhouse_server.web.routes.auth -> sqlalchemy",    # A3
  "greenhouse_server.web.context -> sqlalchemy",        # TYPE_CHECKING; A2 read_session removes it
  "greenhouse_server.services._session -> sqlalchemy",  # TYPE_CHECKING annotation
  "greenhouse_server.services.inventory -> sqlalchemy", # A8 — drop when the repo translates IntegrityError
]

[[tool.importlinter.contracts]]
name = "Persistence sits below domain behaviour"
type = "forbidden"
source_modules = ["greenhouse_core.repository", "greenhouse_core.database", "greenhouse_core.models", "greenhouse_core.auth"]
forbidden_modules = ["greenhouse_core.logic", "greenhouse_core.learning", "greenhouse_core.devices", "greenhouse_core.sync",
                     "greenhouse_core.plant_db", "greenhouse_core.stats", "greenhouse_core.utils", "greenhouse_core.schemas"]

[[tool.importlinter.contracts]]
name = "Only sync and server wiring touch devices"
type = "forbidden"
source_modules = ["greenhouse_core.learning", "greenhouse_core.stats", "greenhouse_core.plant_db", "greenhouse_core.utils",
                  "greenhouse_core.repository", "greenhouse_core.auth", "greenhouse_core.database"]
forbidden_modules = ["greenhouse_core.devices", "greenhouse_core.sync", "tinytuya"]

[[tool.importlinter.contracts]]
name = "Server config is a leaf"
type = "forbidden"
source_modules = ["greenhouse_server.config"]
forbidden_modules = ["greenhouse_core.repository", "greenhouse_core.models", "greenhouse_core.devices", "greenhouse_core.logic",
                     "greenhouse_core.learning", "greenhouse_core.sync", "sqlalchemy", "fastapi"]
```

Follow-ups once the fixes land:
- After A4, narrow `services.irrigation -> scheduler` to `services.irrigation_jobs -> scheduler`.
- After A3, drop the 2 auth ignores.
- After A8, drop the inventory ignore.
- Once WP8 is merged, add `greenhouse_core.sync` to the "SQLAlchemy stays behind the repository" sources (no sqlalchemy imports today).

Rejected on purpose: "logic/learning never import `repository`". It would break across 10 modules, and target §4 rejected the repository Protocols.

## Top 15 fixes (priority order)

| # | Fix | Sev | Behavior |
|---|---|---|---|
| 1 | A1: repo-less `DeviceHealthMonitor` (repo per call plus a cache lock); until then, add a REFACTOR_NOTES entry and a two-thread characterization test | H | labeled fix |
| 2 | Add the 10 verified import-linter contracts above | M | none |
| 3 | A3: `state.py` with a single `get_session`/`get_settings` (C-TX-4); auth stops opening a second session | M | labeled fix (one session per request) |
| 4 | A3: user CRUD moves into repository methods; auth routes use `RepoDep` and `repo.commit()` | M | none |
| 5 | A2: `job_session(commit=…)` plus `read_session(app)`, adopted by the 6 ad-hoc sites; `bootstrap_admin` uses the session factory | M | none |
| 6 | A2: rename `services/_session.py` to `services/jobs.py` | L | none |
| 7 | A4: move the job plumbing to `services/irrigation_jobs.py` with by-path re-exports, then narrow the ignore | M | none (logger-name golden decides) |
| 8 | A6: lazy `__getattr__` in the `greenhouse_core/__init__` (and `logic/__init__`) re-exports | M | import timing only |
| 9 | A7: one not-found idiom (typed `LookupError`s from services; per-route detail strings kept) | M | none |
| 10 | A5: split `utils` into `logic/light.py` and `display.py`, with a `utils` re-export shim | M | env-read golden |
| 11 | A8: repository translates `IntegrityError` → `DeviceIdExistsError`; drop sqlalchemy from services | L | none |
| 12 | A9: add the web kill-switch `maybe_notify` drift to `45-drift-track.md`; add a docstring on the `set_check_all_paused` order | L | (drift fix is labeled) |
| 13 | A10: module docstrings for `web/routes/analytics.py` and a feature map in `services/__init__.py` | L | none |
| 14 | A11: delete the 2 redundant function-level imports; tag every remaining lazy import `# lazy: <why>` | L | none |
| 15 | A12/A13: document or prune the `plant_db` singleton; add an `ALL_WEEKDAYS == FULL_WEEKDAY_MASK` guard test | L | none |

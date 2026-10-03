# 46 — Consistency audit (whole repo, end-state "one way to do each thing")

Auditor: consistency auditor (read-only on code). Base: integration branch `claude/focused-hawking-7to7o3` @ `3d1f43d`
(after WP6 + WP7 merges). Scope: `libs/**` (migrations excluded), `CLAUDE.md`, `README.md`, `plugin/**`.
Files under active WP8 refactor (`logic/engine.py`, `logic/timing.py`, `greenhouse_core/sync.py`, `devices/**`) were
audited too; every finding there is tagged **after WP8** and must be re-verified against the WP8 head before work starts.

Not duplicated here (cross-references only):
- **Drift pairs D1–D14** (`45-drift-track.md`). Where a finding overlaps a D-pair, only the *behavior-preserving*
  subset is listed here and the D-pair keeps the rest (notably D6: 404 wording; D7: relative time; D14: `export_csv`).
- **Rule-family lint work** (`40-lint-ratchet.md`): `PLR2004` magic numbers in comparisons, `D` docstring rules,
  `FAST002` (`Annotated` route params, which also normalizes `Query(24, …)` vs `Query(default=24, …)`), `BLE/TRY`
  report-only, `TC`, `ARG`, `RUF012`. Findings below either feed those queues with concrete targets or cover what
  no ruff rule catches (structure, vocabulary, layering, stale prose).

Evidence commands used (re-runnable): `grep -rn` per finding (shown in the Evidence column),
`uvx --offline vulture libs --min-confidence 60 --exclude '*/migrations/*'` (lead only),
`uv run ruff check libs --select D --config 'lint.pydocstyle.convention="google"' --ignore D105,D107 --statistics`,
`uv run mypy --strict <non-listed modules>`, an AST scan of route return annotations, a Typer command-tree dump.

Legend — **Impact**: `none` = behavior-preserving `refactor(...)`; `BEHAVIOR: …` = labeled `fix(consistency): …`
commit with reviewed test/golden updates. **Risk**: L/M/H (H = engine, devices, pipeline/scheduler, auth wiring →
two reviewers). **Area**: core / repo / services / pipeline / scheduler / app-auth / api / web / cli / tui / docs.

---

## 0. Owner decisions — RESOLVED 2026-10-03 (see refactor/BRIEF.md): OD1 keep read models + TypedDict; OD2 handler/service commit rule; OD3 REMOVE all legacy shims; OD4 unify on "stop"; OD5 drop refactor/, keep REFACTOR_NOTES.md + REFACTOR_REPORT.md

| # | Question | Why it blocks | Auditor default |
|---|---|---|---|
| OD1 | **Service results: Pydantic or not?** The seed target says "Pydantic only at the HTTP edge", but 7 services already return Pydantic response models (`services/{insights,efficacy,forecast,system_health,data_quality,search,charts}.py` import `greenhouse_core.schemas`) while 6 return untyped dicts that routes rebuild. | Converting the 7 to dataclasses + route mapping is pure churn; converting the dict ones to Pydantic moves validation into services. | Keep the 7 (their models are core-owned read models, also rendered by web); give the dict-returning services `TypedDict` results (zero runtime change). Amend the seed target accordingly. |
| OD2 | **Transaction owner.** Actuating services must commit mid-flow (event durable before notify/watcher; per-cluster isolation in `check_all_clusters`); CRUD commits in the handler. | "One owner per request" cannot be literal without behavior change. | Rule: *the handler commits CRUD; a service commits only when a side effect must follow a durable write; both through one API* (`repo.commit()`); jobs through one session helper. |
| OD3 | **Legacy data shims**: `health_monitor.migrate_legacy_pump_alerts` + `LEGACY_PUMP_DRY_RUN_CODE` (startup hook), `devices/registry.py:37-48` `LEGACY_*_ALIASES` (DB values), `database.py:35-90` pre-Alembic repair, `Settings.check_interval_hours` (deprecated env var, frozen contract). | Removal is a behavior change for old databases/deployments. | Keep all four; document each as a deliberate compat path (one-line "why kept" comment). Removing the pump-alert migration is safe only if the production DB has no open `pump_dry_run` alerts — owner can check. |
| OD4 | **Irrigation event action vocabulary**: manual stop records `action="off"` (`services/manual_control.py:179`), auto/watcher/emergency stops record `"stop"` (`services/irrigation.py:72`, `services/bulk.py:44`). | Unifying changes stored data, history/stats output and the `stop_irrigator` route docstring ("records an `off` irrigation event"). | Unify on `"stop"` as `fix(consistency)` (C-NAME-1) in the doc-contract stage, or keep and document. |
| OD5 | **End state of `refactor/`**: `Makefile` `typecheck` reads `refactor/mypy-strict.txt`, `sizecheck` runs `refactor/scripts/sizecheck.py`; `REFACTOR_NOTES.md` at root. | Build config referencing a work folder is a stale artifact once the refactor lands. | Final commit: all modules strict → `[tool.mypy] files = ["libs"]`, delete the list; move sizecheck to `scripts/`; move `REFACTOR_NOTES.md` bugs into issues, then delete `refactor/` (or keep as `docs/refactor/` archive). |

---

## 1. Findings

### 1.1 Transactions (68 commits in routes, 13 in services; 2 idioms in routes)

Counts: API routes 34 commit + 3 rollback in 11 files; web routes 34 + 1 in 12 files; services 9 + 4 in 4 files
(+2 `flush`); scheduler 5; app 2; auth 1. Within routes, 15 calls use `session.commit()` via `SessionDep` and 53 use
`repo.session.commit()` — same object (FastAPI caches `get_session` per request), two spellings.

| ID | File:line | Evidence | Proposed change | Impact | Risk | Area |
|---|---|---|---|---|---|---|
| C-TX-1 | `repository.py` (no commit API); callers `routes/*.py` (53× `repo.session.commit()`), `routes/operations.py:156,206,233,254`, `routes/alerts.py:90,110,142,164`, `web/routes/operations.py:50,59,75,86,100` (`session.commit()`), `routes/auth.py:88`, `web/routes/auth.py:87` | `grep -rn 'repo\.session\.commit()\|^\s+session\.commit()' routes web/routes` → 68 | Add `IrrigationRepository.commit()` / `rollback()` (thin delegates); replace both spellings; drop `SessionDep` params that exist only to commit (check_all, check_single, sync, monitor, alerts ack/resolve…). | none (same `Session` object via DI cache; OpenAPI unaffected — `Session` deps are not schema params; prove with OpenAPI + MCP goldens) | M | repo → api, web |
| C-TX-2 | `services/manual_control.py:132,183,231`, `services/bulk.py:53`, `services/pump_watcher.py:357,361`, `services/irrigation.py:206-208,345-347,931,950,963` | services reach through `repo.session` / a local `session` | Same `repo.commit()/rollback()` spelling; keep commit *placement* (OD2). Add a one-line "commits because …" docstring note where a service commits. | none | H (pipeline) for `irrigation.py`, M otherwise | services, pipeline |
| C-TX-3 | `services/irrigation.py:183-211` (`_run_pump_watcher`), `:323-350` (`_run_leak_check`), `:423-431` (`rearm_leak_checks`); `scheduler.py:313-330` (`_job_session`), `:446-462` (`init_health_monitor`); `app.py:320-354` (2×) | 7 hand-written `factory()` / try / commit / rollback / close blocks; `_job_session` already exists but lives in `scheduler` (services may not import it: layer contract) | Move `_job_session` to a module below services (e.g. `greenhouse_server/services/_session.py`, logger passed in so record names stay per-module); adopt where the shape is identical: `_run_leak_check` (exact match). Leave `_run_pump_watcher` (conditional commit), `rearm_leak_checks` (no commit), `app.py` (silent fallback) with a "why different" comment. | none for the exact-match adoption | H (scheduler/pipeline; `_job_session` was already corrected once in WP7) | pipeline, scheduler |
| C-TX-4 | `auth.py:143-149` `_session_from_app` vs `deps.py:23-29` `get_session`; `auth.py:139` vs `app.py:99` `_get_settings` | duplicate dependency callables (forced by the `deps \| auth` sibling layer) | Move `get_session` and `get_settings` to one module below both (e.g. `greenhouse_server/state.py`), import from deps, auth and app. | **BEHAVIOR:** FastAPI caches dependencies by callable identity, so today an authenticated request opens **two** sessions (auth lookup + handler); after unification they share one session/identity map. `_get_settings` dedupe alone is `none`. | M (auth wiring → 2 reviewers) | app-auth |
| C-TX-5 | `web/context.py:31-71` (`_repo_from_request`, `_preference_flags`) | every template render opens a third, private session to read preferences/vacation | Document as the deliberate exception (reads committed state, never writes) — or pass the request repo. | none if documented; **BEHAVIOR** (sees uncommitted state) if switched | L | web |
| C-TX-6 | `routes/operations.py:160-184` (`monitor`) vs `web/routes/operations.py:56-62` | API `monitor_cluster(cluster_id)` runs `ensure_fresh_and_read` (Cloud sync of stale sensors + `flush`) and **never commits**; web passes `no_sync=True` and commits a clean session | **New observed bug** (see §2 B-N1); pair for the drift track (proposed D15). | BEHAVIOR (API persists synced rows / web syncs) | M | api, web |

### 1.2 Entity lookups / 404 (65 inline 404 raises; 1 shared helper)

`deps.py:79-86` defines only `require_cluster` (untyped return, local import). Web already grew private equivalents:
`web/routes/sensors.py:15` `_get_sensor_in_cluster`, `web/routes/plants.py:53` `_get_plant_in_cluster`,
`web/routes/irrigators.py:19` `_require_cluster_irrigator`, `web/routes/irrigators.py:173` `_get_irrigator_or_404` —
four naming styles for one idea. Inline messages: "Cluster not found" 20, "Plant not found" 8, "Sensor not found in
cluster" 4, "Irrigator not found" 4, "Cluster has no irrigator" 4, "Plant not found in cluster" 3, "Alert not found" 3,
vacation/window variants 6 (punctuation differs → D6).

| ID | File:line | Proposed change | Impact | Risk | Area |
|---|---|---|---|---|---|
| C-404-1 | `routes/clusters.py:133-134`, `routes/alerts.py:137-139`, `web/routes/clusters.py:61-63` (direct `repo.get_cluster` + inline raise) | use `require_cluster` | none (same detail string, `Cluster` rows are always truthy) | L | api, web |
| C-404-2 | API `routes/irrigators.py:213,240,267` + web `irrigators.py:173-177` | `deps.require_irrigator` | none (identical "Irrigator not found") | L | api, web |
| C-404-3 | API `routes/irrigators.py:127,155,182` + web `irrigators.py:19-23` | `deps.require_cluster_irrigator` | none ("Cluster has no irrigator") | L | api, web |
| C-404-4 | API `routes/sensors.py:115,141,199` + web `sensors.py:15-19` | `deps.require_sensor_in_cluster` | none | L | api, web |
| C-404-5 | API `routes/plants.py:129,155` + web `plants.py:53-57` | `deps.require_plant_in_cluster` | none | L | api, web |
| C-404-6 | `routes/plants.py:188,254`, `routes/charts.py:39,69,178`, `web/routes/plant_dashboard.py:38,159,177` | `deps.require_plant` (status-code spelling `404` vs `status.HTTP_404_NOT_FOUND` also unified) | none | L | api, web |
| C-404-7 | `routes/alerts.py:68,89,109` | `deps.require_alert` | none (web ack/resolve missing-404 is a recorded bug → stays with D6) | L | api |
| C-404-8 | `deps.py:79` | annotate `-> Cluster`, hoist the import | none | L | api |
| C-404-9 | "None-as-not-found" from services: `routes/operations.py:59-61,301-303`, `routes/insights.py:33-35`, `routes/charts.py:94-96,122-124,150-152`, web `clusters.py:156,223,239,260,277,294`, `analytics.py:50-52`; string sentinel `routes/operations.py:154` matching `_error_result("cluster not found")` (`services/irrigation.py:782`) | Four error-signalling idioms coexist: `None`, `bool` (delete/update), string sentinel in a result dict, domain exception (`PlantNotFoundError`, `IrrigatorExistsError`, `JobNotRegisteredError`, `ManualActionError(status_code, …)` — the last carries HTTP codes inside a service). Target: services raise one `NotFoundError(entity)` family; one exception handler maps it; routes stop string-matching. | none if the mapped detail strings stay byte-identical (golden-proven); do it after D6 fixes the wording | M | services, api, web |

### 1.3 Service return types

| ID | File:line | Evidence | Proposed change | Impact | Risk | Area |
|---|---|---|---|---|---|---|
| C-RES-1 | `services/cluster.py:21,72,98,119,138` (`decision_to_view`, `get_cluster_status`, `get_cluster_history` → `dict[str, Any]`) | routes rebuild field by field (`routes/operations.py:285-318`) | `TypedDict` results (templates use `x["key"]`, so keep dicts at runtime) | none | L | services |
| C-RES-2 | `services/sync.py:41,47,78` (`-> dict`), core `sync.py:21` (`-> dict`) **after WP8** | `routes/operations.py:250-260` rebuilds `SyncResponse` | `SyncStats` TypedDict shared by core and service | none | M | services / core-WP8 |
| C-RES-3 | `stats.py:28,60` `-> dict[str, Any]` with `defaultdict` values | `routes/operations.py:341` `StatsResponse(**result)` | `IrrigationStats` TypedDict | none | L | core |
| C-RES-4 | `services/weather.py:25,53` `-> dict \| None`; `scheduler.py:477` `get_jobs -> list[dict]` | `routes/scheduler.py:29,44` `SchedulerJobResponse(**j)` | TypedDicts (`WeatherNow`, `ForecastWindow`, `JobInfo`) | none | L (weather) / H-review (scheduler) | services, scheduler |
| C-RES-5 | `services/irrigation.py:437-470` TypedDicts exist but routes still need `# type: ignore[misc, arg-type]` (`routes/operations.py:157,208,234`) and `learn` (`:281`) | contract comment "TypedDict → Pydantic" | `IrrigateResponse.model_validate(result)` instead of `**result` drops the ignores | none (same validation) | M (pipeline output) | api |
| C-RES-6 | `routes/vacation.py:26-27`, `routes/plants.py:262` `# type: ignore[arg-type]` (ORM rows into Pydantic fields) | ORM→schema coercion hidden by ignores | `Model.model_validate(row)` / `from_attributes` | none | L | api |
| C-RES-7 | `routes/charts.py:67,94`, `web/routes/clusters.py:110,260` `# type: ignore[arg-type]` on `metric` | `metric: str` passed where `Metric` Literal is expected | type `CLUSTER_METRICS` as `tuple[Metric, ...]` and narrow the query value with a validated `cast` helper (route param type stays `str` — OpenAPI frozen) | none | L | api, web |
| C-RES-8 | `services/bulk.py:12`, `services/cluster.py:219` `-> tuple[int, list[str]]` | anonymous tuples | `NamedTuple` (`StopAllResult`, `PlantSyncResult`) — still unpacks positionally | none | L | services |
| C-RES-9 | devices `read_live/status/get_live_reading -> dict` (`devices/sensors/base.py:25`, `irrigators/base.py:39`, `gateway.py:141,256`) **after WP8** | untyped adapter payloads | TypedDicts | none | H | devices |

### 1.4 Repository bypass (12 `repo.session` uses outside the repository + 1 legacy query)

| ID | File:line | Proposed change | Impact | Risk | Area |
|---|---|---|---|---|---|
| C-REPO-1 | `services/search.py:43,69,95,121` (`repo.session.scalars(select(...))`) | `repo.search_{clusters,plants,sensors,irrigators}(q, limit)` | none (move query verbatim) | L | repo, services |
| C-REPO-2 | `services/efficacy.py:47`, `services/charts.py:117,347` | repository methods (`list_start_events_since`, `get_sensors_by_ids`) | none | L | repo, services |
| C-REPO-3 | `services/health_monitor.py:268,294,366` (open-alert statements) | `repo.get_open_alert_by_key`, `repo.list_open_alerts_by_code` | none | M (health gate) | repo, services |
| C-REPO-4 | `services/sync.py:74`, `services/cluster.py:231` (`self._repo.session.flush()`) | `repo.flush()` | none | L | services |
| C-REPO-5 | `auth.py:330` `session.query(User).first()` (SQLAlchemy 1.x legacy API; only one in repo) | `session.scalar(select(User).limit(1))` | none | M (auth) | app-auth |
| C-REPO-6 | `routes/irrigators.py:4,102`, `routes/sensors.py:4,81` import `sqlalchemy.exc.IntegrityError` in routes | translate in repository (`DuplicateDeviceError`) like `IrrigatorExistsError` | none (same 409 detail) — the web half is D1/D2 | L | repo, api |

### 1.5 Clock / env access

60 clock reads; idiom split: `int(time.time())` 57×, `int(_time.time())` 3× (`services/irrigation.py:5` aliases
`import time as _time`), `datetime.now(tz=UTC)` 2× (`services/health.py:179`, `utils.py:25`), `time.strftime`
1× (`web/context.py:90`, local tz — recorded bug), `time.monotonic` 4× (weather cache; correct for TTL). Two classes
inject a clock (`health_monitor.py:121`, `pump_watcher.py:120`), `auth.issue_token(now=)` a third way.

| ID | File:line | Proposed change | Impact | Risk | Area |
|---|---|---|---|---|---|
| C-CLK-1 | whole repo | **Do not add a clock helper (YAGNI).** Declare the idiom `int(time.time())` read at call time; tests freeze via `time_machine` and patch `time.time`. | none | — | docs (CLAUDE.md "Conventions") |
| C-CLK-2 | `services/irrigation.py:5` `import time as _time` | **Blocked:** `tests/server/test_contract_wp7_gaps.py:299` and `tests/server/test_leak_rearm.py:184` patch `irrigation_mod._time`; rule 8 forbids editing them. Keep, add a one-line "patched by pinned tests" comment. | none | — | pipeline |
| C-CLK-3 | `services/weather.py` imports `time` only for `monotonic` | keep — `tests/server/test_contract_weather.py:51` patches `weather_module.time` | none | — | services |
| C-ENV-1 | `cli/commands/_helpers.py:16`, `commands/auth.py:26`, `commands/tui.py:35` — three copies of `ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", "http://localhost:8000")`; default URL also in `client.py:87`, `main.py:34` help | one `server_url(ctx)` in `_helpers.py` + `DEFAULT_SERVER_URL` constant | none | L | cli |
| C-ENV-2 | `plant_db.py:9-13` reads `IRRIGATION_PLANT_DB_PATH` at **import time**; `Settings.plant_db_path` (`config.py:26`) reads the same var and `app.py:357-362` passes it | two readers of one env var | keep `PLANT_DB_PATH` (pinned public name) but document it as the CLI/test fallback; server path goes through Settings only (already true) | none | L | core |
| C-ENV-3 | `devices/gateway.py:116-118` reads `TUYA_*` env in the constructor **after WP8** | edge read inside core | `app._init_tuya` reads env and passes explicit args; constructor keeps the env fallback for compatibility *(Settings schema is frozen, so no new Settings fields)* | none | H (devices) | app, devices |
| C-ENV-4 | `utils.py:80` `os.getenv("IRRIGATION_TZ")` fallback | documented fallback; leave | none | — | core |

### 1.6 Logging

All 52 log calls already use %-style lazy args (0 f-string log calls, 0 `.format`). Remaining inconsistencies:

| ID | File:line | Proposed change | Impact | Risk | Area |
|---|---|---|---|---|---|
| C-LOG-1 | variable named `log` in `services/notify.py:17`, `database.py:16`, `logic/engine.py:95` (**after WP8**) vs `logger` in 12 modules | rename to `logger` (logger *names* unchanged; no test patches `.log`) | none | L (engine part H-review) | services, core |
| C-LOG-2 | `sync.py:55` `logger.error("  %s: %s", …)` inside a per-sensor `except` — CLI-print indentation and ERROR for a handled per-sensor failure **after WP8** | `logger.warning("Sync failed for sensor %s: %s", …)` | BEHAVIOR (log text + level) | L | core-WP8 |
| C-LOG-3 | silent swallows with no log at all: `services/weather.py:50,96`, `services/maintenance.py:32`, `web/context.py:40,65`, `app.py:332,353`, `sync.py:112`, `devices/irrigators/tuya_generic.py:76`, `ik10pw.py:173,186,194`, `utils.py:99`, `logic/plant_needs.py:37` | add `logger.debug(..., exc_info=True)` (pattern already used in `services/sync.py:72`, `notify.py:63`) | BEHAVIOR (debug-level log records only) | L | various |
| C-LOG-4 | `logger.exception` (ERROR) for job failures vs `log.warning(..., exc_info=True)` for advisory failures (engine) | keep; document the rule: *job/actuation failure → `exception`; advisory/best-effort → `warning`/`debug` + `exc_info`* | none | — | docs |

### 1.7 Error handling (52 `except Exception`, 13 tuple excepts; report-only per 40-lint-ratchet)

Inventory (each *narrowing* changes what is caught → BEHAVIOR; listed for the BLE/TRY report, not for auto-fix):

| ID | Sites | Category | Proposed change | Impact |
|---|---|---|---|---|
| C-ERR-1 | `app.py:77` `except (ValueError, Exception):` | redundant tuple | `except Exception:` | **none** (ValueError ⊂ Exception) |
| C-ERR-2 | `services/irrigation.py:62,152`, `services/bulk.py:50` carry `# noqa: BLE001`; the other 49 broad excepts do not, and `BLE` is not selected (`pyproject.toml` `[tool.ruff.lint]`) | inconsistent suppression | when BLE is enabled report-only (40 §6), add `# noqa: BLE001 — <why>` to every *intentional* one in the same commit; until then drop nothing | none |
| C-ERR-3 | job/actuation boundaries that must not crash a scheduler thread: `irrigation.py:207,346,428,932`, `scheduler.py:327,456,574`, `health_monitor.py:172,177,360,371`, `pump_watcher.py:295,310,329,347,358` | intentional, logged | keep; annotate | none |
| C-ERR-4 | silent `pass`/`return default`: `pump_watcher.py:362`, `web/context.py:65`, `app.py:353`, `sync.py:112`, `tuya_generic.py:76`, `ik10pw.py:173,186,194` | swallow, no log | add debug log (C-LOG-3); narrowing = BEHAVIOR, report-only | BEHAVIOR if narrowed |
| C-ERR-5 | value parsing guarded by `Exception`: `irrigation.py:545`, `logic/plant_needs.py:37`, `utils.py:99`, `maintenance.py:32` | over-broad parse guard | narrow to `(ValueError, TypeError, …)` | BEHAVIOR (other errors now propagate) — report-only |
| C-ERR-6 | errors surfaced as strings: `services/cluster.py:217`, `bulk.py:50`, `sync.py:53`, `tuya_generic.py:36` (`{"error": str(e)}`), `ik10pw.py:115`, `routes/scheduler.py:85` (500 with `str(exc)`) | stringly results | keep (frozen outputs); record | none |
| C-ERR-7 | CLI/TUI: `client.py:105`, `tui/widgets.py:346` | best-effort decode / cursor restore | keep | none |
| C-ERR-8 | `services/manual_control.py:24-36` `ManualActionError(status_code, detail)` — HTTP status inside a service | see C-404-9 | domain error subclasses mapped at the edge | none (same status/detail) |

### 1.8 Constants (literals the PLR2004 queue does not catch)

| ID | File:line | Proposed change | Impact | Risk | Area |
|---|---|---|---|---|---|
| C-CONST-1 | `SECONDS_PER_DAY`/`SECONDS_PER_HOUR` exist (`constants.py:260-261`, WP0 "definitions only") but literals remain: `repository.py:418,428,485,924`, `stats.py:62,147`, `learning/profiling.py:13,152,184`, `services/charts.py:106,159,261,340,381`, `services/efficacy.py:39`, `services/leak.py:127`, `services/data_quality.py:15`, `services/system_health.py:15-17`, `services/vacation.py:17` (duplicate `_SECONDS_PER_DAY`), `web/filters.py:19`; **after WP8**: `logic/engine.py:407,424,425,499,504,537`, `sync.py:70`, `devices/gateway.py:199` | adopt the constants | none | L (engine H) | core, services, web |
| C-CONST-2 | `FULL_WEEKDAY_MASK` exists (`constants.py:362`) but `schemas.py:235` (`weekday_mask: int = 127`) and `models.py:364` (`default=127`) use the literal | adopt (OpenAPI default stays `127`; Python-side default, DDL unchanged) | none | L | core |
| C-CONST-3 | CLI/TUI copies of 127 (`client.py:511`, `commands/windows.py:37,40,65`, `tui/resources.py:91`, `tui/formatting.py:133`, `tui/screens/cluster.py:419`) — CLI may not import core | one CLI-side `ALL_WEEKDAYS = 127` (e.g. in `client.py`, which both CLI and TUI import) | none (help text literal stays) | L | cli, tui |
| C-CONST-4 | `Settings.weather_lat/lon = 45.464 / 9.189` (`config.py:29-30`) duplicate the otherwise-dead `DEFAULT_LATITUDE/LONGITUDE` (`constants.py:215-216`) | Settings defaults reference the constants (revives them instead of deleting) | none (settings golden: same values) | L | app |
| C-CONST-5 | service thresholds as private module constants — CLAUDE.md invariant 5 says thresholds live in `constants.py`: `services/anomaly.py:30-36` (z-score, window, min std, stale multiplier), `services/efficacy.py:18-19` (before/after windows; `_BEFORE_SECONDS=1800` duplicates `learning/profiling.py:13 PRE_WINDOW_SEC` and `repository.py:428` defaults), `services/system_health.py:15-18` (`_STALE_SECONDS = 3h` == `MAINTENANCE_STALE_SECONDS`), `services/data_quality.py:15` (24h), `web/filters.py:19` (7d), `services/weather.py:7` (TTL) | move to `constants.py` under distinct names (five different "stale" thresholds exist — 30 min, 3 h, 4 h, 24 h, 7 d — name each by purpose) | none (constants golden is a superset; values unchanged) | L | core, services |
| C-CONST-6 | activity/alert vocabularies half-adopted: `ENTITY_*` (`models.py:15-19`), `SOURCE_*` split across `services/alerts.py:26-32` and `health_monitor.py:59`; literals bypass them: `services/leak.py:116` `source="leak"` (constant exists), `services/data_quality.py:57,73,94,106,126,137`, `services/search.py:56,82,108,134`, `services/irrigation.py:959` (`entity_type="…"`), `services/irrigation.py:144,659,712,747,756,821` (`source="irrigation"`, no constant), `repository.py:252,1259` | one vocabulary block in core (next to `ENTITY_*`): `SOURCE_*`, `SEVERITY_*`, `EVENT_ACTION_*` (`"start"` is repeated in 9 readers: `engine.py:529`, `trends.py:88`, `profiling.py:154`, `repository.py:508`, `efficacy.py:51`, `manual_control.py:62,71`, `irrigation.py:394`, `_cluster_card.html:84`), `TRIGGERED_BY_*`; `pump_watcher.py:56 EVENT_ACTION_ABORTED` moves there | none | L–M | core, services |
| C-CONST-7 | `tui/app.py:86` `401`, `client.py:102` `400`, `web/routes/irrigators.py:190` `503` | `http.HTTPStatus` members | none | L | cli, tui, web |
| C-CONST-8 | route bound `le=8760` repeated (`routes/charts.py:48,78`, `web/routes/clusters.py:154,256`, `web/routes/plant_dashboard.py:50,152`) | `MAX_LOOKBACK_HOURS` module constant | none (OpenAPI equal) | L | api, web |

### 1.9 Naming (one vocabulary per concept)

| ID | Where | Evidence | Proposed change | Impact | Risk | Area |
|---|---|---|---|---|---|---|
| C-NAME-1 | event action stop | `"off"` (`manual_control.py:179`) vs `"stop"` (`irrigation.py:72`, `bulk.py:44`) | OD4 | BEHAVIOR (stored data, stats `events_by_type`, history, route docstring) | M | services |
| C-NAME-2 | `DeviceGateway` called `cloud` (`scheduler.py:302 _get_cloud`, `:338,363,391`, `services/sync.py:35-45`, core `sync.py:21,60` **after WP8**) but `gateway` in `deps.py:51`, `devices/**`, `app.state.device_gateway` | legacy `TuyaCloud` name | rename internals to `gateway` (`_get_gateway`, `self._gateway`); public `sync_sensor_data(db, cloud, …)` parameter names are pinned → keep there or owner-approve | none (private); public params frozen | M | scheduler, services |
| C-NAME-3 | repository parameter `db` in core (`logic/*`, `learning/*`, `stats.py`, `sync.py` — 25 signatures) vs `repo` in server (66) | split by package | keep public core signatures (keyword callers in tests); new/private code uses `repo`; document | none | — | docs |
| C-NAME-4 | repository verbs: collections via `get_*` (`get_plants_in_cluster:131`, `get_sensors_in_cluster:307`, `get_recent_*`) and `list_*` (`list_irrigation_windows:988`, `list_all_*`), noun-style `readings_for_plant:282`, `irrigator_consumption_liters:494`, `sensor_assignments_for_plant:262` | 4 verb styles | **Blocked** for existing names (tests call them; renames need aliases = new shims). Rule for new methods: `get_` = one row, `list_` = collection. | none | — | repo |
| C-NAME-5 | 404 helper names: `require_cluster`, `_require_cluster_irrigator`, `_get_irrigator_or_404`, `_get_sensor_in_cluster`, `_get_plant_in_cluster` | 4 styles | all `deps.require_*` (C-404-*) | none | L | api, web |
| C-NAME-6 | device adapter constructor order: `TR301Z(gateway, profile)` (`sensors/tr301z.py:57`), `IK10PW(gateway, profile)` (`irrigators/ik10pw.py:98`) vs `TuyaGeneric*(profile, gateway)` (`sensors/tuya_generic.py:28`, `irrigators/tuya_generic.py:27`) **after WP8** | inconsistent positional order | make both keyword-only `(*, gateway, profile=None)`; update `devices/__init__.py:36` registry factory + call sites in one commit | none | H | devices |
| C-NAME-7 | CLI verbs (frozen command tree): `irrigator show` vs `cluster get`/`alerts get`; plural sub-apps `alerts/decisions/windows` vs singular `cluster/plant/irrigator/sensor/vacation` | record only (CLI tree frozen) | none | — | cli |
| C-NAME-8 | `deps.get_health_monitor` local var `factory` holds the monitor (`deps.py:49`) | rename `monitor` | none | L | api |

### 1.10 Docstrings (373 findings, Google convention; 0 reST/numpy — style is already uniform)

`ruff --select D` (google, ignoring D105/D107): D102 99, D103 96, D101 73, D417 69, D205 18, D209 8, D104 4, D212 4,
D106 1, D202 1. Top modules: `schemas.py` 71 (OpenAPI-visible → doc-contract commits), `tui/screens/cluster.py` 21,
`routes/operations.py` 11, `tui/widgets.py` 11, `tui/screens/modals.py` 11, `web/routes/irrigators.py` 10,
`web/routes/clusters.py` 9, `deps.py` 9, `tui/screens/settings.py` 9. D417 (undocumented param): 66 of 69 are in
`routes/*` — CLAUDE.md invariant 7 requires `Args:`; those are MCP descriptions → doc-contract stage only. This is the
40-lint `D` queue; no separate tasks here beyond the stale-docstring items in §1.12.

### 1.11 Typing

| ID | Item | Proposed change | Impact | Risk |
|---|---|---|---|---|
| C-TYPE-1 | **29 modules are already mypy-strict clean but not listed** in `refactor/mypy-strict.txt`: `cli/commands/{alerts,clusters,configs,decisions,irrigators,operations,plants,preferences,scheduler,sensors,vacation,windows}.py`, `cli/main.py`, `server/routes/{irrigators,preferences,scheduler,search,sensors}.py`, `server/web/routes/{activity,alerts,auth,decisions,efficacy,fragments,health_page,pages,preferences,quality,sensors}.py` | append (list-only commit) | none | L |
| C-TYPE-2 | 36 modules with strict errors (top: `devices/gateway.py` 28, `plant_db.py` 17, `logic/engine.py` 11 *(WP8 T8.0)*, `tui/resources.py` 8, `services/manual_control.py` 6, `sync.py` 6 *(WP8 T8.16)*, `services/sync.py` 5, `web/filters.py` 4, `services/weather.py` 4; 27 more with 1–3) | one "add type annotations" commit per module group, append to list | none | L–H by area |
| C-TYPE-3 | 55/86 API route handlers and 83/85 web handlers lack a return annotation (framework-boundary override in `pyproject.toml [[tool.mypy.overrides]]`) | add `-> XResponse` where `response_model=XResponse` (response_model wins) and `-> Response`/`HTMLResponse` on web handlers; prove OpenAPI/MCP goldens unchanged; then drop the override for `web.routes.*` | none (golden-proven) | M |
| C-TYPE-4 | 14 `type: ignore` in `routes/*`/`web/*` | removed by C-RES-5/6/7 | none | L |

### 1.12 Stale artifacts (no TODO/FIXME/XXX anywhere; `ERA` clean)

| ID | File:line | Evidence | Proposed change | Impact | Area |
|---|---|---|---|---|---|
| C-STALE-1 | `utils.py:1` `#!/usr/bin/env python3` in a library module (only shebang in `libs/`) | remove | none | core |
| C-STALE-2 | `deps.py:40-50` docstring says the monitor "is constructed per-request" — it returns the app singleton | fix docstring | none | api |
| C-STALE-3 | `services/health_monitor.py:61,180` "original PumpWatcher" / "shared with PumpWatcher" — class is `PumpWatcherService` | fix comments | none | services |
| C-STALE-4 | `cli/commands/windows.py:22` help: "Empty list = global defaults apply." and `plugin/.../CLI.md:115` — engine: *no windows = all hours allowed* (`logic/engine.py:296-304`, CLAUDE.md inv. 9, issue #83) | fix both (Typer help → doc-contract commit regenerating the CLI-help golden) | none (help text) | cli, docs |
| C-STALE-5 | `plugin/.../PLANT_DATABASE.md:87` "Local-hour window the engine prefers to water in" — advisory only since #83 | fix | none | docs |
| C-STALE-6 | `plugin/.../CLI.md:102` "only `cluster` also has a single-item `get`" — `alerts get` and `irrigator show` exist | fix | none | docs |
| C-STALE-7 | `CLAUDE.md` "Packages" — CLI sub-apps listed as `cluster, plant, irrigator, sensor, config`; real tree adds `scheduler, alerts, decisions, prefs, vacation, windows`; services list names 6 of 22 modules; Tests list omits `tests/devices/` | fix | none | docs |
| C-STALE-8 | `CLAUDE.md` Development block: `make check` described as "pre-commit + coverage gate" (Makefile: `pre-commit-run lint-imports typecheck sizecheck coverage`); `typecheck`, `lint-imports`, `sizecheck` targets missing. `README.md:140` "make check # lint + test" | fix (after OD5) | none | docs |
| C-STALE-9 | `CLAUDE.md` invariant 6 "Persisted to `decision_logs` for **every** evaluation" — false for the device-health block (B-6, pinned) | fix the bug (separate PR) or qualify the sentence | none (doc) | docs |
| C-STALE-10 | `tui/resources.py:66` irrigator config placeholder `{"ip": "…", "version": "3.5"}` — code reads `device_ip` / `local_key` (`devices/gateway.py:277,322`, CLI `irrigators.py:31`, web `irrigators.py:85`); `version` is never read (protocol comes from the profile) | placeholder `{"device_ip": "…", "local_key": "…"}` | BEHAVIOR (TUI form hint; regenerate the affected TUI render golden if it captures placeholders) | tui |
| C-STALE-11 | `cli/main.py:38` `ctx.ensure_object(dict)` immediately overwritten by `ctx.obj = server` | remove the dead statement | none | cli |
| C-STALE-12 | `routes/clusters.py:99` `expand` accepted and ignored (frozen param) | keep; comment is accurate | none | api |

### 1.13 Legacy aliases / compat shims

| ID | File:line | Status |
|---|---|---|
| C-LEG-1 | `health_monitor.py:61-64,278-300` + `scheduler.py:451-453` | OD3 (keep by default) |
| C-LEG-2 | `devices/registry.py:11-13,37-48,107,111` legacy `tuya_cloud`/`tuya_local` aliases **after WP8** | OD3 (DB values; keep) |
| C-LEG-3 | `config.py:43-48,76-92`, `scheduler.py:137-160` deprecated `IRRIGATION_CHECK_INTERVAL_HOURS` | frozen env contract; keep |
| C-LEG-4 | `database.py:35-90` pre-Alembic repair | OD3 (keep) |
| C-LEG-5 | `devices/__init__.py:15-22` re-export of `tinytuya` "for tests that patch" | keep (tests patch it) |
| C-LEG-6 | `devices/gateway.py:241-245` `DeviceGateway.group_logs_by_timestamp` staticmethod delegating to the module function **after WP8** | keep — `core sync.py:74` calls it on the instance and the fakes in `tests/server/test_contract_sync_service.py:38` / `test_contract_pipeline.py:87` record that call |
| C-LEG-7 | `pump_watcher.py:52-55` `ALERT_CODE` alias "so tests can keep importing" | keep (used by `tests/server/test_pump_watcher.py`) — or rule-4 removal with those test imports switched to `HealthAlarm.NO_WATER.value` (test edit → owner) |
| C-LEG-8 | `web/routes/{irrigators.py:39,configs.py:15,sensors.py:34,plants.py:62}` "Legacy URL" 301 redirects | never dead (web handlers); keep |
| C-LEG-9 | `utils.py:10-13` `_SEASONAL_LIGHT_FACTOR` alias of `SEASONAL_LIGHT_FACTOR_BY_MONTH` "NIGHT_LUX_THRESHOLD stays importable from here" | keep the re-export (pinned import surface); the private alias can go | none |

### 1.14 Dead code (BRIEF rules; vulture = lead, grep = proof across `libs/ tests/ plugin/`, templates, `app.tcss`)

| ID | Name / File:line | Evidence (`grep -rnw <name> libs tests plugin`) | Rule | Action | Impact |
|---|---|---|---|---|---|
| C-DEAD-1 | `stats.print_stats_report` + `_print_counts` (`stats.py:84-117`) | libs: def only; tests: `test_contract_stats.py:109-155` (own char tests) + `imports.json:467` | 4 | remove + its 3 tests + golden name (removal-only diff) | none |
| C-DEAD-2 | `stats.format_duration` (`stats.py:19`) | only users: `print_stats_report`, `export_csv` (D14), tests | 4 | remove after C-DEAD-1 + D14 | none |
| C-DEAD-3 | `StressIndicators.any_critical` (`logic/decision.py:173`) | 0 refs in libs/tests (mutation campaign found it) | 1 | remove | none |
| C-DEAD-4 | `IrrigationRepository.sensor_assignments_for_plant` (`repository.py:262`) | 0 refs | 1 | remove | none |
| C-DEAD-5 | `PlantDatabase.get_light_needs_info` (`plant_db.py:141`) | 0 refs | 1 | remove | none |
| C-DEAD-6 | `SOURCE_DECISION`, `SOURCE_SYSTEM` (`services/alerts.py:28,31`) | 0 refs; no `"decision"`/`"system"` source literal written anywhere | 1 | remove (or fold into C-CONST-6 block as documented values — pick one) | none |
| C-DEAD-7 | `ENTITY_SYSTEM` (`models.py:19`) | only `imports.json:273` | 4 | remove + golden name | none |
| C-DEAD-8 | `DEFAULT_QUIET_START_HOUR/END_HOUR` (`constants.py:20-21`) | only goldens (`imports.json:56`, `constants.json:38`); live default is the migration literal (REFACTOR_NOTES) | 4 | remove + golden names (owner OK: constants golden) | none |
| C-DEAD-9 | `logic/timing.is_within_preferred_hours` (`timing.py:84`) **after WP8** | libs: def only; tests `test_timing.py:10-36` (own tests); leftover from #83 | 4 | remove + tests | none |
| C-DEAD-10 | `DeviceGateway.invalidate_key` (`gateway.py:303`) **after WP8** | libs: def only; `tests/devices/test_contract_adapters.py:601-602` (own char test) | 4 | remove + test, or wire it into the stale-key path (BEHAVIOR) | none |
| C-DEAD-11 | `#global-config` rule (`tui/app.tcss:133`) | no widget id `global-config` in `tui/**/*.py` (recorded in REFACTOR_NOTES) | 1 | remove | none (renders unchanged) |
| C-DEAD-12 | `CONFIDENCE_BASELINE` (`constants.py:266`) | 0 refs; WP0 definition awaiting WP8 T8.1 | — | re-check after WP8: adopted → done; else remove | none |
| — | **Not dead** (keep): `TriggerCode.DAILY_CAP_HIT/LEARNING_ALERT` (frozen enum, OpenAPI), `head_revision` (test utility API), `registered_*_keys` (drive parametrized contract tests), `get_plant_database/reset_plant_database/set_plant_database` (singleton API used by tests), `bulk_add_sensor_readings` (test fixtures), `AuthUserDep`/`DeviceGatewayDep` (pinned public aliases), `is_system` (asserted by `test_contract_auth.py:399`), Typer/Textual/route handlers flagged by vulture. |

### 1.15 Duplicated helpers

| ID | Copies | Proposed change | Impact | Risk | Area |
|---|---|---|---|---|---|
| C-DUP-1 | server URL resolution ×3 (C-ENV-1) | one helper | none | L | cli |
| C-DUP-2 | irrigator `config` JSON parsing ×3 with different leniency: `schemas.py:9` (raises on bad JSON), `web/routes/irrigators.py:26` (`{}` on bad JSON, passes non-dict JSON through), `devices/gateway.py:57` (`{}` for bad *and* non-dict JSON) | one `parse_irrigator_config` in core (models/property) | BEHAVIOR for malformed stored configs only (web vs gateway differ for non-dict JSON) → propose drift pair **D16** | M | core, web, devices |
| C-DUP-3 | web form parsers spread over 7 route modules (`configs.py:21,32,46`, `plants.py:21`, `irrigators.py:55`, `sensors.py:22`, `vacation.py:25`, `windows.py:29`) plus inline `int(x) if x.strip() else None` (`configs.py:79-80,113-114`, `operations.py:41,112-113`) and two truthy-form vocabularies (`operations.py:42` vs `configs.py:51-53`) | `web/forms.py` with `opt_int/opt_float/tri_bool/is_checked`; preserve each site's current error behavior (the bare `int()` 500s are recorded bugs → separate fix) | none (moves only) | L | web |
| C-DUP-4 | relative-time ×3: `web/filters.py:30`, `web/routes/plant_dashboard.py:131` (D7), `tui/formatting.py:51-73` (CLI may not import server) | D7 covers the server pair; TUI copy stays (package boundary) | — | — | — |
| C-DUP-5 | `getattr(app.state, "<x>", None)` ×18 (`ntfy_notifier` 5, `device_registry` 5, `health_monitor` 4, `device_gateway` 2, `settings` 2) across scheduler/services/deps | small typed accessors in one module (`state.py`, see C-TX-4) | none | M (scheduler) | scheduler, services |
| C-DUP-6 | `routes/operations.py:205` vs `web/routes/operations.py:85` `has_alerts` | D4 | — | — | — |

### 1.16 Other inconsistencies

| ID | File:line | Finding | Proposed change | Impact |
|---|---|---|---|---|
| C-MISC-1 | `routes/efficacy.py:16` `days: Query(default=14, ge=1)` vs `web/routes/efficacy.py:20` `le=365` | same param, different bounds | drift pair **D17** (API side is laxer; default rule "stricter wins" → API gains `le=365`) | BEHAVIOR (API/MCP 422 above 365; OpenAPI) |
| C-MISC-2 | `routes/operations.py:288-289` / `web/routes/analytics.py:47-48` history `hours`/`limit` unbounded vs `le=8760` elsewhere | inconsistent bounds | owner decision; BEHAVIOR | BEHAVIOR |
| C-MISC-3 | `Query(24, …)` positional vs `Query(default=24, …)` | style | FAST002 (40 queue) normalizes to `Annotated` | none |
| C-MISC-4 | `stats.py` prints with emoji (`stats.py:87-116,159`) — the only `print` in core/server | goes with C-DEAD-1/D14 | none |
| C-MISC-5 | `web/templates/irrigators/{new,edit}.html:47,42` placeholder `192.168.1.42` trips the CLAUDE.md privacy grep (`192.168`) | use RFC 5737 `192.0.2.42` — templates are out of scope for the refactor → defer to a UI PR | BEHAVIOR (placeholder text) |

---

## 2. New observed bugs (for `REFACTOR_NOTES.md` "Observed bugs (not fixed)")

- **B-N1 — API `/clusters/{id}/monitor` discards its own sync.** `routes/operations.py:160-184` calls
  `monitor_cluster(cluster_id)` with `no_sync=False`; `services/irrigation.py:846-847` →
  `SyncService.ensure_fresh_and_read` (`services/sync.py:47-76`) does a Cloud `sync_single_sensor` for stale sensors and
  only `flush()`es; the route never commits, so the rows are rolled back at session close and every later `/monitor`
  call re-reads the Cloud (contrast CLAUDE.md invariant 8: sync is the sole Cloud reader). The web route passes
  `no_sync=True` (`web/routes/operations.py:58`). Pin with a characterization test before C-TX-1 touches the route.
- **B-N2 — two DB sessions per authenticated request** (`auth.py:143-149` vs `deps.py:23-29`, C-TX-4). Not a
  correctness bug today; recorded because unifying it changes session sharing.

---

## 3. Ordered task list

Three parallel worktrees with disjoint file groups (`W1` core, `W2` routes/web/deps, `W3` services/scheduler/app/auth/
config + CLI/TUI). The integrator lane `INT` owns `pyproject.toml`, `refactor/mypy-strict.txt`, goldens and docs.
Phase A is behavior-preserving (`refactor(...)`), phase B labeled behavior changes (`fix(consistency|drift): …`),
phase C the doc-contract stage, phase D the WP8 follow-ups. Cross-lane dependencies are marked `⇐`.

### Phase A — behavior-preserving

| # | Lane | Commit | Files | Gate / notes |
|---|---|---|---|---|
| A1 | INT | `refactor(types): add type annotations — list 29 already-strict modules` (C-TYPE-1) | `refactor/mypy-strict.txt` | `make typecheck` |
| A2 | W1 | `refactor(repo): introduce commit/rollback/flush on IrrigationRepository` (C-TX-1 part 1) | `repository.py` | repo tests |
| A3 | W1 | `refactor(core): replace literal — SECONDS_PER_*, FULL_WEEKDAY_MASK in core (non-WP8)` (C-CONST-1/2) | `repository.py`, `stats.py`, `learning/profiling.py`, `schemas.py`, `models.py` | OpenAPI + DDL goldens unchanged |
| A4 | W1 | `refactor(core): introduce constant — vocabularies (SOURCE/SEVERITY/EVENT_ACTION/TRIGGERED_BY) and service thresholds` (definitions only; C-CONST-5/6) | `constants.py`, `models.py` | constants golden superset |
| A5 | W1 | `refactor(core): remove dead code — any_critical, sensor_assignments_for_plant, get_light_needs_info, utils shebang` (C-DEAD-3/4/5, C-STALE-1) | `logic/decision.py`, `repository.py`, `plant_db.py`, `utils.py` | grep evidence in body |
| A6 | W2 | `refactor(api): consolidate duplicate — deps.require_* for identical-wording lookups` (C-404-1…8, C-NAME-5/8, C-STALE-2) | `deps.py`, `routes/{clusters,alerts,irrigators,sensors,plants,charts}.py`, `web/routes/{clusters,irrigators,sensors,plants,plant_dashboard}.py` | split API / web into 2 commits; goldens unchanged |
| A7 | W2 ⇐A2 | `refactor(api): replace repo.session/SessionDep commits with repo.commit()` (C-TX-1 part 2) | `routes/*.py`, `web/routes/*.py` | OpenAPI + MCP goldens unchanged; one commit per tree |
| A8 | W2 | `refactor(api): introduce typed result — model_validate instead of **dict + type: ignore; Metric-typed chart metric` (C-RES-5/6/7) | `routes/{operations,vacation,plants,charts}.py`, `web/routes/clusters.py`, `services/charts.py` (CLUSTER_METRICS typing only) | pipeline output → 2 reviewers for operations |
| A9 | W2 | `refactor(web): move function — web/forms.py form parsers (no error-behavior change)` (C-DUP-3) | `web/forms.py` (new), `web/routes/{configs,plants,irrigators,sensors,vacation,windows,operations}.py` | web HTML goldens |
| A10 | W3 ⇐A2 | `refactor(services): replace repo.session uses with repository methods` (C-REPO-1…4, C-TX-2 spelling) | `services/{search,efficacy,charts,health_monitor,sync,cluster,manual_control,bulk,pump_watcher}.py`, `repository.py` additions go in a preceding W1 commit | health_monitor → 2 reviewers |
| A11 | W3 | `refactor(services): introduce typed result — TypedDicts for cluster/sync/stats/weather/jobs; NamedTuples for bulk/plant sync` (C-RES-1/2-service/3/4/8) | `services/{cluster,sync,weather,bulk}.py`, `stats.py` (W1 sub-commit), `scheduler.py` | scheduler → 2 reviewers |
| A12 | W3 ⇐A4 | `refactor(services): replace literal — vocabulary + threshold constants in services` (C-CONST-5/6) | `services/{leak,data_quality,search,irrigation,anomaly,efficacy,system_health,vacation,weather,pump_watcher}.py`, `web/filters.py` (W2) | irrigation.py → 2 reviewers |
| A13 | W3 | `refactor(app): consolidate duplicate — Settings defaults use DEFAULT_LATITUDE/LONGITUDE; except Exception in _init_tuya` (C-CONST-4, C-ERR-1) | `config.py`, `app.py` | settings golden |
| A14 | W3 | `refactor(scheduler): move function — _job_session below services; _run_leak_check uses it` (C-TX-3) | new `services/_session.py`, `scheduler.py`, `services/irrigation.py` | HIGH: two reviewers, `FULL` gate |
| A15 | W3 | `refactor(services): rename — log→logger, cloud→gateway (private names), stale PumpWatcher comments` (C-LOG-1, C-NAME-2 private, C-STALE-3) | `services/notify.py`, `services/sync.py`, `scheduler.py`, `services/health_monitor.py`; `database.py` (W1) | |
| A16 | W3 | `refactor(cli): consolidate duplicate — server_url helper, DEFAULT_SERVER_URL, ALL_WEEKDAYS, HTTPStatus; remove ensure_object` (C-ENV-1, C-CONST-3/7, C-STALE-11) | `cli/commands/{_helpers,auth,tui,windows}.py`, `cli/main.py`, `cli/client.py`, `tui/{app,formatting,resources}.py`, `tui/screens/cluster.py` | CLI help/output goldens unchanged |
| A17 | W3 | `refactor(tui): remove dead code — #global-config rule` (C-DEAD-11) | `tui/app.tcss` | TUI render goldens unchanged |
| A18 | W1 | `refactor(core): remove dead code — print_stats_report (+ tests, golden name)`; after D14: `format_duration` (C-DEAD-1/2, C-MISC-4) | `stats.py`, `tests/test_contract_stats.py`, `tests/golden/contracts/imports.json` | rule 4: removal-only golden diff |
| A19 | W1 | `refactor(core): remove dead code — ENTITY_SYSTEM, DEFAULT_QUIET_*; SOURCE_DECISION/SYSTEM` (C-DEAD-6/7/8) | `models.py`, `constants.py`, `services/alerts.py` (W3 sub-commit), goldens | owner OK on constants golden |
| A20 | W2 | `refactor(types): add type annotations — route return types; drop web.routes mypy override` (C-TYPE-3) | `routes/*.py`, `web/routes/*.py`, `pyproject.toml` (INT) | OpenAPI/MCP goldens byte-identical |
| A21 | all | `refactor(types): add type annotations — <module group>` per C-TYPE-2 (non-WP8 first: `plant_db`, `tui/resources`, `manual_control`, `services/sync`, `web/filters`, `weather`, …) | per group | append to list |

### Phase B — labeled behavior changes (each pinned first; one pair per commit)

| # | Lane | Commit | Finding |
|---|---|---|---|
| B1 | W2 | `fix(drift): D15 monitor — API commits its freshness sync / web syncs like the API` | C-TX-6, B-N1 |
| B2 | W3 | `fix(consistency): auth and handlers share one request session` | C-TX-4 (2 reviewers) |
| B3 | W1+W2+W1(WP8) | `fix(drift): D16 irrigator config parsing — one lenient parser` | C-DUP-2 |
| B4 | W2 | `fix(drift): D17 efficacy days bound on the API` | C-MISC-1 |
| B5 | W3 | `fix(consistency): debug log on silent exception swallows` | C-LOG-3 |
| B6 | W1 (WP8 file) | `fix(consistency): sync per-sensor failure logged as warning` | C-LOG-2 |
| B7 | W3 | `fix(consistency): TUI irrigator config hint uses device_ip/local_key` | C-STALE-10 |
| B8 | W3 | `fix(consistency): manual stop records action "stop"` (only if OD4 = unify) | C-NAME-1 |
| B9 | W2+W3 | `fix(consistency): services raise NotFoundError; one handler maps it` (after D6 fixes wording; may be `none` if goldens prove identical) | C-404-9, C-ERR-8 |

### Phase C — doc-contract stage and docs (INT)

C1 `docs(cli)`: `windows list` help text (C-STALE-4) with CLI-help golden regen. C2 `docs`: CLAUDE.md (C-STALE-7/8/9,
C-CLK-1 / C-LOG-4 / C-NAME-3 conventions, OD2 transaction rule), README `make check` line, plugin CLI.md/
PLANT_DATABASE.md (C-STALE-4/5/6) — folded into the 45-track docs sync. C3 OD5 end-state cleanup (mypy `files`,
sizecheck move, `refactor/` disposition).

### Phase D — after WP8 (re-verify line numbers on the WP8 head; high-risk review)

D-1 C-CONST-1 engine/sync/gateway literals (if T8.1 didn't already); D-2 C-LOG-1 engine `log→logger`;
D-3 C-DEAD-9 `is_within_preferred_hours` (+tests); D-4 C-DEAD-10 `invalidate_key` (+test) or wire it; D-5 C-DEAD-12
`CONFIDENCE_BASELINE`; D-6 C-NAME-6 keyword-only adapter constructors; D-7 C-ENV-3 TUYA env read at the edge;
D-8 C-RES-2/9 core sync + device TypedDicts; D-9 C-NAME-2 public-param decision for `sync_sensor_data(db, cloud)`;
D-10 C-TYPE-2 `devices/gateway.py` (28 errors).

---

## 4. Counts per pattern

| Pattern | Findings | Behavior-preserving | Labeled behavior change | Blocked / keep / owner |
|---|---|---|---|---|
| Transactions | 6 | 3 | 2 (C-TX-4, C-TX-6) | 1 documented exception (C-TX-5) |
| 404 lookups | 9 | 8 | 0 (wording → D6) | 1 conditional (C-404-9) |
| Service results | 9 | 9 | 0 | OD1 |
| Repository bypass | 6 | 6 | 0 | — |
| Clock / env | 7 | 3 | 0 | 4 keep/blocked (patched by pinned tests) |
| Logging | 4 | 1 | 2 | 1 rule |
| Error handling | 8 groups / 65 sites | 1 (C-ERR-1) | report-only (narrowing) | — |
| Constants | 8 | 8 | 0 | — |
| Naming | 8 | 4 | 1 (C-NAME-1) | 3 frozen/blocked |
| Docstrings | 373 ruff findings | → 40 `D` queue | route/schema text in doc-contract stage | — |
| Typing | 4 | 4 | 0 | — |
| Stale artifacts | 12 | 11 | 1 (TUI hint) | — |
| Legacy shims | 9 | 1 (C-LEG-9 alias) | 0 | 8 keep (OD3 / pinned) |
| Dead code | 12 (+ verified not-dead list) | 12 | 0 | 2 need owner OK (constants golden) |
| Duplicated helpers | 6 | 3 | 1 (D16) | 2 covered by D4/D7 |
| Other | 5 | 1 | 3 (D17, bounds, template IP) | — |

## Added after the drift track (2026-10-03)
- **D18 (W2, fix):** templates pass an age in seconds (`reading_age_seconds`, `dev.age_seconds`) into the `age_seconds`
  filter, which expects a timestamp → those cells render "stale". Unify the filter contract and call sites.
- **D10b (W3):** a fourth soil-target parser in `services/charts._parse_range` → use `parse_moisture_target`.
- **D16b (after WP8):** `devices/gateway._coerce_config` → call `greenhouse_core.models.parse_device_config`
  (already behavior-identical).
- **D11 (after WP8):** shared quiet-hours "active now" helper for web + engine.
- **D19 (W2, fix):** `/clusters/{id}/stats` without irrigator → 500; return the documented error/empty shape consistently with the web.
- **W3:** rename module-level `log` → `logger` in `database.py`/`notify.py` (logger NAME unchanged; `log` golden name removal approved, removal-only diff).
- **After drift merges:** delete `format_duration` if dead (D14 interaction).

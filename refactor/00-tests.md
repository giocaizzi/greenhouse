# 00 — Test audit (Phase 0, read-only)

Baseline: `main @ a1b2622`. Full run (`refactor/baseline/pytest-full.txt`): **1182 passed, 2 warnings in 1168 s**;
line coverage 91.8 %, branch coverage 79.7 % (`refactor/baseline/coverage.json`, 9826 stmts / 2296 branches).
Everything below was derived by reading `tests/` + `libs/`, `pytest --collect-only`, and targeted runs (listed in §2.8).
Nothing outside `refactor/` was modified. Scratch plugins used for the experiments live in the session scratchpad,
not in the repo.

---

## 1. Inventory

### 1.1 Test files and counts (collected items, parametrize-expanded — total 1182)

| Area | File | Tests |
|---|---|---|
| core | `tests/test_cleaning.py` | 16 |
| core | `tests/test_cloud.py` (DeviceGateway) | 16 |
| core | `tests/test_db.py` | 17 |
| core | `tests/test_engine_timing.py` | 17 |
| core | `tests/test_leak_hold.py` | 15 |
| core | `tests/test_learning.py` | 31 |
| core | `tests/test_logic.py` | 39 |
| core | `tests/test_migrations.py` | 5 |
| core | `tests/test_plant_db.py` | 15 |
| core | `tests/test_stats.py` | 12 |
| core | `tests/test_timing.py` | 23 |
| core | `tests/test_utils.py` | 18 |
| core | `tests/test_vacation_rationing.py` | 14 |
| devices | `tests/devices/test_adapter_contract.py` | 15 |
| devices | `tests/devices/test_ik10pw_adapter.py` | 30 |
| cli | `tests/cli/test_cli.py` | 43 |
| cli | `tests/cli/test_completeness.py` | 40 |
| cli/tui | `tests/cli/test_tui.py` | 91 |
| server API | activity 7, alerts 16, anomaly 7, auth 16, bulk_stop 3, charts_heatmap 4, charts_overlay 4, charts_plant_health_timeline 4, clusters 24, configs 12, data_quality 5, decisions 7, efficacy 6, forecast 6, health_monitor 14, insights 3, irrigation_windows 7, irrigators 23, leak_detection 19, leak_rearm 8, mcp 13, notify 27, operations 24, plants 27, plants_health 7, preferences 6, pump_watcher 10, rate_limit 6, scheduler 6, scheduler_jobs 19, scheduler_pause 10, scheduler_settings 34, scheduler_shutdown 7, search 10, sensor_assignments 7, sensors 20, sync_snapshot 7, system_health 5, vacation 13, well_known 5 | 486 |
| server web | web_activity 6, web_alerts 13, web_analytics 12, web_charts 14, web_charts_overlay 7, web_cluster_forms 3, web_cluster_pages 6, web_config_pages 5, web_crud_actions 18, web_dashboard 5, web_decision_rationale 3, web_decisions_efficacy 6, web_filters 24, web_fragments 3, web_health 4, web_insights 5, web_irrigator_actions 15, web_irrigator_capacity 5, web_irrigator_pages 7, web_irrigators 3, web_nav 3, web_operations 6, web_plant_hero 4, web_plant_pages 4, web_polish 8, web_preferences 16, web_quality 4, web_redirects 5, web_scheduler_emergency 9, web_sensor_pages 3, web_vacation 14, web_vacation_edit 9, web_windows 18 | 268 |

Tooling: `pytest>=9.1.1`, `pytest-cov`. **No** `freezegun`/`time-machine`, **no** `pytest-asyncio` (TUI tests use
`asyncio.run`), **no** `pytest-xdist`, **no** `pytest-randomly`, **no** `pytest-socket`. `pythonpath` puts `tests/` on
`sys.path`, which is how `fake_data`, `fake_devices`, `server.conftest`, `cli.tui_fixtures` are imported.

### 1.2 Fixtures and fakes

**`tests/conftest.py`** (global)
- `tmp_db` — in-memory SQLite via `Base.metadata.create_all` (NOT Alembic) → `IrrigationRepository`. Note: because
  this skips migrations, the migration-seeded `global_irrigation_config` row (quiet hours 00–05) does **not** exist
  here; repository built-ins give quiet hours = off.
- `fake_tuya_env` — sets `TUYA_CLIENT_ID/SECRET/REGION` to fakes.
- `sample_cluster` — cluster + plant + irrigator + sensor + 2 readings at `int(time.time())` and `-3600` (real clock).

**`tests/server/conftest.py`**
- `_make_stubbed_app(bypass_auth, **settings)` — `create_app(Settings(...), engine=StaticPool in-memory)`; runs the
  **real Alembic upgrade** (so global quiet hours 00–05 UTC are seeded); overrides `get_device_registry` with a
  `FakeDeviceWiring` (one shared `FakeIrrigatorAdapter` / `FakeSensorAdapter` for every model key + legacy alias) and
  `get_device_gateway` → `None`. Does **not** override `get_weather_client` (see §2.3). `Settings` is built without
  `_env_file=None`, so `.env` in CWD and `IRRIGATION_*`/`GREENHOUSE_*` env vars leak in (§2.4).
- `app` (auth bypassed via `dependency_overrides[require_user|require_web_user]`), `app_real_auth`,
  `auth_disabled_app`, `client`, `anonymous_client`, `authed_real_client` (real login), `seeded_client` (1 cluster
  "Test Cluster" indoor, Monstera, sensor `fake_sensor_001` with **no readings**, irrigator `fake_irrigator_001`,
  config smart/2 min/12 h/auto_run), `running_scheduler` (starts the **process-global** APScheduler paused; stops it
  in teardown).
- `FakeDeviceWiring` — `.irrigator`, `.sensor`, `.registry`; exposed as `app.state.fake_devices`.

**`tests/fake_devices.py`** — `FakeIrrigatorAdapter` (records `calls` tuples `("start", id, minutes)`, `("stop", id)`,
`("status", id)`, `("read_health", id)`; canned `start_result`/`stop_result`/`status_result`; `set_health`),
`FakeSensorAdapter` (`reading` default `{"temperature": 22.0, "soil_moisture": 50.0}`, records `read_live`/
`read_health`), fake `IrrigatorProfile`/`SensorProfile` (`fake.irrigator`/`fake.sensor`). `_clean_health()` stamps
`observed_at=int(time.time())`.

**`tests/fake_data.py`** — constants only: fake Tuya creds, `FAKE_DEVICE_ID`, `FAKE_SENSOR_ID`, `FAKE_DEVICE_ID_2`,
`FAKE_DEVICE_IP=192.0.2.1`, `FAKE_LOCAL_KEY`, cluster/plant/irrigator/sensor names.

**`tests/cli/tui_fixtures.py`**
- `CLUSTERS` — Living Room (indoor, Monstera 58 %, Nephrolepis 38 %, irrigator), Balcony (**outdoor**, Citrus 22 %,
  irrigator), Desk (indoor, Echeveria 80 %, Opuntia no sensor, no irrigator).
- `seed_greenhouse(http, engine)` — creates everything via the API, inserts 96 half-hourly readings per sensor
  (sawtooth, deterministic shape but **anchored at `int(time.time())`**), 3 `start` events at −40/−24/−8 h, a critical
  leak alert on sensor 3 / cluster 2, a battery alert, 3 activity events.
- `tui_client_factory(http, log)` — `IrrigationClient` whose `.http` is the in-process `TestClient`; serialises
  requests with a per-app lock (module-global `_app_locks` keyed by `id(http)`) and optionally records
  `(method, path, json, params)`; `writes(log)` filters out GETs.
- In `test_tui.py`: `greenhouse` fixture, `_app()`, `_settle()` (waits until no worker runs, 10 s deadline),
  `_confirm()`, `_submit_form()`, `_render()` (Rich console, `color_system=None`), autouse `_isolated_token_dir`
  (sets `XDG_CONFIG_HOME`, drops `GREENHOUSE_API_TOKEN`). `SIZE=(160, 50)`.

Other autouse fixtures: `test_utils.py::_reset_display_tz`, `test_scheduler.py:193`, `test_scheduler_settings.py:16`,
`test_completeness.py::_isolated_token_dir`, class-level ones in `test_logic.py` (freeze `time.time` to `_FROZEN_TS`),
`test_leak_hold.py`, `test_learning.py`, `test_plant_db.py`, `test_cloud.py`.

---

## 2. Determinism hazards

### 2.1 Real wall clock (no freezing library in the repo)

Production reads the clock almost exclusively through `time.time()` attribute lookups (`import time` /
`import time as _time` in `services/irrigation.py:5`), plus two `datetime.now(tz=UTC)` calls:
`greenhouse_core/utils.py:40` (month → seasonal light factor) and `services/health.py:128` (daily snapshot date key).
`logic/engine.py:129` takes `evaluated_at = int(time.time())` with no injection point. So any test that does not
monkeypatch `time.time` evaluates against "now". Consequences:

| Hazard | Evidence | Impact |
|---|---|---|
| **Quiet hours 00:00–05:00 UTC** are seeded by migration `7d3c1f9b4a08` into `global_irrigation_config` for every server/TUI app (`tmp_db` tests are immune — `create_all` path, repository defaults `None`). | migration lines 35–36, 53–62; `repository.py:46-49,554-560`; `tests/server/test_clusters.py:117` opts out explicitly "so the wall-clock hour cannot short-circuit". | Any server/TUI test that expects an `irrigate` decision would flip to `skip quiet_hours` between 00–05 UTC. **Measured** (§2.8): all 456 non-web server tests still pass with the engine's quiet-hours check forced to 02:30 UTC — today's assertions are loose enough. A golden/snapshot of decisions, `/status`, the cluster web page (`web/routes/clusters.py:166` `quiet_active_now`) or TUI renders **will** differ at night. |
| Hour-rollover race | `tests/server/test_operations.py:21-57` compute `hour = datetime.now(UTC).hour` then the engine reads `time.time()` later; if the hour ticks between, the window `[h, h+1)` misses. | Rare flake (≈ ms window per hour). |
| Season / month | `logic/engine.py::_apply_seasonal_multiplier` uses `season_for(evaluated_at)`; `utils.py:40` month. Only `test_engine_timing.py` / `test_clusters.py:116` pin season. | `interval_hours`, seasonal reasons and confidence in un-frozen server/TUI responses vary by month → any snapshot drifts across the year. |
| Day boundaries | daily caps (`daily_cap_minutes`, `max_events_per_day`), `services/health.py:128` date key, TUI seed events at −8/−24/−40 h, `test_web_activity.py:12,93`, `test_web_plant_hero.py:28`, `test_stats.py:43,93,169`. | Totals bucketed "today" change near midnight UTC. |
| Relative-time strings | `web/filters.py` (`age_seconds`), TUI `formatting.py:48` (`int(time.time())`), `formatting.py:81` `datetime.fromtimestamp(ts)` uses the **process local TZ** (`TZ` env), not UTC. | Rendered HTML / TUI text contain "Ns ago", local clock times → not snapshot-stable without freezing time and pinning `TZ=UTC`. |
| JWT `iat/exp` | `greenhouse_core/auth.py`, `greenhouse_server/auth.py` | tokens differ per run (never asserted verbatim — OK). |
| Learned/seeded series anchored at now | 26 `time.time()` uses in `test_learning.py`, 11 in `test_vacation.py`, 10 in `test_leak_rearm.py`, 9 in `test_web_vacation.py`, 7 each in `test_anomaly.py` / `test_scheduler_shutdown.py`. | Fine for relative assertions; not snapshot-stable. |

Tests that already freeze: `test_logic.py:32,616` (`time.time` → `_FROZEN_TS`), `test_engine_timing.py:37` and
`test_vacation_rationing.py:37` (`monkeypatch.setattr("time.time", …)`), `test_scheduler.py:163-164`
(engine + repo modules), `test_leak_rearm.py:184` (`irrigation_mod._time`). Pattern works because production uses
attribute lookup; it cannot reach the two `datetime.now` sites.

### 2.2 Sleeps / real elapsed time

- `tests/server/test_sensor_assignments.py:89,101,106,138,150,154` — six `time.sleep(1.1)` (needs strictly increasing
  second-resolution timestamps; ~6.6 s wall). Deterministic but slow; freezing/advancing a fake clock would remove it.
- `tests/server/test_scheduler_shutdown.py:106` — spawns a **subprocess** server, real APScheduler threads, polls with
  `time.sleep(0.05)` (script line 77) under a 15 s deadline, subprocess `timeout=45`, asserts `elapsed < 30`. Generous
  margins; ran stable 2/2 here.
- `test_pump_watcher.py` — uses injected `sleep`/clock (deterministic).

### 2.3 Real network

- **Open-Meteo is called for real from server and TUI tests.** `services/irrigation.py:410,414`
  (`WeatherClient.get_current`, `urllib.request.urlopen`, timeout 8 s, `services/weather.py:39`) runs whenever the
  pipeline resolves temperature without `temp_override` and without a sensor temperature — i.e. every indoor
  `no_sync` call and every outdoor call — and `logic/engine.py:557` / `services/forecast.py:127` call
  `get_forecast` for outdoor clusters. `get_weather_client` is not overridden in `tests/server/conftest.py`.
  **Measured** with a urlopen-logging plugin over `test_operations.py`, `test_forecast.py`, `test_web_operations.py`:
  13 real `urlopen("https://api.open-meteo.com/v1/forecast?latitude=45.464&longitude=9.189&…")` attempts from 12
  tests (`TestIrrigate::*`, `TestLeakHold::*`, `TestCheck::*`, `TestFullLifecycle::test_create_to_irrigate`,
  `test_web_operations::{test_check_all_clusters,test_check_single_cluster,test_irrigate_dry_run_returns_decision_panel}`).
  Web + TUI run adds 7 more (§2.8) — 19 network-touching tests found in the files sampled (the rest of the
  suite was not instrumented). Effect: when online, `temperature` (and so duration / reasons) follows live Milan
  weather; when offline, each uncached call costs up to the DNS/connect timeout (≤ 8 s) and falls back to 20 °C.
  This is the main reason tests like `test_decisions.py:28` assert only `action in ("irrigate", "skip")`.
- `tests/cli/test_cli.py:248-254` (`test_server_unreachable`) connects to `127.0.0.1:1` (local refusal; fine).
- No Tuya calls: `get_device_gateway → None`; `test_cloud.py`/`test_ik10pw_adapter.py` mock `tinytuya`.
- ntfy: only if `GREENHOUSE_NTFY_SERVER_URL` + `GREENHOUSE_NTFY_TOPIC` are set in the developer's environment
  (see §2.4) — then server tests would POST to the real ntfy server.

### 2.4 Environment-variable dependence

- `Settings` (`greenhouse_server/config.py:8-14`) reads `env_file=".env"` and `IRRIGATION_*` + the
  `GREENHOUSE_{MCP_TOKEN,NTFY_*,AUTH_*}` aliases. Only `test_scheduler_settings.py:23` and the shutdown subprocess pass
  `_env_file=None`. Every `_make_stubbed_app` app inherits unset fields from the environment — e.g. a developer's
  `IRRIGATION_PUMP_WATCHER_ENABLED=false`, `IRRIGATION_CHECK_CRON_HOURS`, `GREENHOUSE_NTFY_*`, `IRRIGATION_WEATHER_LAT`
  would silently change behaviour (and the OpenAPI/MCP output is unaffected but HTML/notify are not).
- `IRRIGATION_TZ` → `utils.get_display_timezone` fallback (`utils.py:95`); reset only in `test_utils.py`.
- `IRRIGATION_PLANT_DB_PATH` → `plant_db.py:10-11`.
- `TUYA_CLIENT_ID/SECRET/REGION` → `devices/gateway.py:116-118` (tests set them via `fake_tuya_env`).
- CLI: `XDG_CONFIG_HOME`, `GREENHOUSE_API_TOKEN`, `IRRIGATION_SERVER_URL`. `test_tui.py` and `test_completeness.py`
  isolate the first two (autouse); **`tests/cli/test_cli.py` does not** — it reads the real `~/.config/greenhouse/token`
  and `GREENHOUSE_API_TOKEN` (harmless today because the mock transport ignores headers, but a `--help`/output golden
  could pick up a "logged in" state).
- Typer/Rich `--help` output depends on `COLUMNS`, `TERM`, `NO_COLOR`/`FORCE_COLOR` — any help golden must pin them.

### 2.5 Process-global state / ordering dependence

- `greenhouse_server/scheduler.py:28` — one module-level `BackgroundScheduler` shared by every app in the process;
  `_CORE_JOB_IDS` (line 80) only ever grows; `_shutdown_event` (line 37) stays **set** after `stop_scheduler()` until the
  next `start_scheduler()`. `stop_scheduler` uses `shutdown(wait=False)`, so job threads from one test may outlive it.
  Correctness relies on `init_scheduler` calling `configure()` + `remove_all_jobs()` (lines 163, 171) on every
  `create_app`. Reverse-order run of the 5 scheduler-related files (53 tests) passed (§2.8), so no current order bug.
- `utils._display_timezone` (`utils.py:74`) — set by every `create_app` (`set_display_timezone(tz_name)`), so server
  tests self-heal; core tests rely on the autouse reset in `test_utils.py` only.
- `PlantDatabase` singleton (`test_plant_db.py:103-109`).
- `tui_fixtures._app_locks` keyed by `id(http)` — ids can be recycled after GC (would share a lock; harmless).
- `WeatherClient` caches per instance (per app) with `time.monotonic()` TTL.

### 2.6 Randomness

None: no `random`, `uuid`, `secrets` in `libs/` or `tests/` (bcrypt salts / JWTs aside, never compared verbatim).

### 2.7 Textual timing

- `_settle()` is a correct "wait until idle" loop with a 10 s deadline — good.
- Fixed pauses that depend on scheduler speed: `test_tui.py:1292` `test_auto_refresh_polls_again` (`refresh_seconds=0.2`,
  `pause(1.2)`, asserts `>= 3` hits — would flake on a heavily loaded runner), `:938` `pause(0.4)`,
  `:292` `pause(0.01)` ×12 in `test_rapid_reloads_never_duplicate_cards` (intentionally racy regression test).
- Animated sprites tick via `set_interval(ANIMATION_INTERVAL)` (`tui/widgets.py:40`) unless `animations=False`; the
  test helper `_app()` leaves animations on → any screen snapshot must disable them and pin `TZ`.
- Workers run blocking client calls in threads (`app.py:84`, `asyncio.to_thread`); concurrency is serialised by the
  fixture lock, which hides real ordering between concurrent loads.

### 2.8 Experiments run (evidence)

| Run | Result |
|---|---|
| `pytest --collect-only -q` | 1182 items |
| Non-web server tests (`tests/server --ignore-glob=test_web_*`) with engine quiet-hours check forced to 02:30 UTC (scratch plugin patching `engine.is_within_quiet_hours`; 2 self-referential quiet-hour tests deselected) | **456 passed** — current assertions are not night-sensitive |
| Same plugin, sanity: `test_irrigate_blocked_by_quiet_hours` | fails as expected (plugin effective) |
| Engine-adjacent core + server files (17 files) with an ineffective constants patch | 260 passed (discarded — `DEFAULT_QUIET_*` in `constants.py` is **unused**; the live default is the migration literal) |
| urlopen/socket logging plugin over `test_operations.py`, `test_forecast.py`, `test_web_operations.py` | 36 passed, **13 Open-Meteo calls from 12 tests** |
| `test_scheduler_jobs.py` + `test_scheduler_shutdown.py` + `test_sensor_assignments.py` ×2 | 33/33 passed both runs, identical |
| 5 scheduler-related files in **reverse** test order | 53 passed |
| All `tests/server/test_web_*.py` + `tests/cli/test_tui.py` with night plugin + network logging (urlopen blocked) | **358 passed**; 7 more Open-Meteo calls: `test_web_decision_rationale::test_rationale_panel_after_irrigate`, `test_web_operations::{test_irrigate_dry_run_returns_decision_panel,test_check_single_cluster,test_check_all_clusters}`, `test_tui::TestDashboard::test_cards_for_every_cluster`, `::TestSearchAndSystem::test_search_opens_cluster`, `::TestExactRequests::test_dashboard_check_all_and_sync` |

Observation for `REFACTOR_NOTES.md` (not a bug fix): `constants.DEFAULT_QUIET_START_HOUR/END_HOUR` are dead — no
consumer outside the migration's own private copy. CLAUDE.md invariant 5 ("all thresholds live in constants.py") is
thus violated in spirit by `migrations/versions/7d3c1f9b4a08…py:35-36` (out of scope to touch).

---

## 3. Contract coverage matrix (frozen surfaces)

Legend: **PINNED** = an existing test would fail on any change; **PARTIAL** = some aspects asserted; **MISSING**.

| # | Contract | Status | Existing tests | What's missing |
|---|---|---|---|---|
| a | Full `app.openapi()` JSON | **MISSING** (PARTIAL on shape) | `test_mcp.py::test_mcp_does_not_leak_web_routes` (all paths start `/api/v1`), `::test_mcp_exposes_every_api_operation_as_a_tool` (path set == MCP op paths), `test_well_known.py::test_well_known_paths_stay_out_of_openapi` | No snapshot of paths × methods × operationIds × schemas × tags × docstrings × components. Route function renames, docstring edits, Pydantic field order/alias changes all pass today. |
| b | MCP tool list (name / description / inputSchema) | **PARTIAL** | `test_mcp.py`: `::test_mcp_exposes_every_api_operation_as_a_tool`, `::test_mcp_tool_names_fit_within_64_chars`, `::test_mcp_every_api_route_has_a_response_model` ("Example Response:" present), `::test_every_api_route_has_a_docstring` (AST), `::test_mcp_request_bodies_carry_their_field_schemas` (8 paths, first field only), auth tests (401/503/accept), `::test_mcp_tool_invocation_reaches_inner_endpoint` | No golden of `[(tool.name, tool.description, tool.inputSchema)]`; names could change freely as long as ≤ 64 chars. |
| c | Rendered HTML of every web route | **PARTIAL** (markers only) | 268 web tests; heuristic path scan: 85/88 non-API routes are requested by some test. ~250 `assert "…" in resp.text` fragment assertions; 63 redirect/HX-header asserts. | **Not requested by any test:** `POST /clusters/{cluster_id}/plants/{plant_id}/move` (`plant_dashboard.move_plant_web`), `GET /dashboard/hero` (`fragments.dashboard_hero`), `POST /logout` (`auth.logout_submit`). No full-HTML golden anywhere; template names / context keys unpinned. |
| d | CLI `--help`, command tree, options, exit codes | **PARTIAL** | `test_cli.py` (43) + `test_completeness.py` (40): request shape per command (method/path/json/params), exit codes 0 / 1 (ServerError, not found, conflict) / 2 (`check` with alerts, `monitor` needs water: `test_cli.py:256,265`); `test_tui.py::TestTuiCommand` (`--server`, `--refresh`, `--no-animation`, env fallback) | **No `--help` test at all**, no command-tree snapshot, no option default snapshot; JSON stdout asserted only loosely. |
| e | Console scripts / packaging | **MISSING** | — | Nothing asserts `greenhouse = greenhouse_cli.main:app`, `greenhouse-server = greenhouse_server.app:main` (pyproject `[project.scripts]`), nor that `main()` calls `load_dotenv()` → `Settings()` → `create_app` → `uvicorn.run(host, port)`. |
| f | Env vars / `Settings` schema | **PARTIAL** | `test_scheduler_settings.py` (34: `CHECK_CRON_HOURS`, `CHECK_INTERVAL_HOURS`, `SYNC_INTERVAL_MINUTES` validation + env), `test_mcp.py` (mcp_token via kwarg), `test_utils.py` (`IRRIGATION_TZ`), CLI `IRRIGATION_SERVER_URL` | No snapshot of `Settings.model_json_schema()`/field defaults; aliases `GREENHOUSE_MCP_TOKEN`, `GREENHOUSE_NTFY_*`, `GREENHOUSE_AUTH_*` never read from env in a test; `IRRIGATION_` prefix, `env_file`, `extra="ignore"` unpinned. |
| g | SQLAlchemy DDL + Alembic head | **PARTIAL** | `test_migrations.py`: empty → head (subset of table names), idempotent upgrade, legacy repair, device-type backfill, logger regression | Head compared to `head_revision()` (dynamic) not the literal `9f2b5e7c6a31`; no DDL snapshot of `Base.metadata` (columns/types/nullability/indexes/uniques/FKs); no "migrated schema == `create_all` schema" check. |
| h | Public import-path surfaces | **MISSING** | indirect imports only | No `dir()` / `__all__` snapshot for `greenhouse_core.{models,schemas,repository,constants,utils,plant_db,logic,devices,learning,sync,stats,database,auth}` / `greenhouse_server.{app,config,deps,auth,scheduler}`. Only `greenhouse_core`, `.logic`, `.devices`, `.learning` define `__all__`. |
| i | `constants.py` values + `TriggerCode` members | **MISSING** (PARTIAL) | Tests import `LEAK_*`, `MIN_COOLDOWN_HOURS`, `CLEANING_HAMPEL_MIN_READINGS` and compute with them (so value changes go unnoticed); individual codes asserted as strings (`"quiet_hours"`, `"leak_hold"`, …) | No snapshot of the 100 module-level constants nor of `TriggerCode` (name → value) / `Action` / `Severity` members. |
| j | TUI | **PARTIAL** | Sprite mood mapping `TestSprites::test_mood_with_fallback_band`/`::test_mood_uses_supplied_band` (PINNED); request bodies: `TestExactRequests` (9 `writes(log)` asserts: irrigate body, water-now body, check, new/edit routing, cancel never writes for `i w x c n u D`), CRUD tests assert resulting server state; confirm flow: `test_stop_all_requires_confirmation`, `test_check_cluster_after_confirm`, `test_dashboard_check_all_and_sync`, `test_system_maintenance_actions` (pause confirms, resume doesn't), `test_remove_scheduler_job`, delete flows in `TestClusterCrud`, `test_preferences_global_and_vacation` | **No test enumerates `BINDINGS` per screen** (app, dashboard, cluster, alerts, activity, system, settings, search, modals, forms, ClusterCard). No screen-render snapshot (`run_test` + `export_screenshot`/text). Widget ids/classes vs `app.tcss` unpinned. `action_*`/`on_*` method names unpinned. |

---

## 4. Invariant coverage matrix

| # | Invariant | Status | Pinning test(s) | Gap / to add |
|---|---|---|---|---|
| 1 | Protocol v3.5 for IK10PW | **MISSING** | `test_cloud.py:224-242` pass `3.5` as an argument to `open_local` (doesn't prove the adapter uses it) | Assert `profiles/ik10pw.json` → `IrrigatorProfile.protocol_version == 3.5` and that `IK10PWAdapter` local ops call `gateway.open_local(irr, 3.5)` (spy). |
| 2 | Driest plant drives (`min_soil_moisture`) | PINNED | `test_logic.py::test_multi_sensor_driest_triggers`, `test_sync_snapshot.py::test_soil_moisture_is_the_driest_sensor`, `test_cleaning.py::test_snapshot_min_soil_ignores_spike`, `test_tui.py::TestModel::test_summarize_picks_driest_plant` | — |
| 3 | 6 h global cooldown; check cadence independent | PARTIAL | `test_logic.py::test_cooldown_blocks_irrigation` (1 h ago), `test_engine_timing.py::test_cooldown_beats_quiet_hours`, `test_scheduler.py::test_24_hourly_checks_yield_at_most_4_actuations`, `test_scheduler.py::test_check_all_uses_cron_trigger` / `::test_cron_trigger_runs_hourly_by_default` | Boundary (5 h 59 m blocks / 6 h 01 m allows); `MIN_COOLDOWN_HOURS == 6` literal; cooldown is global across triggered_by (manual start also counts). |
| 4 | Learning advisory, never blocks | **MISSING** | — (`engine.py:582-594` `_attach_learning_alerts` swallows) | Monkeypatch `IrrigationLearner.detect_issues` to raise → decision still produced, identical action. |
| 5 | Thresholds in `constants.py` | n/a (code rule) | — | Covered by the constants snapshot (3.i). Note dead `DEFAULT_QUIET_*` (§2.8). |
| 6 | `IrrigationDecision` canonical; `DecisionLog` for every evaluation | PARTIAL | `test_engine_timing.py::TestDecisionAssignmentValidation` (2), `test_decisions.py::test_irrigate_writes_decision_log`, `::test_decision_log_fields`, `test_leak_hold.py::test_hold_is_persisted_to_the_decision_log` | Every early-return branch persisted on the scheduled path (`no_plants`, `cooldown`, `quiet_hours`, weather skip, final) with correct `primary_code` / `actuated` / `payload_json` round-trip; `actuated=True` only on real start. |
| 7 | Every `/api/v1` route: `response_model`, Pydantic body, Google docstring | PARTIAL | `test_mcp.py::test_mcp_every_api_route_has_a_response_model`, `::test_every_api_route_has_a_docstring` | Docstring *format* (`Args:`/`Returns:`/`Raises:`) and "Pydantic request body" not enforced — OpenAPI snapshot (3.a) freezes current state instead. |
| 8 | One Cloud writer; health reads SQLite; `ensure_fresh_and_read` force-syncs one stale sensor; single-call live read; `open_local` from config/cache | PARTIAL | `test_cloud.py::test_v2_shadow_empty_is_authoritative_no_fallback`, `::test_get_live_reading_v2_fallback_to_v1`, `::test_open_local_uses_cached_config_key_no_getdevices`, `::test_open_local_cold_lookup_then_cached`; `test_health_monitor.py::test_offline_transition_via_stale_last_seen`; `test_sync_snapshot.py` (fresh-data snapshot shape) | **`greenhouse_core/sync.py` is 8.6 % covered (0 % branches)** — the sole Cloud reader is untested. No test that a fresh reading costs zero gateway calls and a > `SENSOR_READING_STALE_SECONDS` reading triggers exactly one `sync_single_sensor` for that sensor; no test that the health monitor never calls `read_live`/Cloud. |
| 9 | Plant-DB timing fields JSON → plant_db → engine | PINNED | `test_plant_db.py::test_category_defaults_*`, `::test_species_overrides_category_defaults`; `test_engine_timing.py::test_*_no_windows_*` (no windows = all hours), `::test_outdoor_fruit_tree_summer_emits_seasonal_boost`, `::test_winter_multiplier_lengthens_interval`; `test_timing.py` (23) | — |
| 10 | Cleaned view for judgements, raw for archive | PARTIAL | `test_cleaning.py` (16 incl. `clean_readings_desc`/`_around`), judge-side: `test_sync_snapshot.py::test_spike_reading_does_not_become_the_snapshot`, `test_leak_detection.py::test_outlier_spike_does_not_raise_an_alert`, forecast/efficacy/plants_health spike tests; raw side: `test_anomaly.py::test_spike_detected` | Display/archive side: charts, cluster history, data-quality must still show the spike (raw) — no test. |
| 11 | Leak alert IS the hold | PINNED (one gap) | `test_leak_hold.py` (15: hold, ack still holds, force doesn't bypass, resolve releases, expiry at `LEAK_HOLD_HOURS`, other cluster), `test_operations.py::TestLeakHold` (3 E2E), `test_leak_detection.py` (19 incl. `::test_no_fake_irrigation_event_is_written`, `::test_settled_sensor_resolves_its_open_alert`), `test_leak_rearm.py` (8, 30-min delay), `test_logic.py::test_schedule_updated_does_not_suppress_fallback` | Escape hatch: `POST /irrigators/{id}/start` still actuates while held — no test. |
| (i) | `reasons` always `list[Reason]`; `validate_assignment=True` | PINNED | `test_engine_timing.py::test_seasonal_reason_keeps_reasons_a_list`, `::TestDecisionAssignmentValidation::test_tuple_reasons_are_coerced_to_list`, `::test_wrong_type_fails_at_the_assignment`, `test_clusters.py` seasonal `/status` warning-as-error test (line ~110) | — |
| (ii) | `check_all_clusters` per-cluster commit/rollback, `action="error"`, `check_failed` resolved by next success, earlier start events survive | PARTIAL | `test_operations.py::TestCheckAllIsolation::test_crash_in_one_cluster_keeps_earlier_clusters_committed`, `::test_failure_alert_resolves_on_next_success` | Failing cluster raises *before* writing, so "rolled back alone" (partial writes of the failing cluster discarded) is untested; survival is shown with an `ActivityEvent` stand-in, not an `IrrigationEvent(action="start")`; crash in an *earlier* cluster not blocking later ones untested; scheduler `_check_job` path (vs API) untested. |
| (iii) | Scheduler semantics | PARTIAL | idempotent registration `test_scheduler_jobs.py::TestRegistrationIsIdempotent` (2); `paused` truthfulness `::TestPausedFlagIsTruthful` (5) + `test_scheduler_pause.py` (10, persisted in preferences, survives restart); job defaults `::test_periodic_jobs_tolerate_late_wakeups` (misfire None + coalesce); 409 `::test_every_startup_job_is_refused_with_409`, `::test_core_jobs_refused_on_running_scheduler`; stopped pause/resume `::test_pause_resume_on_stopped_scheduler`, `test_web_scheduler_emergency.py::test_scheduler_pause_works_when_not_running` / `::test_scheduler_resume_works_when_not_running`; shutdown `test_scheduler_shutdown.py::test_shutdown_during_irrigation_is_prompt[auto-1|manual-0]`, `::TestShutdownPolicy` (3, `triggered_by="shutdown"`) | `max_instances == 1` not asserted; "web + API share one code path" not asserted (spy on `set_check_all_paused`); job bodies `_sync_job`, `_health_snapshot_job`, `_check_job`, `_anomaly_job`, `_health_monitor_job`, `init_health_monitor` uncovered (`scheduler.py` 57.6 % lines, missing 289-443). |
| (iv) | Alembic `fileConfig(disable_existing_loggers=False)` | PINNED | `test_migrations.py::test_init_db_does_not_disable_application_loggers` | — |
| (v) | TUI: blocking calls via `api()` threads; 401 → sign-in; cursors stable; actuating keys via `ConfirmScreen` | PARTIAL | 401: `TestLogin::test_401_prompts_login_and_stores_token`, `TestResilience::test_login_failure_reprompts`; cursors: `TestRefill::test_cursor_follows_record_across_reload`, `::test_cursor_clamps_when_record_disappears` (widget-level only); confirm: see 3.j; errors toasted: `TestResilience::test_server_error_is_toasted_not_raised` | Thread off-loop not asserted; cursor stability across a real screen auto-refresh not asserted; no exhaustive key→dialog table. **Doc/behaviour note:** `i` and `w` open `IrrigateScreen` / `WaterNowScreen` (own modals, not `ConfirmScreen`), and scheduler *resume* (`p` while paused) and `S` sync / `P` plant sync / `H` snapshot run without confirmation — CLAUDE.md says "every actuating key goes through ConfirmScreen". Pin current behaviour; record the wording mismatch in REFACTOR_NOTES. |

---

## 5. Thin spots

Judged from tests plus `refactor/baseline/coverage.json` (branch coverage on; migrations excluded).

### 5.1 Worst 30 modules by branch coverage

| Branch % | Line % | Stmts | Miss | Br | Br miss | Module |
|---:|---:|---:|---:|---:|---:|---|
| 0.0 | 8.6 | 57 | 50 | 24 | 24 | `greenhouse_core/sync.py` |
| 0.0 | 50.0 | 22 | 9 | 4 | 4 | `greenhouse_server/web/routes/fragments.py` |
| 12.5 | 50.0 | 24 | 9 | 8 | 7 | `greenhouse_cli/commands/sensors.py` |
| 12.5 | 53.8 | 31 | 11 | 8 | 7 | `greenhouse_cli/commands/plants.py` |
| 16.7 | 63.9 | 30 | 8 | 6 | 5 | `greenhouse_core/devices/sensors/tr301z.py` |
| 20.0 | 45.9 | 51 | 25 | 10 | 8 | `greenhouse_server/services/weather.py` |
| 33.3 | 67.0 | 70 | 17 | 18 | 12 | `greenhouse_server/web/routes/operations.py` |
| 41.2 | 55.7 | 72 | 27 | 34 | 20 | `greenhouse_core/stats.py` |
| 43.8 | 69.4 | 56 | 13 | 16 | 9 | `greenhouse_core/devices/irrigators/tuya_generic.py` |
| 46.2 | 71.1 | 88 | 19 | 26 | 14 | `greenhouse_server/web/routes/plant_dashboard.py` |
| 50.0 | 79.5 | 42 | 8 | 2 | 1 | `greenhouse_server/web/context.py` |
| 50.0 | 83.9 | 52 | 5 | 10 | 5 | `greenhouse_cli/tui/screens/search.py` |
| 50.0 | 84.6 | 37 | 5 | 2 | 1 | `greenhouse_core/auth.py` |
| 50.0 | 87.4 | 99 | 8 | 12 | 6 | `greenhouse_cli/tui/screens/modals.py` |
| 50.0 | 89.8 | 55 | 4 | 4 | 2 | `greenhouse_server/web/routes/sensors.py` |
| 50.0 | 90.5 | 19 | 1 | 2 | 1 | `greenhouse_cli/commands/vacation.py` |
| 50.0 | 90.5 | 19 | 1 | 2 | 1 | `greenhouse_cli/commands/windows.py` |
| 50.0 | 99.3 | 573 | 2 | 4 | 2 | `greenhouse_core/schemas.py` |
| 57.1 | 77.2 | 95 | 16 | 28 | 12 | `greenhouse_server/routes/plants.py` |
| 57.1 | 89.2 | 69 | 3 | 14 | 6 | `greenhouse_cli/tui/screens/alerts.py` |
| 58.3 | 81.8 | 43 | 5 | 12 | 5 | `greenhouse_cli/commands/irrigators.py` |
| 58.3 | 90.4 | 82 | 4 | 12 | 5 | `greenhouse_cli/tui/screens/settings.py` |
| 60.7 | 60.0 | 107 | 43 | 28 | 11 | `greenhouse_core/devices/irrigators/ik10pw.py` |
| 62.5 | 76.4 | 56 | 11 | 16 | 6 | `greenhouse_cli/commands/operations.py` |
| 64.3 | 57.6 | 222 | 97 | 42 | 15 | `greenhouse_server/scheduler.py` |
| 64.3 | 78.6 | 56 | 10 | 14 | 5 | `greenhouse_server/web/routes/configs.py` |
| 64.7 | 76.8 | 191 | 36 | 68 | 24 | `greenhouse_server/services/health_monitor.py` |
| 70.0 | 89.4 | 37 | 2 | 10 | 3 | `greenhouse_server/services/efficacy.py` |
| 71.4 | 94.3 | 73 | 1 | 14 | 4 | `greenhouse_cli/tui/app.py` |
| 72.2 | 85.4 | 140 | 18 | 18 | 5 | `greenhouse_server/app.py` |

### 5.2 Largest absolute gaps (missed lines + missed branches)

`scheduler.py` 112 · `services/irrigation.py` 83 (76.5 % br; missing 148-223 pump-watcher scheduling/interrupt
paths, 482-495, 587-614) · `core/sync.py` 74 · `services/health_monitor.py` 60 · `devices/irrigators/ik10pw.py` 54 ·
`tui/screens/cluster.py` 51 · `core/stats.py` 47 · `repository.py` 43 · `logic/engine.py` 38 (88.1 % br; missing
143, 158, 420, 474-475, 559, 593-594, 682, 769-771, 827-828, 842-844, 883, 916-928) · `services/weather.py` 33 ·
`web/routes/plant_dashboard.py` 33 · `devices/gateway.py` 32.

### 5.3 Qualitative thin spots (high-risk areas from BRIEF)

- **devices/**: `ik10pw.py` 60 % lines — local keep-alive / Duration DP 102 / DP 105 paths partly untested;
  `tuya_generic.py` adapters (irrigator + sensor) have no direct test; `tr301z.py` parser branches 1/6.
- **engine**: well covered for rules, but `evaluated_at` has no injection seam; outdoor weather-skip branch only hit
  through the un-mocked network client.
- **auth**: `greenhouse_core/auth.py` 50 % branches; `test_auth.py` (16) + `test_rate_limit.py` (6) cover the flows.
- **scheduler**: job bodies untested (see 4.iii).
- `services/weather.py` has **no** unit test (only hit via live network in other tests).
- `web/exception_handlers.py`, `services/search.py`, `learning/report.py`, `logic/fallback.py`, `logic/stress.py` have
  no direct tests (exercised only transitively — acceptable unless moved).

---

## 6. Phase-1 gap list (concrete tests to add before any production move)

Shared determinism kit (add first, test-only):
- **T0 `tests/_determinism.py` + fixture `frozen_clock(ts)`**: monkeypatch `time.time` (global attribute — reaches all
  production call sites incl. `irrigation._time`), `greenhouse_core.utils.datetime` and
  `greenhouse_server.services.health.datetime` (two `datetime.now` sites) to a fixed `2026-04-15T10:00:00Z`
  (spring, outside 00–05 quiet hours, mid-day — far from day boundaries). Alternatively add `time-machine` to the
  dev group (BRIEF allows dev-group `uv.lock` changes) — preferred because it also freezes `datetime.now` and Textual
  `fromtimestamp`.
- **T1 fixture `offline_weather`** (autouse in new golden tests): `app.dependency_overrides[get_weather_client] =
  lambda: StubWeather(current=None, forecast=None)` *and* set `app.state.weather_client` to the same stub (the
  `IrrigationService` dependency reads it via `deps.py:63`). Do not change the existing conftest (rule 8) — apply in
  the new test modules only.
- **T2 fixture `clean_env`**: `monkeypatch.delenv` for every `IRRIGATION_*`, `GREENHOUSE_*`, `TUYA_*`; `chdir(tmp_path)`
  so `.env` can't load; `TZ=UTC` + `time.tzset()`; `COLUMNS=100`, `TERM=dumb`, `NO_COLOR=1` for CLI help.
- Goldens stored under `tests/golden/` as pretty-printed JSON/text with sorted keys; an opt-in regeneration switch
  (`GOLDEN_UPDATE=1`) used only in the commit that creates them.

| ID | Target file | Asserts | Determinism |
|---|---|---|---|
| G1 | `tests/server/test_contract_openapi.py` | `json.dumps(app.openapi(), sort_keys=True, indent=1)` == `tests/golden/openapi.json` | Pure function of code; build app via `_make_stubbed_app` under T2 (env can't alter routes, but keep it clean). |
| G2 | `tests/server/test_contract_mcp.py` | `[(t.name, t.description, t.inputSchema) for t in app.state.mcp.tools]` sorted by name == `golden/mcp_tools.json`; plus `operation_map` (name → method, path) | Same as G1. |
| G3 | `tests/server/test_contract_web_html.py` | For a fixed seeded DB, GET every web route (path list generated from the router, ids substituted from the seed) and POST-redirect routes: status, `location`/`HX-*` headers, and full `resp.text` == `golden/web/<route>.html`; plus an assertion that the route list itself equals `golden/web_routes.json` (method, path, endpoint name, `include_in_schema=False`). Must include the 3 currently unexercised routes (`POST …/plants/{id}/move`, `GET /dashboard/hero`, `POST /logout`). | T0 + T1 + T2; seed with explicit timestamps (no `time.time()`); normalise only things that are genuinely random (none found) — do **not** normalise times, freeze them. Quiet-hours banner is then stable. |
| G4 | `tests/server/test_contract_templates.py` | Spy on `templates.TemplateResponse` during G3: template name + sorted context keys per route == golden | as G3. |
| G5 | `tests/cli/test_contract_help.py` | Walk the Typer/Click command tree (`typer.main.get_command(app)`), for every command/sub-app: `--help` output, params (name, opts, default, required, type), golden; exit codes: `--help` 0, unknown command 2, `ServerError` 1, `check` alerts 2, `monitor` needs-water 2 | T2 (`COLUMNS`, `NO_COLOR`, `TERM`, isolated `XDG_CONFIG_HOME`, no `GREENHOUSE_API_TOKEN`); `CliRunner(mix_stderr=…)` per installed Click. |
| G6 | `tests/cli/test_contract_json_output.py` | For each command already exercised in `test_cli.py`/`test_completeness.py`, exact stdout bytes for a canned response | MockTransport (existing pattern). |
| G7 | `tests/test_contract_packaging.py` | `importlib.metadata.entry_points(group="console_scripts")` contains `greenhouse=greenhouse_cli.main:app`, `greenhouse-server=greenhouse_server.app:main`; `greenhouse_server.app.main` with `uvicorn.run`/`load_dotenv` monkeypatched calls `create_app(Settings())` then `uvicorn.run(app, host=…, port=…)` | Monkeypatch `uvicorn.run`, `create_app`; T2. |
| G8 | `tests/server/test_contract_settings.py` | `Settings.model_json_schema()` + `{name: (default, alias choices)}` == golden; env-driven reads for `GREENHOUSE_MCP_TOKEN`, `GREENHOUSE_NTFY_{SERVER_URL,TOPIC,TOKEN}`, `GREENHOUSE_AUTH_{SECRET_KEY,ADMIN_USERNAME,ADMIN_PASSWORD}`, `IRRIGATION_PORT` etc.; `env_prefix`, `env_file`, `extra` | T2 + `_env_file=None` where reading `.env` is not the subject; one test writes a temp `.env` in `tmp_path` to pin `env_file=".env"`. |
| G9 | `tests/test_contract_schema.py` | (a) DDL of `Base.metadata` via `CreateTable(t).compile(sqlite_dialect)` for every table + indexes == golden; (b) `head_revision() == "9f2b5e7c6a31"`; (c) reflected schema after `init_db` on a temp file vs. `create_all` (columns/types/nullable/uniques) — record any existing drift as an observed bug rather than "fixing" | Pure; temp-file SQLite (existing `file_db` pattern). |
| G10 | `tests/test_contract_imports.py` | For each listed module: `sorted(n for n in dir(mod) if not n.startswith("_"))` and `__all__` (if any) == golden. Use public names only (private helpers may move). | Pure. Note: `dir()` includes re-imported names (e.g. `time`, `logging`); keep them in the golden — refactors that drop an import from a frozen module must add a shim or justify. |
| G11 | `tests/test_contract_constants.py` | `{k: v for k, v in vars(constants).items() if k.isupper()}` == golden (100 names); `{m.name: m.value for m in TriggerCode}`, `Action`, `Severity` == golden | Pure. |
| G12 | `tests/cli/test_contract_tui.py` | Per screen class (GreenhouseApp, DashboardScreen, ClusterScreen, AlertsScreen, ActivityScreen, SystemScreen, SettingsScreen, SearchScreen, ConfirmScreen, IrrigateScreen, WaterNowScreen, LoginScreen, FormScreen, ClusterCard): normalised `BINDINGS` (key, action, description, show) == golden; `CSS_PATH`; set of `action_*`/`on_*`/`_on_*` method names; screen render text via `pilot.app.export_screenshot()`→ strip SVG or `Console(record=True)` export for dashboard / cluster tabs / alerts / activity / system / settings == golden; table of actuating key → expected modal class (`ConfirmScreen` for X, c, x, delete, D, p-pause, delete-job, delete-vacation; `IrrigateScreen` for i; `WaterNowScreen` for w; *no modal* for p-resume, S, P, H, y) | T0 (freeze before `seed_greenhouse`, which uses `time.time()`), animations off (`animations=False`, the flag `greenhouse tui --no-animation` passes through — see `TestTuiCommand`); `TZ=UTC`; T1 (Balcony is outdoor → weather). |
| G13 | `tests/cli/test_tui.py` (new class) or `test_contract_tui.py` | Blocking calls run off the event-loop thread: client factory whose methods record `threading.current_thread() is not threading.main_thread()` / `asyncio` loop thread id | Deterministic. |
| G14 | same | Cursor stability on a real screen: select row k in `#alerts-table`, trigger `screen.reload()` with an extra row inserted server-side, assert `selected_key` unchanged | `_settle`. |
| I1 | `tests/devices/test_ik10pw_adapter.py` (new test) | `IrrigatorProfile` from `ik10pw.json` has `protocol_version == 3.5`; local DP writes call `gateway.open_local(irrigator, 3.5)` | Spy gateway; `fake_tuya_env`. |
| I3 | `tests/test_logic.py` (new class) | cooldown boundary: start at `now − 6h + 60s` → skip `cooldown`; at `now − 6h − 60s` → not cooldown; manual `start` counts; `schedule_updated` doesn't (already pinned) | `frozen_clock`. |
| I4 | `tests/test_logic.py` | `IrrigationLearner.detect_issues` raising → `decide_for_cluster` returns the same action/codes as with learner patched to `[]` | `frozen_clock`. |
| I6 | `tests/server/test_decisions.py` | For each early return (`no_plants`, `cooldown`, `quiet_hours`, `leak_hold`, final irrigate via `/check`): one `decision_logs` row with expected `primary_code`, `actuated`, `payload_json` parsing back to `IrrigationDecision` | T0 + T1. |
| I8a | `tests/server/test_sync_service.py` (new) | `SyncService.ensure_fresh_and_read`: fresh reading (< `SENSOR_READING_STALE_SECONDS`) → zero gateway calls; stale → exactly one `sync_single_sensor` for that sensor only | Fake gateway object recording calls; `frozen_clock`. |
| I8b | `tests/test_sync.py` (new, core) | `greenhouse_core.sync.sync_sensor_data` / `sync_single_sensor`: backfill via `get_device_logs` + one live read per sensor, dedup by `(sensor_id, timestamp)`, stats dict shape, error paths | Fake `DeviceGateway`; explicit timestamps. |
| I8c | `tests/server/test_health_monitor.py` (new test) | Health poll derives sensor health from latest `SensorReading`; `FakeSensorAdapter.calls` contains no `read_live` | existing fixtures. |
| I10 | `tests/server/test_web_charts.py` / `test_data_quality.py` (new tests) | A Hampel-rejected spike still appears in chart JSON, cluster history and data-quality counts (raw) while `/status` snapshot ignores it | Explicit timestamps; T0. |
| I11 | `tests/server/test_operations.py::TestLeakHold` (new test) | With an open leak alert, `POST /api/v1/irrigators/{id}/start` still starts (fake adapter `("start", …)` recorded) | existing pattern. |
| I-ii | `tests/server/test_operations.py::TestCheckAllIsolation` (new tests) | (1) failing cluster writes an `IrrigationEvent`/activity then raises → its writes rolled back, others kept; (2) crash in cluster 1 → cluster 2 still evaluated; (3) earlier cluster's real `IrrigationEvent(action="start")` survives a later crash; (4) same via scheduler `_check_job` | Monkeypatch `IrrigationService.check_cluster`; T1. |
| I-iii | `tests/server/test_scheduler_jobs.py` (new tests) | `job.max_instances == 1` for all `DEFAULT_JOB_IDS`; web `POST /scheduler/pause` and API `POST /api/v1/scheduler/pause` both call `scheduler.set_check_all_paused` (spy); `_sync_job`, `_check_job`, `_anomaly_job`, `_health_snapshot_job`, `_health_monitor_job` invoked directly with `_app` set → expected service calls / no-op when gateway is `None` | Call job functions synchronously; no running scheduler needed. |
| W1 | `tests/server/test_weather.py` (new) | `WeatherClient.get_current`/`get_forecast` parsing, cache TTL (`time.monotonic` patched), exception → `None`, URL incl. `timezone=` | Monkeypatch `urllib.request.urlopen` with canned JSON. |

Ordering: T0–T2 → G9/G10/G11 (pure, cheap) → G1/G2 → G7/G8 → G5/G6 → G3/G4 → G12–G14 → invariant tests → W1.
All new tests must pass on unmodified `main` before any production change (BRIEF rule 2), and should be run twice
plus once at a frozen night-time timestamp to prove determinism.

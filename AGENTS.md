# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) and other coding agents working in this repository. The file is `AGENTS.md`; `CLAUDE.md` is a symlink to it (keep it that way).

## What this is

**greenhouse** — smart plant irrigation. Reads Tuya sensors → decides per cluster → acts on Tuya irrigators (local protocol v3.5) → learns from each cycle → persists everything to local SQLite. **Tuya Cloud is the live source; SQLite is the permanent record.** Actuation is **local-first**: cycle bounding (Duration DP 102) and the dry-run safety read (DP 105) are local-only v3.5, and device discovery / `local_key` lookup never touch the Cloud in steady state (keys resolve from `irrigator.config` or the gateway's process cache; a stored key a re-paired device rejects with tinytuya error 914 is refreshed by one rate-limited Cloud lookup, the local call retried once, and the new key persisted to the config). The on/off **switch pulse** does go via the Cloud API, because the Zigbee-gateway pump can't be reliably kept awake over LAN — this is the one deliberate Cloud actuation call. There is no local keep-alive fallback (removed): a bounded IK10PW start whose local Duration DP write fails is **refused before any ON is sent** (the pipeline records `attempted` + an `actuation_failed` alert; a manual start answers 502).

Stack: Python 3.11+, uv workspaces, FastAPI + Pydantic v2 + SQLAlchemy v2 + Alembic, Typer CLI + Textual TUI, APScheduler, tinytuya, Jinja2 + HTMX + Chart.js + Pico.css (no build step), `fastapi-mcp`.

## Architecture — four interfaces, one server

The **server** is the only thing that touches the DB and the devices. Four ways to talk to it:

| Interface | URL | Notes |
|-----------|-----|-------|
| REST API | `/api/v1` | Authoritative entry point. OpenAPI schema and interactive docs at `/docs` — that's the source of truth, not a hand-maintained list. |
| Web UI | `/` | HTMX + Jinja2, server-rendered. Calls the service layer **in-process** — shares code with the API (services, `deps.require_*` lookups, validators), not a client of it. |
| CLI (`greenhouse`) | — | Thin `httpx` client against `/api/v1`. Does not import core, has no DB access; useless when the server is down. |
| MCP | `/mcp` | Every `/api/v1` endpoint auto-published via `fastapi-mcp`. Web routes excluded (they set `include_in_schema=False`). Bearer-token auth, fail-closed. |

Anything new the CLI or an MCP tool should be able to do must first exist as an API endpoint. A rule that both the API and the web UI enforce lives once, in a service (see Conventions), never re-implemented per surface.

### MCP security model

The `require_mcp_token` dependency in `app.py` is wired into `FastApiMCP` via `AuthConfig(dependencies=[...])`:

- `GREENHOUSE_MCP_TOKEN` unset, empty or whitespace-only → `/mcp` returns 503 (`MCP auth not configured`).
- Set but missing/wrong `Authorization: Bearer …` → 401 with `WWW-Authenticate: Bearer`, checked against `settings.mcp_token` on every request in constant time (`auth.is_mcp_token`, `hmac.compare_digest`; the same check grants the MCP principal in `require_user`).
- Bearer header only: the MCP token sent as the session cookie is decoded as a JWT and refused (401 `Invalid session`).
- Only `GREENHOUSE_MCP_TOKEN` is read (bare `MCP_TOKEN` / `IRRIGATION_MCP_TOKEN` are ignored, with a startup WARNING naming them).
- The server binds `127.0.0.1` by default (`IRRIGATION_HOST`); the Docker image and `docker-compose.yml` opt in to `0.0.0.0`.
- Live `FastApiMCP` instance is stored on `app.state.mcp` for test introspection.

> ⚠️ **MCP grants physical actuation authority** (`/clusters/{id}/irrigate`, `/irrigators/{id}/start`, etc.). Treat the bearer token like a root credential: high entropy (`openssl rand -hex 32`), unique per deployment, never committed, rotated on suspected compromise.

## Packages — `core ← server ← cli`

uv workspace, three packages under `libs/` — `libs/greenhouse-{core,server,cli}/greenhouse_{core,server,cli}/` (deliberately no `src/` layout). Distribution / import / entry point: `greenhouse-{core,server,cli}` / `greenhouse_{core,server,cli}` / `greenhouse` (CLI, `greenhouse_cli.main:app`), `greenhouse-server` (`greenhouse_server.app:main`).

Dependency direction is strict and **enforced by import-linter** (`make lint-imports`; contracts under `[tool.importlinter]` in the root `pyproject.toml`):

- the CLI imports neither core, server, SQLAlchemy nor FastAPI; core never imports server or CLI;
- server layers: `app > routes | web > deps | auth > scheduler > state > services > config` (three documented lazy exceptions: `services.irrigation_jobs`, `services.pump_watcher_jobs` and `services.system_health` → `scheduler`); API `routes` and `web` are independent of each other; services import no FastAPI/Starlette;
- `devices/` never imports `logic`, `learning`, `repository`, `schemas` or `sync`; `models` / `schemas` / `constants` import no behavior; `logic` never drives devices;
- `learning` builds on `logic` (the engine's advisory learning hook is the only way back); inside `logic`, `engine` orchestrates and the helpers never import it;
- the TUI view-model modules (`tui/model.py`, `tui/formatting.py`, the `tui/render/` package) import no Textual, screens, widgets, client or httpx.

### `greenhouse-core` (`greenhouse_core`)

| Module | Holds |
|---|---|
| `__init__.py`, `_lazy.py` | package re-exports resolve lazily on first access (PEP 562, `lazy_module_attrs`; also in `devices/` and `learning/`), so importing a leaf such as `greenhouse_core.constants` does not load SQLAlchemy, Alembic, the engine or `tinytuya` |
| `models.py` | SQLAlchemy v2 models (they declare the migrations' server defaults and named uniques, so `create_all` and the migrated schema agree); event vocabularies (`EVENT_ACTION_*`: `start`, `stop`, `attempted`, `aborted`, legacy `off`; `TRIGGERED_BY_*`); `parse_device_config` — the one lenient device-`config` parser (malformed / non-object JSON → `{}`) |
| `schemas.py` | Pydantic v2 request/response models (class and field names/order are the OpenAPI contract); irrigator and sensor responses mask `local_key` as `LOCAL_KEY_MASK` (`••••••••`) |
| `repository/` | `IrrigationRepository` — every query and write, composed from per-entity mixins (`_audit`, `_sensors`, `_clusters`, `_irrigation`, `_settings` on `_base`; patch types in `_types`); import it from `greenhouse_core.repository`. Transaction API: `repo.commit()` / `repo.rollback()` / `repo.flush()` |
| `database.py`, `migrations/` | engine + session factory; `init_db` always runs `alembic upgrade head` (no pre-Alembic repair, see Key facts) |
| `constants.py` | every threshold and tuning value, named by purpose (invariant 5) |
| `plant_db.py`, `data/plant_database.json` | curated plant DB and the layered `get_care_data` merge |
| `logic/` | decision engine — `engine.py` (`IrrigationLogic.decide_for_cluster`), `decision.py` (`IrrigationDecision`, `Reason`, `TriggerCode`), `cleaning.py`, `sensors.py` (snapshot, each sensor's current reading), `stress.py`, `trends.py`, `timing.py` (`environment_for`: only `"outdoor"` is outdoor, for the engine, forecast and temperature source alike), `plant_needs.py` (`parse_moisture_target` / `moisture_target_range`, the only soil-target parser, validated; per-plant bands via `current_soil`), `fallback.py` |
| `learning/` | post-irrigation learning — `profiling.py`, `issues.py`, `learner.py`, `report.py`, `models.py` |
| `devices/` | `gateway.py` (`DeviceGateway`: one `tinytuya.Cloud` client + local-device factory, `open_local`, `run_local` (error-914 key refresh + one retry), paginated `get_device_logs`), `local_keys.py` (`LocalKeyResolver`: config → cache → Cloud, superseded keys, per-device refresh budget), `datapoints.py` (pure DP code → canonical key parsers and `group_logs_by_timestamp`; re-exported from `gateway`), `registry.py` (model key → adapter; `registered_{irrigator,sensor}_keys` drive the write-time type validation), `profile.py` + `profiles/*.json`, `health.py`, `irrigators/` (`ik10pw.py`, `tuya_generic.py`), `sensors/` (`tr301z.py`, `tuya_generic.py`) |
| `sync.py` | core Tuya Cloud → SQLite sensor sync (driven by the server's `SyncService`) |
| `stats.py`, `auth.py`, `utils.py` | cluster stats, Argon2id password hashing / user helpers, small time/light helpers |

`logic/engine.py` stays one cohesive rule pipeline (a registered size exception: frozen tests patch `engine.time` / `engine.season_for` and pin its constant imports); its rules are small private helpers inside it.

### `greenhouse-server` (`greenhouse_server`)

| Module | Holds |
|---|---|
| `app.py` | `create_app` + `main`: settings, DB init, device/gateway wiring, scheduler start, routers, MCP mount (`require_mcp_token`) |
| `config.py` | `Settings` (env prefix `IRRIGATION_`; secrets read only as `GREENHOUSE_*`, see Key facts; `host` defaults to `127.0.0.1`) |
| `state.py` | typed accessors for `app.state` (session factory, settings, plant DB, weather, gateway, registry, health monitor, notifier) and the request providers shared by `deps` and `auth` |
| `deps.py` | FastAPI providers (`get_repository`, gateway / registry / health monitor / weather / notifier / plant DB, service factories) and the **`require_*` lookups** — `require_{cluster,cluster_irrigator,irrigator,plant,plant_in_cluster,sensor,sensor_in_cluster,window_in_cluster,vacation_window,alert,metric}` — one 404 wording per entity, used by API and web |
| `auth.py` | JWT issuance, session cookie, auth dependencies, `is_mcp_token` (constant-time MCP token check) |
| `scheduler.py` | APScheduler: built-in jobs `check_all` (cron, `IRRIGATION_CHECK_CRON_HOURS`, default `"*"` = top of every hour), `sensor_sync`, `sensor_anomaly`, `device_health_monitor`, `plant_health_snapshot`, `alert_digest` (09:00 local: one ntfy push counting the unresolved critical alerts, with the oldest one's age); ad-hoc one-shots (pump watchers, leak checks). Built-in jobs can be paused (`check_all`) but not deleted |
| `routes/` | `/api/v1` JSON routes, one module per resource |
| `web/` | HTMX/Jinja2 UI — `router.py`, `routes/`, `templates/`, `static/`, `templating.py`, `forms.py` (shared form parsers; a non-numeric or non-finite value is a 400 naming the field), `weekdays.py`, `filters.py` (`relative_age`; the `age_seconds` filter takes an age in seconds, `time_ago` a timestamp), `context.py` (banners only for a signed-in viewer; footer clock in the `timezone` preference), `exception_handlers.py` (error format follows `Accept`) |
| `services/` | orchestration shared by API, web and jobs (below) |

`services/`:

- **pipeline & actuation** — `irrigation.py` (`IrrigationService`: pipeline, `monitor_cluster`, `check_cluster` / `check_all_clusters`, `stop_unrecorded_start` compensating stop), `actuation_gates.py` (the gates between an IRRIGATE decision and `adapter.start`, in order: device health → per-day caps → `dry_run_global`), `caps.py` (`daily_cap_violation`: effective caps, cluster row over global row — one verdict for the manual 409 and the automatic `daily_cap_hit` skip), `manual_control.py` (manual start / stop / log), `bulk.py` (stop-all), `pump_watcher.py` (DP 105 dry-run abort), `leak.py` (post-irrigation leak detector), `health_monitor.py` (`DeviceHealthMonitor` state machine, shared cache, actuation gate, `for_repo` per-job view, `restore_from_open_alerts`), `health_alarms.py` (alarm vocabulary: trigger codes, dedup keys, inbox wording, `derive_alarms`);
- **data** — `sync.py` (`SyncService.ensure_fresh_and_read`), `weather.py` (Open-Meteo), `anomaly.py`, `maintenance.py`, `data_quality.py`, `system_health.py`;
- **read models** — `cluster.py` (status, history, plant-DB sync, CSV export), `charts.py`, `forecast.py`, `efficacy.py`, `insights.py`, `health.py` (plant health scores + daily snapshot), `search.py`;
- **shared write rules** — `inventory.py` (irrigator / sensor registration, duplicate device ids, plant-in-cluster), `windows.py` (`validate_window`), `vacation.py` (`validate_vacation_range`, reservoir budgets), `alerts.py` (alert inbox upsert / reconcile), `notify.py` (ntfy);
- **job plumbing** — `device_keys.py` (the gateway's `on_key_discovered` hook: persists a Cloud-fetched `local_key` to the irrigator config on a background thread), `jobs.py` (`job_session(commit=…)`, the one transaction scaffold for scheduler jobs, and `read_session`), `irrigation_jobs.py` (leak-check scheduling, `leak_check_armed` / `leak_check_skipped` markers, restart re-arm), `pump_watcher_jobs.py` (pump-watcher scheduling, job body and the shutdown policy);
- `errors.py` (`NotFoundError` family mapped to 404 at the edge), `chart_payload.py` (chart payload TypedDicts).

### `greenhouse-cli` (`greenhouse_cli`)

| Module | Holds |
|---|---|
| `main.py` | Typer root app (`--server`), registers every command |
| `client.py`, `_client/` | `IrrigationClient` (composed from per-area endpoint mixins in `_client/`; httpx, 30 s timeout; transport errors and empty / non-JSON bodies become `ServerError`, a 422 detail is rendered `field: message`) and the token file (`~/.config/greenhouse/token`, `$XDG_CONFIG_HOME`; a non-blank `$GREENHOUSE_API_TOKEN` wins) |
| `constants.py` | `DEFAULT_SERVER_URL`, `ALL_WEEKDAYS` — values mirrored from the server side (the CLI may not import core) |
| `commands/` | `_helpers.py` (`server_url`, `call` → `ServerError` → exit 1, `output` via `rich.print_json`, `HelpOnBareGroup`: a bare group prints its help and exits 0), `operations.py` (top-level operations), `auth.py`, `tui.py`, one module per sub-app |
| `tui/` | Textual UI — `app.py` (`GreenhouseApp`; blocking client calls run in threads via `GreenhouseApp.api()`), `screens/`, `table.py` (the `DataTable` every TUI table uses: Textual's, repainting when a column widens after a paint; a registered `SHADOWING_ALLOWED` exception), `widgets.py`, `sprites.py` (half-block pixel-art), pure view-model `model.py` (payload → summaries), `render/` (payload → rows / rich text by screen area, golden-pinned), `formatting.py`, `resources.py` (form field specs) |

Commands: top-level `status`, `irrigate`, `check`, `monitor`, `sync`, `learn`, `history`, `stats`, `health`, `stop-all`, `login`, `logout`, `whoami`, `tui`; sub-apps `cluster`, `plant`, `irrigator`, `sensor`, `config` (+ `config global`), `scheduler`, `alerts`, `decisions`, `prefs`, `vacation`, `windows`. Server URL resolves `--server` → `$IRRIGATION_SERVER_URL` → `http://localhost:8000`. Output is JSON; exit 1 on errors, 2 for `check --all` with `has_alerts` and `monitor` with plants needing water.

**TUI actuation is not uniformly confirmed** (pinned by `tests/golden/tui/actuation.json`): yes/no `ConfirmScreen` guards check / check-all, stop / stop-all, cluster delete, every row delete, scheduler pause and resume, and the plant-DB sync (`P`); irrigate (`i`) and water-now (`w`) open their own dialogs and writes go through forms; sensor sync (`S`), health snapshot (`H`), alert ack / resolve / re-scan (`k` / `v` / `y`) and logout run on the key press. Keep `references/CLI.md` in step with that table.

## Key invariants

1. **Protocol v3.5** for Rainpoint IK10PW (not 3.3).
2. **Driest plant drives the call** — the lowest of each sensor's newest cleaned soil reading, each judged against the band of its own linked plant (an unlinked sensor gets the cluster's widest band), never the average. The soil rule and the critical-stress override both work this way; a sensor whose newest reading is older than `SENSOR_READING_STALE_SECONDS` is not current and neither drives nor vetoes. The 24 h aggregates (`min_soil_moisture`, …) stay in the snapshot for the audit log.
3. **6h global cooldown** between irrigations (`MIN_COOLDOWN_HOURS` in `constants.py`), counting every `start` event (auto, manual, forced, logged manual watering). The edge is exclusive: a start exactly 6 h ago no longer blocks. Check cadence (`IRRIGATION_CHECK_CRON_HOURS`) is independent of irrigation cadence — the scheduler decides how often to *observe*; the engine cooldown gates *actuation*. The legacy `IRRIGATION_CHECK_INTERVAL_HOURS` is gone (no longer read; use `IRRIGATION_CHECK_CRON_HOURS=*/N`).
4. **Learning is advisory** — never blocks decisions.
5. **All thresholds live in `constants.py`** — no magic numbers scattered through engines or services; give each a purpose name (e.g. the freshness thresholds `SENSOR_READING_STALE_SECONDS`, `MAINTENANCE_STALE_SECONDS`, `DATA_QUALITY_STALE_SECONDS` are distinct on purpose). CLI/TUI-side values that mirror the server go in `greenhouse_cli/constants.py`.
6. **`IrrigationDecision` is the canonical engine output** — typed Pydantic with `action`, `duration_minutes`, `interval_hours`, `confidence`, `reasons: list[Reason]`. Each `Reason` carries a stable `TriggerCode` enum so UI / MCP / audit log key on it without parsing free text. Persisted to `decision_logs` for every pipeline evaluation (acted-on or not, dry runs included): `DecisionLog` stores `primary_code`, `reason_text`, `actuated`, full `payload_json`. A run the server's actuation gates stop (device health, per-day caps, `dry_run_global`) is written back: the row becomes `skip` with the gate's code as `primary_code` (`device_*`, `daily_cap_hit`, `dry_run_global`) and `actuated = false`, and a `decision_skip` activity row carries the details. An automatic start is committed the moment the device accepts it (event, `irrigated` activity, `actuated`), before any follow-up can fail.
7. **Every `/api/v1` route MUST declare** `response_model=`, a Pydantic request body, and a Google-style docstring (imperative summary, then `Args:` / `Returns:` / `Raises:` — skip framework-level params like `repo`). `fastapi-mcp` derives MCP tool schemas from OpenAPI and uses the docstring as the LLM-facing tool description; untyped/undocumented routes produce tools an LLM cannot reason about safely. Enforced by `tests/server/test_mcp.py` (binary endpoints like CSV export are exempted there). Route function names are the MCP tool names — renaming one renames a tool.
8. **One Cloud writer, everyone else reads SQLite.** The sync job (`IRRIGATION_SYNC_INTERVAL_MINUTES`, default 180) is the routine reader of sensor data from the Tuya Cloud — it backfills `getdevicelog` (full-granularity, device-pushed) + one live read per sensor. The health monitor derives sensor health (battery / offline-by-staleness) from the latest persisted `SensorReading` via `read_health(sensor, latest)` — **no live Cloud read**; a sensor counts as offline only after one sync interval + `SENSOR_OFFLINE_GRACE_MINUTES` (default 210 min), irrigators after `OFFLINE_AFTER_MINUTES` (30, live local read). `water_warning` (DP 111) means "soil dry" everywhere — an engine trigger, never a sensor-fault alert. The irrigation pipeline and the monitor (`GET /clusters/{id}/monitor` and the web monitor panel) read the persisted row through `SyncService.ensure_fresh_and_read`, which force-syncs **one** sensor only when its reading is older than `SENSOR_READING_STALE_SECONDS` (`constants.py`, default 4h; a soil sensor is dated by its newest soil reading); the monitor commits what that sync fetched, so a repeat call does not hit the Cloud again. `get_live_reading` is single-call: v1.0 `getstatus` fires only when v2.0 shadow *fails*, never on an empty-but-successful read. All device I/O funnels through the one app-scoped `DeviceGateway` (one token); `open_local` resolves `local_key` from config/cache so local reads (health poll, pump watcher) cost zero Cloud calls. The one exception is a stale key: adapters' local calls go through `DeviceGateway.run_local`, which on error 914 makes one Cloud `getdevices` lookup (at most one per device per `LOCAL_KEY_REFRESH_INTERVAL_SECONDS`, 1 h), retries the local call once only when the Cloud key differs (never a second Cloud ON), uses that key for the rest of the process and hands it to the `on_key_discovered` hook, which the server wires to `services/device_keys` (persists `config['local_key']` on its own session).
9. **Plant DB timing fields flow JSON → plant_db → engine → decision in three layers.** Source of truth is `libs/greenhouse-core/greenhouse_core/data/plant_database.json`: per-species `preferred_water_hours_local` and `season_frequency_multiplier{,_outdoor}` live on `species[…]`; per-category defaults live in the top-level `_category_defaults` block. `plant_db.get_care_data` (in `plant_db.py`) merges these with precedence species > `_category_defaults[category]` > `categories[category]` > built-in fallbacks, and surfaces `_category_defaults` verbatim so the engine can keep the category layer distinct. The engine consumes them in `logic/engine.py`: `_apply_window_rule` gates ONLY on per-cluster `IrrigationWindow` rows — **no windows = all hours allowed** (subject to quiet hours; issue #83), so `preferred_water_hours_local` is advisory plant data and never blocks irrigation; `_apply_seasonal_multiplier` picks `season_frequency_multiplier_outdoor` when `cluster.environment == "outdoor"` and feeds both species- and category-level overrides to `logic/timing.seasonal_multiplier` for per-season fallback.
10. **Cleaned view for judgements, raw rows for the archive.** `sensor_readings` is never mutated; `logic/cleaning.py` produces a cleaned view (range gate + Hampel spike filter, a neighbour test for series of 3–4 points) at read time. Anything that *judges* — decision snapshot, trends, leak detection, efficacy, plant-health scores, forecast, monitor status, learned profiles and issue heuristics — reads through `clean_readings` / `clean_readings_desc` / `clean_readings_around`. Anything that *displays or archives* (charts, cluster history, data quality, maintenance battery/staleness) and the `sensor_drift` anomaly scan (which must see what cleaning masks) read raw. Mind the ordering: `get_recent_readings` is DESC, `clean_readings` is ASC — use `clean_readings_desc` when the caller means "latest".
11. **A leak/stuck-valve alert IS the hold.** The post-irrigation detector (`services/leak.py`) runs after **every real start**: 30 min after an automatic or forced start, and `max(30 min, run + 25 min settle)` after a manual start with a duration (a manual start without one gets a `leak_check_skipped` activity row; a hand log gets nothing). Non-automatic starts write a `leak_check_armed` marker with the event, so the restart re-arm finds them. It raises one critical `leak_or_stuck_valve` alert per offending sensor; `IrrigationLogic._enforce_leak_hold` then skips automatic irrigation for `LEAK_HOLD_HOURS` (24h) while that alert is unresolved, with the terminal `leak_hold` code in the decision trail. Resolving the alert (or a later check finding the sensor settled) releases it; acknowledging does not, and `force=true` does not bypass it — the escape hatch is `POST /irrigators/{id}/start`. Never re-express a hold as an `IrrigationEvent`: `_enforce_cooldown` counts **only** `action == "start"`, so a `schedule_updated` row blocks nothing (that was issue #103's phantom "auto-cancel 24h"). The hold edge is exclusive: an alert last seen exactly 24 h ago no longer holds.
12. **Stops record `stop`.** Manual, emergency (stop-all) and shutdown stops all write `action="stop"`; older databases may still hold `off` rows for earlier manual stops, so history and stats can show both. A dry-run abort writes `aborted`; an automatic start whose device call failed (or was refused, e.g. no local Duration DP) writes `attempted`. Stop-all counts a device only when it confirms the stop: a refused stop is reported in `errors` and records no event. A start that cannot be recorded after the device accepted it is stopped again (compensating stop).
13. **A forced run is a manual run.** `force=true` bypasses quiet hours only; its decision log, event and push say `manual`, it gets the leak check and the dry-run watcher, and at shutdown it is stopped like an automatic run (a plain manual start is left on its device timer). Nothing bypasses the actuation gates or the leak hold.
14. **`dry_run_global` means no start actuates.** Automatic runs become a `dry_run_global` skip; every manual start answers 409 `global dry run is on: irrigator not started`. Stops, stop-all and shutdown stops always work.

### Key facts that are easy to get wrong

- **Vacation times** are Unix seconds on the API / MCP / CLI; the web UI and the TUI parse and display them in the `timezone` preference. Every write path rejects `starts_at >= ends_at` (400). A window is active for `starts_at <= now < ends_at`.
- **Emergency runs obey the operator's limits.** The device water warning, critical stress and the no-sensor fallback change the dosage, but still pass the irrigation windows and the vacation budget.
- **Scheduler job ids are free text.** The API (`DELETE /scheduler/jobs/{job_id:path}`) and web delete routes take a path parameter, so an id containing `/` works; the CLI client percent-encodes the id and the web template URL-encodes it.
- **Per-day caps** (`max_events_per_day`, `daily_cap_minutes`) resolve cluster → global (`services/caps.py`) and bind every start: the manual start and a hand log refuse with 409; the automatic pipeline (`/irrigate` with or without `force`, `/check`, the scheduler) turns the run into a `daily_cap_hit` skip.
- **Actuation gate order** before `adapter.start`: device health → per-day caps → `dry_run_global` → start (`services/actuation_gates.py`); the first that fires wins and `force` bypasses none of them.
- **Secrets are read from `GREENHOUSE_X` only** — `GREENHOUSE_MCP_TOKEN`, `GREENHOUSE_NTFY_{SERVER_URL,TOPIC,TOKEN}`, `GREENHOUSE_AUTH_{SECRET_KEY,ADMIN_USERNAME,ADMIN_PASSWORD}`. The bare and `IRRIGATION_`-prefixed spellings are ignored (one startup WARNING per ignored name). The ntfy URL must be `http(s)://` with a host, or the server refuses to start.
- **`local_key` and `device_ip` never leave the server in clear**: irrigator and sensor responses show both as `••••••••` (`schemas.mask_device_secrets`); a write that sends the mask back keeps the stored value (`inventory.keep_masked_device_secrets`), a new value replaces it, `config: null` clears the whole config. The web edit form reads the stored row, so it still shows the IP.
- **PATCH tells absent from null**: an omitted field is unchanged, an explicit `null` clears a nullable field and is ignored for a required one. A `default_cluster_id` that names no cluster is a 404.
- **Device types are validated on write** against the registry's model keys (`rainpoint.ik10pw`, `tuya.tr301z`): an unknown new or changed type is a 422 listing the known keys; an unchanged stored type is left alone. Data migration `a1d3f5b7c902` rewrote legacy values (`tuya_cloud`, `soil_moisture`, …) on upgrade.
- **Database migrations are Alembic-only.** A database created before Alembic (tables present, no `alembic_version`) is no longer patched up at startup — it fails with "table … already exists" and must be compared with head and stamped by hand (`alembic stamp head`). Old open `pump_dry_run` alerts from before the pump-alert unification are no longer auto-resolved at startup; resolve them through the inbox.
- `migrations/versions/*` are frozen history — add a new revision, never edit an old one.
- Deliberate behaviour that looks like a gap, and the tests that pin it, is in [`docs/design-notes.md`](docs/design-notes.md); a test named `*_current_behavior*` pins one. Changing one is a labeled behavior change that updates exactly that test (renamed to the new behavior).

## Conventions

- **Transactions.** Route handlers own the commit for CRUD (`repo.commit()`; never `session.commit()` directly). A service commits only when a side effect must follow a durable write (e.g. `manual_start` commits the `start` event and its leak-check marker before scheduling the leak check, arming the watcher and notifying; the pipeline commits an automatic start the moment the device accepts it; `check_all_clusters` commits per cluster; the health job commits per device). Scheduler jobs open their transaction with `services/jobs.job_session` (commit on success, roll back + log on failure, always close). The web template context reads preferences on its own short-lived session (deliberate: read-only).
- **Lookups and 404s** go through `deps.require_*` (one wording per entity, API and web alike); services signal "not found" with domain exceptions (e.g. `ClusterNotFoundError`, `PlantNotInClusterError`) or `None`, mapped to HTTP at the edge.
- **Shared rules live in services**, called by both the API route and the web route: `services/inventory.py`, `services/windows.validate_window`, `services/vacation.validate_vacation_range`, `models.parse_device_config`, `logic/plant_needs.parse_moisture_target`. Error wording is the API's.
- **Service results** are core read models or `TypedDict`s; Pydantic models are built at the HTTP edge.
- **Clock.** Read `int(time.time())` at call time (no clock helper, no `from time import time` — `tests/test_refactor_guards.py` enforces it); tests freeze time with `time_machine`.
- **Logging.** Module-level `logger = logging.getLogger(__name__)`, %-style lazy arguments. Job / actuation failures → `logger.exception`; advisory or best-effort failures → `warning` / `debug` with `exc_info`. Never swallow an exception silently — at least a DEBUG line. Logger names are pinned (`tests/golden/contracts/loggers.json`).
- **Naming.** Core functions keep their public `db` repository parameter; new and server-side code uses `repo`.
- **Size and complexity.** Functions ≤ 40 body lines, nesting ≤ 3, files ≤ 400 lines (`make sizecheck`, exceptions in `scripts/size-exceptions.txt`); ruff `C90` max-complexity 8 plus `PLR0911/0912/0915` and the preview size rules `PLR0904/0914/0917/1702`. Existing exceptions are registered and only shrink.
- **Lint.** ruff runs every rule (`select = ["ALL"]`, `[tool.ruff]` in `pyproject.toml`). The only ignores are rules that conflict with something kept — `COM812`/`ISC001` (formatter), `S101` in tests, `FAST001`/`D417` in `routes/` (invariant 7). A line that genuinely cannot satisfy a rule carries a one-rule `# noqa: CODE — reason`; never a blanket noqa, never a new per-file ignore.
- **Docstrings** are Google style and say *why*. Route docstrings are MCP tool descriptions, Typer docstrings are `--help` text, schema docstrings are OpenAPI descriptions: changing them regenerates the matching goldens in a dedicated `docs(api)` / `docs(cli)` commit.
- **Dead code** is removed in its own commit with the grep evidence in the body; route / web / Typer / Textual handlers, scheduler jobs and anything a golden renders are never "dead".

## Development

Repo conventions (PRs, CI `check` job, releases) follow the [giocaizzi GitHub standard](https://github.com/giocaizzi/.github/blob/main/docs/standard.md); this repo is its reference implementation.

```bash
make install       # uv sync
make serve         # uv run greenhouse-server (API + web UI on :8000)
make check         # full local gate: pre-commit-run + lint-imports + typecheck + sizecheck + coverage
make check-fast    # lint + lint-imports + typecheck + sizecheck, no tests (~15 s): what the agents' stop hook runs
make test          # uv run pytest -n auto (PYTEST_XDIST="-n 2" on a shared host, PYTEST_XDIST= for serial)
make lint          # ruff check libs/ tests/
make format        # ruff format libs/ tests/
make lint-imports  # import-linter layering contracts
make typecheck     # mypy --strict over libs/ and tests/ ([tool.mypy] files in pyproject.toml)
make sizecheck     # size limits via scripts/sizecheck.py (FILES=... to narrow)
make coverage      # pytest -n auto with coverage (fails under 60%); full suite ~6 min on 4 workers, ~20 min serial
make pre-commit-install / make pre-commit-run   # hooks: ruff, ruff-format, hygiene, hadolint, gitleaks
make docker-build / docker-up / docker-down / docker-logs / docker-shell
make help          # list every target
```

CI (`.github/workflows/ci.yml`) runs `lint` (ruff check + format), `static` (`make lint-imports`, `make typecheck`, `make sizecheck`), `hygiene` (pre-commit file hooks), hadolint, gitleaks and `make coverage`; `make check` is the local equivalent (pre-commit-run, then lint-imports, typecheck, sizecheck and coverage, stopping at the first failure) — run it before pushing. `make typecheck` is plain `uv run mypy`: strict mode over every module listed by `files` in `[tool.mypy]` (`pyproject.toml`; all of `libs/` and `tests/`, the frozen Alembic revisions excluded; tests are annotated like library code). `make sizecheck` runs `scripts/sizecheck.py` over `libs/`; deliberate exceptions live in `scripts/size-exceptions.txt`, one `<path>[::<qualname>] — <reason>` line each, and the register only shrinks.

**Dependency pins.** `uv.lock` is kept free of published advisories (`pip-audit`, run by `make audit` and the CI `deps` job; a newly published advisory turns it red until the lock is bumped). The root `[tool.uv] constraint-dependencies = ["mcp>=1.28.1,<2"]` pins a transitive package: the floor clears the mcp advisories, the ceiling exists because `fastapi-mcp` 0.4.0 constructs `mcp.server.lowlevel.Server(name, version)`, which mcp 2.x rejects (TypeError at mount) — lift it when `fastapi-mcp` supports mcp 2. Each workspace package declares every third-party package it imports (`deptry` from each `libs/greenhouse-*`, also in `make audit`, run through `uv run` so it sees the installed packages; the server's `[tool.deptry.per_rule_ignores]` keeps `jinja2` and `python-multipart`, runtime needs that are never imported by name).

### Tests

~3,300 tests. All use `tests/conftest.py` fixtures (`clean_env`, `frozen_clock`, …) and the placeholders in `tests/fake_data.py` / `tests/fake_devices.py` (fake device IDs like `fake_tuya_device_aabbccdd`, RFC 5737 IPs `192.0.2.x`, generic names). Server + web tests use FastAPI `TestClient` with in-memory SQLite. CLI tests use Typer `CliRunner` + `httpx.MockTransport`. TUI tests use Textual `Pilot` against the real app. Web tests assert on rendered HTML / fragment markers, not visual layout. No real network or hardware: an autouse fixture in `tests/conftest.py` blocks `urllib.request.urlopen` and every non-loopback socket connect, so a test that forgets a stub fails fast instead of reaching the internet (the weather client then falls back to 20 °C); stub weather with `install_offline_weather(app)`. Two more autouse fixtures keep the developer's machine out: the CLI token lookup is isolated (`GREENHOUSE_API_TOKEN` unset, `XDG_CONFIG_HOME` in a temp dir), and `Settings` ignores a local `.env` unless the test is marked `dotenv`. The server fixtures (`app`, `client`, …) live in `tests/server/fixtures.py`, registered as a global plugin by `tests/conftest.py` (`tests/server/conftest.py` only re-exports its helpers and constants).

Every test has a 300 s timeout (`timeout` in `[tool.pytest.ini_options]`, pytest-timeout): a hung test fails with a traceback instead of holding the CI job (the full suite takes ~9 min on a 2-core runner); the slowest golden test is ~40 s locally. Raise it per test with `@pytest.mark.timeout(n)` only with a reason.

```bash
uv run pytest tests/server/test_alerts.py::test_acknowledge -v   # one test
uv run pytest -n auto                                            # whole suite in parallel (pytest-xdist)
uv run pytest -n 2 tests/server                                  # one tree, fewer workers on a shared machine
```

Test paths from different directories can be mixed on one command line (`tests/server/X tests/<core file> tests/server/Y`); the global fixtures plugin keeps `client` resolvable, and `tests/test_refactor_guards.py` checks it in a subprocess.

Where tests go:

- core → `tests/test_*.py`; device adapters → `tests/devices/`
- JSON API → `tests/server/test_<resource>.py`
- Web pages / HX fragments / template filters → `tests/server/test_web_*.py`
- CLI → `tests/cli/test_cli.py` (gap commands: `tests/cli/test_completeness.py`)
- TUI → `tests/cli/test_tui.py` (seeded by `tests/cli/tui_fixtures.py`)
- Contract / characterization tests → `test_contract_*.py` in each tree; `tests/test_refactor_guards.py` holds cheap structural guards

**Golden tests.** `test_contract_*.py` pin current behavior against snapshots in `tests/golden/`: `contracts/` (OpenAPI, MCP tools, routes, DDL and migrated schema, settings schema, env reads, import surfaces, loggers, constants, scheduler jobs, package data), `engine/` (the decision grid driven by `tests/engine_grid.py`), `orchestration/`, `ingress_devices/`, `web/` (rendered HTML), `cli/` (help, requests, JSON output) and `tui/` (renders, actuation, ids/selectors, sprites). Comparison is strict equality, except public import surfaces, `constants.py` values and logger names, which are supersets (additions allowed; every golden name must still resolve with an equal value); enum members stay strict. `PHASE0_OPENAPI_SHA256` / `PHASE0_MCP_TOOLS_SHA256` in `tests/server/test_contract_{openapi,mcp}.py` fingerprint the API contract.

**Golden policy.** `GOLDEN_UPDATE=1 uv run pytest <test>` rewrites goldens. Use it only in a commit that intentionally changes the pinned behavior (a labeled `fix(...)` commit) or description text (`docs(api)` / `docs(cli)`), then review the golden diff and re-record the fingerprint so only the intended lines move. Never regenerate a golden to make a refactor pass. Goldens are byte-exact (`tests/golden/` is excluded from the whitespace fixers) and stay < 400 KB each; a Textual / plotext / rich bump regenerates the TUI render goldens in its own commit.

**Determinism kit** (`tests/golden.py`): `FROZEN_INSTANT` (2026-04-15 10:00 UTC, outside the seeded quiet hours) / `FROZEN_TS`, `OfflineWeather` + `install_offline_weather(app)`, `ENV_PREFIXES` (`IRRIGATION_`, `GREENHOUSE_`, `TUYA_` — cleared by the `clean_env` fixture), `to_canonical_json`, `assert_golden` / `assert_golden_json`.

### Adding a plant species

1. Research with at least 2 sources, then update `libs/greenhouse-core/greenhouse_core/data/plant_database.json`.
2. Apply with `greenhouse plant sync`.

## Bundled plugin — keep the skill docs in sync

The repo ships a Claude Code plugin under `plugin/` (marketplace entry in `.claude-plugin/marketplace.json`): a `greenhouse` skill plus an MCP client (`plugin/.mcp.json`) that points at a running server's `/mcp`. The MCP **tool schemas auto-derive from the OpenAPI spec** — no manual upkeep. The **hand-written skill docs do not**, and silently drift:

- `plugin/skills/greenhouse/SKILL.md` — capabilities overview + trigger guidance
- `plugin/skills/greenhouse/references/CLI.md` — CLI command reference
- `plugin/skills/greenhouse/references/LOGIC.md` — decision-engine behavior
- `plugin/skills/greenhouse/references/PLANT_DATABASE.md` — plant-DB schema/fields
- `plugin/.claude-plugin/plugin.json` — plugin description

**Invariant: any change to the surfaces below MUST update the matching plugin doc in the same PR** — treat it like updating a test, not optional follow-up:

| You change… | Update… |
|---|---|
| CLI commands / sub-apps / flags, TUI keys and dialogs (`libs/greenhouse-cli`) | `references/CLI.md` |
| decision engine / `logic/` / `learning/` / `constants.py` | `references/LOGIC.md` |
| `data/plant_database.json` or `plant_db.py` fields | `references/PLANT_DATABASE.md` |
| new/changed `/api/v1` capabilities (hence MCP tools), or a changed refusal / status code | `SKILL.md` (+ `plugin.json` description when a capability is added or removed) |

## Privacy

**Never commit** device IDs, IP addresses, local keys, API credentials, database files, or personal configs. Live data lives in `data/*.db` (gitignored) and `.env` (outside repo). Test data uses the fakes in `tests/fake_data.py`.

Pre-commit sanity:

```bash
git grep -i "bf60\|192.168\|local_key\|api.*key" -- '*.py' '*.md' '*.json'
```

## Releases — project-specific facts only

Releases are automated by release-please (see the `/release-please` skill for the workflow). Commit conventions follow the `/conventional-commits` skill.

What's specific to this repo:

- **`.release-please-manifest.json` is canonical.** The four `pyproject.toml` files (root + `libs/greenhouse-{core,server,cli}/pyproject.toml`) mirror it via `extra-files` in `release-please-config.json` — release-please overwrites them on every release. Do not hand-edit versions; drift will confuse `uv`.
- **`CHANGELOG.md` is generated** — do not hand-edit.
- **`uv.lock` is bumped through `extra-files`.** The lock records each workspace package's own version, so `release-please-config.json` has one `toml` entry per `[[package]]` (`greenhouse`, `-core`, `-server`, `-cli`) with the jsonpath `$.package[?(@.name.value=='<name>')].version`. The `.value` is required: release-please's TOML parser wraps strings, so `@.name=='x'` matches nothing. Add an entry when a workspace package is added; a positional path (`$.package[0]`) would break when dependencies change.
- **`group-pull-request-title-pattern` is the load-bearing release-please key — it MUST include `${version}`.** Because `separate-pull-requests: false`, release-please routes the combined release PR through its **Merge plugin**, whose title comes from `group-pull-request-title-pattern`, **not** `pull-request-title-pattern` (that key is ignored in grouped mode — set both to the same value to avoid confusion). With it unset, the Merge plugin falls back to the hardcoded default `chore: release ${branch}` → `chore: release main` (no version); on merge, release-please finds the PR by the `autorelease: pending` label but parses the *version from the title*, gets nothing, and **silently skips tagging** — no `vX.Y.Z` tag, CD never fires. This is upstream [release-please#2712](https://github.com/googleapis/release-please/issues/2712). It silently broke v2.1.0 and v3.0.0 (both hand-recovered with `git tag … && gh release create … && gh pr edit --add-label "autorelease: tagged"`); #16 and #24 both mis-diagnosed it as `pull-request-title-pattern`. Do not remove either pattern.
- **The repo is COMPONENT-LESS — the root package `.` sets no `component` and no `package-name`. Do not add them.** greenhouse is one product, one version, one bare `vX.Y.Z` tag. If `package-name` is set, release-please derives a component (`greenhouse`) from it; with `include-component-in-tag: false` the tags carry no component, so the two disagree: discovery matches releases to the path *by component* and finds **zero** prior releases (`⚠ Expected 1 releases, only found 0` → `No latest release found … Set(0)`), and the tagging phase rejects the PR (`⚠ PR component: undefined does not match configured component: greenhouse` → tags 0) — release-please aborts, no tag, CD never fires. This (not the title pattern) is why v3.0.1 **and** v3.0.2 needed hand-recovery. Leaving the component empty everywhere makes both phases agree → the next release self-tags. Because there's no `package-name`, the root `pyproject.toml` is bumped via `extra-files` (all four pyprojects are listed). Do **not** "fix" this by *adding* components per dir (code/docs/tests) — that's the monorepo model and would produce prefixed tags (`code-vX.Y.Z`) that break `cd.yml`'s `v*` trigger. (A real monorepo like `interviewer` instead sets `include-component-in-tag: true` + explicit components so tags like `api-vX.Y.Z` carry the matching component — the opposite, also-consistent end of the spectrum.)
- The `vX.Y.Z` tag created by release-please triggers `.github/workflows/cd.yml`: Docker build → GHCR push, cosign signing, SBOM, Trivy scan.
- Hotfixing an older release while `main` has moved on: [`docs/runbook-release-maintenance.md`](docs/runbook-release-maintenance.md).

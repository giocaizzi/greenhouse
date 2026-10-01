# 00 — Frozen external contracts (Phase 0, read-only)

Baseline: `main` @ `a1b2622` (branch `claude/focused-hawking-7to7o3`). Runtime: Python 3.11, fastapi 0.141.1, starlette 1.0.0,
fastapi-mcp 0.4.0, pydantic 2.13.4, pydantic-settings 2.14.1, SQLAlchemy 2.0.52, alembic 1.18.4, APScheduler 3.11.2,
typer 0.27.1 (vendors click as `typer._click`), textual 8.2.8.

Path shorthands: `s/` = `libs/greenhouse-server/greenhouse_server/`, `core/` = `libs/greenhouse-core/greenhouse_core/`,
`cli/` = `libs/greenhouse-cli/greenhouse_cli/`.

All tables below were **generated programmatically** (not hand-typed) by the scripts in the scratchpad
(`/tmp/claude-0/-home-user-greenhouse/13425f2d-7c47-5b83-96d4-7045d3907ad2/scratchpad/`):
`dump_routes.py` (REST/MCP/hidden routes + `openapi.json`), `mcp_hash.py` (`mcp_tools.json`), `dump_web.py` (AST over
`s/web/routes/*`), `dump_cli.py` + `dump_help.py` (`cli_help.txt`), `dump_tui.py`, `dump_core.py` (Settings, ORM DDL
`orm_ddl.sql`, public import surfaces), `probe_hdr.py` (auth header probe). Re-run any of them with
`cd /home/user/greenhouse && uv run python <script>`; they are deterministic (verified by double run).

## Headline counts

| Surface | Count | Baseline fingerprint |
|---|---|---|
| `/api/v1` OpenAPI paths / operations | **66 paths / 84 operations** (+0 non-`/api/v1` paths in schema) | `sha256(json.dumps(app.openapi(), sort_keys=True))` = `f983a5a8d42e60215a6e9571daccc30b635e34271879f75b3e8c46154ae7f0ee`; 101 component schemas |
| MCP tools | **84** (== the 84 operationIds, 1:1, no include/exclude filters) | `sha256` of sorted `mcp.tools` dump = `dc15b6abe916468a72845fc1322e18633d7388bfd8b9d1c0166256626df9b2a0` |
| Web UI routes (`include_in_schema=False`) | **85** (82 protected + 3 login/logout) | — (needs HTML goldens) |
| Other hidden routes | 2 `/.well-known/*`, `/mcp` (GET/POST/DELETE), `/static` mount, FastAPI `/openapi.json` `/docs` `/docs/oauth2-redirect` `/redoc` | — |
| CLI | **13 groups** (root + 12 sub-apps incl. nested `config global`), **61 leaf commands**, 74 `--help` pages | `sha256(cli_help.txt)` (COLUMNS=120, NO_COLOR) = `f17c4eb1ff96d45b35386fbaebe1d1fb8ffddc4a66386acaf8d4bd9f0f81430f` |
| TUI | 1 App, 5 modes, 21 DOM classes, **59 key bindings** | — |
| Env vars | **26 `Settings` fields** (26 env names) + **8 non-Settings env reads** = 34 | — |
| DB tables | **17** tables, 17 mapped classes, Alembic head **`9f2b5e7c6a31`** | `sha256(orm_ddl.sql)` (SQLite `CREATE TABLE`/`CREATE INDEX` from `Base.metadata`) = `7b6a2d72054011a900438ae7e7edd471acbffc6c6a2f2d60cc1583973fb36f2b` |
| `TriggerCode` members | **46** | — |
| `constants.py` public names | **100** | — |
| `greenhouse_core.schemas` public classes | **100** | — |

Pin legend: **[T: name]** = already pinned by existing test `name`; **[G]** = *needs golden snapshot in Phase 1*;
**[T+G]** = partly pinned, golden recommended.

---

## 1. REST `/api/v1`

App factory: `s/app.py:123 create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI`.
Load-bearing app settings (all **[G]** via OpenAPI golden):

- `title="Greenhouse API"`, `version="1.0.0"`, description string (`s/app.py:142-148`), `openapi_tags` 14 entries in fixed order
  (clusters, plants, irrigators, sensors, configs, operations, scheduler, alerts, activity, decisions, preferences, vacation,
  search, bulk — `s/app.py:150-165`). Tags `auth`, `charts`, `windows` are used by routes but not declared in `openapi_tags`.
- `generate_unique_id_function=lambda route: route.name` (`s/app.py:166`) — **this is why operationId == route function
  name == MCP tool name.** Renaming any route function changes OpenAPI + MCP.
- Router include **order** (`s/app.py:199-222`) is part of the OpenAPI `paths` ordering and route matching:
  auth (unprotected) → clusters, plants, irrigators, sensors, configs, operations, scheduler, charts, alerts, activity,
  decisions, forecast, preferences, vacation, search, bulk, insights, health, quality, efficacy, windows — all with
  `dependencies=[Depends(require_user)]`; then `well_known.router` (root, unprotected), `/static` mount, `web_router`,
  `register_web_exception_handlers`, then `FastApiMCP(...).mount_http()`.
- Only `/api/v1/auth/login` (and `/logout`, `/me` which do their own checks) sit outside `require_user`.
- `stats_export` (`GET /api/v1/clusters/{cluster_id}/stats/export`) has `response_model=None` and returns a `text/csv`
  `StreamingResponse` with `Content-Disposition: attachment; filename=cluster_{id}_stats.csv`; it is the documented exemption in
  `tests/server/test_mcp.py`.

Pins: **[T: test_mcp_exposes_every_api_operation_as_a_tool, test_mcp_every_api_route_has_a_response_model,
test_every_api_route_has_a_docstring, test_mcp_request_bodies_carry_their_field_schemas, test_mcp_does_not_leak_web_routes]**
+ per-resource tests in `tests/server/test_<resource>.py`. Full schema (field order, aliases, descriptions, docstrings)
is **[G]**: snapshot `app.openapi()` as sorted JSON (the scratchpad `openapi.json` is that snapshot at baseline).

### 1.1 Route table (84 operations, generated from `app.routes` effective contexts)

Status = declared success status. All responses are `JSONResponse` except `stats_export` (CSV). No route declares
extra `responses=`.

| method | path | operationId (fn) | response_model | status | tags | source |
|---|---|---|---|---|---|---|
| POST | `/api/v1/auth/login` | `login` | `LoginResponse` | 200 | auth | s/routes/auth.py:48 |
| POST | `/api/v1/auth/logout` | `logout` | `LogoutResponse` | 200 | auth | s/routes/auth.py:98 |
| GET | `/api/v1/auth/me` | `whoami` | `WhoAmIResponse` | 200 | auth | s/routes/auth.py:116 |
| POST | `/api/v1/clusters` | `create_cluster` | `ClusterResponse` | 201 | clusters | s/routes/clusters.py:22 |
| GET | `/api/v1/clusters` | `list_clusters` | `list[ClusterResponse]` | 200 | clusters | s/routes/clusters.py:41 |
| GET | `/api/v1/clusters/{cluster_id}` | `get_cluster` | `ClusterResponse` | 200 | clusters | s/routes/clusters.py:47 |
| GET | `/api/v1/clusters/{cluster_id}/detail` | `get_cluster_detail` | `ClusterDetailResponse` | 200 | clusters | s/routes/clusters.py:65 |
| PUT | `/api/v1/clusters/{cluster_id}` | `update_cluster` | `ClusterResponse` | 200 | clusters | s/routes/clusters.py:114 |
| DELETE | `/api/v1/clusters/{cluster_id}` | `delete_cluster` | `SuccessResponse` | 200 | clusters | s/routes/clusters.py:139 |
| GET | `/api/v1/plants` | `list_all_plants` | `PlantListResponse` | 200 | plants | s/routes/plants.py:23 |
| POST | `/api/v1/clusters/{cluster_id}/plants` | `add_plant` | `PlantResponse` | 201 | plants | s/routes/plants.py:61 |
| GET | `/api/v1/clusters/{cluster_id}/plants` | `list_plants` | `list[PlantResponse]` | 200 | plants | s/routes/plants.py:97 |
| PUT | `/api/v1/clusters/{cluster_id}/plants/{plant_id}` | `update_plant` | `PlantResponse` | 200 | plants | s/routes/plants.py:107 |
| DELETE | `/api/v1/clusters/{cluster_id}/plants/{plant_id}` | `delete_plant` | `SuccessResponse` | 200 | plants | s/routes/plants.py:134 |
| POST | `/api/v1/plants/{plant_id}/move` | `move_plant` | `PlantResponse` | 200 | plants | s/routes/plants.py:160 |
| POST | `/api/v1/plants/sync` | `sync_plants` | `SyncPlantsResponse` | 200 | plants | s/routes/plants.py:198 |
| GET | `/api/v1/plants/{plant_id}/health` | `get_plant_health` | `PlantHealthResponse` | 200 | plants | s/routes/plants.py:257 |
| POST | `/api/v1/plants/health/snapshot` | `trigger_health_snapshot` | `SnapshotResponse` | 200 | plants | s/routes/plants.py:288 |
| GET | `/api/v1/irrigators` | `list_all_irrigators` | `IrrigatorListResponse` | 200 | irrigators | s/routes/irrigators.py:29 |
| POST | `/api/v1/clusters/{cluster_id}/irrigator` | `add_irrigator` | `IrrigatorResponse` | 201 | irrigators | s/routes/irrigators.py:56 |
| GET | `/api/v1/clusters/{cluster_id}/irrigator` | `get_irrigator` | `IrrigatorResponse` | 200 | irrigators | s/routes/irrigators.py:108 |
| PUT | `/api/v1/clusters/{cluster_id}/irrigator` | `update_irrigator` | `IrrigatorResponse` | 200 | irrigators | s/routes/irrigators.py:131 |
| DELETE | `/api/v1/clusters/{cluster_id}/irrigator` | `delete_irrigator` | `SuccessResponse` | 200 | irrigators | s/routes/irrigators.py:161 |
| POST | `/api/v1/irrigators/{irrigator_id}/start` | `start_irrigator` | `IrrigatorActionResponse` | 200 | irrigators | s/routes/irrigators.py:188 |
| POST | `/api/v1/irrigators/{irrigator_id}/stop` | `stop_irrigator` | `IrrigatorActionResponse` | 200 | irrigators | s/routes/irrigators.py:221 |
| POST | `/api/v1/irrigators/{irrigator_id}/log-manual` | `log_manual` | `LogManualResponse` | 200 | irrigators | s/routes/irrigators.py:248 |
| GET | `/api/v1/sensors` | `list_all_sensors` | `SensorListResponse` | 200 | sensors | s/routes/sensors.py:20 |
| POST | `/api/v1/clusters/{cluster_id}/sensors` | `add_sensor` | `SensorResponse` | 201 | sensors | s/routes/sensors.py:47 |
| GET | `/api/v1/clusters/{cluster_id}/sensors` | `list_sensors` | `list[SensorResponse]` | 200 | sensors | s/routes/sensors.py:88 |
| GET | `/api/v1/clusters/{cluster_id}/sensors/{sensor_id}` | `get_sensor` | `SensorResponse` | 200 | sensors | s/routes/sensors.py:98 |
| PUT | `/api/v1/clusters/{cluster_id}/sensors/{sensor_id}` | `update_sensor` | `SensorResponse` | 200 | sensors | s/routes/sensors.py:119 |
| GET | `/api/v1/sensors/{sensor_id}/assignments` | `list_sensor_assignments` | `SensorAssignmentListResponse` | 200 | sensors | s/routes/sensors.py:147 |
| DELETE | `/api/v1/clusters/{cluster_id}/sensors/{sensor_id}` | `delete_sensor` | `SuccessResponse` | 200 | sensors | s/routes/sensors.py:179 |
| PUT | `/api/v1/clusters/{cluster_id}/config` | `set_config` | `ConfigResponse` | 200 | configs | s/routes/configs.py:26 |
| GET | `/api/v1/clusters/{cluster_id}/config` | `get_config` | `ConfigResponse` | 200 | configs | s/routes/configs.py:54 |
| GET | `/api/v1/clusters/{cluster_id}/config/effective` | `get_effective_config` | `EffectiveConfigResponse` | 200 | configs | s/routes/configs.py:73 |
| GET | `/api/v1/config/global` | `get_global_config` | `GlobalConfigResponse` | 200 | configs | s/routes/configs.py:104 |
| PUT | `/api/v1/config/global` | `update_global_config` | `GlobalConfigResponse` | 200 | configs | s/routes/configs.py:115 |
| GET | `/api/v1/clusters/{cluster_id}/status` | `cluster_status` | `ClusterStatusResponse` | 200 | operations | s/routes/operations.py:43 |
| POST | `/api/v1/clusters/{cluster_id}/irrigate` | `irrigate` | `IrrigateResponse` | 200 | operations | s/routes/operations.py:109 |
| GET | `/api/v1/clusters/{cluster_id}/monitor` | `monitor` | `MonitorResponse` | 200 | operations | s/routes/operations.py:155 |
| POST | `/api/v1/check` | `check_all` | `CheckAllResponse` | 200 | operations | s/routes/operations.py:183 |
| POST | `/api/v1/clusters/{cluster_id}/check` | `check_single` | `CheckClusterResponse` | 200 | operations | s/routes/operations.py:208 |
| POST | `/api/v1/sync` | `sync` | `SyncResponse` | 200 | operations | s/routes/operations.py:232 |
| GET | `/api/v1/clusters/{cluster_id}/learn` | `learn` | `LearnResponse` | 200 | operations | s/routes/operations.py:258 |
| GET | `/api/v1/clusters/{cluster_id}/history` | `history` | `HistoryResponse` | 200 | operations | s/routes/operations.py:278 |
| GET | `/api/v1/clusters/{cluster_id}/stats` | `stats` | `StatsResponse` | 200 | operations | s/routes/operations.py:319 |
| GET | `/api/v1/clusters/{cluster_id}/stats/export` | `stats_export` | `None` | 200 | operations | s/routes/operations.py:339 |
| GET | `/api/v1/health` | `health` | `HealthResponse` | 200 | scheduler | s/routes/scheduler.py:18 |
| GET | `/api/v1/scheduler/jobs` | `list_jobs` | `list[SchedulerJobResponse]` | 200 | scheduler | s/routes/scheduler.py:33 |
| DELETE | `/api/v1/scheduler/jobs/{job_id}` | `delete_job` | `SuccessResponse` | 200 | scheduler | s/routes/scheduler.py:47 |
| POST | `/api/v1/scheduler/pause` | `pause_scheduler` | `SchedulerStateResponse` | 200 | scheduler | s/routes/scheduler.py:90 |
| POST | `/api/v1/scheduler/resume` | `resume_scheduler` | `SchedulerStateResponse` | 200 | scheduler | s/routes/scheduler.py:109 |
| GET | `/api/v1/plants/{plant_id}` | `get_plant` | `PlantResponse` | 200 | charts | s/routes/charts.py:27 |
| GET | `/api/v1/plants/{plant_id}/chart-data` | `plant_chart_data` | `ChartPayloadResponse` | 200 | charts | s/routes/charts.py:43 |
| GET | `/api/v1/clusters/{cluster_id}/chart-data` | `cluster_chart_data` | `ChartPayloadResponse` | 200 | charts | s/routes/charts.py:73 |
| GET | `/api/v1/clusters/{cluster_id}/overlay` | `cluster_overlay` | `MultiMetricOverlayResponse` | 200 | charts | s/routes/charts.py:100 |
| GET | `/api/v1/clusters/{cluster_id}/heatmap` | `cluster_heatmap` | `HeatmapResponse` | 200 | charts | s/routes/charts.py:128 |
| GET | `/api/v1/plants/{plant_id}/health-timeline` | `plant_health_timeline` | `PlantHealthTimelineResponse` | 200 | charts | s/routes/charts.py:156 |
| GET | `/api/v1/alerts` | `list_alerts` | `AlertListResponse` | 200 | alerts | s/routes/alerts.py:12 |
| GET | `/api/v1/alerts/{alert_id}` | `get_alert` | `AlertSummary` | 200 | alerts | s/routes/alerts.py:53 |
| POST | `/api/v1/alerts/{alert_id}/acknowledge` | `acknowledge_alert` | `AlertSummary` | 200 | alerts | s/routes/alerts.py:72 |
| POST | `/api/v1/alerts/{alert_id}/resolve` | `resolve_alert` | `AlertSummary` | 200 | alerts | s/routes/alerts.py:94 |
| POST | `/api/v1/clusters/{cluster_id}/alerts/sync` | `refresh_cluster_alerts` | `AlertListResponse` | 200 | alerts | s/routes/alerts.py:114 |
| POST | `/api/v1/alerts/sync` | `refresh_all_alerts` | `AlertListResponse` | 200 | alerts | s/routes/alerts.py:146 |
| GET | `/api/v1/activity` | `list_activity` | `ActivityListResponse` | 200 | activity | s/routes/activity.py:11 |
| GET | `/api/v1/clusters/{cluster_id}/decisions` | `list_decisions` | `DecisionLogListResponse` | 200 | decisions | s/routes/decisions.py:11 |
| GET | `/api/v1/clusters/{cluster_id}/forecast` | `get_cluster_forecast` | `ForecastResponse` | 200 | operations | s/routes/forecast.py:25 |
| GET | `/api/v1/preferences` | `get_preferences` | `PreferencesResponse` | 200 | preferences | s/routes/preferences.py:12 |
| PUT | `/api/v1/preferences` | `update_preferences` | `PreferencesResponse` | 200 | preferences | s/routes/preferences.py:25 |
| GET | `/api/v1/vacation` | `list_vacation_windows` | `VacationListResponse` | 200 | vacation | s/routes/vacation.py:17 |
| POST | `/api/v1/vacation` | `create_vacation_window` | `VacationResponse` | 201 | vacation | s/routes/vacation.py:30 |
| PUT | `/api/v1/vacation/{window_id}` | `update_vacation_window` | `VacationResponse` | 200 | vacation | s/routes/vacation.py:62 |
| DELETE | `/api/v1/vacation/{window_id}` | `delete_vacation_window` | `SuccessResponse` | 200 | vacation | s/routes/vacation.py:96 |
| GET | `/api/v1/search` | `global_search` | `SearchResponse` | 200 | search | s/routes/search.py:12 |
| POST | `/api/v1/bulk/stop-all` | `bulk_stop_all` | `StopAllResponse` | 200 | bulk | s/routes/bulk.py:13 |
| GET | `/api/v1/clusters/{cluster_id}/insights` | `cluster_insights` | `ClusterInsightsResponse` | 200 | operations | s/routes/insights.py:12 |
| GET | `/api/v1/health/system` | `system_health` | `SystemHealthResponse` | 200 | scheduler | s/routes/health.py:12 |
| GET | `/api/v1/quality/report` | `quality_report` | `DataQualityReport` | 200 | operations | s/routes/quality.py:12 |
| GET | `/api/v1/clusters/{cluster_id}/efficacy` | `cluster_efficacy` | `EfficacyListResponse` | 200 | operations | s/routes/efficacy.py:12 |
| GET | `/api/v1/clusters/{cluster_id}/windows` | `list_windows` | `IrrigationWindowListResponse` | 200 | windows | s/routes/windows.py:36 |
| POST | `/api/v1/clusters/{cluster_id}/windows` | `add_window` | `IrrigationWindowResponse` | 201 | windows | s/routes/windows.py:63 |
| PUT | `/api/v1/clusters/{cluster_id}/windows/{window_id}` | `update_window` | `IrrigationWindowResponse` | 200 | windows | s/routes/windows.py:101 |
| DELETE | `/api/v1/clusters/{cluster_id}/windows/{window_id}` | `delete_window` | `SuccessResponse` | 200 | windows | s/routes/windows.py:135 |

### 1.2 Error contracts (`HTTPException` status/detail by route group) — **[T+G]**

Shared:
- `s/deps.py:79 require_cluster` → `404 "Cluster not found"` (used by many routes).
- Auth (`s/auth.py`): `AuthError` (`:65`) → `401`, default detail `"Not authenticated"`; variants `"Session expired"`,
  `"Invalid session"` (`:128-130`), `"Malformed session"` (`:169`), `"User no longer active"` (`:172`).
  `AuthConfigError` (`:76`) → `503 "Auth not configured"` / `"auth_secret_key is not set"` (`:97`).
  `require_user` (`:188`) accepts session JWT (bearer header first, then cookie `settings.auth_cookie_name`) **or** the MCP
  token (constant-time compare) → synthetic principal `MCP_USER_ID=-2/"mcp"`; with `auth_enabled=False` returns
  `SYSTEM_USER_ID=-1/"system"`. JWT: `HS256`, audience `"greenhouse-session"`.
  **[T: test_anonymous_api_returns_401, test_invalid_bearer_token_returns_401, test_missing_secret_key_returns_503_on_login,
  test_auth_disabled_grants_anonymous_access, test_mcp_tool_invocation_reaches_inner_endpoint]**
- `POST /api/v1/auth/login` bad creds → `401 "Invalid username or password"` (`s/routes/auth.py:81-83`).
- Validation errors → FastAPI default `422 {"detail": [...]}` on `/api/*` and `/mcp*`.
- Global exception handler (`s/web/exception_handlers.py:32`): for `/api/*` and `/mcp*` paths returns
  `JSONResponse({"detail": exc.detail}, status_code=exc.status_code)`; everything else renders `_error.html` (HX) or
  `error_page.html`. `_RedirectAuthError` (303 sentinel) is converted to `303 Location: /login?next=<quoted path?query>`
  or, for `HX-Request: true`, `204` + `HX-Redirect: /login?next=…` (`s/auth.py:260-277`) — on *any* path.
  **[T: test_anonymous_web_redirects_to_login, test_hx_request_unauthenticated_returns_hx_redirect]**

Per group (file:line of the `raise`):

| Group | Status → detail |
|---|---|
| clusters (`s/routes/clusters.py:134,157`) | 404 `Cluster not found` |
| plants (`s/routes/plants.py`) | 404 `Plant not found in cluster` (128,154); 404 `Plant not found` (187,277); 404 `Target cluster not found` (189); 400 `str(SameClusterMoveError)` (193); 404 `f"Plant {id} not found"` (232) |
| irrigators (`s/routes/irrigators.py`) | 409 `Cluster already has an irrigator` (101); 409 `Device ID already exists` (104); 404 `Cluster has no irrigator` (127,155,182); 404 `Irrigator not found` (213,240,267); start/stop/log-manual re-raise `ManualActionError(status, detail)` (217,244,271) |
| manual control (`s/services/manual_control.py`) | 409 `cluster max_events_per_day reached` (67); 409 `irrigator daily cap reached` (73); 503 `No device registry (missing Tuya credentials)` (78,115); 503 `str(exc)` (82); 502 adapter output (121,175) |
| sensors (`s/routes/sensors.py`) | 404 `f"Plant {id} not found in cluster"` (70); 409 `Device ID already exists` (83); 404 `Sensor not found in cluster` (115,141,199); 404 `Sensor not found` (171) |
| configs (`s/routes/configs.py:69`) | 404 `No config set for cluster` |
| operations (`s/routes/operations.py:58,150,297`) | 404 `Cluster not found` |
| charts (`s/routes/charts.py`) | 404 `Plant not found` (39,69,178); 400 `f"Unsupported metric: {metric}"` (66,93); 404 `Cluster not found` (96,124,152) |
| alerts (`s/routes/alerts.py`) | 404 `Alert not found` (68,89,109); 404 `Cluster not found` (139) |
| scheduler (`s/routes/scheduler.py`) | 409 `str(CoreJobError)` (74) = `"Job {id} is a built-in scheduler job and cannot be deleted. To stop automatic irrigation checks use POST /api/v1/scheduler/pause (resume with POST /api/v1/scheduler/resume)."` (`s/scheduler.py:504-507`); 404 `"Job {id} not found"` (76,84); 500 `f"Failed to {verb} job: {exc}"` (87) |
| vacation (`s/routes/vacation.py`) | 404 `Vacation window not found` (86,111); 400 `starts_at must be < ends_at` (90) |
| windows (`s/routes/windows.py`) | 400 `start_hour and end_hour must be 0..23` (26); 400 `start_hour and end_hour must differ` (28); 400 `weekday_mask must be 1..127 (Mon=1, Sun=64)` (33); 404 `Window not found in cluster` (123,156) |
| insights (`s/routes/insights.py:35`) | 404 `Cluster not found` |
| well-known (`s/routes/well_known.py:46-66`) | `/.well-known/oauth-authorization-server` → 404 `{"error":"not_an_authorization_server","error_description":"greenhouse uses static bearer auth; see .well-known/oauth-protected-resource"}`; `/.well-known/oauth-protected-resource` → 200 `{resource: "<scheme>://<netloc>/mcp", authorization_servers: [], bearer_methods_supported: ["header"], resource_name: "greenhouse"}` **[T: tests/server/test_well_known.py (4 tests)]** |

**Observed bug (not fixed — orchestrator please record in `REFACTOR_NOTES.md` and pin):** the JSON branch of the global
`HTTPException` handler (`s/web/exception_handlers.py:41-44`) drops `exc.headers`, so the `WWW-Authenticate: Bearer`
header set by `AuthError` (`s/auth.py:72`) and by `require_mcp_token` (`s/app.py:119`) never reaches the client.
Verified by `probe_hdr.py`: `GET /api/v1/clusters` → `401 {"detail":"Not authenticated"}`, `www-authenticate: None`;
`GET /mcp` (token configured) → `401 {"detail":"Invalid MCP token"}`, `www-authenticate: None`. Characterize as-is.

---

## 2. MCP (`/mcp`) — `s/app.py:236-252`

- `FastApiMCP(app, name="greenhouse", description="Smart plant irrigation system — manage clusters, plants, sensors, irrigators, configs; run smart-irrigation decisions; read history, stats, and learning reports.", auth_config=AuthConfig(dependencies=[Depends(require_mcp_token)]))`, then `mcp.mount_http()` (streamable HTTP at `/mcp`, methods GET/POST/DELETE, endpoint `fastapi_mcp.server.handle_mcp_streamable_http`), `app.state.mcp = mcp`.
- No `include_operations/exclude_operations/include_tags/exclude_tags`; `describe_all_responses=False`, `describe_full_response_schema=False`. Exclusion of web routes is purely via `include_in_schema=False` on `web_router` (`s/web/router.py:36`) and `well_known.router`.
- Tool name = operationId = route function name; tool description = route docstring (+ fastapi-mcp formatting); input schema = path/query params + request body schema.
- `require_mcp_token` (`s/app.py:97`): `settings.mcp_token is None` → `503 "MCP auth not configured"`; missing/wrong bearer → `401 "Invalid MCP token"` (header `WWW-Authenticate` set but dropped, see §1.2). Settings read from `request.app.state.settings` per request (`_get_settings`, `s/app.py:92`). `_mcp_bearer = HTTPBearer(auto_error=False)` (`:89`).
- Tool calls reuse `/api/v1` routes internally, forwarding the `Authorization` header; `require_user` accepts the MCP token there.
- Tool list (84): acknowledge_alert, add_irrigator, add_plant, add_sensor, add_window, bulk_stop_all, check_all, check_single, cluster_chart_data, cluster_efficacy, cluster_heatmap, cluster_insights, cluster_overlay, cluster_status, create_cluster, create_vacation_window, delete_cluster, delete_irrigator, delete_job, delete_plant, delete_sensor, delete_vacation_window, delete_window, get_alert, get_cluster, get_cluster_detail, get_cluster_forecast, get_config, get_effective_config, get_global_config, get_irrigator, get_plant, get_plant_health, get_preferences, get_sensor, global_search, health, history, irrigate, learn, list_activity, list_alerts, list_all_irrigators, list_all_plants, list_all_sensors, list_clusters, list_decisions, list_jobs, list_plants, list_sensor_assignments, list_sensors, list_vacation_windows, list_windows, log_manual, login, logout, monitor, move_plant, pause_scheduler, plant_chart_data, plant_health_timeline, quality_report, refresh_all_alerts, refresh_cluster_alerts, resolve_alert, resume_scheduler, set_config, start_irrigator, stats, stats_export, stop_irrigator, sync, sync_plants, system_health, trigger_health_snapshot, update_cluster, update_global_config, update_irrigator, update_plant, update_preferences, update_sensor, update_vacation_window, update_window, whoami.

Pins: **[T: test_mcp_endpoint_is_mounted, test_mcp_does_not_leak_web_routes, test_mcp_exposes_every_api_operation_as_a_tool,
test_mcp_tool_names_fit_within_64_chars, test_mcp_rejects_request_without_authorization_header,
test_mcp_rejects_request_with_wrong_token, test_mcp_accepts_request_with_correct_token,
test_mcp_returns_503_when_token_setting_is_unset, test_mcp_tool_invocation_reaches_inner_endpoint,
test_mcp_tool_invocation_rejects_garbage_bearer]**. Tool names+descriptions+input schemas: **[G]** (snapshot `mcp_tools.json`).

---

## 3. Web UI (`/`) — 85 routes, `s/web/routes/*`

Assembly: `s/web/router.py` — `web_router = APIRouter(include_in_schema=False)`; `web_auth.router` first (no auth), then 19
sub-routers **in this order** with `dependencies=[Depends(require_web_user)]`: activity, alerts, analytics, clusters, configs,
decisions, efficacy, fragments, health_page, irrigators, operations, pages, plants, plant_dashboard, preferences, quality,
sensors, vacation, windows. Order matters for path matching (e.g. `/clusters/new` before `/clusters/{cluster_id}` lives in
the same file, `clusters.py`). Web routes share paths with nothing under `/api/v1`; several mirror API paths at root
(`/clusters`, `/alerts`, `/check`, `/sync`, …).

Template plumbing (frozen): `s/web/templating.py` — `Jinja2Templates(directory=s/web/templates)` + filters from
`s/web/filters.py ALL_FILTERS`: `format_ts, age_seconds, moisture_badge, severity_class, decision_badge, format_minutes,
yesno, strip_emoji, stat_position, icon_for_code, cluster_caps`. Every `TemplateResponse` context is built by
`s/web/context.py:98 base_context(request, **extra)` which always supplies: `request, is_hx, now_text, dry_run_global,
active_vacation, scheduler_paused, theme, app_version, auth_enabled, show_chrome` (extra keys override). `is_hx` =
`HX-Request: true`. `APP_VERSION` = `importlib.metadata.version("greenhouse-server")`. 57 template files, 6 static files.

Pins: existing web tests assert HTML markers/fragments (**[T: tests/server/test_web_*.py — 35 files]**, notably
`test_web_redirects.py` (5), `test_auth.py` login flow, `test_web_fragments.py`, `test_web_nav.py`). Template names and
context keys are not asserted directly → **[G]**: Phase 1 should snapshot, per route below, (template name, sorted context
keys) via a `TemplateResponse` spy, plus normalized rendered HTML for representative seeded pages.

Generated per-route detail (AST; "via X" = found in helper `X` in the same module; `params` are `Form`/`Query` defaults):


#### GET `/activity` — `activity_list` (web/routes/activity.py:39)
- params: entity_type=Query(), source=Query(), severity=Query()
- template: activity/list.html
- base_context keys: items, next_cursor, entity_type, source, severity

#### GET `/activity/page` — `activity_page` (web/routes/activity.py:62)
- params: before=Query(), entity_type=Query(), source=Query(), severity=Query()
- template: partials/_activity_rows.html
- base_context keys: items, next_cursor, entity_type, source, severity

#### GET `/alerts` — `alert_list` (web/routes/alerts.py:19)
- params: status=Query(None), cluster_id=Query(None), plant_id=Query(None)
- template: alerts/list.html
- base_context keys: alerts, open_count, current_status, current_cluster_id, current_plant_id

#### POST `/alerts/{alert_id}/ack` — `ack_alert` (web/routes/alerts.py:43)
- template: partials/_alert_row.html
- base_context keys: alert
- HX headers: HX-Toast

#### POST `/alerts/{alert_id}/resolve` — `resolve_alert` (web/routes/alerts.py:56)
- template: partials/_alert_row.html
- base_context keys: alert
- HX headers: HX-Toast

#### POST `/alerts/sync` — `sync_alerts` (web/routes/alerts.py:69)
- template: partials/_alert_list_body.html
- base_context keys: alerts
- HX headers: HX-Toast

#### GET `/alerts/badge` — `alert_badge` (web/routes/alerts.py:83)

#### GET `/clusters/{cluster_id}/history` — `cluster_history` (web/routes/analytics.py:41)
- params: hours=Query(), limit=Query()
- template: clusters/history.html
- base_context keys: history, cluster_id, hours, limit
- HTTPException: 404, 'Cluster not found'

#### GET `/clusters/{cluster_id}/stats` — `cluster_stats` (web/routes/analytics.py:59)
- params: days=Query()
- template: clusters/stats.html
- base_context keys: cluster, stats, days

#### GET `/clusters/{cluster_id}/stats/export` — `cluster_stats_export` (web/routes/analytics.py:75)
- params: days=Query()

#### GET `/clusters/{cluster_id}/learn` — `cluster_learn` (web/routes/analytics.py:110)
- template: clusters/learn.html
- base_context keys: cluster, insights, forecast

#### GET `/scheduler` — `scheduler_page` (web/routes/analytics.py:128)
- template: scheduler.html
- base_context keys: scheduler_running, jobs, check_all_paused

#### POST `/scheduler/jobs/{job_id}/delete` — `scheduler_delete_job` (web/routes/analytics.py:154) {'response_class': 'HTMLResponse'}
- HTTPException: 503, 'Scheduler not running' | 409, str(exc) | 404, str(exc)

#### POST `/scheduler/pause` — `scheduler_pause` (web/routes/analytics.py:178)
- redirect: ; url='/scheduler', status_code=303 (via _set_check_all_paused_web)
- HTTPException: 404, str(exc) (via _set_check_all_paused_web)

#### POST `/scheduler/resume` — `scheduler_resume` (web/routes/analytics.py:183)
- redirect: ; url='/scheduler', status_code=303 (via _set_check_all_paused_web)
- HTTPException: 404, str(exc) (via _set_check_all_paused_web)

#### POST `/bulk/stop-all` — `bulk_stop_all_web` (web/routes/analytics.py:188)
- template: partials/_stop_all_result.html
- base_context keys: stopped, errors

#### GET `/login` — `login_form` (web/routes/auth.py:42) {'response_class': 'HTMLResponse'}
- template: auth/login.html
- base_context keys: next_url, auth_enabled, show_chrome

#### POST `/login` — `login_submit` (web/routes/auth.py:58)
- params: username=Form(), password=Form(), next=Form()
- template: auth/login.html, status=status.HTTP_401_UNAUTHORIZED
- base_context keys: next_url, error, auth_enabled, show_chrome
- redirect: ; url=target, status_code=303

#### POST `/logout` — `logout_submit` (web/routes/auth.py:94)
- redirect: ; url='/login', status_code=303

#### GET `/clusters` — `list_clusters` (web/routes/clusters.py:30)
- template: clusters/list.html
- base_context keys: clusters

#### GET `/clusters/new` — `new_cluster_form` (web/routes/clusters.py:36)
- template: clusters/new.html

#### POST `/clusters` — `create_cluster` (web/routes/clusters.py:41)
- params: name=Form(...), location=Form(''), environment=Form('indoor')
- redirect: ; url=f'/clusters/{cluster_id}', status_code=303

#### GET `/clusters/{cluster_id}/edit` — `edit_cluster_form` (web/routes/clusters.py:54)
- template: clusters/edit.html
- base_context keys: cluster
- HTTPException: 404, 'Cluster not found'

#### POST `/clusters/{cluster_id}/edit` — `update_cluster` (web/routes/clusters.py:62)
- params: name=Form(...), location=Form(''), environment=Form('indoor')
- redirect: ; url=f'/clusters/{cluster_id}', status_code=303
- HTTPException: 404, 'Cluster not found'

#### DELETE `/clusters/{cluster_id}` — `delete_cluster` (web/routes/clusters.py:83) {'response_class': 'HTMLResponse'}
- HTTPException: 404, 'Cluster not found'

#### GET `/clusters/{cluster_id}` — `cluster_detail` (web/routes/clusters.py:108)
- params: hours=Query(24)
- template: clusters/detail.html
- base_context keys: status, cluster_id, hours, allowed_hours, metrics, chart_payloads, chart_thresholds, rationale_reasons, declared_config, effective_config, windows, weekday_bits, weekday_labels, plants_by_id, quiet_active_now
- HTTPException: 404, 'Cluster not found'

#### GET `/clusters/{cluster_id}/status-fragment` — `cluster_status_fragment` (web/routes/clusters.py:206)
- template: partials/_cluster_status.html
- base_context keys: status, cluster_id
- HTTPException: 404, 'Cluster not found'

#### GET `/clusters/{cluster_id}/card-fragment` — `cluster_card_fragment` (web/routes/clusters.py:221)
- template: partials/_cluster_card.html
- base_context keys: status, plants_by_id
- HTTPException: 404, 'Cluster not found'

#### GET `/clusters/{cluster_id}/chart-fragment` — `cluster_chart_fragment` (web/routes/clusters.py:239)
- params: metric=Query('soil_moisture'), hours=Query(24)
- template: partials/_chart_panel.html
- base_context keys: metric, hours, payload_json
- HTTPException: 400, f'Unsupported metric: {metric}' | 404, 'Cluster not found'

#### GET `/clusters/{cluster_id}/overlay-fragment` — `cluster_overlay_fragment` (web/routes/clusters.py:260)
- params: hours=Query(72)
- template: partials/_chart_overlay.html
- base_context keys: cluster_id, hours, payload_json
- HTTPException: 404, 'Cluster not found'

#### GET `/clusters/{cluster_id}/heatmap-fragment` — `cluster_heatmap_fragment` (web/routes/clusters.py:277)
- params: days=Query(30)
- template: partials/_heatmap_panel.html
- base_context keys: cluster_id, days, payload
- HTTPException: 404, 'Cluster not found'

#### GET `/clusters/{cluster_id}/config` — `config_form` (web/routes/configs.py:26)
- redirect: ; url=f'/clusters/{cluster_id}#config', status_code=301

#### POST `/clusters/{cluster_id}/config` — `save_config` (web/routes/configs.py:71)
- params: mode=Form(''), duration_minutes=Form(''), interval_hours=Form(''), auto_run=Form(''), quiet_start_hour=Form(''), quiet_end_hour=Form('')
- redirect: ; url=f'/clusters/{cluster_id}#config', status_code=303
- HTTPException: 400, 'Invalid hour value' (via _parse_optional_hour) | 400, f'Invalid tri-bool value: {raw!r}' (via _parse_tri_bool)

#### POST `/config/global` — `save_global_config` (web/routes/configs.py:103)
- params: mode=Form(''), duration_minutes=Form(''), interval_hours=Form(''), auto_run=Form(''), daily_cap_minutes=Form(''), max_events_per_day=Form(''), quiet_start_hour=Form(''), quiet_end_hour=Form('')
- redirect: ; url='/preferences?saved=global#global-config', status_code=303
- HTTPException: 400, 'Value must be non-negative' (via _parse_non_negative_int) | 400, 'Invalid numeric value' (via _parse_non_negative_int) | 400, 'Invalid hour value' (via _parse_optional_hour) | 400, f'Invalid tri-bool value: {raw!r}' (via _parse_tri_bool)

#### GET `/clusters/{cluster_id}/decisions` — `cluster_decisions` (web/routes/decisions.py:15)
- params: limit=Query()
- template: clusters/decisions.html
- base_context keys: cluster, logs, limit

#### GET `/clusters/{cluster_id}/efficacy` — `cluster_efficacy_page` (web/routes/efficacy.py:16)
- params: days=Query()
- template: clusters/efficacy.html
- base_context keys: cluster, result, days

#### GET `/health/badge` — `health_badge` (web/routes/fragments.py:18)
- template: partials/_health_badge.html
- base_context keys: scheduler_running, job_count

#### GET `/dashboard/hero` — `dashboard_hero` (web/routes/fragments.py:31)
- template: partials/_dashboard_hero.html
- base_context keys: total, counts

#### GET `/health` — `health_page` (web/routes/health_page.py:16)
- template: health.html
- base_context keys: pulse

#### GET `/clusters/{cluster_id}/irrigators` — `list_irrigators` (web/routes/irrigators.py:38)
- redirect: ; url=f'/clusters/{cluster_id}#irrigators', status_code=301

#### GET `/clusters/{cluster_id}/irrigators/new` — `new_irrigator_form` (web/routes/irrigators.py:46)
- template: irrigators/new.html
- base_context keys: cluster
- redirect: ; url=f'/clusters/{cluster_id}#irrigators', status_code=303

#### POST `/clusters/{cluster_id}/irrigators` — `create_irrigator` (web/routes/irrigators.py:70)
- params: tuya_device_id=Form(...), name=Form(...), type=Form(...), device_ip=Form(''), local_key=Form(''), reservoir_l=Form(''), flow_rate_l_per_min=Form('')
- template: irrigators/new.html, status=409
- base_context keys: cluster, error
- redirect: ; url=f'/clusters/{cluster_id}#irrigators', status_code=303 | ; url=f'/clusters/{cluster_id}#irrigators', status_code=303 (via update_irrigator)
- HTTPException: 400, 'Capacity values must be >= 0' (via _parse_capacity) | 400, 'Capacity values must be numbers' (via _parse_capacity)

#### GET `/clusters/{cluster_id}/irrigators/edit` — `edit_irrigator_form` (web/routes/irrigators.py:119)
- template: irrigators/edit.html
- base_context keys: cluster, irrigator, config
- HTTPException: 404, 'Cluster has no irrigator' (via _require_cluster_irrigator)

#### POST `/clusters/{cluster_id}/irrigators/edit` — `update_irrigator` (web/routes/irrigators.py:131)
- params: name=Form(...), type=Form(...), device_ip=Form(''), local_key=Form(''), reservoir_l=Form(''), flow_rate_l_per_min=Form('')
- redirect: ; url=f'/clusters/{cluster_id}#irrigators', status_code=303
- HTTPException: 400, 'Capacity values must be >= 0' (via _parse_capacity) | 400, 'Capacity values must be numbers' (via _parse_capacity) | 404, 'Cluster has no irrigator' (via _require_cluster_irrigator)

#### DELETE `/clusters/{cluster_id}/irrigators` — `delete_irrigator` (web/routes/irrigators.py:165) {'response_class': 'HTMLResponse'}
- HTTPException: 404, 'Cluster has no irrigator' (via _require_cluster_irrigator)

#### POST `/irrigators/{irrigator_id}/start` — `start_irrigator` (web/routes/irrigators.py:201)
- params: minutes=Form('')
- template: partials/_irrigator_action_result.html (via _action_result)
- base_context keys: success (via _action_result), message (via _action_result), action (via _action_result)
- HTTPException: 400, 'Invalid minutes' | 503, exc.detail (via _action_result) | 404, 'Irrigator not found' (via _get_irrigator_or_404)

#### POST `/irrigators/{irrigator_id}/stop` — `stop_irrigator` (web/routes/irrigators.py:222)
- template: partials/_irrigator_action_result.html (via _action_result)
- base_context keys: success (via _action_result), message (via _action_result), action (via _action_result)
- HTTPException: 503, exc.detail (via _action_result) | 404, 'Irrigator not found' (via _get_irrigator_or_404)

#### GET `/irrigators/{irrigator_id}/log-manual` — `log_manual_form` (web/routes/irrigators.py:234)
- template: irrigators/log_manual.html
- base_context keys: irrigator
- HTTPException: 404, 'Irrigator not found' (via _get_irrigator_or_404)

#### POST `/irrigators/{irrigator_id}/log-manual` — `log_manual_submit` (web/routes/irrigators.py:240)
- params: minutes=Form(...), notes=Form('')
- template: irrigators/log_manual.html, status=e.status_code
- base_context keys: irrigator, error, minutes, notes
- redirect: ; url=f'/clusters/{irr.cluster_id}#irrigators', status_code=303
- HTTPException: 404, 'Irrigator not found' (via _get_irrigator_or_404)

#### POST `/clusters/{cluster_id}/irrigate` — `irrigate` (web/routes/operations.py:22)
- params: dry_run=Form(''), no_sync=Form(''), temp_override=Form(''), force=Form('')
- template: partials/_decision_panel.html
- base_context keys: result, cluster_id

#### GET `/clusters/{cluster_id}/monitor` — `monitor` (web/routes/operations.py:56)
- template: partials/_monitor_panel.html
- base_context keys: result, cluster_id

#### POST `/clusters/{cluster_id}/check` — `check_single` (web/routes/operations.py:65)
- template: partials/_check_result.html
- base_context keys: results, has_alerts

#### POST `/check` — `check_all` (web/routes/operations.py:83)
- template: partials/_check_result.html
- base_context keys: results, has_alerts

#### POST `/sync` — `sync_all` (web/routes/operations.py:93)
- params: hours=Form('24')
- template: partials/_sync_result.html
- base_context keys: result
- HTTPException: 400, 'Invalid hours'

#### POST `/plants/sync` — `sync_plants` (web/routes/operations.py:104)
- params: plant_id=Form(''), cluster_id=Form('')
- template: partials/_sync_result.html
- base_context keys: result
- HTTPException: 404, f'Plant {pid} not found'

#### GET `/` — `dashboard` (web/routes/pages.py:15) {'include_in_schema': 'False'}
- template: dashboard.html
- base_context keys: clusters

#### GET `/clusters/{cluster_id}/plants/{plant_id}` — `plant_dashboard` (web/routes/plant_dashboard.py:36)
- params: hours=Query(24)
- template: plants/dashboard.html
- base_context keys: cluster, other_clusters, plant, plant_sensors, latest_readings, care_info, recent_events, alerts, hours, allowed_hours, metrics, chart_payloads, health_score, health_history, last_irrigated_relative
- HTTPException: 404, 'Plant not found' (via _get_plant_or_404)

#### GET `/clusters/{cluster_id}/plants/{plant_id}/chart-fragment` — `plant_chart_fragment` (web/routes/plant_dashboard.py:128)
- params: metric=Query('soil_moisture'), hours=Query(24)
- template: partials/_chart_panel.html
- base_context keys: metric, hours, payload_json
- HTTPException: 400, f'Unsupported metric: {metric}' | 404, 'Plant not found' | 404, 'Plant not found' (via _get_plant_or_404)

#### GET `/clusters/{cluster_id}/plants/{plant_id}/health-fragment` — `plant_health_fragment` (web/routes/plant_dashboard.py:151)
- template: partials/_plant_health_chart.html
- base_context keys: plant, payload_json
- HTTPException: 404, 'Plant not found' | 404, 'Plant not found' (via _get_plant_or_404)

#### POST `/clusters/{cluster_id}/plants/{plant_id}/move` — `move_plant_web` (web/routes/plant_dashboard.py:169)
- params: target_cluster_id=Form(...)
- redirect: ; url=f'/clusters/{target_cluster_id}/plants/{plant_id}', status_code=303
- HTTPException: 404, 'Target cluster not found' | 400, str(exc) | 404, 'Plant not found' (via _get_plant_or_404)

#### GET `/clusters/{cluster_id}/plants` — `list_plants` (web/routes/plants.py:29)
- redirect: ; url=f'/clusters/{cluster_id}#plants', status_code=301

#### GET `/clusters/{cluster_id}/plants/new` — `new_plant_form` (web/routes/plants.py:38)
- template: plants/new.html
- base_context keys: cluster

#### POST `/clusters/{cluster_id}/plants` — `create_plant` (web/routes/plants.py:44)
- params: species=Form(...), category=Form(''), water_needs=Form(''), light_needs=Form(''), ideal_temp_min=Form(''), ideal_temp_max=Form(''), ideal_humidity_min=Form(''), ideal_humidity_max=Form(''), notes=Form('')
- redirect: ; url=f'/clusters/{cluster_id}#plants', status_code=303

#### GET `/clusters/{cluster_id}/plants/{plant_id}/edit` — `edit_plant_form` (web/routes/plants.py:76)
- template: plants/edit.html
- base_context keys: cluster, plant
- HTTPException: 404, 'Plant not found in cluster' (via _get_plant_in_cluster)

#### POST `/clusters/{cluster_id}/plants/{plant_id}/edit` — `update_plant` (web/routes/plants.py:83)
- params: species=Form(...), category=Form(''), water_needs=Form(''), light_needs=Form(''), ideal_temp_min=Form(''), ideal_temp_max=Form(''), ideal_humidity_min=Form(''), ideal_humidity_max=Form(''), notes=Form('')
- redirect: ; url=f'/clusters/{cluster_id}#plants', status_code=303
- HTTPException: 404, 'Plant not found in cluster' (via _get_plant_in_cluster)

#### DELETE `/clusters/{cluster_id}/plants/{plant_id}` — `delete_plant` (web/routes/plants.py:116) {'response_class': 'HTMLResponse'}
- HTTPException: 404, 'Plant not found in cluster' (via _get_plant_in_cluster)

#### GET `/preferences` — `preferences_page` (web/routes/preferences.py:19)
- template: preferences.html
- base_context keys: prefs, clusters, global_config, ntfy_configured, saved

#### POST `/preferences` — `update_preferences` (web/routes/preferences.py:41)
- params: units=Form(...), timezone=Form(...), theme=Form(...), refresh_interval_seconds=Form(...), default_cluster_id=Form(''), dry_run_global=Form(''), notify_manual=Form(''), notify_emergency=Form(''), notify_alerts=Form(''), notify_auto=Form('')
- redirect: ; url='/preferences?saved=1', status_code=303

#### POST `/preferences/theme` — `update_theme` (web/routes/preferences.py:79)
- params: theme=Form(...)
- redirect: ; url='/preferences?saved=1', status_code=303 (via update_preferences)
- HTTPException: status_code=status.HTTP_400_BAD_REQUEST detail=f'Invalid theme: {theme!r}'

#### GET `/quality` — `quality_page` (web/routes/quality.py:16)
- template: quality.html
- base_context keys: report, grouped

#### GET `/clusters/{cluster_id}/sensors` — `list_sensors` (web/routes/sensors.py:33)
- redirect: ; url=f'/clusters/{cluster_id}#sensors', status_code=301

#### GET `/clusters/{cluster_id}/sensors/new` — `new_sensor_form` (web/routes/sensors.py:41)
- template: sensors/new.html
- base_context keys: cluster, plants

#### POST `/clusters/{cluster_id}/sensors` — `create_sensor` (web/routes/sensors.py:50)
- params: tuya_device_id=Form(...), name=Form(...), type=Form(...), plant_id=Form('')
- redirect: ; url=f'/clusters/{cluster_id}#sensors', status_code=303
- HTTPException: 400, 'Invalid plant_id' (via _parse_optional_plant_id)

#### GET `/clusters/{cluster_id}/sensors/{sensor_id}/edit` — `edit_sensor_form` (web/routes/sensors.py:74)
- template: sensors/edit.html
- base_context keys: cluster, sensor, plants
- HTTPException: 404, 'Sensor not found in cluster' (via _get_sensor_in_cluster)

#### POST `/clusters/{cluster_id}/sensors/{sensor_id}/edit` — `update_sensor` (web/routes/sensors.py:84)
- params: name=Form(...), type=Form(...), plant_id=Form('')
- redirect: ; url=f'/clusters/{cluster_id}#sensors', status_code=303
- HTTPException: 404, 'Sensor not found in cluster' (via _get_sensor_in_cluster) | 400, 'Invalid plant_id' (via _parse_optional_plant_id)

#### DELETE `/clusters/{cluster_id}/sensors/{sensor_id}` — `delete_sensor` (web/routes/sensors.py:103) {'response_class': 'HTMLResponse'}
- HTTPException: 404, 'Sensor not found in cluster' (via _get_sensor_in_cluster)

#### GET `/vacation` — `vacation_list` (web/routes/vacation.py:37)
- template: vacation/list.html
- base_context keys: active, windows, budgets, budget_window

#### POST `/vacation` — `create_vacation` (web/routes/vacation.py:63)
- params: starts_at=Form(...), ends_at=Form(...), contact_email=Form(''), notes=Form('')
- redirect: ; url='/vacation', status_code=303
- HTTPException: 400, 'ends_at must be after starts_at.' | 400, 'Invalid date format. Use YYYY-MM-DD or Unix timestamp.'

#### GET `/vacation/{window_id}/edit` — `edit_vacation_form` (web/routes/vacation.py:89)
- template: vacation/edit.html
- base_context keys: window
- HTTPException: 404, 'Vacation window not found.'

#### POST `/vacation/{window_id}/edit` — `update_vacation` (web/routes/vacation.py:101)
- params: starts_at=Form(...), ends_at=Form(...), contact_email=Form(''), notes=Form('')
- redirect: ; url='/vacation', status_code=303
- HTTPException: 400, 'ends_at must be after starts_at.' | 404, 'Vacation window not found.' | 400, 'Invalid date format. Use YYYY-MM-DD or Unix timestamp.'

#### POST `/vacation/{window_id}/delete` — `delete_vacation` (web/routes/vacation.py:131)
- redirect: ; url='/vacation', status_code=303
- HTTPException: 404, 'Vacation window not found.'

#### POST `/clusters/{cluster_id}/windows` — `create_window` (web/routes/windows.py:53)
- params: start_hour=Form(...), end_hour=Form(...), weekday_mask=Form(), label=Form('')
- redirect: ; url=f'/clusters/{cluster_id}/config', status_code=303
- HTTPException: 400, 'weekday_mask values must be 1, 2, 4, 8, 16, 32, or 64.' (via _parse_weekday_mask) | 400, 'weekday_mask values must be integers.' (via _parse_weekday_mask) | 400, 'start_hour and end_hour must be 0..23.' (via _validate_window_form) | 400, 'start_hour and end_hour must differ.' (via _validate_window_form) | 400, 'Select at least one weekday.' (via _validate_window_form)

#### GET `/clusters/{cluster_id}/windows/{window_id}/edit` — `edit_window_form` (web/routes/windows.py:80)
- template: configs/window_edit.html
- base_context keys: cluster, window, weekday_checks
- HTTPException: 404, 'Window not found in cluster.' (via _get_window_in_cluster)

#### POST `/clusters/{cluster_id}/windows/{window_id}/edit` — `update_window` (web/routes/windows.py:95)
- params: start_hour=Form(...), end_hour=Form(...), weekday_mask=Form(), label=Form('')
- redirect: ; url=f'/clusters/{cluster_id}/config', status_code=303
- HTTPException: 404, 'Window not found in cluster.' (via _get_window_in_cluster) | 400, 'weekday_mask values must be 1, 2, 4, 8, 16, 32, or 64.' (via _parse_weekday_mask) | 400, 'weekday_mask values must be integers.' (via _parse_weekday_mask) | 400, 'start_hour and end_hour must be 0..23.' (via _validate_window_form) | 400, 'start_hour and end_hour must differ.' (via _validate_window_form) | 400, 'Select at least one weekday.' (via _validate_window_form)

#### DELETE `/clusters/{cluster_id}/windows/{window_id}` — `delete_window` (web/routes/windows.py:120) {'response_class': 'HTMLResponse'}
- HTTPException: 404, 'Window not found in cluster.' (via _get_window_in_cluster)


#### Non-template responses worth pinning (not visible in the AST lines above)

- `GET /alerts/badge` (`s/web/routes/alerts.py:80-85`): `HTMLResponse("")` when 0 open alerts, else `<span class="bell__count">{count}</span>`.
- `GET /clusters/{id}/stats/export` (`s/web/routes/analytics.py:74-107`): CSV `StreamingResponse`, header row
  `timestamp,date,time,irrigator,action,duration_minutes,triggered_by,notes`, `Content-Disposition: attachment; filename=cluster_{id}_stats.csv`.
- HTMX deletes (`DELETE /clusters/{id}`, `/clusters/{id}/irrigators`, `/clusters/{id}/plants/{pid}`, `/clusters/{id}/sensors/{sid}`,
  `/clusters/{id}/windows/{wid}`, `POST /scheduler/jobs/{job_id}/delete`) return `HTMLResponse("")` (row removal).
- `HX-Toast` response header is set by `POST /alerts/{id}/ack`, `/alerts/{id}/resolve`, `/alerts/sync`.
- Redirect status codes: legacy list pages (`/clusters/{id}/plants|sensors|irrigators|config`) → **301** to `/clusters/{id}#<anchor>`
  **[T: test_web_redirects.py]**; form POSTs → **303**.
- `POST /login` (`s/web/routes/auth.py:57`): bad creds re-render `auth/login.html` with **401**; success → 303 to sanitized `next`
  (external `next` rejected) **[T: test_form_login_redirects_to_next, test_form_login_rejects_external_next]**; sets cookie via
  `set_session_cookie` (`httponly`, `samesite=lax`, `secure=settings.auth_cookie_secure`, `max_age=ttl*60`, `path=/`).
- Web error pages: `error_page.html` (full page) / `_error.html` (HX), context `status_code`, `detail`; validation → 422 `"Form validation failed."`.

---

## 4. CLI (`greenhouse`) and TUI

### 4.1 Entry, global behavior

- Console script `greenhouse = greenhouse_cli.main:app` (`libs/greenhouse-cli/pyproject.toml`). `app = typer.Typer(help="Smart irrigation system CLI", no_args_is_help=True, rich_markup_mode="rich")` (`cli/main.py:22`).
- Root callback `main` (`cli/main.py:29`): option `--server TEXT` (default `None`), help `"Server URL (default: $IRRIGATION_SERVER_URL or http://localhost:8000)"`; stores it in `ctx.obj`. Docstring `"Smart irrigation system — evidence-based plant care with Tuya IoT sensors."`. Typer adds `--install-completion` / `--show-completion`.
- Registration order (`cli/main.py:43-58`, determines `--help` listing): top-level `register_operations` (status, irrigate, check, monitor, sync, learn, history, stats, health, stop-all), `register_auth` (login, logout, whoami), `register_tui` (tui); sub-apps cluster, plant, irrigator, sensor, config (with nested `global`), scheduler, alerts, decisions, prefs, vacation, windows.
- Server URL resolution: `--server` → `$IRRIGATION_SERVER_URL` → `http://localhost:8000` (`cli/commands/_helpers.py:14`, duplicated in `cli/commands/auth.py:26`, `cli/commands/tui.py:35`). **[T: test_custom_server_url, test_launches_with_resolved_server, test_env_server_fallback]**
- Auth token: `$GREENHOUSE_API_TOKEN` (stripped) wins, else file `${XDG_CONFIG_HOME:-~/.config}/greenhouse/token` (`cli/client.py:9-34`); `store_token` writes it with mode `0o600`; header `Authorization: Bearer <token>`; `IrrigationClient(base_url, token=None, **kwargs)` uses `httpx.Client(timeout=30.0)`. `login` uses `token=""` to suppress the stored token. **[T: tests/cli/test_completeness.py::TestAuthCommands]**
- Output: `_helpers.output(data)` → `rich.print_json(json.dumps(data, default=str))` (`cli/commands/_helpers.py:27`).
- Exit codes / error output (**[T]** in `tests/cli/test_cli.py`):
  - `call()` (`cli/commands/_helpers.py:18`): `ServerError` → stderr `Error: {detail}`, **exit 1** [T: test_server_404, test_server_unreachable, test_pause_server_error_exits_1, test_show_404_exits_nonzero, test_add_conflict_exits_nonzero, …].
  - `ServerError(status_code, detail)` (`cli/client.py:66`): HTTP ≥400 → `detail = resp.json().get("detail", resp.text)` (falls back to text); connect failure → `ServerError(0, "Cannot connect to server: {e}")`. `text/csv` responses become `{"csv": text}`.
  - `irrigate`: exit 1 if response `action == "error"`. `check`: no cluster and no `--all` → stderr `Error: provide a cluster ID or --all`, exit 1; `has_alerts` → **exit 2** [T: test_check_with_alerts_exits_2]; `action=="error"` → 1. `monitor`: `needs_water` → **exit 2** [T: test_monitor_needs_water_exits_2]. `login` failure → `Error: {detail}`, exit 1. Delete commands without `--yes/-y` prompt via `typer.confirm(..., abort=True)` (abort → click "Aborted!", exit 1). `stats --export FILE` writes CSV and echoes `Exported to {FILE}`.
  - Plain-text messages: `Logged in as {user}. Token stored at {path}.`, `Server returned an empty token (auth may be disabled).`, `Logged out — token removed.`, `No token was stored; nothing to remove.`
- Full `--help` text for all 74 pages: **[G]** (baseline captured in scratchpad `cli_help.txt`; note CliRunner prints `Usage: root …`). Request shapes per command: **[T: tests/cli/test_cli.py (43), tests/cli/test_completeness.py (9 classes)]**.

### 4.2 Command tree (generated by walking `typer.main.get_command(app)`)

Groups (all `no_args_is_help=True`, no group-level options except root):

- `greenhouse` hidden=False no_args_is_help=True invoke_without_command=False params: --server=None, --install-completion=None, --show-completion=None
- `greenhouse alerts` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse cluster` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse config` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse config global` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse decisions` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse irrigator` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse plant` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse prefs` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse scheduler` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse sensor` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse vacation` hidden=False no_args_is_help=True invoke_without_command=False params: -
- `greenhouse windows` hidden=False no_args_is_help=True invoke_without_command=False params: -


Leaf commands (61):

| command | callback (source) | arguments | options (default) |
|---|---|---|---|
| `alerts ack` | `alerts_ack` cli/commands/alerts.py:40 | ALERT_ID:int | - |
| `alerts get` | `alerts_get` cli/commands/alerts.py:31 | ALERT_ID:int | - |
| `alerts list` | `alerts_list` cli/commands/alerts.py:14 | - | --status:str; --cluster:int; --plant:int; --limit:int range=100 |
| `alerts resolve` | `alerts_resolve` cli/commands/alerts.py:49 | ALERT_ID:int | - |
| `alerts sync` | `alerts_sync` cli/commands/alerts.py:58 | - | --cluster:int |
| `check` | `check` cli/commands/operations.py:35 | CLUSTER?:int | --all:boolean=False |
| `cluster add` | `cluster_add` cli/commands/clusters.py:12 | NAME:str | --location:str; --environment:str='indoor' |
| `cluster delete` | `cluster_delete` cli/commands/clusters.py:55 | CLUSTER_ID:int | --yes/-y:boolean=False |
| `cluster get` | `cluster_get` cli/commands/clusters.py:29 | CLUSTER_ID:int | - |
| `cluster list` | `cluster_list` cli/commands/clusters.py:23 | - | - |
| `cluster update` | `cluster_update` cli/commands/clusters.py:38 | CLUSTER_ID:int | --name:str; --location:str; --environment:str |
| `config effective` | `config_effective` cli/commands/configs.py:61 | - | --cluster:int REQUIRED |
| `config get` | `config_get` cli/commands/configs.py:55 | - | --cluster:int REQUIRED |
| `config global get` | `global_get` cli/commands/configs.py:67 | - | - |
| `config global set` | `global_set` cli/commands/configs.py:73 | - | --mode:str; --minutes:int; --interval:int; --auto-run/--no-auto-run:boolean; --daily-cap:int; --max-events:int; --quiet-start:int; --quiet-end:int |
| `config set` | `config_set` cli/commands/configs.py:22 | - | --cluster:int REQUIRED; --mode:str; --minutes:int; --interval:int; --auto-run/--no-auto-run:boolean; --daily-cap:int; --max-events:int; --quiet-start:int; --quiet-end:int |
| `decisions list` | `decisions_list` cli/commands/decisions.py:14 | - | --cluster:int REQUIRED; --limit:int range=50 |
| `health` | `health` cli/commands/operations.py:102 | - | - |
| `history` | `history` cli/commands/operations.py:76 | CLUSTER:int | --hours:int=24; --limit:int=50 |
| `irrigate` | `irrigate` cli/commands/operations.py:18 | CLUSTER:int | --temp:float; --dry-run:boolean=False; --no-sync:boolean=False; --force:boolean=False |
| `irrigator add` | `irrigator_add` cli/commands/irrigators.py:12 | - | --cluster:int REQUIRED; --device-id:str REQUIRED; --name:str REQUIRED; --type:str REQUIRED; --device-ip:str; --local-key:str; --reservoir-l:float; --flow-rate-l-per-min:float |
| `irrigator delete` | `irrigator_delete` cli/commands/irrigators.py:133 | CLUSTER:int | --yes/-y:boolean=False |
| `irrigator list` | `irrigator_list` cli/commands/irrigators.py:49 | - | - |
| `irrigator log-manual` | `irrigator_log_manual` cli/commands/irrigators.py:80 | ID:int | --minutes:int REQUIRED; --notes:str |
| `irrigator show` | `irrigator_show` cli/commands/irrigators.py:55 | CLUSTER:int | - |
| `irrigator start` | `irrigator_start` cli/commands/irrigators.py:64 | ID:int | --minutes:int |
| `irrigator stop` | `irrigator_stop` cli/commands/irrigators.py:74 | ID:int | - |
| `irrigator update` | `irrigator_update` cli/commands/irrigators.py:91 | CLUSTER:int | --name:str; --type:str; --device-ip:str; --local-key:str; --reservoir-l:float; --flow-rate-l-per-min:float |
| `learn` | `learn` cli/commands/operations.py:71 | CLUSTER:int | - |
| `login` | `login` cli/commands/auth.py:33 | - | --username:str REQUIRED; --password:str REQUIRED; --print-token:boolean=False |
| `logout` | `logout` cli/commands/auth.py:76 | - | - |
| `monitor` | `monitor` cli/commands/operations.py:55 | CLUSTER:int | - |
| `plant add` | `plant_add` cli/commands/plants.py:12 | SPECIES:str | --cluster:int REQUIRED; --category:str; --water-needs:str; --light-needs:str; --temp-min:float; --temp-max:float; --humidity-min:float; --humidity-max:float; --notes:str |
| `plant delete` | `plant_delete` cli/commands/plants.py:121 | PLANT_ID:int | --cluster:int REQUIRED; --yes/-y:boolean=False |
| `plant list` | `plant_list` cli/commands/plants.py:45 | - | --cluster:int |
| `plant move` | `plant_move` cli/commands/plants.py:71 | PLANT_ID:int | --to-cluster:int REQUIRED |
| `plant sync` | `plant_sync` cli/commands/plants.py:61 | - | --plant-id:int; --cluster:int |
| `plant update` | `plant_update` cli/commands/plants.py:85 | PLANT_ID:int | --cluster:int REQUIRED; --species:str; --category:str; --water-needs:str; --light-needs:str; --temp-min:float; --temp-max:float; --humidity-min:float; --humidity-max:float; --notes:str |
| `prefs get` | `prefs_get` cli/commands/preferences.py:14 | - | - |
| `prefs set` | `prefs_set` cli/commands/preferences.py:20 | - | --units:str; --timezone:str; --theme:str; --refresh-interval:int; --dry-run-global/--no-dry-run-global:boolean; --default-cluster:int |
| `scheduler pause` | `scheduler_pause` cli/commands/scheduler.py:10 | - | - |
| `scheduler resume` | `scheduler_resume` cli/commands/scheduler.py:21 | - | - |
| `scheduler status` | `scheduler_status` cli/commands/scheduler.py:27 | - | - |
| `sensor add` | `sensor_add` cli/commands/sensors.py:12 | - | --cluster:int REQUIRED; --device-id:str REQUIRED; --name:str REQUIRED; --type:str REQUIRED; --plant-id:int |
| `sensor delete` | `sensor_delete` cli/commands/sensors.py:61 | ID:int | --cluster:int REQUIRED; --yes/-y:boolean=False |
| `sensor list` | `sensor_list` cli/commands/sensors.py:27 | - | --cluster:int |
| `sensor update` | `sensor_update` cli/commands/sensors.py:43 | ID:int | --cluster:int REQUIRED; --name:str; --type:str; --plant-id:int |
| `stats` | `stats` cli/commands/operations.py:86 | CLUSTER:int | --days:int=7; --export:str |
| `status` | `status` cli/commands/operations.py:13 | CLUSTER:int | - |
| `stop-all` | `stop_all` cli/commands/operations.py:107 | - | --yes/-y:boolean=False |
| `sync` | `sync` cli/commands/operations.py:63 | - | --hours:int=24 |
| `tui` | `tui` cli/commands/tui.py:14 | - | --refresh:float range=30.0; --no-animation:boolean=False |
| `vacation add` | `vacation_add` cli/commands/vacation.py:20 | - | --starts-at:int REQUIRED; --ends-at:int REQUIRED; --email:str; --notes:str |
| `vacation delete` | `vacation_delete` cli/commands/vacation.py:72 | WINDOW_ID:int | --yes/-y:boolean=False |
| `vacation list` | `vacation_list` cli/commands/vacation.py:14 | - | - |
| `vacation update` | `vacation_update` cli/commands/vacation.py:44 | WINDOW_ID:int | --starts-at:int; --ends-at:int; --email:str; --notes:str |
| `whoami` | `whoami` cli/commands/auth.py:94 | - | - |
| `windows add` | `windows_add` cli/commands/windows.py:26 | - | --cluster:int REQUIRED; --start-hour:int range REQUIRED; --end-hour:int range REQUIRED; --weekday-mask:int range=127; --label:str |
| `windows delete` | `windows_delete` cli/commands/windows.py:84 | WINDOW_ID:int | --cluster:int REQUIRED; --yes/-y:boolean=False |
| `windows list` | `windows_list` cli/commands/windows.py:17 | - | --cluster:int REQUIRED |
| `windows update` | `windows_update` cli/commands/windows.py:58 | WINDOW_ID:int | --cluster:int REQUIRED; --start-hour:int range; --end-hour:int range; --weekday-mask:int range; --label:str |

(`int range` / `float range` = Click `IntRange`/`FloatRange` with min/max; the number after `=` is the default.)

### 4.3 TUI (`greenhouse tui`)

- Command `tui` (`cli/commands/tui.py:14`): `--refresh FLOAT` (min 0, default `30.0`, "Auto-refresh interval in seconds (0 disables)"), `--no-animation` (default False). Lazily imports `greenhouse_cli.tui.run(server_url, refresh_seconds=30.0, animations=True)` (`cli/tui/__init__.py:10`) → `GreenhouseApp(server_url, refresh_seconds=…, animations=…).run()`.
- `GreenhouseApp` (`cli/tui/app.py:30`): `CSS_PATH="app.tcss"`, `TITLE="greenhouse"`, `MODES` = dashboard/alerts/activity/system/settings; `async api(fn, *, quiet=False)` (`:73`) runs blocking client calls in threads; 401 → `LoginScreen` → stores token (**[T: test_401_prompts_login_and_stores_token]**). Every actuating key goes through `ConfirmScreen` (**[T: test_stop_all_requires_confirmation, test_cancel_never_writes, test_check_cluster_after_confirm]**).
- Request bodies sent by TUI: **[T: test_tui_reaches_every_client_capability, test_irrigate_options_map_to_request_body, test_water_now_blank_minutes_uses_device_default, test_irrigate_dry_run_does_not_actuate, test_water_now_and_stop, …]** (71 tests in `tests/cli/test_tui.py`).
- Bindings / action names / handler names / CSS_PATH: not enumerated by any test → **[G]** (snapshot the dump below, e.g. `[(cls, b.key, b.action, b.description, b.show, b.priority) for each BINDINGS]`).
- `app.tcss` (267 lines) selectors — coupled to widget ids/classes, **[G]**:
  - ids: `#alert-detail #alerts-table #cluster-grid #dashboard-empty #decision-panel #devices-table #efficacy-table #forecast-panel #garden #global-config #global-panel #heatmap #insights-panel #insights-row #irrigator-panel #jobs-table #metric-chart #overview-panels #plant-chart #plants-table #prefs-panel #quality-table #scheduler-panel #search-results #settings-panels #stats-panel #system-panels #vacation-table` (note: `#global-config` is not assigned to any widget in Python — dead selector, keep as-is).
  - classes: `.card-body .card-info .card-spark .card-sprite .cluster-card .dialog .dialog-buttons .dialog-error .dialog-head .dialog-message .dialog-sprite .form-body .form-dialog .form-label .form-row .hint .login-grid .panel .plant-tile .search-dialog`; type selectors `Banner`, `ModalScreen`, `Screen`.
  - Python-assigned ids (superset; tests query many via `#id`): account, activity-hint, activity-table, alert-detail, alerts-hint, alerts-table, banner, can, cancel, chart-hint, cluster-grid, cluster-tabs, config-panel, confirm, dashboard-empty, dashboard-scroll, decision-panel, decisions-table, devices-table, dry-run, efficacy-table, force, forecast-panel, form-error, garden, global-panel, heatmap, history-table, insights-panel, insights-row, irrigator-info, irrigator-panel, jobs-table, learn-panel, login, metric-chart, minutes, no-sync, overview-panels, password, plant-chart, plants-table, prefs-panel, quality-hint, quality-table, run, scheduler-panel, search-input, search-results, sensors-table, settings-panels, start, stats-panel, submit, system-banner, system-panels, tab-charts, tab-config, tab-decisions, tab-history, tab-insights, tab-overview, tab-plants, tab-sensors, tab-windows, username, vacation-table, windows-table, `cluster-card-{id}`, `field-{name}`, `plant-tile-{id}`.
- `ClusterCard.Selected` message → `DashboardScreen.on_cluster_card_selected`; `SearchScreen` uses `@on(Input.Changed)`/`@on(Input.Submitted)`/`@on(DataTable.RowSelected)` handlers `_changed/_submitted/_selected`.

Per-class dump (generated by `dump_tui.py`; only classes with bindings/actions/handlers or DOM subclasses):

#### `tui.app.GreenhouseApp(App)` — cli/tui/app.py:30
- CSS_PATH = 'app.tcss'
- TITLE = 'greenhouse'
- MODES = {'dashboard': <class 'greenhouse_cli.tui.screens.dashboard.DashboardScreen'>, 'alerts': <class 'greenhouse_cli.tui.screens.alerts.AlertsScreen'>, 'activity': <class 'greenhouse_cli.tui.screens.activity.ActivityScreen'>, 'system': <class 'greenhouse_cli.tui.screens.system.SystemScreen'>, 'settings': <class 'greenhouse_cli.tui.screens.settings.SettingsScreen'>}
- BINDINGS (9): 'd'->"switch_mode('dashboard')" 'Dashboard'; 'a'->"switch_mode('alerts')" 'Alerts'; 'l'->"switch_mode('activity')" 'Activity'; 's'->"switch_mode('system')" 'System'; 'o'->"switch_mode('settings')" 'Settings'; 'slash'->'search' 'Search'; 'r'->'refresh' 'Refresh'; 'question_mark'->'show_help_panel' 'Keys' show=False; 'q'->'quit' 'Quit'
- actions: action_refresh, action_search
- on_/watch_ etc: _on_login

#### `tui.widgets.SpriteView(Static)` — cli/tui/widgets.py:25
- DEFAULT_CSS = '<51 chars>'
- on_/watch_ etc: on_mount

#### `tui.widgets.Banner(Static)` — cli/tui/widgets.py:55

#### `tui.widgets.ClusterCard(Vertical)` — cli/tui/widgets.py:86
- BINDINGS (1): 'enter' / 'select' / 'Open'
- actions: action_select
- on_/watch_ etc: on_click
- nested classes: Selected

#### `tui.widgets.PlantTile(Vertical)` — cli/tui/widgets.py:152

#### `tui.widgets.MetricChart(PlotextPlot)` — cli/tui/widgets.py:181

#### `tui.widgets.Heatmap(Static)` — cli/tui/widgets.py:271

#### `tui.widgets.KeyValue(Static)` — cli/tui/widgets.py:298

#### `tui.screens.activity.ActivityScreen(DataScreen)` — cli/tui/screens/activity.py:16
- BINDINGS (2): 'f'->'cycle_filter' 'Severity'; 'n'->'more' 'Older'
- actions: action_cycle_filter, action_more
- on_/watch_ etc: on_mount

#### `tui.screens.alerts.AlertsScreen(DataScreen)` — cli/tui/screens/alerts.py:18
- BINDINGS (4): 'f'->'cycle_filter' 'Filter'; 'k'->'acknowledge' 'Acknowledge'; 'v'->'resolve' 'Resolve'; 'y'->'sync_alerts' 'Re-scan'
- actions: action_acknowledge, action_cycle_filter, action_resolve, action_sync_alerts
- on_/watch_ etc: on_data_table_row_highlighted, on_mount

#### `tui.screens.base.DataScreen(Screen)` — cli/tui/screens/base.py:18
- on_/watch_ etc: on_mount

#### `tui.screens.cluster.ClusterScreen(DataScreen)` — cli/tui/screens/cluster.py:43
- BINDINGS (17): 'escape'->'app.pop_screen' 'Back'; 'i'->'irrigate' 'Irrigate'; 'w'->'water_now' 'Water now'; 'x'->'stop' 'Stop'; 'c'->'check' 'Check'; 'n'->'new' 'New'; 'u'->'edit' 'Edit'; 'delete'->'delete' 'Delete'; 'm'->'cycle_metric' 'Metric' show=False; '['->'range(-1)' 'Range -' show=False; ']'->'range(1)' 'Range +' show=False; 'L'->'log_manual' 'Log manual' show=False; 'M'->'move_plant' 'Move plant' show=False; 'P'->'plant_sync' 'Plant-DB sync' show=False; 'e'->'edit_cluster' 'Edit cluster' show=False; 'D'->'delete_cluster' 'Delete cluster' show=False; 'E'->'export_stats' 'Export CSV' show=False
- actions: action_check, action_cycle_metric, action_delete, action_delete_cluster, action_edit, action_edit_cluster, action_export_stats, action_irrigate, action_log_manual, action_move_plant, action_new, action_plant_sync, action_range, action_stop, action_water_now
- on_/watch_ etc: on_data_table_row_highlighted, on_mount, on_tabbed_content_tab_activated

#### `tui.screens.dashboard.DashboardScreen(DataScreen)` — cli/tui/screens/dashboard.py:18
- BINDINGS (6): 'n'->'new_cluster' 'New cluster'; 'S'->'sync' 'Sync sensors'; 'c'->'check_all' 'Check all'; 'X'->'stop_all' 'STOP ALL'; 'right,down,j'->'app.focus_next' 'Next' show=False; 'left,up,k'->'app.focus_previous' 'Previous' show=False
- actions: action_check_all, action_new_cluster, action_stop_all, action_sync
- on_/watch_ etc: on_cluster_card_selected

#### `tui.screens.forms.FormScreen(ModalScreen)` — cli/tui/screens/forms.py:78
- BINDINGS (2): 'escape' / 'dismiss(None)' / 'Cancel'; 'ctrl+s' / 'submit' / 'Save'
- actions: action_submit
- on_/watch_ etc: on_button_pressed, on_input_submitted

#### `tui.screens.modals.ConfirmScreen(ModalScreen)` — cli/tui/screens/modals.py:13
- BINDINGS (3): 'escape' / 'dismiss(False)' / 'Cancel'; 'y' / 'dismiss(True)' / 'Yes'; 'n' / 'dismiss(False)' / 'No'
- on_/watch_ etc: on_button_pressed, on_mount

#### `tui.screens.modals.IrrigateScreen(ModalScreen)` — cli/tui/screens/modals.py:38
- BINDINGS (1): 'escape' / 'dismiss(None)' / 'Cancel'
- on_/watch_ etc: on_button_pressed

#### `tui.screens.modals.WaterNowScreen(ModalScreen)` — cli/tui/screens/modals.py:77
- BINDINGS (1): 'escape' / 'dismiss(None)' / 'Cancel'
- on_/watch_ etc: on_button_pressed, on_input_submitted

#### `tui.screens.modals.LoginScreen(ModalScreen)` — cli/tui/screens/modals.py:115
- BINDINGS (1): 'escape' / 'dismiss(None)' / 'Cancel'
- on_/watch_ etc: on_button_pressed, on_input_submitted

#### `tui.screens.search.SearchScreen(ModalScreen)` — cli/tui/screens/search.py:21
- BINDINGS (1): 'escape' / 'dismiss(None)' / 'Close'
- on_/watch_ etc: on_mount
- @on handlers: _changed<-[('Input.Changed', {})], _selected<-[('DataTable.RowSelected', {})], _submitted<-[('Input.Submitted', {})]

#### `tui.screens.settings.SettingsScreen(DataScreen)` — cli/tui/screens/settings.py:20
- BINDINGS (6): 'p'->'edit_preferences' 'Preferences'; 'g'->'edit_global' 'Global config'; 'n'->'new_vacation' 'New vacation'; 'u'->'edit_vacation' 'Edit vacation'; 'delete'->'delete_vacation' 'Delete vacation'; 'O'->'logout' 'Log out'
- actions: action_delete_vacation, action_edit_global, action_edit_preferences, action_edit_vacation, action_logout, action_new_vacation
- on_/watch_ etc: on_mount

#### `tui.screens.system.SystemScreen(DataScreen)` — cli/tui/screens/system.py:18
- BINDINGS (5): 'p'->'toggle_scheduler' 'Pause/resume'; 'S'->'sync' 'Sync sensors'; 'P'->'plant_sync' 'Plant-DB sync'; 'H'->'health_snapshot' 'Health snapshot'; 'delete'->'delete_job' 'Remove job'
- actions: action_delete_job, action_health_snapshot, action_plant_sync, action_sync, action_toggle_scheduler
- on_/watch_ etc: on_mount


---

## 5. Entry points & packaging — **[G]** (a wheel-content listing or `importlib.resources` existence test)

| Distribution | Import pkg | pyproject | Console script | Build |
|---|---|---|---|---|
| `greenhouse` (root, meta) | — | `pyproject.toml` (deps: the 3 libs; uv workspace `libs/*`; dev group pytest/pytest-cov/ruff) | — | — |
| `greenhouse-core` | `greenhouse_core` | `libs/greenhouse-core/pyproject.toml` | — | hatchling, `[tool.hatch.build.targets.wheel] packages=["greenhouse_core"]` |
| `greenhouse-server` | `greenhouse_server` | `libs/greenhouse-server/pyproject.toml` | `greenhouse-server = "greenhouse_server.app:main"` (`s/app.py:309`: `load_dotenv(); Settings(); create_app(); uvicorn.run(host, port)`) | hatchling, `packages=["greenhouse_server"]` |
| `greenhouse-cli` | `greenhouse_cli` | `libs/greenhouse-cli/pyproject.toml` | `greenhouse = "greenhouse_cli.main:app"` | hatchling, `packages=["greenhouse_cli"]` |

All four versions = `5.0.1` (mirrors `.release-please-manifest.json`; do not touch). No explicit `include`/`artifacts`
config: hatch ships every non-ignored file under each package dir. Non-Python package data that must keep its relative path:

- `core/alembic.ini` (read by `core/database.py:62 _alembic_config` via `Path(__file__).parent`), `core/migrations/env.py`, `core/migrations/script.py.mako`, `core/migrations/versions/*.py` (10 revisions).
- `core/data/plant_database.json` (via `importlib.resources.files("greenhouse_core") / "data" / "plant_database.json"`, `core/plant_db.py:8`).
- `core/devices/profiles/ik10pw.json`, `core/devices/profiles/tr301z.json`.
- `s/web/templates/**` (57 files; `s/web/templating.py:8`), `s/web/static/**` (6 files: `app.css`, `app.js`, 3 Geist woff2 fonts; mounted at `/static` name `static`, `s/app.py:231-232`).
- `cli/tui/app.tcss` (`CSS_PATH`, relative to `cli/tui/app.py`).

---

## 6. Configuration

### 6.1 `Settings` (`s/config.py:7`) — `BaseSettings`, `env_prefix="IRRIGATION_"`, `env_file=".env"`, `case_sensitive=False`, `extra="ignore"`, `populate_by_name=True`

Fields in declaration order (order is part of the frozen schema). `GREENHOUSE_*` names are `validation_alias=AliasChoices("GREENHOUSE_…", "<field>")`.

| field | env var | type | default |
|---|---|---|---|
| `db_url` | `IRRIGATION_DB_URL` | `str` | `'sqlite:///data/irrigation.db'` |
| `host` | `IRRIGATION_HOST` | `str` | `'0.0.0.0'` |
| `port` | `IRRIGATION_PORT` | `int` | `8000` |
| `debug` | `IRRIGATION_DEBUG` | `bool` | `False` |
| `plant_db_path` | `IRRIGATION_PLANT_DB_PATH` | `str | None` | `None` |
| `weather_lat` | `IRRIGATION_WEATHER_LAT` | `float` | `45.464` |
| `weather_lon` | `IRRIGATION_WEATHER_LON` | `float` | `9.189` |
| `sync_interval_minutes` | `IRRIGATION_SYNC_INTERVAL_MINUTES` | `int` | `180` |
| `check_cron_hours` | `IRRIGATION_CHECK_CRON_HOURS` | `str` | `'*'` |
| `check_interval_hours` | `IRRIGATION_CHECK_INTERVAL_HOURS` | `int | None` | `None` |
| `enable_scheduler` | `IRRIGATION_ENABLE_SCHEDULER` | `bool` | `True` |
| `mcp_token` | `GREENHOUSE_MCP_TOKEN` | `str | None` | `None` |
| `ntfy_server_url` | `GREENHOUSE_NTFY_SERVER_URL` | `str | None` | `None` |
| `ntfy_topic` | `GREENHOUSE_NTFY_TOPIC` | `str | None` | `None` |
| `ntfy_token` | `GREENHOUSE_NTFY_TOKEN` | `str | None` | `None` |
| `auth_enabled` | `IRRIGATION_AUTH_ENABLED` | `bool` | `True` |
| `auth_secret_key` | `GREENHOUSE_AUTH_SECRET_KEY` | `str | None` | `None` |
| `auth_token_ttl_minutes` | `IRRIGATION_AUTH_TOKEN_TTL_MINUTES` | `int` | `1440` |
| `auth_cookie_name` | `IRRIGATION_AUTH_COOKIE_NAME` | `str` | `'greenhouse_session'` |
| `auth_cookie_secure` | `IRRIGATION_AUTH_COOKIE_SECURE` | `bool` | `False` |
| `auth_admin_username` | `GREENHOUSE_AUTH_ADMIN_USERNAME` | `str | None` | `None` |
| `auth_admin_password` | `GREENHOUSE_AUTH_ADMIN_PASSWORD` | `str | None` | `None` |
| `pump_watcher_enabled` | `IRRIGATION_PUMP_WATCHER_ENABLED` | `bool` | `True` |
| `pump_watcher_poll_seconds` | `IRRIGATION_PUMP_WATCHER_POLL_SECONDS` | `float` | `2.0` |
| `pump_watcher_warmup_seconds` | `IRRIGATION_PUMP_WATCHER_WARMUP_SECONDS` | `float` | `5.0` |
| `pump_watcher_max_read_failures` | `IRRIGATION_PUMP_WATCHER_MAX_READ_FAILURES` | `int` | `5` |

Property `check_cron_hours_explicit` (`s/config.py:89`) = `"check_cron_hours" in model_fields_set`.

Startup validation messages (exact text frozen) — **[T: tests/server/test_scheduler_settings.py (12 tests)]**:
- `s/config.py:51` `IRRIGATION_SYNC_INTERVAL_MINUTES must be a positive number of minutes, got {value}` (value ≤ 0).
- `s/config.py:65-66` `IRRIGATION_CHECK_CRON_HOURS={value!r} is not a valid cron hour expression (e.g. '*', '0,6,12,18', '*/3', '6-20/2'): {exc}` (parsed with APScheduler `CronTrigger(hour=value, minute=0)`).
- `s/config.py:83-85` `IRRIGATION_CHECK_INTERVAL_HOURS={n} is not supported: it is translated to the cron hour step '*/N', so N must be 1-23 and divide 24 evenly (1, 2, 3, 4, 6, 8, 12). Set the hours explicitly instead, e.g. IRRIGATION_CHECK_CRON_HOURS={suggestion}` (suggestion `*` if n≤0, `0` if n≥24, else `range(0,24,n)` joined by commas). Skipped when cron hours explicitly set.
- Runtime warnings in `s/scheduler.py:116 _resolve_check_cron_hours`: both set → `"Both IRRIGATION_CHECK_CRON_HOURS and the deprecated IRRIGATION_CHECK_INTERVAL_HOURS are set; using IRRIGATION_CHECK_CRON_HOURS=%r and ignoring the interval."`; legacy only → `"IRRIGATION_CHECK_INTERVAL_HOURS is deprecated; set IRRIGATION_CHECK_CRON_HOURS instead. Translating value %d to '*/%d'."` and returns `*/N`. **[T: test_legacy_used_only_when_cron_unset, test_explicit_star_beats_legacy_interval]**
- `s/auth.py:336-341` bootstrap warning `"Auth is enabled but no users exist and GREENHOUSE_AUTH_ADMIN_USERNAME / GREENHOUSE_AUTH_ADMIN_PASSWORD are not set. The API will reject every request with 401 until a user is created."`; info `"Bootstrapped initial admin user %r from environment."` **[G]**

### 6.2 Env vars read outside `Settings` (8)

| var | default | where |
|---|---|---|
| `TUYA_CLIENT_ID` | `""` | `core/devices/gateway.py:116` (DeviceGateway ctor; missing creds → degraded mode, `app.state.device_gateway=None`, `s/app.py:59-75`) |
| `TUYA_CLIENT_SECRET` | `""` | `core/devices/gateway.py:117` |
| `TUYA_REGION` | `"eu"` | `core/devices/gateway.py:118` |
| `IRRIGATION_PLANT_DB_PATH` | bundled JSON | `core/plant_db.py:9-13` (read **at import time** into `PLANT_DB_PATH`; also a `Settings` field) |
| `IRRIGATION_TZ` | `"UTC"` | `core/utils.py:95` (display-tz fallback when no preference recorded) **[T: tests/test_utils.py]** |
| `IRRIGATION_DB_URL` | `sqlite:///data/irrigation.db` | `core/migrations/env.py:30` (Alembic CLI path; also a `Settings` field) |
| `IRRIGATION_SERVER_URL` | `http://localhost:8000` | CLI (`cli/commands/_helpers.py:14`, `auth.py:26`, `tui.py:35`) |
| `GREENHOUSE_API_TOKEN` | — | CLI token override (`cli/client.py:24`) |

(+ `XDG_CONFIG_HOME` for the CLI token path.) `.env.example` at repo root documents TUYA_*, GREENHOUSE_MCP_TOKEN,
GREENHOUSE_AUTH_*, IRRIGATION_AUTH_ENABLED/TOKEN_TTL_MINUTES/COOKIE_SECURE, DB_URL, PLANT_DB_PATH, HOST, PORT,
SYNC_INTERVAL_MINUTES, CHECK_CRON_HOURS, CHECK_INTERVAL_HOURS. Pins for the Settings schema itself: **[G]**
(snapshot `[(name, str(annotation), default, env names)]` + `Settings.model_config`).

---

## 7. Persistence

- 17 tables / 17 mapped classes (`core/models.py`): Base:7, Cluster:22, Plant:43, Irrigator:64, Sensor:86, SensorAssignment:104, SensorReading:137, IrrigationEvent:160, IrrigationConfig:180, GlobalIrrigationConfig:211, DecisionLog:233, ActivityEvent:261, Alert:286, PlantHealthDaily:321, IrrigationWindow:347, VacationWindow:370, User:394, UserPreferences:407. Module constants `ENTITY_CLUSTER="cluster"`, `ENTITY_PLANT="plant"`, `ENTITY_SENSOR="sensor"`, `ENTITY_IRRIGATOR="irrigator"`, `ENTITY_SYSTEM="system"` (`core/models.py:15-19`).
- Alembic: head **`9f2b5e7c6a31`** (`irrigator_cluster_unique`); chain 1c9b09f02432 → 2a4d1e7c8f02 → 3f1a8b5c9d10 → 4a7e2b9c8d10 → 5b8f3c2d4e11 → 6c9d4e2f3a12 → 7d3c1f9b4a08 → 7d0e5f3a4b13 → 8e1a4d6c5f20 → 9f2b5e7c6a31. `core/database.py:29 init_db` (empty → upgrade head; legacy tables w/o `alembic_version` → `create_all` + additive `ALTER TABLE … ADD COLUMN` + stamp head; else upgrade head), `head_revision()` (`:93`). `render_as_batch=True` in `env.py`. **[T: tests/test_migrations.py — test_empty_db_runs_baseline_to_head, test_idempotent_upgrade, test_device_type_backfill_rewrites_legacy_values, test_legacy_partial_db_gets_repaired]**; literal head id and ORM DDL: **[G]** (scratchpad `orm_ddl.sql`, sha above).
- Alembic env logging: `core/migrations/env.py:20-26` calls `fileConfig(config.config_file_name, disable_existing_loggers=False)`; `core/alembic.ini` loggers root=WARN, sqlalchemy.engine=WARN, alembic=INFO, console handler to stderr, format `%(levelname)-5.5s [%(name)s] %(message)s`. **[T: test_init_db_does_not_disable_application_loggers]** (asserts logger `greenhouse_server.services.irrigation` stays enabled — logger names are `logging.getLogger(__name__)` everywhere, so **moving a module renames its logger**).
- Serialized JSON columns (format frozen): `irrigators.config`, `sensors.config` (JSON text), `decision_logs.payload_json` (full `IrrigationDecision` dump), `alerts.payload_json`, `activity_events.payload_json`. **[T+G]**

Generated column dump:

(17 tables; flags: PK, NN=NOT NULL, UQ, FK->target, IX)

- **activity_events** (9 cols): id:INTEGER PK, timestamp:INTEGER NN, source:VARCHAR NN, entity_type:VARCHAR NN, entity_id:INTEGER, severity:VARCHAR NN, code:VARCHAR NN, message:VARCHAR NN, payload_json:VARCHAR; indexes: ['idx_activity_events_source(source)', 'idx_activity_events_entity(entity_type,entity_id)', 'idx_activity_events_timestamp(timestamp)']
- **alerts** (18 cols): id:INTEGER PK, dedup_key:VARCHAR NN UQ, source:VARCHAR NN, code:VARCHAR NN, severity:VARCHAR NN, entity_type:VARCHAR NN, entity_id:INTEGER, cluster_id:INTEGER, plant_id:INTEGER, title:VARCHAR NN, message:VARCHAR NN, payload_json:VARCHAR, status:VARCHAR NN, first_seen_at:INTEGER NN, last_seen_at:INTEGER NN, occurrence_count:INTEGER NN, acknowledged_at:INTEGER, resolved_at:INTEGER; constraints: ['UniqueConstraint(dedup_key)']; indexes: ['idx_alerts_entity(entity_type,entity_id)', 'idx_alerts_status(status)', 'idx_alerts_dedup_key(dedup_key)']
- **clusters** (5 cols): id:INTEGER PK, name:VARCHAR NN, location:VARCHAR, created_at:INTEGER NN, environment:VARCHAR NN
- **decision_logs** (12 cols): id:INTEGER PK, cluster_id:INTEGER NN, evaluated_at:INTEGER NN, action:VARCHAR NN, duration_minutes:INTEGER NN, interval_hours:INTEGER NN, confidence:FLOAT NN, primary_code:VARCHAR, reason_text:VARCHAR NN, payload_json:VARCHAR NN, triggered_by:VARCHAR NN, actuated:BOOLEAN NN; indexes: ['idx_decision_logs_cluster_id(cluster_id)', 'idx_decision_logs_evaluated_at(evaluated_at)']
- **global_irrigation_config** (10 cols): id:INTEGER PK, mode:VARCHAR, duration_minutes:INTEGER, interval_hours:INTEGER, auto_run:BOOLEAN, daily_cap_minutes:INTEGER, max_events_per_day:INTEGER, quiet_start_hour:INTEGER, quiet_end_hour:INTEGER, last_updated:INTEGER NN
- **plant_health_daily** (10 cols): id:INTEGER PK, plant_id:INTEGER NN, date_key:VARCHAR NN, timestamp:INTEGER NN, score:FLOAT NN, soil_in_band_pct:FLOAT, temp_in_band_pct:FLOAT, humidity_in_band_pct:FLOAT, efficiency:FLOAT, sample_count:INTEGER NN; constraints: ['UniqueConstraint(plant_id,date_key)']; indexes: ['idx_plant_health_daily_plant(plant_id)']
- **user_preferences** (12 cols): id:INTEGER PK, units:VARCHAR NN, timezone:VARCHAR NN, theme:VARCHAR NN, default_cluster_id:INTEGER, refresh_interval_seconds:INTEGER NN, dry_run_global:BOOLEAN NN, scheduler_paused:BOOLEAN NN, notify_manual:BOOLEAN NN, notify_emergency:BOOLEAN NN, notify_alerts:BOOLEAN NN, notify_auto:BOOLEAN NN
- **users** (6 cols): id:INTEGER PK, username:VARCHAR NN UQ, hashed_password:VARCHAR NN, is_active:BOOLEAN NN, created_at:INTEGER NN, last_login_at:INTEGER; constraints: ['UniqueConstraint(username)']
- **vacation_windows** (6 cols): id:INTEGER PK, starts_at:INTEGER NN, ends_at:INTEGER NN, contact_email:VARCHAR, notes:VARCHAR, created_at:INTEGER NN
- **irrigation_configs** (11 cols): id:INTEGER PK, cluster_id:INTEGER NN UQ FK->clusters.id, mode:VARCHAR, duration_minutes:INTEGER, interval_hours:INTEGER, auto_run:BOOLEAN, last_updated:INTEGER NN, daily_cap_minutes:INTEGER, max_events_per_day:INTEGER, quiet_start_hour:INTEGER, quiet_end_hour:INTEGER; constraints: ['UniqueConstraint(cluster_id)']
- **irrigation_windows** (6 cols): id:INTEGER PK, cluster_id:INTEGER NN FK->clusters.id, weekday_mask:INTEGER NN, start_hour:INTEGER NN, end_hour:INTEGER NN, label:VARCHAR; indexes: ['idx_irrigation_windows_cluster(cluster_id)']
- **irrigators** (8 cols): id:INTEGER PK, cluster_id:INTEGER NN UQ FK->clusters.id, tuya_device_id:VARCHAR NN UQ, name:VARCHAR NN, type:VARCHAR NN, config:VARCHAR, reservoir_l:FLOAT, flow_rate_l_per_min:FLOAT; constraints: ['UniqueConstraint(tuya_device_id)', 'UniqueConstraint(cluster_id)']
- **plants** (11 cols): id:INTEGER PK, cluster_id:INTEGER NN FK->clusters.id, species:VARCHAR NN, category:VARCHAR, water_needs:VARCHAR, light_needs:VARCHAR, ideal_temp_min:FLOAT, ideal_temp_max:FLOAT, ideal_humidity_min:FLOAT, ideal_humidity_max:FLOAT, notes:VARCHAR
- **irrigation_events** (7 cols): id:INTEGER PK, irrigator_id:INTEGER NN FK->irrigators.id, timestamp:INTEGER NN, action:VARCHAR NN, duration_minutes:INTEGER, triggered_by:VARCHAR NN, notes:VARCHAR; indexes: ['idx_irrigation_events_timestamp(timestamp)', 'idx_irrigation_events_irrigator_id(irrigator_id)']
- **sensors** (7 cols): id:INTEGER PK, cluster_id:INTEGER NN FK->clusters.id, tuya_device_id:VARCHAR NN UQ, name:VARCHAR NN, type:VARCHAR NN, config:VARCHAR, plant_id:INTEGER FK->plants.id; constraints: ['UniqueConstraint(tuya_device_id)']
- **sensor_assignments** (5 cols): id:INTEGER PK, sensor_id:INTEGER NN FK->sensors.id, plant_id:INTEGER NN FK->plants.id, started_at:INTEGER NN, ended_at:INTEGER; indexes: ['idx_sensor_assignments_sensor(sensor_id)', 'idx_sensor_assignments_time(started_at,ended_at)', 'idx_sensor_assignments_plant(plant_id)']
- **sensor_readings** (9 cols): id:INTEGER PK, sensor_id:INTEGER NN FK->sensors.id, timestamp:INTEGER NN, temperature:FLOAT, soil_moisture:FLOAT, light:INTEGER, env_humidity:FLOAT, battery_state:VARCHAR, water_warning:BOOLEAN; constraints: ['UniqueConstraint(sensor_id,timestamp)']; indexes: ['idx_sensor_readings_sensor_id(sensor_id)', 'idx_sensor_readings_timestamp(timestamp)']

---

## 8. Public import surfaces — **[G]** (snapshot `sorted(n for n in dir(mod) if not n.startswith("_"))` per module; any move needs a re-export shim)

Subpackages with `__init__` re-exports and explicit `__all__`: `greenhouse_core.logic` (12), `greenhouse_core.devices` (19,
includes third-party `tinytuya`), `greenhouse_core.learning` (4). All other listed modules are plain modules without `__all__`.
Note name collisions: `greenhouse_core.models.Alert` (ORM) vs `greenhouse_core.learning.Alert` (learning model).
Generated listing: names defined in the module are bare; `Name[c.x.y]` = re-exported from `greenhouse_core.x.y`
(`s.` = `greenhouse_server.`); `(submodule)` = attribute that is a child module; stdlib/third-party callables are omitted but
non-callable imported values (e.g. `annotations`, `logger`, `func`) are listed because they are reachable as attributes.

#### `greenhouse_core.models` (module, core/models.py) — 23 names
ActivityEvent, Alert, Base, Cluster, DecisionLog, ENTITY_CLUSTER, ENTITY_IRRIGATOR, ENTITY_PLANT, ENTITY_SENSOR, ENTITY_SYSTEM, GlobalIrrigationConfig, IrrigationConfig, IrrigationEvent, IrrigationWindow, Irrigator, Plant, PlantHealthDaily, Sensor, SensorAssignment, SensorReading, User, UserPreferences, VacationWindow

#### `greenhouse_core.schemas` (module, core/schemas.py) — 100 names
ActivityEventResponse, ActivityListResponse, AlertListResponse, AlertResponse, AlertSummary, CareInsight, ChartDatasetResponse, ChartEventResponse, ChartPayloadResponse, ChartThresholdResponse, CheckAllResponse, CheckClusterResponse, ClusterBase, ClusterDetailResponse, ClusterInsightsResponse, ClusterResponse, ClusterStatusIrrigatorResponse, ClusterStatusResponse, ClusterStatusSensorResponse, ConfigResponse, CreateClusterRequest, CreateIrrigationWindowRequest, CreateIrrigatorRequest, CreatePlantRequest, CreateSchedulerJobRequest, CreateSensorRequest, DataQualityIssue, DataQualityReport, DecisionLogListResponse, DecisionLogResponse, EffectiveConfigResponse, EfficacyItemResponse, EfficacyListResponse, ForecastResponse, GlobalConfigResponse, HealthResponse, HeatmapCell, HeatmapResponse, HistoryResponse, IrrigateRequest, IrrigateResponse, IrrigationEventResponse, IrrigationWindowBase, IrrigationWindowListResponse, IrrigationWindowResponse, IrrigatorActionResponse, IrrigatorBase, IrrigatorHistoryResponse, IrrigatorListResponse, IrrigatorResponse, LearnResponse, LogManualRequest, LogManualResponse, MonitorResponse, MovePlantRequest, MultiMetricOverlayResponse, OverlayDataset, PlantBase, PlantHealthDailyResponse, PlantHealthResponse, PlantHealthTimelineResponse, PlantListResponse, PlantResponse, PreferencesResponse, PreferencesUpdateRequest, ReasonResponse, ResolvedConfigField, SchedulerJobResponse, SchedulerStateResponse, SearchHit, SearchResponse, SensorAssignmentListResponse, SensorAssignmentResponse, SensorBase, SensorHistoryResponse, SensorListResponse, SensorReadingResponse, SensorResponse, SensorStatusResponse, SetConfigRequest, StartIrrigatorRequest, StatsResponse, StopAllResponse, SuccessResponse, SyncPlantsRequest, SyncPlantsResponse, SyncRequest, SyncResponse, SystemHealthDevice, SystemHealthResponse, UpdateClusterRequest, UpdateGlobalConfigRequest, UpdateIrrigationWindowRequest, UpdateIrrigatorRequest, UpdatePlantRequest, UpdateSensorRequest, UpdateVacationWindowRequest, VacationCreateRequest, VacationListResponse, VacationResponse

#### `greenhouse_core.repository` (module, core/repository.py) — 26 names
ActivityEvent[c.models], Alert[c.models], Cluster[c.models], DEFAULT_AUTO_RUN, DEFAULT_DURATION_MINUTES, DEFAULT_INTERVAL_HOURS, DEFAULT_IRRIGATION_MODE, DecisionLog[c.models], ENTITY_PLANT, ENTITY_SENSOR, GlobalIrrigationConfig[c.models], IrrigationConfig[c.models], IrrigationEvent[c.models], IrrigationRepository, IrrigationWindow[c.models], IrrigatorExistsError, Irrigator[c.models], PlantHealthDaily[c.models], Plant[c.models], SameClusterMoveError, SensorAssignment[c.models], SensorReading[c.models], Sensor[c.models], UserPreferences[c.models], VacationWindow[c.models], func

#### `greenhouse_core.constants` (module, core/constants.py) — 100 names
BATTERY_CRITICAL_PCT, BATTERY_LOW_PCT, CLEANING_HAMPEL_MIN_READINGS, CLEANING_HAMPEL_N_SIGMA, CLEANING_HAMPEL_WINDOW_RADIUS, CLEANING_MAD_FLOOR, CLEANING_MAD_SCALE, CONFIDENCE_CONFIG_FALLBACK, CONFIDENCE_CONFLICT, CONFIDENCE_COOLDOWN, CONFIDENCE_CRITICAL_STRESS, CONFIDENCE_NO_DATA, CONFIDENCE_OVER_WATERING, CONFIDENCE_SENSOR_ADEQUATE, CONFIDENCE_SENSOR_DRY, CONFIDENCE_SENSOR_VERY_DRY, CONFIDENCE_SENSOR_WET, CONFIDENCE_TEMP_FALLBACK, CONFIDENCE_WATER_WARNING, CONFLICT_DURATION_MINUTES, CONFLICT_INTERVAL_HOURS, CONFLICT_WET_MARGIN, DEFAULT_AUTO_RUN, DEFAULT_DURATION_MINUTES, DEFAULT_INTERVAL_HOURS, DEFAULT_IRRIGATION_MODE, DEFAULT_LATITUDE, DEFAULT_LONGITUDE, DEFAULT_PREFERRED_WATER_HOURS, DEFAULT_QUIET_END_HOUR, DEFAULT_QUIET_START_HOUR, DEFAULT_SEASON_MULTIPLIER_INDOOR, DEFAULT_SEASON_MULTIPLIER_OUTDOOR, DEFAULT_SOIL_MOISTURE_MAX, DEFAULT_SOIL_MOISTURE_MIN, HEALTH_POLL_IDLE_MINUTES, HUMIDITY_HIGH_INTERVAL_STEP, HUMIDITY_HIGH_OFFSET, HUMIDITY_LOW_INTERVAL_STEP, HUMIDITY_LOW_OFFSET, HUMIDITY_VERY_LOW_INTERVAL_STEP, HUMIDITY_VERY_LOW_OFFSET, LEAK_AFTER_WINDOW_SECONDS, LEAK_ALERT_CODE, LEAK_BEFORE_WINDOW_SECONDS, LEAK_CHECK_DELAY_SECONDS, LEAK_HOLD_HOURS, LEAK_MIN_AFTER_SAMPLES, LEAK_MIN_BEFORE_SAMPLES, LEAK_PINNED_MIN_SAMPLES, LEAK_PINNED_THRESHOLD, LEAK_RISING_DELTA, LEAK_SETTLE_TOLERANCE, LEARNING_MIN_ABSORPTION_PER_MIN, LEARNING_MIN_EFFICIENCY, LEARNING_MIN_EVENTS, LEARNING_OVER_WATER_THRESHOLD, LEARNING_RAPID_DRAINAGE_THRESHOLD, LIGHT_BRIGHT, LIGHT_BRIGHT_INTERVAL_STEP, LIGHT_DARK, LIGHT_DARK_INTERVAL_STEP, LIGHT_VERY_BRIGHT, LIGHT_VERY_BRIGHT_DURATION_STEP, LIGHT_VERY_BRIGHT_INTERVAL_STEP, LIGHT_VERY_DARK, LIGHT_VERY_DARK_INTERVAL_STEP, MAX_DURATION_MINUTES, MAX_INTERVAL_HOURS, MIN_COOLDOWN_HOURS, MIN_INTERVAL_HOURS, OFFLINE_AFTER_MINUTES, SENSOR_HEALTH_BACKFILL_WINDOW, SENSOR_PHYSICAL_RANGES, SENSOR_READING_STALE_SECONDS, SIGNAL_LOSS_THRESHOLD, SOIL_MOISTURE_CRITICAL, SOIL_MOISTURE_LOW, SOIL_MOISTURE_SATURATED, STRESS_DURATION_MINUTES, STRESS_INTERVAL_HOURS, TEMP_ADJUST_OFFSET, TEMP_COLD, TEMP_HIGH_INTERVAL_STEP, TEMP_HOT, TEMP_LOW_INTERVAL_STEP, TEMP_WARM, TREND_MIN_READINGS, TREND_MOISTURE_INTERVAL_STEP, TREND_MOISTURE_THRESHOLD, TREND_TEMP_RISING_HOT_C, TREND_TEMP_RISING_INTERVAL_STEP, TREND_TEMP_THRESHOLD, TREND_UNDERWATERING_DURATION_STEP, VACATION_MIN_RUN_MINUTES, VACATION_RESERVOIR_USABLE_FRACTION, VERY_DRY_MARGIN, WATER_NEEDS_DURATION_STEP, WATER_NEEDS_HIGH_INTERVAL_STEP, WATER_NEEDS_LOW_INTERVAL_STEP

#### `greenhouse_core.utils` (module, core/utils.py) — 8 names
NIGHT_LUX_THRESHOLD, UTC, daytime_lux_readings, effective_light_threshold, format_timestamp, get_display_timezone, seasonal_light_factor, set_display_timezone

#### `greenhouse_core.plant_db` (module, core/plant_db.py) — 5 names
PLANT_DB_PATH, PlantDatabase, get_plant_database, reset_plant_database, set_plant_database

#### `greenhouse_core.logic` (package, core/logic/__init__.py) __all__=12 — 21 names
Action[c.logic.decision], DEVICE_BLOCKING_CODES, IrrigationDecision[c.logic.decision], IrrigationLogic[c.logic.engine], PerSensorSnapshot[c.logic.decision], Reason[c.logic.decision], SensorSnapshot[c.logic.decision], Severity[c.logic.decision], StressIndicators[c.logic.decision], Trends[c.logic.decision], TriggerCode[c.logic.decision], WeatherSnapshot[c.logic.decision], cleaning(submodule), decision(submodule), engine(submodule), fallback(submodule), plant_needs(submodule), sensors(submodule), stress(submodule), timing(submodule), trends(submodule)
__all__: ['Action', 'DEVICE_BLOCKING_CODES', 'IrrigationDecision', 'IrrigationLogic', 'PerSensorSnapshot', 'Reason', 'SensorSnapshot', 'Severity', 'StressIndicators', 'Trends', 'TriggerCode', 'WeatherSnapshot']

#### `greenhouse_core.devices` (package, core/devices/__init__.py) __all__=19 — 25 names
AbstractIrrigatorAdapter[c.devices.irrigators.base], AbstractSensorAdapter[c.devices.sensors.base], DATAPOINT_PARSERS, DeviceGateway[c.devices.gateway], DeviceHealthState[c.devices.health], DeviceRegistry[c.devices.registry], HealthAlarm[c.devices.health], IK10PWAdapter[c.devices.irrigators.ik10pw], IK10PW_PROFILE, IrrigatorProfile[c.devices.profile], SensorProfile[c.devices.profile], TR301ZAdapter[c.devices.sensors.tr301z], TR301Z_PROFILE, TuyaIrrigatorAdapter[c.devices.irrigators.tuya_generic], TuyaSensorAdapter[c.devices.sensors.tuya_generic], UnknownDeviceModel[c.devices.registry], alarm_indicates_no_water[c.devices.irrigators.ik10pw], annotations, build_default_registry, gateway(submodule), health(submodule), irrigators(submodule), profile(submodule), registry(submodule), sensors(submodule)
__all__: ['AbstractIrrigatorAdapter', 'AbstractSensorAdapter', 'DATAPOINT_PARSERS', 'DeviceGateway', 'DeviceHealthState', 'DeviceRegistry', 'HealthAlarm', 'IK10PWAdapter', 'IK10PW_PROFILE', 'IrrigatorProfile', 'SensorProfile', 'TR301ZAdapter', 'TR301Z_PROFILE', 'TuyaIrrigatorAdapter', 'TuyaSensorAdapter', 'UnknownDeviceModel', 'alarm_indicates_no_water', 'build_default_registry', 'tinytuya']

#### `greenhouse_core.learning` (package, core/learning/__init__.py) __all__=4 — 9 names
Alert[c.learning.models], IrrigationLearner[c.learning.learner], IrrigationResponse[c.learning.models], PlantProfile[c.learning.models], issues(submodule), learner(submodule), models(submodule), profiling(submodule), report(submodule)
__all__: ['Alert', 'IrrigationLearner', 'IrrigationResponse', 'PlantProfile']

#### `greenhouse_core.sync` (module, core/sync.py) — 5 names
DeviceGateway[c.devices.gateway], IrrigationRepository[c.repository], logger, sync_sensor_data, sync_single_sensor

#### `greenhouse_core.stats` (module, core/stats.py) — 6 names
IrrigationRepository[c.repository], export_csv, format_duration, format_timestamp[c.utils], get_irrigation_stats, print_stats_report

#### `greenhouse_core.database` (module, core/database.py) — 6 names
Base[c.models], create_db_engine, create_session_factory, head_revision, init_db, log

#### `greenhouse_core.auth` (module, core/auth.py) — 10 names
User[c.models], annotations, create_user, get_user, get_user_by_username, hash_password, needs_rehash, record_login, set_password, verify_password

#### `greenhouse_server.app` (module, s/app.py) — 23 names
DeviceGateway[c.devices.gateway], NtfyClient[s.services.notify], PlantDatabase[c.plant_db], Settings[s.config], WeatherClient[s.services.weather], apply_persisted_pause[s.scheduler], bootstrap_admin[s.auth], build_default_registry[c.devices], create_app, create_db_engine[c.database], create_session_factory[c.database], init_db[c.database], init_health_monitor[s.scheduler], init_scheduler[s.scheduler], main, rearm_leak_checks[s.services.irrigation], register_web_exception_handlers[s.web.exception_handlers], require_mcp_token, require_user[s.auth], set_display_timezone[c.utils], start_scheduler[s.scheduler], stop_scheduler[s.scheduler], web_router

#### `greenhouse_server.config` (module, s/config.py) — 1 names
Settings

#### `greenhouse_server.deps` (module, s/deps.py) — 35 names
ClusterServiceDep, ClusterService[s.services.cluster], DeviceGatewayDep, DeviceGateway[c.devices.gateway], DeviceHealthMonitor[s.services.health_monitor], DeviceRegistryDep, DeviceRegistry[c.devices.registry], IrrigationRepository[c.repository], IrrigationServiceDep, IrrigationService[s.services.irrigation], NtfyClient[s.services.notify], NtfyNotifierDep, PlantDatabase[c.plant_db], PlantDbDep, PlantHealthServiceDep, PlantHealthService[s.services.health], RepoDep, SessionDep, SyncServiceDep, SyncService[s.services.sync], WeatherClientDep, WeatherClient[s.services.weather], get_cluster_service, get_device_gateway, get_device_registry, get_health_monitor, get_irrigation_service, get_ntfy_notifier, get_plant_db, get_plant_health_service, get_repository, get_session, get_sync_service, get_weather_client, require_cluster

#### `greenhouse_server.auth` (module, s/auth.py) — 29 names
AuthConfigError, AuthError, AuthUserDep, AuthenticatedUser, JWT_ALGORITHM, JWT_AUDIENCE, MCP_USER_ID, MCP_USER_NAME, SYSTEM_USER_ID, SYSTEM_USER_NAME, Settings[s.config], User[c.models], annotations, authenticate, bootstrap_admin, clear_session_cookie, create_user[c.auth], decode_token, get_user[c.auth], get_user_by_username[c.auth], issue_token, logger, needs_rehash[c.auth], render_login_redirect, require_user, require_web_user, set_password[c.auth], set_session_cookie, verify_password[c.auth]

#### `greenhouse_server.scheduler` (module, s/scheduler.py) — 23 names
CHECK_ALL_JOB_ID, CoreJobError, DeviceGateway[c.devices.gateway], HEALTH_POLL_IDLE_MINUTES, IrrigationRepository[c.repository], JobNotRegisteredError, Settings[s.config], apply_persisted_pause, apply_timezone_preference, core_job_ids, delete_job, get_jobs, init_health_monitor, init_scheduler, is_check_all_paused, logger, reschedule_for_timezone, scheduler, set_check_all_paused, shutdown_requested, start_scheduler, stop_scheduler, wait_for_shutdown


---

## 9. Domain data

### 9.1 `constants.py` (`core/constants.py`, 256 lines) — 100 public names, values live in the file. **[G]** (snapshot `{n: getattr(constants, n)}`); individual thresholds are exercised by `tests/test_logic.py`, `test_engine_timing.py`, `test_leak_hold.py`, `test_cleaning.py`, … The full name list is in §8 under `greenhouse_core.constants`. Notables: `MIN_COOLDOWN_HOURS`, `SENSOR_READING_STALE_SECONDS`, `LEAK_HOLD_HOURS`, `LEAK_ALERT_CODE="leak_or_stuck_valve"` (`:156`), `HEALTH_POLL_IDLE_MINUTES` (re-exported by `greenhouse_server.scheduler`).

### 9.2 Decision enums (`core/logic/decision.py`) — **[G]** (no test enumerates members)

- `Action(StrEnum)` (`:8`): `irrigate`, `skip`.  `Severity(StrEnum)` (`:15`): `info`, `warning`, `critical`.
- `TriggerCode(StrEnum)` (`:23`), 46 members, in order: no_plants, cooldown, water_warning, water_stress, over_watering, sensor_very_dry, sensor_dry, sensor_adequate, sensor_wet, conflict, weather_skip, temp_fallback, config_fallback, no_data, daily_cap_hit, temp_high, temp_low, humidity_very_low, humidity_low, humidity_high, light_very_bright, light_bright, light_dark, light_very_dark, water_needs_high, water_needs_low, trend_moisture_declining, trend_moisture_rising, trend_temp_rising, underwatering_pattern, learning_alert, outside_window, seasonal_hold, seasonal_boost, vacation_active, vacation_rationing, vacation_budget_exhausted, quiet_hours, manual_override_quiet_hours, device_no_water, device_rain_detected, device_battery_low, device_battery_critical, device_signal_loss, device_offline, leak_hold.
- `DEVICE_BLOCKING_CODES` (`greenhouse_core.logic`): {device_no_water, device_offline, device_rain_detected}.
- `Reason` (`:107`), `IrrigationDecision` (`:204`) — field order/names appear in `decision_logs.payload_json` and OpenAPI (`ReasonResponse`).
- `HealthAlarm` (`greenhouse_core.devices`): no_water, rain_detected, low_battery, battery_critical, signal_loss, device_offline, sensor_fault.

### 9.3 Scheduler (`s/scheduler.py`) — process-wide `BackgroundScheduler(job_defaults={"misfire_grace_time": None, "coalesce": True, "max_instances": 1})` (`:26-28`)

Core jobs, registered by `init_scheduler` (`:146`) **in this order** (after `scheduler.configure(timezone=ZoneInfo(prefs tz or UTC))` and `remove_all_jobs()`), all `replace_existing=True`:

| # | id | name | trigger | func |
|---|---|---|---|---|
| 1 | `sensor_sync` | Sensor data sync | interval, `minutes=settings.sync_interval_minutes` | `_sync_job` (`:292`) |
| 2 | `check_all` | Check all clusters | cron `hour=_resolve_check_cron_hours(settings)`, `minute=0` (tz-bound) | `_check_job` (`:334`) |
| 3 | `plant_health_snapshot` | Daily plant health snapshot | cron `hour=0, minute=30` (tz-bound) | `_health_snapshot_job` (`:315`) |
| 4 | `sensor_anomaly` | Sensor anomaly scan | interval `minutes=15` | `_anomaly_job` (`:367`) |
| 5 | `device_health_monitor` | Device health monitor | interval `minutes=HEALTH_POLL_IDLE_MINUTES` | `_health_monitor_job` (`:383`) |

`CHECK_ALL_JOB_ID="check_all"` (`:446`); `_TZ_BOUND_CRON_JOBS=("check_all","plant_health_snapshot")` re-added on tz change (`reschedule_for_timezone`, `:223`). Ad-hoc job ids: `pump-watcher-{irrigator_id}-{started_at}` (`s/services/irrigation.py:165`), `leak-check-{cluster_id}-{started_at}` (`s/services/irrigation.py:307`). `get_jobs()` dict includes `trigger=str(job.trigger)` and `core` flag. Pause state persisted in `user_preferences.scheduler_paused` and re-applied at startup (`apply_persisted_pause`). Job-failure log lines: `"Sync job failed"`, `"Plant health snapshot job failed"`, `"Check job failed"`, `"Anomaly scan job failed"`, `"Device health monitor job failed"` (`logger.exception`). **[T: tests/server/test_scheduler_jobs.py (19), test_scheduler_pause.py, test_scheduler.py, test_scheduler_shutdown.py]**; literal id/name/trigger table **[G]** (`test_core_job_set_matches_registration` only compares the set to the API listing).

### 9.4 Persisted string vocabularies — **[T+G]**

- `IrrigationEvent.action`: `start` (auto success, manual start, log-manual), `attempted` (auto failure, `s/services/irrigation.py:544`), `stop` (shutdown / emergency), `off` (manual stop, `s/services/manual_control.py:177`), `aborted` (`EVENT_ACTION_ABORTED`, pump watcher, `s/services/pump_watcher.py:47`). `_enforce_cooldown` counts only `action == "start"` (Invariant 11).
- `IrrigationEvent.triggered_by`: `auto` (the engine-driven event at `s/services/irrigation.py:544` is always `auto`), `manual`, `emergency` (bulk stop, notes `"kill switch"`), `shutdown`, `pump_watcher`.
- `DecisionLog.triggered_by`: `auto`, or `manual` when `irrigate(force=true)` (`s/services/irrigation.py:448`).
- Alert/activity `source`: `learning`, `maintenance`, `decision`, `leak`, `anomaly`, `system`, `pump` (`s/services/alerts.py:26-32`), `health` (`s/services/health_monitor.py:52`), `irrigation`, `sensor`, `plant`, `local`.
- Alert/activity `code`s: `leak_or_stuck_valve`, `check_failed`, `pump_watcher_shutdown`, `leak_check`, `leak_hold`, `pump_dry_run` (activity + legacy alert code), `no_water` (pump-watcher alert = `HealthAlarm.NO_WATER`), health alarm values, `decision_skip`, `irrigated`, `actuation_failed`, `sensor_stale`, `sensor_drift`, `stale_sensor`, `unknown_species`, `sensor_without_plant`, `plant_without_sensor`, `irrigator_in_empty_cluster`, `cluster_without_config`, `duplicate_tuya_id`, `sensor_reassigned`, `plant_moved`. Device-health blocks map `HealthAlarm` → `TriggerCode` via `HEALTH_ALARM_TO_TRIGGER` (`s/services/irrigation.py:507`).
- Health-alert dedup key: `f"health:{entity_type}:{entity_id}:{alarm.value}"` (`s/services/health_monitor.py:91`).
- Alert `status`: `open` → `acknowledged` (`core/repository.py:849`) → `resolved`.
- ntfy titles/tags keyed by `triggered_by` (`s/services/notify.py`); priority `"5"` for emergency else `"3"`.

### 9.5 Log messages asserted by tests

- `tests/server/test_scheduler_shutdown.py`: `"unprotected"` (from `s/services/irrigation.py:97-102` warning) and `"device unreachable"` (stop_msg interpolated into `:86-91` error) in `caplog.text`.
- `tests/test_migrations.py::test_init_db_does_not_disable_application_loggers`: logger `greenhouse_server.services.irrigation` must stay enabled after `init_db`.
- No other test asserts on log text.

---

## 10. Error contracts — summary

- API/MCP: JSON `{"detail": <str|list>}` with the status codes in §1.2; headers from `HTTPException.headers` are **dropped** (observed bug).
- Web: HTML error templates with same status codes; auth failures → 303 `/login?next=…` or 204 + `HX-Redirect`.
- CLI: `Error: <detail>` on stderr + exit 1 (`call()`); exit 2 for `check` with alerts and `monitor` needing water; `detail` may be a list (422) and is printed via Python `str()`.
- TUI: 401 opens `LoginScreen`; other errors surface as notifications (pinned by `test_unreachable_server`).

## 11. Phase-1 golden snapshot checklist (items marked [G])

1. `app.openapi()` sorted JSON (covers paths, operationIds, response models, schemas field order/aliases, docstrings, tags). 
2. `mcp.tools` dump (names, descriptions, inputSchemas) + `/mcp` mount methods.
3. Web: per-route (template name, sorted context keys, status, redirect Location, HX headers) via a `TemplateResponse` spy over a seeded app; normalized HTML for dashboard, cluster detail, plant dashboard, preferences, scheduler, vacation, alerts, activity, health, quality, login, error page.
4. CLI: all 74 `--help` pages at fixed width; command tree dump (`dump_cli.py`).
5. TUI: BINDINGS / action_* / on_* / CSS_PATH / MODES dump (`dump_tui.py`), and `app.tcss` selector ↔ widget-id cross-check.
6. Settings schema dump + `model_config`; non-Settings env reads list.
7. ORM DDL (`orm_ddl.sql`) + literal Alembic head `9f2b5e7c6a31` + revision chain.
8. Public import surface per module (§8) incl. `__all__` lists.
9. `constants` name→value map; `TriggerCode`/`Action`/`Severity`/`HealthAlarm` member lists; `DEVICE_BLOCKING_CODES`.
10. Scheduler core-job table (id, name, trigger type/fields, order) after `create_app`.
11. Characterization test for the dropped `WWW-Authenticate` header (API 401 and MCP 401).
12. Package-data existence: alembic.ini, migrations/, data/plant_database.json, devices/profiles/*.json, web/templates, web/static, tui/app.tcss.

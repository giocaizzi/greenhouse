# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-04-28

### Added

- **Typed decision pipeline** — `IrrigationDecision` Pydantic model is now the canonical output of the irrigation engine, carrying `action`, `duration_minutes`, `interval_hours`, `confidence`, and a `reasons: list[Reason]` trail keyed by stable `TriggerCode` `StrEnum` values (`logic/decision.py`).
- **Decision audit log** — every evaluation (acted-on or not) is persisted to `decision_logs` via `DecisionLog`; includes `primary_code`, `reason_text`, `actuated`, `triggered_by`, and full `payload_json`. Accessible via `GET /api/v1/clusters/{id}/decisions`.
- **Alert inbox** — `Alert` model with deduplicated upsert (stable `dedup_key`), `open → acknowledged → resolved` lifecycle, `first_seen_at` / `last_seen_at` / `occurrence_count` tracking. Endpoints: `GET /api/v1/alerts`, `GET /api/v1/alerts/{id}`, `POST /api/v1/alerts/{id}/acknowledge`, `POST /api/v1/alerts/{id}/resolve`, `POST /api/v1/clusters/{id}/alerts/sync`, `POST /api/v1/alerts/sync`.
- **Activity timeline** — polymorphic `ActivityEvent` stream across all entity types. `GET /api/v1/activity`.
- **Forecast service + weather-aware skip** — `WeatherClient` fetches precipitation forecast; engine appends `weather_skip` reason and returns `SKIP` when precipitation ≥ threshold. `GET /api/v1/clusters/{id}/forecast`.
- **Plant health score** — daily 0–100 composite (in-band soil/temp/humidity time + learning efficiency) stored in `plant_health_daily`. Daily snapshot background job. `GET /api/v1/plants/{id}/health`, `POST /api/v1/plants/health/snapshot`.
- **Trust layer** — sensor anomaly scan (drift detection with std-floor at 1.0%, stale-data check), leak/stuck-valve detector, per-cluster per-day rate limit (`daily_cap_hit` trigger code).
- **Cluster insights** — aggregated signal cards per cluster. `GET /api/v1/clusters/{id}/insights`.
- **System health pulse** — overall DB, scheduler, and device status. `GET /api/v1/health/system`.
- **Data quality report** — per-sensor coverage and anomaly summary. `GET /api/v1/quality/report`.
- **Irrigation efficacy scorer** — post-event absorption efficiency per cycle. `GET /api/v1/clusters/{id}/efficacy`.
- **User preferences** — server-side `UserPreferences` model (units, timezone, theme, dry-run flag, notification settings). `GET /api/v1/preferences`, `PUT /api/v1/preferences`.
- **Vacation windows** — pause irrigation while away. `GET /api/v1/vacation`, `POST /api/v1/vacation`, `DELETE /api/v1/vacation/{id}`.
- **Global search** — search across clusters, plants, sensors, and irrigators. `GET /api/v1/search`.
- **Emergency stop-all** — `POST /api/v1/bulk/stop-all` stops all irrigators immediately.
- **Full CRUD** — edit (`PUT`) and delete (`DELETE`) for cluster, plant, sensor, and irrigator; `GET`-by-id for sensor and irrigator.
- **2026 design system** — elevation/motion/z-index tokens, top-of-page progress bar, toast notifications, command-K palette, bottom-sheet primitive, plant hero card, health ring, insight cards, decision-rationale rows, dry-run and vacation banners.
- **`ActivityEvent` and `DecisionLog` persistence foundation** — new SQLAlchemy models, repository methods, and Alembic migrations.

### Changed

- `IrrigationDecision` replaces the previous dict-based engine output. All call sites updated to consume the typed model.
- `TriggerCode`, `Action`, and `Severity` are `StrEnum` values; the engine appends structured `Reason` objects instead of free-text strings.
- FastAPI app `version` bumped to `1.2.0`.
- Sensor anomaly std-dev floor raised to 1.0% (fixes false-positive drift alerts on near-constant sensor series).
- `references/API.md` regenerated to cover the full 1.2.0 endpoint inventory (grouped by tag).
- `references/LOGIC.md` updated with typed-pipeline section, trust-layer description, and alert inbox lifecycle.

### Fixed

- Sensor anomaly scanner no longer triggers drift alerts on near-constant moisture series (std floor at 1.0%).

### Removed

- Dict-based engine output shape (`{"action": "...", "reason": "..."}`) — replaced by typed `IrrigationDecision`.

## [1.1.0] - 2025-12-01

### Added

- FastAPI server with JSON REST API at `/api/v1` and HTMX web UI at `/`.
- MCP server at `/mcp` via `fastapi-mcp` — every `/api/v1` endpoint auto-published as an MCP tool.
- uv workspace structure with three packages: `tuya-irrigation-core`, `tuya-irrigation-server`, `tuya-irrigation-cli`.
- SQLAlchemy v2 models with `Mapped` types and `UNIQUE(sensor_id, timestamp)` dedup.
- Repository pattern (`IrrigationRepository`).
- APScheduler background jobs (sensor sync every 30 min, cluster check every 6h).
- Typer CLI client (`tuya-irrigation`) — thin `httpx` client against `/api/v1`.
- Learning engine with absorption / drainage profiles and 7 alert types.
- Forecast weather integration via Open-Meteo for outdoor clusters.

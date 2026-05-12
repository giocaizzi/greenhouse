# AGENTS.md - Guidelines for AI Agents & Developers

## Project Overview

**greenhouse** is a smart plant irrigation system that:

1. **Reads** soil moisture / temperature / humidity / light from Tuya-compatible
   sensors via the Tuya Cloud API.
2. **Decides** when and how long to irrigate per *cluster* (a group of plants
   irrigated together) using evidence-based plant care data, multi-sensor
   conflict resolution (driest plant drives the call), a 6h global cooldown,
   per-day rate cap, and a weather-aware precipitation skip rule. Every
   evaluation produces a typed `IrrigationDecision` with a structured `Reason`
   trail keyed by stable `TriggerCode` enums, and is persisted to
   `decision_logs` whether or not it was acted on.
3. **Acts** by controlling Tuya irrigators directly over the local protocol
   (v3.5) for reliable duration control — never via the Cloud for actuation.
   A trust layer runs a leak/stuck-valve detector and sensor anomaly scan
   (drift + stale) before each actuation.
4. **Learns** from each irrigation cycle: builds per-plant absorption /
   drainage profiles, computes a daily 0–100 health score per plant, and raises
   advisory alerts (blocked drip, rapid drainage, chronic underwatering,
   unresolvable conflict, etc.). Learning never blocks decisions.
5. **Persists** every sensor reading, irrigation event, decision log, alert,
   and activity event into a local SQLite archive — Tuya Cloud is the live
   source, SQLite is the permanent record.

It exposes a FastAPI server (JSON API + HTMX web UI), a Typer CLI client, and
runs sync + check jobs in the background via APScheduler.

### Interfaces — four ways in, one source of truth

The server is the only thing that touches the DB and the devices. There are
four ways to talk to it:

1. **JSON REST API** at `/api/v1` — the authoritative entry point. OpenAPI
   docs at `/docs`. Full endpoint inventory: [references/API.md](references/API.md).
2. **HTMX web UI** at `/` — same FastAPI app, server-rendered. Calls the
   service layer **in-process** (not over HTTP), so it shares code with the
   API but isn't itself a client of it.
3. **CLI** (`greenhouse`) — a thin `httpx`-based client that builds
   requests against `/api/v1`. It does **not** import `greenhouse-core`
   and has no DB access; if the server isn't running, the CLI does nothing.
4. **MCP server** at `/mcp` — same FastAPI app, exposed via
   [`fastapi-mcp`](https://github.com/tadata-org/fastapi_mcp). Every
   `/api/v1` endpoint is auto-published as an MCP tool over streamable HTTP;
   web routes are excluded automatically because they set
   `include_in_schema=False`. **Auth is bearer-token, fail-closed**: the
   `require_mcp_token` dependency in `app.py` is wired into `FastApiMCP` via
   `AuthConfig(dependencies=[...])`. With `GREENHOUSE_MCP_TOKEN` unset the
   endpoint returns 503; with it set, clients must send
   `Authorization: Bearer <token>` or get 401. The token is checked against
   `settings.mcp_token` on every request. Wired in `app.py` after all
   routers are registered; the live `FastApiMCP` instance is stored on
   `app.state.mcp` for introspection in tests.

Stop the server and the UI, CLI, and MCP all go dark. Anything new the CLI
or an MCP tool should be able to do must first exist as an API endpoint.

> ⚠️ **MCP gives an LLM the ability to actuate physical irrigation hardware**
> (`/clusters/{id}/irrigate`, `/irrigators/{id}/start`, etc.). The bearer
> token is therefore equivalent to full physical-actuation authority — treat
> it like a root credential: high entropy (`openssl rand -hex 32`), unique
> per deployment, never committed, rotated on suspicion of compromise.

**API surface in 1.0.0** (all under `/api/v1`):

- Decisions audit log: `GET /clusters/{id}/decisions`
- Alert inbox: `GET /alerts`, `GET /alerts/{id}`, `POST /alerts/{id}/acknowledge`, `POST /alerts/{id}/resolve`, `POST /clusters/{id}/alerts/sync`, `POST /alerts/sync`
- Activity timeline: `GET /activity`
- Forecast: `GET /clusters/{id}/forecast`
- Plant health: `GET /plants/{id}/health`, `POST /plants/health/snapshot`
- Insights: `GET /clusters/{id}/insights`
- System health: `GET /health/system`
- Data quality: `GET /quality/report`
- Efficacy: `GET /clusters/{id}/efficacy`
- Preferences: `GET /preferences`, `PUT /preferences`
- Vacation: `GET /vacation`, `POST /vacation`, `DELETE /vacation/{id}`
- Search: `GET /search`
- Bulk stop: `POST /bulk/stop-all`

**Tech Stack:** Python 3.11+, uv workspaces, FastAPI, SQLAlchemy v2, Pydantic v2, Typer, ruff, pytest, SQLite, tinytuya, APScheduler, Jinja2 + HTMX + Chart.js + Pico.css (server-rendered web UI, no build step), `fastapi-mcp` (MCP server mounted on the same FastAPI app)

## Privacy & Security

### NO Personal Data in Git

**NEVER commit:** Device IDs, IP addresses, API keys, database files, personal configs.

**Verify before commit:**
```bash
git grep -i "bf60\|192.168\|local_key\|api.*key" -- '*.py' '*.md' '*.json'
```

**Personal data belongs in:** `data/*.db` (gitignored), `.env` (outside repo)

### Test Data

All test data uses fake/placeholder values centralized in `tests/fake_data.py`:
- Fake device IDs: `fake_tuya_device_aabbccdd`
- RFC 5737 IPs: `192.0.2.x`
- Generic names: "Test Cluster", "Test Sensor"

## Package Structure

uv workspace with three packages under `libs/`. Strict dependency direction:
**core ← server ← cli** (CLI never imports core, only the HTTP API).

- **`greenhouse-core`** — domain. SQLAlchemy v2 models, Pydantic v2 schemas,
  repository, Tuya Cloud + local device adapters, irrigation decision engine
  (`logic/`), post-irrigation learning (`learning/`), plant-care DB, project-wide
  thresholds (`constants.py`).
- **`greenhouse-server`** — FastAPI app exposing both:
  - **JSON API** under `/api/v1` (`routes/`).
  - **Web UI** at `/` (`web/`): HTMX + Jinja2 server-rendered, served by the
    same app. No SPA, no build step.

  Orchestration lives in `services/` (cluster, irrigation, sync, maintenance,
  charts, weather). Background jobs use APScheduler (`scheduler.py`).
- **`greenhouse-cli`** — Typer CLI. Talks to the API over HTTP via an
  `httpx`-based `IrrigationClient` (in `client.py`); no DB or core imports.

  Surface: top-level **operation** commands registered directly on the root app
  (`status`, `irrigate`, `sync`, `stats`, etc. — see
  `commands/operations.py`) plus per-resource **sub-apps**: `cluster`, `plant`,
  `irrigator`, `sensor`, `config`. Server URL resolves in order:
  `--server` flag → `$IRRIGATION_SERVER_URL` → `http://localhost:8000`.
  Output is JSON via `rich.print_json`; `ServerError` maps to a non-zero exit
  through the shared `call()` helper in `commands/_helpers.py`.

**Naming:**
- Distribution: `greenhouse-core`, `greenhouse-server`, `greenhouse-cli`
- Import: `greenhouse_core`, `greenhouse_server`, `greenhouse_cli`
- Entry points: `greenhouse` (CLI), `greenhouse-server` (server)

Other top-level dirs: `data/` (gitignored runtime SQLite),
`tests/` (mirrors package layout: root = core, `server/`, `cli/`), `references/`.
The curated `plant_database.json` ships inside the core package at
`libs/greenhouse-core/greenhouse_core/data/`.

## Key Technical Decisions

1. **Protocol v3.5** for Rainpoint IK10PW (not 3.3)
2. **Tuya Cloud as live source**, SQLite as permanent archive
3. **SQLAlchemy v2** with `Mapped` types, `UNIQUE(sensor_id, timestamp)` for dedup
4. **Repository pattern** — `IrrigationRepository` wraps SQLAlchemy session
5. **min_soil_moisture** (driest plant) drives decisions, not average
6. **6h global cooldown** between any irrigations
7. **Evidence-based** plant care data with source citations
8. **Learning is advisory** — never blocks irrigation decisions
9. **All thresholds** centralized in `constants.py`
10. **Server is the only thing with DB and device access** — UI, CLI, and MCP all go through it (see "Interfaces" above). Web UI is HTMX + Jinja2 server-rendered (no SPA, no build step) and lives in the same FastAPI app.
11. **Every `/api/v1` route must declare `response_model=`, a Pydantic request body, and a Google-style docstring** — fastapi-mcp builds MCP tool schemas from the OpenAPI schema and uses the route's docstring as the tool description an LLM reads when picking tools. Untyped or undocumented routes produce tools the LLM cannot reason about safely. Enforced by `tests/server/test_mcp.py` (binary downloads like CSV export are exempted explicitly there). Docstring style: imperative summary, then `Args:` / `Returns:` / `Raises:` sections — skip framework-level params (`repo`, dependency injectables) since they only add noise to the MCP tool schema.
12. **`IrrigationDecision` is the canonical engine output** — a typed Pydantic model carrying `action`, `duration_minutes`, `interval_hours`, `confidence`, and a `reasons: list[Reason]` trail. Each `Reason` carries a stable `TriggerCode` enum value so the UI, MCP, and audit log can key on it without parsing free text. The decision is persisted to `decision_logs` for every evaluation (acted-on or not) — the `DecisionLog` record stores `primary_code`, `reason_text`, `actuated`, and the full `payload_json`.

## Development

### Setup

```bash
uv sync
uv run greenhouse-server    # Start server (API at /api/v1, web UI at /)
uv run greenhouse --help    # CLI help
make serve                       # Same as uv run greenhouse-server
```

### Code Quality

```bash
make check      # lint + test
make test       # uv run pytest
make lint       # uv run ruff check libs/ tests/
make format     # uv run ruff format libs/ tests/
make coverage   # pytest with coverage (60% threshold)
```

**Ruff config** (in `pyproject.toml`): line-length=120, py311+, rules: E/W/F/I/B/C4/UP

### Testing

```bash
uv run pytest -v
```

All tests use `conftest.py` fixtures and `fake_data.py`. Server + web tests use FastAPI TestClient with in-memory SQLite. CLI tests use Typer CliRunner with httpx MockTransport. Web tests assert on rendered HTML / fragment markers, not on visual layout. All use fake data — no real API calls.

### Adding Tests

- Core → `tests/test_*.py`
- JSON API endpoints → `tests/server/test_<resource>.py` (use TestClient)
- Web pages / HX fragments / template filters → `tests/server/test_web_*.py` (use TestClient, assert on HTML markers)
- CLI → `tests/cli/test_cli.py` (use CliRunner + MockTransport)

### Add a Plant Species

1. Research (min 2 sources), update `libs/greenhouse-core/greenhouse_core/data/plant_database.json`
2. Sync with `greenhouse plant sync`

### Pre-commit Check

```bash
make check
git diff --cached | grep -i "bf60\|192.168\|secret"
```

## Releases & Versioning

Releases are automated by [release-please](https://github.com/googleapis/release-please)
running in `.github/workflows/release-please.yml`. **Do not hand-edit
`CHANGELOG.md` or version strings** — release-please owns them.

### How it works

1. Push commits to `main` using [Conventional Commits](https://www.conventionalcommits.org/).
   The commit `type` decides the bump:
   - `feat:` → minor bump (e.g. `1.0.0 → 1.1.0`), `Added` section
   - `fix:` / `perf:` → patch bump (e.g. `1.0.0 → 1.0.1`), `Fixed` / `Performance`
   - `refactor:` → patch bump, `Changed`
   - `<type>!:` or `BREAKING CHANGE:` footer → major bump (e.g. `1.0.0 → 2.0.0`)
   - `docs:` / `chore:` / `test:` / `build:` / `ci:` / `style:` → no bump
     (hidden from the changelog by `release-please-config.json`)
2. release-please opens (or updates) a **Release PR** titled
   `chore(release): X.Y.Z` that batches every release-worthy commit. Merging
   the PR is the release.
3. On merge, release-please:
   - bumps `version` in the root `pyproject.toml` **and** in all three
     `libs/*/pyproject.toml` files (kept in lockstep via `extra-files`)
   - regenerates `CHANGELOG.md` from the Conventional Commits
   - tags `vX.Y.Z` and creates the GitHub Release
4. The `v*` tag triggers `.github/workflows/cd.yml`, which builds and
   pushes the Docker image to GHCR with cosign signing, SBOM, and Trivy scan.

### Files involved

| File | Purpose |
|------|---------|
| `release-please-config.json` | Release-type, changelog sections, packages, `extra-files` for workspace `pyproject.toml`s |
| `.release-please-manifest.json` | Current released version — release-please's source of truth, **not** the `pyproject.toml`s |
| `.github/workflows/release-please.yml` | Runs the action on every push to `main` |
| `CHANGELOG.md` | Generated; do not edit by hand |
| `pyproject.toml` (root + 3 workspace) | Version mirrored from the manifest on each release |

### Version source of truth

`.release-please-manifest.json` is canonical. The four `pyproject.toml` files
must always match it. If they drift (e.g. someone edits a `version` by hand),
release-please will still bump from the manifest and overwrite them on the
next release — but the drift will confuse `uv` and anyone reading the source,
so don't.

### Cutting a release manually (escape hatch)

If release-please is broken or you need an emergency cut, you can still
release by hand:

```bash
# 1. Set the same version in all four pyproject.toml files and the manifest
# 2. Update CHANGELOG.md by hand
# 3. Tag and create the GitHub Release — this triggers the Docker build
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --notes-from-tag
```

This is a last resort; prefer the Release PR flow.

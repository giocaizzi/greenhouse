# AGENTS.md - Guidelines for AI Agents & Developers

## Project Overview

**tuya-irrigation** is a smart plant irrigation system that:

1. **Reads** soil moisture / temperature / humidity / light from Tuya-compatible
   sensors via the Tuya Cloud API.
2. **Decides** when and how long to irrigate per *cluster* (a group of plants
   irrigated together) using evidence-based plant care data, multi-sensor
   conflict resolution (driest plant drives the call), and a 6h global cooldown.
3. **Acts** by controlling Tuya irrigators directly over the local protocol
   (v3.5) for reliable duration control — never via the Cloud for actuation.
4. **Learns** from each irrigation cycle: builds per-plant absorption /
   drainage profiles and raises advisory alerts (blocked drip, rapid drainage,
   chronic underwatering, unresolvable conflict, etc.). Learning never blocks
   decisions.
5. **Persists** every sensor reading and irrigation event into a local SQLite
   archive — Tuya Cloud is the live source, SQLite is the permanent record.

It exposes a FastAPI server (JSON API + HTMX web UI), a Typer CLI client, and
runs sync + check jobs in the background via APScheduler.

### Interfaces — three ways in, one source of truth

The server is the only thing that touches the DB and the devices. There are
three ways to talk to it:

1. **JSON REST API** at `/api/v1` — the authoritative entry point. OpenAPI
   docs at `/docs`.
2. **HTMX web UI** at `/` — same FastAPI app, server-rendered. Calls the
   service layer **in-process** (not over HTTP), so it shares code with the
   API but isn't itself a client of it.
3. **CLI** (`tuya-irrigation`) — a thin `httpx`-based client that builds
   requests against `/api/v1`. It does **not** import `tuya-irrigation-core`
   and has no DB access; if the server isn't running, the CLI does nothing.

Stop the server and both the UI and the CLI go dark. Anything new the CLI
should be able to do must first exist as an API endpoint.

**Tech Stack:** Python 3.11+, uv workspaces, FastAPI, SQLAlchemy v2, Pydantic v2, Typer, ruff, pytest, SQLite, tinytuya, APScheduler, Jinja2 + HTMX + Chart.js + Pico.css (server-rendered web UI, no build step)

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

- **`tuya-irrigation-core`** — domain. SQLAlchemy v2 models, Pydantic v2 schemas,
  repository, Tuya Cloud + local device adapters, irrigation decision engine
  (`logic/`), post-irrigation learning (`learning/`), plant-care DB, project-wide
  thresholds (`constants.py`).
- **`tuya-irrigation-server`** — FastAPI app exposing both:
  - **JSON API** under `/api/v1` (`routes/`).
  - **Web UI** at `/` (`web/`): HTMX + Jinja2 server-rendered, served by the
    same app. No SPA, no build step.

  Orchestration lives in `services/` (cluster, irrigation, sync, maintenance,
  charts, weather). Background jobs use APScheduler (`scheduler.py`).
- **`tuya-irrigation-cli`** — Typer CLI. Talks to the API over HTTP via an
  `httpx`-based `IrrigationClient` (in `client.py`); no DB or core imports.

  Surface: top-level **operation** commands registered directly on the root app
  (`status`, `irrigate`, `sync`, `stats`, etc. — see
  `commands/operations.py`) plus per-resource **sub-apps**: `cluster`, `plant`,
  `irrigator`, `sensor`, `config`. Server URL resolves in order:
  `--server` flag → `$IRRIGATION_SERVER_URL` → `http://localhost:8000`.
  Output is JSON via `rich.print_json`; `ServerError` maps to a non-zero exit
  through the shared `call()` helper in `commands/_helpers.py`.

**Naming:**
- Distribution: `tuya-irrigation-core`, `tuya-irrigation-server`, `tuya-irrigation-cli`
- Import: `tuya_irrigation_core`, `tuya_irrigation_server`, `tuya_irrigation_cli`
- Entry points: `tuya-irrigation` (CLI), `tuya-irrigation-server` (server)

Other top-level dirs: `data/` (gitignored runtime + `plant_database.json`),
`tests/` (mirrors package layout: root = core, `server/`, `cli/`), `references/`.

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
10. **Server is the only thing with DB and device access** — UI and CLI go through it (see "Interfaces" above). Web UI is HTMX + Jinja2 server-rendered (no SPA, no build step) and lives in the same FastAPI app.

## Development

### Setup

```bash
uv sync
uv run tuya-irrigation-server    # Start server (API at /api/v1, web UI at /)
uv run tuya-irrigation --help    # CLI help
make serve                       # Same as uv run tuya-irrigation-server
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

1. Research (min 2 sources), update `data/plant_database.json`
2. Sync with `tuya-irrigation plant sync`

### Pre-commit Check

```bash
make check
git diff --cached | grep -i "bf60\|192.168\|secret"
```

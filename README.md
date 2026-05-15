<div align="center">

# greenhouse


**Smart plant irrigation system with Tuya IoT sensors, evidence-based plant care, and self-learning efficiency analysis.**

[![CI](https://img.shields.io/github/actions/workflow/status/giocaizzi/greenhouse/ci.yml?branch=main&label=CI)](https://github.com/giocaizzi/greenhouse/actions)
[![codecov](https://codecov.io/gh/giocaizzi/greenhouse/graph/badge.svg)](https://codecov.io/gh/giocaizzi/greenhouse)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/github/license/giocaizzi/greenhouse)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/badge/uv-DE5FE9?logo=uv&logoColor=white)](https://github.com/astral-sh/uv)

</div>

## Overview

**greenhouse** monitors soil moisture, temperature, humidity, and light from Tuya-compatible sensors and makes smart irrigation decisions based on evidence-based plant care data. It learns from past irrigation cycles to detect efficiency issues, blocked drips, and unresolvable plant conflicts.

```bash
uv sync
uv run greenhouse-server        # start REST API + web UI
uv run greenhouse check --all   # check all clusters
```

## How it decides

1. **Reads** sensor data from Tuya Cloud, synced to a local SQLite archive.
2. **Decides** using a typed `IrrigationDecision` pipeline: cooldown check, stress detection, multi-sensor conflict resolution, trend analysis, preferred irrigation windows, evidence-based moisture targets. Every evaluation produces a structured `Reason` trail and is persisted whether or not it was acted on.
3. **Acts** by controlling Tuya irrigators over local protocol v3.5.
4. **Learns** — builds per-plant absorption/drainage profiles; raises advisory alerts (blocked drip, rapid drainage, chronic underwatering, unresolvable conflict). Learning never blocks decisions.
5. **Persists** sensor readings, irrigation events, decision logs, alerts, activity events, and sensor-to-plant assignment history in a local SQLite archive. Tuya Cloud is the live source; SQLite is the permanent record.

The `irrigation_windows` table holds per-cluster preferred watering hours (with a weekday bitmask) so the engine waters in the morning by default; stress overrides still fire outside windows. The `sensor_assignments` table records every time a probe is moved between plants, so historical readings remain attributed to the plant they were actually measuring — not whichever plant the sensor currently points at.

## Interfaces — four ways in, one source of truth

| Interface | URL | Notes |
|-----------|-----|-------|
| **JSON REST API** | `/api/v1` | Authoritative entry point. JWT-bearer auth (`POST /api/v1/auth/login`). OpenAPI docs at `/docs`. |
| **Web UI** | `/` | HTMX + Jinja2 server-rendered. Session cookie auth via `/login`. Pages cover dashboard, per-plant charts, irrigators, history, decisions, scheduler, alerts, **vacation**, **irrigation windows**, and health. |
| **CLI** | `greenhouse` | Thin `httpx` client against `/api/v1`. No DB access. |
| **MCP server** | `/mcp` | Every `/api/v1` endpoint as an MCP tool via `fastapi-mcp`. Bearer-token auth (`GREENHOUSE_MCP_TOKEN`). Fails closed: unset token → 503. |

Stop the server and all four go dark. Anything new the CLI or an MCP tool should be able to do must first exist as an API endpoint.

### Authentication

- **API and Web UI**: a single admin user is bootstrapped from `GREENHOUSE_AUTH_ADMIN_USERNAME` / `GREENHOUSE_AUTH_ADMIN_PASSWORD` on first boot. `POST /api/v1/auth/login` returns a JWT signed with `GREENHOUSE_AUTH_SECRET_KEY`; pass it as `Authorization: Bearer …`. The web UI sets a cookie. For local development, set `IRRIGATION_AUTH_ENABLED=false` to disable auth entirely (never do this in production).
- **MCP**: gated by a separate static bearer token (`GREENHOUSE_MCP_TOKEN`). Generate with `openssl rand -hex 32`. The MCP layer can actuate physical hardware (`/clusters/{id}/irrigate`, `/irrigators/{id}/start`, bulk emergency stop), so treat the token like a root credential, never commit it, and rotate on suspected compromise.

> **Warning:** Both auth boundaries are mandatory in production. The MCP token is not the API token — they protect different surfaces and must be set independently.

## Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- Tuya Cloud credentials ([Tuya IoT Platform](https://iot.tuya.com/))

### Installation

```bash
git clone https://github.com/giocaizzi/greenhouse.git
cd greenhouse
uv sync
```

### Configuration

Copy `.env.example` and fill in your Tuya credentials:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `TUYA_CLIENT_ID` | Yes | Tuya IoT Platform client ID |
| `TUYA_CLIENT_SECRET` | Yes | Tuya IoT Platform client secret |
| `TUYA_REGION` | Yes | `eu`, `us`, `cn`, or `in` |
| `GREENHOUSE_AUTH_SECRET_KEY` | Yes (prod) | JWT signing key for API/Web auth. Generate with `openssl rand -hex 32`. |
| `GREENHOUSE_AUTH_ADMIN_USERNAME` | Yes (prod) | Admin username bootstrapped on first boot. |
| `GREENHOUSE_AUTH_ADMIN_PASSWORD` | Yes (prod) | Admin password (hashed at rest). |
| `GREENHOUSE_MCP_TOKEN` | Yes (for MCP) | Static bearer token for `/mcp`. Unset → MCP fails closed with 503. |
| `IRRIGATION_AUTH_ENABLED` | No | Set to `false` to disable API/Web auth in local dev. Default: `true`. |
| `IRRIGATION_DB_URL` | No | SQLite URL (default: `sqlite:///data/irrigation.db`) |
| `IRRIGATION_SERVER_URL` | No | CLI server URL (default: `http://localhost:8000`) |

### Usage

```bash
# Start the server (API at /api/v1, web UI at /)
uv run greenhouse-server

# Set up a cluster
uv run greenhouse cluster add "Living Room" --environment indoor
uv run greenhouse plant add "Monstera deliciosa" --cluster 1
uv run greenhouse sensor add --cluster 1 --device-id YOUR_DEVICE_ID --name "Monstera Sensor" --type soil_moisture

# Operations
uv run greenhouse status 1          # full cluster overview
uv run greenhouse irrigate 1        # smart irrigation pipeline
uv run greenhouse check --all       # check all clusters + alerts
uv run greenhouse learn 1           # learning report
uv run greenhouse stats 1 --days 7  # irrigation statistics
```

Same data is also available via:

- **Web UI** — open `http://localhost:8000/` for the HTMX dashboard: clusters, per-plant charts, irrigators, history, decisions, scheduler, alerts, vacation windows, irrigation windows, system health.
- **REST API** — `http://localhost:8000/api/v1/...`; OpenAPI docs at `http://localhost:8000/docs`. Authenticate via `POST /api/v1/auth/login` to obtain a JWT.
- **MCP server** — `http://localhost:8000/mcp` (streamable HTTP). Set `GREENHOUSE_MCP_TOKEN` and pass it as a bearer token; point an MCP client (e.g. Claude Desktop) at it and every API endpoint shows up as a tool.

## Development

```bash
make check      # lint + test
make test       # uv run pytest
make lint       # uv run ruff check libs/ tests/
make format     # uv run ruff format libs/ tests/
make coverage   # pytest with coverage (60% threshold)
```

See [AGENTS.md](AGENTS.md) for full developer guide, package structure, and testing conventions.

## Releases

Versioning is automated by [release-please](https://github.com/googleapis/release-please).
Push [Conventional Commits](https://www.conventionalcommits.org/) to `main` and
the bot will keep a Release PR open; merging it bumps `pyproject.toml` (root +
all three workspace packages), regenerates `CHANGELOG.md`, tags `vX.Y.Z`, and
publishes the GitHub Release. The new tag then triggers
`.github/workflows/cd.yml` to build and push the signed Docker image to GHCR.

Do not hand-edit `CHANGELOG.md` or `version` fields — release-please owns them.
See [AGENTS.md → Releases — project-specific facts only](AGENTS.md#releases--project-specific-facts-only) for details.

## License

[MIT](LICENSE)

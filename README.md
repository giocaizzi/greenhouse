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
2. **Decides** using a typed `IrrigationDecision` pipeline: cooldown check, stress detection, multi-sensor conflict resolution, trend analysis, evidence-based moisture targets. Every evaluation produces a structured `Reason` trail and is persisted whether or not it was acted on.
3. **Acts** by controlling Tuya irrigators over local protocol v3.5.
4. **Learns** — builds per-plant absorption/drainage profiles; raises advisory alerts (blocked drip, rapid drainage, chronic underwatering, unresolvable conflict). Learning never blocks decisions.
5. **Persists** sensor readings, irrigation events, decision logs, alerts, and activity events in a local SQLite archive. Tuya Cloud is the live source; SQLite is the permanent record.

## Interfaces — four ways in, one source of truth

| Interface | URL | Notes |
|-----------|-----|-------|
| **JSON REST API** | `/api/v1` | Authoritative entry point. OpenAPI docs at `/docs`. |
| **Web UI** | `/` | HTMX + Jinja2 server-rendered. Shares service layer with the API. |
| **CLI** | `greenhouse` | Thin `httpx` client against `/api/v1`. No DB access. |
| **MCP server** | `/mcp` | Every `/api/v1` endpoint as an MCP tool via `fastapi-mcp`. Auth deferred — localhost-only. |

Stop the server and all four go dark. Anything new the CLI or an MCP tool should be able to do must first exist as an API endpoint.

> **Warning:** MCP gives a connected LLM the ability to actuate physical irrigation hardware (`/clusters/{id}/irrigate`, `/irrigators/{id}/start`, etc.). Keep the server localhost-only until auth is added.

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

- **Web UI** — open `http://localhost:8000/` for the HTMX dashboard (clusters, per-plant charts, irrigators, history, scheduler, alerts, health).
- **REST API** — `http://localhost:8000/api/v1/...`; OpenAPI docs at `http://localhost:8000/docs`.
- **MCP server** — `http://localhost:8000/mcp` (streamable HTTP). Point an MCP client (e.g. Claude Desktop) at it and every API endpoint shows up as a tool.

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

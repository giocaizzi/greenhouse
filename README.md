<div align="center">

# tuya-irrigation


**Smart plant irrigation system with Tuya IoT sensors, evidence-based plant care, and self-learning efficiency analysis.**

[![CI](https://img.shields.io/github/actions/workflow/status/giocaizzi/tuya-irrigation/ci.yml?branch=main&label=CI)](https://github.com/giocaizzi/tuya-irrigation/actions)
[![codecov](https://codecov.io/gh/giocaizzi/tuya-irrigation/graph/badge.svg)](https://codecov.io/gh/giocaizzi/tuya-irrigation)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/github/license/giocaizzi/tuya-irrigation)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/badge/uv-DE5FE9?logo=uv&logoColor=white)](https://github.com/astral-sh/uv)

</div>

## What's new in 1.2.0

- **Typed decision pipeline** — `IrrigationDecision` is the typed output of the engine. Every evaluation is persisted in `decision_logs` with a structured `Reason` trail keyed by stable `TriggerCode` enums (`GET /api/v1/clusters/{id}/decisions`).
- **Alert inbox** — deduplicated upsert with `open → acknowledged → resolved` lifecycle (`GET /api/v1/alerts`, `POST /api/v1/alerts/{id}/acknowledge`, `POST /api/v1/alerts/{id}/resolve`, `POST /api/v1/clusters/{id}/alerts/sync`).
- **Activity timeline** — cross-cutting event stream (`GET /api/v1/activity`).
- **Forecast + weather-aware skip** — next-irrigation forecast and precipitation-based skip rule (`GET /api/v1/clusters/{id}/forecast`).
- **Plant health score** — daily 0–100 composite (in-band soil/temp/humidity time + learning efficiency) with snapshot job (`GET /api/v1/plants/{id}/health`).
- **Trust layer** — leak/stuck-valve detector, per-cluster per-day rate limit, sensor anomaly scan (drift + stale).
- **Cluster insights, system health pulse, data quality report, irrigation efficacy scorer** — `GET /api/v1/clusters/{id}/insights`, `GET /api/v1/health/system`, `GET /api/v1/quality/report`, `GET /api/v1/clusters/{id}/efficacy`.
- **User preferences, vacation windows, global search, emergency stop-all** — `GET/PUT /api/v1/preferences`, `GET/POST/DELETE /api/v1/vacation/{id}`, `GET /api/v1/search`, `POST /api/v1/bulk/stop-all`.
- **Full CRUD** — edit/delete for cluster, plant, sensor, and irrigator; GET-by-id for sensor and irrigator.
- **2026 design system** — elevation/motion/z tokens, progress bar, toasts, command-K palette, bottom-sheet, plant hero card, health ring, insight cards, decision-rationale rows, dry-run and vacation banners.

## Overview

**tuya-irrigation** monitors soil moisture, temperature, humidity, and light from Tuya-compatible sensors and makes smart irrigation decisions based on evidence-based plant care data. It learns from past irrigation cycles to detect efficiency issues, blocked drips, and unresolvable plant conflicts.

```bash
uv sync
uv run tuya-irrigation-server        # start REST API + web UI
uv run tuya-irrigation check --all   # check all clusters
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
| **CLI** | `tuya-irrigation` | Thin `httpx` client against `/api/v1`. No DB access. |
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
git clone https://github.com/giocaizzi/tuya-irrigation.git
cd tuya-irrigation
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
uv run tuya-irrigation-server

# Set up a cluster
uv run tuya-irrigation cluster add "Living Room" --environment indoor
uv run tuya-irrigation plant add "Monstera deliciosa" --cluster 1
uv run tuya-irrigation sensor add --cluster 1 --device-id YOUR_DEVICE_ID --name "Monstera Sensor" --type soil_moisture

# Operations
uv run tuya-irrigation status 1          # full cluster overview
uv run tuya-irrigation irrigate 1        # smart irrigation pipeline
uv run tuya-irrigation check --all       # check all clusters + alerts
uv run tuya-irrigation learn 1           # learning report
uv run tuya-irrigation stats 1 --days 7  # irrigation statistics
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

## License

[MIT](LICENSE)

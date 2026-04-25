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

## Overview

**tuya-irrigation** monitors soil moisture, temperature, humidity, and light from Tuya-compatible sensors and makes smart irrigation decisions based on evidence-based plant care data. It learns from past irrigation cycles to detect efficiency issues, blocked drips, and unresolvable plant conflicts.

```bash
uv sync
uv run tuya-irrigation-server        # start REST API
uv run tuya-irrigation check --all    # check all clusters
```

## Features

- 🌿 **Evidence-based decisions** — plant care data from scientific literature drives moisture targets, temperature thresholds, and watering frequency
- ⚖️ **Multi-sensor conflict resolution** — handles clusters where one plant is dry and another is wet, using conservative short-burst irrigation
- 🧠 **Self-learning profiles** — tracks absorption rates, drainage patterns, and irrigation efficiency per plant over time
- 🚨 **7 alert types** — blocked drip, rapid drainage, chronic underwatering, unresolvable conflict, low light, low humidity, light-accelerated drainage
- ☁️ **Tuya Cloud + Local protocol** — reads sensors via Cloud API, controls irrigators via local protocol v3.5 for reliable duration control
- ⏰ **Background scheduling** — APScheduler syncs sensors every 30 min and checks clusters every 6 hours
- 🔌 **Three ways in** — JSON REST API (OpenAPI docs at `/docs`), server-rendered HTMX web UI at `/`, and a thin Typer CLI client. The CLI is just an HTTP client to the same API — no direct DB access.

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
# Start the server
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

- **Web UI** — open `http://localhost:8000/` for the HTMX dashboard (clusters, per-plant charts, irrigators, history, scheduler).
- **REST API** — `http://localhost:8000/api/v1/...`; OpenAPI docs at `http://localhost:8000/docs`.

## License

[MIT](LICENSE)

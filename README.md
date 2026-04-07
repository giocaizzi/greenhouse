# tuya-irrigation

Smart plant irrigation system with Tuya IoT sensors, evidence-based plant care, and self-learning efficiency analysis.

[![CI](https://img.shields.io/github/actions/workflow/status/giocaizzi/tuya-irrigation/ci.yml?branch=main&label=CI)](https://github.com/giocaizzi/tuya-irrigation/actions)
[![codecov](https://codecov.io/gh/giocaizzi/tuya-irrigation/graph/badge.svg)](https://codecov.io/gh/giocaizzi/tuya-irrigation)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/github/license/giocaizzi/tuya-irrigation)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/badge/uv-DE5FE9?logo=uv&logoColor=white)](https://github.com/astral-sh/uv)

## Overview

**tuya-irrigation** monitors soil moisture, temperature, humidity, and light from Tuya-compatible sensors and makes smart irrigation decisions based on evidence-based plant care data. It learns from past irrigation cycles to detect efficiency issues, blocked drips, and unresolvable plant conflicts.

```bash
uv sync
uv run tuya-irrigation-server        # start REST API
uv run tuya-irrigation check --all    # check all clusters
```

## Features

- **Evidence-based decisions** — plant care data from scientific literature drives moisture targets, temperature thresholds, and watering frequency
- **Multi-sensor conflict resolution** — handles clusters where one plant is dry and another is wet, using conservative short-burst irrigation
- **Self-learning profiles** — tracks absorption rates, drainage patterns, and irrigation efficiency per plant over time
- **7 alert types** — blocked drip, rapid drainage, chronic underwatering, unresolvable conflict, low light, low humidity, light-accelerated drainage
- **Tuya Cloud + Local protocol** — reads sensors via Cloud API, controls irrigators via local protocol v3.5 for reliable duration control
- **Background scheduling** — APScheduler syncs sensors every 30 min and checks clusters every 6 hours
- **REST API + CLI** — full FastAPI server with OpenAPI docs at `/docs`, thin Typer CLI client

## Architecture

```
CLI (Typer) ──── HTTP ────→ Server (FastAPI) ──→ Core (logic, learning, repository)
                                │                        │
                                ├── APScheduler          ├── SQLAlchemy v2 → SQLite
                                └── Weather (Open-Meteo)  └── tinytuya → Tuya Cloud
```

<details>
<summary>Package structure</summary>

```
tuya-irrigation/
├── libs/
│   ├── tuya-irrigation-core/         # Domain models, business logic, device control
│   │   └── tuya_irrigation_core/
│   │       ├── logic/                # Decision engine (6 modules)
│   │       ├── learning/             # Efficiency analysis (5 modules)
│   │       ├── models.py             # SQLAlchemy v2 ORM
│   │       ├── repository.py         # Data access layer
│   │       ├── schemas.py            # Pydantic v2 request/response
│   │       ├── cloud.py              # Tuya Cloud API client
│   │       ├── devices.py            # Device control (Cloud + Local v3.5)
│   │       └── ...
│   ├── tuya-irrigation-server/       # FastAPI REST API
│   │   └── tuya_irrigation_server/
│   │       ├── routes/               # 7 route modules
│   │       ├── services/             # 5 service modules
│   │       ├── deps.py               # Dependency injection
│   │       └── scheduler.py          # Background jobs
│   └── tuya-irrigation-cli/          # Typer CLI (server-only)
│       └── tuya_irrigation_cli/
│           ├── commands/             # 7 command modules
│           └── client.py             # httpx API client
├── data/                             # plant_database.json
└── tests/                            # ~191 tests
```

</details>

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

## Development

```bash
make check      # lint + test
make test       # uv run pytest
make lint       # uv run ruff check libs/ tests/
make format     # uv run ruff format libs/ tests/
make coverage   # pytest with coverage (60% threshold)
```

## License

[MIT](LICENSE)

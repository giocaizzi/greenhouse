# AGENTS.md - Guidelines for AI Agents & Developers

## Project Overview

**tuya-irrigation** is a smart plant irrigation system with:
- Evidence-based plant care from scientific literature
- Tuya Cloud sensor integration (Zigbee TR-301Z via Cloud API)
- Multi-sensor conflict resolution for single-irrigator clusters
- Self-learning irrigation profiles (absorption, drainage, efficiency)
- Client-server architecture: FastAPI server + Typer CLI client
- OpenClaw skill compatible

**Tech Stack:** Python 3.11+, uv workspaces, FastAPI, SQLAlchemy v2, Pydantic v2, Typer, ruff, pytest, SQLite, tinytuya

### Architecture

```
┌─────────────────┐     HTTP/JSON      ┌──────────────────────┐
│  CLI (Typer)    │ ──────────────────→ │  Server (FastAPI)    │
│  tuya-irrigation│                     │  tuya-irrigation-    │
│  -cli           │ ←────────────────── │  server              │
└─────────────────┘                     └──────┬───────────────┘
                                               │
                                    ┌──────────┴──────────┐
                                    │  Core Library        │
                                    │  tuya-irrigation-    │
                                    │  core                │
                                    └──────┬───────────────┘
                                           │
                              ┌────────────┴────────────┐
                              │                         │
                        ┌─────┴─────┐            ┌──────┴──────┐
                        │ SQLite DB │            │ Tuya Cloud  │
                        │ (SQLAlchemy)│           │ API         │
                        └───────────┘            └─────────────┘
```

### Data Flow

```
Sensors (Zigbee) → Tuya Cloud API
                        ↓
              cloud.py (getstatus + getdevicelog + DP parsing)
                        ↓
              APScheduler (server background jobs)
              ┌──────────┴──────────────┐
         sensor_sync              check_all
         (every 30min)            (every 6h)
              ↓                         ↓
         sync.py                 irrigation.py service
         (Cloud → DB)            (sync→weather→decide→exec)
              ↓                         ↓
         repository.py           learning.py (advisory)
         (SQLAlchemy)                   ↓
              └──────────┬──────────────┘
                   REST API (JSON)
                        ↓
               CLI / dashboards / agents
```

## Privacy & Security

### NO Personal Data in Git

**NEVER commit:** Device IDs, IP addresses, API keys, database files, personal configs.

**Verify before commit:**
```bash
git grep -i "bf60\|192.168\|local_key\|api.*key" -- '*.py' '*.md' '*.json'
```

**Personal data belongs in:**
- `data/*.db` (gitignored)
- `.env` (outside repo)

### Test Data

All test data uses fake/placeholder values centralized in `tests/fake_data.py`:
- Fake device IDs: `fake_tuya_device_aabbccdd`
- RFC 5737 IPs: `192.0.2.x`
- Generic names: "Test Cluster", "Test Sensor"

## Package Structure

```
tuya-irrigation/
├── pyproject.toml                    # Workspace root (uv + ruff + pytest)
├── Makefile                          # Dev commands: test, lint, format, check, coverage
├── libs/
│   ├── tuya-irrigation-core/         # Core library: models, repository, business logic
│   │   ├── pyproject.toml            # Deps: sqlalchemy, pydantic, tinytuya, alembic
│   │   └── tuya_irrigation_core/
│   │       ├── models.py             # SQLAlchemy v2 ORM models
│   │       ├── schemas.py            # Pydantic v2 request/response schemas
│   │       ├── database.py           # Engine, session factory
│   │       ├── repository.py         # DB operations (replaces old db.py)
│   │       ├── cloud.py              # Tuya Cloud API client
│   │       ├── devices.py            # Device control (hybrid Cloud + Local v3.5)
│   │       ├── logic.py              # Smart irrigation decision engine
│   │       ├── learning.py           # Post-irrigation analysis and alerts
│   │       ├── sync.py               # Cloud → DB sensor sync
│   │       ├── stats.py              # Statistics and CSV export
│   │       ├── plant_db.py           # Evidence-based plant care lookup
│   │       ├── constants.py          # Project-wide thresholds
│   │       └── utils.py              # Timezone, seasonal light utilities
│   │
│   ├── tuya-irrigation-server/       # FastAPI server
│   │   ├── pyproject.toml            # Deps: core, fastapi, uvicorn, apscheduler
│   │   └── tuya_irrigation_server/
│   │       ├── app.py                # App factory, lifespan
│   │       ├── config.py             # Pydantic BaseSettings
│   │       ├── deps.py               # Dependency injection
│   │       ├── scheduler.py          # APScheduler background jobs
│   │       ├── services/             # Business orchestration (extracted from old cli.py)
│   │       │   ├── cluster.py        # Status, history, plant sync
│   │       │   ├── irrigation.py     # Irrigate, monitor, check pipelines
│   │       │   ├── sync.py           # Sensor sync orchestration
│   │       │   └── maintenance.py    # Alert collection, learning
│   │       └── routes/               # API endpoints
│   │           ├── clusters.py       # Cluster CRUD
│   │           ├── plants.py         # Plant CRUD + sync
│   │           ├── irrigators.py     # Irrigator CRUD + control
│   │           ├── sensors.py        # Sensor CRUD
│   │           ├── configs.py        # Config get/set
│   │           ├── operations.py     # Status, irrigate, check, monitor, sync, learn, history, stats
│   │           └── scheduler.py      # Health + scheduler management
│   │
│   └── tuya-irrigation-cli/          # Typer CLI client (NO dependency on core)
│       ├── pyproject.toml            # Deps: httpx, typer
│       └── tuya_irrigation_cli/
│           ├── client.py             # httpx API client
│           └── main.py               # Typer CLI commands
│
├── scripts/main.py                   # OpenClaw compatibility wrapper
├── data/                             # plant_database.json, schema.sql (DB gitignored)
├── tests/                            # 155 tests
│   ├── conftest.py                   # Shared fixtures (SQLAlchemy in-memory)
│   ├── fake_data.py                  # Fake test data
│   ├── test_*.py                     # Core package tests (93)
│   ├── server/                       # Server functional tests (46)
│   └── cli/                          # CLI functional tests (16)
└── references/                       # PLANT_DATABASE.md
```

**Naming:**
- Distribution: `tuya-irrigation-core`, `tuya-irrigation-server`, `tuya-irrigation-cli`
- Import: `tuya_irrigation_core`, `tuya_irrigation_server`, `tuya_irrigation_cli`
- Entry points: `tuya-irrigation` (CLI), `tuya-irrigation-server` (server)

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
10. **CLI is server-only** — always talks to API, no direct DB access

## Development

### Setup

```bash
uv sync                     # Install all workspace packages + deps
uv run tuya-irrigation --help
uv run tuya-irrigation-server  # Start server
```

### Code Quality

```bash
make check      # lint + test (single command)
make test       # uv run pytest
make lint       # uv run ruff check libs/ tests/
make format     # uv run ruff format libs/ tests/
make coverage   # pytest with coverage report (60% threshold)
```

**Ruff config** (in `pyproject.toml`): line-length=120, py311+, rules: E/W/F/I/B/C4/UP

### Testing

```bash
uv run pytest -v
```

All tests use `conftest.py` fixtures and `fake_data.py`. Server tests use FastAPI TestClient with in-memory SQLite. CLI tests use Typer CliRunner with httpx MockTransport.

| Suite | Tests | Coverage |
|---|---|---|
| **Core** | | |
| `test_db.py` | 16 | Repository ops, dedup, readings-around, bulk insert |
| `test_logic.py` | 16 | Decisions, multi-sensor conflict, water needs, cooldown |
| `test_devices.py` | 8 | Device control, sensor parsing, error handling |
| `test_cloud.py` | 8 | Cloud API parsing, log grouping, v2 shadow |
| `test_learning.py` | 9 | Absorption profiles, drainage, reports |
| `test_utils.py` | 13 | Seasonal light, timestamp formatting, timezone |
| `test_plant_db.py` | 12 | Species/category lookup, fallback, singleton |
| `test_stats.py` | 8 | Statistics aggregation, CSV export |
| **Server** | | |
| `server/test_clusters.py` | 11 | Cluster CRUD + status + history via HTTP |
| `server/test_operations.py` | 17 | Irrigate, monitor, check, stats, health, lifecycle |
| `server/test_irrigators.py` | 6 | Irrigator CRUD + start/stop via HTTP |
| `server/test_plants.py` | 4 | Plant CRUD + sync via HTTP |
| `server/test_sensors.py` | 4 | Sensor CRUD via HTTP |
| `server/test_configs.py` | 4 | Config get/set via HTTP |
| **CLI** | | |
| `cli/test_cli.py` | 16 | CLI commands via Typer CliRunner + mock HTTP |

**Total: 155 tests.** All use fake data (no real API calls).

### Adding Tests

- Core DB/logic/learning → `tests/test_*.py`
- Server endpoints → `tests/server/test_*.py` (use TestClient)
- CLI commands → `tests/cli/test_cli.py` (use CliRunner + MockTransport)

### OpenClaw Compatibility

**OpenClaw calls:** `python3 scripts/main.py [args]`
**Package users call:** `tuya-irrigation [args]` (via entry point)

## API Reference

Server runs at `http://localhost:8000` by default. OpenAPI docs at `/docs`.

### CRUD Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/clusters` | Create cluster |
| GET | `/api/v1/clusters` | List clusters |
| GET | `/api/v1/clusters/{id}` | Get cluster |
| POST | `/api/v1/clusters/{id}/plants` | Add plant |
| GET | `/api/v1/clusters/{id}/plants` | List plants |
| POST | `/api/v1/plants/sync` | Sync plant care data |
| POST | `/api/v1/clusters/{id}/irrigators` | Add irrigator |
| GET | `/api/v1/clusters/{id}/irrigators` | List irrigators |
| POST | `/api/v1/irrigators/{id}/start` | Start irrigator |
| POST | `/api/v1/irrigators/{id}/stop` | Stop irrigator |
| POST | `/api/v1/clusters/{id}/sensors` | Add sensor |
| GET | `/api/v1/clusters/{id}/sensors` | List sensors |
| PUT | `/api/v1/clusters/{id}/config` | Set config |
| GET | `/api/v1/clusters/{id}/config` | Get config |

### Operation Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/clusters/{id}/status` | Full cluster status |
| POST | `/api/v1/clusters/{id}/irrigate` | Smart irrigation |
| GET | `/api/v1/clusters/{id}/monitor` | Moisture monitoring |
| POST | `/api/v1/check` | Check all clusters |
| POST | `/api/v1/clusters/{id}/check` | Check single cluster |
| POST | `/api/v1/sync` | Sync sensor data |
| GET | `/api/v1/clusters/{id}/learn` | Learning report |
| GET | `/api/v1/clusters/{id}/history` | Readings + events |
| GET | `/api/v1/clusters/{id}/stats` | Statistics |
| GET | `/api/v1/health` | Server health |

### Configuration

Server configured via environment variables (prefix `IRRIGATION_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `IRRIGATION_DB_URL` | `sqlite:///data/irrigation.db` | Database URL |
| `IRRIGATION_HOST` | `0.0.0.0` | Server bind host |
| `IRRIGATION_PORT` | `8000` | Server bind port |
| `IRRIGATION_SYNC_INTERVAL_MINUTES` | `30` | Sensor sync frequency |
| `IRRIGATION_CHECK_INTERVAL_HOURS` | `6` | Check-all frequency |

CLI configured via:
| Variable | Default | Description |
|----------|---------|-------------|
| `IRRIGATION_SERVER_URL` | `http://localhost:8000` | Server URL |

## Common Tasks

### Add a Plant Species
1. Research (min 2 sources), update `data/plant_database.json`
2. Sync with `tuya-irrigation plant sync`
3. Add tests if special behavior needed

### Add a Sensor
1. Pair in Tuya Smart app, get device_id from iot.tuya.com
2. `tuya-irrigation sensor add --cluster 1 --device-id XXX --name "Name" --type soil_moisture --plant-id N`

### Initialize a Cluster
1. `tuya-irrigation cluster add "Name" --location "..." --environment indoor`
2. `tuya-irrigation plant add --cluster <id> "Species" --category tropical --water-needs medium`
3. `tuya-irrigation irrigator add --cluster <id> --device-id XXX --name "Name" --type tuya_cloud`
4. `tuya-irrigation sensor add --cluster <id> --device-id XXX --name "Name" --type soil_moisture --plant-id N`
5. `tuya-irrigation config set --cluster <id> --mode smart --minutes 2 --interval 12`

### Pre-commit Check
```bash
make check
git diff --cached | grep -i "bf60\|192.168\|secret"
```

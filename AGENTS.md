# AGENTS.md - Guidelines for AI Agents & Developers

## Project Overview

**tuya-irrigation** is a smart plant irrigation system with a client-server architecture.

**Tech Stack:** Python 3.11+, uv workspaces, FastAPI, SQLAlchemy v2, Pydantic v2, Typer, ruff, pytest, SQLite, tinytuya

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

```
tuya-irrigation/
├── pyproject.toml                    # Workspace root (uv + ruff + pytest)
├── Makefile                          # Dev commands
├── libs/
│   ├── tuya-irrigation-core/         # Models, repository, business logic
│   │   └── tuya_irrigation_core/
│   │       ├── models.py             # SQLAlchemy v2 ORM models
│   │       ├── schemas.py            # Pydantic v2 request/response schemas
│   │       ├── database.py           # Engine, session factory
│   │       ├── repository.py         # DB operations
│   │       ├── cloud.py              # Tuya Cloud API client
│   │       ├── devices.py            # Device control (Cloud + Local v3.5)
│   │       ├── logic.py              # Irrigation decision engine
│   │       ├── learning.py           # Post-irrigation analysis
│   │       ├── sync.py               # Cloud → DB sensor sync
│   │       ├── stats.py              # Statistics and CSV export
│   │       ├── plant_db.py           # Evidence-based plant care lookup
│   │       ├── constants.py          # Project-wide thresholds
│   │       └── utils.py              # Timezone, seasonal light
│   ├── tuya-irrigation-server/       # FastAPI server
│   │   └── tuya_irrigation_server/
│   │       ├── app.py                # App factory, lifespan
│   │       ├── config.py             # Pydantic BaseSettings
│   │       ├── deps.py               # Dependency injection
│   │       ├── scheduler.py          # APScheduler background jobs
│   │       ├── services/             # Orchestration (cluster, irrigation, sync, maintenance)
│   │       └── routes/               # API endpoints
│   └── tuya-irrigation-cli/          # Typer CLI (NO dependency on core)
│       └── tuya_irrigation_cli/
│           ├── client.py             # httpx API client
│           └── main.py               # Typer CLI commands
├── scripts/main.py                   # OpenClaw compatibility wrapper
├── data/                             # plant_database.json, schema.sql
├── tests/                            # 155 tests (core + server + cli)
└── references/                       # Reference docs
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
uv sync
uv run tuya-irrigation-server    # Start server
uv run tuya-irrigation --help    # CLI help
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

All tests use `conftest.py` fixtures and `fake_data.py`. Server tests use FastAPI TestClient with in-memory SQLite. CLI tests use Typer CliRunner with httpx MockTransport.

| Suite | Tests | Scope |
|---|---|---|
| `test_db.py` | 16 | Repository ops, dedup, bulk insert |
| `test_logic.py` | 16 | Decisions, conflict, cooldown, stress |
| `test_devices.py` | 8 | Device control, sensor parsing |
| `test_cloud.py` | 8 | Cloud API parsing, v2 shadow |
| `test_learning.py` | 9 | Absorption, drainage, reports |
| `test_utils.py` | 13 | Seasonal light, timestamps |
| `test_plant_db.py` | 12 | Species/category lookup |
| `test_stats.py` | 8 | Stats, CSV export |
| `server/test_*.py` | 46 | All API endpoints via HTTP |
| `cli/test_cli.py` | 16 | CLI commands via mock HTTP |

**Total: 155 tests.** All use fake data (no real API calls).

### Adding Tests

- Core → `tests/test_*.py`
- Server endpoints → `tests/server/test_*.py` (use TestClient)
- CLI → `tests/cli/test_cli.py` (use CliRunner + MockTransport)

### Add a Plant Species

1. Research (min 2 sources), update `data/plant_database.json`
2. Sync with `tuya-irrigation plant sync`

### Pre-commit Check

```bash
make check
git diff --cached | grep -i "bf60\|192.168\|secret"
```

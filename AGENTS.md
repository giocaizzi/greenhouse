# AGENTS.md - Guidelines for AI Agents & Developers

## Project Overview

**tuya-irrigation** is a smart plant irrigation system with:
- Evidence-based plant care from scientific literature
- Tuya Cloud sensor integration (Zigbee TR-301Z via Cloud API)
- Multi-sensor conflict resolution for single-irrigator clusters
- Self-learning irrigation profiles (absorption, drainage, efficiency)
- OpenClaw skill compatible

**Tech Stack:** Python 3.11+, uv, ruff, pytest, SQLite, tinytuya

### Data Flow

```
Sensors (Zigbee) → Tuya Cloud API
                        ↓
                 cloud.py (getstatus + getdevicelog + DP parsing)
                        ↓
             cli.py check --all   ← cron entry point
             ┌──────────┴──────────────┐
        has irrigator?           no irrigator?
             ↓                         ↓
      irrigation logic           monitor logic
  (sync→weather→decide→exec)   (sync→soil check)
             ↓                         ↓
       learning.py              structured output
    (efficiency alerts)               ↓
             └──────────┬──────────────┘
                  structured output
                ACTION: / ALERT: lines
                        ↓
                 agent parses & forwards
                 via Telegram (exit 2)
```

## Privacy & Security

### NO Personal Data in Git

**NEVER commit:** Device IDs, IP addresses, API keys, database files, personal configs.

**Verify before commit:**
```bash
git grep -i "bf60\|192.168\|local_key\|api.*key" -- '*.py' '*.md' '*.json'
```

**Personal data belongs in:**
- `data/cluster.json` (gitignored)
- `~/.openclaw/.env` (outside repo)
- `data/*.db` (gitignored)

### Test Data

All test data uses fake/placeholder values centralized in `tests/fake_data.py`:
- Fake device IDs: `fake_tuya_device_aabbccdd`
- RFC 5737 IPs: `192.0.2.x`
- Generic names: "Test Cluster", "Test Sensor"

## Package Structure

```
tuya-irrigation/
├── pyproject.toml          # Package config (uv + ruff + pytest)
├── Makefile                # Dev commands: test, lint, format, check
├── src/tuya_irrigation/    # Core package (13 modules)
│   ├── __init__.py         # Package exports
│   ├── cli.py              # Main CLI entry point
│   ├── cloud.py            # Tuya Cloud API client
│   ├── constants.py        # Project-wide thresholds and constants
│   ├── db.py               # SQLite database management
│   ├── devices.py          # Device control (hybrid Cloud + Local v3.5)
│   ├── learning.py         # Post-irrigation analysis and alerts
│   ├── logger_daemon.py    # Cloud -> DB sensor sync
│   ├── logic.py            # Smart irrigation decision engine
│   ├── models.py           # Data models (dataclasses)
│   ├── plant_db.py         # Evidence-based plant care lookup
│   ├── stats.py            # Statistics and CSV export
│   └── utils.py            # Timezone, seasonal light utilities
├── scripts/main.py         # OpenClaw compatibility wrapper
├── tests/                  # pytest suite (8 test files + conftest + fake_data)
├── data/                   # plant_database.json, schema.sql, cluster.json.example (DB gitignored)
└── references/             # PLANT_DATABASE.md (evidence-based plant care docs)
```

**Naming:**
- Distribution: `tuya-irrigation` | Import: `tuya_irrigation`
- Entry points: `tuya-irrigation`, `tuya-irrigation-logger`, `tuya-irrigation-stats`

## Key Technical Decisions

1. **Protocol v3.5** for Rainpoint IK10PW (not 3.3)
2. **Tuya Cloud as live source**, SQLite as permanent archive
3. **UNIQUE(sensor_id, timestamp)** for zero-cost dedup
4. **min_soil_moisture** (driest plant) drives decisions, not average
5. **6h global cooldown** between any irrigations
6. **Evidence-based** plant care data with source citations
7. **Learning is advisory** — never blocks irrigation decisions
8. **All thresholds** centralized in `constants.py`

## Development

### Setup

```bash
uv sync                     # Install deps + create .venv
uv run tuya-irrigation --help
```

### Code Quality

```bash
make check      # lint + test (single command)
make test       # uv run pytest
make lint       # uv run ruff check src/ tests/
make format     # uv run ruff format src/ tests/
```

**Ruff config** (in `pyproject.toml`): line-length=120, py311+, rules: E/W/F/I/B/C4/UP

### Testing

```bash
uv run pytest -v
```

All tests use `conftest.py` fixtures (`tmp_db`, `fake_tuya_env`, `sample_cluster`) and `fake_data.py`.

| Suite | Tests | Coverage |
|---|---|---|
| `test_db.py` | 16 | DB ops, dedup, readings-around, bulk insert, environment, migrations |
| `test_logic.py` | 16 | Decisions, multi-sensor conflict, water needs, cooldown, stress |
| `test_devices.py` | 8 | Device control, sensor parsing, error handling |
| `test_cloud.py` | 8 | Cloud API parsing, log grouping, v2 shadow, credentials |
| `test_learning.py` | 9 | Absorption profiles, drainage, reports |
| `test_utils.py` | 13 | Seasonal light, timestamp formatting, timezone |
| `test_plant_db.py` | 12 | Species/category lookup, fallback, singleton |
| `test_stats.py` | 8 | Statistics aggregation, CSV export, duration formatting |

**Total: 93 tests.** All use fake data (no real API calls).

### Adding Tests

- DB tests -> `test_db.py`
- Logic tests -> `test_logic.py`
- Cloud tests -> `test_cloud.py` (mock tinytuya)
- Learning tests -> `test_learning.py` (synthetic data)
- Utils/plant_db/stats -> corresponding `test_*.py`

### OpenClaw Compatibility

**OpenClaw calls:** `python3 scripts/main.py [args]`
**Package users call:** `tuya-irrigation [args]` (via entry point)

## Common Tasks

### Add a Plant Species
1. Research (min 2 sources), update `data/plant_database.json`
2. Sync with `tuya-irrigation plant sync`
3. Add tests if special behavior needed

### Add a Sensor
1. Pair in Tuya Smart app, get device_id from iot.tuya.com
2. `tuya-irrigation sensor add --cluster 1 --device-id XXX --name "Name" --type soil_moisture --plant-id N`

### Initialize a Cluster
1. Copy `data/cluster.json.example` to `data/cluster.json`
2. Edit with your device IDs and Tuya credentials
3. `tuya-irrigation cluster setup`

### Pre-commit Check
```bash
make check
git diff --cached | grep -i "bf60\|192.168\|secret"
```

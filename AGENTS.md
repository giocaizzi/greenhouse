# AGENTS.md - Guidelines for AI Agents & Developers

## Project Overview

**tuya-irrigation** is a smart plant irrigation system (v0.3.0) with:
- Evidence-based plant care from scientific literature
- Tuya Cloud sensor integration (Zigbee TR-301Z via Cloud API)
- Multi-sensor conflict resolution for single-irrigator clusters
- Self-learning irrigation profiles (absorption, drainage, efficiency)
- OpenClaw skill compatible

**Tech Stack:** Python 3.11+, uv, ruff, SQLite, tinytuya, unittest (49 tests)

## 🔒 Privacy & Security

### NO Personal Data in Git

**NEVER commit:** Device IDs, IP addresses, API keys, database files, personal configs.

**Verify before commit:**
```bash
git grep -i "bf60\|192.168\|local_key\|api.*key" -- '*.py' '*.md' '*.json'
```

**Personal data belongs in:**
- `tools/cluster.local.json` (gitignored)
- `~/.openclaw/.env` (outside repo)
- `data/*.db` (gitignored)

### Test Data

All test data uses fake/placeholder values centralized in `tests/fake_data.py`:
- Fake device IDs: `fake_tuya_device_aabbccdd`
- RFC 5737 IPs: `192.0.2.x`
- Generic names: "Test Cluster", "Test Sensor"

## 📁 Structure

```
src/tuya_irrigation/     # Core package (12 modules)
scripts/                 # OpenClaw wrappers + auto_irrigate entrypoint
tests/                   # 49 tests (5 test files)
data/                    # plant_database.json + schema.sql (DB gitignored)
```

## 🔑 Key Technical Decisions

1. **Protocol v3.5** for Rainpoint IK10PW (not 3.3)
2. **Tuya Cloud as live source**, SQLite as permanent archive
3. **UNIQUE(sensor_id, timestamp)** for zero-cost dedup
4. **min_soil_moisture** (driest plant) drives decisions, not average
5. **6h global cooldown** between any irrigations
6. **Evidence-based** plant care data with source citations
7. **Learning is advisory** — never blocks irrigation decisions

## 🧪 Development Workflow

```bash
# Run tests + lint
./test.sh

# Check for data leaks before commit
git diff --cached | grep -i "bf60\|192.168\|secret"
```

## Common Tasks

### Add a Plant Species
1. Research (min 2 sources), update `data/plant_database.json`
2. Add tests if special behavior needed

### Add a Sensor
1. Pair in Tuya Smart app, get device_id from iot.tuya.com
2. `python3 main.py sensor add --cluster 1 --device-id XXX --name "Name" --type soil_moisture --plant-id N`

### Add Tests
- DB tests → `test_db.py`
- Logic tests → `test_logic.py`
- Cloud tests → `test_cloud.py` (mock tinytuya)
- Learning tests → `test_learning.py` (synthetic data)

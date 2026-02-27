# AGENTS.md - Guidelines for AI Agents & Developers

This file provides essential guidance for AI agents (like Claude, GPT, etc.) and human developers working on this project.

## Project Overview

**tuya-irrigation** is a smart plant irrigation system with evidence-based plant care data, Tuya device integration, and autonomous decision-making based on environmental sensors and historical trends.

**Key Features:**
- Evidence-based plant care from scientific literature
- Smart irrigation decisions with confidence scoring
- Historical trend analysis & stress detection
- Tuya device control (local + cloud mode)
- Optional sensor integration (soil moisture, temperature, humidity)
- Comprehensive logging, statistics, and reports
- OpenClaw skill compatible

**Tech Stack:**
- Python 3.11+ with `uv` (package manager) and `ruff` (linter)
- SQLite for persistence
- `tinytuya` for Tuya device communication (protocol v3.5)
- `unittest` for testing (28 tests)

## 🔒 Privacy & Security: CRITICAL RULES

### 1. NO Personal Data in Git

**NEVER commit:**
- Device IDs (Tuya device identifiers)
- IP addresses (local network addresses)
- Local keys / API keys / tokens
- Database files (`*.db`)
- Personal cluster configurations
- Location details beyond generic examples

**Verify before commit:**
```bash
git grep -i "bf60\|192.168\|local_key\|api.*key" -- '*.py' '*.md' '*.json'
# Should return NO results or only fake test data
```

### 2. Use Local Configuration Pattern

**Personal data belongs in gitignored files:**

```
tools/cluster.local.json     # User's personal config (gitignored)
tools/cluster.local.json.example  # Template (in git, no real data)

~/.openclaw/config/secrets.env    # Credentials (outside repo)
```

**Code should:**
- Read from environment variables (`os.environ.get()`)
- Provide sensible defaults for missing config
- Never hardcode personal values

**Example:**
```python
# ✅ Good
device_id = os.environ.get("TUYA_DEVICE_ID")
cluster_name = local_config.get("cluster_name", "My Plants")

# ❌ Bad
device_id = "bf60e488d51a74ec24osb0"  # Never hardcode real IDs
cluster_name = "Indoor Milano"  # Never hardcode personal locations
```

### 3. Test Data Must Be Fake

**All test data MUST use:**
- Fake device IDs: `fake_tuya_device_aabbccdd`
- RFC 5737 IP addresses: `192.0.2.x` (reserved for documentation)
- Generic names: "Test Cluster", "Sample Plant"

**Centralized in:** `tests/fake_data.py`

### 4. Examples: Generic & Public

**In documentation and examples, use:**
- Generic locations: "Milano" (public city name, OK for weather examples)
- Scientific names: "Monstera deliciosa" (generic plant species)
- Template values: "YOUR_DEVICE_ID", "My Indoor Plants"

**Avoid:**
- Specific addresses: "Via Roma 123" ❌
- Personal identifiers: full names, emails ❌
- Exact coordinates: GPS data ❌

## 📁 Repository Structure

```
tuya-irrigation/
├── src/tuya_irrigation/     # Core package
│   ├── cli.py               # Main CLI
│   ├── db.py                # SQLite operations
│   ├── logic.py             # Decision engine
│   ├── devices.py           # Tuya device control
│   ├── tuya_irrigation.py   # CLI wrapper (protocol v3.5!)
│   ├── plant_db.py          # Evidence-based plant data
│   └── ...
├── scripts/                 # OpenClaw wrappers
│   ├── main.py
│   ├── auto_irrigate.py     # HEARTBEAT entrypoint
│   └── ...
├── tools/                   # Dev utilities
│   ├── cluster.local.json.example  # Template (safe)
│   ├── cluster.local.json   # User config (gitignored!)
│   └── ...
├── tests/                   # Test suite (28 tests)
│   ├── fake_data.py         # Centralized fake data
│   └── ...
├── data/
│   ├── plant_database.json  # Scientific plant care data
│   ├── irrigation.db        # SQLite DB (gitignored!)
│   └── schema.sql
└── docs/                    # Documentation (8 .md files)
```

## 🧪 Development Workflow

### Before Making Changes

1. **Read existing code** - Understand patterns and conventions
2. **Check tests** - Run `./test.sh` to ensure everything passes
3. **Verify gitignore** - Ensure sensitive files are excluded

### Making Changes

1. **Follow existing patterns:**
   - Use `IrrigationDB` for database operations
   - Use `TuyaDeviceManager` for device control
   - Log decisions with confidence scores

2. **Maintain quality:**
   ```bash
   .venv/bin/ruff check src/ tests/ scripts/ tools/
   ./test.sh
   ```

3. **Update tests** - Add/modify tests for new features

4. **Update docs** - Keep README.md, SKILL.md in sync

### Before Committing

**Security checklist:**
```bash
# 1. Check for personal data
git diff --cached | grep -i "bf60\|192.168\|api.*key\|secret"

# 2. Verify gitignore compliance
git status --ignored | grep "\.db$\|cluster\.local\.json"

# 3. Run quality checks
.venv/bin/ruff check src/ tests/ scripts/ tools/
./test.sh

# 4. Review commit
git diff --cached
```

## 🔑 Key Technical Decisions

### Protocol Version: v3.5 (CRITICAL!)

**Rainpoint IK10PW requires Tuya protocol version 3.5, not 3.3.**

```python
# ALWAYS use v3.5 for local mode
device = tinytuya.OutletDevice(device_id, device_ip, local_key)
device.set_version(3.5)  # Critical for Rainpoint compatibility
```

**Why:** Protocol v3.5 uses `DP_QUERY_NEW` command (0x10) instead of `DP_QUERY` (0x0A). Devices with newer firmware reject v3.3 commands with error 904.

### Evidence-Based Plant Data

Plant care data is sourced from scientific literature (The Spruce, NY Botanical Garden, Biology Insights). Every value in `plant_database.json` includes source citations.

**When adding new plants:**
- Cite minimum 2 independent sources
- Include source URLs in `sources` array
- Use scientific names + common aliases

### Confidence Scoring System

Decisions include confidence scores:
- **95%**: Critical stress (water stress, over-watering) - override
- **70-90%**: Sensor-based decisions
- **60%**: Temperature fallback (no sensors)

## 📋 Common Tasks

### Add a New Plant Species

1. Research care requirements (min 2 sources)
2. Update `data/plant_database.json`
3. Add tests in `test_logic.py` if special behavior needed
4. Document sources

### Add a New Sensor Type

1. Update `models.py` - add to `SensorType` if needed
2. Update `devices.py` - add reading logic
3. Update `logic.py` - integrate into decisions
4. Add tests in `test_devices.py` and `test_logic.py`

### Modify Decision Logic

1. Update `logic.py` - modify `decide_for_cluster()`
2. Update tests - ensure 28/28 pass
3. Document new thresholds in code comments
4. Update `TRENDS.md` if trend analysis affected

## 🐛 Debugging

### Local Mode Not Working?

Check protocol version first:
```python
device.set_version(3.5)  # Not 3.3!
```

### Device Returns Error 904?

- Verify local key: check Cloud API vs `secrets.env`
- Verify protocol version: must be 3.5 for Rainpoint
- Check device IP: ping test, router confirmation

### Tests Failing?

```bash
# Run specific test
python3 -m unittest tests.test_logic.TestIrrigationLogic.test_temperature_fallback_hot

# Enable debug output
DEBUG=1 ./test.sh
```

## 🤝 Contributing

When working on this project:

1. **Privacy first** - Review security checklist before every commit
2. **Test coverage** - Maintain 28/28 passing tests
3. **Code quality** - Keep ruff clean (0 errors/warnings)
4. **Documentation** - Update relevant .md files
5. **Commit messages** - Be descriptive, reference issues if any

## 📚 Key Files to Read

**Start here:**
- `README.md` - Quick start & overview
- `SKILL.md` - OpenClaw integration guide
- `PACKAGE.md` - Package structure & design decisions

**Technical details:**
- `TESTING.md` - Test suite guide
- `TRENDS.md` - Historical analysis system
- `PLANT_DATABASE.md` - Evidence-based data methodology
- `LOGGING.md` - Event logging & statistics

**Setup:**
- `tools/cluster.local.json.example` - Configuration template

## 🎯 Project Status

**Current State:** ✅ Production Ready

- Code quality: Ruff clean (0 errors)
- Tests: 28/28 passing
- Local mode: Operational (protocol v3.5)
- Documentation: Complete
- Privacy: Audited & secure

**Next Steps:**
- Sensor integration (when hardware arrives)
- Advanced trend analysis features
- Multi-cluster support

---

**Remember:** This system controls real plants and water. Code carefully, test thoroughly, respect privacy.

Built with 🦞 by the OpenClaw community.

# Irrigation - Smart Plant Care System

Modern Python package for automated plant irrigation using evidence-based plant care data, Tuya smart devices, and historical trend analysis.

## Features

🌱 **Evidence-Based Plant Care**
- All plant data from scientific literature (see [PLANT_DATABASE.md](PLANT_DATABASE.md))
- Cross-referenced from 2+ independent professional sources
- Covers tropical, succulent, cacti, fern, and fruit tree categories

📊 **Historical Trend Analysis**
- Analyzes 48h sensor data for declining/rising patterns
- Detects water stress, heat stress, and over-watering
- Delta calculations: Δ soil moisture, Δ temperature
- Irrigation frequency pattern analysis

🤖 **Smart Decision Engine**
- Priority system: stress override → sensor logic → temperature fallback
- Confidence scoring (95% with stress detection, 60% fallback)
- Multi-plant cluster support
- Temperature-based fallback when no sensors

💧 **Comprehensive Logging**
- Every irrigation decision logged with full context
- Statistics and periodic reports
- CSV export for external analysis
- Automatic tracking of water usage

🏠 **Tuya Smart Devices**
- Supports Tuya irrigators (local or cloud mode)
- Optional sensor integration (soil moisture, temperature, humidity)
- Auto-detects local mode capabilities
- Works without sensors (temperature-based fallback)

📦 **Modern Python Package**
- Built with `uv` (fast package manager) and `ruff` (linter/formatter)
- Proper `src/` layout with clean imports
- Installable via pip from GitHub
- Full OpenClaw skill compatibility

## Quick Start

### OpenClaw Skill Usage (Current)

```bash
# Analyze irrigation needs
python3 scripts/main.py analyze 1 --temp 23

# Auto-irrigate based on smart logic
. ~/.openclaw/config/secrets.env
python3 scripts/main.py auto-irrigate 1 --temp 23

# View recent events with summary
python3 scripts/main.py log events --cluster 1 --hours 48

# Statistics (last 7 days)
python3 scripts/main.py log stats --cluster 1 --days 7

# Generate report
python3 scripts/report.py 1 --days 7
```

### As Installed Package

```bash
# Install from GitHub
pip install git+https://github.com/kezclaw/tuya-irrigation.git

# Use CLI (installed as 'tuya-irrigation')
tuya-irrigation cluster list
tuya-irrigation analyze 1 --temp 23
tuya-irrigation auto-irrigate 1 --temp 23

# Use programmatically (import as 'tuya_irrigation')
from tuya_irrigation import IrrigationDB, IrrigationLogic, TuyaDeviceManager

db = IrrigationDB()
logic = IrrigationLogic(db)
decision = logic.decide_for_cluster(cluster_id=1, current_temp=23.0)
print(f"Action: {decision['action']}, Confidence: {decision['confidence']:.0%}")
```

### HEARTBEAT Integration (Autonomous Operation)

For fully autonomous irrigation via OpenClaw heartbeat:

```bash
# Add to HEARTBEAT.md:
. ~/.openclaw/config/secrets.env && \
WTTR=$(curl -s "wttr.in/Milano?format=%l:+%c+%t+(%f),+pioggia+%p,+vento+%w") && \
python3 ~/.openclaw/workspace/skills/tuya-irrigation/scripts/auto_irrigate.py --wttr "$WTTR"
```

The `auto_irrigate.py` script:
- Parses feels-like temperature from weather data
- Makes smart irrigation decisions (logic + confidence scoring)
- Executes irrigation on physical device
- Logs all decisions to database (even if device execution fails)
- Silent on skip, verbose only on irrigate/error

## Architecture

```
tuya-irrigation/
├── src/tuya_irrigation/         # Core package
│   ├── cli.py                   # Main CLI entry point
│   ├── db.py                    # SQLite database management
│   ├── logic.py                 # Smart irrigation decision engine
│   ├── devices.py               # Tuya device control (uses tinytuya)
│   ├── tuya_irrigation.py       # CLI wrapper for tinytuya device control
│   ├── plant_db.py              # Evidence-based plant care database
│   ├── logger_daemon.py         # Sensor data logging daemon
│   ├── stats.py                 # Statistics module
│   └── report.py                # Report generator
├── scripts/                     # OpenClaw compatibility wrappers
│   ├── main.py                  # Wrapper for CLI
│   ├── auto_irrigate.py         # HEARTBEAT entrypoint (autonomous operation)
│   ├── logger.py                # Wrapper for logger daemon
│   ├── stats.py                 # Wrapper for stats
│   └── report.py                # Wrapper for report
├── tools/                       # Development utilities
│   ├── cluster.local.json.example  # Template for personal config
│   ├── setup_kez_cluster.py     # Initial cluster setup (reads cluster.local.json)
│   ├── sync_plant_data.py       # Sync plants with database
│   ├── test_trends.py           # Test trend analysis
│   └── add_test_data.py         # Generate test data
├── tests/                       # Test suite (28 tests)
│   ├── fake_data.py             # Centralized fake test data (RFC 5737 IPs)
│   ├── test_db.py               # Database tests (9 tests)
│   ├── test_logic.py            # Logic tests (11 tests)
│   └── test_devices.py          # Device tests (8 tests)
└── data/                        # Data files
    ├── plant_database.json      # Evidence-based plant care data
    ├── irrigation.db            # SQLite database (gitignored)
    └── schema.sql               # Database schema

```

**Package naming convention:**
- PyPI distribution name: `tuya-irrigation` (with hyphen)
- Python import name: `tuya_irrigation` (with underscore)
- CLI entry points: `tuya-irrigation`, `tuya-irrigation-logger`

## Documentation

- **[SKILL.md](SKILL.md)** - Complete skill documentation and command reference
- **[PACKAGE.md](PACKAGE.md)** - Package structure, development guide, OpenClaw compatibility
- **[PLANT_DATABASE.md](PLANT_DATABASE.md)** - Evidence-based plant care data system
- **[LOGGING.md](LOGGING.md)** - Comprehensive logging and reporting guide
- **[TRENDS.md](TRENDS.md)** - Historical trend analysis and stress detection
- **[SENSORS.md](SENSORS.md)** - Sensor integration guide
- **[TESTING.md](TESTING.md)** - Test suite and validation

## Development

### Setup

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone/navigate to skill directory
cd ~/.openclaw/workspace/skills/tuya-irrigation

# Install with uv (creates .venv automatically)
uv sync

# Run tests
./test.sh

# Lint and format
uv run ruff check src/ tests/ --fix
uv run ruff format src/ tests/
```

### Adding Features

1. Create module in `src/irrigation/`
2. Export in `__init__.py` if public API
3. Add tests in `tests/`
4. Update documentation
5. Run `ruff` to format/lint

## Testing

```bash
# Run all tests (28 tests, ~1s)
./test.sh

# Run specific test file
python3 -m unittest tests.test_db
python3 -m unittest tests.test_logic

# Coverage report
python3 tests/run_tests.py --verbose
```

## Requirements

- Python 3.11+
- `uv` (package manager)
- `tinytuya` (Tuya device control)
- `ruff` (dev only - linting/formatting)

## License

MIT

## Credits

- Plant care data compiled from professional horticultural sources:
  - The Spruce, NY Botanical Garden, Biology Insights, Gardenia.net, Colorado State University Extension
- All data cross-referenced from minimum 2 independent sources
- See [PLANT_DATABASE.md](PLANT_DATABASE.md) for full citations

---

Built with 🦞 by Kezclaw

Smart irrigation based on science, not guesses. 🌱📚📊

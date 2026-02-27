---
name: tuya-irrigation
description: |
  Smart irrigation system for Tuya-based devices with sensor integration.
  Features: cluster management, plant profiles, sensor data logging, smart irrigation logic.
  Use when: managing irrigation, adding plants/sensors, analyzing conditions, auto-watering.
  Requires: TUYA_CLIENT_ID, TUYA_CLIENT_SECRET in secrets.env.
---

# Tuya Smart Irrigation System

Complete irrigation management with plant profiles, sensor integration, and smart decision logic.

## Architecture

- **Modern Python package** (`uv` + `ruff`, see `PACKAGE.md`)
- **Package name**: `tuya-irrigation` (PyPI-style) / `tuya_irrigation` (import name)
- **Database**: SQLite (`~/.openclaw/workspace/skills/tuya-irrigation/data/irrigation.db`)
- **Plant Database**: JSON (`~/.openclaw/workspace/skills/tuya-irrigation/data/plant_database.json`) - Evidence-based care data from scientific literature
- **Package structure**:
  - `src/tuya_irrigation/` - Core package (db, logic, devices, plant_db, models, cli, logger)
  - `scripts/` - OpenClaw compatibility wrappers
  - `tests/` - Test suite (28 tests)
  - `data/` - Plant database + SQLite
  - Entry points: `tuya-irrigation` CLI, `tuya-irrigation-logger` daemon

**All irrigation decisions are based on scientific literature**, not guesses. See `PLANT_DATABASE.md` for sources and methodology.

## Setup

### 1. Environment Variables

Add to `~/.openclaw/config/secrets.env`:

```bash
TUYA_CLIENT_ID=your_client_id
TUYA_CLIENT_SECRET=your_client_secret
TUYA_DEVICE_ID=your_irrigator_device_id
TUYA_REGION=eu          # eu | us | cn | in

# Optional (for local/LAN mode)
TUYA_DEVICE_IP=192.0.2.1
TUYA_LOCAL_KEY=xxxxxxxxxxxxxxxx
```

**Get credentials**: [iot.tuya.com](https://iot.tuya.com) → Cloud Projects → Create project → select region → copy Access ID/Key.

### 2. Initialize Your Cluster

Run the setup script (pre-configured from `tools/cluster.local.json`):

```bash
. ~/.openclaw/config/secrets.env && \
python3 ~/.openclaw/workspace/skills/tuya-irrigation/scripts/setup_kez_cluster.py
```

This creates:
- Cluster: name from your local config
- Plants: Monstera, Areca/Kentia palm, Dracaena, Nespolo (loquat)
- Irrigator: Rainpoint IK10PW (auto-detects local/cloud mode)
- Initial config: temperature-based schedule mode

## CLI Usage

Main CLI: `~/.openclaw/workspace/skills/tuya-irrigation/scripts/main.py`

### Cluster Management

```bash
# List clusters
python3 main.py cluster list

# Add a new cluster
python3 main.py cluster add "Outdoor Garden" --location "Backyard"
```

### Plant Management

```bash
# List plants
python3 main.py plant list
python3 main.py plant list --cluster 1

# Add a plant
python3 main.py plant add --cluster 1 \
  "Ficus elastica" \
  --category tropical \
  --water-needs medium \
  --light-needs high \
  --temp-min 18 --temp-max 24 \
  --humidity-min 50 --humidity-max 70 \
  --notes "Rubber plant, wipe leaves monthly"
```

### Irrigator Management

```bash
# List irrigators
python3 main.py irrigator list
python3 main.py irrigator list --cluster 1

# Add an irrigator
python3 main.py irrigator add \
  --cluster 1 \
  --device-id bf123456789abcdef \
  --name "Main Irrigator" \
  --type tuya_local \
  --device-ip 192.0.2.1 \
  --local-key xxxxxxxxxxxxxxxx \
  --interval 12

# Control irrigator
python3 main.py irrigator status 1
python3 main.py irrigator on 1
python3 main.py irrigator off 1
python3 main.py irrigator start 1 --minutes 5
```

### Sensor Management

```bash
# List sensors
python3 main.py sensor list
python3 main.py sensor list --cluster 1

# Add a sensor
python3 main.py sensor add \
  --cluster 1 \
  --device-id bf987654321fedcba \
  --name "Temp/Humidity Sensor" \
  --type temp_humidity

# Read current sensor data
python3 main.py sensor read --cluster 1
```

### Irrigation Configuration

```bash
# Get current config
python3 main.py config get 1

# Set config
python3 main.py config set --cluster 1 \
  --mode smart \
  --minutes 2 \
  --interval 12 \
  --auto-run true

# Modes:
#   manual   - No automation
#   schedule - Fixed schedule (temperature-based)
#   smart    - Sensor-driven decisions
```

### Smart Analysis & Auto-Irrigation

```bash
# Analyze conditions and get recommendation
python3 main.py analyze 1
python3 main.py analyze 1 --temp 22.5

# Automatically apply smart logic
python3 main.py auto-irrigate 1
python3 main.py auto-irrigate 1 --temp 22.5
```

**Smart logic considers**:
- Soil moisture (primary indicator, if sensor present)
- Temperature (vs ideal range for plants)
- Humidity (vs ideal range for plants)
- Plant water needs (low/medium/high)
- Recent irrigation history

**Confidence levels**:
- High (>80%): Soil sensor + recent readings
- Medium (60-80%): Temperature + plant profiles
- Low (<60%): Minimal data, conservative defaults

### Logging & History

```bash
# View sensor readings
python3 main.py log readings --cluster 1 --hours 24

# View irrigation events
python3 main.py log events --cluster 1 --hours 48

# Run sensor logger once
python3 logger.py

# Run sensor logger continuously (every 15 min)
python3 logger.py --interval 15
```

## Heartbeat Integration

For automated checks, update `HEARTBEAT.md`:

```bash
# Smart auto-irrigation (replaces irrigation_manager.py)
. ~/.openclaw/config/secrets.env && \
python3 ~/.openclaw/workspace/skills/tuya-irrigation/scripts/main.py auto-irrigate 1
```

## Sensor Data Collection

For continuous logging, run as a background process or via cron:

```bash
# Every 30 minutes
*/30 * * * * . ~/.openclaw/config/secrets.env && python3 ~/.openclaw/workspace/skills/tuya-irrigation/scripts/logger.py
```

## Migration from Old System

The old scripts (`tuya_irrigation.py`, `irrigation_manager.py`) still work and are used internally by `devices.py`. They remain available for backward compatibility or manual control.

To migrate:
1. Run `setup_kez_cluster.py` (or manually create cluster/plants/irrigator via CLI)
2. Update `HEARTBEAT.md` to use `main.py auto-irrigate` instead of `irrigation_manager.py`
3. Add sensors when they arrive: `main.py sensor add ...`
4. Switch config mode to "smart": `main.py config set --cluster 1 --mode smart`

## Database Schema

See `scripts/db.py` for full schema. Key tables:

- `clusters` - Plant groupings
- `plants` - Plant species & care profiles
- `irrigators` - Irrigation devices
- `sensors` - Sensor devices (temp, humidity, soil moisture, light)
- `sensor_readings` - Time-series sensor data
- `irrigation_events` - Irrigation action log
- `irrigation_configs` - Per-cluster automation settings

## Datapoints Reference (Tuya Devices)

See legacy `tuya_irrigation.py` docs for detailed DP mappings. Common DPs:

- `switch` - Main on/off
- `start` - Start/stop session
- `countdown` - Duration in minutes
- `countdown_left` - Remaining time
- `battery_percentage` - Battery level
- `temp_current` / `humidity_current` - Sensor readings (if supported)

## Examples

### Setup and first analysis

```bash
# Source env
. ~/.openclaw/config/secrets.env

# Initialize cluster from tools/cluster.local.json
python3 setup_kez_cluster.py

# Sync plants with evidence-based data from literature
python3 sync_plant_data.py

# Check what the smart logic suggests
python3 main.py analyze 1 --temp 23

# Apply it
python3 main.py auto-irrigate 1 --temp 23

# View recent events
python3 main.py log events --cluster 1
```

### Add sensors when they arrive

```bash
python3 main.py sensor add \
  --cluster 1 \
  --device-id bf111222333444555 \
  --name "Soil Moisture Sensor" \
  --type soil_moisture

python3 main.py sensor add \
  --cluster 1 \
  --device-id bf666777888999aaa \
  --name "Temp/Humidity Sensor" \
  --type temp_humidity

# Start logging
python3 logger.py --interval 30 &
```

### Switch to full smart mode

```bash
# After a few days of sensor data
python3 main.py config set --cluster 1 --mode smart

# Now auto-irrigate uses sensor data + plant profiles
python3 main.py auto-irrigate 1
```

## Testing

Comprehensive test suite covering database, logic, and device management:

```bash
# Run all tests (28 tests, ~1s)
./test.sh

# Or directly
python3 tests/run_tests.py

# Specific test file
python3 -m unittest tests.test_db
python3 -m unittest tests.test_logic
python3 -m unittest tests.test_devices
```

See `tests/README.md` for details.

## Logging & Statistics

All irrigation events are automatically logged to the database. View activity and statistics:

```bash
# Recent events with summary
python3 main.py log events --cluster 1 --hours 48

# Detailed statistics (last 7 days)
python3 main.py log stats --cluster 1 --days 7

# Export to CSV for analysis
python3 main.py log stats --cluster 1 --days 30 --export irrigation_data.csv

# Generate formatted report
python3 report.py 1 --days 7
python3 report.py 1 --days 30 --output report.txt
```

### What Gets Logged

Every irrigation action is logged with:
- **Timestamp**: when it happened
- **Action**: start, stop, schedule_updated, skip_decision, error
- **Duration**: how long (minutes)
- **Triggered by**: auto, manual, schedule
- **Notes**: decision reason, confidence, parameters
- **Irrigator**: which device

### Statistics Include

- Total events and irrigations
- Total water time
- Average duration per irrigation
- Frequency (irrigations per day)
- Breakdown by trigger type (auto vs manual)
- Recent irrigation history

### Periodic Reports

For weekly/monthly summaries:

```bash
# Weekly report (can be automated via cron)
python3 report.py 1 --days 7

# Monthly report
python3 report.py 1 --days 30 --output monthly_report.txt
```

Add to cron for automatic weekly reports:
```bash
# Every Monday at 9 AM
0 9 * * 1 cd ~/.openclaw/workspace/skills/tuya-irrigation/scripts && python3 report.py 1 --days 7 > /tmp/irrigation_report.txt
```

## Notes

- **Sensors are optional**: System works without them (temperature-based fallback)
- **Local mode preferred**: Faster response, richer DPs (especially for RainPoint IK10PW)
- **Database is portable**: Copy `data/irrigation.db` to backup/migrate
- **Confidence scoring**: Low confidence → conservative defaults; high confidence → sensor-driven decisions
- **One DB for all clusters**: Multi-cluster support built-in, but each cluster can have its own mode/config
- **Evidence-based**: All plant care data from scientific literature (see `PLANT_DATABASE.md`)

## Documentation

- **SKILL.md** (this file) - Overview and commands
- **PACKAGE.md** - Package structure, development guide, OpenClaw compatibility
- **TESTING.md** - Test suite and validation
- **SENSORS.md** - Sensor integration guide
- **PLANT_DATABASE.md** - Evidence-based plant care data system
- **LOGGING.md** - Comprehensive logging and reporting guide
- **TRENDS.md** - Historical trend analysis and stress detection
- **data/schema.sql** - Database schema reference

## Troubleshooting

- **"Missing TUYA_CLIENT_ID"**: Source `~/.openclaw/config/secrets.env` before running
- **"Irrigator not found"**: Run `python3 main.py irrigator list` to get ID
- **"No sensors found"**: Normal before sensors arrive; system uses temperature fallback
- **Local mode not working**: Check `TUYA_DEVICE_IP` and `TUYA_LOCAL_KEY` in secrets.env

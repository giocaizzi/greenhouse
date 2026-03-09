---
name: tuya-irrigation
description: |
  Smart irrigation system for Tuya-based devices with sensor integration.
  Features: cluster management, plant profiles, sensor data logging, smart irrigation logic.
  Use when: managing irrigation, adding plants/sensors, analyzing conditions, auto-watering.
  Requires: TUYA_CLIENT_ID, TUYA_CLIENT_SECRET in ~/.openclaw/.env.
---

# Tuya Smart Irrigation System

Evidence-based irrigation with Tuya Cloud sensors, multi-plant conflict resolution, and self-learning efficiency analysis.

## Architecture

```
Sensor TR-301Z → Zigbee GW → Tuya Cloud
                                  ↓
                    cloud.py (get_live_reading + get_device_logs)
                                  ↓
                    cli.py irrigate (sync → weather → decide → execute)
                        ↓               ↓
                 learning.py        logic.py
                 (efficiency)       (multi-sensor decisions)
                        ↓               ↓
                  alerts/reports    Irrigator Rainpoint
```

**Package:** `tuya_irrigation` (v0.4.0)

| Module | Purpose |
|---|---|
| `cli.py` | Single entry point: status, irrigate, sync, learn, history, stats |
| `cloud.py` | Tuya Cloud API client (getstatus, getdevicelog, DP parsing) |
| `db.py` | SQLite with dedup, bulk insert, readings-around queries |
| `devices.py` | Physical device control (irrigators via tinytuya) |
| `logic.py` | Smart decisions: multi-sensor conflict, trends, stress detection |
| `learning.py` | Post-irrigation analysis: absorption profiles, efficiency, alerts |
| `logger_daemon.py` | Cloud → DB sync (used by CLI `sync` + `irrigate`) |
| `plant_db.py` | Evidence-based plant care data (JSON) |
| `models.py` | Dataclasses (Cluster, Plant, Sensor, SensorReading, etc.) |
| `stats.py` | Statistics computation and CSV export |
| `utils.py` | Timezone-aware timestamp formatting |

## Setup

### Environment Variables (`~/.openclaw/.env`)

```bash
TUYA_CLIENT_ID=your_client_id
TUYA_CLIENT_SECRET=your_client_secret
TUYA_DEVICE_ID=your_irrigator_device_id
TUYA_REGION=eu          # eu | us | cn | in

# Optional (for local/LAN mode)
TUYA_DEVICE_IP=192.0.2.1
TUYA_LOCAL_KEY=xxxxxxxxxxxxxxxx
```

### Initialize Cluster

```bash
cd ~/.openclaw/workspace/skills/tuya-irrigation/scripts
python3 setup_cluster.py   # From tools/cluster.local.json
```

## CLI Reference

```bash
cd ~/.openclaw/workspace/skills/tuya-irrigation/scripts
P="python3 main.py"

# OPERATIONS (one call = full picture)
$P status 1              # Full overview: sensors, config, events, smart analysis, alerts
$P irrigate 1            # Full pipeline: sync → weather → decide → execute
$P irrigate 1 --dry-run  # Analysis only (no execution)
$P irrigate 1 --temp 22  # Override temperature (skips sync + weather)
$P irrigate 1 --no-sync  # Skip sensor sync (use DB data)
$P sync --hours 6        # Cloud → DB sensor sync
$P learn 1               # Learning report + efficiency alerts
$P history 1 --hours 24  # Readings + events combined timeline
$P stats 1 --days 7      # Statistics + CSV export (--export file.csv)

# SETUP (infrequent, CRUD)
$P cluster list
$P cluster add "Garden" --location "Backyard" --environment outdoor
$P plant list --cluster 1
$P plant add --cluster 1 "Ficus elastica" --category tropical --water-needs medium
$P irrigator list --cluster 1
$P irrigator start 1 --minutes 3
$P irrigator stop 1
$P irrigator log-manual 1 --minutes 5 --notes "Watered by hand"
$P sensor list --cluster 1
$P sensor add --cluster 1 --device-id XXXX --name "Nespolo" --type soil_moisture --plant-id 4
$P config get 1
$P config set --cluster 1 --mode smart --minutes 2 --interval 12
```

## Data Flow

### Sensor Sync (every 30min via cron)

```bash
python3 scripts/main.py sync --hours 24
```

1. Pulls `getdevicelog()` from Tuya Cloud (backfills gaps)
2. Gets `getstatus()` for live reading
3. Deduplicates by `(sensor_id, timestamp)` UNIQUE constraint
4. Stores in `sensor_readings` table

### Smart Irrigation Decision (7:00 + 20:00 via cron)

```bash
python3 scripts/main.py irrigate 1
```

1. Syncs sensor data from cloud
2. Determines temperature source based on `cluster.environment`:
   - **Indoor:** sensor temp primary, Open-Meteo fallback
   - **Outdoor:** Open-Meteo primary (outdoor temp matters)
3. Runs `IrrigationLogic.decide_for_cluster()`:
   - Global 6h cooldown check
   - Stress detection (water stress, heat, over-watering)
   - Multi-sensor conflict resolution (driest plant vs wettest)
   - Trend analysis (48h soil moisture, temperature)
   - Plant-specific targets from scientific literature
4. Executes on physical irrigator if `action == "irrigate"`
5. Logs decision to DB (even on skip or failure)

## Multi-Sensor Logic

With one irrigator serving multiple plants:

| Scenario | Action | Duration | Confidence |
|---|---|---|---|
| All adequate (40-65%) | Skip | — | 70% |
| One dry, none wet | Normal irrigation | 2-3 min | 80-90% |
| **Conflict:** one dry + one wet | Short burst | 1 min | 65% |
| All wet | Skip | — | 80% |

Decision uses `min_soil_moisture` (driest sensor), not average.

## Learning Engine

After ≥3 irrigation cycles with sensor data, the system learns:

- **Absorption rate:** +X%/min of irrigation per plant (how much water each receives)
- **Drainage rate:** -X%/hr natural moisture loss
- **Efficiency score:** How consistently irrigation increases moisture

### Alert Types

| Alert | Severity | Trigger |
|---|---|---|
| 🚫 Blocked drip | Critical | <0.5%/min absorption, <30% efficiency |
| 💨 Rapid drainage | Warning | >5%/hr moisture loss |
| 🏜️ Chronic underwatering | Warning | Peak moisture never reaches target (7d) |
| ⚠️ Unresolvable conflict | Critical | Irrigating for dry plant would bring wet plant >85% |

## Confidence Scoring

| Level | Source | Score |
|---|---|---|
| Critical stress override | Sensor + trends | 95% |
| Sensor-driven (adequate data) | Sensor | 70-90% |
| Temperature fallback | Open-Meteo | 60% |
| Minimal data | Config defaults | 20-30% |

## Testing

```bash
./test.sh    # 49 tests + ruff lint
```

| Suite | Tests | Coverage |
|---|---|---|
| `test_db.py` | 12 | DB operations, dedup, readings-around, bulk insert, environment |
| `test_logic.py` | 14 | Decisions, multi-sensor conflict, water needs, stress |
| `test_devices.py` | 8 | Device control, sensor parsing, error handling |
| `test_cloud.py` | 6 | Cloud API parsing, log grouping, credentials |
| `test_learning.py` | 9 | Absorption profiles, drainage, reports |

## Key Technical Decisions

- **Protocol v3.5** for Rainpoint IK10PW (not v3.3 — newer firmware)
- **Tuya Cloud as live source**, local SQLite as permanent archive
- **Sensor sampling ~10min** (firmware-hardcoded, not configurable)
- **UNIQUE(sensor_id, timestamp)** for zero-cost dedup on bulk sync
- **Evidence-based plant care** from scientific literature (see `PLANT_DATABASE.md`)

## Documentation

| File | Content |
|---|---|
| `SKILL.md` | This overview |
| `AGENTS.md` | Developer/AI guidelines, privacy rules |
| `PACKAGE.md` | Package structure, uv/ruff setup |
| `PLANT_DATABASE.md` | Plant care data sources |
| `data/schema.sql` | Database schema reference |

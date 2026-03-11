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

**Cluster types:**
- **With irrigator** — automated irrigation + learning alerts
- **Without irrigator** — moisture monitoring only (manual watering reminders)

**Device Communication:**
- **Sensors (Zigbee):** Tuya Cloud API only (required for Zigbee sub-devices)
- **Irrigators:** Tuya Cloud API (reliable, ~200ms latency)
- **Local mode deprecated:** Protocol v3.5 error 914 (query commands fail)

**Package:** `tuya_irrigation` (v0.5.0)

| Module | Purpose |
|---|---|
| `cli.py` | Single entry point: all commands |
| `cloud.py` | Tuya Cloud API client (getstatus, getdevicelog, DP parsing) |
| `db.py` | SQLite with dedup, bulk insert, readings-around queries |
| `devices.py` | Physical device control via Tuya Cloud API |
| `logic.py` | Smart decisions: multi-sensor conflict, trends, stress detection |
| `learning.py` | Post-irrigation analysis: absorption profiles, efficiency, alerts |
| `logger_daemon.py` | Cloud → DB sync (used by CLI `sync` + `check`) |
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
```

### Initialize Cluster

```bash
cd ~/.openclaw/workspace/skills/tuya-irrigation/scripts
python3 setup_cluster.py   # From tools/cluster.local.json
```

## CLI Reference

All commands via:
```bash
cd ~/.openclaw/workspace/skills/tuya-irrigation
P=".venv/bin/python3 scripts/main.py"
```

### Primary (cron / automated)

```bash
$P check --all           # Unified check: all clusters, all alert types
$P check 1               # Check single cluster
```

**Output protocol (for agent parsing):**
```
ACTION: irrigated|skipped|monitored|error <cluster_name> [— notes]
ALERT: <cluster_name> <type>          # needs_water | learning
ALERT_ITEM: <emoji> <message>
ALERT_END
```

**Exit codes:** `0` = silent (ok/skip) · `2` = alerts to forward · `1` = error

### Operations (interactive / manual)

```bash
$P status 1              # Full overview: sensors, config, events, smart analysis
$P irrigate 1            # Force irrigation pipeline: sync → weather → decide → execute
$P irrigate 1 --dry-run  # Analysis only, no execution
$P irrigate 1 --temp 22  # Override temperature (skips weather fetch)
$P irrigate 1 --no-sync  # Skip sensor sync (use DB data)
$P monitor 1             # Raw moisture check for sensor-only cluster
$P monitor 1 --no-sync   # Skip sync
$P sync --hours 6        # Cloud → DB sensor sync
$P learn 1               # Learning report + efficiency alerts
$P history 1 --hours 24  # Readings + events combined timeline
$P stats 1 --days 7      # Statistics + CSV export (--export file.csv)
```

### Setup (CRUD — infrequent)

```bash
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

## Cron Setup

Two cron jobs manage automated operation:

| Cron | Schedule | Command | Purpose |
|---|---|---|---|
| Sensor Sync | every 30min | `sync --hours 24` | Cloud → DB data freshness |
| Irrigation & Plant Check | 07:00 + 20:00 (Rome) | `check --all` | Irrigate + monitor + alert |

The **Irrigation & Plant Check** cron runs as an OpenClaw agent job: it executes `check --all`, parses `ALERT_ITEM:` lines (exit code 2), and forwards a single batched Telegram message to Kez.

Adding a new cluster (with or without irrigator) is automatically picked up by `check --all` — no cron changes needed.

## Check Command: Internal Logic

### Cluster with irrigator

1. Sync sensors from Tuya Cloud (last 6h)
2. Determine temperature (indoor → sensor primary; outdoor → Open-Meteo primary)
3. Run `IrrigationLogic.decide_for_cluster()`:
   - 6h global cooldown check
   - Stress detection (water stress, heat, over-watering)
   - Multi-sensor conflict resolution (driest plant wins)
   - 48h trend analysis
   - Plant-specific targets from scientific literature
4. Execute on irrigator if `action == "irrigate"`
5. Collect learning alerts (drainage, efficiency)
6. Output `ACTION:` + any `ALERT:` blocks

### Cluster without irrigator

1. Sync sensors from Tuya Cloud (last 2h)
2. Compare latest soil moisture vs plant targets
3. Flag sensors below threshold as `needs_water`
4. Output `ACTION: monitored` + `ALERT: needs_water` block if dry

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

- **Absorption rate:** +X%/min of irrigation per plant
- **Drainage rate:** -X%/hr natural moisture loss
- **Efficiency score:** How consistently irrigation increases moisture

### Alert Types

| Alert | Severity | Trigger |
|---|---|---|
| 🚫 Blocked drip | Critical | <0.5%/min absorption, <30% efficiency |
| 💨 Rapid drainage | Warning | >5%/hr moisture loss |
| 🏜️ Chronic underwatering | Warning | Peak moisture never reaches target (7d) |
| ⚠️ Unresolvable conflict | Critical | Irrigating dry plant would bring wet plant >85% |

## Confidence Scoring

| Level | Source | Score |
|---|---|---|
| Critical stress override | Sensor + trends | 95% |
| Sensor-driven (adequate data) | Sensor | 70-90% |
| Temperature fallback | Open-Meteo | 60% |
| Minimal data | Config defaults | 20-30% |

## Testing

```bash
./test.sh    # 48 tests + ruff lint
```

| Suite | Tests | Coverage |
|---|---|---|
| `test_db.py` | 12 | DB operations, dedup, readings-around, bulk insert, environment |
| `test_logic.py` | 14 | Decisions, multi-sensor conflict, water needs, stress |
| `test_devices.py` | 8 | Device control, sensor parsing, error handling |
| `test_cloud.py` | 6 | Cloud API parsing, log grouping, credentials |
| `test_learning.py` | 9 | Absorption profiles, drainage, reports |

## Key Technical Decisions

- **`check --all` as single cron entry point** — auto-detects cluster type, unified output protocol
- **Agent-side alert forwarding** — scripts never call Telegram directly; output is parsed by the agent
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

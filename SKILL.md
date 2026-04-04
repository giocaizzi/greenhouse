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

## Cluster Types

- **With irrigator** — automated irrigation + learning alerts
- **Without irrigator** — moisture monitoring only (manual watering reminders)

`check --all` auto-detects cluster type and runs the appropriate pipeline. Output is always structured `ACTION:` / `ALERT:` lines (exit code `2` = alerts to forward via Telegram).

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
cp tools/cluster.local.json.example tools/cluster.local.json
# Edit with your device IDs and credentials
tuya-irrigation cluster setup
```

## CLI Reference

### Primary (cron / automated)

```bash
tuya-irrigation check --all           # Unified check: all clusters, all alert types
tuya-irrigation check 1               # Check single cluster
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
tuya-irrigation status 1              # Full overview: sensors, config, events, smart analysis
tuya-irrigation irrigate 1            # Force irrigation pipeline: sync → weather → decide → execute
tuya-irrigation irrigate 1 --dry-run  # Analysis only, no execution
tuya-irrigation irrigate 1 --temp 22  # Override temperature (skips weather fetch)
tuya-irrigation irrigate 1 --no-sync  # Skip sensor sync (use DB data)
tuya-irrigation monitor 1             # Raw moisture check for sensor-only cluster
tuya-irrigation monitor 1 --no-sync   # Skip sync
tuya-irrigation sync --hours 6        # Cloud → DB sensor sync
tuya-irrigation learn 1               # Learning report + efficiency alerts
tuya-irrigation history 1 --hours 24  # Readings + events combined timeline
tuya-irrigation stats 1 --days 7      # Statistics + CSV export (--export file.csv)
```

### Setup (CRUD)

```bash
tuya-irrigation cluster setup                # Initialize cluster from config file
tuya-irrigation cluster setup --config path  # Custom config path
tuya-irrigation cluster list
tuya-irrigation cluster add "Garden" --location "Backyard" --environment outdoor
tuya-irrigation plant list --cluster 1
tuya-irrigation plant add --cluster 1 "Ficus elastica" --category tropical --water-needs medium
tuya-irrigation plant sync                   # Sync all plants with evidence-based data
tuya-irrigation plant sync --plant-id 1      # Sync specific plant
tuya-irrigation plant sync --cluster 1       # Sync plants in cluster
tuya-irrigation irrigator list --cluster 1
tuya-irrigation irrigator start 1 --minutes 3
tuya-irrigation irrigator stop 1
tuya-irrigation irrigator log-manual 1 --minutes 5 --notes "Watered by hand"
tuya-irrigation sensor list --cluster 1
tuya-irrigation sensor add --cluster 1 --device-id XXXX --name "Nespolo" --type soil_moisture --plant-id 4
tuya-irrigation config get --cluster 1
tuya-irrigation config set --cluster 1 --mode smart --minutes 2 --interval 12
```

## Cron Setup

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
| Blocked drip | Critical | <0.5%/min absorption, <30% efficiency |
| Rapid drainage | Warning | >5%/hr moisture loss |
| Chronic underwatering | Warning | Peak moisture never reaches target (7d) |
| Unresolvable conflict | Critical | Irrigating dry plant would bring wet plant >85% |

## Confidence Scoring

| Level | Source | Score |
|---|---|---|
| Critical stress override | Sensor + trends | 95% |
| Sensor-driven (adequate data) | Sensor | 70-90% |
| Temperature fallback | Open-Meteo | 60% |
| Minimal data | Config defaults | 20-30% |

## References

- [Plant Database](references/PLANT_DATABASE.md) — Evidence-based plant care data and sources
- [Database Schema](data/schema.sql) — SQLite table definitions

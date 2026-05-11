---
name: tuya-irrigation
description: |
  Smart irrigation system with Tuya IoT sensors and evidence-based plant care.
  Client-server architecture: FastAPI REST API + Typer CLI.
  Features: cluster management, sensor data sync, smart irrigation decisions,
  moisture monitoring, learning analytics, maintenance alerts.
  Use when: managing irrigation, adding plants/sensors, analyzing soil conditions,
  automated watering, checking plant health, viewing sensor history or stats.
compatibility: Requires tuya-irrigation-server running. Python 3.11+, uv.
metadata:
  author: kezclaw
  version: "1.0"
---

# Tuya Smart Irrigation System

Evidence-based irrigation with Tuya Cloud sensors, multi-plant conflict resolution, and self-learning efficiency analysis.

## Architecture

The system runs as a **server + CLI client**. The server handles all business logic, scheduling, and device control. The CLI sends HTTP requests and outputs JSON.

```
CLI (tuya-irrigation) → HTTP → Server (tuya-irrigation-server) → SQLite + Tuya Cloud
```

- **Server**: `tuya-irrigation-server` — FastAPI at `http://localhost:8000`, OpenAPI docs at `/docs`
- **CLI**: `tuya-irrigation` — Typer CLI, all output is JSON
- **Background**: APScheduler runs sensor sync (30min) and check-all (6h)

## Setup

### Environment Variables

**Server** (prefix `IRRIGATION_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `IRRIGATION_DB_URL` | `sqlite:///data/irrigation.db` | Database URL |
| `IRRIGATION_HOST` | `0.0.0.0` | Server bind host |
| `IRRIGATION_PORT` | `8000` | Server port |
| `IRRIGATION_SYNC_INTERVAL_MINUTES` | `30` | Sensor sync frequency |
| `IRRIGATION_CHECK_INTERVAL_HOURS` | `6` | Check-all frequency |

**Tuya Cloud** (required for sensor/device operations):

| Variable | Description |
|----------|-------------|
| `TUYA_CLIENT_ID` | Tuya IoT Platform client ID |
| `TUYA_CLIENT_SECRET` | Tuya IoT Platform client secret |
| `TUYA_REGION` | `eu`, `us`, `cn`, or `in` |

**CLI**:

| Variable | Default | Description |
|----------|---------|-------------|
| `IRRIGATION_SERVER_URL` | `http://localhost:8000` | Server URL |

### Start Server

```bash
uv run tuya-irrigation-server
```

### Initialize a Cluster

```bash
tuya-irrigation cluster add "My Plants" --location "Indoor" --environment indoor
tuya-irrigation plant add --cluster 1 "Monstera deliciosa" --category tropical --water-needs medium
tuya-irrigation irrigator add --cluster 1 --device-id YOUR_DEVICE_ID --name "Rainpoint" --type tuya_cloud
tuya-irrigation sensor add --cluster 1 --device-id SENSOR_ID --name "Monstera" --type soil_moisture --plant-id 1
tuya-irrigation config set --cluster 1 --mode smart --minutes 2 --interval 12
```

## CLI Reference

### Primary (automated / cron replacement)

```bash
tuya-irrigation check --all           # Check all clusters (irrigate or monitor)
tuya-irrigation check 1               # Check single cluster
```

All output is JSON. Exit codes: `0` = ok/skip, `2` = alerts present, `1` = error.

### Operations

```bash
tuya-irrigation status 1              # Full cluster overview
tuya-irrigation irrigate 1            # Smart irrigation pipeline
tuya-irrigation irrigate 1 --dry-run  # Analysis only
tuya-irrigation irrigate 1 --temp 22  # Override temperature
tuya-irrigation irrigate 1 --no-sync  # Skip sensor sync
tuya-irrigation monitor 1             # Raw moisture check
tuya-irrigation sync --hours 6        # Manual sensor sync
tuya-irrigation learn 1               # Learning report + alerts
tuya-irrigation history 1 --hours 24  # Readings + events timeline
tuya-irrigation stats 1 --days 7      # Statistics
tuya-irrigation stats 1 --export f.csv # CSV export
tuya-irrigation health                # Server health + scheduler
```

### CRUD

```bash
# Clusters
tuya-irrigation cluster list
tuya-irrigation cluster add "Garden" --location "Backyard" --environment outdoor

# Plants
tuya-irrigation plant list --cluster 1
tuya-irrigation plant add --cluster 1 "Ficus elastica" --category tropical --water-needs medium
tuya-irrigation plant sync                  # Sync all with evidence-based data
tuya-irrigation plant sync --plant-id 1     # Sync specific plant

# Irrigators
tuya-irrigation irrigator list --cluster 1
tuya-irrigation irrigator start 1 --minutes 3
tuya-irrigation irrigator stop 1
tuya-irrigation irrigator log-manual 1 --minutes 5 --notes "Watered by hand"

# Sensors
tuya-irrigation sensor list --cluster 1
tuya-irrigation sensor add --cluster 1 --device-id XXX --name "Sensor" --type soil_moisture --plant-id 1

# Config
tuya-irrigation config get --cluster 1
tuya-irrigation config set --cluster 1 --mode smart --minutes 2 --interval 12
```

### Server URL

```bash
tuya-irrigation --server http://192.168.1.50:8000 cluster list
# or
export IRRIGATION_SERVER_URL=http://192.168.1.50:8000
```

## Cluster Types

- **With irrigator** — automated irrigation + learning alerts
- **Without irrigator** — moisture monitoring only (manual watering reminders)

`check --all` auto-detects cluster type and runs the appropriate pipeline.

## Scheduling

The server runs two background jobs by default:

| Job | Interval | Purpose |
|-----|----------|---------|
| Sensor sync | every 30min | Cloud → DB data freshness |
| Check all | every 6h | Irrigate + monitor + collect alerts |

Manage via API: `GET /api/v1/scheduler/jobs`, `DELETE /api/v1/scheduler/jobs/{id}`

## References

For detailed information loaded on demand:

- [API Reference](references/API.md) — Full REST API endpoint documentation
- [Irrigation Logic](references/LOGIC.md) — Decision engine, multi-sensor conflict, learning engine, confidence scoring
- [Plant Database](references/PLANT_DATABASE.md) — Evidence-based plant care data and sources

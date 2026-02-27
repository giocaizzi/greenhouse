# Irrigation Logging & Statistics

Complete tracking and reporting system for irrigation events.

## Automatic Logging

Every irrigation action is **automatically logged** to the database with full context:

| Field | Description | Example |
|-------|-------------|---------|
| `timestamp` | When it happened | 1772175802 (2026-02-27 07:03) |
| `action` | What happened | start, stop, schedule_updated, skip_decision |
| `duration_minutes` | How long | 2, 3, 5 |
| `triggered_by` | Who/what triggered | auto, manual, schedule |
| `notes` | Decision context | "Smart logic: soil very dry (25% < 40%)" |
| `irrigator_id` | Which device | Rainpoint IK10PW |

## Commands

### View Recent Events

```bash
# Last 24 hours (default)
python3 main.py log events --cluster 1

# Last 48 hours with summary
python3 main.py log events --cluster 1 --hours 48
```

**Output:**
```
💧 Rainpoint IK10PW (last 48h, 4 events):
  2026-02-27 07:03 | start (2min) [auto]
    → Smart logic: temp above ideal (30°C > 29°C)
  2026-02-26 19:03 | skip_decision [auto]
    → Smart logic: soil moisture adequate (55%)

📊 Summary (48h):
   Total irrigations: 2
   Total water time: 4min
   Average per irrigation: 2.0min
```

### Statistics

```bash
# Last 7 days (default)
python3 main.py log stats --cluster 1

# Last 30 days
python3 main.py log stats --cluster 1 --days 30
```

**Output:**
```
📊 Irrigation Statistics - My Indoor Cluster
   Period: last 7 days

🔢 Summary:
   Total events: 14
   Irrigations: 14
   Total water time: 35min
   Average per irrigation: 2.5min
   Frequency: 2.0 times/day

📋 Events by type:
   start: 12
   skip_decision: 2

🎯 Triggered by:
   auto: 10 (71%)
   manual: 4 (29%)

💧 Recent irrigations:
   2026-02-27 19:03 | 2min | auto | Rainpoint IK10PW
   2026-02-27 07:03 | 3min | manual | Rainpoint IK10PW
   ...
```

### Export to CSV

For external analysis (Excel, Python, R):

```bash
# Export last 30 days
python3 main.py log stats --cluster 1 --days 30 --export irrigation_data.csv
```

**CSV format:**
```csv
timestamp,date,time,irrigator,action,duration_minutes,triggered_by,notes
1772175802,2026-02-27,07:03:22,Rainpoint IK10PW,start,2,auto,"Smart logic: ..."
```

### Formatted Report

Human-readable report with summary and context:

```bash
# Console output
python3 report.py 1 --days 7

# Save to file
python3 report.py 1 --days 7 --output weekly_report.txt
python3 report.py 1 --days 30 --output monthly_report.txt
```

**Report format:**
```
🌱 Irrigation Report: My Indoor Cluster
📅 Period: 2026-02-27 (last 7 days)

💧 Summary:
• Irrigations: 14
• Total water time: 35min
• Average per irrigation: 2min
• Frequency: 2.0 times/day

🎯 Triggered by:
• auto: 10 (71%)
• manual: 4 (29%)

📋 Recent irrigations:
• 02/27 19:03: 2min (auto)
• 02/27 07:03: 3min (manual)

🌿 Plants: 4
• Monstera deliciosa [medium]
• Areca/Kentia palm [medium]
• Dracaena [low]
• Nespolo [medium]

⚙️ Configuration:
• Mode: smart
• Schedule: 2min every 12h
• Auto-run: ON
```

## Automated Reports

### Weekly Summary via Cron

Add to OpenClaw cron for automatic weekly reports:

```bash
# Via OpenClaw cron (recommended)
openclaw cron add --name "Weekly irrigation report" \
  --schedule '{"kind":"cron","expr":"0 9 * * 1","tz":"Europe/Rome"}' \
  --payload '{"kind":"systemEvent","text":"Generate weekly irrigation report for cluster 1"}' \
  --delivery '{"mode":"announce"}'
```

Or via system cron:

```bash
# Every Monday at 9 AM
0 9 * * 1 cd ~/.openclaw/workspace/skills/tuya-irrigation/scripts && python3 report.py 1 --days 7
```

### Monthly Report

```bash
# First day of month at 10 AM
0 10 1 * * cd ~/.openclaw/workspace/skills/tuya-irrigation/scripts && python3 report.py 1 --days 30 --output ~/monthly_irrigation_report.txt
```

## What Gets Logged

### Automatic Events

Logged by `auto-irrigate` command:

- **Decision to irrigate**: `action=start` or `action=schedule_updated`
  - Includes: duration, interval, confidence, reason
- **Decision to skip**: `action=skip_decision`
  - Includes: why (e.g., "soil moisture adequate")
- **Errors**: `action=error`
  - Includes: error message

### Manual Events

Logged by manual commands (`irrigator on/off/start`):

- **Manual start**: `action=start`, `triggered_by=manual`
- **Manual on/off**: `action=on` or `action=off`

## Analysis Use Cases

### Track Water Usage

```bash
# Monthly water consumption
python3 main.py log stats --cluster 1 --days 30
# Look at "Total water time" field
```

### Optimize Frequency

```bash
# Compare auto vs manual
python3 main.py log stats --cluster 1 --days 7
# Check "Triggered by" breakdown
```

### Debug Issues

```bash
# Recent errors
python3 main.py log events --cluster 1 --hours 168 | grep error

# Check skip reasons
python3 main.py log events --cluster 1 --hours 48 | grep skip_decision
```

### Export for ML/Analysis

```bash
# Export 90 days of data
python3 main.py log stats --cluster 1 --days 90 --export irrigation_90d.csv

# Then analyze in Python/R/Excel
import pandas as pd
df = pd.read_csv('irrigation_90d.csv')
df['date'] = pd.to_datetime(df['date'])
# ... analysis
```

## Database Tables

Events stored in `irrigation_events` table:

```sql
CREATE TABLE irrigation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    irrigator_id INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    action TEXT NOT NULL,
    duration_minutes INTEGER,
    triggered_by TEXT NOT NULL,
    notes TEXT,
    FOREIGN KEY (irrigator_id) REFERENCES irrigators(id)
);
```

Sensor readings stored in `sensor_readings` table (when sensors available).

## Retention

- **No automatic cleanup**: All events kept indefinitely
- **Manual cleanup** (if needed):
  ```python
  from db import IrrigationDB
  db = IrrigationDB()
  # Delete events older than 1 year
  cutoff = int(time.time()) - (365 * 24 * 3600)
  db.conn.execute("DELETE FROM irrigation_events WHERE timestamp < ?", (cutoff,))
  db.conn.commit()
  ```

## Tips

- Run `log stats` weekly to monitor health
- Export monthly for long-term trends
- Check `skip_decision` events to tune thresholds
- Compare auto vs manual to validate smart logic
- Use reports for accountability/debugging

---

Complete transparency and traceability for every drop of water. 💧📊

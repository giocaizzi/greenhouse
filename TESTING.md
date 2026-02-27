# Testing Guide - Smart Irrigation System

## Current Status (2026-02-27)

✅ **Implemented & Tested**:
- Database schema & initialization
- Cluster/plant/irrigator management
- CLI commands (all working)
- Smart analysis with temperature fallback
- Device connectivity (Rainpoint IK10PW)
- Event logging structure

⏳ **Pending (waiting for sensors)**:
- Sensor reading & parsing
- Sensor data logging daemon
- Smart logic with real sensor data
- High-confidence irrigation decisions

## Test Plan

### Phase 1: Without Sensors (NOW) ✅

```bash
. ~/.openclaw/config/secrets.env
cd ~/.openclaw/workspace/skills/tuya-irrigation/scripts

# 1. Setup
python3 setup_kez_cluster.py

# 2. Verify data
python3 main.py cluster list
python3 main.py plant list
python3 main.py irrigator list
python3 main.py config get 1

# 3. Test analysis (temperature-based fallback)
python3 main.py analyze 1 --temp 18
python3 main.py analyze 1 --temp 22
python3 main.py analyze 1 --temp 28

# 4. Test device control (manual)
python3 main.py irrigator status 1
# python3 main.py irrigator start 1 --minutes 1  # Uncomment to test actual irrigation

# 5. Test event logging
python3 main.py log events --cluster 1
```

### Phase 2: With Sensors (WHEN THEY ARRIVE)

```bash
. ~/.openclaw/config/secrets.env
cd ~/.openclaw/workspace/skills/tuya-irrigation/scripts

# 1. Add sensors
python3 main.py sensor add \
  --cluster 1 \
  --device-id <TUYA_SENSOR_ID_1> \
  --name "Soil Moisture Sensor" \
  --type soil_moisture

python3 main.py sensor add \
  --cluster 1 \
  --device-id <TUYA_SENSOR_ID_2> \
  --name "Temp/Humidity Sensor" \
  --type temp_humidity

# 2. Test sensor reading
python3 main.py sensor read --cluster 1

# 3. Start continuous logging (every 30 min)
python3 logger.py --interval 30 &
LOGGER_PID=$!
echo "Logger PID: $LOGGER_PID"

# 4. Wait for data collection (run for a few days)
# ... wait ...

# 5. Check collected data
python3 main.py log readings --cluster 1 --hours 48

# 6. Test smart analysis with sensor data
python3 main.py analyze 1
# Should now show higher confidence and sensor-driven decisions

# 7. Switch to smart mode
python3 main.py config set --cluster 1 --mode smart

# 8. Test auto-irrigation with smart logic
python3 main.py auto-irrigate 1
# Should use sensor data + plant profiles

# 9. Monitor for a week
python3 main.py log events --cluster 1 --hours 168
python3 main.py log readings --cluster 1 --hours 168
```

### Phase 3: Production (AFTER VALIDATION)

```bash
# 1. Update HEARTBEAT.md
# Replace:
#   python3 irrigation_manager.py --wttr "$WTTR"
# With:
#   python3 main.py auto-irrigate 1

# 2. Add cron for sensor logging (if not using systemd/supervisor)
# */30 * * * * . ~/.openclaw/config/secrets.env && python3 ~/.openclaw/workspace/skills/tuya-irrigation/scripts/logger.py

# 3. Monitor daily for first week
python3 main.py log events --cluster 1 --hours 24

# 4. Tune if needed
# - Adjust plant profiles (water_needs, ideal_temp, etc.)
# - Modify irrigation_logic.py decision thresholds
# - Update duration/interval defaults
```

## Expected Behavior

### Temperature-based (no sensors)

| Feels-like Temp | Interval | Duration | Confidence |
|-----------------|----------|----------|------------|
| ≤18°C           | 24h      | 2 min    | 60%        |
| 19-24°C         | 12h      | 2 min    | 60%        |
| 25-28°C         | 8h       | 2 min    | 60%        |
| ≥29°C           | 6h       | 2 min    | 60%        |

### Smart (with sensors)

Primary: **Soil Moisture**
- <30%: irrigate 3 min / 8h (high confidence 90%)
- 30-50%: irrigate 2 min / 12h (confidence 80%)
- >50%: skip (confidence 70%)

Adjustments:
- High temp (>ideal+3°C): -4h interval
- Low temp (<ideal-3°C): +6h interval
- Low humidity (<ideal-10%): -2h interval
- High water needs plants: +1 min, -2h interval
- Low water needs plants: -1 min, +4h interval

## Success Metrics

- ✅ No wilting or yellowing leaves
- ✅ No water pooling in saucers (overwatering)
- ✅ Consistent soil moisture (40-60% ideal for these plants)
- ✅ Battery lasts >1 month
- ✅ No false starts (triggered when not needed)

## Troubleshooting

### "No sensors found"
Normal before sensors arrive. System uses temperature fallback.

### "Cannot parse sensor data"
Check sensor device type and output format. May need to update `devices.py` parsing logic for specific sensor models.

### "Low confidence decisions"
Expected until sensor data accumulates. Wait 2-3 days for baseline.

### Database locked
Stop any running `logger.py` processes: `pkill -f logger.py`

### Irrigation not stopping
Manual stop: `python3 main.py irrigator off 1`

## Rollback Plan

If smart system fails, revert to old system:

```bash
# Stop logger
pkill -f logger.py

# Revert HEARTBEAT.md to use irrigation_manager.py
# (old script still exists and works)

# Keep database for debugging
cp ~/.openclaw/workspace/skills/tuya-irrigation/data/irrigation.db ~/backup_irrigation.db
```

## Notes

- Database path: `~/.openclaw/workspace/skills/tuya-irrigation/data/irrigation.db`
- Backup DB before major changes
- Sensor readings accumulate over time (no auto-cleanup yet)
- Event log grows over time (consider periodic archival)
- Smart logic is conservative by default (better dry than drowned)

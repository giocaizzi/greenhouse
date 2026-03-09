# Sensor Integration Guide

## Supported Sensor Types

The system supports these sensor types (extensible):

- `temp_humidity` - Temperature + humidity sensors
- `soil_moisture` - Soil moisture sensors
- `light` - Light/lux sensors

## Adding a New Sensor

### Step 1: Get Tuya Device ID

From Tuya IoT Platform:
1. Add device to your project
2. Copy Device ID (format: `bf123456789abcdef`)

### Step 2: Add to Database

```bash
# Env vars loaded automatically by OpenClaw
cd ~/.openclaw/workspace/skills/tuya-irrigation/scripts

python3 main.py sensor add \
  --cluster 1 \
  --device-id bf123456789abcdef \
  --name "Living Room Temp Sensor" \
  --type temp_humidity
```

For local mode (faster, more reliable):
```bash
python3 main.py sensor add \
  --cluster 1 \
  --device-id bf123456789abcdef \
  --name "Living Room Temp Sensor" \
  --type temp_humidity \
  --device-ip 192.0.2.2 \
  --local-key xxxxxxxxxxxxxxxx
```

### Step 3: Test Reading

```bash
python3 main.py sensor read --cluster 1
```

Expected output:
```
📊 Living Room Temp Sensor:
  Temperature: 22.5°C
  Humidity: 65%
```

### Step 4: Start Continuous Logging

```bash
# Run once
python3 logger.py

# Run continuously (every 30 minutes)
python3 logger.py --interval 30 &
```

### Step 5: Verify Data Collection

```bash
# Check last 24 hours
python3 main.py log readings --cluster 1 --hours 24
```

Expected output:
```
📊 Living Room Temp Sensor (last 24h, 48 readings):
  2026-02-27 18:00 | temp=22.5°C | hum=65%
  2026-02-27 17:30 | temp=22.3°C | hum=66%
  ...
```

## Sensor Data Parsing

The `devices.py` module uses basic text parsing of the `tuya_irrigation.py status` output.

### Current Parsing Logic

```python
for line in output.split("\n"):
    line_lower = line.lower()
    
    # Temperature
    if "temperature" in line_lower or "temp" in line_lower:
        # Extract: "Temperature: 22.5°C" → 22.5
        temp = float([x for x in line.split() if "°" in x][0].rstrip("°C").rstrip("°"))
        data["temperature"] = temp
    
    # Humidity
    if "humidity" in line_lower:
        # Extract: "Humidity: 65%" → 65
        hum = float([x for x in line.split() if "%" in x][0].rstrip("%"))
        data["humidity"] = hum
    
    # Soil moisture
    if "soil" in line_lower or "moisture" in line_lower:
        # Extract: "Soil moisture: 45%" → 45
        moisture = float([x for x in line.split() if "%" in x][0].rstrip("%"))
        data["soil_moisture"] = moisture
    
    # Light
    if "light" in line_lower or "lux" in line_lower:
        # Extract: "Light: 350 lux" → 350
        light = int([x for x in line.split() if x.isdigit()][0])
        data["light"] = light
```

### If Parsing Fails

If your sensor outputs different format, update `devices.py` parsing:

1. Run `python3 tuya_irrigation.py status` with `TUYA_DEVICE_ID` set to your sensor
2. Copy the raw output
3. Update parsing logic in `devices.py` → `read_sensor()` method
4. Test again

Example custom parser for specific sensor:
```python
# In devices.py, add to read_sensor():

if sensor.tuya_device_id == "bf123456789abcdef":  # Your specific sensor
    # Custom parsing for this model
    if "va_temperature" in line:
        temp = int(line.split(":")[-1].strip()) / 10.0  # Some sensors return temp*10
        data["temperature"] = temp
```

## Multiple Sensors in One Cluster

You can have multiple sensors per cluster:

```bash
# Soil moisture near roots
python3 main.py sensor add \
  --cluster 1 \
  --device-id bf111222333444555 \
  --name "Soil Moisture - Monstera" \
  --type soil_moisture

# Air temp/humidity near leaves
python3 main.py sensor add \
  --cluster 1 \
  --device-id bf666777888999aaa \
  --name "Air Sensor - Cluster" \
  --type temp_humidity

# Light sensor
python3 main.py sensor add \
  --cluster 1 \
  --device-id bfaaabbbcccdddee \
  --name "Light - Window" \
  --type light
```

The smart logic averages all readings for decision-making.

## Tuya Sensor Datapoints

Common Tuya sensor DPs (check your device's actual DPs with `discover`):

### Temperature/Humidity Sensors
- `va_temperature` (value) - Temperature in °C (sometimes ×10)
- `va_humidity` (value) - Humidity in %
- `battery_percentage` (value) - Battery level

### Soil Moisture Sensors
- `humidity_value` (value) - Soil moisture in %
- `temp_current` (value) - Soil temperature in °C
- `battery_percentage` (value) - Battery level

### Light Sensors
- `bright_value` (value) - Brightness in lux

Run discovery to see what your sensor actually reports:
```bash
TUYA_DEVICE_ID=bf123456789abcdef python3 tuya_irrigation.py discover
```

## Troubleshooting

### "Cannot read sensor"
1. Check device is online in Tuya IoT Platform
2. Verify `TUYA_CLIENT_ID` and `TUYA_CLIENT_SECRET` are correct
3. Check device region matches `TUYA_REGION`
4. Try reading via `tuya_irrigation.py status` directly

### "Sensor returns 0 or null values"
- Sensor might be offline or battery dead
- Check Tuya app to see if device is reporting
- Wait a few minutes and try again (some sensors have slow update intervals)

### "Parsing returns empty data"
- Sensor output format might be different
- Run `discover` to see actual DPs
- Update parsing logic in `devices.py`

### "Logger crashes or stops"
- Check database isn't locked (stop other processes)
- Check disk space in `~/.openclaw/workspace/skills/tuya-irrigation/data/`
- Check Tuya API rate limits (free tier has limits)

## Best Practices

1. **Test reading manually** before starting logger
2. **Use local mode** for sensors near your Pi (faster, more reliable)
3. **Check battery levels** weekly (add to heartbeat checks)
4. **Backup database** before major changes
5. **Monitor first week** closely to verify data quality
6. **Calibrate if needed** - some cheap sensors need offset correction

## Calibration

If sensors are inaccurate, add calibration in `devices.py`:

```python
# After reading sensor data
if sensor.id == 1:  # Specific sensor needs calibration
    if data.get("temperature"):
        data["temperature"] += 2.0  # Offset correction
    if data.get("soil_moisture"):
        data["soil_moisture"] *= 0.9  # Scale correction
```

Or add to sensor config JSON:
```bash
# Manual DB edit or rebuild with calibration
config = {"calibration": {"temp_offset": 2.0, "moisture_scale": 0.9}}
```

Then update `devices.py` to apply calibration from config.

## Advanced: Direct API Integration

For better parsing, you can bypass `tuya_irrigation.py` and call Tuya API directly in `devices.py`:

```python
# In read_sensor():
resp = self._run_tuya_script(sensor.tuya_device_id, "status")
# becomes:
from tuya_irrigation import TuyaClient
client = TuyaClient(self.client_id, self.secret, sensor.tuya_device_id, self.region)
status = client.status()  # Returns list of {code, value} dicts
```

This gives you structured data instead of text parsing.

---

Ready to integrate your sensors! 🌱📊

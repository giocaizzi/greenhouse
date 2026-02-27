# Historical Trends & Stress Detection

Advanced irrigation logic using time-series analysis and stress indicators.

## Overview

The system analyzes **48 hours of sensor data** and **7 days of irrigation history** to:
- Detect trends (rising/declining soil moisture, temperature changes)
- Identify stress conditions (water stress, heat stress, over-watering)
- Adjust irrigation decisions based on patterns, not just current state

This prevents issues like:
- **Water stress** from slow drying before sensors trigger "dry" threshold
- **Over-watering** from excessive manual intervention
- **Heat stress** from prolonged temperature extremes

## Trend Analysis

### Soil Moisture Trend

Compares first half vs second half of 48h window:

| Delta | Trend | Impact |
|-------|-------|--------|
| < -5% | Declining | Reduce interval by 2h |
| > +5% | Rising | Increase interval by 2h |
| -5 to +5% | Stable | No adjustment |

**Example:**
```
t-48h: 55% → t-24h: 45% → t-0h: 33%
Delta: -22% (first half 50% vs second half 28%)
→ Trend: DECLINING (-15% delta)
→ Action: Reduce interval (12h → 10h)
```

### Temperature Trend

Same windowing approach:

| Delta | Trend | Impact |
|-------|-------|--------|
| > +2°C | Rising | Reduce interval by 2h (if hot) |
| < -2°C | Falling | Increase interval by 2h (if cold) |
| -2 to +2°C | Stable | No adjustment |

### Irrigation Frequency

Analyzes last 7 days:

| Pattern | Detection | Impact |
|---------|-----------|--------|
| < 1/day + short duration | Under-watering | Increase duration by 1min |
| > 3/day | Over-watering | Flag as stress condition |

## Stress Detection

### Water Stress (⚠️ Priority Override)

**Critical conditions:**
- Soil < 30% (critical low) → **Irrigate 3min every 6h**
- Soil < 40% + declining > -10% (steep decline) → **Irrigate 3min every 6h**

**Confidence:** 95% (highest priority)

**Example:**
```
Soil: 33% (adequate by threshold)
Trend: declining -15% over 48h
→ WATER STRESS: "low (33%) + steep decline (-15%)"
→ Override: irrigate 3min every 6h
```

### Heat Stress

**Conditions:**
- Temperature > ideal_max + 5°C
- Rising temperature trend

**Impact:**
- Logged in decision context
- Increases urgency of irrigation
- Combined with other indicators

### Over-watering Stress (⚠️ Priority Override)

**Critical conditions:**
- Soil > 70% (saturated) + high frequency (>3/day) → **Skip, 24h interval**
- Soil > 70% + rising trend → **Skip, 24h interval**

**Confidence:** 90%

**Purpose:** Prevent root rot and fungal issues

## Decision Flow

```
1. Collect sensor data (24h avg + 48h history)
2. Analyze trends (soil moisture, temperature, frequency)
3. Detect stress conditions

4. PRIORITY CHECK:
   ├─ Water stress? → IRRIGATE (3min/6h) [95% confidence]
   ├─ Over-watering? → SKIP (24h) [90% confidence]
   └─ No critical stress → Continue to normal logic

5. Normal logic (if no critical stress):
   ├─ Check soil moisture vs plant targets
   ├─ Apply temperature adjustments
   ├─ Apply humidity adjustments
   └─ Apply trend-based adjustments

6. Return decision with:
   - action, duration, interval
   - reason (with trend indicators 📉📈)
   - confidence score
   - stress_indicators dict
   - trends dict
```

## Output Format

### Decision Object

```python
{
  "action": "irrigate",  # or "skip"
  "duration_minutes": 3,
  "interval_hours": 6,
  "reason": "⚠️ water stress detected (low (33%) + steep decline (-15%))",
  "confidence": 0.95,
  "stress_indicators": {
    "water_stress": "low (33%) + steep decline (-15%)"
  },
  "trends": {
    "soil_moisture_trend": "declining",
    "soil_moisture_delta": -15.0,
    "temperature_trend": "falling",
    "temperature_delta": -2.5,
    "irrigation_avg_per_day": 1.4,
    "irrigation_avg_duration": 2.0
  }
}
```

### Reason Indicators

- `⚠️ water stress detected` — Critical condition override
- `⚠️ over-watering detected` — Critical condition override
- `📉 soil moisture declining` — Negative trend adjustment
- `📈 soil moisture rising` — Positive trend adjustment
- `🌡️ temperature rising + hot` — Environmental stress
- `📊 recent under-watering pattern` — Historical pattern detected

## Testing

```bash
# Test with simulated declining moisture scenario
cd ~/.openclaw/workspace/skills/tuya-irrigation/scripts
python3 test_trends.py 1 --populate

# Output will show:
# - 48h of declining soil moisture (55% → 27%)
# - Sparse irrigation pattern (every 2 days)
# - Detection of water stress with steep decline
# - Decision: irrigate 3min/6h with 95% confidence
```

## Use Cases

### 1. Slow Drying Detection

**Scenario:** Soil moisture declining from 50% to 35% over 2 days (still "adequate" by threshold)

**Without trends:** Skip (35% is adequate)
**With trends:** Water stress detected → Irrigate

### 2. Over-enthusiastic Manual Watering

**Scenario:** User manually waters 4 times per day, soil at 75%

**Without trends:** Skip (soil too wet)
**With trends:** Over-watering stress → Skip + extend interval to 24h

### 3. Heatwave Recovery

**Scenario:** Temperature spiked to 32°C for 3 days, now cooling

**Without trends:** Hot conditions → short interval
**With trends:** Falling trend detected → moderate interval (don't over-compensate)

## Configuration

No manual configuration required — trends and stress detection are **always active** when sensor data is available.

Fallback to temperature-based logic when:
- No sensors configured
- Sensor data is too sparse (< 4 readings in 48h)

## Thresholds (Tunable)

Current thresholds in `irrigation_logic.py`:

```python
# Soil moisture trend detection
TREND_THRESHOLD = 5  # % change to classify as rising/declining

# Water stress detection
CRITICAL_LOW = 30  # % below which is critical
EARLY_WARNING = 40  # % with steep decline triggers stress
STEEP_DECLINE = -10  # % delta considered steep

# Over-watering detection  
SATURATED = 70  # % above which is excessive
HIGH_FREQUENCY = 3  # irrigations/day threshold

# Temperature trend
TEMP_DELTA = 2  # °C change to classify as rising/falling
```

## Future Enhancements

Potential additions:
- Machine learning on historical patterns
- Seasonal adjustments (winter vs summer)
- Plant health scoring over time
- Predictive modeling (forecast next irrigation need)
- Integration with weather forecast APIs

---

The system learns from the past to make smarter decisions for the future. 📊🌱

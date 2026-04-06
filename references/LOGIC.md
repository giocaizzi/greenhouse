# Irrigation Logic Reference

## Check Command Pipeline

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
6. Collect maintenance alerts (battery, stale data, humidity, light)

### Cluster without irrigator

1. Sync sensors from Tuya Cloud (last 2h)
2. Compare latest soil moisture vs plant targets
3. Flag sensors below threshold as `needs_water`

## Multi-Sensor Conflict Resolution

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

### Maintenance Alerts

| Alert | Trigger |
|---|---|
| battery_low | Sensor battery state is "low" |
| stale_data | No readings in last 3h |
| low_env_humidity | Ambient humidity below plant ideal - 10% |
| low_light | Daytime avg lux below seasonal plant minimum * 0.5 |

## Confidence Scoring

| Level | Source | Score |
|---|---|---|
| Critical stress override | Sensor + trends | 95% |
| Water warning (device) | Device DP 111 | 92% |
| Sensor-driven (adequate data) | Sensor | 70-90% |
| Temperature fallback | Open-Meteo | 60% |
| Minimal data | Config defaults | 20-30% |

## Constants

All thresholds in `libs/tuya-irrigation-core/tuya_irrigation_core/constants.py`:

- Cooldown: 6h between irrigations
- Soil moisture: critical 30%, low 40%, saturated 70%
- Duration: default 2min, conflict 1min, stress 3min, max 5min
- Intervals: min 6h, max 24h, default 12h

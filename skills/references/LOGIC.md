# Irrigation Logic Reference

## Typed Decision Pipeline

The irrigation engine produces a single typed `IrrigationDecision` per evaluation. The pipeline composes pure rule functions so each step is independently testable and contributes structured `Reason` entries to the trail.

### `IrrigationDecision` shape

```python
class IrrigationDecision(BaseModel):
    cluster_id: int
    evaluated_at: int           # Unix timestamp
    action: Action              # "irrigate" | "skip"
    duration_minutes: int
    interval_hours: int
    confidence: float           # 0.0–1.0
    reasons: list[Reason]       # ordered, most decisive first
    sensor_snapshot: SensorSnapshot | None
    stress_indicators: StressIndicators
    trends: Trends
    weather: WeatherSnapshot | None
```

### `Reason` and `TriggerCode`

Every rule function appends a `Reason` to the decision's trail via `decision.add_reason(code, message, severity=…, duration_delta=…, interval_delta=…)`.

`TriggerCode` is a `StrEnum` of stable identifiers. The UI, MCP, and audit log key on these codes without parsing free text. Adding a new code is non-breaking; renaming one is.

**Terminal codes** (set the action on their own):

| Code | Effect |
|------|--------|
| `no_plants` | Skip — cluster has no plants |
| `cooldown` | Skip — last irrigation within 6h |
| `daily_cap_hit` | Skip — per-day rate limit reached |
| `water_warning` | Irrigate — device DP 111 triggered |
| `water_stress` | Irrigate — critical low moisture |
| `over_watering` | Skip — soil saturated |
| `sensor_very_dry` | Irrigate — below critical threshold |
| `sensor_dry` | Irrigate — below low threshold |
| `sensor_adequate` | Skip — moisture in target band |
| `sensor_wet` | Skip — moisture above saturation |
| `conflict` | Short burst — one dry, one wet |
| `weather_skip` | Skip — precipitation forecast ≥ threshold |
| `temp_fallback` | Decide from temperature alone (no sensor) |
| `config_fallback` | Decide from config interval alone |
| `no_data` | Skip — no usable data |

**Adjustment codes** (modify duration/interval delta, don't override action):

`temp_high`, `temp_low`, `humidity_very_low`, `humidity_low`, `humidity_high`, `light_very_bright`, `light_bright`, `light_dark`, `light_very_dark`, `water_needs_high`, `water_needs_low`, `trend_moisture_declining`, `trend_moisture_rising`, `trend_temp_rising`, `underwatering_pattern`, `learning_alert`

### Decision persistence

Every evaluation is persisted to `decision_logs` (whether acted-on or not) via `DecisionLog`:

| Column | Type | Notes |
|--------|------|-------|
| `cluster_id` | int | |
| `evaluated_at` | int | Unix timestamp |
| `action` | str | `"irrigate"` or `"skip"` |
| `primary_code` | str | `reasons[0].code` |
| `reason_text` | str | `"; "`-joined reason messages |
| `confidence` | float | |
| `actuated` | bool | True only if the irrigator was started |
| `triggered_by` | str | `"auto"` or `"manual"` |
| `payload_json` | str | Full `IrrigationDecision` JSON |

Accessible via `GET /api/v1/clusters/{id}/decisions`.

### Weather-skip rule

If the `WeatherClient` returns `precipitation_next_6h_mm` above a configured threshold, the engine appends a `weather_skip` reason and returns `action=SKIP` before evaluating sensor data. This runs after cooldown and rate-cap checks, before stress detection.

## Check Command Pipeline

### Cluster with irrigator

1. Sync sensors from Tuya Cloud (last 6h)
2. Determine temperature (indoor → sensor primary; outdoor → Open-Meteo primary)
3. Run trust layer: sensor anomaly scan (drift + stale), leak/stuck-valve detector
4. Run `IrrigationEngine.decide_for_cluster()` → typed `IrrigationDecision`:
   - 6h global cooldown check
   - Per-day rate cap check
   - Weather-aware precipitation skip
   - Stress detection (water stress, heat, over-watering)
   - Multi-sensor conflict resolution (driest plant wins)
   - 48h trend analysis
   - Plant-specific targets from scientific literature
5. Persist `DecisionLog`
6. Execute on irrigator if `action == "irrigate"` and not dry-run / vacation window
7. Emit `ActivityEvent`; reconcile alert inbox

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

Decision uses `min_soil_moisture` (driest sensor), not average. Code: `TriggerCode.CONFLICT`.

## Trust Layer

Runs before the decision engine on every evaluation:

- **Sensor anomaly scan** — per-sensor drift detection (std dev floor 1.0% to avoid false positives on near-constant series) and stale-data check (no readings in last 3h). Raises `sensor_drift` / `stale_data` alerts.
- **Leak detector** — flags irrigators where recent moisture stayed low after a recorded irrigation (possible blocked drip or failed actuation).
- **Stuck-valve detector** — flags irrigators that show unexplained moisture rise without a logged irrigation event.
- **Rate limit** — per-cluster daily cap (configurable); logs `daily_cap_hit` reason and skips if exceeded.

## Learning Engine

After ≥3 irrigation cycles with sensor data, the system learns:

- **Absorption rate:** +X%/min of irrigation per plant
- **Drainage rate:** -X%/hr natural moisture loss
- **Efficiency score:** how consistently irrigation increases moisture

### Alert Types (learning)

| Alert | Severity | Trigger |
|---|---|---|
| Blocked drip | Critical | <0.5%/min absorption, <30% efficiency |
| Rapid drainage | Warning | >5%/hr moisture loss |
| Chronic underwatering | Warning | Peak moisture never reaches target (7d) |
| Unresolvable conflict | Critical | Irrigating dry plant would bring wet plant >85% |

### Alert Types (maintenance)

| Alert | Trigger |
|---|---|
| `battery_low` | Sensor battery state is "low" |
| `stale_data` | No readings in last 3h |
| `sensor_drift` | Sensor readings anomalously constant |
| `low_env_humidity` | Ambient humidity below plant ideal - 10% |
| `low_light` | Daytime avg lux below seasonal plant minimum * 0.5 |

### Alert inbox lifecycle

Alerts are deduplicated by a stable `dedup_key` (source + entity + code + plant). Status flows `open → acknowledged → resolved`. Each inbox entry tracks `first_seen_at`, `last_seen_at`, and `occurrence_count`.

## Plant Health Score

A daily 0–100 composite score per plant:

- In-band time fraction for soil moisture, temperature, humidity (weighted)
- Learning-derived irrigation efficiency factor

Stored in `plant_health_daily` for long-horizon trend plotting. Snapshot job runs once daily; also triggerable via `POST /api/v1/plants/health/snapshot`.

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

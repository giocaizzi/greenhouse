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
| `cooldown` | Skip — any irrigator in cluster fired within 6h |
| `quiet_hours` | Skip — current local time is inside the configured quiet-hours window (auto runs only) |
| `water_warning` | Irrigate — sensor DP 111 water-warning set |
| `water_stress` | Irrigate — critical low moisture |
| `over_watering` | Skip — soil saturated |
| `outside_window` | Skip — local time outside the cluster's irrigation window / preferred hours |
| `sensor_very_dry` | Irrigate — below critical threshold |
| `sensor_dry` | Irrigate — below low threshold |
| `sensor_adequate` | Skip — moisture in target band |
| `sensor_wet` | Skip — moisture above saturation |
| `conflict` | Short burst — one dry, one wet |
| `weather_skip` | Skip — rain forecast > 2mm/6h (outdoor clusters only) |
| `temp_fallback` | Decide from temperature alone (no sensor) |
| `config_fallback` | Decide from config interval alone |
| `no_data` | Skip — no usable data |
| `vacation_budget_exhausted` | Skip — vacation reservoir budget is spent this cycle (see Vacation rationing below) |

`daily_cap_hit` is defined in the enum but is **not** emitted by the decision engine. Per-day rate limits (`max_events_per_day`, `daily_cap_minutes`) are enforced at the API actuation routes (HTTP 409), not inside `decide_for_cluster`.

**Device-health gate codes** force `Action.SKIP` at actuation time (applied by the irrigation service, not the engine — see Device-Health Gate below): `device_no_water`, `device_rain_detected`, `device_offline`. Advisory (non-blocking) device codes also exist: `device_battery_low`, `device_battery_critical`, `device_signal_loss`.

**Adjustment codes** (modify duration/interval delta, don't override action):

`temp_high`, `temp_low`, `humidity_very_low`, `humidity_low`, `humidity_high`, `light_very_bright`, `light_bright`, `light_dark`, `light_very_dark`, `water_needs_high`, `water_needs_low`, `trend_moisture_declining`, `trend_moisture_rising`, `trend_temp_rising`, `underwatering_pattern`, `learning_alert`, `seasonal_hold`, `seasonal_boost`, `vacation_rationing`

**Informational codes** (audit-only, never change action or dosage):

`vacation_active` — appended to *every* decision while a vacation window is active, so logs always record vacation status (even on SKIP).

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

### Quiet-hours gate

A hard gate against automatic actuation during user-defined quiet windows (indoor pumps are noisy at night). It runs **after the cooldown check and before the weather-skip rule**, so cooldown is still the most decisive skip reason.

- The window is resolved through the hierarchical config (see below): `quiet_start_hour` / `quiet_end_hour` are integers 0–23, end-exclusive, wrap-around supported (`start > end` crosses midnight). Timezone comes from `preferences.timezone` (UTC fallback).
- `start == end` at a level means quiet hours are **explicitly disabled** there — e.g. an outdoor cluster opting out of an inherited indoor window.
- When the current local hour is inside the window and the run is automatic, the engine emits `quiet_hours` and returns `action=SKIP`.
- **Manual override:** a manual trigger with `force=true` (REST `POST /clusters/{id}/irrigate` body, or the UI confirm) bypasses the SKIP. Every other rule still runs, and the final decision carries a `manual_override_quiet_hours` reason (severity WARNING) so the audit log records that the gate was overridden.

Quiet hours have **no built-in default** in the resolver: production deployments are seeded with the canonical `00:00–05:00` window by the Alembic migration, while fresh databases (tests, dev installs) start with quiet hours **off** until configured at the global or cluster level.

### Hierarchical irrigation config

Each `IrrigationConfig` field resolves **cluster → global → built-in constant**. A `null` at a level means "inherit from the next level down." `Repository.get_effective_config(cluster_id)` returns, per field, `{value, source}` where `source ∈ {"cluster", "global", "default"}` so the UI/CLI can render inheritance state. The engine reads quiet-hours bounds through this same resolver.

Surfaces: `GET/PUT /api/v1/config/global` (the singleton global defaults), `GET /api/v1/clusters/{id}/config/effective` (merged view), `PUT /api/v1/clusters/{id}/config` (per-cluster partial override). In the web UI the global defaults live on `/preferences`; per-cluster overrides render inline on `/clusters/{id}#config` with source badges.

### Weather-skip rule

For **outdoor** clusters only, if a weather client is configured and the 6h forecast reports `precipitation_mm > 2.0`, the engine appends a `weather_skip` reason and returns `action=SKIP` before fetching sensor data. No-ops for indoor clusters or when no weather client is wired. Runs after the cooldown and quiet-hours checks, before stress detection.

### Irrigation-window gate

After the terminal stress overrides (`water_warning`, `water_stress`, `over_watering`) — so a wilting plant still gets water at 2am — the engine applies a local-time gate before the soil-moisture rule:

- If the cluster has `IrrigationWindow` rows, the current local time must fall inside at least one window (weekday mask + `[start_hour, end_hour)`, wrap-around supported). Outside the allowed hours it emits `outside_window` and skips.
- **If it has none, all hours are allowed** (issue #83) — the gate is a no-op. Night protection comes from quiet hours, not this rule. `preferred_water_hours_local` remains advisory plant data (still merged by `plant_db.get_care_data` and surfaced in plant care info) but no longer gates actuation.

Timezone comes from `preferences.timezone` (UTC fallback).

### Seasonal frequency multiplier

After all sensor/temperature/humidity/light/trend adjustments, `decision.interval_hours` is scaled by a plant-aware seasonal multiplier (a *frequency* factor: the interval is divided by it, then clamped to `[MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS]`). When the multiplier ≠ 1.0 it appends `seasonal_hold` (factor < 1.0, stretch interval) or `seasonal_boost` (factor > 1.0, tighten interval).

Precedence (most → least specific): species-level `season_frequency_multiplier{,_outdoor}` → category-level value under `_category_defaults` → built-in default table. The `_outdoor` key is used when `cluster.environment == "outdoor"`; per-season keys missing at one layer fall through to the next. Built-in defaults:

| Season | Indoor | Outdoor |
|---|---|---|
| winter | 0.5 | 0.3 |
| spring | 1.0 | 1.0 |
| summer | 1.2 | 1.5 |
| autumn | 0.8 | 0.7 |

The 6h cooldown remains the hard floor regardless of multiplier.

### Vacation rationing (final adjustment)

`_apply_vacation_budget` is the **last** engine adjustment — it runs after the seasonal multiplier, so it clamps the final dosage. It makes vacation windows genuinely *enforced*: rather than a blanket hold, the engine rations a configured reservoir so the water lasts the trip.

Behaviour by case:

- **No active vacation** → no-op, decision returned unchanged.
- **Vacation active** → appends an informational `vacation_active` reason (with the window dates) to *every* decision, including SKIPs, for the audit trail.
- **Vacation active, but no capacity configured** → normal irrigation. Rationing only engages when the cluster's **irrigator** has **both** `reservoir_l` (usable tank volume, liters) and `flow_rate_l_per_min` (pump throughput, L/min) set. Unset capacity = today's behavior.
- **Vacation active, capacity set, action is not `irrigate`** → no-op (only real irrigations are throttled).

A cluster is irrigated by a single device (strict 0:1): `run_irrigation_pipeline` actuates the cluster's irrigator, so rationing tracks **that same tank**. Budget-envelope math for the cluster's irrigator, applied when a vacation is active and the decision is to irrigate:

```
usable_l       = reservoir_l * VACATION_RESERVOIR_USABLE_FRACTION   # 0.95 — reserve 5% so the pump never runs dry
D_days         = max(1, ceil((ends_at - starts_at) / 86400))        # vacation length in days
day_index      = floor((now - starts_at) / 86400)                   # 0-based current day
daily_budget_l = usable_l / D_days
allowed_cum_l  = min(usable_l, daily_budget_l * (day_index + 1))     # cumulative allowance through today
spent_l        = consumption so far this vacation (Σ start-event minutes × flow_rate, [starts_at, now])
headroom_l     = max(0, allowed_cum_l - spent_l)
binding_max_min = floor(headroom_l / flow_rate_l_per_min)
```

Then:

- `binding_max_min >= decision.duration_minutes` → within budget, duration unchanged.
- `VACATION_MIN_RUN_MINUTES (1) <= binding_max_min < decision.duration_minutes` → trim `duration_minutes` to `binding_max_min`, append `vacation_rationing`.
- `binding_max_min < VACATION_MIN_RUN_MINUTES` → no meaningful budget left this cycle: flip to `Action.SKIP`, `duration_minutes = 0`, append `vacation_budget_exhausted`, confidence set to the cooldown level.

The tank is assumed full at vacation start; consumption is derived by summing recorded `start` irrigation-event durations × flow rate within the window. Relevant constants live in `constants.py`: `VACATION_RESERVOIR_USABLE_FRACTION = 0.95`, `VACATION_MIN_RUN_MINUTES = 1`.

### Device-health gate (actuation-time)

Separate from the engine: when the irrigation **service** is about to actuate, it consults the cached `DeviceHealthMonitor` state via `is_actuation_blocked`. If a blocking alarm (`device_no_water`, `device_rain_detected`, `device_offline`) is open for the target irrigator, it appends a `CRITICAL` reason, flips the decision to `Action.SKIP`, re-persists the `DecisionLog`, and records a `decision_skip` activity event — no water is dispensed.

### Pump dry-run abort (DP 105)

While an irrigation is running, `PumpWatcherService` polls the IK10PW's DP 105 water-shortage alarm (~2s cadence, local protocol v3.5) after a short warmup. On the first `NO_WATER` reading it immediately stops the pump, raises a `no_water` health alert through `DeviceHealthMonitor` (dedup key `health:irrigator:{id}:no_water`), and records an `aborted` irrigation event. False positives are safe (stop early); the alarm is motor-current-based, so a hardware float switch is still recommended for unattended use.

## Check Command Pipeline

### Cluster with irrigator

1. Read the cluster's latest persisted sensor snapshot (`SyncService.ensure_fresh_and_read`); this hits SQLite, and force-syncs from the Tuya Cloud **only** for a sensor whose newest reading is staler than `SENSOR_READING_STALE_SECONDS` (4h). The background sync job (default every 3h) is the routine Cloud writer; the pipeline no longer syncs every sensor on every check.
2. Determine temperature (indoor → sensor primary; outdoor → Open-Meteo primary)
3. Run trust layer: sensor anomaly scan (drift + stale), leak/stuck-valve detector
4. Run `decide_for_cluster()` → typed `IrrigationDecision`, in order:
   - Sensor snapshot + trends are built from a **cleaned view** of each sensor's series (range-gate + Hampel spike filter; see Sensor Data Cleaning)
   - `no_plants` short-circuit
   - 6h global cooldown check (any irrigator in cluster)
   - Quiet-hours gate (auto runs skip inside the window; manual `force=true` bypasses with a `manual_override_quiet_hours` warning)
   - Weather-aware precipitation skip (outdoor only, > 2mm/6h)
   - Terminal stress overrides (`water_warning`, then `water_stress` / `over_watering`)
   - Irrigation-window / preferred-hours gate (runs *after* stress overrides)
   - Soil-moisture rule (driest plant wins) + conflict resolution
   - Temperature / humidity / light / water-needs / 48h-trend adjustments
   - Seasonal frequency multiplier on the interval
   - Vacation rationing (final adjustment): when a vacation is active, append `vacation_active`; if the cluster's irrigator has reservoir + flow capacity, clamp/skip the run to fit the burn-down budget (`vacation_rationing` / `vacation_budget_exhausted`)
   - (No sensor data → temperature/config fallback path instead)
5. Persist `DecisionLog`
6. If `action == "irrigate"` and not dry-run: device-health actuation gate (may flip to skip), then execute on the irrigator for `decision.duration_minutes` (already rationed by the vacation rule if a vacation is active); `PumpWatcherService` watches DP 105 for the run's duration
7. Emit `ActivityEvent`; reconcile alert inbox

### Cluster without irrigator

1. Read the latest persisted sensor snapshot (`ensure_fresh_and_read`; SQLite, force-syncing only a stale sensor)
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

A conflict fires only when the driest sensor is below `target_min` **and** the wettest is within `CONFLICT_WET_MARGIN` (5%) of `target_max`. The margin is deliberately narrow so the wet band does not overlap the healthy range: a normal spread such as driest 44 / wettest 56 against a 45–65 target is treated as ordinarily dry (driest drives the call), not a spurious unresolvable conflict.

## Sensor Data Cleaning

Raw Tuya readings are noisy — capacitive soil probes glitch to single-sample **spikes** that revert one reading later, a wedged probe reports a **flat run**, and comms errors inject **dirty out-of-range** values. Because the soil-moisture rule keys on `min_soil_moisture` (the driest sensor), a single spurious low sample is otherwise enough to trigger a needless irrigation.

`logic/cleaning.py:clean_readings()` produces a *cleaned view* of one sensor's series at read time. It is consumed by the decision snapshot (`logic/sensors.py:get_recent_sensor_data`) and the 48h trend analysis (`logic/trends.py`), applied **per sensor** before aggregation. **Raw rows in `sensor_readings` are never mutated** — they remain the permanent record, and charts / anomaly detection still see the unfiltered signal.

Two stages, applied independently per numeric metric (`temperature`, `soil_moisture`, `env_humidity`, `light`):

1. **Range gate** — values outside the per-metric physical bounds in `SENSOR_PHYSICAL_RANGES` are dropped as dirty (e.g. humidity 250%, a negative-temperature blip). `0.0` stays in-range so a genuinely bone-dry probe survives.
2. **Hampel spike filter** — the standard robust time-series test: a point is rejected when it deviates from its rolling-window median (radius 3 → 7 samples) by more than `CLEANING_HAMPEL_N_SIGMA` (3.0) scaled MADs (`× 1.4826`). The MAD scale is floored at `CLEANING_MAD_FLOOR` (1.0%) so a flat run (MAD ≈ 0) does **not** flag a later genuine step change (e.g. a real post-irrigation jump) as an outlier. Series shorter than `CLEANING_HAMPEL_MIN_READINGS` (5) are left untouched — too little context to judge spikes.

Cleaning is field-independent (a `soil_moisture` spike does not discard that reading's `temperature`) and advisory (rejected values become `None`, so existing `is not None` filters skip them). This complements the trust layer's `sensor_drift` scan, which intentionally runs on the *raw* series to detect the drift cleaning would otherwise mask.

## Trust Layer

Runs before the decision engine on every evaluation:

- **Sensor anomaly scan** — per-sensor drift detection (std dev floor 1.0% to avoid false positives on near-constant series) and stale-data check (no readings in last 3h). Raises `sensor_drift` / `stale_data` alerts.
- **Leak detector** — flags irrigators where recent moisture stayed low after a recorded irrigation (possible blocked drip or failed actuation).
- **Stuck-valve detector** — flags irrigators that show unexplained moisture rise without a logged irrigation event.

Per-day rate limits (`max_events_per_day`, `daily_cap_minutes`) are **not** part of the decision engine or trust layer — they are enforced at the API actuation routes, which return HTTP 409 when a manual or scheduled start would exceed the cap.

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

All thresholds in `libs/greenhouse-core/greenhouse_core/constants.py`:

- Cooldown: 6h between irrigations (`MIN_COOLDOWN_HOURS`)
- Sensor cleaning: physical ranges `SENSOR_PHYSICAL_RANGES`; Hampel spike filter `CLEANING_HAMPEL_WINDOW_RADIUS = 3`, `CLEANING_HAMPEL_N_SIGMA = 3.0`, `CLEANING_HAMPEL_MIN_READINGS = 5`, `CLEANING_MAD_SCALE = 1.4826`, `CLEANING_MAD_FLOOR = 1.0`
- Soil moisture: critical 30%, low 40%, saturated 70%
- Duration: default 2min, conflict 1min, stress 3min, max 5min
- Intervals: min 6h, max 24h, default 12h (conflict 8h, stress 6h)
- Preferred watering window default: 06:00–10:00 local (`DEFAULT_PREFERRED_WATER_HOURS`)
- Seasonal multipliers: indoor {winter 0.5, spring 1.0, summer 1.2, autumn 0.8}, outdoor {0.3, 1.0, 1.5, 0.7}
- Quiet-hours seed window: 00:00–05:00 local (`DEFAULT_QUIET_START_HOUR` / `DEFAULT_QUIET_END_HOUR`) — used by the migration seed only, **not** as a resolver fallback (unconfigured = off)
- Hierarchical config built-ins: `DEFAULT_IRRIGATION_MODE = "smart"`, `DEFAULT_AUTO_RUN = True`
- Vacation rationing: `VACATION_RESERVOIR_USABLE_FRACTION = 0.95` (reserve 5% so the pump never runs dry), `VACATION_MIN_RUN_MINUTES = 1` (below this, skip instead of a token dribble)

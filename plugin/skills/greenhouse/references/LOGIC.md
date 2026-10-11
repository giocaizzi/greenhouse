# Irrigation Logic Reference

## Typed Decision Pipeline

The irrigation engine (`IrrigationLogic.decide_for_cluster` in `greenhouse_core/logic/engine.py`) produces a single typed `IrrigationDecision` per evaluation. The pipeline composes rule functions so each step is independently testable and contributes structured `Reason` entries to the trail. The behavior described here is pinned by the decision-grid and engine goldens; `logic/engine.py` stays one rule pipeline whose rules are small private helpers (the names used below are stable).

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
| `cooldown` | Skip — the cluster's irrigator has a `start` event within 6h (auto, manual, forced, or a logged manual watering — `log-manual` records `start` too) |
| `leak_hold` | Skip — a confirmed leak / stuck valve is unresolved on the cluster (24h hold, see Trust Layer) |
| `quiet_hours` | Skip — current local time is inside the configured quiet-hours window (auto runs only) |
| `water_warning` | Irrigate — sensor DP 111 water-warning set (the probe reports the soil dry; it is never treated as a probe fault) <!-- TODO(D): only the newest reading per sensor counts (61905d9) --> |
| `water_stress` | Irrigate — critical low moisture: **average** soil moisture < 30%, or < 40% and falling steeply (see the note under the pipeline) |
| `over_watering` | Skip — average soil moisture > 70% **and** (high irrigation frequency or a rising trend) |
| `outside_window` | Skip — local time outside every configured `IrrigationWindow` of the cluster (never emitted when the cluster has no windows) |
| `sensor_very_dry` | Irrigate — below critical threshold |
| `sensor_dry` | Irrigate — below low threshold |
| `sensor_adequate` | Skip — moisture in target band |
| `sensor_wet` | Skip — moisture above saturation |
| `conflict` | Short burst — one dry, one wet |
| `weather_skip` | Skip — rain forecast > 2mm/6h (non-indoor clusters only) |
| `temp_fallback` | Decide from temperature alone (no sensor) |
| `config_fallback` | Decide from config interval alone |
| `no_data` | Skip — no usable data |
| `vacation_budget_exhausted` | Skip — vacation reservoir budget is spent this cycle (see Vacation rationing below) |

**Actuation gate codes** turn an IRRIGATE decision into `Action.SKIP` at actuation time. The irrigation service applies them after the engine, in this order (see Actuation gates below):

| Code | Effect |
|------|--------|
| `device_no_water`, `device_rain_detected`, `device_offline` | Skip — a blocking device-health alarm is open on the cluster's irrigator |
| `daily_cap_hit` | Skip — the run would exceed `max_events_per_day` or `daily_cap_minutes` (cluster value, else the global one) |
| `dry_run_global` | Skip — the global dry-run preference is on: the decision is evaluated and logged, the pump never starts |

Advisory (non-blocking) device codes also exist: `device_battery_low`, `device_battery_critical`, `device_signal_loss`.

**Adjustment codes** (modify duration/interval delta, don't override action):

`temp_high`, `temp_low`, `humidity_very_low`, `humidity_low`, `humidity_high`, `light_very_bright`, `light_bright`, `light_dark`, `light_very_dark`, `water_needs_high`, `water_needs_low`, `trend_moisture_declining`, `trend_moisture_rising`, `trend_temp_rising`, `underwatering_pattern`, `learning_alert`, `seasonal_hold`, `seasonal_boost`, `vacation_rationing`

**Informational codes** (audit-only, never change action or dosage):

`vacation_active` — appended while a vacation window is active to every decision that reaches the final adjustment step (including a SKIP from the soil-moisture rule). Decisions that end earlier — `no_plants`, `leak_hold`, `cooldown`, `quiet_hours`, `weather_skip`, `water_warning` / `water_stress` / `over_watering`, `outside_window`, and the no-sensor fallback — carry no `vacation_active` reason and are not rationed.

### Decision persistence

Every evaluation run through the pipeline (`irrigate`, `check`, the scheduled `check_all`, including dry runs) is persisted to `decision_logs` — whether acted on or not — via `DecisionLog`. A run that an **actuation gate** stops is re-written to the skip: the gate's reason leads the trail, so `action = "skip"` and `primary_code` is the gate code (`device_*`, `daily_cap_hit`, `dry_run_global`), with `actuated = false`; a `decision_skip` activity event carries the details.

| Column | Type | Notes |
|--------|------|-------|
| `cluster_id` | int | |
| `evaluated_at` | int | Unix timestamp |
| `action` | str | `"irrigate"` or `"skip"` |
| `primary_code` | str | `reasons[0].code` <!-- TODO(D): never NULL — a decision with no other reason carries `no_action_needed` (f20ebff) --> |
| `reason_text` | str | `"; "`-joined reason messages |
| `confidence` | float | |
| `actuated` | bool | True only if the irrigator was started |
| `triggered_by` | str | `"auto"`, or `"manual"` for a `force=true` irrigate (its `start` event and push say `manual` too) |
| `payload_json` | str | Full `IrrigationDecision` JSON |

Accessible via `GET /api/v1/clusters/{id}/decisions`.

### Quiet-hours gate

A hard gate against automatic actuation during user-defined quiet windows (indoor pumps are noisy at night). It runs **after the cooldown check and before the weather-skip rule**, so cooldown is still the most decisive skip reason.

- The window is resolved through the hierarchical config (see below): `quiet_start_hour` / `quiet_end_hour` are integers 0–23, end-exclusive, wrap-around supported (`start > end` crosses midnight). Timezone comes from `preferences.timezone` (UTC fallback).
- `start == end` at a level means quiet hours are **explicitly disabled** there — e.g. an outdoor cluster opting out of an inherited indoor window.
- When the current local hour is inside the window and the run is automatic, the engine emits `quiet_hours` and returns `action=SKIP`.
- **Manual override:** a manual trigger with `force=true` (REST `POST /clusters/{id}/irrigate` body, or the UI confirm) bypasses the SKIP. Every other rule still runs (and so do the actuation gates and the leak hold), and the final decision carries a `manual_override_quiet_hours` reason (severity WARNING) so the audit log records that the gate was overridden. A forced run is recorded as **manual** throughout (decision log, `start` event, push), keeps the post-watering leak check and the dry-run watcher, and is stopped if the server shuts down mid-run.

Quiet hours have **no built-in default** in the resolver: production deployments are seeded with the canonical `00:00–05:00` window by the Alembic migration, while fresh databases (tests, dev installs) start with quiet hours **off** until configured at the global or cluster level.

### Hierarchical irrigation config

Each `IrrigationConfig` field resolves **cluster → global → built-in constant**. A `null` at a level means "inherit from the next level down." `Repository.get_effective_config(cluster_id)` returns, per field, `{value, source}` where `source ∈ {"cluster", "global", "default"}` so the UI/CLI can render inheritance state. The engine reads quiet-hours bounds through this same resolver.

Surfaces: `GET/PUT /api/v1/config/global` (the singleton global defaults), `GET /api/v1/clusters/{id}/config/effective` (merged view), `PUT /api/v1/clusters/{id}/config` (per-cluster partial override). In the web UI the global defaults live on `/preferences`; per-cluster overrides render inline on `/clusters/{id}#config` with source badges.

### Weather-skip rule

For **non-indoor** clusters only (`environment != "indoor"`), if a weather client is configured and the 6h forecast reports `precipitation_mm > 2.0`, the engine appends a `weather_skip` reason and returns `action=SKIP` before fetching sensor data. No-ops for indoor clusters or when no weather client is wired. Runs after the cooldown and quiet-hours checks, before stress detection.

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
- **Vacation active** → appends an informational `vacation_active` reason (with the window dates) for the audit trail — on every decision that reaches this step, SKIPs included. Terminal early exits never reach it: critical stress (`water_stress`), the device `water_warning` and the no-sensor temperature/config fallback **bypass rationing** and run their normal dose even during a vacation, and cooldown / quiet-hours / leak-hold / weather / window skips carry no `vacation_active` tag. <!-- TODO(D): emergency paths obey windows and vacation rationing (0c43ca8); a vacation ending exactly now is over (e081eb0). -->
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
- `binding_max_min < VACATION_MIN_RUN_MINUTES` → no meaningful budget left this cycle: flip to `Action.SKIP`, `duration_minutes = 0`, put `vacation_budget_exhausted` **first** in the trail (so it is the decision's `primary_code` and leads `reason_text`), confidence set to the cooldown level.

The tank is assumed full at vacation start; consumption is derived by summing recorded `start` irrigation-event durations × flow rate within the window. Relevant constants live in `constants.py`: `VACATION_RESERVOIR_USABLE_FRACTION = 0.95`, `VACATION_MIN_RUN_MINUTES = 1`.

### Actuation gates (actuation-time)

Separate from the engine: when the irrigation **service** is about to actuate an IRRIGATE decision, it runs three gates (`services/actuation_gates.py`) in a fixed order; the first that fires wins, and `force=true` bypasses none of them:

1. **Device health** — the cached `DeviceHealthMonitor` state (`is_actuation_blocked`). A blocking alarm (`device_no_water`, `device_rain_detected`, `device_offline`) open on the target irrigator → `CRITICAL` reason; the API result also lists `blocking_alarms`. The cache is rebuilt from the open health alerts at startup, so an alarm raised before a restart blocks immediately.
2. **Per-day caps** — `max_events_per_day` (start events in the last `DAILY_CAP_WINDOW_HOURS`, 24 h) and `daily_cap_minutes` (minutes in that window, this run included), each resolved cluster → global → none (`services/caps.py`) → `daily_cap_hit` (WARNING). The manual start path refuses on the same verdict with HTTP 409.
3. **Global dry run** — the `dry_run_global` preference → `dry_run_global` (WARNING, "would have irrigated N min"). The manual start path refuses with HTTP 409.

A block puts its reason first in the trail, flips the result to `skip`, rewrites the engine's `DecisionLog` row to that skip (`primary_code` = the gate code, `actuated = false`) and records a `decision_skip` activity event with the payload (`blocking_alarms` / `daily_cap` / `dry_run_global`, `irrigator_id`) — no water is dispensed.

### Pump dry-run abort (DP 105)

While an irrigation is running, `PumpWatcherService` polls the IK10PW's DP 105 water-shortage alarm (~2s cadence, local protocol v3.5) after a short warmup. On the first `NO_WATER` reading it immediately stops the pump, raises a `no_water` health alert through `DeviceHealthMonitor` (dedup key `health:irrigator:{id}:no_water`), and records an `aborted` irrigation event. False positives are safe (stop early); the alarm is motor-current-based, so a hardware float switch is still recommended for unattended use. If the server shuts down mid-irrigation the watcher is interrupted (shutdown no longer waits out the cycle): an **auto** cycle and a **forced** run (`force=true`, recorded as manual) are stopped (`stop` event, `triggered_by="shutdown"`); a plain **manual** start is left running on the device's own DP 102 timer without dry-run protection. All log a `pump_watcher_shutdown` activity event. If a database write fails during a trip, the pump is still stopped first; each record commits on its own and a lost alert is retried.

## Check Command Pipeline

### Cluster with irrigator

`check` / `check_all` first skip a cluster whose effective config has `auto_run = false` (result `skipped`, no decision); otherwise they run the pipeline below — the same one `POST /clusters/{id}/irrigate` runs.

1. Resolve the temperature. With `temp_override` it is used as is (no sensor read). Otherwise read the cluster's latest persisted sensor snapshot (`SyncService.ensure_fresh_and_read`, skipped by `no_sync`); this hits SQLite and force-syncs from the Tuya Cloud **only** for a sensor whose newest reading is staler than `SENSOR_READING_STALE_SECONDS` (4h). The background sync job (default every 3h) is the routine Cloud writer. Indoor clusters prefer the sensor temperature, then the Open-Meteo feels-like; other clusters prefer Open-Meteo, then the sensor; 20 °C (`FALLBACK_TEMPERATURE_C`) when neither is available.
2. Run `decide_for_cluster()` → typed `IrrigationDecision`, in order:
   - Sensor snapshot + trends are built from a **cleaned view** of each sensor's series (range-gate + Hampel spike filter; see Sensor Data Cleaning)
   - `no_plants` short-circuit
   - Leak / stuck-valve hold (safety gate — runs *before* cooldown so the audit trail names the hardware fault, not the routine skip)
   - 6h global cooldown check (the cluster's irrigator, any `start` event)
   - Quiet-hours gate (auto runs skip inside the window; manual `force=true` bypasses with a `manual_override_quiet_hours` warning)
   - Weather-aware precipitation skip (non-indoor only, > 2mm/6h)
   - No sensors / no sensor data → temperature/config fallback (`temp_fallback` / `config_fallback` / `no_data`), then done
   - Terminal stress overrides (`water_warning`, then `water_stress` / `over_watering`)
   - Irrigation-window gate (runs *after* stress overrides; no-op without `IrrigationWindow` rows)
   - Soil-moisture rule (driest plant wins) + conflict resolution
   - Temperature / humidity / light / water-needs / 48h-trend adjustments
   - Seasonal frequency multiplier on the interval
   - Vacation rationing (final adjustment): when a vacation is active, append `vacation_active`; if the cluster's irrigator has reservoir + flow capacity, clamp/skip the run to fit the burn-down budget (`vacation_rationing` / `vacation_budget_exhausted`)
3. Persist `DecisionLog` (dry runs too). A `skip` (not dry-run) writes a `decision_skip` activity event.
4. If `action == "irrigate"` and not dry-run: the actuation gates — device health → per-day caps → global dry run (any may flip to skip, see above) — then start the cluster's irrigator for `decision.duration_minutes` (already rationed by the vacation rule if a vacation is active). Order after the device accepts: `start` event (`triggered_by="auto"`, or `"manual"` for `force=true`) → `irrigated` activity → `DecisionLog` marked actuated → (forced only) `leak_check_armed` marker → **commit** → leak check scheduled (30 min later) → DP 105 dry-run watcher → push notification (`auto`, or `manual` when forced). Committing before the follow-ups means a later failure cannot erase a run the pump really made, so the 6h cooldown always sees it.
   - A refused or failed device call records an `attempted` event, an `actuation_failed` activity row and alert, and an error result. For the IK10PW this includes a start whose local Duration DP (102) cannot be written: the run is refused before the pump is switched on (there is no keep-alive fallback).
   - If the start cannot be recorded after the device accepted it, the server stops the pump again (compensating stop) and the error surfaces: `irrigate` answers 500; `check_all` reports the cluster as `error` and raises `check_failed`.
5. `check` then reconciles the alert inbox for the cluster (learning + maintenance alerts). `check_all` commits each cluster on its own; a cluster that crashes is rolled back alone and raises `check_failed`.

There is no per-evaluation anomaly scan: sensor drift / staleness are judged by the separate `sensor_anomaly` job (see Trust Layer).

### Cluster without irrigator

1. Read the latest persisted sensor snapshot (`ensure_fresh_and_read`; SQLite, force-syncing only a stale sensor). The same path backs `GET clusters/{id}/monitor` (API/MCP/CLI) and the web monitor panel: both 404 on an unknown cluster and **store** the readings a force-sync fetched, so repeated calls do not re-hit the Cloud for a sensor that is now fresh.
2. Clean each sensor's readings over `SNAPSHOT_LOOKBACK_HOURS` (24h, enough samples for the spike filter), take the newest soil value inside the last 2h (`MONITOR_LOOKBACK_HOURS`) and classify it against the linked plant's target band: `very_dry` (< min − 15), `dry` (< min), `wet` (> max + 10), `ok`, or `no_data`. A lone glitch is filtered out instead of reading `very_dry`.
3. List the `dry` / `very_dry` sensors in `needs_water` (the CLI's `monitor` exits 2 when it is non-empty; `check --all` folds it into `has_alerts`).

### Soil-moisture target parsing

A plant's `soil_moisture_target` (e.g. `"45-65"`) is read through one parser, `parse_moisture_target`
(`logic/plant_needs.py`, via `moisture_target_range`), everywhere it is judged: the engine, plant health, the forecast,
the monitor / check of sensor-only clusters, and the learning issue heuristics (chronic underwatering, unresolvable
conflict). It takes the first two `-`-separated numbers (`"40-50-60"` → 40–50) and falls back to the default band
45–65 for anything else — a bare `"50"`, non-numeric text, a missing or non-string value. It does not validate the
band (an inverted `"65-45"` is used as given). <!-- TODO(D): validated since 4a195d3 (inverted bands swapped; out-of-range / NaN / inf → default band). --> The plant chart's water-needs threshold band uses the same parser;
only a target with no `-` at all (or none) shows the default band labelled `default`.

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

`logic/cleaning.py:clean_readings()` produces a *cleaned view* of one sensor's series at read time, applied **per sensor** before aggregation. **Raw rows in `sensor_readings` are never mutated** — they remain the permanent record.

The line is drawn by what the caller does with the data:

- **Judgements read the cleaned view** — decision snapshot (`logic/sensors.py`), 48h trends (`logic/trends.py`), leak detection (`services/leak.py`), efficacy scoring (`services/efficacy.py`), plant health scores (`services/health.py`), next-irrigation forecast (`services/forecast.py`), learned profiles and issue heuristics (`learning/profiling.py`, `learning/issues.py`).
- **Archive and display read raw** — charts, cluster history, data-quality reports, the maintenance battery/staleness checks, and the `sensor_drift` anomaly scan (which must see the drift cleaning would mask).

Two ordering helpers keep that switch cheap: `clean_readings_desc()` mirrors `get_recent_readings`' newest-first ordering, and `clean_readings_around()` cleans a before/after pair as one series before splitting it back, so a spike sitting at the event boundary is judged against neighbours on both sides.

Two stages, applied independently per numeric metric (`temperature`, `soil_moisture`, `env_humidity`, `light`):

1. **Range gate** — values outside the per-metric physical bounds in `SENSOR_PHYSICAL_RANGES` are dropped as dirty (e.g. humidity 250%, a negative-temperature blip). `0.0` stays in-range so a genuinely bone-dry probe survives.
2. **Hampel spike filter** — the standard robust time-series test: a point is rejected when it deviates from its rolling-window median (radius 3 → 7 samples) by more than `CLEANING_HAMPEL_N_SIGMA` (3.0) scaled MADs (`× 1.4826`). The MAD scale is floored at `CLEANING_MAD_FLOOR` (1.0%) so a flat run (MAD ≈ 0) does **not** flag a later genuine step change (e.g. a real post-irrigation jump) as an outlier. Series shorter than `CLEANING_HAMPEL_MIN_READINGS` (5) are left untouched — too little context to judge spikes.

Cleaning is field-independent (a `soil_moisture` spike does not discard that reading's `temperature`) and advisory (rejected values become `None`, so existing `is not None` filters skip them). This complements the trust layer's `sensor_drift` scan, which intentionally runs on the *raw* series to detect the drift cleaning would otherwise mask.

## Trust Layer

Three independent safety nets, none of which is a step inside the per-evaluation pipeline:

- **Sensor anomaly scan** — the `sensor_anomaly` scheduler job, every `ANOMALY_SCAN_INTERVAL_MINUTES` (15 min), over every sensor's last `ANOMALY_WINDOW_READINGS` (50) raw readings (needs ≥ `ANOMALY_MIN_READINGS`, 10). **Stale:** silent for more than `ANOMALY_STALE_INTERVAL_MULTIPLIER` (2×) its median report gap → `sensor_stale` warning. **Drift/spike:** the latest soil value's |z-score| against the window exceeds `ANOMALY_Z_THRESHOLD` (4.0), with the std floored at `ANOMALY_MIN_STD` (1.0%) so near-constant series don't false-alarm → `sensor_drift` warning. Runs on the *raw* series (see Sensor Data Cleaning). These alerts inform; they do not block actuation.
- **Leak / stuck-valve detector** — post-irrigation, below; its alert *is* a hold inside the engine.
- **Actuation gates** — at actuation time, above (device health `device_no_water` / `device_rain_detected` / `device_offline`, then `daily_cap_hit`, then `dry_run_global`).

### Leak / stuck-valve detector

Not part of the pre-decision scan: it is scheduled per irrigation, running `LEAK_CHECK_DELAY_SECONDS` (30 min) **after** a start event, and asks one question per sensor — *did the soil settle, or is water still arriving?* Readings come from the **cleaned view**, same as the engine.

It is scheduled after **every real start**:

| Start | When the check runs (and its observation window ends) |
|---|---|
| automatic | `LEAK_CHECK_DELAY_SECONDS` (30 min) after the start |
| forced (`force=true`, recorded as manual) | 30 min after the start, like an automatic run (the engine chose the duration) |
| manual with `minutes` | `max(LEAK_CHECK_DELAY_SECONDS, minutes × 60 + LEAK_CHECK_SETTLE_SECONDS)` — once the run has ended plus a 25-min settle, never sooner than 30 min |
| manual without `minutes` | not checked: the device timer decides how long the pump runs, so there is no safe time to judge the soil; a `leak_check_skipped` activity row says so |
| `log-manual` (watered by hand) | not checked: no pump ran |

A forced or manual start writes a `leak_check_armed` activity row (source `leak`, payload `started_at`, plus `check_after_seconds` when it is not the default), committed with its `start` event; an automatic `start` event is its own proof. Every completed check writes a `leak_check` activity row (payload `started_at`) — the durable "already checked" marker, committed with the check's own effects; the "already done" lookup scans those markers by time since the start, not by a row count. Scheduler jobs are in-memory, so on startup `rearm_leak_checks` re-schedules every automatic start and every `leak_check_armed` marker from the last `LEAK_HOLD_HOURS` (24h) whose check has no `leak_check` (or `leak_hold`) row: at its normal due time if still ahead, otherwise immediately. The job itself skips a start that is already marked, so a restart never produces a duplicate check or alert; a check that fails leaves no marker and is re-armed on the next start.

Three verdicts per sensor:

| Verdict | Condition | Effect |
|---|---|---|
| **Inconclusive** | fewer than `LEAK_MIN_AFTER_SAMPLES` (3) samples in the 30-min window, or fewer than `LEAK_MIN_BEFORE_SAMPLES` (2) in the `LEAK_BEFORE_WINDOW_SECONDS` (3h) baseline | nothing raised, nothing resolved |
| **Pinned** | the last `LEAK_PINNED_MIN_SAMPLES` (2) samples are all above `LEAK_PINNED_THRESHOLD` (95%) | critical alert + hold (needs no baseline) |
| **Never settled** | moisture is still at its peak when the window closes (within `LEAK_SETTLE_TOLERANCE`, 2pp), climbed across the window, **and** sits `LEAK_RISING_DELTA` (30pp) above the baseline median | critical alert + hold |

Anything else is **settled** — a successful dose — and *resolves* that sensor's open leak alert.

A successful irrigation legitimately clears a 30pp before/after delta, which is why the delta alone is never sufficient: the shape of the after-window (jump-then-plateau vs. never turns over) is what separates a working valve from a stuck one. Baseline windows are routinely empty because sensor rows land in SQLite at sync time (default every 3h) — an empty baseline means *unknown*, never 0%.

**The hold is the alert.** A confirmed finding raises one critical `leak_or_stuck_valve` alert per offending sensor; `IrrigationLogic._enforce_leak_hold` skips automatic irrigation for `LEAK_HOLD_HOURS` (24h) while that alert is unresolved, emitting the `leak_hold` terminal code into the decision trail and `decision_logs`. Two ways out:

- **resolve the alert** (`POST /api/v1/alerts/{id}/resolve`, or a later check finding the sensor settled) — releases the hold immediately;
- **wait it out** — the hold expires 24h after the last detection.

Acknowledging an alert does *not* release the hold, and `force=true` does not bypass it (a stuck valve is a hardware fault, like the device-health alarms). The deliberate escape hatch is the direct irrigator route, `POST /api/v1/irrigators/{id}/start`.

Per-day rate limits (`max_events_per_day`, `daily_cap_minutes`) are **not** part of the decision engine or trust layer: they are a server-side actuation gate (see Actuation gates). They resolve cluster → global, and bind every start: the automatic pipeline (`irrigate` incl. `force`, `check`, `check_all`) skips with `daily_cap_hit`; the manual start path (`POST /api/v1/irrigators/{id}/start`, the web "start" button, the TUI water-now dialog) and `log-manual` answer HTTP 409.

## Learning Engine

After ≥3 irrigation cycles with sensor data, the system learns:

- **Absorption rate:** +X%/min of irrigation per plant
- **Drainage rate:** -X%/hr natural moisture loss
- **Efficiency score:** how consistently irrigation increases moisture

Light-based heuristics (light-accelerated drainage, `low_light`) scale their lux thresholds by the seasonal light factor of the current month in the `timezone` preference — the same month the engine's light adjustment and seasonal multiplier use.

### Alert Types (learning)

| Alert | Severity | Trigger |
|---|---|---|
| Blocked drip | Critical | <0.5%/min absorption, <30% efficiency |
| Rapid drainage | Warning | >5%/hr moisture loss |
| Chronic underwatering | Warning | Peak moisture never reaches target (7d) |
| Unresolvable conflict | Critical | Irrigating dry plant would bring wet plant >85% |

### Alert Types (maintenance and anomaly)

| Alert | Trigger |
|---|---|
| `battery_low` | Sensor battery state is "low" (maintenance) |
| `stale_data` | No readings in the last 3h (`MAINTENANCE_STALE_SECONDS`; maintenance) |
| `low_env_humidity` | Ambient humidity below plant ideal − 10% (maintenance) |
| `low_light` | Daytime avg lux below seasonal plant minimum × 0.5 (maintenance) |
| `sensor_stale` | Silent for more than 2× its median report gap (anomaly scan) |
| `sensor_drift` | Latest soil value's \|z\| > 4 against its last 50 readings (anomaly scan) |
| `actuation_failed` | The pipeline's device start was refused or failed (warning) — e.g. the IK10PW's local Duration DP is unreachable |
| `check_failed` | The scheduled/`POST /check` run crashed for this cluster (error). Each cluster is checked and committed on its own, so one failure never rolls back another cluster's recorded pump runs; the next successful check resolves it |

### Alert inbox lifecycle

Alerts are deduplicated by a stable `dedup_key` (source + entity + code + plant). Status flows `open → acknowledged → resolved`. Each inbox entry tracks `first_seen_at`, `last_seen_at`, and `occurrence_count`. A resolved alert that is raised again re-opens and notifies again (if the alert notification category is on).

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

All engine thresholds live in `libs/greenhouse-core/greenhouse_core/constants.py` (project invariant #5); the services' tuning values moved there too, each named by purpose. The most useful ones when explaining a decision:

- Cooldown: 6h between irrigations (`MIN_COOLDOWN_HOURS`); leak hold 24h (`LEAK_HOLD_HOURS`), leak check 30 min after a start (`LEAK_CHECK_DELAY_SECONDS`), or after a longer manual run plus a 25-min settle (`LEAK_CHECK_SETTLE_SECONDS`)
- Soil moisture: critical 30%, low 40%, saturated 70% (`SOIL_MOISTURE_CRITICAL` / `_LOW` / `_SATURATED`); default target band 45–65 (`DEFAULT_SOIL_MOISTURE_MIN` / `_MAX`, `DEFAULT_SOIL_MOISTURE_TARGET = "45-65"`); conflict wet margin 5 (`CONFLICT_WET_MARGIN`), very-dry margin 10 (`VERY_DRY_MARGIN`)
- Stress detection: steep decline −10 pp (`STRESS_STEEP_DECLINE_DELTA`), heat +5 °C over the ideal max (`STRESS_HEAT_OFFSET_C`), humidity deficit 20 (`STRESS_HUMIDITY_DEFICIT`), low light 0.4 × seasonal minimum (`STRESS_LOW_LIGHT_FRACTION`)
- Duration: default 2 min, conflict 1 min, stress 3 min, max 5 min (`*_DURATION_MINUTES`)
- Intervals: min 6h, max 24h, default 12h, conflict 8h, stress 6h (`*_INTERVAL_HOURS`); per-rule interval/duration steps are the `TEMP_*`, `HUMIDITY_*`, `LIGHT_*`, `WATER_NEEDS_*`, `TREND_*` constants
- Weather skip: 6h forecast horizon, skip above 2.0 mm (`WEATHER_FORECAST_HOURS`, `WEATHER_SKIP_PRECIP_MM`)
- Confidence levels: `CONFIDENCE_*` (table above)
- Freshness, each with its own purpose: force a sync before deciding after 4h (`SENSOR_READING_STALE_SECONDS`); maintenance `stale_data` after 3h (`MAINTENANCE_STALE_SECONDS`); irrigator offline after 30 min without a live read (`OFFLINE_AFTER_MINUTES`); sensor offline after one sync interval + 30 min (`SENSOR_OFFLINE_GRACE_MINUTES`; 210 min with the default `DEFAULT_SYNC_INTERVAL_MINUTES` = 180), because sensor readings are persisted only by the sync; system-health page and data-quality report thresholds (`SYSTEM_HEALTH_*`, `DATA_QUALITY_STALE_SECONDS`)
- Sensor cleaning: physical ranges `SENSOR_PHYSICAL_RANGES`; Hampel spike filter `CLEANING_HAMPEL_WINDOW_RADIUS = 3`, `CLEANING_HAMPEL_N_SIGMA = 3.0`, `CLEANING_HAMPEL_MIN_READINGS = 5`, `CLEANING_MAD_SCALE = 1.4826`, `CLEANING_MAD_FLOOR = 1.0`
- Anomaly scan: `ANOMALY_*` (15-min job, 50-reading window, z > 4, std floor 1.0, stale at 2× the median gap)
- Service read windows and scan limits (no decision effect): anomaly scan loads 72h per sensor (`ANOMALY_LOOKBACK_HOURS`); the pipeline's one-sensor freshness sync pulls 6h (`FRESHNESS_SYNC_BACKFILL_HOURS`); per-day caps count the last 24h (`DAILY_CAP_WINDOW_HOURS`, manual and automatic starts); the sync's `getdevicelog` backfill pages 100 entries at a time up to 50 pages per sensor (`SENSOR_LOG_PAGE_SIZE`, `SENSOR_LOG_MAX_PAGES`); the forecast falls back to −2.0 %/h without a learned profile (`FORECAST_FALLBACK_DRAINAGE_PER_HOUR`); alert sync / auto-resolve scan the newest 200 alerts (`ALERT_SCAN_LIMIT`); efficacy scores 5 points per pp of soil rise (`EFFICACY_SCORE_PER_PCT_RISE`); the `/monitor` cleans over `SNAPSHOT_LOOKBACK_HOURS` (24h) before its 2h slice (`MONITOR_LOOKBACK_HOURS`); status and plant-page windows are `STATUS_*` / `PLANT_PAGE_*`
- Seasonal multipliers: indoor {winter 0.5, spring 1.0, summer 1.2, autumn 0.8}, outdoor {0.3, 1.0, 1.5, 0.7} (`DEFAULT_SEASON_MULTIPLIER_INDOOR` / `_OUTDOOR`)
- Quiet hours: **no constant** — the baseline Alembic migration seeds the global row with 00:00–05:00 local; an unconfigured database has quiet hours off
- Hierarchical config built-ins: `DEFAULT_IRRIGATION_MODE = "smart"`, `DEFAULT_AUTO_RUN = True`
- Vacation rationing: `VACATION_RESERVOIR_USABLE_FRACTION = 0.95` (reserve 5% so the pump never runs dry), `VACATION_MIN_RUN_MINUTES = 1` (below this, skip instead of a token dribble)

## Known quirks (pinned by tests, not fixed)

These are current behavior, recorded during the refactor and pinned by characterization tests. Explain them rather than assuming a malfunction:

<!-- TODO(D): package D fixes every item in this list (critical stress on the driest plant, emergency paths obey windows and vacation rationing, NO_ACTION_NEEDED primary code, short-series spike guard, Hampel step sample, exclusive edges, light thresholds by preference-tz month); replace the list with what remains and update the pipeline and constants sections (CLEANING_SHORT_SERIES_MIN_READINGS, environment_for, parse_moisture_target validation, 0 °C / 0 % real values, B-14 applied interval delta, B-24 global schedule fallback). -->

- **Critical stress keys on the average.** `water_stress` / `over_watering` compare the cluster's **average** soil moisture, while the soil-moisture rule uses the driest sensor (invariant #2). A multi-plant cluster with one very dry plant can therefore get a normal `sensor_dry` / `sensor_very_dry` run rather than a `water_stress` one.
- **Stress, water warning and the no-sensor fallback bypass vacation rationing** (they end before the final step).
- **A decision can end with no reasons** (e.g. temperature-only data that triggers no rule): `skip`, confidence 0.5, `primary_code` empty in the log.
- **Fewer than 5 readings are not spike-filtered**, so a lone glitch can trigger a `sensor_very_dry` run on a new sensor. The spike filter also drops the first sample of a genuine step change.
- **Cooldown (6h) and leak hold (24h) are inclusive** at the exact edge.

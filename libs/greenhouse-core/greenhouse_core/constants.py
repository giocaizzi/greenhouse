"""Project-wide constants and thresholds."""

# ── Irrigation Cooldown ──────────────────────────────────────────────────────

MIN_COOLDOWN_HOURS = 6

# ── Vacation Rationing — reservoir burn-down envelope ─────────────────────────
# When a vacation window is active and an irrigator has reservoir/flow capacity
# configured, the engine rations each cycle against a daily budget so the tank
# lasts the whole trip (see logic/engine.py:_apply_vacation_budget).
VACATION_RESERVOIR_USABLE_FRACTION = 0.95  # reserve 5% so the pump never runs dry
VACATION_MIN_RUN_MINUTES = 1  # below this, skip instead of a token dribble

# ── Quiet Hours — hard gate against actuation during user-defined windows ────
# No built-in fallback here: the baseline Alembic migration seeds the global row
# with 00:00-05:00 local time (indoor pumps are noisy at night), and an
# unconfigured database has quiet hours off. Start/end are integers 0-23,
# end-exclusive, wrap-around supported. A row with start == end means
# "explicitly disabled at this level" (e.g. an outdoor cluster that should be
# allowed to run overnight).

# ── Irrigation Config — hierarchical defaults ────────────────────────────────
# Built-in fallbacks for fields that resolve cluster → global → here.
DEFAULT_IRRIGATION_MODE = "smart"
DEFAULT_AUTO_RUN = True

# ── Soil Moisture Defaults (when plant-specific data unavailable) ────────────

DEFAULT_SOIL_MOISTURE_MIN = 45.0
DEFAULT_SOIL_MOISTURE_MAX = 65.0

# ── Confidence Scores ────────────────────────────────────────────────────────
# Higher = more confident in the decision.

CONFIDENCE_CRITICAL_STRESS = 0.95
CONFIDENCE_WATER_WARNING = 0.92
CONFIDENCE_COOLDOWN = 0.9
CONFIDENCE_OVER_WATERING = 0.9
CONFIDENCE_SENSOR_VERY_DRY = 0.9
CONFIDENCE_SENSOR_DRY = 0.8
CONFIDENCE_SENSOR_WET = 0.8
CONFIDENCE_SENSOR_ADEQUATE = 0.7
CONFIDENCE_CONFLICT = 0.65
CONFIDENCE_TEMP_FALLBACK = 0.6
CONFIDENCE_CONFIG_FALLBACK = 0.3
CONFIDENCE_NO_DATA = 0.2

# ── Default Irrigation Durations (minutes) ───────────────────────────────────

DEFAULT_DURATION_MINUTES = 2
CONFLICT_DURATION_MINUTES = 1
STRESS_DURATION_MINUTES = 3
MAX_DURATION_MINUTES = 5

# ── Default Intervals (hours) ────────────────────────────────────────────────

MIN_INTERVAL_HOURS = 6
MAX_INTERVAL_HOURS = 24
DEFAULT_INTERVAL_HOURS = 12
CONFLICT_INTERVAL_HOURS = 8
STRESS_INTERVAL_HOURS = 6

# ── Temperature Thresholds (Celsius) — for fallback logic ────────────────────

TEMP_COLD = 18
TEMP_WARM = 24
TEMP_HOT = 28

# ── Engine adjustment tuning — interval/duration deltas per rule ──────────────
# Named knobs for the sensor-driven adjustment rules in logic/engine.py. Kept
# here so the engine carries no bare magic numbers (invariant #5). All interval
# values are hours, durations are minutes; the engine clamps to
# [MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS] / MAX_DURATION_MINUTES afterwards.

# Soil-moisture rule — conflict + very-dry banding (percent moisture).
# A "wet" sensor is one within CONFLICT_WET_MARGIN of its target max; a conflict
# is the driest sensor below target_min while the wettest is still in that wet
# band. The margin must be narrow enough that the wet band does NOT overlap the
# healthy range: with defaults 45-65 and margin 5, "wet" means >60, so a normal
# spread like driest 44 / wettest 56 is treated as ordinarily dry (driest drives
# the call), not an unresolvable conflict that forces a short burst.
CONFLICT_WET_MARGIN = 5  # % below target_max that still counts as "wet"
VERY_DRY_MARGIN = 10  # % below target_min that escalates to SENSOR_VERY_DRY

# Temperature adjustment — band offsets and interval steps.
TEMP_ADJUST_OFFSET = 3  # °C above/below the ideal band before adjusting
TEMP_HIGH_INTERVAL_STEP = 4  # shorten interval (hot)
TEMP_LOW_INTERVAL_STEP = 6  # lengthen interval (cold)

# Humidity adjustment — band offsets and interval steps.
HUMIDITY_VERY_LOW_OFFSET = 20  # % below ideal min → "very dry air"
HUMIDITY_LOW_OFFSET = 5  # % below ideal min → "dry air"
HUMIDITY_HIGH_OFFSET = 10  # % above ideal max → "high humidity"
HUMIDITY_VERY_LOW_INTERVAL_STEP = 3
HUMIDITY_LOW_INTERVAL_STEP = 1
HUMIDITY_HIGH_INTERVAL_STEP = 2

# Light adjustment — interval/duration steps (thresholds are LIGHT_* below).
LIGHT_VERY_BRIGHT_INTERVAL_STEP = 2
LIGHT_VERY_BRIGHT_DURATION_STEP = 1
LIGHT_BRIGHT_INTERVAL_STEP = 1
LIGHT_VERY_DARK_INTERVAL_STEP = 4
LIGHT_DARK_INTERVAL_STEP = 2

# Water-needs adjustment — duration/interval steps by plant water demand.
WATER_NEEDS_DURATION_STEP = 1
WATER_NEEDS_HIGH_INTERVAL_STEP = 2  # shorten (high demand)
WATER_NEEDS_LOW_INTERVAL_STEP = 4  # lengthen (low demand)

# Trend adjustment — moisture/temperature trend steps and the hot-temp gate.
TREND_MOISTURE_INTERVAL_STEP = 2  # declining shortens / rising lengthens
TREND_TEMP_RISING_HOT_C = 25  # only boost on a rising trend above this temp
TREND_TEMP_RISING_INTERVAL_STEP = 2
TREND_UNDERWATERING_DURATION_STEP = 1

# ── Soil Moisture Thresholds ─────────────────────────────────────────────────

SOIL_MOISTURE_CRITICAL = 30
SOIL_MOISTURE_LOW = 40
SOIL_MOISTURE_SATURATED = 70

# ── Sensor Data Cleaning ─────────────────────────────────────────────────────
# Raw readings are noisy: capacitive soil probes glitch to single-sample spikes,
# comms errors inject out-of-range values, and a wedged probe reports a flat run.
# The decision snapshot and trend analysis consume a *cleaned view*
# (logic/cleaning.py) so "the driest plant drives the call" (invariant #2) is not
# tripped by a lone bad sample. Raw rows in sensor_readings are never mutated.

# Physical plausibility bounds per metric (inclusive); values outside are dropped
# as dirty. 0.0 is in-range for soil_moisture/humidity — a genuine bone-dry probe
# must survive the gate (the Hampel filter still rejects a lone 0 among healthy
# readings as a spike).
SENSOR_PHYSICAL_RANGES = {
    "temperature": (-40.0, 80.0),  # °C — beyond any greenhouse/outdoor reality
    "soil_moisture": (0.0, 100.0),  # % volumetric
    "env_humidity": (0.0, 100.0),  # % relative
    "light": (0.0, 200_000.0),  # lux — direct midday sun tops out ~120k
}

# Hampel spike filter (rolling median ± n·MAD) — the robust time-series outlier
# test. Window radius 3 → 7 samples; on hourly Tuya logs that's a ~7h context,
# wide enough to ride out a slow dry-down yet reject a single revert spike.
CLEANING_HAMPEL_WINDOW_RADIUS = 3  # window = 2*radius + 1 samples
CLEANING_HAMPEL_N_SIGMA = 3.0  # deviation beyond this many scaled MADs is a spike
CLEANING_HAMPEL_MIN_READINGS = 5  # fewer points than this: too short to judge spikes
CLEANING_MAD_SCALE = 1.4826  # MAD→sigma consistency factor for Gaussian noise
CLEANING_MAD_FLOOR = 1.0  # min sigma (%) so a flat run isn't hyper-sensitive to change

# ── Leak / stuck-valve detection ─────────────────────────────────────────────
# A post-irrigation sanity check: 30 min after a start event the detector asks
# "did the soil settle, or is water still arriving?". Getting this wrong in the
# alarming direction is expensive — it fires a critical alert AND holds the
# cluster (see LEAK_HOLD_HOURS) — so every rule below is written to stay silent
# on thin data rather than guess (issue #103).
LEAK_ALERT_CODE = "leak_or_stuck_valve"
LEAK_CHECK_DELAY_SECONDS = 1800  # run the check 30 min after the start event

# Baseline window BEFORE the start event. Sized well above the sync cadence
# (IRRIGATION_SYNC_INTERVAL_MINUTES, default 3h) because sensor rows land in
# SQLite at sync time, not at reading time: a 10-minute window is empty on most
# cycles, and an empty baseline must never be read as "soil was at 0%".
LEAK_BEFORE_WINDOW_SECONDS = 10800  # 3h — still inside the 6h cooldown
LEAK_AFTER_WINDOW_SECONDS = 1800  # observation window after the start event

# Sample floors. Below these the verdict is "not enough data" — no alert, no
# hold. The rising test needs a real baseline AND enough after-samples to see a
# shape (a jump that settles vs. a climb that never turns over).
LEAK_MIN_BEFORE_SAMPLES = 2
LEAK_MIN_AFTER_SAMPLES = 3
LEAK_PINNED_MIN_SAMPLES = 2  # consecutive pinned samples before we believe it

LEAK_PINNED_THRESHOLD = 95.0  # % soil moisture that counts as "pinned high"
# Rise over baseline that is too much to be this cycle's dose. Only consulted
# together with the never-settled shape test — a successful irrigation legitimately
# clears this delta, which is exactly why the old delta-only rule cried wolf.
LEAK_RISING_DELTA = 30.0
# Slack (percentage points) when judging "still climbing at the end of the
# window": absorbs probe jitter around a genuine plateau.
LEAK_SETTLE_TOLERANCE = 2.0

# How long a confirmed leak holds the cluster's automatic irrigation. The hold
# is derived from the open alert (lifted by resolving it) and expires on its own
# this long after the last detection.
LEAK_HOLD_HOURS = 24

# ── Trend Analysis ───────────────────────────────────────────────────────────

TREND_MOISTURE_THRESHOLD = 5  # % delta for rising/declining
TREND_TEMP_THRESHOLD = 2  # °C delta for rising/falling
TREND_MIN_READINGS = 4  # Minimum readings for trend analysis

# ── Device Health Monitor ────────────────────────────────────────────────────
# Battery thresholds + offline / signal cut-offs consumed by DeviceHealthMonitor
# (adapters report raw percent and last-seen-ts; the monitor decides what
# counts as "low" so thresholds stay tunable without touching device code).
BATTERY_LOW_PCT = 20
BATTERY_CRITICAL_PCT = 5
OFFLINE_AFTER_MINUTES = 30
SIGNAL_LOSS_THRESHOLD = 30  # 0-100 link quality
HEALTH_POLL_IDLE_MINUTES = 5
SENSOR_HEALTH_BACKFILL_WINDOW = 5  # consecutive readings

# ── Read-model freshness contract ────────────────────────────────────────────
# The sync job is the sole Cloud writer of sensor_readings; every other consumer
# reads the latest persisted row. If the reading feeding an actuation decision
# is older than this, the pipeline forces ONE targeted sync (per stale sensor)
# before deciding — never N redundant live reads. Sized above the default sync
# cadence + a typical sensor report gap so a healthy feed never trips it, well
# inside the 6h actuation cooldown.
SENSOR_READING_STALE_SECONDS = 14400  # 4h

# ── Open-Meteo Defaults (can be overridden via env vars) ─────────────────────

DEFAULT_LATITUDE = 45.464  # Milan
DEFAULT_LONGITUDE = 9.189

# ── Learning Engine ──────────────────────────────────────────────────────────

LEARNING_MIN_EVENTS = 3
LEARNING_MIN_EFFICIENCY = 0.3
LEARNING_MIN_ABSORPTION_PER_MIN = 0.5
LEARNING_RAPID_DRAINAGE_THRESHOLD = -5  # %/hr
LEARNING_OVER_WATER_THRESHOLD = 85  # % moisture

# ── Light Thresholds (lux, before seasonal scaling) ──────────────────────────

LIGHT_VERY_BRIGHT = 1500
LIGHT_BRIGHT = 800
LIGHT_DARK = 150
LIGHT_VERY_DARK = 50

# ── Irrigation timing — preferred windows + seasonal multipliers ─────────────
# Defaults applied when neither a per-cluster IrrigationWindow nor a per-species
# / per-category override is present. Hours are local-time integers 0-23,
# end-exclusive. The biology evidence behind these numbers lives in the team
# audit report (Webb 2003 / PMC8997731 / extension service guidance): water
# in the morning so foliage dries before nightfall and the root zone is moist
# before peak transpiration.
DEFAULT_PREFERRED_WATER_HOURS = (6, 10)
# Indoor cluster — heated/cooled, photoperiod near-constant. Halve in winter
# (dormancy + low light), +20% in summer (peak transpiration), 0.8x autumn.
DEFAULT_SEASON_MULTIPLIER_INDOOR = {
    "winter": 0.5,
    "spring": 1.0,
    "summer": 1.2,
    "autumn": 0.8,
}
# Outdoor cluster — driven by temperature + photoperiod. Big summer ramp for
# fruit trees / vegetables, true winter dormancy in temperate zones.
DEFAULT_SEASON_MULTIPLIER_OUTDOOR = {
    "winter": 0.3,
    "spring": 1.0,
    "summer": 1.5,
    "autumn": 0.7,
}

# ── Time units ───────────────────────────────────────────────────────────────

SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400

# ── Decision engine — lookbacks, baseline and weather skip ───────────────────

SNAPSHOT_LOOKBACK_HOURS = 24  # sensor history feeding the decision snapshot
CONFIDENCE_BASELINE = 0.5  # starting confidence before any rule fires
WEATHER_FORECAST_HOURS = 6  # rain-forecast horizon consulted before irrigating
WEATHER_SKIP_PRECIP_MM = 2.0  # forecast precipitation at or below this does not skip
DEFAULT_SOIL_MOISTURE_TARGET = "45-65"  # plant care fallback when no target is known

# ── Stress detection ─────────────────────────────────────────────────────────

STRESS_HUMIDITY_DEFICIT = 20  # % below the ideal humidity minimum
STRESS_LOW_LIGHT_FRACTION = 0.4  # fraction of the seasonal light minimum
STRESS_STEEP_DECLINE_DELTA = -10  # soil-moisture delta that counts as a steep decline
STRESS_HEAT_OFFSET_C = 5  # °C above the ideal temperature maximum

# ── Trend analysis — lookback and irrigation cadence ─────────────────────────

TREND_LOOKBACK_HOURS = 48
CADENCE_WINDOW_DAYS = 7
CADENCE_LOW_EVENTS_PER_DAY = 1
CADENCE_LOW_AVG_MINUTES = 2
CADENCE_HIGH_EVENTS_PER_DAY = 3

# ── Temperature fallback — interval steps by plant water needs ───────────────

FALLBACK_HIGH_NEEDS_INTERVAL_STEP = 4  # shorten (high demand)
FALLBACK_LOW_NEEDS_INTERVAL_STEP = 6  # lengthen (low demand)

# ── Daylight ─────────────────────────────────────────────────────────────────

NIGHT_LUX_THRESHOLD = 15  # readings at or below this lux are treated as night
# Seasonal light factor per calendar month (Northern-hemisphere daylight curve).
SEASONAL_LIGHT_FACTOR_BY_MONTH: dict[int, float] = {
    1: 0.50,
    2: 0.60,
    3: 0.72,
    4: 0.85,
    5: 0.95,
    6: 1.00,
    7: 1.00,
    8: 0.95,
    9: 0.83,
    10: 0.70,
    11: 0.58,
    12: 0.50,
}

# ── Learning issue / conflict detection ──────────────────────────────────────

LEARNING_CONFLICT_LOOKBACK_HOURS = 6
LEARNING_DRAINAGE_LUX_LOOKBACK_HOURS = 48
LEARNING_WEEK_HOURS = 168
LEARNING_LATEST_SAMPLES = 3  # latest readings averaged per sensor
LEARNING_CHRONIC_MIN_RESPONSES = 5
LEARNING_CONFLICT_DRY_MARGIN = 5  # % below target minimum
LEARNING_MIN_ENV_SAMPLES = 5
LEARNING_HUMIDITY_DEFICIT = 15  # % below the ideal humidity minimum
LOW_LIGHT_ALERT_FRACTION = 0.5  # fraction of the seasonal light minimum

# ── Maintenance alerts ───────────────────────────────────────────────────────

MAINTENANCE_LOOKBACK_HOURS = 24
MAINTENANCE_STALE_SECONDS = 3 * SECONDS_PER_HOUR
MAINTENANCE_MIN_SAMPLES = 3
MAINTENANCE_HUMIDITY_DEFICIT = 10  # % below the ideal humidity minimum

# ── Irrigation pipeline — monitor bands and fallbacks ────────────────────────

MONITOR_LOOKBACK_HOURS = 2
MONITOR_VERY_DRY_MARGIN = 15  # % below target minimum
MONITOR_WET_MARGIN = 10  # % above target maximum
FALLBACK_TEMPERATURE_C = 20.0  # used when no temperature source is available
LEAK_CHECK_ACTIVITY_SCAN_LIMIT = 500

# ── Pump watcher defaults ────────────────────────────────────────────────────

PUMP_WATCHER_POLL_SECONDS = 2.0
PUMP_WATCHER_WARMUP_SECONDS = 5.0
PUMP_WATCHER_MAX_READ_FAILURES = 5

# ── Scheduler jobs ───────────────────────────────────────────────────────────

ANOMALY_SCAN_INTERVAL_MINUTES = 15
HEALTH_SNAPSHOT_HOUR = 0
HEALTH_SNAPSHOT_MINUTE = 30
SYNC_JOB_BACKFILL_HOURS = 6
SENSOR_HEALTH_BACKFILL_HOURS = 24 * 7

# ── Forecast and plant health scoring ────────────────────────────────────────

FORECAST_CONFIDENCE_HIGH = 0.7
FORECAST_CONFIDENCE_MEDIUM = 0.4
FORECAST_CONFIDENCE_LOW = 0.2
FORECAST_HIGH_CONFIDENCE_PROFILES = 3  # profiled sensors needed for high confidence
HEALTH_SCORE_WINDOW_DAYS = 14

# ── Irrigation windows ───────────────────────────────────────────────────────

WINDOW_HOUR_MAX = 23
FULL_WEEKDAY_MASK = 127  # Monday..Sunday bits all set

# ── Irrigation response windows (learning profiles, efficacy) ────────────────
# Sensor readings around a start event that show how the soil answered.

RESPONSE_PRE_WINDOW_SECONDS = 1800  # baseline: 30 min before the start event
RESPONSE_POST_WINDOW_SECONDS = 7200  # learning: 2 h after (water needs time to soak)
RESPONSE_MIN_POST_DELAY_SECONDS = 600  # ignore readings < 10 min after (water still distributing)
EFFICACY_AFTER_WINDOW_SECONDS = 5400  # efficacy scoring: 90 min after the start event

# ── Anomaly scan ─────────────────────────────────────────────────────────────

ANOMALY_MIN_READINGS = 10  # readings needed before a sensor is scanned
ANOMALY_WINDOW_READINGS = 50  # most recent readings the z-score baseline uses
ANOMALY_Z_THRESHOLD = 4.0
ANOMALY_STALE_INTERVAL_MULTIPLIER = 2.0  # stale when silent > this x the median report gap
# Minimum std to use for z-score; prevents false alarms on near-constant series
# while still catching large absolute deviations (e.g. 95% vs 50% baseline).
ANOMALY_MIN_STD = 1.0

# ── Freshness thresholds by purpose ──────────────────────────────────────────
# Distinct on purpose: each answers a different question about "how old is too old".
# (SENSOR_READING_STALE_SECONDS: force a sync before deciding; MAINTENANCE_STALE_SECONDS:
# maintenance alert; OFFLINE_AFTER_MINUTES: device health.)

SYSTEM_HEALTH_FRESH_SECONDS = SECONDS_PER_HOUR  # system page: Cloud reachable if a reading is newer
SYSTEM_HEALTH_STALE_SECONDS = 3 * SECONDS_PER_HOUR  # system page: device counts as stale
SYSTEM_HEALTH_COLD_SECONDS = 24 * SECONDS_PER_HOUR  # system page: device counts as cold
SYSTEM_HEALTH_DEVICE_LIMIT = 20  # devices listed on the system page
DATA_QUALITY_STALE_SECONDS = 24 * SECONDS_PER_HOUR  # data-quality report: sensor is stale
AGE_BADGE_STALE_SECONDS = 7 * SECONDS_PER_DAY  # web relative time renders "stale" past this age
WEATHER_FORECAST_CACHE_TTL_SECONDS = 600  # Open-Meteo forecast cache lifetime

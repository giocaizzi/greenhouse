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
# Default applies to every cluster that hasn't overridden. Indoor irrigators
# tend to make pump noise at night, so the baseline blocks 00:00–05:00 local
# time. Start/end are integers 0–23, end-exclusive, wrap-around supported.
# A row with start == end means "explicitly disabled at this level" (e.g. an
# outdoor cluster that should be allowed to run overnight).
DEFAULT_QUIET_START_HOUR = 0
DEFAULT_QUIET_END_HOUR = 5

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
# healthy range: with defaults 45–65 and margin 5, "wet" means >60, so a normal
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
# / per-category override is present. Hours are local-time integers 0–23,
# end-exclusive. The biology evidence behind these numbers lives in the team
# audit report (Webb 2003 / PMC8997731 / extension service guidance): water
# in the morning so foliage dries before nightfall and the root zone is moist
# before peak transpiration.
DEFAULT_PREFERRED_WATER_HOURS = (6, 10)
# Indoor cluster — heated/cooled, photoperiod near-constant. Halve in winter
# (dormancy + low light), +20% in summer (peak transpiration), 0.8× autumn.
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

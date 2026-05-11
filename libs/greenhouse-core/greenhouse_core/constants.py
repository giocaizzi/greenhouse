"""Project-wide constants and thresholds."""

# ── Irrigation Cooldown ──────────────────────────────────────────────────────

MIN_COOLDOWN_HOURS = 6

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

# ── Soil Moisture Thresholds ─────────────────────────────────────────────────

SOIL_MOISTURE_CRITICAL = 30
SOIL_MOISTURE_LOW = 40
SOIL_MOISTURE_SATURATED = 70

# ── Trend Analysis ───────────────────────────────────────────────────────────

TREND_MOISTURE_THRESHOLD = 5  # % delta for rising/declining
TREND_TEMP_THRESHOLD = 2  # °C delta for rising/falling
TREND_MIN_READINGS = 4  # Minimum readings for trend analysis

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

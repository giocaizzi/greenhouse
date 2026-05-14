"""Server configuration via Pydantic BaseSettings."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IRRIGATION_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    db_url: str = "sqlite:///data/irrigation.db"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    plant_db_path: str | None = None

    # Weather API
    weather_lat: float = 45.464
    weather_lon: float = 9.189

    # Scheduler defaults
    sync_interval_minutes: int = 30
    # Cron `hour` field for the check_all job (e.g. "*", "0,6,12,18").
    # Cadence ≠ irrigation cadence — engine cooldown
    # (`MIN_COOLDOWN_HOURS` in `greenhouse_core.constants`) gates actuation;
    # the scheduler decides how often to observe.
    check_cron_hours: str = "*"
    # Deprecated alias for the old interval-trigger config. When set and
    # `check_cron_hours` is at its default, it is translated to `*/N` on
    # startup with a one-time warning. Slated for removal — prefer
    # IRRIGATION_CHECK_CRON_HOURS.
    check_interval_hours: int | None = None
    enable_scheduler: bool = True

    # MCP bearer token (fail-closed: unset -> /mcp returns 503).
    # Lives outside the IRRIGATION_ prefix to match the public deployment contract.
    mcp_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GREENHOUSE_MCP_TOKEN", "mcp_token"),
    )

    # Pump dry-run watcher. During every active irrigation a background job
    # polls DP 105 (water-shortage alarm) over the local Tuya protocol and
    # immediately stops the pump on the first positive reading. Disable only
    # if your hardware doesn't expose the flag.
    pump_watcher_enabled: bool = True
    # Seconds between alarm polls. Lower = faster detection, more local
    # network traffic. The firmware itself debounces dry-run detection over
    # several seconds, so going below ~1s gains little.
    pump_watcher_poll_seconds: float = 2.0
    # Grace window at the start of each irrigation before the watcher will
    # trip. Avoids false positives during pump prime / initial suction.
    pump_watcher_warmup_seconds: float = 5.0
    # Cap on consecutive local-read failures tolerated before the watcher
    # gives up and exits (logging a warning). Does NOT stop the pump — a
    # broken local socket is not by itself evidence of a dry pump.
    pump_watcher_max_read_failures: int = 5

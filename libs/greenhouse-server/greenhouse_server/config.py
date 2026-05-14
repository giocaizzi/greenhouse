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

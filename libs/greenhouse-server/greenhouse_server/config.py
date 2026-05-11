"""Server configuration via Pydantic BaseSettings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IRRIGATION_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
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
    check_interval_hours: int = 6
    enable_scheduler: bool = True

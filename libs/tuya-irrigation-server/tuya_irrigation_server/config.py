"""Server configuration via Pydantic BaseSettings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IRRIGATION_", env_file=".env", case_sensitive=False)

    db_url: str = "sqlite:///data/irrigation.db"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    plant_db_path: str | None = None

    # Scheduler defaults
    sync_interval_minutes: int = 30
    check_interval_hours: int = 6

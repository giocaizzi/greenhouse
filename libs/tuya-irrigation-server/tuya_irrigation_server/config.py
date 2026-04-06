"""Server configuration via Pydantic BaseSettings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IRRIGATION_", case_sensitive=False)

    db_url: str = "sqlite:///data/irrigation.db"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Scheduler defaults
    sync_interval_minutes: int = 30
    check_interval_hours: int = 6

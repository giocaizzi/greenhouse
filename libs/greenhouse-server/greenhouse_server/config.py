"""Server configuration via Pydantic BaseSettings."""

from collections.abc import Iterable
from typing import Any, cast
from urllib.parse import urlsplit

from apscheduler.triggers.cron import CronTrigger
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from greenhouse_core.constants import (
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    PUMP_WATCHER_MAX_READ_FAILURES,
    PUMP_WATCHER_POLL_SECONDS,
    PUMP_WATCHER_WARMUP_SECONDS,
)

# The only URL schemes the ntfy client may publish to. urllib would happily
# open ``file:`` / ``ftp:`` / custom-handler URLs (ruff S310), so anything else
# is refused when the setting is loaded.
NTFY_URL_SCHEMES = ("http", "https")


def check_ntfy_server_url(value: str) -> str:
    """Return ``value`` if it is an absolute http(s) URL with a host, else raise ``ValueError``.

    Shared by the settings validator (fail fast at startup, naming the env var)
    and ``NtfyClient`` (never build a publisher for another scheme).
    """
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
    except ValueError:  # e.g. an unbalanced IPv6 bracket
        parts, hostname = None, None
    if parts is None or parts.scheme.lower() not in NTFY_URL_SCHEMES or not hostname:
        msg = f"GREENHOUSE_NTFY_SERVER_URL must be an http:// or https:// URL with a host, got {_redact(value)!r}"
        raise ValueError(msg)
    return value


def _redact(url: str) -> str:
    """``url`` with any ``user:password@`` replaced by ``***@`` — error messages never echo credentials."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        scheme, rest = "", url
    authority, slash, tail = rest.partition("/")
    if "@" in authority:
        authority = "***@" + authority.rsplit("@", 1)[1]
    return f"{scheme}{sep}{authority}{slash}{tail}"


class Settings(BaseSettings):
    """Server settings from the environment (``IRRIGATION_*``; secrets only from their ``GREENHOUSE_*`` names)."""

    model_config = SettingsConfigDict(
        env_prefix="IRRIGATION_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        # Validation errors must not echo raw input: these settings carry secrets.
        hide_input_in_errors=True,
        # Off on purpose: with it on, pydantic-settings also reads every aliased
        # secret from ``IRRIGATION_<FIELD>``. Constructor kwargs still accept the
        # field name — see ``__init__``.
        populate_by_name=False,
    )

    def __init__(self, **values: object) -> None:
        """Accept the field name as a kwarg for the aliased secrets too.

        The secrets below are read from the environment as ``GREENHOUSE_<NAME>``
        only (their single validation alias). ``populate_by_name`` stays off so
        neither the bare ``<NAME>`` nor ``IRRIGATION_<NAME>`` env var configures
        them, so a kwarg spelled with the field name (``Settings(mcp_token=…)``)
        is mapped onto the alias here instead.
        """
        for name, info in type(self).model_fields.items():
            alias = info.validation_alias
            if isinstance(alias, AliasChoices) and name in values:
                values[str(alias.choices[0])] = values.pop(name)
        # Field values of any type: pydantic validates them; the base signature types only its own options.
        super().__init__(**cast("dict[str, Any]", values))

    db_url: str = "sqlite:///data/irrigation.db"
    # Loopback by default: /mcp and the API grant physical actuation, so a host
    # install must opt in to listening on the network. The Docker image sets
    # IRRIGATION_HOST=0.0.0.0 (the container must listen for the published port).
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False
    plant_db_path: str | None = None

    # Weather API
    weather_lat: float = DEFAULT_LATITUDE
    weather_lon: float = DEFAULT_LONGITUDE

    # Scheduler defaults
    # Sync is the sole Cloud reader of sensor data. Sensors push their own
    # readings to Tuya; one getdevicelog per interval backfills the whole
    # window at full granularity, so a multi-hour cadence loses no data — only
    # dashboard freshness. Default 3h sits well inside the 6h actuation cooldown.
    sync_interval_minutes: int = 180
    # Cron `hour` field for the check_all job (e.g. "*", "0,6,12,18").
    # Cadence ≠ irrigation cadence — engine cooldown
    # (`MIN_COOLDOWN_HOURS` in `greenhouse_core.constants`) gates actuation;
    # the scheduler decides how often to observe.
    check_cron_hours: str = "*"
    enable_scheduler: bool = True

    # ── Scheduling validation: fail fast at startup, naming the env var ─────

    @field_validator("sync_interval_minutes")
    @classmethod
    def _validate_sync_interval(cls, value: int) -> int:
        if value <= 0:
            msg = f"IRRIGATION_SYNC_INTERVAL_MINUTES must be a positive number of minutes, got {value}"
            raise ValueError(msg)
        return value

    @field_validator("check_cron_hours")
    @classmethod
    def _validate_check_cron_hours(cls, value: str) -> str:
        # Parse with APScheduler's own cron `hour` field so anything accepted
        # here is exactly what the scheduler will accept at registration.
        try:
            CronTrigger(hour=value, minute=0)
        except (ValueError, TypeError) as exc:
            msg = (
                f"IRRIGATION_CHECK_CRON_HOURS={value!r} is not a valid cron hour expression "
                f"(e.g. '*', '0,6,12,18', '*/3', '6-20/2'): {exc}"
            )
            raise ValueError(msg) from None
        return value

    # MCP bearer token (fail-closed: unset -> /mcp returns 503).
    # Lives outside the IRRIGATION_ prefix to match the public deployment contract:
    # GREENHOUSE_MCP_TOKEN is the only env name read (as for every aliased secret).
    mcp_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GREENHOUSE_MCP_TOKEN"),
    )

    @field_validator("mcp_token")
    @classmethod
    def _blank_mcp_token_is_unset(cls, value: str | None) -> str | None:
        # A blank or whitespace-only token is a missing token: /mcp must fail closed (503),
        # not answer 401 to a credential nobody can send (bearer values are stripped).
        return value if value and value.strip() else None

    # ── ntfy.sh push notifications ──────────────────────────────────────────
    # Outbound push for manual/emergency/auto irrigations and new alerts.
    # Feature is enabled only when BOTH server_url and topic are set (mirrors
    # the mcp_token fail-closed pattern). Token is optional — public topics
    # need none. Lives outside the IRRIGATION_ prefix to match the
    # deployment-secret convention.
    ntfy_server_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GREENHOUSE_NTFY_SERVER_URL"),
    )

    @field_validator("ntfy_server_url")
    @classmethod
    def _validate_ntfy_server_url(cls, value: str | None) -> str | None:
        # Empty means "not configured" (the notifier stays off), like unset.
        return check_ntfy_server_url(value) if value else value

    ntfy_topic: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GREENHOUSE_NTFY_TOPIC"),
    )
    ntfy_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GREENHOUSE_NTFY_TOKEN"),
    )

    # ── Authentication ──────────────────────────────────────────────────────
    # When True (default) every /api/v1 route and the web UI require a valid
    # session. The login endpoint issues a JWT signed with `auth_secret_key`.
    # MCP keeps its own bearer token (`mcp_token`) — this auth covers humans
    # and CLI clients, MCP covers agent-driven actuation.
    auth_enabled: bool = True
    # HS256 signing key for the session JWT. REQUIRED when auth_enabled.
    # Rotate by changing it (all sessions invalidate). Lives outside the
    # IRRIGATION_ prefix to mirror the deployment-secret convention.
    auth_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GREENHOUSE_AUTH_SECRET_KEY"),
    )
    auth_token_ttl_minutes: int = 60 * 24  # 24h
    auth_cookie_name: str = "greenhouse_session"
    auth_cookie_secure: bool = False  # set True behind HTTPS
    # First-run bootstrap: when no user row exists at startup, create one
    # with these credentials. Outside IRRIGATION_ prefix to match the
    # deployment-secret convention.
    auth_admin_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GREENHOUSE_AUTH_ADMIN_USERNAME"),
    )
    auth_admin_password: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GREENHOUSE_AUTH_ADMIN_PASSWORD"),
    )

    # Pump dry-run watcher. During every active irrigation a background job
    # polls DP 105 (water-shortage alarm) over the local Tuya protocol and
    # immediately stops the pump on the first positive reading. Disable only
    # if your hardware doesn't expose the flag.
    pump_watcher_enabled: bool = True
    # Seconds between alarm polls. Lower = faster detection, more local
    # network traffic. The firmware itself debounces dry-run detection over
    # several seconds, so going below ~1s gains little.
    pump_watcher_poll_seconds: float = PUMP_WATCHER_POLL_SECONDS
    # Grace window at the start of each irrigation before the watcher will
    # trip. Avoids false positives during pump prime / initial suction.
    pump_watcher_warmup_seconds: float = PUMP_WATCHER_WARMUP_SECONDS
    # Cap on consecutive local-read failures tolerated before the watcher
    # gives up and exits (logging a warning). Does NOT stop the pump — a
    # broken local socket is not by itself evidence of a dry pump.
    pump_watcher_max_read_failures: int = PUMP_WATCHER_MAX_READ_FAILURES


def ignored_legacy_secret_env_names(env_names: Iterable[str]) -> list[str]:
    """The legacy spellings of the aliased secrets that are set but no longer read.

    Each aliased secret is read from ``GREENHOUSE_<NAME>`` only; the bare ``<NAME>`` and
    ``IRRIGATION_<NAME>`` used to configure it too. Matching is case-insensitive like the
    settings themselves. Returns the names as given (never the values), sorted.
    """
    legacy: set[str] = set()
    for info in Settings.model_fields.values():
        alias = info.validation_alias
        if isinstance(alias, AliasChoices):
            bare = str(alias.choices[0]).upper().removeprefix("GREENHOUSE_")
            legacy |= {bare, f"IRRIGATION_{bare}"}
    return sorted(name for name in env_names if name.upper() in legacy)

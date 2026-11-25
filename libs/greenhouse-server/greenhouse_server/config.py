"""Server configuration via Pydantic BaseSettings."""

from collections.abc import Iterable
from typing import Any, cast
from urllib.parse import urlsplit

from apscheduler.triggers.cron import CronTrigger
from pydantic import field_validator
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
    """Server settings from the environment (all prefixed ``GREENHOUSE_``)."""

    model_config = SettingsConfigDict(
        env_prefix="GREENHOUSE_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        # Validation errors must not echo raw input: these settings carry secrets.
        hide_input_in_errors=True,
    )

    def __init__(self, **values: object) -> None:
        """Forward kwargs to ``BaseSettings`` so ``_env_file`` and dynamic test kwargs type-check."""
        # Field values of any type: pydantic validates them; the base signature types only its own options.
        super().__init__(**cast("dict[str, Any]", values))

    db_url: str = "sqlite:///data/irrigation.db"
    # Loopback by default: /mcp and the API grant physical actuation, so a host
    # install must opt in to listening on the network. The Docker image sets
    # GREENHOUSE_HOST=0.0.0.0 (the container must listen for the published port).
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
            msg = f"GREENHOUSE_SYNC_INTERVAL_MINUTES must be a positive number of minutes, got {value}"
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
                f"GREENHOUSE_CHECK_CRON_HOURS={value!r} is not a valid cron hour expression "
                f"(e.g. '*', '0,6,12,18', '*/3', '6-20/2'): {exc}"
            )
            raise ValueError(msg) from None
        return value

    # MCP bearer token (fail-closed: unset -> /mcp returns 503).
    mcp_token: str | None = None

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
    # need none.
    ntfy_server_url: str | None = None

    @field_validator("ntfy_server_url")
    @classmethod
    def _validate_ntfy_server_url(cls, value: str | None) -> str | None:
        # Empty means "not configured" (the notifier stays off), like unset.
        return check_ntfy_server_url(value) if value else value

    ntfy_topic: str | None = None
    ntfy_token: str | None = None

    # ── Authentication ──────────────────────────────────────────────────────
    # When True (default) every /api/v1 route and the web UI require a valid
    # session. The login endpoint issues a JWT signed with `auth_secret_key`.
    # MCP keeps its own bearer token (`mcp_token`) — this auth covers humans
    # and CLI clients, MCP covers agent-driven actuation.
    auth_enabled: bool = True
    # HS256 signing key for the session JWT. REQUIRED when auth_enabled.
    # Rotate by changing it (all sessions invalidate).
    auth_secret_key: str | None = None
    auth_token_ttl_minutes: int = 60 * 24  # 24h
    auth_cookie_name: str = "greenhouse_session"
    auth_cookie_secure: bool = False  # set True behind HTTPS
    # First-run bootstrap: when no user row exists at startup, create one
    # with these credentials.
    auth_admin_username: str | None = None
    auth_admin_password: str | None = None

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


_BARE_SECRET_ENV_NAMES = frozenset(
    {
        "MCP_TOKEN",
        "NTFY_SERVER_URL",
        "NTFY_TOPIC",
        "NTFY_TOKEN",
        "AUTH_SECRET_KEY",
        "AUTH_ADMIN_USERNAME",
        "AUTH_ADMIN_PASSWORD",
    }
)
_EXTRA_LEGACY_IRRIGATION_ENV_NAMES = frozenset({"IRRIGATION_TZ"})


def ignored_legacy_env_names(env_names: Iterable[str]) -> list[str]:
    """Legacy env names that are set in ``env_names`` but no longer read.

    Every setting now reads from ``GREENHOUSE_<NAME>`` only; bare secret
    spellings (``MCP_TOKEN``, …) and the old ``IRRIGATION_*`` names are
    ignored. Matching is case-insensitive like ``Settings``. Returns the
    names as given (never the values), sorted.
    """
    legacy = (
        _BARE_SECRET_ENV_NAMES
        | _EXTRA_LEGACY_IRRIGATION_ENV_NAMES
        | {f"IRRIGATION_{name.upper()}" for name in Settings.model_fields}
    )
    return sorted(name for name in env_names if name.upper() in legacy)

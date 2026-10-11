"""HTTP client for the greenhouse server API.

``IrrigationClient`` is composed from the transport and one endpoint mixin per API area
(``greenhouse_cli._client``); this module keeps the token file and the constructor.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Self, TypedDict, Unpack

import httpx

from greenhouse_cli._client.clusters import ClusterEndpoints
from greenhouse_cli._client.devices import DeviceEndpoints
from greenhouse_cli._client.insights import InsightEndpoints
from greenhouse_cli._client.operations import OperationEndpoints
from greenhouse_cli._client.schedules import ScheduleEndpoints, WindowPatch
from greenhouse_cli._client.transport import JSONObject, RequestOptions, ServerError
from greenhouse_cli.constants import DEFAULT_SERVER_URL

__all__ = [
    "ClientOptions",
    "IrrigationClient",
    "JSONObject",
    "RequestOptions",
    "ServerError",
    "WindowPatch",
    "clear_stored_token",
    "load_stored_token",
    "store_token",
]


def _default_token_path() -> Path:
    """Resolve the on-disk session token location.

    Honours $XDG_CONFIG_HOME and falls back to ``~/.config/greenhouse/token``.
    """
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "greenhouse" / "token"


def load_stored_token() -> str | None:
    """Return the session JWT cached on disk by ``greenhouse login``, if any.

    ``$GREENHOUSE_API_TOKEN`` wins when it holds a token; otherwise the disk
    file is read. Whitespace is stripped, and a blank value (env or file) is
    treated as absent — a stray ``GREENHOUSE_API_TOKEN=" "`` must not hide the
    token ``greenhouse login`` stored.
    """
    env_token = (os.environ.get("GREENHOUSE_API_TOKEN") or "").strip()
    if env_token:
        return env_token
    path = _default_token_path()
    if not path.exists():
        return None
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def store_token(token: str) -> Path:
    """Persist a session JWT under the user config directory.

    Args:
        token: The bearer token returned by ``POST /api/v1/auth/login``.

    Returns:
        Path to the file that was written.
    """
    path = _default_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    path.chmod(0o600)
    return path


def clear_stored_token() -> bool:
    """Delete the cached session JWT file.

    Returns:
        ``True`` if a file was removed, ``False`` if no token was on disk.
    """
    path = _default_token_path()
    if not path.exists():
        return False
    path.unlink()
    return True


class _TransportOption(TypedDict, total=False):
    """The httpx ``transport`` an ``IrrigationClient`` passes through when one is given (tests mock it)."""

    transport: httpx.BaseTransport | None


class ClientOptions(_TransportOption, total=False):
    """Extra ``IrrigationClient`` settings: request ``headers`` and an httpx ``transport``."""

    headers: Mapping[str, str] | None


class IrrigationClient(ClusterEndpoints, DeviceEndpoints, OperationEndpoints, InsightEndpoints, ScheduleEndpoints):
    """Thin HTTP client wrapping the greenhouse REST API."""

    def __init__(
        self, base_url: str = DEFAULT_SERVER_URL, token: str | None = None, **kwargs: Unpack[ClientOptions]
    ) -> None:
        """Build the HTTP session; ``token=None`` falls back to the stored token, ``""`` sends no bearer header."""
        headers = dict(kwargs.get("headers") or {})
        resolved = token if token is not None else load_stored_token()
        if resolved:
            headers["Authorization"] = f"Bearer {resolved}"
        # Forward ``transport`` only when given: an explicit None would differ from httpx's own default.
        extras: _TransportOption = {"transport": kwargs["transport"]} if "transport" in kwargs else {}
        self.http = httpx.Client(base_url=base_url, timeout=30.0, headers=headers, **extras)

    # Context manager only, no public close(): every public method maps to one /api/v1 endpoint (tests enforce it).
    # CLI commands close the client per call; the TUI keeps one client per session and closes ``http`` on exit.
    def __enter__(self) -> Self:
        """Use the client as a context manager that closes its connection pool."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the underlying httpx connection pool."""
        self.http.close()

"""``local_key`` resolution for the gateway, including the refresh of a key that went stale.

A re-paired Tuya device gets a new ``local_key``, but the irrigator's stored ``config`` keeps the
old one, and tinytuya then answers every local call with error 914 ("Check device key or
version") — as a returned error dict, not an exception. ``DeviceGateway.run_local`` spots that
answer with :func:`is_key_or_version_error` and asks :meth:`LocalKeyResolver.refresh` for a new
key. The resolver keeps what that needs:

- the process cache of keys the Cloud handed out, and the persistence hook they go to;
- which stored keys a refresh superseded, so the refreshed key wins over the stale config value
  for the rest of the process while an operator-entered new key still wins over both;
- when each device last had a Cloud key lookup, so the refresh is rate-limited per device
  (``LOCAL_KEY_REFRESH_INTERVAL_SECONDS``) and steady state stays Cloud-free (invariant 8).

The Cloud ``getdevices`` call itself stays in the gateway; the resolver gets it as ``lookup``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

import tinytuya

from greenhouse_core.constants import LOCAL_KEY_REFRESH_INTERVAL_SECONDS
from greenhouse_core.models import parse_device_config

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_KEY_OR_VERSION_ERROR_CODE = str(tinytuya.ERR_KEY_OR_VER)  # tinytuya puts the code in "Err" as a string


def is_key_or_version_error(result: object) -> bool:
    """True for tinytuya's error-914 answer, ``{"Error": ..., "Err": "914", ...}``.

    Only that code points at a wrong key; every other error (offline, payload, …) is left to
    the caller, so an unreachable device never costs a Cloud lookup.
    """
    return isinstance(result, dict) and str(result.get("Err")) == _KEY_OR_VERSION_ERROR_CODE


class LocalKeyResolver:
    """Config key → process cache → one Cloud lookup, plus the rate-limited 914 refresh.

    One per (app-scoped) gateway. Thread-safe where it matters: request threads and scheduler
    jobs share the gateway, so claiming a device's lookup budget is one atomic check-and-stamp.
    """

    def __init__(
        self, lookup: Callable[[str], str | None], on_key_discovered: Callable[[str, str], None] | None
    ) -> None:
        """Bind the gateway's Cloud key ``lookup`` and the optional persistence hook."""
        self.cache: dict[str, str] = {}
        self._lookup = lookup
        self._on_key_discovered = on_key_discovered
        self._superseded: dict[str, set[str]] = {}
        self._last_lookup: dict[str, int] = {}
        self._lock = threading.Lock()

    def resolve(self, device_id: str, config: object | None) -> str | None:
        """The key to open ``device_id`` with: :meth:`known`, else one Cloud lookup (cached, persisted)."""
        known = self.known(device_id, config)
        if known:
            return known
        key = self._lookup(device_id)
        with self._lock:
            self._last_lookup[device_id] = int(time.time())  # a cold lookup spends the refresh budget too
        if key:
            self._remember(device_id, key)
        return key

    def known(self, device_id: str, config: object | None) -> str | None:
        """The Cloud-free answer: the config key unless a refresh superseded it, else the cache."""
        cfg_key: str | None = parse_device_config(config).get("local_key") if config is not None else None
        with self._lock:
            if cfg_key and cfg_key not in self._superseded.get(device_id, ()):
                return cfg_key
        return self.cache.get(device_id) or None

    def refresh(self, device_id: str, config: object | None) -> bool:
        """After a 914, fetch the device's key from the Cloud; True when a different key now applies.

        At most one lookup per device per ``LOCAL_KEY_REFRESH_INTERVAL_SECONDS``. A lookup that
        fails, or a Cloud key equal to the one that failed (a version or reachability problem,
        not a stale key), returns False. A new key supersedes the failed one for the rest of
        the process and goes to the persistence hook, so a restart does not regress. Never logs
        a key.
        """
        if not self._claim_lookup(device_id):
            logger.debug("Local call to %s answered error 914; the key refresh is rate-limited", device_id)
            return False
        logger.warning(
            "Local call to %s answered error 914 (device key or version); refreshing its local_key from the Cloud",
            device_id,
        )
        used = self.known(device_id, config)
        try:
            fresh = self._lookup(device_id)
        except Exception:  # a failed lookup (logged with traceback) leaves the original 914 result standing
            logger.warning("Cloud local_key lookup for %s failed; keeping the local error", device_id, exc_info=True)
            return False
        if not fresh or fresh == used:
            logger.info("The Cloud holds no different local_key for %s; not retrying the local call", device_id)
            return False
        if used:
            with self._lock:
                self._superseded.setdefault(device_id, set()).add(used)
        logger.info("Found a new local_key for %s in the Cloud; retrying the local call with it", device_id)
        self._remember(device_id, fresh)
        return True

    def _claim_lookup(self, device_id: str) -> bool:
        """Take the device's lookup budget if the interval since its last lookup has passed."""
        now = int(time.time())
        with self._lock:
            last = self._last_lookup.get(device_id)
            if last is not None and now - last < LOCAL_KEY_REFRESH_INTERVAL_SECONDS:
                return False
            self._last_lookup[device_id] = now
            return True

    def _remember(self, device_id: str, key: str) -> None:
        """Cache a key the Cloud handed out and give it to the persistence hook (whose failure is logged)."""
        self.cache[device_id] = key
        if self._on_key_discovered is None:
            return
        try:
            self._on_key_discovered(device_id, key)
        except Exception:
            logger.exception("local_key persistence hook failed for %s", device_id)

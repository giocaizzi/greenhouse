"""Persist a ``local_key`` the device gateway learned from the Cloud into the irrigator's config.

The gateway hands every key it fetches from the Cloud — a cold lookup, or the refresh after a
re-paired device answered error 914 — to its ``on_key_discovered`` hook. The server wires that
hook to :func:`local_key_persister`, so the key survives a restart instead of being re-fetched
(or, for a stale stored key, failing again until the next refresh).

The write runs on a short-lived daemon thread with its own session. The hook fires inside a
local device call, often while the caller's own session holds the SQLite write lock (the
pipeline has already written its decision log before it starts the pump): a write from the
same thread would wait out the busy timeout before failing, delaying the actuation it sits
in. From a thread it simply waits for that transaction to commit. A failed write is logged
and never reaches the device path; the gateway keeps using the key in-process either way.
"""

import logging
import threading
from typing import TYPE_CHECKING

from greenhouse_core.models import parse_device_config
from greenhouse_core.repository import IrrigationRepository
from greenhouse_server.services.jobs import job_session

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

logger = logging.getLogger(__name__)

PERSIST_THREAD_NAME = "local-key-persist"


def persist_local_key(app: "FastAPI", device_id: str, local_key: str) -> None:
    """Write ``local_key`` into the config of the irrigator registered under ``device_id``.

    The rest of the config (``device_ip``, …) is kept. Commits on its own session; a device
    id no irrigator uses (a sensor) is a no-op. Failures are logged, never raised, and the
    key itself is never logged.

    Args:
        app: The FastAPI app whose ``state.session_factory`` opens the session.
        device_id: The Tuya device id the gateway looked the key up for.
        local_key: The key the Cloud holds for that device now.
    """
    with job_session(app, logger, "Could not persist the refreshed local_key for %s", device_id, commit=False) as s:
        repo = IrrigationRepository(s)
        irrigator = repo.get_irrigator_by_device_id(device_id)
        if irrigator is None:
            logger.debug("No irrigator uses device %s; its local_key is not persisted", device_id)
            return
        config = parse_device_config(irrigator.config)
        if config.get("local_key") == local_key:
            return
        repo.update_irrigator(irrigator.id, config={**config, "local_key": local_key})
        repo.commit()
        logger.info("Persisted the new local_key of device %s to irrigator %d", device_id, irrigator.id)


def local_key_persister(app: "FastAPI") -> "Callable[[str, str], None]":
    """The gateway's ``on_key_discovered`` hook: persist each key on a background thread.

    Args:
        app: The FastAPI app whose database the key is written to.

    Returns:
        A ``(device_id, local_key)`` callback that starts the write and returns at once.
    """

    def persist_in_background(device_id: str, local_key: str) -> None:
        threading.Thread(
            target=persist_local_key, args=(app, device_id, local_key), name=PERSIST_THREAD_NAME, daemon=True
        ).start()

    return persist_in_background

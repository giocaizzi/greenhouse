"""One background-job transaction: the session scaffolding every scheduler job shares.

Lives below the scheduler (``app > … > scheduler > services``) so services may use it too;
the scheduler's five jobs do. The services' one-shot jobs (leak check, pump watcher) keep
their own scaffolding because their shapes differ (an early return that leaves the read-only
transaction to ``close()``; a conditional commit). The caller passes its module logger, so
each failure record keeps its module's logger name.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging

    from fastapi import FastAPI
    from sqlalchemy.orm import Session


@contextmanager
def job_session(
    app: "FastAPI | None", logger: "logging.Logger", failure_message: str, *args: object
) -> "Iterator[Session]":
    """Commit on success, roll back and log on failure, always close.

    The session is opened before the ``try``, so a missing app or ``session_factory`` escapes
    the job instead of being logged. It yields the bare session: each caller builds its
    repository inside the ``with`` body, so a failure there is logged and swallowed like any
    other job failure (a raise before a ``yield`` would surface as
    ``RuntimeError("generator didn't yield")``). Callers pass the module ``_app``
    they read at call time — never a default captured at import.

    Args:
        app: The FastAPI app whose ``state.session_factory`` opens the session.
        logger: The calling module's logger; receives ``logger.exception(failure_message, *args)``.
        failure_message: %-style message logged (with traceback) when the body or the commit fails.
        *args: Lazy %-arguments for ``failure_message``.
    """
    session = app.state.session_factory()  # type: ignore[union-attr]  # a None app escapes as AttributeError
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception(failure_message, *args)
    finally:
        session.close()

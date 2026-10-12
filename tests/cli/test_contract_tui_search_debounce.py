"""Characterization pin for the search dialog's 0.2 s keystroke debounce (``SearchScreen.search``).

``test_contract_tui_screens`` holds search responses until the last keystroke (commit 5faca04), so it no longer
observes the debounce. This test does: every ``client.search`` call is timestamped with the monotonic clock while
"citrus" is typed key by key. The debounce means no search can be sent sooner than 0.2 s after the first keystroke,
and the last query sent is the full text. CPU load can only delay calls, so it cannot make this test fail falsely.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from cli.test_contract_tui import make_seeded_app, make_tui, settle_fast, wait_until
from cli.tui_fixtures import tui_client_factory

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    import time_machine
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine

    from greenhouse_cli._client.transport import JSONObject
    from greenhouse_cli.client import IrrigationClient

SIZE = (120, 40)
DEBOUNCE_SECONDS = 0.2


@pytest.fixture
def seeded(clean_env: Path, frozen_clock: time_machine.Traveller) -> Iterator[tuple[TestClient, Engine]]:
    """(http, engine) for a seeded app — clock frozen before ``seed_greenhouse`` runs (monotonic keeps running)."""
    del clean_env, frozen_clock  # side-effect fixtures (a hermetic environment; the clock is frozen)
    http, _, engine = make_seeded_app()
    yield http, engine
    engine.dispose()


def _timestamping_factory(
    http: TestClient, calls: list[tuple[float, str]], mp: pytest.MonkeyPatch
) -> Callable[[str | None], IrrigationClient]:
    base = tui_client_factory(http)

    def factory(token: str | None) -> IrrigationClient:
        client = base(token)
        search: Callable[..., JSONObject] = client.search

        def timed(query: str, *args: object, **kwargs: object) -> JSONObject:
            calls.append((time.monotonic(), query))
            return search(query, *args, **kwargs)

        mp.setattr(client, "search", timed)
        return client

    return factory


def test_search_is_debounced_and_sends_the_full_query_last(
    seeded: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """First search ≥ 0.2 s after the first keystroke; the last query sent is "citrus"."""
    http, _ = seeded
    calls: list[tuple[float, str]] = []
    first_key: list[float] = []

    async def scenario() -> None:
        tui = make_tui(http, factory=_timestamping_factory(http, calls, monkeypatch))
        async with tui.run_test(size=SIZE) as pilot:
            await settle_fast(pilot, tui)
            await pilot.press("slash")
            await settle_fast(pilot, tui)
            first_key.append(time.monotonic())
            for key in "citrus":
                await pilot.press(key)
            await wait_until(lambda: any(q == "citrus" for _, q in calls), "a search for 'citrus' to be sent")
            await settle_fast(pilot, tui)

    asyncio.run(scenario())
    assert calls, "no search was sent"
    assert calls[0][0] - first_key[0] >= DEBOUNCE_SECONDS, f"first search after {calls[0][0] - first_key[0]:.3f}s"
    assert calls[-1][1] == "citrus"

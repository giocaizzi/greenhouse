"""Characterization pin for the search dialog's 0.2 s keystroke debounce (``SearchScreen.search``).

``test_contract_tui_screens`` holds search responses until the last keystroke (commit 5faca04), so it no longer
observes the debounce. This test does: every ``client.search`` call is timestamped with the monotonic clock while
"citrus" is typed key by key. The debounce means no search can be sent sooner than 0.2 s after the first keystroke,
and the last query sent is the full text. CPU load can only delay calls, so it cannot make this test fail falsely.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from cli.test_contract_tui import make_seeded_app, make_tui, settle, wait_until
from cli.tui_fixtures import tui_client_factory

SIZE = (120, 40)
DEBOUNCE_SECONDS = 0.2


@pytest.fixture
def seeded(clean_env, frozen_clock):
    """(http, engine) for a seeded app — clock frozen before ``seed_greenhouse`` runs (monotonic keeps running)."""
    http, _, engine = make_seeded_app()
    yield http, engine
    engine.dispose()


def _timestamping_factory(http, calls: list[tuple[float, str]]):
    base = tui_client_factory(http)

    def factory(token):
        client = base(token)
        search = client.search

        def timed(query, *args, **kwargs):
            calls.append((time.monotonic(), query))
            return search(query, *args, **kwargs)

        client.search = timed
        return client

    return factory


def test_search_is_debounced_and_sends_the_full_query_last(seeded):
    """First search ≥ 0.2 s after the first keystroke; the last query sent is "citrus"."""
    http, _ = seeded
    calls: list[tuple[float, str]] = []
    first_key: list[float] = []

    async def scenario():
        tui = make_tui(http, factory=_timestamping_factory(http, calls))
        async with tui.run_test(size=SIZE) as pilot:
            await settle(pilot, tui)
            await pilot.press("slash")
            await settle(pilot, tui)
            first_key.append(time.monotonic())
            for key in "citrus":
                await pilot.press(key)
            await wait_until(lambda: any(q == "citrus" for _, q in calls), "a search for 'citrus' to be sent")
            await settle(pilot, tui)

    asyncio.run(scenario())
    assert calls, "no search was sent"
    assert calls[0][0] - first_key[0] >= DEBOUNCE_SECONDS, f"first search after {calls[0][0] - first_key[0]:.3f}s"
    assert calls[-1][1] == "citrus"

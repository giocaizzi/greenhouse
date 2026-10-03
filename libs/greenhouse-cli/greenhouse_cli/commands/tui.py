"""``greenhouse tui`` — launch the full-screen terminal UI."""

from __future__ import annotations

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import server_url


def tui(
    ctx: typer.Context,
    refresh: Annotated[
        float,
        typer.Option(min=0, help="Auto-refresh interval in seconds (0 disables)"),
    ] = 30.0,
    no_animation: Annotated[
        bool,
        typer.Option("--no-animation", help="Render sprites as still frames"),
    ] = False,
):
    """Open the interactive terminal dashboard.

    Animated plant sprites, live charts, decision trails, alerts, activity
    and system health — plus irrigate / water-now / stop / check / sync
    actions behind confirmation dialogs. Talks to the server over HTTP,
    so it works against any reachable greenhouse server (``--server``).
    """
    from greenhouse_cli.tui import run

    run(server_url(ctx), refresh_seconds=refresh, animations=not no_animation)


def register(app: typer.Typer) -> None:
    """Register the top-level ``tui`` command on the main Typer app."""
    app.command()(tui)

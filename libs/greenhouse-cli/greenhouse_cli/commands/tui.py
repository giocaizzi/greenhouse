"""``greenhouse tui`` — launch the full-screen terminal UI."""

from __future__ import annotations

import os
from typing import Annotated

import typer


def register(app: typer.Typer) -> None:
    """Register the top-level ``tui`` command on the main Typer app."""

    @app.command()
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

        server = ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", "http://localhost:8000")
        run(server, refresh_seconds=refresh, animations=not no_animation)

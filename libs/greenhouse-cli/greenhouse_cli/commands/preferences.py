"""User-preference commands."""

from __future__ import annotations

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output

prefs_app = typer.Typer(help="Read and update user preferences", no_args_is_help=True)


@prefs_app.command("get")
def prefs_get(ctx: typer.Context):
    """Show the current preferences."""
    output(call(ctx, lambda c: c.get_preferences()))


@prefs_app.command("set")
def prefs_set(
    ctx: typer.Context,
    units: Annotated[str | None, typer.Option(help="metric or imperial")] = None,
    timezone: Annotated[str | None, typer.Option(help="IANA timezone name")] = None,
    theme: Annotated[str | None, typer.Option(help="light / dark / auto")] = None,
    refresh_interval: Annotated[
        int | None,
        typer.Option("--refresh-interval", help="Web UI auto-refresh in seconds"),
    ] = None,
    dry_run_global: Annotated[
        bool | None,
        typer.Option("--dry-run-global/--no-dry-run-global", help="Run engine in dry-run for every cluster"),
    ] = None,
    default_cluster: Annotated[
        int | None,
        typer.Option("--default-cluster", help="Default cluster ID for one-click commands"),
    ] = None,
):
    """Patch preferences. Omitted fields are left unchanged."""
    output(
        call(
            ctx,
            lambda c: c.update_preferences(
                units=units,
                timezone=timezone,
                theme=theme,
                refresh_interval_seconds=refresh_interval,
                dry_run_global=dry_run_global,
                default_cluster_id=default_cluster,
            ),
        )
    )

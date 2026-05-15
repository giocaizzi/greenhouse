"""Per-cluster irrigation-window commands."""

from __future__ import annotations

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output

windows_app = typer.Typer(
    help="Manage per-cluster preferred watering windows",
    no_args_is_help=True,
)


@windows_app.command("list")
def windows_list(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Option(help="Cluster ID")],
):
    """List configured windows for a cluster. Empty list = global defaults apply."""
    output(call(ctx, lambda c: c.list_windows(cluster)))


@windows_app.command("add")
def windows_add(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Option(help="Cluster ID")],
    start_hour: Annotated[int, typer.Option("--start-hour", min=0, max=23, help="Local-time start hour")],
    end_hour: Annotated[int, typer.Option("--end-hour", min=0, max=23, help="Local-time end hour (exclusive)")],
    weekday_mask: Annotated[
        int,
        typer.Option(
            "--weekday-mask",
            min=1,
            max=127,
            help="Weekday bitmask (Mon=1, Tue=2, ..., Sun=64; 127 = every day)",
        ),
    ] = 127,
    label: Annotated[str | None, typer.Option(help="Optional label, e.g. 'morning'")] = None,
):
    """Add a watering window. Wrap-around (start > end) crosses midnight."""
    output(
        call(
            ctx,
            lambda c: c.add_window(
                cluster,
                start_hour=start_hour,
                end_hour=end_hour,
                weekday_mask=weekday_mask,
                label=label,
            ),
        )
    )


@windows_app.command("update")
def windows_update(
    ctx: typer.Context,
    window_id: Annotated[int, typer.Argument(help="Window ID")],
    cluster: Annotated[int, typer.Option(help="Cluster the window belongs to")],
    start_hour: Annotated[int | None, typer.Option("--start-hour", min=0, max=23)] = None,
    end_hour: Annotated[int | None, typer.Option("--end-hour", min=0, max=23)] = None,
    weekday_mask: Annotated[int | None, typer.Option("--weekday-mask", min=1, max=127)] = None,
    label: Annotated[str | None, typer.Option()] = None,
):
    """Patch a window. Only supplied fields are sent."""
    output(
        call(
            ctx,
            lambda c: c.update_window(
                cluster,
                window_id,
                start_hour=start_hour,
                end_hour=end_hour,
                weekday_mask=weekday_mask,
                label=label,
            ),
        )
    )


@windows_app.command("delete")
def windows_delete(
    ctx: typer.Context,
    window_id: Annotated[int, typer.Argument(help="Window ID")],
    cluster: Annotated[int, typer.Option(help="Cluster the window belongs to")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")] = False,
):
    """Remove an irrigation window."""
    if not yes:
        typer.confirm(f"Delete window {window_id} on cluster {cluster}?", abort=True)
    output(call(ctx, lambda c: c.delete_window(cluster, window_id)))

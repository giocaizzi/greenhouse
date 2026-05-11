"""Irrigation configuration commands."""

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output

config_app = typer.Typer(help="Irrigation configuration", no_args_is_help=True)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Option(help="Cluster ID")],
    mode: Annotated[str, typer.Option(help="manual, schedule, or smart")],
    minutes: Annotated[int | None, typer.Option(help="Duration in minutes")] = None,
    interval: Annotated[int | None, typer.Option(help="Interval in hours")] = None,
    auto_run: Annotated[bool, typer.Option(help="Enable auto-run")] = True,
):
    """Set irrigation config for a cluster."""
    data = call(
        ctx,
        lambda c: c.set_config(
            cluster,
            mode=mode,
            duration_minutes=minutes,
            interval_hours=interval,
            auto_run=auto_run,
        ),
    )
    output(data)


@config_app.command("get")
def config_get(ctx: typer.Context, cluster: Annotated[int, typer.Option(help="Cluster ID")]):
    """Get irrigation config for a cluster."""
    output(call(ctx, lambda c: c.get_config(cluster)))

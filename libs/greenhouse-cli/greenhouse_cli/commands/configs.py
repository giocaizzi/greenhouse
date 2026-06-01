"""Irrigation configuration commands.

Per-cluster config is hierarchical: each field resolves cluster → global →
built-in constant. Omitting an option here leaves the stored value unchanged;
to clear an override back to "inherit", edit it from the web UI (empty field)
or the API. For quiet hours, passing equal ``--quiet-start``/``--quiet-end``
switches the gate off at that level (an outdoor cluster opting out of an
inherited indoor window).
"""

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output

config_app = typer.Typer(help="Irrigation configuration", no_args_is_help=True)
global_app = typer.Typer(help="Global irrigation defaults (inherited by every cluster)", no_args_is_help=True)
config_app.add_typer(global_app, name="global")


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Option(help="Cluster ID")],
    mode: Annotated[str | None, typer.Option(help="manual, schedule, or smart")] = None,
    minutes: Annotated[int | None, typer.Option(help="Duration in minutes")] = None,
    interval: Annotated[int | None, typer.Option(help="Interval in hours")] = None,
    auto_run: Annotated[bool | None, typer.Option("--auto-run/--no-auto-run", help="Enable auto-run")] = None,
    daily_cap: Annotated[int | None, typer.Option("--daily-cap", help="Daily cap in minutes")] = None,
    max_events: Annotated[int | None, typer.Option("--max-events", help="Max irrigation events per day")] = None,
    quiet_start: Annotated[
        int | None, typer.Option("--quiet-start", help="Quiet-hours start (0-23); equal to end = disabled")
    ] = None,
    quiet_end: Annotated[int | None, typer.Option("--quiet-end", help="Quiet-hours end (0-23, exclusive)")] = None,
):
    """Patch a cluster's irrigation config. Omitted options are left unchanged."""
    data = call(
        ctx,
        lambda c: c.set_config(
            cluster,
            mode=mode,
            duration_minutes=minutes,
            interval_hours=interval,
            auto_run=auto_run,
            daily_cap_minutes=daily_cap,
            max_events_per_day=max_events,
            quiet_start_hour=quiet_start,
            quiet_end_hour=quiet_end,
        ),
    )
    output(data)


@config_app.command("get")
def config_get(ctx: typer.Context, cluster: Annotated[int, typer.Option(help="Cluster ID")]):
    """Get a cluster's declared irrigation config (nulls = inherited)."""
    output(call(ctx, lambda c: c.get_config(cluster)))


@config_app.command("effective")
def config_effective(ctx: typer.Context, cluster: Annotated[int, typer.Option(help="Cluster ID")]):
    """Show the merged config: each field's resolved value and its source layer."""
    output(call(ctx, lambda c: c.get_effective_config(cluster)))


@global_app.command("get")
def global_get(ctx: typer.Context):
    """Show the global irrigation defaults (nulls = fall through to constants)."""
    output(call(ctx, lambda c: c.get_global_config()))


@global_app.command("set")
def global_set(
    ctx: typer.Context,
    mode: Annotated[str | None, typer.Option(help="manual, schedule, or smart")] = None,
    minutes: Annotated[int | None, typer.Option(help="Duration in minutes")] = None,
    interval: Annotated[int | None, typer.Option(help="Interval in hours")] = None,
    auto_run: Annotated[bool | None, typer.Option("--auto-run/--no-auto-run", help="Enable auto-run")] = None,
    daily_cap: Annotated[int | None, typer.Option("--daily-cap", help="Daily cap in minutes")] = None,
    max_events: Annotated[int | None, typer.Option("--max-events", help="Max irrigation events per day")] = None,
    quiet_start: Annotated[
        int | None, typer.Option("--quiet-start", help="Quiet-hours start (0-23); equal to end = disabled")
    ] = None,
    quiet_end: Annotated[int | None, typer.Option("--quiet-end", help="Quiet-hours end (0-23, exclusive)")] = None,
):
    """Patch the global irrigation defaults. Omitted options are left unchanged."""
    data = call(
        ctx,
        lambda c: c.update_global_config(
            mode=mode,
            duration_minutes=minutes,
            interval_hours=interval,
            auto_run=auto_run,
            daily_cap_minutes=daily_cap,
            max_events_per_day=max_events,
            quiet_start_hour=quiet_start,
            quiet_end_hour=quiet_end,
        ),
    )
    output(data)

"""Scheduler management commands."""

import typer

from greenhouse_cli.commands._helpers import call, output

scheduler_app = typer.Typer(help="Manage background scheduler jobs", no_args_is_help=True)


@scheduler_app.command("pause")
def scheduler_pause(ctx: typer.Context) -> None:
    """Pause the `check_all` scheduler job.

    Stops automated cluster checks until `resume`. Other background jobs
    (sensor sync, anomaly scan, plant-health snapshot) continue to run.
    The pause is persisted, so it survives a server restart.
    """
    output(call(ctx, lambda c: c.scheduler_pause()))


@scheduler_app.command("resume")
def scheduler_resume(ctx: typer.Context) -> None:
    """Resume the `check_all` scheduler job after a pause."""
    output(call(ctx, lambda c: c.scheduler_resume()))


@scheduler_app.command("status")
def scheduler_status(ctx: typer.Context) -> None:
    """List every background job with its trigger and pause state."""
    output(call(ctx, lambda c: c.scheduler_jobs()))

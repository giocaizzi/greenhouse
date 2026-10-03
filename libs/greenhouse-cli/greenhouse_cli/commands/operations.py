"""Top-level operation commands: status, irrigate, check, monitor, sync, learn, history, stats, health."""

from pathlib import Path
from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import ClusterArg, YesOpt, call, output


def status(ctx: typer.Context, cluster: ClusterArg) -> None:
    """Full cluster overview: sensors, config, decision, alerts."""
    output(call(ctx, lambda c: c.status(cluster)))


def irrigate(
    ctx: typer.Context,
    cluster: ClusterArg,
    temp: Annotated[float | None, typer.Option(help="Override temperature (skips sync + weather)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Analyze only, don't execute")] = False,
    no_sync: Annotated[bool, typer.Option("--no-sync", help="Skip sensor sync")] = False,
    force: Annotated[bool, typer.Option("--force", help="Bypass the quiet-hours gate (logs an override)")] = False,
) -> None:
    """Smart irrigation: sync sensors → fetch weather → decide → execute."""
    data = call(ctx, lambda c: c.irrigate(cluster, temp_override=temp, dry_run=dry_run, no_sync=no_sync, force=force))
    output(data)
    if data.get("action") == "error":
        raise typer.Exit(1)


def check(
    ctx: typer.Context,
    cluster: Annotated[int | None, typer.Argument(help="Cluster ID")] = None,
    all_clusters: Annotated[bool, typer.Option("--all", help="Check all clusters")] = False,
) -> None:
    """Unified check: irrigate or monitor + collect all alerts."""
    if not cluster and not all_clusters:
        typer.echo("Error: provide a cluster ID or --all", err=True)
        raise typer.Exit(1)

    data = call(ctx, lambda c: c.check() if all_clusters else c.check(cluster))
    output(data)

    if isinstance(data, dict):
        if data.get("has_alerts"):
            raise typer.Exit(2)
        if data.get("action") == "error":
            raise typer.Exit(1)


def monitor(ctx: typer.Context, cluster: ClusterArg) -> None:
    """Raw moisture check for sensor-only clusters."""
    data = call(ctx, lambda c: c.monitor(cluster))
    output(data)
    if data.get("needs_water"):
        raise typer.Exit(2)


def sync(
    ctx: typer.Context,
    hours: Annotated[int, typer.Option(help="History window in hours")] = 24,
) -> None:
    """Sync sensor data from Tuya Cloud."""
    output(call(ctx, lambda c: c.sync(hours=hours)))


def learn(ctx: typer.Context, cluster: ClusterArg) -> None:
    """Learning report: efficiency analysis and pattern detection."""
    output(call(ctx, lambda c: c.learn(cluster)))


def history(
    ctx: typer.Context,
    cluster: ClusterArg,
    hours: Annotated[int, typer.Option(help="Hours of history")] = 24,
    limit: Annotated[int, typer.Option(help="Max entries per section")] = 50,
) -> None:
    """Sensor readings + irrigation events timeline."""
    output(call(ctx, lambda c: c.history(cluster, hours=hours, limit=limit)))


def stats(
    ctx: typer.Context,
    cluster: ClusterArg,
    days: Annotated[int, typer.Option(help="Days to analyze")] = 7,
    export: Annotated[str | None, typer.Option(help="Export CSV to file")] = None,
) -> None:
    """Irrigation statistics and CSV export."""
    if export:
        csv_data = call(ctx, lambda c: c.stats_export(cluster, days=days))
        with Path(export).open("w") as f:
            f.write(csv_data)
        typer.echo(f"Exported to {export}")
    else:
        output(call(ctx, lambda c: c.stats(cluster, days=days)))


def health(ctx: typer.Context) -> None:
    """Server health and scheduler status."""
    output(call(ctx, lambda c: c.health()))


def stop_all(
    ctx: typer.Context,
    yes: YesOpt = False,
) -> None:
    """Emergency kill switch: stop every irrigator in the system."""
    if not yes:
        typer.confirm(
            "Send emergency stop to ALL irrigators in the system?",
            abort=True,
        )
    output(call(ctx, lambda c: c.bulk_stop_all()))


def register(app: typer.Typer) -> None:
    """Register all operation commands on the main app; this order is the `--help` listing order."""
    app.command()(status)
    app.command()(irrigate)
    app.command()(check)
    app.command()(monitor)
    app.command()(sync)
    app.command()(learn)
    app.command()(history)
    app.command()(stats)
    app.command()(health)
    app.command("stop-all")(stop_all)

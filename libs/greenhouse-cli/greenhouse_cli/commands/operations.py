"""Top-level operation commands: status, irrigate, check, monitor, sync, learn, history, stats, health."""

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output


def register(app: typer.Typer) -> None:
    """Register all operation commands on the main app."""

    @app.command()
    def status(ctx: typer.Context, cluster: Annotated[int, typer.Argument(help="Cluster ID")]):
        """Full cluster overview: sensors, config, decision, alerts."""
        output(call(ctx, lambda c: c.status(cluster)))

    @app.command()
    def irrigate(
        ctx: typer.Context,
        cluster: Annotated[int, typer.Argument(help="Cluster ID")],
        temp: Annotated[float | None, typer.Option(help="Override temperature (skips sync + weather)")] = None,
        dry_run: Annotated[bool, typer.Option("--dry-run", help="Analyze only, don't execute")] = False,
        no_sync: Annotated[bool, typer.Option("--no-sync", help="Skip sensor sync")] = False,
        force: Annotated[bool, typer.Option("--force", help="Bypass the quiet-hours gate (logs an override)")] = False,
    ):
        """Smart irrigation: sync sensors → fetch weather → decide → execute."""
        data = call(
            ctx, lambda c: c.irrigate(cluster, temp_override=temp, dry_run=dry_run, no_sync=no_sync, force=force)
        )
        output(data)
        if data.get("action") == "error":
            raise typer.Exit(1)

    @app.command()
    def check(
        ctx: typer.Context,
        cluster: Annotated[int | None, typer.Argument(help="Cluster ID")] = None,
        all_clusters: Annotated[bool, typer.Option("--all", help="Check all clusters")] = False,
    ):
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

    @app.command()
    def monitor(ctx: typer.Context, cluster: Annotated[int, typer.Argument(help="Cluster ID")]):
        """Raw moisture check for sensor-only clusters."""
        data = call(ctx, lambda c: c.monitor(cluster))
        output(data)
        if data.get("needs_water"):
            raise typer.Exit(2)

    @app.command()
    def sync(
        ctx: typer.Context,
        hours: Annotated[int, typer.Option(help="History window in hours")] = 24,
    ):
        """Sync sensor data from Tuya Cloud."""
        output(call(ctx, lambda c: c.sync(hours=hours)))

    @app.command()
    def learn(ctx: typer.Context, cluster: Annotated[int, typer.Argument(help="Cluster ID")]):
        """Learning report: efficiency analysis and pattern detection."""
        output(call(ctx, lambda c: c.learn(cluster)))

    @app.command()
    def history(
        ctx: typer.Context,
        cluster: Annotated[int, typer.Argument(help="Cluster ID")],
        hours: Annotated[int, typer.Option(help="Hours of history")] = 24,
        limit: Annotated[int, typer.Option(help="Max entries per section")] = 50,
    ):
        """Sensor readings + irrigation events timeline."""
        output(call(ctx, lambda c: c.history(cluster, hours=hours, limit=limit)))

    @app.command()
    def stats(
        ctx: typer.Context,
        cluster: Annotated[int, typer.Argument(help="Cluster ID")],
        days: Annotated[int, typer.Option(help="Days to analyze")] = 7,
        export: Annotated[str | None, typer.Option(help="Export CSV to file")] = None,
    ):
        """Irrigation statistics and CSV export."""
        if export:
            csv_data = call(ctx, lambda c: c.stats_export(cluster, days=days))
            with open(export, "w") as f:
                f.write(csv_data)
            typer.echo(f"Exported to {export}")
        else:
            output(call(ctx, lambda c: c.stats(cluster, days=days)))

    @app.command()
    def health(ctx: typer.Context):
        """Server health and scheduler status."""
        output(call(ctx, lambda c: c.health()))

    @app.command("stop-all")
    def stop_all(
        ctx: typer.Context,
        yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")] = False,
    ):
        """Emergency kill switch: stop every irrigator in the system."""
        if not yes:
            typer.confirm(
                "Send emergency stop to ALL irrigators in the system?",
                abort=True,
            )
        output(call(ctx, lambda c: c.bulk_stop_all()))

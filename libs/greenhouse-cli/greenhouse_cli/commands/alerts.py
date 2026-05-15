"""Alert inbox commands."""

from __future__ import annotations

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output

alerts_app = typer.Typer(help="Inspect and manage the alert inbox", no_args_is_help=True)


@alerts_app.command("list")
def alerts_list(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option(help="Filter by status: open / acknowledged / resolved")] = None,
    cluster: Annotated[int | None, typer.Option(help="Filter by cluster ID")] = None,
    plant: Annotated[int | None, typer.Option(help="Filter by plant ID")] = None,
    limit: Annotated[int, typer.Option(min=1, max=500, help="Max number of items")] = 100,
):
    """List persisted alerts. Newest-seen first."""
    output(
        call(
            ctx,
            lambda c: c.list_alerts(status=status, cluster_id=cluster, plant_id=plant, limit=limit),
        )
    )


@alerts_app.command("get")
def alerts_get(
    ctx: typer.Context,
    alert_id: Annotated[int, typer.Argument(help="Alert ID")],
):
    """Fetch a single alert by ID."""
    output(call(ctx, lambda c: c.get_alert(alert_id)))


@alerts_app.command("ack")
def alerts_ack(
    ctx: typer.Context,
    alert_id: Annotated[int, typer.Argument(help="Alert ID")],
):
    """Acknowledge an open alert (idempotent)."""
    output(call(ctx, lambda c: c.acknowledge_alert(alert_id)))


@alerts_app.command("resolve")
def alerts_resolve(
    ctx: typer.Context,
    alert_id: Annotated[int, typer.Argument(help="Alert ID")],
):
    """Mark an alert as resolved."""
    output(call(ctx, lambda c: c.resolve_alert(alert_id)))


@alerts_app.command("sync")
def alerts_sync(
    ctx: typer.Context,
    cluster: Annotated[int | None, typer.Option(help="Sync just this cluster (default: all)")] = None,
):
    """Recompute alerts and reconcile the inbox.

    Without ``--cluster`` this syncs every cluster. With ``--cluster`` it
    syncs only that cluster.
    """
    output(call(ctx, lambda c: c.sync_alerts(cluster_id=cluster)))

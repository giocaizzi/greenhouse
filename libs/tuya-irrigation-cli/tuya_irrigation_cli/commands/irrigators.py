"""Irrigator management and control commands."""

import json
from typing import Annotated

import typer

from tuya_irrigation_cli.commands._helpers import call, output

irrigator_app = typer.Typer(help="Manage and control irrigators", no_args_is_help=True)


@irrigator_app.command("add")
def irrigator_add(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Option(help="Cluster ID")],
    device_id: Annotated[str, typer.Option(help="Tuya device ID")],
    name: Annotated[str, typer.Option(help="Irrigator name")],
    type: Annotated[str, typer.Option(help="tuya_cloud or tuya_local")],
    device_ip: Annotated[str | None, typer.Option(help="Local IP")] = None,
    local_key: Annotated[str | None, typer.Option(help="Local key")] = None,
):
    """Add an irrigator to a cluster."""
    config = {}
    if device_ip:
        config["device_ip"] = device_ip
    if local_key:
        config["local_key"] = local_key
    data = call(
        ctx,
        lambda c: c.add_irrigator(
            cluster,
            tuya_device_id=device_id,
            name=name,
            type=type,
            config=json.dumps(config) if config else None,
        ),
    )
    output(data)


@irrigator_app.command("list")
def irrigator_list(
    ctx: typer.Context,
    cluster: Annotated[int | None, typer.Option(help="Filter by cluster ID")] = None,
):
    """List irrigators."""
    if cluster:
        output(call(ctx, lambda c: c.list_irrigators(cluster)))
    else:
        clusters = call(ctx, lambda c: c.list_clusters())
        for cl in clusters:
            irrigators = call(ctx, lambda c, cid=cl["id"]: c.list_irrigators(cid))
            if irrigators:
                output({"cluster": cl["name"], "irrigators": irrigators})


@irrigator_app.command("start")
def irrigator_start(
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Irrigator ID")],
    minutes: Annotated[int | None, typer.Option(help="Duration in minutes")] = None,
):
    """Start an irrigator."""
    output(call(ctx, lambda c: c.start_irrigator(id, minutes)))


@irrigator_app.command("stop")
def irrigator_stop(ctx: typer.Context, id: Annotated[int, typer.Argument(help="Irrigator ID")]):
    """Stop an irrigator."""
    output(call(ctx, lambda c: c.stop_irrigator(id)))


@irrigator_app.command("log-manual")
def irrigator_log_manual(
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Irrigator ID")],
    minutes: Annotated[int, typer.Option(help="Duration in minutes")],
    notes: Annotated[str | None, typer.Option()] = None,
):
    """Log a manual irrigation event (no device command)."""
    output(call(ctx, lambda c: c.log_manual(id, minutes, notes)))

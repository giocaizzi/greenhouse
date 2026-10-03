"""Irrigator management and control commands."""

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import ClusterArg, ClusterOpt, YesOpt, call, output

irrigator_app = typer.Typer(help="Manage and control irrigators", no_args_is_help=True)

# Options shared by `irrigator add` and `irrigator update` (same flag, type and help in both).
DeviceIpOpt = Annotated[str | None, typer.Option(help="Local IP")]
LocalKeyOpt = Annotated[str | None, typer.Option(help="Local key")]
ReservoirOpt = Annotated[
    float | None, typer.Option(help="Usable reservoir/tank volume in liters (for vacation rationing)")
]
FlowRateOpt = Annotated[
    float | None, typer.Option(help="Measured pump throughput in liters per minute (for vacation rationing)")
]


def _device_config(device_ip: str | None, local_key: str | None) -> dict[str, str] | None:
    """Build the irrigator ``config`` blob from the local-control options; ``None`` when neither is given.

    One rule for ``add`` and ``update``: an option counts as given when it is not
    ``None`` — an explicit empty string is sent as is, so the server sees exactly what
    was typed.
    """
    if device_ip is None and local_key is None:
        return None
    config: dict[str, str] = {}
    if device_ip is not None:
        config["device_ip"] = device_ip
    if local_key is not None:
        config["local_key"] = local_key
    return config


@irrigator_app.command("add")
def irrigator_add(
    ctx: typer.Context,
    cluster: ClusterOpt,
    device_id: Annotated[str, typer.Option(help="Tuya device ID")],
    name: Annotated[str, typer.Option(help="Irrigator name")],
    type: Annotated[str, typer.Option(help="tuya_cloud or tuya_local")],
    device_ip: DeviceIpOpt = None,
    local_key: LocalKeyOpt = None,
    reservoir_l: ReservoirOpt = None,
    flow_rate_l_per_min: FlowRateOpt = None,
) -> None:
    """Add an irrigator to a cluster."""
    config = _device_config(device_ip, local_key)
    data = call(
        ctx,
        lambda c: c.add_irrigator(
            cluster,
            tuya_device_id=device_id,
            name=name,
            type=type,
            config=config,
            reservoir_l=reservoir_l,
            flow_rate_l_per_min=flow_rate_l_per_min,
        ),
    )
    output(data)


@irrigator_app.command("list")
def irrigator_list(ctx: typer.Context) -> None:
    """List every irrigator across all clusters."""
    output(call(ctx, lambda c: c.list_irrigators()))


@irrigator_app.command("show")
def irrigator_show(
    ctx: typer.Context,
    cluster: ClusterArg,
) -> None:
    """Show the cluster's irrigator. Exits non-zero if the cluster has none."""
    output(call(ctx, lambda c: c.get_irrigator(cluster)))


@irrigator_app.command("start")
def irrigator_start(
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Irrigator ID")],
    minutes: Annotated[int | None, typer.Option(help="Duration in minutes")] = None,
) -> None:
    """Start an irrigator."""
    output(call(ctx, lambda c: c.start_irrigator(id, minutes)))


@irrigator_app.command("stop")
def irrigator_stop(ctx: typer.Context, id: Annotated[int, typer.Argument(help="Irrigator ID")]) -> None:
    """Stop an irrigator."""
    output(call(ctx, lambda c: c.stop_irrigator(id)))


@irrigator_app.command("log-manual")
def irrigator_log_manual(
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Irrigator ID")],
    minutes: Annotated[int, typer.Option(help="Duration in minutes")],
    notes: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Log a manual irrigation event (no device command)."""
    output(call(ctx, lambda c: c.log_manual(id, minutes, notes)))


@irrigator_app.command("update")
def irrigator_update(
    ctx: typer.Context,
    cluster: ClusterArg,
    name: Annotated[str | None, typer.Option(help="New irrigator name")] = None,
    type: Annotated[str | None, typer.Option(help="tuya_cloud or tuya_local")] = None,
    device_ip: DeviceIpOpt = None,
    local_key: LocalKeyOpt = None,
    reservoir_l: ReservoirOpt = None,
    flow_rate_l_per_min: FlowRateOpt = None,
) -> None:
    """Patch the cluster's irrigator. Only the supplied fields are sent.

    ``--device-ip`` or ``--local-key`` overwrite the ``config`` blob; pass both
    when switching a device to local control.
    """
    config = _device_config(device_ip, local_key)
    output(
        call(
            ctx,
            lambda c: c.update_irrigator(
                cluster,
                name=name,
                type=type,
                config=config,
                reservoir_l=reservoir_l,
                flow_rate_l_per_min=flow_rate_l_per_min,
            ),
        )
    )


@irrigator_app.command("delete")
def irrigator_delete(
    ctx: typer.Context,
    cluster: ClusterArg,
    yes: YesOpt = False,
) -> None:
    """Delete the cluster's irrigator. Historic events stay attached to the cluster."""
    if not yes:
        typer.confirm(f"Delete the irrigator from cluster {cluster}?", abort=True)
    output(call(ctx, lambda c: c.delete_irrigator(cluster)))

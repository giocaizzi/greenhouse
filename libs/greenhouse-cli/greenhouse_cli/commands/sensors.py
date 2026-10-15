"""Sensor management commands."""

from operator import methodcaller
from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import (
    ArchivedOpt,
    ClusterFilterOpt,
    ClusterOpt,
    HelpOnBareGroup,
    PurgeOpt,
    YesOpt,
    call,
    output,
)

sensor_app = typer.Typer(help="Manage sensors", no_args_is_help=True, cls=HelpOnBareGroup)


@sensor_app.command("add")
def sensor_add(  # noqa: PLR0913 — one parameter per CLI option/argument: Typer has no option groups
    ctx: typer.Context,
    *,
    cluster: ClusterOpt,
    device_id: Annotated[str, typer.Option(help="Tuya device ID")],
    name: Annotated[str, typer.Option(help="Sensor name")],
    type: Annotated[str, typer.Option(help="Device model key (tuya.tr301z)")],  # noqa: A002 — the Python name is the click parameter name (tests/golden/cli/params.json)
    plant_id: Annotated[int | None, typer.Option(help="Associated plant ID")] = None,
) -> None:
    """Add a sensor to a cluster."""
    output(
        call(ctx, lambda c: c.add_sensor(cluster, tuya_device_id=device_id, name=name, type=type, plant_id=plant_id))
    )


@sensor_app.command("list")
def sensor_list(
    ctx: typer.Context,
    cluster: ClusterFilterOpt = None,
    *,
    archived: ArchivedOpt = False,
) -> None:
    """List sensors."""
    if cluster is not None:
        output(call(ctx, lambda c: c.list_sensors(cluster, include_archived=archived)))
    else:
        clusters = call(ctx, lambda c: c.list_clusters())
        for cl in clusters:
            sensors = call(ctx, methodcaller("list_sensors", cl["id"], include_archived=archived))
            if sensors:
                output({"cluster": cl["name"], "sensors": sensors})


@sensor_app.command("update")
def sensor_update(  # noqa: PLR0913 — one parameter per CLI option/argument: Typer has no option groups
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Sensor ID")],  # noqa: A002 — the Python name is the click parameter name (tests/golden/cli/params.json)
    *,
    cluster: Annotated[int, typer.Option(help="Cluster the sensor belongs to")],
    name: Annotated[str | None, typer.Option(help="New sensor name")] = None,
    type: Annotated[str | None, typer.Option(help="Device model key (tuya.tr301z)")] = None,  # noqa: A002 — the Python name is the click parameter name (tests/golden/cli/params.json)
    plant_id: Annotated[int | None, typer.Option(help="Reassign to a different plant")] = None,
) -> None:
    """Patch sensor metadata. Only the supplied fields are sent."""
    output(
        call(
            ctx,
            lambda c: c.update_sensor(cluster, id, name=name, type=type, plant_id=plant_id),
        )
    )


@sensor_app.command("move")
def sensor_move(  # noqa: PLR0913 — one parameter per CLI option/argument: Typer has no option groups
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Sensor ID")],  # noqa: A002 — the Python name is the click parameter name (tests/golden/cli/params.json)
    *,
    cluster: Annotated[int, typer.Option(help="Cluster the sensor belongs to")],
    plant_id: Annotated[int | None, typer.Option(help="Plant the sensor now measures")] = None,
    detach: Annotated[bool, typer.Option(help="Detach the sensor from its plant instead")] = False,
    at: Annotated[int | None, typer.Option(help="When it was moved (Unix seconds, default now)")] = None,
) -> None:
    """Record that a sensor was moved to another plant (or detached), and when.

    Readings before --at stay with the old plant. Naming the plant the sensor is
    already on re-dates its last move. Give exactly one of --plant-id / --detach.
    """
    if (plant_id is not None) == detach:
        msg = "give exactly one of --plant-id or --detach"
        raise typer.BadParameter(msg)
    output(call(ctx, lambda c: c.move_sensor(cluster, id, plant_id, effective_at=at)))


@sensor_app.command("restore")
def sensor_restore(
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Sensor ID")],  # noqa: A002 — the Python name is the click parameter name (tests/golden/cli/params.json)
    *,
    cluster: Annotated[int, typer.Option(help="Cluster the sensor belongs to")],
) -> None:
    """Bring an archived sensor back, re-linked to its plant if that plant is still active."""
    output(call(ctx, lambda c: c.restore_sensor(cluster, id)))


@sensor_app.command("delete")
def sensor_delete(
    ctx: typer.Context,
    *,
    id: Annotated[int, typer.Argument(help="Sensor ID")],  # noqa: A002 — the Python name is the click parameter name (tests/golden/cli/params.json)
    cluster: Annotated[int, typer.Option(help="Cluster the sensor belongs to")],
    purge: PurgeOpt = False,
    yes: YesOpt = False,
) -> None:
    """Archive a sensor (restore undoes it); --purge deletes an archived one and its readings for good."""
    if not yes:
        verb = "Permanently delete" if purge else "Archive"
        typer.confirm(f"{verb} sensor {id} in cluster {cluster}?", abort=True)
    output(call(ctx, lambda c: c.delete_sensor(cluster, id, purge=purge)))

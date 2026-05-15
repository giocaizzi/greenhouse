"""Sensor management commands."""

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output

sensor_app = typer.Typer(help="Manage sensors", no_args_is_help=True)


@sensor_app.command("add")
def sensor_add(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Option(help="Cluster ID")],
    device_id: Annotated[str, typer.Option(help="Tuya device ID")],
    name: Annotated[str, typer.Option(help="Sensor name")],
    type: Annotated[str, typer.Option(help="soil_moisture, temp_humidity, or light")],
    plant_id: Annotated[int | None, typer.Option(help="Associated plant ID")] = None,
):
    """Add a sensor to a cluster."""
    output(
        call(ctx, lambda c: c.add_sensor(cluster, tuya_device_id=device_id, name=name, type=type, plant_id=plant_id))
    )


@sensor_app.command("list")
def sensor_list(
    ctx: typer.Context,
    cluster: Annotated[int | None, typer.Option(help="Filter by cluster ID")] = None,
):
    """List sensors."""
    if cluster:
        output(call(ctx, lambda c: c.list_sensors(cluster)))
    else:
        clusters = call(ctx, lambda c: c.list_clusters())
        for cl in clusters:
            sensors = call(ctx, lambda c, cid=cl["id"]: c.list_sensors(cid))
            if sensors:
                output({"cluster": cl["name"], "sensors": sensors})


@sensor_app.command("update")
def sensor_update(
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Sensor ID")],
    cluster: Annotated[int, typer.Option(help="Cluster the sensor belongs to")],
    name: Annotated[str | None, typer.Option(help="New sensor name")] = None,
    type: Annotated[str | None, typer.Option(help="soil_moisture, temp_humidity, or light")] = None,
    plant_id: Annotated[int | None, typer.Option(help="Reassign to a different plant")] = None,
):
    """Patch sensor metadata. Only the supplied fields are sent."""
    output(
        call(
            ctx,
            lambda c: c.update_sensor(cluster, id, name=name, type=type, plant_id=plant_id),
        )
    )


@sensor_app.command("delete")
def sensor_delete(
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Sensor ID")],
    cluster: Annotated[int, typer.Option(help="Cluster the sensor belongs to")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")] = False,
):
    """Delete a sensor. Historic readings stay attached to the cluster."""
    if not yes:
        typer.confirm(f"Delete sensor {id} from cluster {cluster}?", abort=True)
    output(call(ctx, lambda c: c.delete_sensor(cluster, id)))

"""Plant management commands."""

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output

plant_app = typer.Typer(help="Manage plants", no_args_is_help=True)


@plant_app.command("add")
def plant_add(
    ctx: typer.Context,
    species: Annotated[str, typer.Argument(help="Species name")],
    cluster: Annotated[int, typer.Option(help="Cluster ID")],
    category: Annotated[str | None, typer.Option()] = None,
    water_needs: Annotated[str | None, typer.Option(help="low/medium/high")] = None,
    light_needs: Annotated[str | None, typer.Option(help="low/medium/high")] = None,
    temp_min: Annotated[float | None, typer.Option()] = None,
    temp_max: Annotated[float | None, typer.Option()] = None,
    humidity_min: Annotated[float | None, typer.Option()] = None,
    humidity_max: Annotated[float | None, typer.Option()] = None,
    notes: Annotated[str | None, typer.Option()] = None,
):
    """Add a plant to a cluster."""
    data = call(
        ctx,
        lambda c: c.add_plant(
            cluster,
            species=species,
            category=category,
            water_needs=water_needs,
            light_needs=light_needs,
            ideal_temp_min=temp_min,
            ideal_temp_max=temp_max,
            ideal_humidity_min=humidity_min,
            ideal_humidity_max=humidity_max,
            notes=notes,
        ),
    )
    output(data)


@plant_app.command("list")
def plant_list(
    ctx: typer.Context,
    cluster: Annotated[int | None, typer.Option(help="Filter by cluster ID")] = None,
):
    """List plants."""
    if cluster:
        output(call(ctx, lambda c: c.list_plants(cluster)))
    else:
        clusters = call(ctx, lambda c: c.list_clusters())
        for cl in clusters:
            plants = call(ctx, lambda c, cid=cl["id"]: c.list_plants(cid))
            if plants:
                output({"cluster": cl["name"], "plants": plants})


@plant_app.command("sync")
def plant_sync(
    ctx: typer.Context,
    plant_id: Annotated[int | None, typer.Option(help="Sync specific plant")] = None,
    cluster: Annotated[int | None, typer.Option(help="Sync plants in cluster")] = None,
):
    """Sync plants with evidence-based care data."""
    output(call(ctx, lambda c: c.sync_plants(plant_id=plant_id, cluster_id=cluster)))

"""Plant management commands."""

from operator import methodcaller
from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import ClusterFilterOpt, ClusterOpt, YesOpt, call, output

plant_app = typer.Typer(help="Manage plants", no_args_is_help=True)

# Option types of the add/update pair; each flag comes from its parameter name (--temp-min, --notes, …).
LevelOpt = Annotated[str | None, typer.Option(help="low/medium/high")]
TextOpt = Annotated[str | None, typer.Option()]
NumberOpt = Annotated[float | None, typer.Option()]


@plant_app.command("add")
def plant_add(
    ctx: typer.Context,
    species: Annotated[str, typer.Argument(help="Species name")],
    cluster: ClusterOpt,
    category: TextOpt = None,
    water_needs: LevelOpt = None,
    light_needs: LevelOpt = None,
    temp_min: NumberOpt = None,
    temp_max: NumberOpt = None,
    humidity_min: NumberOpt = None,
    humidity_max: NumberOpt = None,
    notes: TextOpt = None,
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
    cluster: ClusterFilterOpt = None,
):
    """List plants."""
    if cluster:
        output(call(ctx, lambda c: c.list_plants(cluster)))
    else:
        clusters = call(ctx, lambda c: c.list_clusters())
        for cl in clusters:
            plants = call(ctx, methodcaller("list_plants", cl["id"]))
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


@plant_app.command("move")
def plant_move(
    ctx: typer.Context,
    plant_id: Annotated[int, typer.Argument(help="Plant ID to move")],
    to_cluster: Annotated[int, typer.Option("--to-cluster", help="Target cluster ID")],
):
    """Move a plant to a different cluster.

    Plant identity, health history, and learning profile follow the plant.
    Decision logs, irrigation events, and alerts stay with the original cluster.
    """
    output(call(ctx, lambda c: c.move_plant(plant_id, to_cluster)))


@plant_app.command("update")
def plant_update(
    ctx: typer.Context,
    plant_id: Annotated[int, typer.Argument(help="Plant ID")],
    cluster: Annotated[int, typer.Option(help="Cluster the plant belongs to")],
    species: TextOpt = None,
    category: TextOpt = None,
    water_needs: LevelOpt = None,
    light_needs: LevelOpt = None,
    temp_min: NumberOpt = None,
    temp_max: NumberOpt = None,
    humidity_min: NumberOpt = None,
    humidity_max: NumberOpt = None,
    notes: TextOpt = None,
):
    """Patch plant metadata. Only the supplied fields are sent."""
    output(
        call(
            ctx,
            lambda c: c.update_plant(
                cluster,
                plant_id,
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
    )


@plant_app.command("delete")
def plant_delete(
    ctx: typer.Context,
    plant_id: Annotated[int, typer.Argument(help="Plant ID")],
    cluster: Annotated[int, typer.Option(help="Cluster the plant belongs to")],
    yes: YesOpt = False,
):
    """Delete a plant and its health / learning history."""
    if not yes:
        typer.confirm(f"Delete plant {plant_id} from cluster {cluster}?", abort=True)
    output(call(ctx, lambda c: c.delete_plant(cluster, plant_id)))

"""CLI entry point — thin Typer client that talks to the tuya-irrigation server API."""

from typing import Annotated

import typer

from tuya_irrigation_cli.commands.clusters import cluster_app
from tuya_irrigation_cli.commands.configs import config_app
from tuya_irrigation_cli.commands.irrigators import irrigator_app
from tuya_irrigation_cli.commands.operations import register as register_operations
from tuya_irrigation_cli.commands.plants import plant_app
from tuya_irrigation_cli.commands.sensors import sensor_app

app = typer.Typer(
    help="Smart irrigation system CLI",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.callback()
def main(
    ctx: typer.Context,
    server: Annotated[
        str | None,
        typer.Option(help="Server URL (default: $IRRIGATION_SERVER_URL or http://localhost:8000)"),
    ] = None,
):
    """Smart irrigation system — evidence-based plant care with Tuya IoT sensors."""
    ctx.ensure_object(dict)
    ctx.obj = server


# Register operation commands directly on app
register_operations(app)

# Register sub-apps
app.add_typer(cluster_app, name="cluster")
app.add_typer(plant_app, name="plant")
app.add_typer(irrigator_app, name="irrigator")
app.add_typer(sensor_app, name="sensor")
app.add_typer(config_app, name="config")

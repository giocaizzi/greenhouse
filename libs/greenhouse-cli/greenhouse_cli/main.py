"""CLI entry point — thin Typer client that talks to the greenhouse server API."""

from typing import Annotated

import typer

from greenhouse_cli.commands.alerts import alerts_app
from greenhouse_cli.commands.auth import register as register_auth
from greenhouse_cli.commands.clusters import cluster_app
from greenhouse_cli.commands.configs import config_app
from greenhouse_cli.commands.decisions import decisions_app
from greenhouse_cli.commands.irrigators import irrigator_app
from greenhouse_cli.commands.operations import register as register_operations
from greenhouse_cli.commands.plants import plant_app
from greenhouse_cli.commands.preferences import prefs_app
from greenhouse_cli.commands.scheduler import scheduler_app
from greenhouse_cli.commands.sensors import sensor_app
from greenhouse_cli.commands.vacation import vacation_app
from greenhouse_cli.commands.windows import windows_app

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


# Register operation + auth commands directly on app
register_operations(app)
register_auth(app)

# Register sub-apps
app.add_typer(cluster_app, name="cluster")
app.add_typer(plant_app, name="plant")
app.add_typer(irrigator_app, name="irrigator")
app.add_typer(sensor_app, name="sensor")
app.add_typer(config_app, name="config")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(alerts_app, name="alerts")
app.add_typer(decisions_app, name="decisions")
app.add_typer(prefs_app, name="prefs")
app.add_typer(vacation_app, name="vacation")
app.add_typer(windows_app, name="windows")

"""CLI entry point — thin Typer client that talks to the tuya-irrigation server API."""

import json
import os
from typing import Annotated

import typer
from rich import print_json

from tuya_irrigation_cli.client import IrrigationClient, ServerError

app = typer.Typer(
    help="Smart irrigation system CLI",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _client(ctx: typer.Context) -> IrrigationClient:
    server = ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", "http://localhost:8000")
    return IrrigationClient(base_url=server)


def _call(ctx: typer.Context, fn, *args, **kwargs):
    """Call a client method with error handling. Returns the result or exits on error."""
    try:
        return fn(_client(ctx), *args, **kwargs)
    except ServerError as e:
        typer.echo(f"Error: {e.detail}", err=True)
        raise typer.Exit(1) from None


def _output(data):
    print_json(json.dumps(data, default=str))


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


# ── Operation Commands ───────────────────────────────────────────────────────


@app.command()
def status(ctx: typer.Context, cluster: Annotated[int, typer.Argument(help="Cluster ID")]):
    """Full cluster overview: sensors, config, decision, alerts."""
    _output(_call(ctx, lambda c: c.status(cluster)))


@app.command()
def irrigate(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Argument(help="Cluster ID")],
    temp: Annotated[float | None, typer.Option(help="Override temperature (skips sync + weather)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Analyze only, don't execute")] = False,
    no_sync: Annotated[bool, typer.Option("--no-sync", help="Skip sensor sync")] = False,
):
    """Smart irrigation: sync sensors → fetch weather → decide → execute."""
    data = _call(ctx, lambda c: c.irrigate(cluster, temp_override=temp, dry_run=dry_run, no_sync=no_sync))
    _output(data)
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

    data = _call(ctx, lambda c: c.check() if all_clusters else c.check(cluster))
    _output(data)

    if isinstance(data, dict):
        if data.get("has_alerts"):
            raise typer.Exit(2)
        if data.get("action") == "error":
            raise typer.Exit(1)


@app.command()
def monitor(ctx: typer.Context, cluster: Annotated[int, typer.Argument(help="Cluster ID")]):
    """Raw moisture check for sensor-only clusters."""
    data = _call(ctx, lambda c: c.monitor(cluster))
    _output(data)
    if data.get("needs_water"):
        raise typer.Exit(2)


@app.command()
def sync(
    ctx: typer.Context,
    hours: Annotated[int, typer.Option(help="History window in hours")] = 24,
):
    """Sync sensor data from Tuya Cloud."""
    _output(_call(ctx, lambda c: c.sync(hours=hours)))


@app.command()
def learn(ctx: typer.Context, cluster: Annotated[int, typer.Argument(help="Cluster ID")]):
    """Learning report: efficiency analysis and pattern detection."""
    _output(_call(ctx, lambda c: c.learn(cluster)))


@app.command()
def history(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Argument(help="Cluster ID")],
    hours: Annotated[int, typer.Option(help="Hours of history")] = 24,
    limit: Annotated[int, typer.Option(help="Max entries per section")] = 50,
):
    """Sensor readings + irrigation events timeline."""
    _output(_call(ctx, lambda c: c.history(cluster, hours=hours, limit=limit)))


@app.command()
def stats(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Argument(help="Cluster ID")],
    days: Annotated[int, typer.Option(help="Days to analyze")] = 7,
    export: Annotated[str | None, typer.Option(help="Export CSV to file")] = None,
):
    """Irrigation statistics and CSV export."""
    if export:
        csv_data = _call(ctx, lambda c: c.stats_export(cluster, days=days))
        with open(export, "w") as f:
            f.write(csv_data)
        typer.echo(f"Exported to {export}")
    else:
        _output(_call(ctx, lambda c: c.stats(cluster, days=days)))


@app.command()
def health(ctx: typer.Context):
    """Server health and scheduler status."""
    _output(_call(ctx, lambda c: c.health()))


# ── Cluster Commands ─────────────────────────────────────────────────────────

cluster_app = typer.Typer(help="Manage plant clusters", no_args_is_help=True)
app.add_typer(cluster_app, name="cluster")


@cluster_app.command("add")
def cluster_add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Cluster name")],
    location: Annotated[str | None, typer.Option(help="Location description")] = None,
    environment: Annotated[str, typer.Option(help="indoor or outdoor")] = "indoor",
):
    """Add a new cluster."""
    _output(_call(ctx, lambda c: c.create_cluster(name, location, environment)))


@cluster_app.command("list")
def cluster_list(ctx: typer.Context):
    """List all clusters."""
    _output(_call(ctx, lambda c: c.list_clusters()))


# ── Plant Commands ───────────────────────────────────────────────────────────

plant_app = typer.Typer(help="Manage plants", no_args_is_help=True)
app.add_typer(plant_app, name="plant")


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
    data = _call(
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
    _output(data)


@plant_app.command("list")
def plant_list(
    ctx: typer.Context,
    cluster: Annotated[int | None, typer.Option(help="Filter by cluster ID")] = None,
):
    """List plants."""
    if cluster:
        _output(_call(ctx, lambda c: c.list_plants(cluster)))
    else:
        clusters = _call(ctx, lambda c: c.list_clusters())
        for cl in clusters:
            plants = _call(ctx, lambda c, cid=cl["id"]: c.list_plants(cid))
            if plants:
                _output({"cluster": cl["name"], "plants": plants})


@plant_app.command("sync")
def plant_sync(
    ctx: typer.Context,
    plant_id: Annotated[int | None, typer.Option(help="Sync specific plant")] = None,
    cluster: Annotated[int | None, typer.Option(help="Sync plants in cluster")] = None,
):
    """Sync plants with evidence-based care data."""
    _output(_call(ctx, lambda c: c.sync_plants(plant_id=plant_id, cluster_id=cluster)))


# ── Irrigator Commands ───────────────────────────────────────────────────────

irrigator_app = typer.Typer(help="Manage and control irrigators", no_args_is_help=True)
app.add_typer(irrigator_app, name="irrigator")


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
    data = _call(
        ctx,
        lambda c: c.add_irrigator(
            cluster,
            tuya_device_id=device_id,
            name=name,
            type=type,
            config=json.dumps(config) if config else None,
        ),
    )
    _output(data)


@irrigator_app.command("list")
def irrigator_list(
    ctx: typer.Context,
    cluster: Annotated[int | None, typer.Option(help="Filter by cluster ID")] = None,
):
    """List irrigators."""
    if cluster:
        _output(_call(ctx, lambda c: c.list_irrigators(cluster)))
    else:
        clusters = _call(ctx, lambda c: c.list_clusters())
        for cl in clusters:
            irrigators = _call(ctx, lambda c, cid=cl["id"]: c.list_irrigators(cid))
            if irrigators:
                _output({"cluster": cl["name"], "irrigators": irrigators})


@irrigator_app.command("start")
def irrigator_start(
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Irrigator ID")],
    minutes: Annotated[int | None, typer.Option(help="Duration in minutes")] = None,
):
    """Start an irrigator."""
    _output(_call(ctx, lambda c: c.start_irrigator(id, minutes)))


@irrigator_app.command("stop")
def irrigator_stop(ctx: typer.Context, id: Annotated[int, typer.Argument(help="Irrigator ID")]):
    """Stop an irrigator."""
    _output(_call(ctx, lambda c: c.stop_irrigator(id)))


@irrigator_app.command("log-manual")
def irrigator_log_manual(
    ctx: typer.Context,
    id: Annotated[int, typer.Argument(help="Irrigator ID")],
    minutes: Annotated[int, typer.Option(help="Duration in minutes")],
    notes: Annotated[str | None, typer.Option()] = None,
):
    """Log a manual irrigation event (no device command)."""
    _output(_call(ctx, lambda c: c.log_manual(id, minutes, notes)))


# ── Sensor Commands ──────────────────────────────────────────────────────────

sensor_app = typer.Typer(help="Manage sensors", no_args_is_help=True)
app.add_typer(sensor_app, name="sensor")


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
    _output(
        _call(ctx, lambda c: c.add_sensor(cluster, tuya_device_id=device_id, name=name, type=type, plant_id=plant_id))
    )


@sensor_app.command("list")
def sensor_list(
    ctx: typer.Context,
    cluster: Annotated[int | None, typer.Option(help="Filter by cluster ID")] = None,
):
    """List sensors."""
    if cluster:
        _output(_call(ctx, lambda c: c.list_sensors(cluster)))
    else:
        clusters = _call(ctx, lambda c: c.list_clusters())
        for cl in clusters:
            sensors = _call(ctx, lambda c, cid=cl["id"]: c.list_sensors(cid))
            if sensors:
                _output({"cluster": cl["name"], "sensors": sensors})


# ── Config Commands ──────────────────────────────────────────────────────────

config_app = typer.Typer(help="Irrigation configuration", no_args_is_help=True)
app.add_typer(config_app, name="config")


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Option(help="Cluster ID")],
    mode: Annotated[str, typer.Option(help="manual, schedule, or smart")],
    minutes: Annotated[int | None, typer.Option(help="Duration in minutes")] = None,
    interval: Annotated[int | None, typer.Option(help="Interval in hours")] = None,
    auto_run: Annotated[bool, typer.Option(help="Enable auto-run")] = True,
):
    """Set irrigation config for a cluster."""
    data = _call(
        ctx,
        lambda c: c.set_config(
            cluster,
            mode=mode,
            duration_minutes=minutes,
            interval_hours=interval,
            auto_run=auto_run,
        ),
    )
    _output(data)


@config_app.command("get")
def config_get(ctx: typer.Context, cluster: Annotated[int, typer.Option(help="Cluster ID")]):
    """Get irrigation config for a cluster."""
    _output(_call(ctx, lambda c: c.get_config(cluster)))

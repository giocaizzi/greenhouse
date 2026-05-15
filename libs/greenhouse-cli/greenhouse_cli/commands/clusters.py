"""Cluster management commands."""

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output

cluster_app = typer.Typer(help="Manage plant clusters", no_args_is_help=True)


@cluster_app.command("add")
def cluster_add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Cluster name")],
    location: Annotated[str | None, typer.Option(help="Location description")] = None,
    environment: Annotated[str, typer.Option(help="indoor or outdoor")] = "indoor",
):
    """Add a new cluster."""
    output(call(ctx, lambda c: c.create_cluster(name, location, environment)))


@cluster_app.command("list")
def cluster_list(ctx: typer.Context):
    """List all clusters."""
    output(call(ctx, lambda c: c.list_clusters()))


@cluster_app.command("get")
def cluster_get(
    ctx: typer.Context,
    cluster_id: Annotated[int, typer.Argument(help="Cluster ID")],
):
    """Fetch one cluster by ID."""
    output(call(ctx, lambda c: c.get_cluster(cluster_id)))


@cluster_app.command("update")
def cluster_update(
    ctx: typer.Context,
    cluster_id: Annotated[int, typer.Argument(help="Cluster ID")],
    name: Annotated[str | None, typer.Option(help="New cluster name")] = None,
    location: Annotated[str | None, typer.Option(help="New location description")] = None,
    environment: Annotated[str | None, typer.Option(help="indoor or outdoor")] = None,
):
    """Update cluster metadata. Only the supplied fields are sent."""
    output(
        call(
            ctx,
            lambda c: c.update_cluster(cluster_id, name=name, location=location, environment=environment),
        )
    )


@cluster_app.command("delete")
def cluster_delete(
    ctx: typer.Context,
    cluster_id: Annotated[int, typer.Argument(help="Cluster ID")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")] = False,
):
    """Delete a cluster and all of its children (plants, sensors, irrigators, history)."""
    if not yes:
        typer.confirm(
            f"Delete cluster {cluster_id} and all its plants, sensors, irrigators, and history?",
            abort=True,
        )
    output(call(ctx, lambda c: c.delete_cluster(cluster_id)))

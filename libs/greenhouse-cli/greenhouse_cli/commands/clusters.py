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

"""Shared CLI helpers for command modules."""

import json
import os
from collections.abc import Callable
from typing import Annotated, Any

import typer
from rich import print_json

from greenhouse_cli.client import IrrigationClient, ServerError
from greenhouse_cli.constants import DEFAULT_SERVER_URL

# Typer parameter types shared by several commands; Typer copies the ParameterInfo per use, so
# reusing one alias yields the same option/argument (and the same --help line) as writing it out.
ClusterArg = Annotated[int, typer.Argument(help="Cluster ID")]
ClusterOpt = Annotated[int, typer.Option(help="Cluster ID")]
ClusterFilterOpt = Annotated[int | None, typer.Option(help="Filter by cluster ID")]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")]


def server_url(ctx: typer.Context) -> str:
    """The server URL: ``--server`` (stored on ``ctx.obj``), else $IRRIGATION_SERVER_URL, else the default."""
    return ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", DEFAULT_SERVER_URL)


def get_client(ctx: typer.Context) -> IrrigationClient:
    """Get an IrrigationClient from the Typer context."""
    return IrrigationClient(base_url=server_url(ctx))


def call(ctx: typer.Context, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a client method with error handling. Returns the result or exits on error."""
    try:
        return fn(get_client(ctx), *args, **kwargs)
    except ServerError as e:
        typer.echo(f"Error: {e.detail}", err=True)
        raise typer.Exit(1) from None


def output(data: Any) -> None:
    """Pretty-print JSON data."""
    print_json(json.dumps(data, default=str))

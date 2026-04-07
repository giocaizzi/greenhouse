"""Shared CLI helpers for command modules."""

import json
import os

import typer
from rich import print_json

from tuya_irrigation_cli.client import IrrigationClient, ServerError


def get_client(ctx: typer.Context) -> IrrigationClient:
    """Get an IrrigationClient from the Typer context."""
    server = ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", "http://localhost:8000")
    return IrrigationClient(base_url=server)


def call(ctx: typer.Context, fn, *args, **kwargs):
    """Call a client method with error handling. Returns the result or exits on error."""
    try:
        return fn(get_client(ctx), *args, **kwargs)
    except ServerError as e:
        typer.echo(f"Error: {e.detail}", err=True)
        raise typer.Exit(1) from None


def output(data):
    """Pretty-print JSON data."""
    print_json(json.dumps(data, default=str))

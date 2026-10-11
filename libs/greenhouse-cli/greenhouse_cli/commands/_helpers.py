"""Shared CLI helpers for command modules."""

import json
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, TypeVar

import typer
from rich import print_json
from typer.core import TyperGroup

from greenhouse_cli.client import IrrigationClient, ServerError
from greenhouse_cli.constants import DEFAULT_SERVER_URL

if TYPE_CHECKING:
    # Typer vendors Click; its group API is typed against the vendored Context.
    from typer._click import Context as ClickContext

# Typer parameter types shared by several commands; Typer copies the ParameterInfo per use, so
# reusing one alias yields the same option/argument (and the same --help line) as writing it out.
ClusterArg = Annotated[int, typer.Argument(help="Cluster ID")]
ClusterOpt = Annotated[int, typer.Option(help="Cluster ID")]
ClusterFilterOpt = Annotated[int | None, typer.Option(help="Filter by cluster ID")]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")]

T = TypeVar("T")


class HelpOnBareGroup(TyperGroup):
    """A command group that, run without arguments, prints its help and exits 0.

    Typer's ``no_args_is_help`` means "show the help page"; since Click 8.2 that
    page is raised as a usage error (exit 2), which made ``greenhouse cluster``
    look like a failure. A bare group now behaves exactly like ``--help``.
    """

    def parse_args(self, ctx: "ClickContext", args: list[str]) -> list[str]:
        """Print help and exit 0 for a bare invocation; otherwise parse as usual."""
        if not args and self.no_args_is_help and not ctx.resilient_parsing:
            typer.echo(ctx.get_help(), color=ctx.color)
            ctx.exit(0)
        return super().parse_args(ctx, args)


def server_url(ctx: typer.Context) -> str:
    """The server URL: ``--server`` (stored on ``ctx.obj``), else $IRRIGATION_SERVER_URL, else the default."""
    return ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", DEFAULT_SERVER_URL)


def get_client(ctx: typer.Context) -> IrrigationClient:
    """A client for the resolved server URL; it sends the stored token when there is one."""
    return IrrigationClient(base_url=server_url(ctx))


def call(ctx: typer.Context, fn: Callable[[IrrigationClient], T]) -> T:
    """Run ``fn`` against a fresh client and return its result; a server error prints and exits 1."""
    try:
        with get_client(ctx) as client:
            return fn(client)
    except ServerError as e:
        typer.echo(f"Error: {e.detail}", err=True)
        raise typer.Exit(1) from None


def output(data: Any) -> None:
    """Print ``data`` as pretty JSON on stdout (the CLI output contract); non-JSON values go through ``str``."""
    print_json(json.dumps(data, default=str))

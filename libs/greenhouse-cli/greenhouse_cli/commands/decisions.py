"""Decision log commands."""

from __future__ import annotations

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output

decisions_app = typer.Typer(help="Inspect the irrigation decision log", no_args_is_help=True)


@decisions_app.command("list")
def decisions_list(
    ctx: typer.Context,
    cluster: Annotated[int, typer.Option(help="Cluster ID")],
    limit: Annotated[int, typer.Option(min=1, max=200, help="Max entries")] = 50,
):
    """List recent decision-engine evaluations for a cluster.

    Every call to the engine writes a row regardless of whether it actuated
    — use this to audit "why did you skip at 3am?".
    """
    output(call(ctx, lambda c: c.list_decisions(cluster, limit=limit)))

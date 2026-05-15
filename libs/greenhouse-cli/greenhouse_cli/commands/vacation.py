"""Vacation window commands."""

from __future__ import annotations

from typing import Annotated

import typer

from greenhouse_cli.commands._helpers import call, output

vacation_app = typer.Typer(help="Manage vacation windows (engine holds during these)", no_args_is_help=True)


@vacation_app.command("list")
def vacation_list(ctx: typer.Context):
    """List vacation windows together with the active one (if any)."""
    output(call(ctx, lambda c: c.list_vacation()))


@vacation_app.command("add")
def vacation_add(
    ctx: typer.Context,
    starts_at: Annotated[int, typer.Option("--starts-at", help="Window start as Unix timestamp (seconds)")],
    ends_at: Annotated[int, typer.Option("--ends-at", help="Window end as Unix timestamp (seconds)")],
    email: Annotated[str | None, typer.Option(help="Contact email to surface in alerts")] = None,
    notes: Annotated[str | None, typer.Option(help="Free-text notes")] = None,
):
    """Schedule a vacation window during which the engine will hold.

    Args:
        starts_at: Unix timestamp (seconds) for the window start.
        ends_at: Unix timestamp (seconds) for the window end.
        email: Optional contact for alerts raised while you're away.
        notes: Optional free-text notes.
    """
    output(
        call(
            ctx,
            lambda c: c.add_vacation(starts_at, ends_at, contact_email=email, notes=notes),
        )
    )


@vacation_app.command("update")
def vacation_update(
    ctx: typer.Context,
    window_id: Annotated[int, typer.Argument(help="Vacation window ID")],
    starts_at: Annotated[int | None, typer.Option("--starts-at", help="New start (Unix seconds)")] = None,
    ends_at: Annotated[int | None, typer.Option("--ends-at", help="New end (Unix seconds)")] = None,
    email: Annotated[str | None, typer.Option(help="New contact email")] = None,
    notes: Annotated[str | None, typer.Option(help="New notes")] = None,
):
    """Patch a vacation window. Only supplied fields are sent.

    NOTE: ``PUT /api/v1/vacation/{id}`` is being added in a parallel branch;
    until that ships this command will report 404/405 against the server.
    """
    output(
        call(
            ctx,
            lambda c: c.update_vacation(
                window_id,
                starts_at=starts_at,
                ends_at=ends_at,
                contact_email=email,
                notes=notes,
            ),
        )
    )


@vacation_app.command("delete")
def vacation_delete(
    ctx: typer.Context,
    window_id: Annotated[int, typer.Argument(help="Vacation window ID")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")] = False,
):
    """Remove a vacation window."""
    if not yes:
        typer.confirm(f"Delete vacation window {window_id}?", abort=True)
    output(call(ctx, lambda c: c.delete_vacation(window_id)))

"""Authentication commands — login, logout, whoami."""

from __future__ import annotations

import os
from typing import Annotated

import typer

from greenhouse_cli.client import (
    IrrigationClient,
    ServerError,
    clear_stored_token,
    store_token,
)
from greenhouse_cli.commands._helpers import call, get_client, output


def _login_client(ctx: typer.Context) -> IrrigationClient:
    """Build an unauthenticated client for the login call.

    A bearer header on the login request itself would short-circuit auth
    on the server, so we deliberately pass ``token=""`` to suppress the
    on-disk token.
    """
    server = ctx.obj or os.environ.get("IRRIGATION_SERVER_URL", "http://localhost:8000")
    return IrrigationClient(base_url=server, token="")


def register(app: typer.Typer) -> None:
    """Register top-level auth commands on the main Typer app."""

    @app.command()
    def login(
        ctx: typer.Context,
        username: Annotated[str, typer.Option(prompt=True, help="Username")],
        password: Annotated[
            str,
            typer.Option(
                prompt=True,
                hide_input=True,
                confirmation_prompt=False,
                help="Password",
            ),
        ],
        print_token: Annotated[
            bool,
            typer.Option("--print-token", help="Print the JWT to stdout instead of storing it"),
        ] = False,
    ):
        """Exchange username/password for a session JWT.

        On success the JWT is written to ``~/.config/greenhouse/token`` with
        mode 600 (or ``$XDG_CONFIG_HOME/greenhouse/token``) and used for
        subsequent CLI calls. Use ``--print-token`` to skip persistence and
        emit the token to stdout — handy for piping into ``$GREENHOUSE_API_TOKEN``.
        """
        try:
            data = _login_client(ctx).login(username, password)
        except ServerError as e:
            typer.echo(f"Error: {e.detail}", err=True)
            raise typer.Exit(1) from None

        token = data.get("access_token", "")
        if print_token:
            typer.echo(token)
            return

        if not token:
            typer.echo("Server returned an empty token (auth may be disabled).")
            return

        path = store_token(token)
        typer.echo(f"Logged in as {data.get('username', username)}. Token stored at {path}.")

    @app.command()
    def logout(ctx: typer.Context):
        """Clear the cached session token and notify the server.

        Deletes ``~/.config/greenhouse/token`` (or the ``$XDG_CONFIG_HOME``
        equivalent). Best-effort server logout — a missing/expired token
        does not block the local cleanup.
        """
        try:
            get_client(ctx).logout()
        except ServerError:
            pass
        removed = clear_stored_token()
        if removed:
            typer.echo("Logged out — token removed.")
        else:
            typer.echo("No token was stored; nothing to remove.")

    @app.command()
    def whoami(ctx: typer.Context):
        """Print the currently-authenticated user."""
        output(call(ctx, lambda c: c.whoami()))

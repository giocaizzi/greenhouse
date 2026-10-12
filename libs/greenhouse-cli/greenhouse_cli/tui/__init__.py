"""Full-screen terminal UI (``greenhouse tui``) built on Textual.

A pure HTTP client of the server's ``/api/v1`` — same transport, auth and
server-URL resolution as the rest of the CLI.
"""

from greenhouse_cli.tui.app import GreenhouseApp


def run(server_url: str, refresh_seconds: float = 30.0, animations: bool = True) -> None:
    """Launch the TUI against ``server_url`` and block until the user quits."""
    GreenhouseApp(server_url, refresh_seconds=refresh_seconds, animations=animations).run()

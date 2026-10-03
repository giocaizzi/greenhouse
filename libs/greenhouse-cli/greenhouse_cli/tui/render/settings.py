"""Settings screen: account line, preferences, global defaults and vacations."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from greenhouse_cli.tui import formatting as fmt
from greenhouse_cli.tui.render._rows import Row


def account_line(who: str | None, server_url: str) -> str:
    """Settings account line (markup): who is signed in where, or that auth is off / nobody is signed in."""
    return (
        f"Signed in as [b]{who}[/b] on {server_url}   [dim]O: log out[/dim]"
        if who
        else f"Server {server_url}  [dim](auth disabled or not signed in)[/dim]"
    )


def preference_rows(prefs: dict[str, Any]) -> list[tuple[str, str]]:
    """Preference rows sorted by key, or a single "unavailable" row."""
    return [(k, str(v)) for k, v in sorted(prefs.items())] or [("preferences", "unavailable")]


def global_config_rows(config: dict[str, Any]) -> list[tuple[str, str | Text]]:
    """Global-default rows in repository field order (bookkeeping hidden, unset = built-in default) or "unavailable"."""
    return [
        (k, Text("built-in default", style="dim") if v is None else str(v))
        for k, v in config.items()
        if k not in {"id", "last_updated"}
    ] or [("config", "unavailable")]


def vacation_rows(vacations: list[dict[str, Any]], active_id: int | None, tz: str | None = None) -> list[Row]:
    """Vacation rows keyed by id with an active/past/upcoming state; times in the ``tz`` preference (else UTC)."""
    rows: list[Row] = []
    now = fmt.now()
    for v in vacations:
        if v["id"] == active_id:
            state = Text("active", style="bold #7ed957")
        elif v["ends_at"] < now:
            state = Text("past", style="dim")
        else:
            state = Text(f"starts {fmt.ago(v['starts_at'])}", style="#6fb7ff")
        rows.append(
            (
                str(v["id"]),
                [
                    str(v["id"]),
                    fmt.clock(v["starts_at"], with_date=True, tz=tz or "UTC"),
                    fmt.clock(v["ends_at"], with_date=True, tz=tz or "UTC"),
                    v.get("contact_email") or "—",
                    v.get("notes") or "",
                    state,
                ],
            )
        )
    return rows

"""Settings screen: account line, preferences, global defaults and vacations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text

from greenhouse_cli.constants import GLOBAL_CONFIG_DEFAULTS
from greenhouse_cli.tui import formatting as fmt

if TYPE_CHECKING:
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


# Rows labelled for the user rather than by API field name.
_ROW_LABELS = {"auto_run": "irrigate automatically"}
_CAP_FIELDS = frozenset({"daily_cap_minutes", "max_events_per_day"})


def _builtin_default(key: str) -> Text:
    """Dim label naming the built-in value an unset global field falls through to (``2 (default)``)."""
    if key not in GLOBAL_CONFIG_DEFAULTS:
        return Text("built-in default", style="dim")
    value = GLOBAL_CONFIG_DEFAULTS[key]
    if value is None:
        label = "no cap" if key in _CAP_FIELDS else "off"
    elif isinstance(value, bool):
        label = "on" if value else "off"
    else:
        label = str(value)
    return Text(f"{label} (default)", style="dim")


def global_config_rows(config: dict[str, Any]) -> list[tuple[str, str | Text]]:
    """Global-default rows in repository field order (bookkeeping hidden, unset = built-in default) or "unavailable"."""
    return [
        (_ROW_LABELS.get(k, k), _builtin_default(k) if v is None else str(v))
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

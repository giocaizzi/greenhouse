"""The table-row shape every ``*_rows`` builder returns."""

from __future__ import annotations

from rich.console import RenderableType

Row = tuple[str | None, list[RenderableType | str]]
"""One ``DataTable`` row for :func:`greenhouse_cli.tui.widgets.refill`: ``(row key or None, cells)``."""

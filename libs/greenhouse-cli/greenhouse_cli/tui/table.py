"""The TUI's ``DataTable``: Textual's table with column widths that never repaint stale."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from textual.widgets import DataTable as TextualDataTable
from textual.widgets.data_table import RowDoesNotExist

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from rich.console import RenderableType
    from textual.widgets.data_table import CellKey, RowKey

CellType = TypeVar("CellType")


class DataTable(TextualDataTable[CellType]):
    """Textual's ``DataTable``, but a column that widens after a paint repaints at its new width.

    Textual sizes auto-width columns lazily: ``add_row`` only queues the row, and the table measures
    it on its next idle (``_update_dimensions``). Its header / row / cell render caches are keyed on
    ``_update_count``, which that measurement does not bump. When the screen paints in between (its
    update timer firing before the table's idle, e.g. on a busy CPU), the header and rows are cached
    at the label-only widths and served narrow, titles cut to the header's width, until the next data
    change. Bumping the count when the widths moved invalidates those stale renders.

    Every TUI table is this class; it keeps Textual's name so CSS type selectors, ``query(DataTable)``
    and the ``on_data_table_*`` handler names are unchanged.
    """

    def _update_column_widths(self, updated_cells: set[CellKey]) -> None:
        """Re-measure updated cells as Textual does, then drop every render made at the old widths."""
        before = self._column_widths()
        super()._update_column_widths(updated_cells)
        self._invalidate_renders_if_resized(before)

    def _update_dimensions(self, new_rows: Iterable[RowKey]) -> None:
        """Measure the new rows as Textual does, then drop every render made at the old widths."""
        before = self._column_widths()
        super()._update_dimensions(new_rows)
        self._invalidate_renders_if_resized(before)

    def _invalidate_renders_if_resized(self, before: list[int]) -> None:
        """Bump the render-cache key and repaint when a measurement changed any column width."""
        if self._column_widths() != before:
            self._update_count += 1
            self.refresh()

    def _column_widths(self) -> list[int]:
        """Content width of the row-label column and of every data column, in order."""
        return [self._label_column.content_width, *(column.content_width for column in self.ordered_columns)]


def selected_key(table: DataTable[Any]) -> str | None:
    """Row key under the cursor, or ``None`` for an empty table."""
    if not table.row_count:
        return None
    row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
    return row_key.value


def refill(table: DataTable[Any], rows: Iterable[tuple[str | None, Sequence[RenderableType | str]]]) -> None:
    """Replace a table's rows while keeping the cursor on the same record.

    Auto-refresh reloads every table; without this the cursor would snap back
    to row 0 and a following edit / delete would target the wrong record.

    Args:
        table: The table to repopulate.
        rows: ``(key, cells)`` pairs; keys must be unique (``None`` = unkeyed).
    """
    previous_key = selected_key(table)
    previous_row = table.cursor_row
    table.clear()
    for key, cells in rows:
        table.add_row(*cells, key=key)
    if not table.row_count:
        return
    try:
        target = table.get_row_index(previous_key) if previous_key is not None else previous_row
    except RowDoesNotExist:  # the record went away — stay at the same position
        target = previous_row
    table.move_cursor(row=min(max(target, 0), table.row_count - 1))

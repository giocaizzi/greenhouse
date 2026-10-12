"""The TUI's ``DataTable``: Textual's table with column widths that never repaint stale."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from textual.widgets import DataTable as TextualDataTable

if TYPE_CHECKING:
    from collections.abc import Iterable

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

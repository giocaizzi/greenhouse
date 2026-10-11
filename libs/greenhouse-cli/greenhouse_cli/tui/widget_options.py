"""Typed keyword pass-through for the composite widgets in :mod:`greenhouse_cli.tui.widgets`.

Each composite widget forwards Textual's standard ``name`` / ``id`` / ``classes`` /
``disabled`` keywords to its base class, minus the ones it sets itself.
"""

from typing import TypedDict


class CardOptions(TypedDict, total=False):
    """Textual widget keywords a :class:`ClusterCard` forwards (it sets its own ``id`` and ``classes``)."""

    name: str | None
    disabled: bool


class TileOptions(CardOptions, total=False):
    """Textual widget keywords a :class:`PlantTile` forwards (it sets its own ``classes``)."""

    id: str | None


class WidgetOptions(TileOptions, total=False):
    """The standard Textual widget keywords a :class:`SpriteView` forwards to ``Static``."""

    classes: str | None

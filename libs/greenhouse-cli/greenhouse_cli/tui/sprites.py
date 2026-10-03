"""Pixel-art sprites rendered with Unicode half-blocks.

Each sprite is a grid of palette keys (``.`` is transparent). Two vertical
pixels share one terminal cell: the upper pixel paints the ``▀`` foreground,
the lower one its background — so a 16×16 sprite takes 16 columns × 8 rows
and pixels come out roughly square. No image protocol needed; any truecolor
terminal renders them.

Plants are drawn per category and re-coloured by *mood* (derived from soil
moisture against the plant's threshold band), and every plant has an
animation loop: foliage sways, thriving plants sparkle, wilting plants drop
leaves, and water drops fall while the irrigator runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.segment import Segment
from rich.style import Style

TRANSPARENT = "."

# Display-only fallback band used when the server returns no threshold for a
# cluster/plant. These only pick a sprite colour — the decision engine's real
# thresholds live server-side in greenhouse_core.constants.
FALLBACK_MOISTURE_MIN = 40.0
FALLBACK_MOISTURE_MAX = 70.0
WILT_MARGIN = 10.0
SOAK_MARGIN = 5.0


class Mood(StrEnum):
    """How a plant looks, given its soil moisture."""

    THRIVING = "thriving"
    OK = "ok"
    THIRSTY = "thirsty"
    WILTING = "wilting"
    SOAKED = "soaked"
    UNKNOWN = "unknown"


MOOD_LABELS: dict[Mood, str] = {
    Mood.THRIVING: "thriving",
    Mood.OK: "happy",
    Mood.THIRSTY: "thirsty",
    Mood.WILTING: "wilting!",
    Mood.SOAKED: "soaked",
    Mood.UNKNOWN: "no data",
}

MOOD_COLORS: dict[Mood, str] = {
    Mood.THRIVING: "#7ed957",
    Mood.OK: "#9bcf5a",
    Mood.THIRSTY: "#e0c341",
    Mood.WILTING: "#e0663f",
    Mood.SOAKED: "#4fb3ff",
    Mood.UNKNOWN: "#8a8a8a",
}


def mood_for(moisture: float | None, band_min: float | None = None, band_max: float | None = None) -> Mood:
    """Classify soil moisture into a sprite mood.

    Args:
        moisture: Latest soil moisture (%), or ``None`` when unknown.
        band_min: Lower edge of the plant's ideal band (falls back to 40%).
        band_max: Upper edge of the plant's ideal band (falls back to 70%).

    Returns:
        The mood used to colour the plant sprite.
    """
    if moisture is None:
        return Mood.UNKNOWN
    lo = FALLBACK_MOISTURE_MIN if band_min is None else band_min
    hi = FALLBACK_MOISTURE_MAX if band_max is None else band_max
    if moisture < lo - WILT_MARGIN:
        return Mood.WILTING
    if moisture < lo:
        return Mood.THIRSTY
    if moisture > hi + SOAK_MARGIN:
        return Mood.SOAKED
    if moisture >= lo + (hi - lo) / 2:
        return Mood.THRIVING
    return Mood.OK


# ── Palettes ────────────────────────────────────────────────────────────────

POT_PALETTE = {
    "R": "#d2744a",  # rim
    "P": "#a8552c",  # pot body
    "p": "#7a3b1e",  # pot shade
    "S": "#5b7f3a",  # stem
}

FOLIAGE: dict[Mood, dict[str, str]] = {
    Mood.THRIVING: {"G": "#3fa34d", "g": "#2a7a36", "L": "#86e05e"},
    Mood.OK: {"G": "#5c9e3f", "g": "#3f7a2e", "L": "#a3d160"},
    Mood.THIRSTY: {"G": "#a7a43c", "g": "#7c7a2a", "L": "#d6cf66"},
    Mood.WILTING: {"G": "#8b6b3a", "g": "#664a26", "L": "#b58d4f", "S": "#7a5a30"},
    Mood.SOAKED: {"G": "#3f8f7a", "g": "#2c6b5c", "L": "#70c8b5"},
    Mood.UNKNOWN: {"G": "#7d7d7d", "g": "#5c5c5c", "L": "#a8a8a8", "S": "#6e6e6e"},
}

WATER = "#4fb3ff"
SPARKLE = "#fff6a8"

# ── Plant art (16 px wide; foliage rows + shared pot) ───────────────────────

_POT = [
    ".RRRRRRRRRRRRRR.",
    "..PPPPPPPPPPPp..",
    "..PPPPPPPPPPPp..",
    "...PPPPPPPPPp...",
    "....pppppppp....",
]

_PLANTS: dict[str, tuple[list[str], str]] = {
    # category: (foliage rows, accent colour for "F" pixels)
    "tropical": (
        [
            "...GG......GG...",
            "..GLLG....GLLG..",
            ".GLGGGG..GGGGLG.",
            ".GG.GgG..GgG.GG.",
            "..GGGgG..GgGGG..",
            "....GgSG.SgG....",
            ".GGG..SS.S..GGG.",
            "GLLGG..SS..GGLLG",
            ".GGgGG.SS.GGgGG.",
            "...GgGGSSGGgG...",
            "......SSSS......",
        ],
        "#ffffff",
    ),
    "fern": (
        [
            ".......G........",
            "...G..GLG..G....",
            "..GLG.GLG.GLG...",
            ".GLG..GLG..GLG..",
            "GLG.G.GLG.G.GLG.",
            "GG.GLGGLGGLG.GG.",
            "G..GLG.S.GLG..G.",
            "..GLG..S..GLG...",
            ".GLG.G.S.G.GLG..",
            ".GG.GLGSGLG.GG..",
            "....GGSSSGG.....",
        ],
        "#ffffff",
    ),
    "succulent": (
        [
            "................",
            "................",
            "................",
            "................",
            ".......L........",
            "......LGL.......",
            "...L..GgG..L....",
            "..LGG.GgG.GGL...",
            ".LGgGGGgGGGgGL..",
            "LGGgGGgFgGGgGGL.",
            ".GgGgGgGgGgGgG..",
        ],
        "#ff8fb1",
    ),
    "cacti": (
        [
            "......FF........",
            ".....GLLG.......",
            ".....GLgG.......",
            ".G...GLgG...G...",
            "GLG..GLgG..GLG..",
            "GLG..GLgG..GLG..",
            "GLGGGGLgGGGGLG..",
            ".GGGGGLgGGGGG...",
            ".....GLgG.......",
            ".....GLgG.......",
            ".....GLgG.......",
        ],
        "#ff6fa8",
    ),
    "fruit_tree": (
        [
            ".....GGGGG......",
            "...GGLLGGGGG....",
            "..GLLGGFGGgGG...",
            ".GLGGGGGGGGgGG..",
            ".GGFGGgGGGFGgG..",
            "..GGgGGGGgGGG...",
            "...GGGgSGGFG....",
            ".....GGSGG......",
            ".......S........",
            ".......S........",
            "......SSS.......",
        ],
        "#ffd23f",
    ),
    "generic": (
        [
            "................",
            "................",
            "................",
            "......GG..GG....",
            ".....GLLGGLLG...",
            "....GLGGSGGGLG..",
            "....GG..S...GG..",
            "........S.......",
            ".......SS.......",
            "........S.......",
            ".......SS.......",
        ],
        "#ffffff",
    ),
}

CATEGORY_ALIASES = {"cactus": "cacti", "tree": "fruit_tree", "fruit": "fruit_tree", "herb": "generic"}

# Rows of foliage that sway; the sway cycle loops left → centre → right → centre.
_SWAY_ROWS = 5
_SWAY_CYCLE = [0, 1, 0, -1]

# Thriving plants twinkle: one sparkle position per frame.
_SPARKLES = [(1, 1), (14, 3), (2, 7), (13, 0)]

# A wilting plant drops a leaf that drifts down beside the pot.
_FALLING_LEAF = [(14, 2), (15, 4), (14, 6), (15, 8), (14, 10), (15, 12)]

# Where water drops fall while watering (col, row) — shifted down per frame.
_DROPS = [(3, 0), (8, 1), (12, 0), (5, 3), (10, 4)]


# ── Other sprites ───────────────────────────────────────────────────────────

LOGO = (
    [
        "........RR..........",
        "......RRccRR........",
        "....RRccccccRR......",
        "..RRccccccccccRR....",
        "RRRRRRRRRRRRRRRRRR..",
        ".Rcc.cRccc.Rcc.cR...",
        ".R.GG.R.LG.R.GL.R...",
        ".RGGLGRGGGGRGGGGR...",
        ".RGgGGRGgGGRGgGGR...",
        ".RRRRRRRRRRRRRRRRR..",
    ],
    {"R": "#e6e6e6", "c": "#9fd8f5", "G": "#3fa34d", "g": "#2a7a36", "L": "#86e05e"},
)

WATERING_CAN = (
    [
        "....MMMMM.......",
        "...M.....M......",
        "..MCCCCCCCM....N",
        "..MCCCCCCCM...N.",
        "MMMCCCCCCCMMMN..",
        "M.MCCCCCCCM.....",
        "..MCCCCCCCM.....",
        "..MMMMMMMMM.....",
    ],
    {"M": "#5d7a8c", "C": "#88aabf", "N": "#5d7a8c", "W": WATER},
)

_CAN_POUR = [(15, 4), (14, 6), (15, 8), (13, 9)]


@dataclass(frozen=True)
class PixelSprite:
    """A Rich renderable that paints a palette-indexed pixel grid with half-blocks."""

    rows: tuple[str, ...]
    palette: dict[str, str]

    @property
    def width(self) -> int:
        """Width in terminal columns (one pixel per column)."""
        return max((len(r) for r in self.rows), default=0)

    @property
    def height(self) -> int:
        """Height in terminal rows (two pixels per row)."""
        return (len(self.rows) + 1) // 2

    def _color(self, key: str) -> str | None:
        if key == TRANSPARENT:
            return None
        return self.palette.get(key)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = self.width
        rows = [r.ljust(width, TRANSPARENT) for r in self.rows]
        if len(rows) % 2:
            rows.append(TRANSPARENT * width)
        for top_row, bottom_row in zip(rows[0::2], rows[1::2], strict=True):
            for top_key, bottom_key in zip(top_row, bottom_row, strict=True):
                top, bottom = self._color(top_key), self._color(bottom_key)
                if top is None and bottom is None:
                    yield Segment(" ")
                elif bottom is None:
                    yield Segment("▀", Style(color=top))
                elif top is None:
                    yield Segment("▄", Style(color=bottom))
                else:
                    yield Segment("▀", Style(color=top, bgcolor=bottom))
            yield Segment.line()

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement(self.width, self.width)


def _sway(rows: list[str], offset: int) -> list[str]:
    """Shift the top foliage rows ``offset`` pixels sideways (wind sway)."""
    out = list(rows)
    for i in range(min(_SWAY_ROWS, len(out))):
        row = out[i]
        if offset > 0:
            out[i] = (TRANSPARENT * offset + row)[: len(row)]
        elif offset < 0:
            out[i] = (row[-offset:] + TRANSPARENT * -offset)[: len(row)]
    return out


def _overlay(rows: list[str], points: list[tuple[int, int]], key: str) -> list[str]:
    grid = [list(r) for r in rows]
    for col, row in points:
        if 0 <= row < len(grid) and 0 <= col < len(grid[row]) and grid[row][col] == TRANSPARENT:
            grid[row][col] = key
    return ["".join(r) for r in grid]


def normalize_category(category: str | None) -> str:
    """Map a plant-DB category onto one of the drawn sprite families."""
    key = (category or "").strip().lower().replace(" ", "_").replace("-", "_")
    key = CATEGORY_ALIASES.get(key, key)
    return key if key in _PLANTS else "generic"


def plant_sprite(category: str | None, mood: Mood, frame: int = 0, watering: bool = False) -> PixelSprite:
    """Build the sprite for a potted plant.

    Args:
        category: Plant-DB category (``tropical``, ``fern``, ``succulent``,
            ``cacti``, ``fruit_tree``); anything else draws a generic sprout.
        mood: Colour scheme, normally from :func:`mood_for`.
        frame: Animation frame counter — drives sway, sparkles, falling
            leaves and water drops.
        watering: Overlay falling water drops (animated by ``frame``).

    Returns:
        A renderable 16-column pixel sprite.
    """
    foliage, accent = _PLANTS[normalize_category(category)]
    # Wilting plants barely move; everyone else sways in the breeze.
    offset = 0 if mood in (Mood.WILTING, Mood.UNKNOWN) else _SWAY_CYCLE[frame % len(_SWAY_CYCLE)]
    rows = _sway(foliage, offset) + list(_POT)
    if watering:
        shift = frame % 3
        rows = _overlay(rows, [(c, r + shift) for c, r in _DROPS], "W")
    elif mood is Mood.THRIVING:
        rows = _overlay(rows, [_SPARKLES[frame % len(_SPARKLES)]], "K")
    elif mood is Mood.WILTING:
        rows = _overlay(rows, [_FALLING_LEAF[frame % len(_FALLING_LEAF)]], "g")
    palette = {**POT_PALETTE, **FOLIAGE[mood], "F": accent, "W": WATER, "K": SPARKLE}
    return PixelSprite(tuple(rows), palette)


def logo_sprite() -> PixelSprite:
    """The greenhouse glass-house logo."""
    rows, palette = LOGO
    return PixelSprite(tuple(rows), palette)


def watering_can_sprite(pouring: bool = False, frame: int = 0) -> PixelSprite:
    """A watering can; when ``pouring`` the spout drips (animated by ``frame``)."""
    rows, palette = WATERING_CAN
    rows = [r.ljust(16, TRANSPARENT) for r in rows] + [TRANSPARENT * 16, TRANSPARENT * 16]
    if pouring:
        shift = frame % 2
        rows = _overlay(rows, [(c, min(r + shift, len(rows) - 1)) for c, r in _CAN_POUR], "W")
    return PixelSprite(tuple(rows), palette)

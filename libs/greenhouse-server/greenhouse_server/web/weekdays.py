"""Weekday bitmask vocabulary and label formatting for web templates.

One copy of the Mon=1 … Sun=64 bit order shared by the cluster detail page and the window
forms, so the labels a template shows always match the bits a form posts.
"""

from greenhouse_core.constants import FULL_WEEKDAY_MASK

WEEKDAY_BITS: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64)
WEEKDAY_LABELS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def format_weekday_mask(mask: int) -> str:
    """Render a weekday bitmask as a human label (e.g. 'Mon, Wed, Fri')."""
    if mask & FULL_WEEKDAY_MASK == FULL_WEEKDAY_MASK:
        return "Every day"
    return ", ".join(label for bit, label in zip(WEEKDAY_BITS, WEEKDAY_LABELS, strict=True) if mask & bit)

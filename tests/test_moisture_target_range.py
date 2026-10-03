"""Equivalence check: ``moisture_target_range(care)`` == the inline expression it replaces.

Six call sites (engine, health, forecast, irrigation, learning/issues x2) spell
``parse_moisture_target(care.get("soil_moisture_target", "45-65"))``; the helper must be
indistinguishable from that expression for every care mapping, including malformed targets.
"""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from greenhouse_core.constants import DEFAULT_SOIL_MOISTURE_TARGET
from greenhouse_core.logic.plant_needs import moisture_target_range, parse_moisture_target

TARGETS = st.one_of(
    st.none(),
    st.text(max_size=12),
    st.from_regex(r"\A-?\d{1,3}(\.\d{1,2})?-\d{1,3}(\.\d{1,2})?\Z"),
    st.sampled_from(["45-65", "nan-5", "inf--inf", "40", "-", "", " 30 - 50 ", "a-b", "1-2-3"]),
    st.integers(),
    st.floats(allow_nan=True),
    st.lists(st.text(max_size=3), max_size=3),
)
OTHER_KEYS = st.dictionaries(
    st.text(max_size=8).filter(lambda k: k != "soil_moisture_target"),
    st.one_of(st.none(), st.integers(), st.text(max_size=5)),
    max_size=3,
)


def _inline(care: Any) -> tuple[float, float]:
    return parse_moisture_target(care.get("soil_moisture_target", "45-65"))


def _same(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return len(a) == len(b) and all(x == y or (math.isnan(x) and math.isnan(y)) for x, y in zip(a, b, strict=True))


def test_default_target_constant_is_the_inline_literal() -> None:
    assert DEFAULT_SOIL_MOISTURE_TARGET == "45-65"
    assert type(DEFAULT_SOIL_MOISTURE_TARGET) is str


@settings(derandomize=True, max_examples=300)
@given(other=OTHER_KEYS, has_target=st.booleans(), target=TARGETS)
def test_moisture_target_range_equals_inline_expression(other: dict[str, Any], has_target: bool, target: Any) -> None:
    care = dict(other)
    if has_target:
        care["soil_moisture_target"] = target
    expected = _inline(care)
    assert _same(moisture_target_range(care), expected)
    assert _same(moisture_target_range(MappingProxyType(care)), expected)


def test_missing_target_falls_back_to_default_band() -> None:
    assert moisture_target_range({}) == (45.0, 65.0)
    assert moisture_target_range({"soil_moisture_target": None}) == (45.0, 65.0)
    assert moisture_target_range({"soil_moisture_target": "30-50"}) == (30.0, 50.0)


@settings(derandomize=True, max_examples=300)
@given(target=TARGETS)
def test_every_band_parser_agrees_with_moisture_target_range(target: Any) -> None:
    """D10: monitor, chronic-underwatering and conflict detection all read a target through one parser."""
    from greenhouse_core.learning.issues import _conflict_band

    care = {"soil_moisture_target": target}
    assert _same(_conflict_band({7: care}, 7), moisture_target_range(care))
    assert _conflict_band({}, 7) == _conflict_band({7: care}, None) == (45.0, 65.0)

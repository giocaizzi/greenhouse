"""Characterization: ``constants.py`` values and the decision enums.

``contracts/constants.json``:

- ``constants`` — every UPPERCASE module attribute of ``greenhouse_core.constants`` →
  ``repr(value)``. ``repr`` keeps what JSON would blur (``6`` vs ``6.0``, tuple vs
  list, dict insertion order). Keys are sorted: definition order inside the file is
  not behavior.
- ``enums`` — ``Action``, ``Severity``, ``TriggerCode`` (``logic/decision.py``) and
  ``HealthAlarm`` (``devices/health.py``) as ``[name, value]`` pairs **in definition
  order** (iteration order reaches the UI / audit log), plus
  ``DEVICE_BLOCKING_CODES`` (a frozenset → sorted).

**Semantics** (orchestrator policy): ``constants`` is a *compatibility (superset)* pin —
every golden name must still exist with an equal ``repr`` (so equal value *and* type);
NEW constants are allowed, since additions break no consumer and goldens may not be
re-generated after the fact. Each golden enum and ``DEVICE_BLOCKING_CODES`` stay STRICT:
exact members, values and order (a brand-new enum class is an allowed addition). ``GOLDEN_UPDATE=1`` (re)writes the full snapshot.
"""

from __future__ import annotations

import json
import os
from enum import Enum

import pytest

from golden import GOLDEN_DIR, UPDATE_ENV_VAR, assert_golden_json
from greenhouse_core import constants
from greenhouse_core.devices import HealthAlarm
from greenhouse_core.logic import DEVICE_BLOCKING_CODES, decision
from greenhouse_core.logic.decision import Action, Severity, TriggerCode


def _constants() -> dict[str, str]:
    return {name: repr(value) for name, value in vars(constants).items() if name.isupper()}


def _decision_enums() -> dict[str, type[Enum]]:
    return {
        name: obj
        for name, obj in vars(decision).items()
        if isinstance(obj, type) and issubclass(obj, Enum) and obj.__module__ == decision.__name__
    }


def _observed() -> dict:
    enums = {name: [[m.name, m.value] for m in enum] for name, enum in _decision_enums().items()}
    enums["HealthAlarm"] = [[m.name, m.value] for m in HealthAlarm]
    return {
        "constants": _constants(),
        "enums": enums,
        "DEVICE_BLOCKING_CODES": sorted(c.value for c in DEVICE_BLOCKING_CODES),
    }


def _golden() -> dict | None:
    name = "contracts/constants.json"
    if os.environ.get(UPDATE_ENV_VAR) == "1":
        assert_golden_json(name, _observed())
        return None
    path = GOLDEN_DIR / name
    assert path.exists(), f"golden file missing: {path} (create it with {UPDATE_ENV_VAR}=1)"
    return json.loads(path.read_text(encoding="utf-8"))


def test_golden_constants_still_exist_with_equal_values():
    golden = _golden()
    if golden is None:
        return
    observed = _constants()
    removed = sorted(set(golden["constants"]) - set(observed))
    changed = {k: (v, observed[k]) for k, v in golden["constants"].items() if k in observed and observed[k] != v}
    assert not removed, f"constants removed: {removed}"
    assert not changed, f"constants changed (golden, now): {changed}"


def test_enums_and_blocking_codes_match_golden_exactly():
    golden = _golden()
    if golden is None:
        return
    observed = _observed()
    for name, members in golden["enums"].items():  # a NEW enum class would be an allowed addition
        assert observed["enums"].get(name) == members, name
    assert observed["DEVICE_BLOCKING_CODES"] == golden["DEVICE_BLOCKING_CODES"]


def test_constant_types():
    values = {name: value for name, value in vars(constants).items() if name.isupper()}
    assert len(values) >= 100
    assert all(isinstance(v, int | float | str | bool | tuple | dict) for v in values.values())


def test_decision_module_defines_the_three_enums():
    assert list(_decision_enums())[:3] == ["Action", "Severity", "TriggerCode"]


@pytest.mark.parametrize("enum", [Action, Severity, TriggerCode, HealthAlarm])
def test_enums_are_str_enums_whose_members_equal_their_values(enum):
    """``StrEnum`` members compare/serialize as their value (persisted in DB / JSON)."""
    for member in enum:
        assert isinstance(member, str)
        assert member == member.value
        assert str(member) == member.value
        assert enum(member.value) is member


def test_trigger_code_count_and_extremes():
    members = list(TriggerCode)
    assert len(members) == 46
    assert (members[0].value, members[-1].value) == ("no_plants", "leak_hold")
    assert len({m.value for m in members}) == 46  # no aliases


def test_notable_thresholds_named_in_claude_md():
    """Values CLAUDE.md's key invariants refer to by name."""
    assert constants.MIN_COOLDOWN_HOURS == 6
    assert constants.SENSOR_READING_STALE_SECONDS == 4 * 3600
    assert constants.LEAK_HOLD_HOURS == 24
    assert constants.LEAK_ALERT_CODE == "leak_or_stuck_valve"

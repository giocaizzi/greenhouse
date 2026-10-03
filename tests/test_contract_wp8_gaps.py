"""WP8 coverage-gap and M-pre survivor tests (engine) — characterization, current behavior.

Pre-authorized test-only commit ahead of the WP8 engine restructuring (plan §0.4 G.1).
Each test pins a branch, boundary or ordering of ``logic/engine.py`` that the WP8
subsets did not reach (coverage gap) or that an M-pre mutant changed without any
test failing (``refactor/wp-handoff/wp8-mutation/engine-pre.jsonl``). Nothing here
asserts what the code *should* do. Cases reuse the pytest-free ``engine_grid``
harness: a fresh in-memory schema per case, a frozen instant, no network.
"""

from __future__ import annotations

from typing import Any

import pytest

from engine_grid import BASE_AT, DAY, HOUR, Case, Event, Leak, Reading, SensorSpec, run_case
from greenhouse_core.logic import IrrigationLogic

_DROP = object()
_KEY = "season_frequency_multiplier"


@pytest.fixture(autouse=True)
def _hermetic(clean_env):
    """Isolate env vars / TZ like the other characterization suites."""


class _OverlayPlantDB:
    """The bundled plant DB with per-species care-key overlays (``_DROP`` removes a key)."""

    def __init__(self, base: Any, overlays: dict[str, dict[str, Any]]):
        self._base = base
        self._overlays = overlays

    def get_care_data(self, species: str | None = None, category: str | None = None) -> dict:
        care = dict(self._base.get_care_data(species=species, category=category))
        for key, value in self._overlays.get(species or "", {}).items():
            if value is _DROP:
                care.pop(key, None)
            else:
                care[key] = value
        return care

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _decide(case_id: str, overlays: dict[str, dict[str, Any]] | None = None, **kw: Any) -> dict:
    def factory(repo, plant_db, **fkw):
        return IrrigationLogic(repo, _OverlayPlantDB(plant_db, overlays or {}), **fkw)

    kw.setdefault("sensors", (SensorSpec(name="WP8 Soil", readings=(Reading(age_s=600, soil=38.0),), plant=0),))
    record = run_case(Case(id=f"wp8/{case_id}", family="wp8", at=BASE_AT, **kw), engine_factory=factory)
    assert len(record["decision_logs"]) == 1
    return record["decision"]


def _reason(decision: dict, code: str) -> dict:
    (match,) = [r for r in decision["reasons"] if r["code"] == code]
    return match


# ``get_plants_in_cluster`` orders by species, so the engine sees Dypsis before Monstera.
_FIRST, _SECOND = "Dypsis lutescens", "Monstera deliciosa"
_TWO_TROPICALS = ((_SECOND, "tropical"), (_FIRST, "tropical"))


def test_seasonal_first_plant_override_kept_while_category_comes_from_a_later_plant():
    """Coverage 352->354: once a plant-level override is found it is never replaced.

    Plant 1 has a species-level table without a ``spring`` entry and no category
    block; plant 2 has a ``spring`` species value (4.0) and a category ``spring``
    value (0.25). The engine keeps plant 1's table (no spring → falls through) and
    takes the category value from plant 2 → 0.25, not 4.0.
    """
    decision = _decide(
        "season-plant-first",
        overlays={
            _FIRST: {_KEY: {"winter": 0.5}, "_category_defaults": {}},
            _SECOND: {_KEY: {"spring": 4.0}, "_category_defaults": {_KEY: {"spring": 0.25}}},
        },
        plants=_TWO_TROPICALS,
    )
    assert _reason(decision, "seasonal_hold")["message"] == "spring multiplier 0.25× for indoor cluster"


def test_seasonal_first_category_override_kept_while_plant_comes_from_a_later_plant():
    """Coverage 354->357: once a category-level override is found it is never replaced.

    Plant 1 has no species-level key but a category ``spring`` value (0.25);
    plant 2 has a species table without ``spring`` and a category ``spring`` of
    9.0. The species table falls through, the FIRST category value wins → 0.25.
    """
    decision = _decide(
        "season-category-first",
        overlays={
            _FIRST: {_KEY: _DROP, "_category_defaults": {_KEY: {"spring": 0.25}}},
            _SECOND: {_KEY: {"winter": 4.0}, "_category_defaults": {_KEY: {"spring": 9.0}}},
        },
        plants=_TWO_TROPICALS,
    )
    assert _reason(decision, "seasonal_hold")["message"] == "spring multiplier 0.25× for indoor cluster"


def test_seasonal_plant_override_from_a_later_plant_still_wins_over_an_earlier_category():
    """M-pre ``plant_override is not None -> is None`` (break guard): the loop only stops once BOTH are found.

    Plant 1 contributes only a category value (0.25); plant 2 contributes a
    species-level ``spring`` value (4.0), which outranks the category layer → 4×.
    """
    decision = _decide(
        "season-plant-later",
        overlays={
            _FIRST: {_KEY: _DROP, "_category_defaults": {_KEY: {"spring": 0.25}}},
            _SECOND: {_KEY: {"spring": 4.0}, "_category_defaults": {}},
        },
        plants=_TWO_TROPICALS,
    )
    assert _reason(decision, "seasonal_boost")["message"] == "spring multiplier 4× for indoor cluster"


# ── cooldown / leak-hold message arithmetic ─────────────────────────────────


@pytest.mark.parametrize(("age_s", "shown"), [(18177, "5.0"), (18184, "5.1")])
def test_cooldown_hours_ago_divides_by_3600(age_s, shown):
    """M-pre ``/ 3600 -> / 3599`` and ``-> / 3601``: the hours-ago figure is ``seconds / 3600`` at ``.1f``.

    18177 s = 5.0492 h ("5.0"; /3599 would show 5.1) and 18184 s = 5.0511 h
    ("5.1"; /3601 would show 5.0).
    """
    decision = _decide(f"cooldown-{age_s}", events=(Event(age_s),))
    assert decision["reasons"][0]["message"] == f"cooldown active (last irrigation {shown}h ago, trigger: auto)"


def test_cooldown_tie_on_timestamp_keeps_the_first_event_returned():
    """M-pre ``event.timestamp > latest.timestamp -> >=``: equal timestamps keep the first event scanned.

    Two ``start`` events share a timestamp; the repository returns them newest
    first and the strict ``>`` keeps whichever comes first, so the trigger shown
    is that event's (pinned as observed: the earlier-inserted ``auto`` row).
    """
    decision = _decide(
        "cooldown-tie",
        events=(Event(HOUR, triggered_by="auto"), Event(HOUR, triggered_by="manual")),
    )
    assert decision["reasons"][0]["message"] == "cooldown active (last irrigation 1.0h ago, trigger: auto)"


@pytest.mark.parametrize(("age_s", "shown"), [(3413, "23.1"), (3427, "23.0")])
def test_leak_hold_hours_left_divides_by_3600(age_s, shown):
    """M-pre ``/ 3600 -> / 3599`` and ``-> / 3601`` in ``hours_left`` (24 h hold, ``.1f``).

    Seen 3413 s ago → 82987 s left = 23.0519 h ("23.1"; /3601 shows 23.0);
    seen 3427 s ago → 82973 s left = 23.0481 h ("23.0"; /3599 shows 23.1).
    """
    decision = _decide(f"leak-{age_s}", leak=Leak(age_s=age_s))
    assert decision["reasons"][0]["code"] == "leak_hold"
    assert decision["reasons"][0]["message"].startswith(f"leak/stuck-valve hold active ({shown}h left) — ")


# ── vacation rationing arithmetic ───────────────────────────────────────────


def _vacation(case_id: str, vacation: tuple[int, int], reservoir_l: float | None = None) -> dict:
    """SENSOR_DRY (2 min IRRIGATE) under a vacation; 1 L/min when a reservoir is given."""
    return _decide(
        case_id,
        vacation=vacation,
        reservoir_l=reservoir_l,
        flow_rate=1.0 if reservoir_l is not None else None,
    )


def test_vacation_days_left_rounds_up_whole_days():
    """M-pre ``(ends_at - now) / 86400 -> / 86401``: 2 days + 1 s left reads "returns in 3d"."""
    decision = _vacation("vac-days-left", (-HOUR, 2 * DAY + 1))
    assert _reason(decision, "vacation_active")["message"] == "vacation active (returns in 3d)"


def test_zero_length_vacation_counts_as_one_day():
    """M-pre ``max(1, ...) -> max(0, ...)``: a vacation starting and ending now still has a 1-day budget.

    95 L usable over 1 day → no trim (a 0-day budget would divide by zero).
    """
    decision = _vacation("vac-zero-length", (0, 0), reservoir_l=100.0)
    assert [r["code"] for r in decision["reasons"]] == ["sensor_dry", "vacation_active"]
    assert (decision["action"], decision["duration_minutes"]) == ("irrigate", 2)


def test_sub_day_vacation_budget_is_one_full_day():
    """M-pre ``max(1, ...) -> max(2, ...)``: a 12 h vacation is one day → 3.8 L usable → 3 min ≥ 2, untouched."""
    decision = _vacation("vac-12h", (-HOUR, 11 * HOUR), reservoir_l=4.0)
    assert [r["code"] for r in decision["reasons"]] == ["sensor_dry", "vacation_active"]
    assert decision["duration_minutes"] == 2


def test_vacation_length_rounds_up_to_whole_days():
    """M-pre ``(ends_at - starts_at) / 86400 -> / 86401``: 2 days + 1 s is a 3-day vacation.

    4.75 L usable / 3 days → 1.58 L today → trimmed to 1 min (2 days would allow 2 min).
    """
    decision = _vacation("vac-2d-plus-1s", (-HOUR, 2 * DAY + 1 - HOUR), reservoir_l=5.0)
    assert _reason(decision, "vacation_rationing")["message"] == "trimmed to 1 min so reservoir lasts the vacation"
    assert decision["duration_minutes"] == 1


@pytest.mark.parametrize(
    ("started_s_ago", "trimmed"),
    [(DAY - 1, True), (DAY, False)],
)
def test_vacation_day_index_is_floor_of_whole_days_elapsed(started_s_ago, trimmed):
    """M-pre ``(now - starts_at) / 86400 -> / 86399`` and ``-> / 86401`` (3-day vacation, 4.75 L usable).

    One second short of a day → still day 0 → 1.58 L allowed → trimmed to 1 min;
    exactly one day → day 1 → 3.17 L allowed → the 2-minute run is untouched.
    """
    decision = _vacation(f"vac-day-index-{started_s_ago}", (-started_s_ago, 3 * DAY - started_s_ago), reservoir_l=5.0)
    assert ("vacation_rationing" in [r["code"] for r in decision["reasons"]]) is trimmed
    assert decision["duration_minutes"] == (1 if trimmed else 2)


# ── soil-moisture conflict boundaries (Monstera target 45-60 %) ─────────────


def _soil_sensor(name: str, soil: float) -> SensorSpec:
    return SensorSpec(name=name, readings=(Reading(age_s=600, soil=soil),), plant=0)


def test_driest_exactly_at_target_min_is_not_a_conflict():
    """M-pre ``min_soil < target_min -> <=`` (conflict test): 45 % on a 45-60 plant is not "dry".

    Driest 45 %, wettest 58 % (> 60 - 5) → no conflict; average 51.5 % → adequate.
    """
    decision = _decide("conflict-edge", sensors=(_soil_sensor("Edge A", 45.0), _soil_sensor("Wet B", 58.0)))
    assert decision["reasons"][0]["code"] == "sensor_adequate"


def test_conflict_dry_names_exclude_a_sensor_exactly_at_target_min():
    """M-pre ``s.avg_soil_moisture < target_min -> <=``: the dry list is strictly below the target minimum."""
    decision = _decide(
        "conflict-names",
        sensors=(_soil_sensor("Dry A", 30.0), _soil_sensor("Edge B", 45.0), _soil_sensor("Wet C", 58.0)),
    )
    assert decision["reasons"][0]["message"] == "conflict: dry=30% (Dry A), wet=58% (Wet C) — short burst"

# 10 — Safety net: decision engine (`logic/` + `learning/`) — Phase 1 characterization

Work package **engine** (high risk). Only new files; no production / existing test / fixture edits; nothing committed.

## Files

| File | What |
|---|---|
| `tests/engine_grid.py` | Data-driven grid: frozen `Case` dataclass (+ `Reading`, `SensorSpec`, `Event`, `Leak`, `Window`), `build_cases(family=None)`, `run_case(case, *, engine_factory=IrrigationLogic)`, `golden_name(case)`, `FakeForecastWeather`, `plant_database()` (bundled JSON by package path, ignores `IRRIGATION_PLANT_DB_PATH`). No pytest dependency → reusable by the Phase 5 differential test (`run_case(c) == run_case(c, engine_factory=Other)`). |
| `tests/test_contract_decision_grid.py` | 42 golden shards + 3 meta tests (ids unique/stable, no orphan goldens, `run_case` deterministic). |
| `tests/golden/engine/**/*.jsonl` | 42 files, 1,432 cases, 2.25 MB total, largest 103 KB (< 400 KB rule). One case per line, sorted keys, compact. |
| `tests/test_invariants_engine.py` | 59 tests, one group per CLAUDE.md invariant (#2 #3 #4 #5 #6 #9 #10 #11). |
| `tests/test_properties_logic.py` | 37 Hypothesis/explicit tests for `cleaning`, `timing`, `stress`, `trends`, `fallback`, `plant_needs`. |

## Grid design (`run_case`)

Each case: fresh schema (`Base.metadata.create_all`, like `tmp_db`) on one shared in-memory engine, every case inside an
outer transaction that is rolled back (ids restart at 1; ~11 ms/case); seeded only through the real
`IrrigationRepository` with timestamps `case.at - age`; evaluated under `time_machine.travel(case.at, tick=False)` via the
public `IrrigationLogic(repo, plant_db, weather_client=…).decide_for_cluster(cid, current_temp, persist=True,
triggered_by=…, bypass_quiet_hours=…)`. Weather is a deterministic `FakeForecastWeather` (no client / `get_forecast → None`
/ fixed dict) — no network.

Record per case: full `IrrigationDecision.model_dump(mode="json")` (reasons order preserved — never sorted); every
`decision_logs` row (id, cluster_id, evaluated_at, action, duration, interval, confidence, primary_code, reason_text,
triggered_by, actuated, `payload_json_sha256` = byte-exact serialization, `payload_equals_decision` = content equality with
the dump — lossless because the dump itself is pinned); `decision_log_id_matches`; `irrigation_events_written`;
`sensor_readings_unchanged`. The test additionally asserts on every case: exactly one log row per evaluation, none for an
unknown cluster, `actuated is False`, `primary_code == reasons[0].code` (or NULL when no reasons), 0 events written,
raw readings untouched (#6/#10/#11 across all 1,432 cases).

Families (axes) — `soil` 320 (12 moisture levels incl. 0/29.9/30/44.9/45 edges × 8 plant sets × 3 climates indoor + outdoor
subset), `driest` 25 (multi-sensor spreads, linked/unlinked, one dry among wet, conflicts), `series` 48 (Hampel spike
mid/newest/oldest/high, <5 readings, out-of-range soil/temp, declining/steep/gentle/rising/saturated, temp rising/falling/
very hot, light 15/16/100/400/1000, water_warning flags, temp-/humidity-only, freshness 0s…24h-1s/24h/24h+1s/47h/49h ×
`current_temp`, DESC-insertion twins, 7-day cadence high/low), `cooldown` 64 (start auto/manual, `schedule_updated`, `stop`
× 1h, 6h±60s, 6h±1s, exactly 6h, 7h × dry/wet; combos; no irrigator; fallback; bypass), `quiet` 167 (5 configs incl. wrap
22–6, cluster 0/0 disabling a global window, global 5/5 × UTC/Rome × start/end ±1s/±60s/mid × bypass; precedence extras),
`windows` 159 (none/6–10/22–6/weekday mask/two windows/9–9 × 8 local times × dry/critical/wet, Saturday, UTC prefs,
water-warning/over-watering/fallback vs windows), `season` 308 (4 mid-season + 8 local-midnight boundaries in Europe/Rome +
UTC-prefs twins × 10 plant sets incl. multi-plant × indoor/outdoor), `dst` 90 (Rome 2026-03-29 and 2026-10-25, 22:00→05:00 UTC
every 30 min × quiet 0–5 / quiet 2–3 / window 3–4), `vacation` 42 (off, active no capacity / plenty / partial trim /
exhausted / spent trim / spent exhausted / starts-now / ends-now / ended-1s / future / flow-only / no irrigator × dry/critical/wet,
fern, fallback), `leak` 35 (none/open/ack/resolved/24h-60s/exactly 24h/24h+1s/other cluster × bypass × cooldown; vs quiet,
no-plants, water-warning), `weather` 74 (no client/offline/precip 0/2.0/2.01/5/None/missing × indoor/outdoor/"greenhouse" ×
dry/critical/fallback), `fallback` 86 (temp None/−5/18/18.1/24/24.1/28/28.1/35 × low/medium/high needs × no/smart/manual config;
sensor without readings; only-stale readings), `learning` 8 (none / blocked_drip / rapid_drainage / conflict+low_light+low_env_humidity),
`misc` 6 (missing cluster, no plants, no irrigator, manual trigger, everything-at-once).

Plant sets: Monstera (category-level tropical timing), Alocasia + Dracaena (species-level multiplier), Eriobotrya (species
`_outdoor` only), unknown fruit tree / fern / cactus (category only; fern has no `_outdoor`), unknown species w/o category
(constants only), mixed (Dracaena+Monstera+fern), unknown+loquat, loquat+unknown.

Trigger codes reached (primary or appended): every engine-emitted `TriggerCode` except `DAILY_CAP_HIT`, `LEARNING_ALERT`
and the `DEVICE_*` codes, which the engine never emits (service layer / health monitor).

## Engine functions / branches the grid covers

`decide_for_cluster` all exits (missing cluster → None, NO_PLANTS, LEAK_HOLD, COOLDOWN, QUIET_HOURS, WEATHER_SKIP, fallback,
WATER_WARNING, WATER_STRESS / OVER_WATERING, OUTSIDE_WINDOW, full pipeline) and `_finalize` with/without the
`MANUAL_OVERRIDE_QUIET_HOURS` reason; `_resolve_quiet_window` (cluster → global → default, disabled 0/0 and 5/5, wrap,
tz); `_apply_window_rule` (no rows, inside/outside, weekday mask, wrap, empty 9–9, tz, DST); `_apply_seasonal_multiplier`
(species/category/constants layers, `_outdoor` selection, multiplier == 1.0 no-op, clamp, HOLD/BOOST); `_apply_vacation_budget`
(all branches incl. no irrigator, no capacity, non-IRRIGATE, trim, exhausted); `_persist` happy path; `_enforce_leak_hold`
(status/age/cluster edges); `_enforce_cooldown` (action filter, latest-event selection, edges); `_apply_weather_skip_rule`
(no client, indoor, None forecast, None/missing precip, ≤2.0, >2.0); `_attach_learning_alerts` (no alerts / alerts);
`_decision_with_reason`; every rule function (`_apply_water_warning_rule`, `_apply_critical_stress_rule`,
`_apply_soil_moisture_rule` all 5 outcomes, temperature/humidity/light (all bands incl. the fall-through)/water-needs/trend
adjustments); `sensors.get_recent_sensor_data`, `trends.analyze_historical_trends`, `stress.detect_stress_conditions`,
`fallback.temperature_based_decision`, `plant_needs.*` — all reached through the public entry point.

### Branch coverage (`--cov=greenhouse_core.logic --cov=greenhouse_core.learning --cov-branch`)

| File | my 3 files only | + existing logic tests¹ |
|---|---|---|
| logic/engine.py | 99 % (miss 352→354, 354→357, 883) | 99 % (same) |
| logic/cleaning.py, fallback.py, plant_needs.py, sensors.py, stress.py, trends.py | 100 % | 100 % |
| logic/timing.py | 97 % (95-97 `is_within_preferred_hours`, unused by engine) | 100 % |
| logic/decision.py | 99 % (175 `StressIndicators.any_critical`, unused) | 99 % |
| learning/issues.py | 83 % | 87 % |
| learning/profiling.py | 79 % (73-87 `analyze_irrigation_response`, not on the engine path) | 97 % |
| learning/learner.py | 84 % | 100 % |
| learning/report.py | 12 % (not on the engine path) | 79 % |
| **TOTAL** | **93 %** | **97 %** (311 passed) |

¹ `test_logic.py test_engine_timing.py test_leak_hold.py test_cleaning.py test_timing.py test_vacation_rationing.py test_learning.py test_plant_db.py`.

Uncovered engine branches, and why:
- **352→354 / 354→357** (`_apply_seasonal_multiplier` loop): unreachable with the bundled plant DB — `get_care_data`
  merges `_category_defaults[c]` into the top level, so a plant that has the category key always has the top-level key
  too; the first plant with data sets both overrides and breaks. (Reachable only with a hand-built care dict.)
- **883** (`_apply_water_needs_adjustment` clamp no-op return): unreachable with today's constants — "high" always raises
  duration; "low" would need duration ≤ 1 (only CONFLICT) **and** interval already 24 h, but CONFLICT starts at 8 h and the
  maximum preceding adjustments (+6 temp, +2 humidity, +4 light) reach 20 h.

## Invariants (test → invariant)

| # | Pinned by (new) | Already pinned elsewhere (cited, not duplicated) |
|---|---|---|
| 2 | `test_inv2_driest_plant_drives_the_call_not_the_average` (avg 47.8 adequate, min 33 → SENSOR_VERY_DRY); `test_inv2_stress_rule_keys_on_average_current_behavior`; grid `driest` | `test_logic.py::test_multi_sensor_driest_triggers` (does not separate min vs avg), `test_cleaning.py::test_snapshot_min_soil_ignores_spike` |
| 3 (I3) | `test_inv3_cooldown_boundary_is_six_hours_inclusive` (6h−60s/−1s/6h → COOLDOWN, +1s/+60s → irrigate), `_manual_start_counts_towards_cooldown`, `_only_start_events_count…` (schedule_updated/stop/dry_run), `_cooldown_is_not_bypassed_by_manual_override`; grid `cooldown` | `test_logic.py::test_cooldown_blocks_irrigation`, `::test_schedule_updated_does_not_suppress_fallback`, `test_engine_timing.py::test_cooldown_beats_quiet_hours` |
| 4 (I4) | `test_inv4_learning_failure_or_alerts_never_change_the_decision` (learner raising / `[]` / alerts → identical decision except `learning_alerts`, ×5 soil scenarios), `_learner_is_consulted_once…`; grid `learning` | — |
| 5 | `test_inv5_engine_constants_mirror_constants_module`, `test_inv5_thresholds_are_bound_at_import_current_behavior`, `test_inv5_inline_literals_current_behavior` (forecast `hours=6`, `> 2.0` mm, base confidence 0.5) | — |
| 6 (I6, core part) | `test_inv6_every_exit_branch_persists_exactly_one_decision_log` (11 exits), `_persist_false_writes_nothing`, `_unknown_cluster…`, `_persistence_failure_never_blocks…`, `_reasons_stay_a_list_of_reason…`; grid-wide audit asserts | `test_engine_timing.py::TestDecisionAssignmentValidation` (validate_assignment / tuple coercion), `test_leak_hold.py::test_hold_is_persisted_to_the_decision_log`. Server-side `actuated=True` flip is out of this package (server I6). |
| 9 | `test_inv9_seasonal_multiplier_precedence_species_category_constants` (7 layer combos incl. fern outdoor → constants, not fern indoor), `_preferred_water_hours_never_gate_without_window_rows`; grid `season`/`windows` | `test_plant_db.py::test_category_defaults_*`, `test_engine_timing.py::TestNoWindowsAllowAllHours`, `::test_outdoor_fruit_tree_summer_emits_seasonal_boost` |
| 10 | `test_inv10_spike_is_ignored_by_the_decision_and_raw_rows_are_untouched`, `test_inv10_short_series_spike_is_not_filtered_current_behavior`; grid asserts readings untouched for every case; cleaning properties | `test_cleaning.py` (16) |
| 11 | `test_inv11_hold_window_edge_is_inclusive` (exactly 24h holds with "(0.0h left)", +1s releases), `test_inv11_hold_is_never_written_as_an_irrigation_event` (open → ack → force → resolve sequence, 0 events, log codes); grid `leak` | `test_leak_hold.py` (15: ack holds, force doesn't bypass, resolve releases, ±60s/600s expiry, other cluster, outranks cooldown); escape hatch `POST /irrigators/{id}/start` is server scope (I11 in 00-tests §6) |

## Properties (Hypothesis, `derandomize=True`, `deadline=None`, ≤150 examples, `database=None`)

cleaning: output = input rows once each, ASC, each numeric value verbatim-or-None, inside `SENSOR_PHYSICAL_RANGES`, input not
mutated; `clean_readings_desc == reversed(clean_readings)`; < 5 points ⇒ range gate only; flat runs untouched;
`clean_readings_around` preserves the partition and equals joint cleaning. timing: `season_for` = meteorological season of the
**local** month (bad tz → UTC) and southern = +6 months; hourly census of 2026 per tz (Rome spring 2207 h / autumn 2185 h,
Sydney 2209/2183 — DST); local-midnight boundaries vs UTC; `seasonal_multiplier` precedence plant > category > built-in;
bundled-DB multipliers ∈ [0.1, 1.6]; quiet window and its swap partition the day, `start==end`/`None` ⇒ off; window = any row,
no rows ⇒ allowed, mask 0 / start==end never match. stress: each indicator fires exactly by its rule, no exceptions over the
valid domain. trends: labels consistent with delta/threshold, frequency flags exclusive and counted from `start`+duration
only. fallback: always IRRIGATE, duration 2, interval ∈ [6, 24], monotone non-increasing in temperature, NO_DATA when no
temp/config. plant_needs: `parse_moisture_target` never raises; aggregates.

## Observed behavior pinned (not fixed) — candidates for REFACTOR_NOTES.md

1. `test_inv5_inline_literals_current_behavior` / grid `series/temp-only`: a sensor-path decision can carry **zero reasons**
   (temperature-only data, in-range): SKIP, confidence 0.5, `primary_code` NULL in `decision_logs`, `reason_text` "no specific conditions".
2. `test_inv2_stress_rule_keys_on_average_current_behavior`: critical stress keys on the cluster **average** (10 % + 60 % → no
   WATER_STRESS; CONFLICT 1-min burst instead) — contrasts invariant #2 (already noted in 00-smells A1).
3. `test_inv5_thresholds_are_bound_at_import_current_behavior`: patching `greenhouse_core.constants.X` has no effect on the engine;
   only `greenhouse_core.logic.engine.X` does (import-time binding). Switching to `constants.X` access would change test-visible behavior.
4. `test_clean_readings_is_not_idempotent_current_behavior`: `[0,0,0,10,0,10]` → once `[…,None,0,10]`, twice `[…,None,0,None]`.
5. `test_inv10_short_series_spike_is_not_filtered_current_behavior`: with < 5 readings a lone 10 % among 55 % drives SENSOR_VERY_DRY.
6. `test_parse_moisture_target_inverted_and_negative_current_behavior`: no validation ("65-45" kept inverted; "-5-10" → default).
7. `test_plant_needs_zero_is_treated_as_missing_current_behavior`: truthiness filter drops 0 °C / 0 % bounds
   (same idiom in `trends.py` temperature lists: a 0.0 °C reading is ignored for the temperature trend — observed, not separately pinned).
8. Grid-pinned edges: cooldown and leak hold are **inclusive** at exactly 6 h / 24 h; a vacation ending exactly now is still
   active ("returns in 0d"); `precipitation_mm` None/missing ⇒ 0; any `environment` other than "indoor" (e.g. "greenhouse")
   gets the weather rule but the indoor seasonal table; critical stress, water warning and the no-sensor fallback **bypass
   vacation rationing** (`vacation/active-tiny-exhausted/no-sensors-fallback` irrigates 2 min; `…/m25` irrigates 3 min) and the
   fallback bypasses windows; light thresholds use the **UTC** month (`seasonal_light_factor()`) while seasons use the prefs tz
   (`season/mar01-*` cases).

## Normalization

None. Payload JSON is recorded as sha256 + equality flag (lossless, see above); nothing is time-normalized — every case
freezes its own instant.

## Determinism evidence

```
uv run pytest tests/test_contract_decision_grid.py tests/test_invariants_engine.py tests/test_properties_logic.py -q   # ×2 → 141 passed, 141 passed
… -p xdist -n 2                                                                                                    # → 141 passed
TZ=America/New_York uv run pytest …                                                                                 # → 141 passed
sha256 of tests/golden/engine/** before == after all runs (369e30c1…)
uv run ruff check / ruff format --check on the 4 files → clean
existing area tests (8 files above) → 170 passed
```
Whole grid ≈ 16 s single process; full new suite ≈ 30 s.

## Production lines the tests guard (mutation targets)

`logic/engine.py`: 125-127 (missing cluster), 129 (`evaluated_at`), 131-144 (NO_PLANTS, confidence 0.0), 149-153 (leak first),
155-159 (cooldown second), 166-181 (quiet unless bypass; message format), 183-194 (`_finalize` override reason + persist),
196-198, 200-223 (hours=24 snapshot; `not sensors or not has_data` → fallback), 225-235 (base SKIP/2/12/0.5), 237-246 (order
water-warning → stress → window), 248-266 (adjustment order, seasonal before vacation), 278-290 (quiet resolution, `int()` casts),
302-319, 334-377 (season key, layer loop, `== 1.0` no-op, `round(interval / m)`, clamp, `< 1.0` HOLD), 401-455 (vacation:
`ceil`/`floor`, 0.95 usable, `>=` comparisons, MIN_RUN), 457-475 (`actuated=False`, swallow), 499-518 (`since = now - hold`,
`hours_left` max/format, duration 0, interval 24), 522-547 (`!= "start"`, `>` latest, message), 554-580 (`== "indoor"`,
`hours=6`, `or 0.0`, `<= 2.0`, MAX interval), 584-594, 616-627, 631-933 (every comparison operator, step, clamp and reason
payload of the rule functions). `logic/sensors.py` 34-71 (per-sensor cleaning, `light > 15`, min/max/mean, sorted warnings).
`logic/stress.py` 27-64. `logic/trends.py` 13-69. `logic/fallback.py` 52-107 (`<=` temperature bands, ±4/+6). `logic/timing.py`
`_hour_in_range`, `is_within_irrigation_window`, `is_within_quiet_hours`, `season_for`, `seasonal_multiplier`.
`logic/cleaning.py` all. `learning/issues.py` 23-160 + `detect_conflicts` (alert list content reaches the payload).

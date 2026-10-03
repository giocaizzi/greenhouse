# WP8 review — R1 (line-level, behavior preservation)

Worktree `/home/user/gh-wp8`, branch `refactor/wp8-engine`, `327ee12..15dcd2e` (17 commits).
Reviewer: R1. Read-only; nothing in the worktree was modified (`git status --porcelain` empty).

## Overall verdict: **APPROVE — no blockers.** 2 NITs (wording / typing-only), 0 behavior changes found.

## Method

1. Read every production diff line by line (`git show`, `git show -w` for the device commits).
2. **Differential call-trace probe** (scratchpad `probe.py`): every `engine_grid.build_cases()` case
   (1618) plus 23 extra quiet/vacation cases with `bypass_quiet_hours` flipped = **1641 cases**, run
   through the real `run_case` with the repository and the weather client wrapped in a recording
   proxy. Each run records the ordered list of every repo / weather call (method + args repr) and the
   full golden record (decision dump, `decision_logs` rows incl. `primary_code`, `reason_text`,
   payload sha256, `decision_log_id`, events written, readings unchanged).
   - **Fault injection:** for each case, one extra run per call index *k* where the *k*-th repo/weather
     call raises → **32,937 fault runs**. These show the exception paths: what gets called after the
     fault, whether `add_decision_log` still runs, and what propagates.
   - Base `327ee12` vs head `15dcd2e` (with injection): output JSON is **byte-identical** (41.6 MB, `cmp`).
   - Each intermediate engine commit (`cb2a8ac … 3cd1ecb`, 12 commits) vs base (no injection):
     **byte-identical**. Every commit can be bisected.
   - The probe covers every repo method the engine reaches: `get_cluster, get_plants_in_cluster,
     get_active_alert, get_irrigator_for_cluster, get_recent_events, get_effective_config,
     get_preferences, list_irrigation_windows, get_recent_readings, get_readings_around,
     get_sensors_in_cluster, get_irrigation_config, get_active_vacation, irrigator_consumption_liters,
     add_decision_log`, plus `weather.get_forecast`. 14 cases reach the consumption query.
3. AST comparison with annotations and docstrings stripped (scratchpad `astcmp.py`) for all 6 device
   files, for timing before T8.11, and for engine at T8.0.
4. Targeted tests: `test_contract_wp8_gaps, test_contract_decision_grid, test_invariants_engine,
   test_engine_timing, test_timing, test_vacation_rationing, test_leak_hold, test_logic,
   test_contract_imports, test_contract_mutation_gaps, test_moisture_target_range` → **303 passed**.

## Per-commit verdicts

| Commit | Task | Verdict | Notes |
|---|---|---|---|
| `cb2a8ac` | T8.0 annotations engine/timing | OK (1 NIT) | AST diff is only the imports, a `TYPE_CHECKING` block, the `RainForecast` Protocol and `cast(...)`, which is an identity. The local `AnnAssign` is not evaluated at runtime. The string annotations `"Cluster"` are safe without `__future__`. See N2. |
| `d1df481` | T8.1 named constants | OK | Every value and type matches: `SECONDS_PER_HOUR=3600` (int), `SECONDS_PER_DAY=86400` (int), `SNAPSHOT_LOOKBACK_HOURS=24`, `CONFIDENCE_BASELINE=0.5`, `WEATHER_FORECAST_HOURS=6`, `WEATHER_SKIP_PRECIP_MM=2.0` (constants.py:260-268, not touched by WP8). `moisture_target_range(d)` is exactly `parse_moisture_target(d.get("soil_moisture_target", "45-65"))` (plant_needs.py:41-43). The dropped `engine.parse_moisture_target` is not a patch target in libs/ or tests/. The UPPERCASE-constant count in `vars(engine)` goes up (test_invariants_engine.py:234). |
| `242578a` | T8.16 device annotations | OK | Stripped AST: only the `Any` import and `raw = json.loads(...); return raw` in profile.py, which returns the same object. |
| `b6538e3` | T8.15 docstrings | OK | `git diff -w` changes docstring lines only. Stripped AST is identical. |
| `326594a` | characterization tests | OK | Test-only. Adds one new file and edits no frozen test. |
| `aee2123` | T8.2 `_tz_name` | OK | Same `get_preferences` call at the same point in all 3 sites (traces identical). |
| `573e1a6` | T8.3 `_record` | OK | `if persist: _persist; return` in all 4 sites. |
| `c421bfe` | T8.4 `_finish(override_window=)` | OK | `quiet_window if bypass_quiet_hours else None` is truth-table-equal to `quiet_window is not None and bypass_quiet_hours`. The reason text is verbatim, and the reason is appended before `_record`, same as before. |
| `eb4805d` | T8.5 `_pre_gates`, `_quiet_hours_skip` | OK | Order stays NO_PLANTS → leak hold → cooldown, and cooldown is returned as-is (`None` falls through). The quiet-hours message is verbatim. |
| `2de5731` | T8.6 `_EngineInputs` | OK | Same read order (snapshot → trends → stress → learning → care). Keyword args are evaluated left to right (temp → humidity → water needs), the same order as before. `get_irrigation_config` is still read only on the fallback path. |
| `8897cc6` | T8.7 `_evaluate_rules` / `_apply_adjustments` | OK | All six exits return the decision unchanged to one `_finish`, and nothing runs between the return and `_finish`. Moving `override` after `_evaluate_rules` has no effect: it is a pure conditional with no side effects. The two terminal `if`s are still sequential. The fallback path still skips windows. Terminal paths still skip vacation. The dropped `_apply_window_rule(cluster, decision)` params were never read, and the method has no other caller or subclass in libs/ or tests/. Fault-injection traces are identical, so a mid-pipeline raise still persists nothing and propagates the same exception. |
| `d4858d1` | T8.8 dead params | OK | None of the 6 callers in base passes `sensor_snapshot`, `stress_indicators` or `trends`, so the result is the same `None` / `StressIndicators()` / `Trends()`. |
| `73fa77f` | T8.9 soil helpers | OK | Same order: band, then extremes, then the short-circuit conflict test. Dry names are still built before wet names. The lambdas read `CONFLICT_WET_MARGIN` from engine globals at call time. `avg_soil` reads the same attribute as before, and the snapshot is never mutated in between. Each branch assigns the same fields, the f-strings are verbatim, and the reasons come in the same order. |
| `5b66434` | T8.10 vacation / seasonal helpers | OK (1 NIT) | `_seasonal_overrides` and `_scaled_interval` are verbatim, with the same float order (`int(round(i / m))`, then clamp). `_binding_max_minutes` arithmetic is verbatim. Guard order and query order and count are unchanged. On the reorder see N1: it is unobservable for any value the ORM can produce. |
| `3cd1ecb` | T8.11 `active_quiet_window` → timing | OK | Body is verbatim. Repo calls are still `get_effective_config`, then `get_preferences`, once each. The `effective[...]` reads moving after `get_preferences` (which can insert a row) would matter only if a key were missing. `get_effective_config` always builds both keys (`_CONFIG_PATCHABLE_FIELDS`, repository.py:517-526, loop at 608-617), and no test mocks it. `int()` casts ran after `get_preferences` before and still do. No code patches `engine.is_within_quiet_hours`. The pinned seams `engine.time`, `engine.season_for` and `engine.seasonal_light_factor` are still looked up through engine globals at call time (engine.py:163, 356, 947), and `test_contract_imports` passes. Patching `engine.MIN_COOLDOWN_HOURS` still takes effect (engine.py:533, 549). |
| `dd2a473` | extra device annotations | OK | Stripped AST: only the `Any` import in 4 modules. All 4 use `from __future__ import annotations`. `DeviceHealthState.raw` changes only a string annotation on a stdlib dataclass, and no pydantic, `get_type_hints` or `fields()` consumer reads it. |
| `15dcd2e` | hand-off docs | OK | — |

Logger name (`logging.getLogger(__name__)`) and both log messages (`"failed to persist decision log"`,
`"learning advisory unavailable"`) are unchanged. `_persist` and `_attach_learning_alerts` are not
touched.

## Findings

### N1 (NIT) — T8.10 / hand-off deviation 3 overstates "cannot raise"
- **Where:** engine.py:438-446 (`_apply_vacation_budget`: consumption query, then `_binding_max_minutes`); commit body of `5b66434`; WP8.md "Deviations" #3.
- **Evidence:** scratchpad `vac_probe.py` calls `_apply_vacation_budget` with a fake repo:
  - `reservoir_l="10"` (str) or `Decimal("10")`. Base: `TypeError` after `[get_active_vacation, get_irrigator_for_cluster]`. Head: the same `TypeError` after `[…, irrigator_consumption_liters]`. Head adds one read-only SELECT before the same exception.
  - `nan` and `inf` give identical call lists and outcomes in base and head. `inf` → `OverflowError` in `math.floor`, which ran after the query in base too.
- **Why it is not a blocker:**
  - `Irrigator.reservoir_l` is `Mapped[float | None] = mapped_column(Float)` (models.py:79), so the ORM never hands the engine a str or Decimal.
  - `irrigator_consumption_liters` only reads (`session.get` + `scalar`, repository.py:494-513). It does not commit or expire.
  - Its autoflush finds nothing to flush, because `get_irrigator_for_cluster` autoflushed just before and nothing touches the session in between.
  - `starts_at`/`ends_at` are `nullable=False` ints (models.py:387-388), and `vac.starts_at` was already read before the query as its `since=` argument.
  - The 14 grid cases that reach this path, plus fault injection at the query, give identical traces.
- **Fix (pick one):** (a) Reword the commit/hand-off claim to "cannot raise for any value the `Float` / non-null `Integer` columns can hold; for corrupt non-float data the same exception now follows one extra read-only SELECT". (b) To make the order strictly identical, split out `_allowed_cum_liters(reservoir_l, starts_at, ends_at, now)` and compute it before the query, leaving `headroom` and `floor` after it.

### N2 (NIT, typing-only) — `RainForecast.get_forecast` default duplicates the literal 6
- **Where:** engine.py:114 `def get_forecast(self, hours: int = 6) -> ...: ...`
- **Evidence:** T8.1 replaced the call-site literal with `WEATHER_FORECAST_HOURS`, but the Protocol stub keeps `= 6`. No runtime effect (Protocol, never instantiated, the engine always passes `hours=` explicitly).
- **Fix:** Use `hours: int = ...` in the stub (the Protocol idiom), or leave it as is. Optional.

### Info (no action)
- `timing.active_quiet_window` annotates `Mapping`, which is imported only under `TYPE_CHECKING` (timing.py:25-26, `__future__` annotations). `typing.get_type_hints(active_quiet_window)` would raise `NameError`, but nothing calls it. This is the same pattern as elsewhere in the repo.
- Device modules and engine gain `Any` and `cast` as module attributes. Registry discovery scans for adapter subclasses only, so this is harmless.

## Blockers
None.

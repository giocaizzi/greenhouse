# WP6 hand-off — core logic + learning + stats

Worktree `/home/user/gh-wp6`, branch `refactor/wp6-logic`, based on `090b5fe`. Not pushed, not merged, not rebased.
Every pytest run: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …` (lock holds ≤ ~5 min;
large subsets split into chunks). Sprint mode: no optional tasks.

**Status:** all 13 tasks done (T6.0–T6.12) plus the pre-authorized gap-test commit. Every commit is green under its
subset. WP gate green.

## Commits

| Task | Commit | Evidence (task subset) |
|---|---|---|
| T6.0 annotations (6 modules strict) | `be229bb` | `$CORE $ENGINE $SETTINGS` + learning/utils/stats 533 ✓; `$RENDER` 307 ✓ |
| T6.1 utils constants | `b9a2bd7` | `$CORE $ENGINE $SETTINGS` + utils/learning 521 ✓; `$RENDER` 307 ✓ |
| T6.2 logic constants | `97fabaa` | `$ENGINE` + cleaning (+cov) 417 ✓; `$RENDER` 307 ✓ |
| T6.3 issues constants | `f45c796` | `$ENGINE` + learning, insights, alerts, learning mutation group (+cov) 457 ✓; `$RENDER` 307 ✓ |
| gap tests | `c6d6578` | `tests/test_contract_wp6_gaps.py` 62 ✓; 65/65 proof mutations killed (stress 8, trends 17, issues 39, stats 1) |
| T6.4 sensors | `47a88e2` | `$ENGINE` + cleaning, sync_snapshot, gaps 390 ✓; `$RENDER` 307 ✓ |
| T6.5 trends | `4f566b8` | `$ENGINE` + gaps 384 ✓; `$RENDER` 307 ✓ |
| T6.6 stress (G+) | `2da94d8` | `$ENGINE` + gaps 390 ✓; `$RENDER` 307 ✓; `C(gc.logic.stress)` in 4 chunks 181+309+236+180 ✓ |
| T6.7 fallback | `c92a8da` | `$ENGINE` + gaps 390 ✓; `$RENDER` 307 ✓ |
| T6.8 detect_conflicts | `ca6b023` | `$ENGINE` + learning, insights, alerts, gaps 451 ✓; `$RENDER` 307 ✓ |
| T6.9 detect_issues | `5337d24` | the same subsets: 451 ✓ and 307 ✓ |
| T6.10 profiling | `4a5c48e` | `$ENGINE` + learning, gaps, `D(gc.learning.profiling)` 489 ✓; `$RENDER` + test_tui 398 ✓ |
| T6.11 stats aggregation/report | `3a360cf` | `$CORE` + `D(gc.stats)` + test_contract_stats + gaps 179 ✓; `$RENDER $TUI` 410 ✓ |
| T6.12 export_csv | `56805a1` | `$CORE` + `D(gc.stats)` (without the TUI) + test_contract_stats + json_output + gaps 455 ✓ |

Every commit also passed: `ruff check` and `format` on touched files, `lint-imports` (10 kept), mypy on touched files,
`git status --porcelain tests/golden` empty.

## WP gate (sprint mode: union + `$CORE` + `$ENGINE` + one FULL, seed 0)

The FULL run was split into 4 chunks to keep each lock hold short. Together they cover the whole collection:
2950 tests collected = 1136 + 236 + 174 + 1404 passed, 0 failed. `$CORE`, `$ENGINE` and every task subset are included.

Static checks:
- `make typecheck`: 67 modules OK.
- `uv run lint-imports`: 10 kept.
- `ruff check libs tests` and `ruff format --check`: clean.
- `git diff 090b5fe -- tests/golden 'tests/**/test_contract_*.py'`: only the new file `tests/test_contract_wp6_gaps.py`.

Sizecheck before → after:
- **Before:** 10 hits in WP6 files: `get_recent_sensor_data`, `analyze_historical_trends`, `detect_stress_conditions`, `temperature_based_decision`, `detect_issues`, `detect_conflicts`, `compute_sensor_response`, `get_plant_profile`, `get_irrigation_stats`, `export_csv`. Also C901 on `print_stats_report`.
- **After:** 0. All 8 WP6 files are clean under sizecheck and under ruff C90/PLR0911/12/15 at 8 with `--isolated`. `issues.py` has 399 lines.

## Mutation (M-pre / M-post)

Results are in `refactor/wp-handoff/mutation/wp6-*-{pre,post}.jsonl`; the function map is `wp6-function-map.json`.

**Tooling deviation:** I ran the tool through a chunked driver (`mdrive.py`, scratchpad). It imports `mutate.py`,
holds the test lock for at most ~3 min per chunk and resumes from the results file. Runs happened in a scratch
worktree, `gh-wp6-mut`, now removed. Each run restored the target file and the worktree was clean afterwards.

| Module | M-pre (unmodified, default group) | M-post (default group + gap file) | Regressions |
|---|---|---|---|
| sensors | 29/30 (1 eq.) | 18/19, 94.7 % | 0 |
| trends | 51/66 → all 15 survivors killed by gap tests | 67/67, 100 % | 0 |
| stress | WP0 run reused (file unchanged): 52/66 | 67/80, 83.8 % | 0 |
| fallback | 37/38 (1 eq.) | 36/37, 97.3 % | 0 |
| issues | sample 70/145 (seed 0): 42/70 → all 28 survivors killed by gap tests | sample 70: 67/70, 95.7 % | 0 |

**Equivalent survivors:**
- `return SensorSnapshot()` → pass. The result has the same values; only `model_fields_set` differs, and nothing reads it for snapshots.
- fallback: `base.duration_minutes = DEFAULT…` → pass. The constructor already sets that value.
- stress: the trailing `return None` → pass, ×6. Falling off the end also returns None.
- stress: the 7 `_low_light` mutants on `min_lux > 0` and the `0` defaults. Daytime light is > 15 lux, and the default sits behind the `care` guard. These are the WP0-listed equivalents.

**New post-only issues survivors (not in the M-pre sample).** I did not add tests after restructuring. I request
characterization tests for:
- (a) `_chronic_underwatering_alert`: `max_recent < target_min` → `<=`. Not equivalent: max exactly equal to the target.
- (b) `max(..., default=0)` → `1` in the same function. Not equivalent: the readings exist but every moisture value is None, so the message shows a 0 % peak.
- (c) `detect_issues`: the no-profiles `return alerts` → pass. Same output; only extra plant and care reads.

## Function map (old → new)

- `get_recent_sensor_data` → `_mean_or_none`, `_per_sensor_snapshot`.
- `analyze_historical_trends` → `_pooled_clean_readings`, `_moisture_trend`, `_temperature_trend`, `_apply_reading_trends`, `_apply_cadence_flags`.
- `detect_stress_conditions` → `_water_warning`, `_low_env_humidity`, `_low_light`, `_water_stress`, `_heat_stress`, `_over_watering`.
- `temperature_based_decision` → `_config_fallback`, `_temperature_interval`.
- `detect_issues` → `_learned_profiles`, `_blocked_drip_alert`, `_drainage_alert`, `_chronic_underwatering_alert`, `_sensor_alerts`.
- `detect_conflicts` → `_latest_moisture_by_sensor`, `_conflict_band`, `_split_dry_wet`, `_conflict_alert`, `_wet_conflict_alerts`, `_overwater_conflict_alerts`, `_cluster_plant`, `_low_light_alerts`, `_low_env_humidity_alerts`.
- `compute_sensor_response` → `_moisture_windows`, `_response_from`.
- `get_plant_profile` → `_event_responses`, `_aggregate_profile`.
- `get_irrigation_stats` → `_empty_stats`, `_count_event`.
- `print_stats_report` → `_print_counts`.
- `export_csv` → `_csv_event_row`, `_write_event_rows`.

## Strict list / ratchet (for the integrator)

- `refactor/mypy-strict.txt` gained `utils.py`, `stats.py`, `logic/trends.py`, `logic/fallback.py`, `learning/issues.py` and `learning/profiling.py`. `logic/sensors.py` and `logic/stress.py` were already on the list.
- **Ratchet:** drop the per-file ignores in `pyproject.toml` for `learning/issues.py` (C901, PLR0912, PLR0915), `logic/sensors.py` (C901), `logic/stress.py` (C901, PLR0912), `logic/trends.py` (C901, PLR0912) and `stats.py` (C901). All are now clean.
- No size-exception entries are proposed.

## Deviations and things to review hardest

1. **History rewrite before hand-back.** The gap-test commit was amended twice, as M-pre for trends and then issues finished, and the later commits were cherry-picked on top. So there is still a single gap commit. The T6.6 body says "proofs in f6c1b8e"; that commit is now `c6d6578`.
2. **Test subsets.** T6.11/T6.12 added `tests/test_contract_stats.py`, because it is the only test pinning `print_stats_report` and it is not in `D(gc.stats)`. T6.12 left `tests/cli/test_tui.py` to the FULL gate.
3. **T6.3.** The 48 h humidity window (target §9 site 284) uses `LEARNING_DRAINAGE_LUX_LOOKBACK_HOURS`, as the table assigns. The name is misleading for humidity; consider a rename in the final sweep.
4. **T6.0.** `typing.cast` is used for strict narrowing in trends, fallback and profiling (a runtime no-op). `utils` now also exposes `Any` and `SEASONAL_LIGHT_FACTOR_BY_MONTH`. The imports golden uses superset semantics, so this is fine.
5. **Look hardest at:**
   - **stress (G+, wants two reviewers):** that detectors are evaluated before assignment, and the positive-comparison guards.
   - **sensors:** aggregates are now built from the pooled cleaned readings, in the same order.
   - **issues:** `_SensorReading` lives under `TYPE_CHECKING`, and `_learned_profiles` is a walrus dict comprehension.
   - **profiling:** the delta lists are now built after the drainage read. This reordering is pure.

## Dead code

None removed. Evidence collected for the final sweep:
- `StressIndicators.any_critical()` in `logic/decision.py` is not a WP6 file. Its only reference is the curated catalogue in `refactor/gate1/mutate.py:1741`.
- `stats.print_stats_report` and `stats.export_csv` are on the frozen `greenhouse_core.stats` surface, and only tests use them. Removing them needs removal of characterization tests (rule 4).

## REFACTOR_NOTES requests

- Record deviations 1–4 and the chunked mutation driver.
- Bugs preserved and untouched in behavior: B-18, B-24, critical stress keyed on the average, light thresholds on the UTC month.
- No values changed, so `plugin/.../LOGIC.md` needs no update.

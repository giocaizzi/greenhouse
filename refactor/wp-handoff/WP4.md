# WP4 hand-off — server services (PARTIAL: 8 of 17 tasks done, 9 blocked)

Worktree `/home/user/gh-wp4`, branch `refactor/wp4-services`, based on `5267aa6`. Not pushed, merged or rebased.
Every pytest run went through `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …`.

**Status.** The tasks whose code was fully covered by their subset, and that touch no M-pre survivor, are done:
T4.0, T4.1, T4.2, T4.6, T4.7, T4.8, T4.13, T4.16. The remaining tasks are **stopped by the coverage precondition
(§0.4 G.1)**: T4.3, T4.4, T4.5, T4.9, T4.11, T4.14, T4.15, T4.17. **M-pre survivors (§0.5)** also block T4.10 and
T4.12. Those two are also G+. For each blocked task a finished, gate-checked draft (mypy strict, ruff, DoD) is in the
session scratchpad, ready to apply once the characterization tests land (see "Drafts").

## Commits

| Task | Commit | Subset → result | DoD (body / CC / nesting) |
|---|---|---|---|
| T4.0 annotations (6 files) | `01aae77` | `$RENDER $CHECK` + `test_contract_pump_watcher` + `test_contract_health_monitor` + `D(…)` ×6 → **969 passed** | n/a |
| T4.1 named constants (5 files) | `b5e01f8` | `$CORE` + the T4.0 set minus `D(charts)` → **940 passed** | n/a |
| T4.2 `moisture_target_range` | `8f48aa1` | `$RENDER D(health) D(forecast)` → **483 passed** | n/a |
| T4.6 `data_quality.build_report` | `07f355c` | `$RENDER D(data_quality)` → **472 passed** | build_report 14/1/0; _sensor_issues 33/4/2; _plant_issues 26/4/2; _cluster_issues 27/4/2; _duplicate_device_issues 20/3/2; _count_by_code 4/2/1 |
| T4.7 `_aggregate_band` | `801dadc` | `$RENDER D(charts)` → **553 passed** | _threshold_for_cluster 21/6/2; _aggregate_band 7/2/1 |
| T4.8 `repo.get_plant` in charts | `e1ab3b2` | `$RENDER D(charts)` → **553 passed** | n/a |
| T4.13 `search` hit builders | `84dd177` | `$RENDER tests/server/test_search.py tests/cli/test_tui.py` → **408 passed** | search 10/2/1; _cluster_hits, _plant_hits, _sensor_hits, _irrigator_hits 22/1/0 each |
| T4.16 `SystemHealthService.pulse` | `7e9805b` | `$RENDER D(system_health)` → **482 passed** | pulse 31/1/0; _sensor_devices 17/4/2; _overall_status 5/3/1 |

These checks were green for every commit:
- `uv run ruff check` and `ruff format --check` on the touched files;
- `uv run lint-imports`: 10 kept, 0 broken;
- `make typecheck`: 42 modules OK;
- `git status --porcelain tests/golden`: empty.

Every log message text and logger is unchanged. No bug site was touched.

## Coverage precondition — how it was measured

1. One run over the union of all WP4 task subsets on the unmodified code: 67 files, **1103 passed**. It used
   `--cov=greenhouse_server.services --cov-branch --cov-context=test`.
2. Each task was then evaluated against **its own subset** by filtering the coverage contexts to that subset's test
   files (script `scratchpad/wp4/covtask.py`; data `scratchpad/wp4/coverage-pre.db`).
3. To tell "uncovered by the subset" from "uncovered by everything", a second run used the same flags on the full
   suite (data `scratchpad/wp4/coverage-full.db`). It had 2796 passed and 1 failure. The failure was my own artifact:
   a stray `services/.ruff_cache` broke the package-data golden. I deleted it, and the later `FULL` run is green.
   **Every gap below is also uncovered by the full suite**, with one exception, noted for T4.15.

### Characterization tests requested (line numbers at `5267aa6`)

| Task | File | Missing line / branch | What a test must pin |
|---|---|---|---|
| T4.3 | `health.py` | L41; branches 40→41, 74→78, 78→84, 84→90 | `compute_score` of an unknown plant_id (the empty-score dict). Readings that carry no soil value (soil pct None). A plant **without** temp bounds, and one **without** humidity bounds (those pcts stay None and drop out of the score). |
| T4.4 | `forecast.py` | L71, L107, L129-133; branches 70→71, 106→107, 128→129, 131→132, 131→135 | A learned profile with drainage ≥ 0 (falls back to -2.0 %/h). ≥ 3 profiled sensors (confidence 0.7). An outdoor cluster with a weather client that returns a forecast, once with precip > 2.0 (weather_skip + reason text) and once with precip ≤ 2.0 (precip reported, no skip). |
| T4.5 | `maintenance.py` | L84; branches 78→29, 83→84 | A `low_light` alert actually raised. A sensor with ≥ 3 daytime lux samples but no plant. |
| T4.9 | `insights.py` | L46; branch 45→46 | Two maintenance alerts of the same type (or a learning alert repeating a maintenance type): the second is dropped. |
| T4.11 | `health_monitor.py` | branch 266→243 | `backfill_from_history` for an all-`water_warning` sensor whose SENSOR_FAULT alert is already open: no second raise. The LOW_BATTERY twin of this branch is covered. |
| T4.12 | `pump_watcher.py` | L296-297; branch 279→290 | The commit fails **and** the rollback raises (swallowed). 279→290 is `monitor is None`, which is unreachable (`_lazy_monitor` always returns an instance). Justify it rather than test it. |
| T4.14 | `anomaly.py` | L117, L124; branches 116→117, 123→124 | A window of ≥ 10 readings with < 10 soil values: no drift check, but the stale check still runs. L124 looks **unreachable**: ≥ 10 soil values ⇒ a baseline of ≥ 9 = `_MIN_READINGS - 1`. Justify it rather than test it. |
| T4.15 | `efficacy.py` | branch 40→85; L56 is covered **only** by `tests/cli/test_contract_tui_actuation.py` (outside the subset) | `score_cluster` for a cluster without an irrigator. For L56, either add the TUI actuation file to the T4.15 subset or add a server-side test (start event with duration 0/None). |
| T4.17 | `charts.py` | branch 268→270 | An overlay where some reading has `soil_moisture=None` (humidity/light only). |

## M-pre — `services/pump_watcher.py` (before any WP4 commit)

`refactor/gate1/mutate.py --module services/pump_watcher.py --lines 77-83 --lines 117-180 --lines 213-297`. These are
the mutation-target lines from `10-safety-ingress-devices.md`. The test set was the runner's `pump` group:
`test_contract_pump_watcher`, `test_pump_watcher`, `test_contract_pipeline`, `test_irrigators`. The baseline was
green (139 passed).

**Result: 74 mutants, 62 killed, 12 survived (83.8 %).** The results are in
`refactor/wp-handoff/mutation/pump_watcher-pre.jsonl`.

**Process note.** The first attempt took the lock per mutant and was starved by WP3's long runs: 6 mutants in about
45 minutes. I stopped it with SIGINT; the runner restored the file and the worktree was verified clean. I re-ran with
`--resume` in 4 chunks. Each chunk is `flock /tmp/greenhouse-tests.lock env MUTATE_NO_LOCK=1 … --timeout 300`, so
every pytest run is still under the lock and one chunk held it for at most about 21 minutes. One record written
around the interrupt was dropped and re-run.

Survivors, which go to the orchestrator for kill tests or justification:

1. `watch`: `read_error or state.offline` → `and`.
2. `watch`: `read_error or "device offline"` → `and` (only the log text).
3. `watch`: completed `read_failures: 0` → `-1` and `+1`. Target §3.6 calls this out explicitly ("completed keeps
   read_failures: 0"), but no test pins it.
4. `watch#2`: `consecutive_failures = 0` deleted. The reset after a good read is unpinned.
5. `watch`: `last_failure_msg = …` deleted, and the abandon `logger.warning` deleted. The log is unpinned.
6. `_handle_trip`:
   - `stop_ok = False` → `True`;
   - `stop_msg = ""` deleted;
   - `stop_msg = f"adapter.stop raised: {exc}"` deleted;
   - `logger.exception("Pump watcher could not stop …")` deleted;
   - `session.rollback()` deleted.

   An `adapter.stop` that raises is not pinned by the pump group.

Because of 3–6, **T4.10 and T4.12 were not started.** They are G+, and survivors 3 and 4 sit exactly on the code
T4.10 restructures.

## M-post — `services/pump_watcher.py` (after T4.0 + T4.1; annotations and default-constant only)

Selection: `--function PumpWatcherService.__init__ --function PumpWatcherService.watch --function
PumpWatcherService._handle_trip`, because the line ranges shifted when T4.0/T4.1 added imports. Same pump test group,
run in chunks under the lock with `--timeout 300` and an empty function map (no pump_watcher decomposition has
landed). Results: `refactor/wp-handoff/mutation/pump_watcher-post.jsonl`.

**90 mutants, 74 killed, 16 survived (82.2 %). M-post: kill rate OK; 0 killed-in-pre identities now
survive; 0 unmatched. The summary exits 0.**

The 16 survivors are the 12 M-pre survivors plus 4 mutants outside the M-pre line ranges (`watch` L112-116 at
baseline, which are mutated only now):
- `consecutive_failures = 0` (init) → `-1` and `+1`;
- `max(0.0, float(duration_seconds))` → `max(-1.0, …)`;
- `last_failure_msg: str | None = None` deleted.

Hand these four to the orchestrator together with the M-pre survivors.

## WP gate (on the 8 committed tasks)

- Union of the done tasks' subsets plus `$CORE` (`scratchpad/wp4/gate-union.txt`, 53 files): **1035 passed** (5m42s).
- `FULL` at `-n 2`: **2797 passed** (9m07s).
- `FULL_SEED2` was **not run**. My brief asked for the union plus `FULL` once.
- `lint-imports` OK, `make typecheck` OK, and `git status --porcelain tests/golden` is empty.

## Sizecheck

Listed functions in WP4 files: **12 before, 9 after** (−3: `data_quality.build_report`, `search.search`,
`SystemHealthService.pulse`). Still listed, all blocked tasks:
- `health.compute_score`, `forecast.predict_next_irrigation`, `maintenance.collect_maintenance_alerts`;
- `charts.build_overlay_payload`, `insights.cluster_insights`;
- `pump_watcher.watch` and `pump_watcher._handle_trip`;
- `anomaly.scan`, `efficacy.score_cluster`.

`health_monitor.py` is at 438 lines and stays excepted.

## Strict list additions

`services/{charts,forecast,health,health_monitor,maintenance,pump_watcher}.py`. All 12 WP4 modules are now strict.

## Ratchet edits for the integrator (I1)

- `pyproject.toml` per-file ignore for `services/data_quality.py` (`["C901", "PLR0912"]`) can be **dropped**: the file
  is clean under `--isolated` C90@8 and PLR091x.
- `search.py` and `system_health.py` had no ignores.
- The other WP4 ignores stay until their blocked tasks land: charts C901, forecast C901/PLR0915, health C901,
  maintenance C901, health_monitor PLR0911.

## Proposed size-exception entries

- `libs/greenhouse-server/greenhouse_server/services/health_monitor.py::DeviceHealthMonitor._alarm_message` —
  PLR0911 (8 returns). It is a flat one-message-per-alarm ladder with byte-pinned alert texts, and no task covers it.
  The per-WP DoD runs ruff `--isolated` on touched files, and health_monitor was touched by T4.0/T4.1. The
  alternative is a dict dispatch, which would need its own task.

## Deviations

1. **T4.0 forecast**: `if has_profile` became `if profile is not None`, the identical condition, for mypy narrowing.
   This is the only non-annotation edit.
2. **Partial WP**: tasks were not done strictly in table order. Blocked tasks were skipped and later independent
   tasks done. Each touches a different file, except charts, where T4.7/T4.8 are done and T4.17 is blocked.

Deviations already built into the drafts (for the reviewer once they are applied):
- **T4.3**: `_pooled_clean_readings` returns `list[CleanedReading]`, which is the real type; the target says
  `list[SensorReading]`.
- **T4.11**: `_raise_if_not_open(alarm, sensor, *, cluster_id)` passes the `cluster_id` the loop read once, instead of
  re-reading `sensor.cluster_id` after a possible commit.
- **T4.12**: `_trip_payload` is **not** extracted. It would take 9 inputs; the payload literal stays inline and
  `_handle_trip` is 33 body lines.
  - The lazy `SOURCE_PUMP` import moves into `_log_trip_activity`. `services.alerts` is already in `sys.modules`
    through health_monitor's top-level import, so the move has no side effect.
- **T4.10**: the local `consecutive_failures` is renamed to `failures` (it matches `_outcome(failures=…)`) so `watch`
  fits in 40 body lines. `origin = deadline - duration_seconds` is computed once. The clock, stop-check and sleep
  calls keep their positions and counts.
- **T4.15**: per-event helper `_event_item(...) -> EfficacyItemResponse | None` instead of `_event_items -> list`.
- **T4.14**: it also adds the pure `_latest_soil_zscore(window)` helper so that `_drift_alert` is ≤ 40 body lines.
- **T4.5**: `_light_alert` uses guard clauses (`len < MIN` / `not plant`) to reach nesting ≤ 3. They are exact
  negations, and the evaluation order is kept.
  - The plant lookup is hoisted to once per sensor. It is a pure dict lookup.

## Drafts for the blocked tasks

Path: `/tmp/claude-0/-home-user-greenhouse/13425f2d-7c47-5b83-96d4-7045d3907ad2/scratchpad/wp4/dr/t4NN/<file>.py`.
Folders: `t43`, `t44`, `t45`, `t49`, `t410`, `t411`, `t412`, `t414`, `t415`, `t417`.

Each draft is the file's full content after that task, built on the preceding drafts. Every draft is mypy-strict
clean, ruff clean, and within the DoD (sizecheck and C90@8 silent).

Two drafts must be rebuilt before use:
- `t417` must be rebuilt on the committed `charts.py`: the scratch copy was re-wrapped at 88 columns.
- `t49` (insights) and `t414`, `t415` start from `5267aa6` contents, which still equal HEAD for those files.

## Function map (committed decompositions)

```json
{"build_report": ["build_report", "_sensor_issues", "_plant_issues", "_cluster_issues", "_duplicate_device_issues", "_count_by_code"],
 "_threshold_for_cluster": ["_threshold_for_cluster", "_aggregate_band"],
 "search": ["search", "_cluster_hits", "_plant_hits", "_sensor_hits", "_irrigator_hits"],
 "SystemHealthService.pulse": ["SystemHealthService.pulse", "SystemHealthService._sensor_devices", "_overall_status"]}
```

## REFACTOR_NOTES requests (I4)

- Record the dead branches found by the coverage pass: `anomaly.scan` L123-124 (the baseline-too-short guard) and
  `pump_watcher._handle_trip` L279 `monitor is not None`. Both are unreachable today.
- Record the M-pre survivors above, and their resolution.

## Look hardest at

- T4.13: the per-hit `_cluster_name` lookups must still interleave with the per-entity SELECTs. The comprehensions
  are eager.
- T4.16: `_overall_status` is equivalent to the old if/elif/else.

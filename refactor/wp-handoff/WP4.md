# WP4 hand-off — server services (complete: 17 tasks + 1 test-only commit)

Worktree `/home/user/gh-wp4`, branch `refactor/wp4-services`, based on `5267aa6`. Not pushed, merged or rebased.
Every pytest run used `PYTHONHASHSEED=<seed> flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …`.

This note supersedes the partial hand-off committed at `72f26ba`.

**Path on its first use:** `G = tests/server/test_contract_services_gaps.py`. The note writes `G` after that. Every
restructuring task after `acc4c05` had `G` added to its subset.

**Checks that every commit passed:**
- `ruff check` and `ruff format --check` on the touched files;
- `uv run lint-imports`: 10 kept, 0 broken;
- `make typecheck`: 42 modules;
- `git status --porcelain tests/golden`: empty.

No logger moved module, and every log text is identical.

## Commits (table order)

| Task | Commit | Subset → result | DoD of decomposed functions (body / CC / nesting) |
|---|---|---|---|
| T4.0 annotations (6 files) | `01aae77` | `$RENDER $CHECK` + `test_contract_pump_watcher` + `test_contract_health_monitor` + `D(…)` ×6 → 969 passed | n/a |
| T4.1 named constants (5 files) | `b5e01f8` | `$CORE` + the T4.0 set → 940 passed | n/a |
| T4.2 `moisture_target_range` | `8f48aa1` | `$RENDER D(health) D(forecast)` → 483 passed | n/a |
| **test** (orchestrator decision 1+2) | `acc4c05` | `G` (27 tests); determinism and mutation proofs below | — |
| T4.3 `compute_score` | `53f942f` | `$RENDER D(health) test_charts_plant_health_timeline G` → 486 passed | compute_score 31/2/1; _empty_score 8/1/0; from_care 5/1/0; _in_band_pct 4/2/1; _band_percentages 13/4/1; _composite_score 3/2/1; _pooled_clean_readings 7/2/1; _first_profile_efficiency 5/3/2 |
| T4.4 `predict_next_irrigation` | `4f4c083` | `$RENDER D(forecast) G` → 453 passed | predict_next_irrigation 40/4/2; _forecast_sensor 32/4/1; _rain_outlook 14/4/3; _no_data_forecast 9/1/0; _confidence_for 5/3/1 |
| T4.5 `collect_maintenance_alerts` | `1faf371` | `$RENDER $CHECK D(maintenance) G` → 816 passed | collect_maintenance_alerts 19/4/3; _battery_alert 8/2/1; _stale_alert 10/2/1; _humidity_alert 13/4/3; _light_alert 17/5/2 |
| T4.6 `data_quality.build_report` | `07f355c` | `$RENDER D(data_quality)` → 472 passed | build_report 14/1/0; _sensor_issues 33/4/2; _plant_issues 26/4/2; _cluster_issues 27/4/2; _duplicate_device_issues 20/3/2; _count_by_code 4/2/1 |
| T4.7 `_aggregate_band` | `801dadc` | `$RENDER D(charts)` → 553 passed | _threshold_for_cluster 21/6/2; _aggregate_band 7/2/1 |
| T4.8 `repo.get_plant` | `e1ab3b2` | `$RENDER D(charts)` → 553 passed | n/a |
| T4.17 `build_overlay_payload` | `45280df` | `$RENDER D(charts) test_charts_overlay G` → 580 passed | build_overlay_payload 18/2/1; _bucket_readings 15/6/3; _overlay_datasets 18/5/1; _to_points 1/1/0 |
| T4.9 `cluster_insights` | `088632c` | `$RENDER D(insights) G` → 450 passed | cluster_insights 40/7/2; _insight_from_alert 6/1/0 |
| **T4.10** `watch` (G+) | `6a06507` | `$SCHED $CHECK D(pump_watcher) G` → 549 passed; `C(gs.services.pump_watcher)` + `G` → 848 passed | watch 40/7/3; _outcome 7/1/0; _poll_step 5/2/1; _log_abandoned 6/1/0 |
| T4.11 `backfill_from_history` (hardware-adjacent) | `440f4a6` | `$RENDER $CHECK D(health_monitor) test_contract_health_monitor G` → 781 passed; `C(gs.services.health_monitor)` + `G` → 888 passed | backfill_from_history 14/6/2; _raise_if_not_open 10/2/1 |
| **T4.12** `_handle_trip` (G+) | `931ad9b` | `$SCHED $CHECK D(pump_watcher) G` → 549 passed; `C(gs.services.pump_watcher)` + `G` → 848 passed | _handle_trip 31/1/0; _trip_payload 13/1/0; _stop_pump 9/2/1; _log_aborted_event 10/2/1; _log_trip_activity 16/2/1; _record_trip_state 12/3/2; _commit_trip 8/3/2 |
| T4.13 `search` | `84dd177` | `$RENDER tests/server/test_search.py tests/cli/test_tui.py` → 408 passed | search 10/2/1; four `_*_hits` 22/1/0 each |
| T4.14 `anomaly.scan` | `afa3a1d` | `$RENDER D(anomaly) test_contract_scheduler G` → 373 passed | scan 25/5/2; _stale_alert 33/2/1; _drift_alert 39/3/1; _latest_soil_zscore 15/3/1 |
| T4.15 `efficacy.score_cluster` | `a9fe7c7` | `$RENDER D(efficacy) G` → 442 passed | score_cluster 26/4/3; _event_item 28/3/1 |
| T4.16 `SystemHealthService.pulse` | `7e9805b` | `$RENDER D(system_health)` → 482 passed | pulse 31/1/0; _sensor_devices 17/4/2; _overall_status 5/3/1 |

**Note on T4.13 and T4.16.** They were committed before `acc4c05`, while the WP was still partial. Their coverage was
already clean.

**Order.** Every other task ran in table order once the blocks were lifted.

**Reviewers.** T4.10 and T4.12 are G+ and need **two reviewers**, one of them an adversary. Target §12 checklist:
- clock and stop-check positions;
- the `elapsed` computed by the caller;
- each trip step being its own failure domain;
- the payload key order.

## Coverage precondition

1. One union run with per-test coverage contexts (`--cov-context=test`).
2. Each task evaluated against its **own subset** (scripts `scratchpad/wp4/covtask.py` and `covtask2.py`). After the
   test commit, every restructured function is fully covered by its subset plus `G`, with the two accepted
   unreachable arms as the only exceptions.

## Characterization tests — `acc4c05` (test-only, no production change)

`G` contains 27 tests:
- They pin every gap of T4.3, T4.4, T4.5, T4.9, T4.11, T4.14, T4.15 and T4.17.
  - Efficacy L56 (an event whose duration is 0 or None) is now pinned directly.
  - The charts soil-None and humidity-None bucket arms are pinned too.
- They kill the pump-watcher M-pre survivors.
- They are hermetic: frozen clock, `tmp_db`, and in-module fakes for plant care, learner and weather, so there is no
  network.

**`…_current_behavior`:** `test_watch_negative_duration_current_behavior_reports_negative_elapsed`. A negative duration
completes at once with `elapsed_seconds == -5.0`.

**Determinism:**
- 27 passed in two serial runs, once at `-n 2`, and once with `TZ=America/New_York` and `PYTHONHASHSEED=12345`.
- `tests/golden` is unchanged.

**Can-fail proofs** (script `scratchpad/wp4/proofs.py`):
- 18 temporary in-place mutations, run under the test lock: health ×4, forecast ×5, maintenance ×2, insights,
  health_monitor, anomaly, efficacy ×2, charts ×2.
- **All 18 were caught.**
- Each file was restored with `git checkout -- <file>`, and `libs` was clean before and after.

## Pump watcher mutation

| Run | Selection | Tests | Mutants | Killed | Survived |
|---|---|---|---|---|---|
| M-pre #1 (unmodified file) | lines 77-83, 117-180, 213-297 | pump group | 74 | 62 | 12 |
| M-post #1 (after T4.0/T4.1) | functions `__init__`, `watch`, `_handle_trip` | pump group | 90 | 74 | 16 (0 regressions) |
| **M-pre #2 (after `acc4c05`, before T4.10)** | the same 3 functions | pump group + `G` | 90 | **88** | **2 (both equivalent)** |
| **M-post #2 (after T4.12)** | the 12 new functions | pump group + `G` | 106 | **105** | **1 (equivalent)** |

The pump group is `test_contract_pump_watcher`, `test_pump_watcher`, `test_contract_pipeline` and `test_irrigators`.
The results are in `refactor/wp-handoff/mutation/pump_watcher-{pre,post,pre2,post2}.jsonl`.

**Equivalence proofs for the two survivors left in M-pre #2:**
- `watch`: deleting `last_failure_msg: str | None = None` is **equivalent**. The variable's only read is the abandon
  warning, and that warning sits in the same branch, right after `last_failure_msg = read_error or "device offline"`.
  So the variable is always assigned before it is read.
- `_handle_trip`: deleting `stop_msg = ""` is **equivalent**. Every path through the following try/except assigns
  `stop_msg` before its first use:
  - success: `stop_ok, stop_msg = adapter.stop(...)`;
  - any exception, including a failed unpack: `stop_msg = f"adapter.stop raised: {exc}"`.

**M-post #2 verdict** (`--compare pump_watcher-pre2.jsonl --map <function map below>`):
- **kill rate 99.1 %: OK**;
- **killed-in-pre identities now surviving: 0**;
- summary exit code 0.

The one survivor is the same equivalent mutant, moved: `stop_msg = ""` deleted, now in `_stop_pump`.

22 pre identities are UNMATCHED: the code was rewritten, not removed. Their snippets mention:
- `consecutive_failures`, which became `failures`;
- the old result dict literals, which became `_outcome(...)`;
- `last_failure_msg`, which was removed;
- the payload literal, which became `_trip_payload`;
- `irrigator.id` in the commit log, which became `irrigator_id`.

For each one the post run contains the rewritten counterpart, and that counterpart is killed: the only post survivor
is the equivalent `stop_msg = ""`.

## WP gate

- Union of all task subsets plus `$CORE` (`scratchpad/wp4/gate-union2.txt`): **1175 passed** (6m17s).
- `FULL` (`-n 2`, `PYTHONHASHSEED=0`): **2824 passed** (9m25s). That is 2797 existing tests plus the 27 in `G`.
- `FULL_SEED2` (`PYTHONHASHSEED=12345`): **2824 passed** (9m24s).
- Static checks: `make typecheck` OK, `lint-imports` OK, `ruff check libs/ tests/` and `ruff format --check` clean.
- **Per-WP DoD.** All 12 WP4 files are clean under:
  - `sizecheck` (only `health_monitor.py` > 400 lines, already in the register);
  - ruff C90@8 + PLR0911/12/15 `--isolated`, except `DeviceHealthMonitor._alarm_message` (approved exception below).

## Sizecheck

| | WP4 entries listed |
|---|---|
| Before | 12 functions |
| After | **0** |

The register file entry for `health_monitor.py` remains.

## Strict list additions

`services/{charts,forecast,health,health_monitor,maintenance,pump_watcher}.py` (T4.0). All 12 WP4 modules are now
strict.

## Ratchet edits for the integrator (I1)

These `pyproject.toml` per-file ignores can be **dropped**, because the files are now clean:

| File | Ignore to drop |
|---|---|
| `services/charts.py` | `["C901"]` |
| `services/data_quality.py` | `["C901", "PLR0912"]` |
| `services/forecast.py` | `["C901", "PLR0915"]` |
| `services/health.py` | `["C901"]` |
| `services/maintenance.py` | `["C901"]` |

Keep `services/health_monitor.py` `["PLR0911"]`, which matches the size exception below.

## Size exception (orchestrator-approved — integrator commits it)

`libs/greenhouse-server/greenhouse_server/services/health_monitor.py::DeviceHealthMonitor._alarm_message — flat
alarm-code → message mapping (8 returns); a lookup table would add indirection without removing a present smell —
approved: orchestrator (wave A)`

## Dead-code candidates for REFACTOR_NOTES (accepted without tests; guards moved verbatim)

1. `anomaly`: the `len(baseline) < _MIN_READINGS - 1` guard, now inside `_latest_soil_zscore`, is unreachable.
   ≥ 10 soil values ⇒ the baseline has ≥ 9 = `_MIN_READINGS - 1`.
2. `pump_watcher._record_trip_state`: the false arm of `if monitor is not None:` is unreachable. `_lazy_monitor`
   always returns an instance.
3. `charts`: the overlay soil-None arm. It was listed as accepted, but it turned out to be reachable and is now pinned
   by `G`.

## Deviations (all behavior-neutral; look at these)

1. **T4.0 forecast**: `if has_profile` became `if profile is not None`, the identical condition, for mypy narrowing.
2. **T4.3**: `_pooled_clean_readings -> list[CleanedReading]`. That is the real type; the target says `SensorReading`.
3. **T4.5**:
   - The plant lookup is hoisted to once per sensor. It is a pure dict read.
   - `_light_alert` uses guard clauses that are exact negations, and the evaluation order is kept.
4. **T4.10**:
   - The local `consecutive_failures` is renamed `failures`, so that `watch` fits in 40 body lines.
   - `last_failure_msg` is gone, because it was only read right after its assignment.
   - `(deadline - duration_seconds)` is still evaluated at each exit, not hoisted. Hoisting would raise earlier for a
     non-numeric duration that `float()` accepts.
5. **T4.11**: `_raise_if_not_open(alarm, sensor, *, cluster_id)` passes the `cluster_id` the loop read once.
6. **T4.12**:
   - `_trip_payload` is extracted as module-level with keyword-only inputs, as the orchestrator asked.
   - The lazy `SOURCE_PUMP` import moves into `_log_trip_activity`. `services.alerts` is already loaded through
     health_monitor's top-level import, so the move has no side effect.
   - `_commit_trip` receives `irrigator.id` read before the commit.
7. **T4.14**: it adds the pure `_latest_soil_zscore` so that `_drift_alert` stays ≤ 40 body lines.
8. **T4.15**: `_event_item -> EfficacyItemResponse | None` instead of `_event_items -> list`.
9. **T4.17**: one blank line was removed after the test run to keep `charts.py` at 400 lines. The file's `ast.dump`
   is identical before and after.
10. **Mutation runs** held the test lock per chunk, for up to about 31 minutes, rather than per mutant: per-mutant
    locking starved behind WP3's runs. Each run inside a chunk was still under the lock.

## Function map (old qualname → new qualnames)

```json
{"PlantHealthService.compute_score": ["PlantHealthService.compute_score", "_empty_score", "_HealthBands.from_care", "PlantHealthService._pooled_clean_readings", "_in_band_pct", "_band_percentages", "PlantHealthService._first_profile_efficiency", "_composite_score"],
 "ForecastService.predict_next_irrigation": ["ForecastService.predict_next_irrigation", "ForecastService._forecast_sensor", "_no_data_forecast", "_confidence_for", "ForecastService._rain_outlook"],
 "collect_maintenance_alerts": ["collect_maintenance_alerts", "_battery_alert", "_stale_alert", "_humidity_alert", "_light_alert"],
 "build_report": ["build_report", "_sensor_issues", "_plant_issues", "_cluster_issues", "_duplicate_device_issues", "_count_by_code"],
 "_threshold_for_cluster": ["_threshold_for_cluster", "_aggregate_band"],
 "build_overlay_payload": ["build_overlay_payload", "_bucket_readings", "_overlay_datasets", "_overlay_datasets._to_points"],
 "InsightsService.cluster_insights": ["InsightsService.cluster_insights", "_insight_from_alert"],
 "PumpWatcherService.watch": ["PumpWatcherService.watch", "PumpWatcherService._poll_step", "PumpWatcherService._log_abandoned", "_outcome"],
 "DeviceHealthMonitor.backfill_from_history": ["DeviceHealthMonitor.backfill_from_history", "DeviceHealthMonitor._raise_if_not_open"],
 "PumpWatcherService._handle_trip": ["PumpWatcherService._handle_trip", "PumpWatcherService._stop_pump", "_trip_payload", "PumpWatcherService._log_aborted_event", "PumpWatcherService._log_trip_activity", "PumpWatcherService._record_trip_state", "PumpWatcherService._commit_trip"],
 "search": ["search", "_cluster_hits", "_plant_hits", "_sensor_hits", "_irrigator_hits"],
 "SensorAnomalyService.scan": ["SensorAnomalyService.scan", "SensorAnomalyService._stale_alert", "SensorAnomalyService._drift_alert", "_latest_soil_zscore"],
 "score_cluster": ["score_cluster", "_event_item"],
 "SystemHealthService.pulse": ["SystemHealthService.pulse", "SystemHealthService._sensor_devices", "_overall_status"]}
```

## Bugs touched (preserved)

- The `water_warning` meaning mismatch: health_monitor's `water_warning is True` → SENSOR_FAULT is moved verbatim
  (T4.11).
- B-3 (the offline flap) and the pump_watcher shutdown semantics are untouched.

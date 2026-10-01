# Gate 1 — mutation campaign (does the Phase-1 safety net fail when behavior changes?)

Worktree branch `worktree-agent-a052ed41bc5879aa9`, based on `claude/focused-hawking-7to7o3` @ `6994bc0`
(the seven safety-net commits + the Gate 1 evidence). Production code was mutated **only** in this isolated
worktree and in plain copies of it; every mutant was restored byte-for-byte (`git diff HEAD~2 -- libs/` is empty).

## Method

**mutmut was tried first and rejected (≈10 min).** `uv run --with mutmut mutmut run` (mutmut 3, ephemeral, a
throw-away `setup.cfg`) mutates by file path and keys its trampolines `libs.greenhouse-core.greenhouse_core.…`,
while the tests import `greenhouse_core.…` through pytest's `pythonpath` — it aborts with *"tests recorded
trampoline hits but none match any mutant key"*. Making it work would mean changing the package layout or the
pytest config, both frozen. Nothing was left behind (`setup.cfg`/`mutants/` deleted).

**Curated campaign — `refactor/gate1/mutate.py` (reusable).** A catalogue of 473 hand-written mutants, each an
exact original snippet + replacement in one production file, chosen by reading the code and the "production
lines/branches this net guards" sections of `refactor/10-safety-*.md`. Operators: comparison flips (`<`↔`<=`,
`>`↔`>=`), boundary/threshold ±, `and`↔`or`, negated conditions, removed early returns / guards, swapped pipeline
steps, changed constants, removed or duplicated side effects (commit, event row, `set_decision_actuated`, leak
check, watcher, notify, adapter stop), user/log/JSON strings, DESC↔ASC, `min`↔`max`/`mean`, dropped dict keys,
changed defaults, protocol version and DP ids in the device profile JSON.

The runner applies **one** mutant, checks that the snippet is unique and the result compiles, runs the test group
that guards the code with `pytest -x -q -p xdist -n N -p no:cacheprovider -o addopts=` (`PYTHONDONTWRITEBYTECODE=1`
so no stale `.pyc` can mask a mutant), records the outcome and writes the original bytes back in a `finally`.
Exit 1 — or exit 2 with "N failed" (that is how xdist reports `-x`) — is **KILLED**; exit 0 is **SURVIVED**.
No mutant timed out, failed to apply, or was killed only by a collection error.

Each test group = the area's `test_contract_*` goldens + the existing tests that
`refactor/baseline/module-tests-map-direct.json` ties to the module (trimmed to the files that exercise it, see
`GROUPS` in `mutate.py`). Every group was run green on unmutated code first (`--baseline`; 16 groups, all exit 0).

```bash
uv run python refactor/gate1/mutate.py --check                      # every snippet applies + compiles
uv run python refactor/gate1/mutate.py --baseline                   # every group green on HEAD
uv run python refactor/gate1/mutate.py --area engine --resume       # one area (results: JSON lines)
MUTATE_GAPS_ONLY=1 uv run python refactor/gate1/mutate.py --with-gaps --only engine-13   # re-run vs gap tests
uv run python refactor/gate1/mutate.py --report --after <gap-rerun.jsonl>
```

Wall time was dominated by survivors (a survivor runs its whole group, 15 s – 4 min). To fit the box (4 cores) the
catalogue ran as up to five shards in parallel (2 xdist workers each): the worktree itself plus plain copies of the tree
(`MUTATE_NO_GIT=1`; a copy imports its own `libs/` because pytest's `pythonpath` is rootdir-relative — verified).

**Equivalent mutants** carry a written proof in the catalogue and are excluded from the denominator while they
survive (if the suite had killed one, the claim would have been wrong and it would count as killed — none was).

**Gap tests.** For every non-equivalent survivor a characterization test was written in
`tests/test_contract_mutation_gaps.py` (core), `tests/server/test_contract_mutation_gaps.py` and
`tests/cli/test_contract_mutation_gaps.py`, following `refactor/10-phase1-brief.md`: hermetic (in-memory SQLite,
`frozen_clock`, `clean_env`, offline weather, fake adapters defined in the module), pinning CURRENT behavior
(including two pinned current-behavior quirks, named `…_current_behavior`), no production edits. Each survivor was
then re-run against the gap files (`MUTATE_GAPS_ONLY=1 --with-gaps`) and is now **KILLED**.

## Results

### Per area (the brief's grouping)

| area | mutants | killed | survived | equivalent | score | score after gap tests |
|---|---|---|---|---|---|---|
| Decision-critical: `logic/*` (+ `constants.py`) | 137 | 117 | 16 | 4 | 88.0% | 100% |
| Decision-critical: `learning/*` | 30 | 17 | 13 | 0 | 56.7% | 100% |
| Devices (`devices/**`, `core/sync.py`) | 61 | 59 | 0 | 2 | 100.0% | 100% |
| Server orchestration (scheduler + services) | 119 | 89 | 28 | 2 | 76.1% | 100% |
| `repository.py` | 21 | 17 | 4 | 0 | 81.0% | 100% |
| Auth (core + server + MCP gate) | 23 | 22 | 1 | 0 | 95.7% | 100% |
| Web/API glue (`routes/*`, `web/*`) | 24 | 10 | 14 | 0 | 41.7% | 100% |
| CLI (`client.py`, `commands/*`) | 25 | 25 | 0 | 0 | 100.0% | 100% |
| TUI | 33 | 28 | 5 | 0 | 84.8% | 100% |
| **overall** | 473 | 384 | 81 | 8 | **82.6%** | **100%** |

- **Decision-critical** (`logic/*` + `learning/*`): 134 / 163 = **82.2 %** before the gap tests (target ≥ 75 %:
  met). `logic/*` alone 88.0 %. **`learning/*` alone was 56.7 %** — below target: the learning path is advisory, its
  only effect on a decision is the `learning_alerts` list inside the payload (pinned as a sha256 in the grid), and the
  learning profiles / report / conflict checks had almost no direct characterization. 13 gap tests now pin them.
- **Overall: 384 / 465 = 82.6 %** before the gap tests (target ≥ 75 %: met); **100 %** after (every non-equivalent
  survivor re-run against the gap files is KILLED).
- Weakest before gaps: **Web/API glue 41.7 %** (pure Jinja filters and rarely-taken API branches — the HTML goldens
  render one seeded state, which never sits on a filter's edge) and **`services/leak.py` 37.5 %** (the existing leak
  tests prove the happy shapes, not the rule thresholds). Strongest: devices and CLI (100 %), auth (95.7 %), engine
  (92.2 %).

### Per module (catalogue area)

| area | mutants | killed | survived | equivalent | invalid | score | survived after gap tests | score after |
|---|---|---|---|---|---|---|---|---|
| auth | 23 | 22 | 1 | 0 | 0 | 96% | 0 | 100% |
| cleaning | 11 | 9 | 2 | 0 | 0 | 82% | 0 | 100% |
| cli | 25 | 25 | 0 | 0 | 0 | 100% | 0 | 100% |
| constants | 3 | 3 | 0 | 0 | 0 | 100% | 0 | 100% |
| decision | 10 | 7 | 1 | 2 | 0 | 88% | 0 | 100% |
| devices | 61 | 59 | 0 | 2 | 0 | 100% | 0 | 100% |
| engine | 64 | 59 | 5 | 0 | 0 | 92% | 0 | 100% |
| fallback | 10 | 10 | 0 | 0 | 0 | 100% | 0 | 100% |
| health | 14 | 13 | 1 | 0 | 0 | 93% | 0 | 100% |
| irrigation | 34 | 28 | 5 | 1 | 0 | 85% | 0 | 100% |
| leak | 16 | 6 | 10 | 0 | 0 | 38% | 0 | 100% |
| learning | 30 | 17 | 13 | 0 | 0 | 57% | 0 | 100% |
| manual | 11 | 7 | 4 | 0 | 0 | 64% | 0 | 100% |
| pump | 12 | 10 | 2 | 0 | 0 | 83% | 0 | 100% |
| repository | 21 | 17 | 4 | 0 | 0 | 81% | 0 | 100% |
| scheduler | 24 | 19 | 5 | 0 | 0 | 79% | 0 | 100% |
| sensors | 5 | 4 | 1 | 0 | 0 | 80% | 0 | 100% |
| stress | 10 | 8 | 2 | 0 | 0 | 80% | 0 | 100% |
| svcsync | 8 | 6 | 1 | 1 | 0 | 86% | 0 | 100% |
| timing | 13 | 10 | 1 | 2 | 0 | 91% | 0 | 100% |
| trends | 11 | 7 | 4 | 0 | 0 | 64% | 0 | 100% |
| tui | 33 | 28 | 5 | 0 | 0 | 85% | 0 | 100% |
| web | 24 | 10 | 14 | 0 | 0 | 42% | 0 | 100% |
| **total** | 473 | 384 | 81 | | | **82.6%** | 0 | **100.0%** |

"Score after" = share of non-equivalent mutants killed once the three gap-test files are added (each survivor
re-run with `MUTATE_GAPS_ONLY=1 … --with-gaps`).

## Survivors

### Equivalent mutants (8) — excluded from the denominator, listed with the proof

| id | location (at HEAD) | mutation | why it cannot change behavior |
|---|---|---|---|
| decision-08 | `greenhouse_core/logic/decision.py:116` | Reason default severity | Reason is only ever built by IrrigationDecision.add_reason, which always passes severity explicitly; Reason is not part of any OpenAPI schema |
| decision-10 | `greenhouse_core/logic/decision.py:173` | StressIndicators.any_critical negated | any_critical() has no caller in libs/ (grep) — dead API |
| devices-41 | `greenhouse_core/devices/irrigators/tuya_generic.py:69` | local status without dps accepted | a non-empty status without 'dps' then raises KeyError on live['dps'] inside the same try, whose `except Exception: pass` falls through to the identical Cloud fallback |
| devices-46 | `greenhouse_core/devices/sensors/tr301z.py:88` | water_warning truthiness | water_warning is a nullable Boolean column; True is the only truthy value it can hold |
| irrigation-09 | `greenhouse_server/services/irrigation.py:512` | health block keeps decision action | after the health block the decision object is discarded: result['action'] is set to 'skip' independently and the decision is not re-persisted (that is bug B-6), so decision.action is never read |
| svcsync-05 | `greenhouse_server/services/sync.py:74` | no flush after freshness sync | every write sync_single_sensor makes goes through repo.add_sensor_reading, which already calls session.flush() after each insert (and the session autoflushes before the snapshot SELECT) |
| timing-12 | `greenhouse_core/logic/timing.py:116` | quiet hours start==end guard removed | _hour_in_range() itself returns False when start == end, so the guard is redundant |
| timing-13 | `greenhouse_core/logic/timing.py:159` | missing-season default 1.0 -> 1.1 | both built-in tables define all four seasons and Season is a closed Literal, so the default is unreachable |

### Real gaps (81) — each now killed by a new characterization test

`core::` = `tests/test_contract_mutation_gaps.py`, `server::` = `tests/server/test_contract_mutation_gaps.py`,
`cli::` = `tests/cli/test_contract_mutation_gaps.py`. "Why it survived" is inferred from what the guarding tests
seed (they never put the code on that edge); every row's mutant was re-run against the gap files and is **KILLED**.

| id | location (at HEAD) | mutation | why it survived | killed by |
|---|---|---|---|---|
| auth-12 | `greenhouse_server/auth.py:185` | MCP token prefix match | MCP-token tests only use the exact token and clearly wrong ones, never a prefix/extension | `server::test_mcp_token_on_api_must_match_exactly` |
| cleaning-04 | `greenhouse_core/logic/cleaning.py:89` | spike test > -> >= | boundary value never exercised (grid/tests step over the exact threshold); no series deviates by exactly n·σ | `core::test_hampel_spike_test_is_strictly_greater_than_threshold` |
| cleaning-09 | `greenhouse_core/logic/cleaning.py:81` | window left edge off-by-one | no series where the left half of the Hampel window decides the median | `core::test_hampel_window_is_centered_with_full_left_context_current_behavior` |
| decision-04 | `greenhouse_core/logic/decision.py:156` | has_data ignores light | no case with lux-only sensor data (every grid series carries soil or temperature) | `core::test_light_only_sensor_data_counts_as_data` |
| engine-13 | `greenhouse_core/logic/engine.py:237` | pipeline order: critical stress before water warning | no case where water_warning and critical stress fire together (rule order unobservable) | `core::test_water_warning_outranks_critical_stress_when_both_fire` |
| engine-28 | `greenhouse_core/logic/engine.py:434` | vacation budget boundary >= -> > | vacation cases trim or exhaust; none has headroom exactly equal to the dose | `core::test_vacation_budget_equal_to_duration_is_not_trimmed` |
| engine-49 | `greenhouse_core/logic/engine.py:737` | adequate/wet boundary <= -> < | boundary value never exercised (grid/tests step over the exact threshold) (avg soil == target_max) | `core::test_soil_exactly_at_target_max_is_adequate` |
| engine-51 | `greenhouse_core/logic/engine.py:759` | temp-high boundary > -> >= | boundary value never exercised (grid/tests step over the exact threshold) (avg temp == ideal max + 3 °C) | `core::test_temp_high_adjustment_is_strictly_above_ideal_plus_offset` |
| engine-57 | `greenhouse_core/logic/engine.py:915` | trend hot boundary > -> >= | boundary value never exercised (grid/tests step over the exact threshold) (rising trend at exactly 25 °C) | `core::test_rising_temperature_trend_needs_strictly_above_25c` |
| health-09 | `greenhouse_server/services/health_monitor.py:264` | sensor fault backfill all -> any | back-fill table has all-or-none water_warning windows, never a mixed window | `server::test_backfill_sensor_fault_needs_every_reading_in_the_window` |
| irrigation-15 | `greenhouse_server/services/irrigation.py:643` | monitor very_dry margin 15 -> 10 | monitor tests use values far from the very_dry band edge | `server::test_monitor_status_bands_around_the_plant_target` |
| irrigation-16 | `greenhouse_server/services/irrigation.py:647` | monitor wet margin removed | monitor tests use values far from the wet band edge | `server::test_monitor_status_bands_around_the_plant_target` |
| irrigation-27 | `greenhouse_server/services/irrigation.py:166` | watcher duration minutes as seconds | pump-watcher scheduling tests stub the watcher; the watch() duration argument was never asserted | `server::test_scheduled_pump_watcher_watches_for_the_duration_in_seconds` |
| irrigation-33 | `greenhouse_server/services/irrigation.py:628` | monitor reads raw readings | monitor never sees an out-of-range latest sample (cleaned vs raw indistinguishable) | `server::test_monitor_reads_the_cleaned_view` |
| irrigation-34 | `greenhouse_server/services/irrigation.py:441` | non-outdoor envs treated as indoor for temperature | no pipeline case with an environment other than indoor/outdoor | `server::test_temperature_source_only_indoor_prefers_the_sensor` |
| leak-01 | `greenhouse_server/services/leak.py:160` | min after samples 3 -> 2 | boundary value never exercised (grid/tests step over the exact threshold) (exactly 2 after-samples) | `server::test_leak_verdict_table`, `server::test_leak_verdict_table` |
| leak-02 | `greenhouse_server/services/leak.py:175` | pinned threshold > -> >= | boundary value never exercised (grid/tests step over the exact threshold) (pinned at exactly 95 %) | `server::test_leak_verdict_table` |
| leak-03 | `greenhouse_server/services/leak.py:175` | pinned all -> any | no window with one pinned sample in the tail | `server::test_leak_verdict_table` |
| leak-04 | `greenhouse_server/services/leak.py:182` | min before samples 2 -> 1 | boundary value never exercised (grid/tests step over the exact threshold) (exactly 1 baseline sample) | `server::test_leak_verdict_table` |
| leak-06 | `greenhouse_server/services/leak.py:189` | settle tolerance dropped from still_climbing | no after-window whose last sample is within the 2 % settle tolerance of a higher peak | `server::test_leak_verdict_table` |
| leak-07 | `greenhouse_server/services/leak.py:190` | rose_through_window threshold | no window with a rise > 0 and ≤ 2 % through the window | `server::test_leak_verdict_table` |
| leak-08 | `greenhouse_server/services/leak.py:191` | rising delta >= -> > | boundary value never exercised (grid/tests step over the exact threshold) (exactly +30 % over baseline) | `server::test_leak_verdict_table` |
| leak-09 | `greenhouse_server/services/leak.py:193` | rose_through_window not required | same as leak-07: the rise-through-window condition is never the deciding one | `server::test_leak_verdict_table` |
| leak-12 | `greenhouse_server/services/leak.py:231` | resolved alerts re-resolved | no check runs over an already-resolved leak alert of the same sensor | `server::test_settled_sensor_releases_only_its_own_open_leak_alert` |
| leak-15 | `greenhouse_server/services/leak.py:113` | leak_hold activity written even with no findings | every no-finding check in the suite asserts alerts, not the absence of a leak_hold activity | `server::test_leak_hold_activity_and_message`, `server::test_leak_check_without_findings_writes_no_activity` |
| learning-02 | `greenhouse_core/learning/issues.py:61` | blocked drip and -> or | blocked-drip profiles in the suite fail both conditions or neither | `core::test_blocked_drip_needs_low_efficiency_and_low_absorption` |
| learning-09 | `greenhouse_core/learning/issues.py:255` | low-light min samples 5 -> 3 | low-light conflict check only seeded with ≥ 5 or 0 samples | `core::test_conflict_light_and_humidity_checks_need_five_samples` |
| learning-12 | `greenhouse_core/learning/issues.py:80` | rapid drainage boundary < -> <= | boundary value never exercised (grid/tests step over the exact threshold) (drainage exactly −5 %/h) | `core::test_rapid_drainage_needs_drainage_strictly_below_threshold` |
| learning-14 | `greenhouse_core/learning/issues.py:286` | low-humidity min samples 5 -> 3 | low-humidity conflict check only seeded with ≥ 5 or 0 samples | `core::test_conflict_light_and_humidity_checks_need_five_samples` |
| learning-15 | `greenhouse_core/learning/issues.py:206` | non-positive absorption guard removed | no dry sensor with zero learned absorption (would divide by zero) | `core::test_conflict_projection_skips_a_dry_plant_with_zero_absorption` |
| learning-16 | `greenhouse_core/learning/profiling.py:38` | post delay boundary >= -> > | boundary value never exercised (grid/tests step over the exact threshold) (post sample exactly 600 s after start) | `core::test_post_irrigation_reading_counts_from_exactly_ten_minutes` |
| learning-17 | `greenhouse_core/learning/profiling.py:45` | pre reading = oldest | profiling cases have one pre-irrigation sample, so first == last | `core::test_post_irrigation_reading_counts_from_exactly_ten_minutes` |
| learning-19 | `greenhouse_core/learning/profiling.py:50` | default duration 2 -> 1 | every seeded start event carries a duration | `core::test_plant_profile_end_to_end` |
| learning-20 | `greenhouse_core/learning/profiling.py:105` | profile counts non-start events | seeded histories contain only start events | `core::test_plant_profile_end_to_end` |
| learning-24 | `greenhouse_core/learning/profiling.py:26` | pre window 30 -> 60 min | no pre-sample between 30 and 60 min before a start | `core::test_plant_profile_end_to_end`, `core::test_pre_irrigation_window_is_thirty_minutes` |
| learning-26 | `greenhouse_core/learning/report.py:37` | report low-efficiency 0.5 -> 0.6 | report text (efficiency < 0.5 warning) not asserted at the edge | `core::test_learning_report_text` |
| learning-27 | `greenhouse_core/learning/report.py:46` | report severity not upper-cased | report text: alert severity case never asserted | `core::test_learning_report_text` |
| learning-29 | `greenhouse_core/learning/learner.py:23` | learner profile default days 30 -> 7 | learner facade's default window never compared with the profiling default | `core::test_plant_profile_end_to_end` |
| manual-03 | `greenhouse_server/services/manual_control.py:71` | daily cap counts non-start events | cap tests only seed start events | `server::test_daily_cap_counts_only_start_minutes` |
| manual-04 | `greenhouse_server/services/manual_control.py:114` | caps checked before registry (409 instead of 503) | no-registry tests have no caps configured, so the check order is unobservable | `server::test_manual_start_checks_registry_before_caps` |
| manual-06 | `greenhouse_server/services/manual_control.py:133` | manual watcher marked auto (stopped on shutdown) | watcher scheduling is skipped in tests (scheduler not running); the triggered_by kwarg was never asserted | `server::test_manual_start_schedules_a_manual_watcher` |
| manual-11 | `greenhouse_server/services/manual_control.py:56` | None minutes counts as 1 | cap tests always pass explicit minutes | `server::test_daily_cap_counts_only_start_minutes` |
| pump-04 | `greenhouse_server/services/pump_watcher.py:140` | offline reads not counted as failures | watcher tests signal failures with an error string, never offline-without-error | `server::test_pump_watcher_counts_offline_reads_as_failures` |
| pump-09 | `greenhouse_server/services/pump_watcher.py:159` | failure counter not reset on good read | failure sequences are all-failing or all-healthy, never interleaved | `server::test_pump_watcher_failure_budget_counts_consecutive_failures_only` |
| repository-11 | `greenhouse_core/repository.py:848` | resolved alerts can be acknowledged | acknowledge is only exercised on open alerts | `core::test_acknowledge_leaves_a_resolved_alert_resolved` |
| repository-17 | `greenhouse_core/repository.py:358` | get_latest_reading returns oldest | freshness/health tests insert a single reading per sensor | `core::test_get_latest_reading_is_the_newest_row` |
| repository-18 | `greenhouse_core/repository.py:429` | readings_around before excludes the event instant | no reading sits exactly at the event timestamp | `core::test_readings_around_puts_the_event_instant_in_both_windows` |
| repository-21 | `greenhouse_core/repository.py:743` | upsert keeps old severity | re-raised alerts in the suite keep the same severity | `core::test_upsert_alert_refreshes_severity` |
| scheduler-10 | `greenhouse_server/scheduler.py:306` | sync job window 6h -> 24h | job-body tests stub SyncService entirely; the hours argument was never asserted | `server::test_sync_job_backfills_six_hours_and_commits` |
| scheduler-19 | `greenhouse_server/scheduler.py:112` | bad tz fallback | scheduler tz tests use valid zone names only | `server::test_scheduler_unknown_timezone_falls_back_to_utc` |
| scheduler-20 | `greenhouse_server/scheduler.py:402` | health job does not bind repo | health job test uses a monitor that ignores bind_repo | `server::test_health_monitor_job_binds_the_job_repo_before_polling` |
| scheduler-22 | `greenhouse_server/scheduler.py:267` | tz preference re-applied when unchanged | preference tz tests always change the zone | `server::test_unchanged_timezone_preference_is_a_no_op` |
| scheduler-23 | `greenhouse_server/scheduler.py:347` | check job does not bind health monitor repo | check job tests do not wire a health monitor | `server::test_check_job_binds_the_health_monitor_then_checks_and_commits` |
| sensors-03 | `greenhouse_core/logic/sensors.py:70` | water warnings not de-duplicated | water-warning cases have one flagged reading per sensor | `core::test_water_warning_names_are_deduplicated_per_sensor` |
| stress-02 | `greenhouse_core/logic/stress.py:36` | low-light factor 0.4 -> 0.5 | light series sit far below or above the 40 % low-light edge | `core::test_low_light_stress_fires_below_40_percent_of_the_seasonal_minimum` |
| stress-10 | `greenhouse_core/logic/stress.py:60` | saturated boundary > -> >= | boundary value never exercised (grid/tests step over the exact threshold) (avg soil exactly 70 %) | `core::test_over_watering_needs_average_strictly_above_saturation` |
| svcsync-04 | `greenhouse_server/services/sync.py:125` | snapshot light max -> min | snapshot tests have one light-reporting sensor | `server::test_cluster_snapshot_soil_is_min_light_is_max` |
| timing-09 | `greenhouse_core/logic/timing.py:36` | bad tz falls back to Rome, not UTC | bad-tz property only checks the season, which UTC and the mutant zone agree on | `core::test_unknown_timezone_falls_back_to_utc` |
| trends-03 | `greenhouse_core/logic/trends.py:34` | declining boundary < -> <= | boundary value never exercised (grid/tests step over the exact threshold) (soil delta exactly −5 %) | `core::test_soil_trend_declining_needs_delta_strictly_below_minus_threshold` |
| trends-04 | `greenhouse_core/logic/trends.py:41` | 0.0 C counted in temperature trend (truthiness -> is not None) | no series with a 0.0 °C reading (a known truthiness quirk) | `core::test_zero_celsius_readings_are_ignored_by_the_temperature_trend_current_behavior` |
| trends-07 | `greenhouse_core/logic/trends.py:66` | frequency-high boundary > -> >= | boundary value never exercised (grid/tests step over the exact threshold) (exactly 3 starts/day) | `core::test_irrigation_frequency_high_needs_more_than_three_starts_per_day` |
| trends-10 | `greenhouse_core/logic/trends.py:47` | falling boundary < -> <= | boundary value never exercised (grid/tests step over the exact threshold) (temperature delta exactly −2 °C) | `core::test_temperature_trend_falling_needs_delta_strictly_below_minus_threshold` |
| tui-02 | `greenhouse_cli/tui/model.py:60` | is_watering end inclusive | boundary value never exercised (grid/tests step over the exact threshold) (reference exactly at start + duration) | `cli::test_is_watering_window_is_end_exclusive` |
| tui-14 | `greenhouse_cli/tui/formatting.py:88` | ints formatted with decimals | rendered values in TUI goldens are floats or use digits=0 | `cli::test_num_formats_ints_without_decimals` |
| tui-17 | `greenhouse_cli/tui/screens/base.py:31` | auto-refresh on every screen | TUI tests only inspect auto-refresh screens | `cli::test_only_auto_refresh_screens_reload_on_a_timer` |
| tui-23 | `greenhouse_cli/tui/screens/modals.py:154` | login submits empty password | login tests always fill both fields | `cli::test_login_dialog_needs_both_fields` |
| tui-32 | `greenhouse_cli/tui/app.py:88` | quiet flag ignored | quiet API calls in TUI tests never fail with a non-401 error | `cli::test_api_error_toast_respects_quiet` |
| web-01 | `greenhouse_server/web/filters.py:32` | age_seconds 60 s boundary | web goldens render ages away from the unit edges | `server::test_age_seconds_unit_boundaries` |
| web-03 | `greenhouse_server/web/filters.py:49` | strip_emoji keeps ' ; ' spacing | no reason text with irregular '; ' spacing reaches the templates | `server::test_template_filters_current_behavior` |
| web-05 | `greenhouse_server/web/filters.py:68` | moisture_badge low boundary | seeded moisture never equals the target minimum | `server::test_template_filters_current_behavior` |
| web-06 | `greenhouse_server/web/filters.py:76` | severity_class case-sensitive | severities are always lower-case in seeded data | `server::test_template_filters_current_behavior` |
| web-07 | `greenhouse_server/web/filters.py:91` | format_minutes whole hours | no whole-hour duration ≥ 60 min is rendered | `server::test_template_filters_current_behavior` |
| web-13 | `greenhouse_server/routes/operations.py:200` | has_alerts ignores maintenance/needs_water | API check tests have no thirsty sensor-only cluster | `server::test_check_all_has_alerts_counts_thirsty_plants` |
| web-15 | `greenhouse_server/routes/vacation.py:89` | vacation update allows start == end | vacation update tests use start > end, never start == end | `server::test_vacation_update_requires_start_strictly_before_end` |
| web-16 | `greenhouse_server/routes/vacation.py:88` | ends_at=0 treated as absent | no update with ends_at = 0 | `server::test_vacation_update_requires_start_strictly_before_end` |
| web-17 | `greenhouse_server/routes/scheduler.py:73` | core job delete 409 -> 403 | API core-job delete path not exercised (web route covered) | `server::test_deleting_a_core_scheduler_job_is_409` |
| web-18 | `greenhouse_server/routes/scheduler.py:86` | resume error verb | scheduler 500 path never triggered | `server::test_scheduler_toggle_unexpected_failure_is_500_naming_the_verb` |
| web-19 | `greenhouse_server/web/routes/operations.py:41` | web force accepts only 'true' | web irrigate tests send force=true or nothing | `server::test_web_irrigate_force_flag_spellings` |
| web-20 | `greenhouse_server/web/routes/operations.py:86` | web check_all never flags alerts | web check results in goldens carry no learning alerts | `server::test_web_check_all_badge_follows_cluster_alerts` |
| web-22 | `greenhouse_server/web/context.py:51` | theme fallback | theme is always a non-empty value in seeds | `server::test_blank_theme_preference_renders_auto` |
| web-23 | `greenhouse_server/web/context.py:22` | is_hx presence-only | requests send HX-Request: true or no header, never another value | `server::test_is_hx_reads_the_header_value` |

Two of the new tests pin current *quirks* rather than intent, named `…_current_behavior` with a docstring:
`core::test_zero_celsius_readings_are_ignored_by_the_temperature_trend_current_behavior` (already noted in
`REFACTOR_NOTES.md`, decision engine: 0 °C dropped by truthiness) and
`core::test_hampel_window_is_centered_with_full_left_context_current_behavior` (a step change `40,40,40,60,60`
loses its first 60 to the spike filter — new observation, not a bug claim; for the orchestrator to note).

## Gap tests — determinism

125 tests in the three new files (core 49, server 65, cli 11 — parametrized cases counted), only new files, no
production edits, no golden files. On the unmodified tree:

| run | result |
|---|---|
| `uv run pytest <3 files>` | 125 passed |
| same, second run | 125 passed |
| `-p xdist -n 2` | 125 passed |
| `TZ=America/New_York` | 125 passed |
| `ruff check` / `ruff format --check` (3 files + `mutate.py`) | clean |

The one Textual test (`cli::test_only_auto_refresh_screens_reload_on_a_timer`) hosts a probe `DataScreen` in a
minimal app; a first version that drove the full app at a 0.2 s refresh was flaky (worker cancellation at exit) and
was replaced before commit.

## What this says about the safety net

- The golden grids are strong at **"which branch fires"** and at **pipeline order** (every engine step swap but one
  was killed; all CLI JSON/exit-code mutants; all device DP / protocol / call-order mutants), but they **step over
  exact thresholds** — 18 of the 81 real gaps were a single `<`↔`<=` / `>`↔`>=` at a boundary value no test seeded,
  and 8 more a shifted threshold or sample-count minimum. The gap tests pin both sides of each such edge.
- Seeded fixtures are "typical": one reading per sensor, one severity spelling, start events only, valid tz names,
  a single flagged sample. Mutants that only differ on mixed / duplicate / malformed inputs survived.
- Side effects whose **arguments** were never asserted (watcher `duration_seconds`, `triggered_by="manual"`, sync
  `hours=6`, health-monitor `bind_repo`) survived because tests stub the callee. These are now asserted.
- Equivalent mutants found two pieces of dead/redundant code worth a look in the refactor (not changed here):
  `StressIndicators.any_critical()` has no caller, and `is_within_quiet_hours`' `start == end` guard duplicates
  `_hour_in_range`. Bug B-6 (the health block is not re-persisted) is why `irrigation-09` is unobservable.

## Reproduce

```bash
uv sync
uv run python refactor/gate1/mutate.py --check                    # 473/473 apply cleanly
uv run python refactor/gate1/mutate.py --baseline                 # all groups green on HEAD
uv run python refactor/gate1/mutate.py --resume --results /tmp/mut.jsonl            # full campaign (~7 CPU-h serial)
uv run python refactor/gate1/mutate.py --report --results /tmp/mut.jsonl
# prove the gap tests kill a survivor:
MUTATE_GAPS_ONLY=1 uv run python refactor/gate1/mutate.py --with-gaps --workers 0 --only engine-13 --results /tmp/gaps.jsonl
```

Campaign cost: 473 mutants, ≈ 6.9 CPU-hours of pytest summed over mutants (survivors dominate), ≈ 3 h wall on 4
cores with up to five shards. After every shard the runner verified `git diff --quiet -- libs/` (worktree) or
byte-restored files (copies); the committed branch has `git diff HEAD~2 -- libs/` empty.

# WP8 hand-off — engine + timing + devices (HIGH RISK)

Worktree `/home/user/gh-wp8`, branch `refactor/wp8-engine`, based on `327ee12` (integration gate after WP7).
Not pushed, not merged, not rebased. Assigned files (orchestrator): `logic/engine.py`, `logic/timing.py`,
`devices/**`, plus `refactor/mypy-strict.txt` (append-only) and this hand-off.
Every task-gate pytest run: `PYTHONHASHSEED=0 … uv run pytest -q -n 2 …`, split into three lock-bounded chunks
(non-server / server half 1 / server half 2, each ≤ ~3.5 min of lock hold); after the orchestrator's speed
change, targeted subsets and mutation ran without the lock and only the one FULL run took it.
Raw logs: scratchpad `wp8/tests.log`; mutation JSONL in `refactor/wp-handoff/wp8-mutation/`.

**Status:** all WP8 rows in scope done (T8.0–T8.11, T8.15, T8.16-devices) plus one extra annotation-only
device commit requested by the orchestrator's typing review. **Out of scope (not assigned):** core `sync.py`
→ T8.16's sync half, T8.17, T8.18 and the sync M-pre/M-post are not done.
One container restart happened mid-WP: the M-pre run was interrupted with one mutant applied in the scratch
mutation worktree (`/home/user/gh-wp8-mut`); it was restored with `git checkout --` (verified clean) and the
campaign resumed from its JSONL. An uncommitted, untested draft in `gh-wp8` was discarded (`git checkout --`) and
every task was re-applied from its script and re-gated.

## Commits (every production commit is G+ → two reviewers, one adversarial)

| Task | Commit | Subset (all chunks, sum) | Result |
|---|---|---|---|
| T8.0 | `cb2a8ac` add type annotations — strict-clean engine (RainForecast Protocol) and timing | `$ENGINE $RENDER $CORE` + core gap file + `C(gc.logic.engine)` | 1561 passed |
| T8.1 | `d1df481` replace literal — named constants and moisture_target_range | same | 1561 passed |
| T8.16 (devices) | `242578a` add type annotations — device profile + generic Tuya sensor adapter | `$DEV $CORE $SCHED` + `C()` of profile/gateway/tuya_generic/tr301z | 1163 passed |
| T8.15 | `b6538e3` add docstrings — correct stale dp_parsers docstrings | `$DEV $CORE C(gc.devices.sensors.tuya_generic)` | 278 passed |
| test | `326594a` characterization — WP8 coverage gaps and M-pre survivors | `tests/test_contract_wp8_gaps.py` | 16 passed ×3 (seed 0, seed 12345, TZ=America/New_York) |
| T8.2 | `aee2123` extract method — `_tz_name` | `$ENGINE $RENDER` + both gap files + `C(gc.logic.engine)` | 1532 passed |
| T8.3 | `573e1a6` extract method — `_record` | same | 1532 passed |
| T8.4 | `c421bfe` extract method — `_finalize` → `_finish(override_window=…)` | same | 1532 passed |
| T8.5 | `eb4805d` extract method — `_pre_gates`, `_quiet_hours_skip` | same | 1532 passed |
| T8.6 | `2de5731` introduce parameter object — `_EngineInputs`, `_gather_inputs`, `_fallback_decision` | same | 1532 passed |
| T8.7 | `8897cc6` extract method — `_evaluate_rules`, `_base_decision`, `_apply_adjustments`; drop unused `_apply_window_rule` params | same | 1532 passed |
| T8.8 | `d4858d1` remove dead parameter — `_decision_with_reason` | same | 1532 passed |
| T8.9 | `73fa77f` extract function — soil-moisture rule helpers | same | 1532 passed |
| T8.10 | `5b66434` extract function — `_vacation_days_left`, `_binding_max_minutes`, `_seasonal_overrides` | same | 1532 passed |
| T8.11 | `3cd1ecb` move function — `active_quiet_window` into `logic/timing` | same + `D(gc.logic.timing) C(gc.logic.timing)` | 1532 passed |
| extra | `dd2a473` add type annotations — device health state + adapter bases | `$DEV $CORE` + `C()` of the 4 modules | 1099 passed |

Every commit: `ruff check` + `ruff format --check` + `--select ERA,PGH,SLF,PLE` clean, mypy on touched files clean,
`uv run lint-imports` 10 kept, `make typecheck` green, `git status --porcelain tests/golden` empty. No red run, no
rerun, no revert. Commit bodies carry the per-task equivalence argument.

## WP gate (on `dd2a473`)

- Union `$ENGINE $RENDER $CORE $DEV $SCHED` + both gap files + `D(gc.logic.timing)` (no lock, per orchestrator):
  **563 + 698 = 1261 passed**.
- `FULL` (seed 0, `-n 2`, under the lock): **2992 passed (8m45s; 2976 at base + 16 new)**.
- `FULL_SEED2` / TZ run: moved to the final gate (sprint mode).
- Whole-file DoD (all 9 touched production files): `sizecheck` lists only the approved register entries
  (`engine.py` file length 1070; `TuyaIrrigatorAdapter.status`); ruff C90/PLR0911/12/15 @ 8 `--isolated` lists only
  `irrigators/tuya_generic.py::status` (already registered, untouched code); mypy strict clean.

## Characterization commit `326594a` (pre-authorized, test-only)

`tests/test_contract_wp8_gaps.py`, 16 tests on the pytest-free `engine_grid` harness (plant-DB overlay via
`engine_factory`):
- **coverage gaps** engine `352->354` / `354->357` (seasonal override loop). With the bundled plant DB both branches
  are unreachable (category timing keys are merged top-level), hence the overlay;
- **all 19 non-equivalent M-pre survivors**: seasonal loop guards/break (6), cooldown `>` tie + `/3600` (3),
  leak-hold `/3600` (2), vacation days-left / day count / day index (6), soil conflict `< target_min` boundaries (2).
- Proof: the 19 survivors re-run in place (mutate.py, `git checkout --` restore) against the file → **19/19
  KILLED** (`wp8-mutation/engine-pre-gaps.jsonl`, `gap-proofs.txt` maps each to its failing test); the one test
  that was never first-to-fail was proven by a temporary in-place edit → FAILED. The 14 equivalents still survive
  against it (`engine-pre-equiv-gaps.jsonl`), as claimed.

## Mutation (refactor/gate1/mutate.py, identity-matched)

**M-pre** on unmodified `327ee12` (scratch worktree `/home/user/gh-wp8-mut`), the 10 engine functions WP8
restructures or edits (decide_for_cluster incl. `_finalize`, `_resolve_quiet_window`, `_apply_window_rule`,
`_apply_seasonal_multiplier`, `_apply_vacation_budget`, `_enforce_leak_hold`, `_enforce_cooldown`,
`_apply_weather_skip_rule`, `_decision_with_reason`, `_apply_soil_moisture_rule`), tests = `$ENGINE` + core gap
file: **273 mutants, 240 killed, 33 survived (87.9 %)**. 19 non-equivalent → killed by `326594a`.
**14 equivalent** (proofs):
- `return None -> pass` at the end of `_resolve_quiet_window` (falling off returns None);
- `return None -> pass` on `_apply_window_rule`'s `if not windows:` (falls through to
  `is_within_irrigation_window([]) is True` → None; the extra `get_preferences` read changes nothing, the same path
  always reaches `_apply_seasonal_multiplier`, which reads/creates prefs anyway);
- `multiplier < 1.0 -> <= 1.0` (`== 1.0` returned earlier);
- `max(0, ceil((ends-now)/86400)) -> max(-1, …)` (`get_active_vacation` only returns windows with `ends_at >= now`);
- `max(0.0, allowed - spent) -> max(-1.0, …)` (binding ≤ 0 either way → exhaust branch, `VACATION_MIN_RUN_MINUTES`=1,
  IRRIGATE durations ≥ 1);
- `max(0.0, hours_left) -> max(-1.0, …)` (`get_active_alert(since=now-hold)` filters `last_seen_at >= since`);
- 4× precipitation default/`or` fallback ±1 (value only used after `> 2.0`, any of 0/±1 ≤ 2.0);
- 4× soil-ladder statement deletions re-setting values the base decision already holds (DRY duration/interval =
  DEFAULT_*, ADEQUATE/WET action = SKIP; the soil rule is the first adjustment).

**M-post** on `3cd1ecb` (engine: all 31 post functions incl. new helpers; timing: `active_quiet_window`), tests =
`$ENGINE` + both gap files (last 50 mutants: the narrow engine set, `-n 1`, no lock, per orchestrator):
**271 mutants, 257 killed, 14 survived (94.8 %)**; **killed-in-pre now surviving: 0** → **PASS**. The 14 survivors are
exactly the 14 pre equivalents at their new homes (`_vacation_days_left`, `_binding_max_minutes`,
`_apply_soil_level`, `active_quiet_window`). 93 pre identities are UNMATCHED (literal → constant renames in T8.1,
`_finalize`/persist scaffolding replaced by `_finish`/`_record`, prefs lines → `_tz_name`); every post mutant at
those sites was run and killed. Files: `engine-pre.jsonl`, `engine-post.jsonl`, `engine-mpost-summary.txt`, `fmap.json`.

**timing / devices:** timing's only code change is the moved `active_quiet_window` (covered above). Device commits
are annotation/docstring-only: annotation- and docstring-stripped AST identical to the parent except the `Any`
imports and `profile.load_profile_json`'s `raw = json.loads(…); return raw` split — the generated mutant set is
unchanged, so no device campaign was run.

## Function map (old → new; `wp8-mutation/fmap.json`)

- `IrrigationLogic.decide_for_cluster` → itself + `_pre_gates`, `_evaluate_rules`, `_gather_inputs`,
  `_fallback_decision`, `_apply_adjustments`, `_record`, module `_base_decision`, `_quiet_hours_skip`
- `decide_for_cluster._finalize` → `IrrigationLogic._finish` + `_record`
- `_resolve_quiet_window` → itself + `logic.timing.active_quiet_window` + `_tz_name`
- `_apply_window_rule` → itself (params `cluster`, `decision` dropped) + `_tz_name`
- `_apply_seasonal_multiplier` → itself + `_seasonal_overrides`, `_scaled_interval`, `_tz_name`
- `_apply_vacation_budget` → itself + `_vacation_days_left`, `_binding_max_minutes`, `_apply_vacation_ration`
- `_apply_soil_moisture_rule` → itself + `_cluster_target_band`, `_soil_extremes`, `_is_conflict`, `_sensor_names`,
  `_apply_conflict`, `_apply_soil_level`

## DoD (sizecheck before → after)

| Function | Before (body / CC) | After |
|---|---|---|
| `decide_for_cluster` | 142 / 18 | 23 / ≤ 8 (helpers ≤ 23) |
| `_apply_soil_moisture_rule` | 73 | 10 (`_apply_soil_level` 39, CC 4; others ≤ 17) |
| `_apply_vacation_budget` | 55 | 32 (`_apply_vacation_ration` 22, `_binding_max_minutes` 8) |
| `_apply_seasonal_multiplier` | 44 | 28 (`_seasonal_overrides` 12, CC 6, nesting 2) |

`make sizecheck` for engine.py now lists only the approved file-length entry (936 → 1070 lines).

## Deviations from the target doc (reviewers: check)

1. **T8.7** `_apply_adjustments` also takes `cluster_id` explicitly (§3.2 lists no `cluster_id`): the vacation rule
   receives the `decide_for_cluster` argument; re-deriving it from `cluster.id`/`decision.cluster_id` would need an
   equivalence argument (D14 spirit).
2. **T8.9** `_soil_extremes(snapshot, avg_soil)` takes the already-narrowed average (§3.3: `(snapshot)`), so strict
   mypy needs neither `cast` nor `assert`.
3. **T8.10** adds `_apply_vacation_ration` (trim/exhaust tail) beyond §3.2's helper list — needed to bring
   `_apply_vacation_budget` under 40 lines. The consumption query now precedes the (exception-free, side-effect-free)
   budget arithmetic; query order/count unchanged.
4. **T8.11** the two `effective[...]` dict reads now follow `get_preferences` (as §3.2 prescribes); pure reads of
   keys `get_effective_config` always builds.
5. **T8.1** drops the now-unused `parse_moisture_target` import from engine (no `engine.parse_moisture_target` user).
6. **Extra** `dd2a473` (orchestrator typing request): 4 more device modules strict-clean (annotation-only).

## Strict list / ratchet (for I1)

`refactor/mypy-strict.txt` gained: `logic/engine.py`, `logic/timing.py`, `devices/profile.py`,
`devices/sensors/tuya_generic.py`, `devices/health.py`, `devices/sensors/base.py`, `devices/irrigators/base.py`,
`devices/irrigators/tuya_generic.py` (tr301z was already listed). `engine.py` is now clean of
C901/PLR0911/PLR0912/PLR0915 at the ratchet thresholds → its per-file ignore can be dropped. Remaining device
strict errors: `gateway.py` (28: `object` parser types, untyped tinytuya client), `ik10pw.py` (`open_local` arg).
New lint families (heads-up): only pre-existing/verbatim hits in engine/timing (RUF001/RUF003 on the frozen `×`/`–`
user strings and the moved comment, RUF046 `int(round(…))` moved verbatim, timing SIM102 in untouched
`seasonal_multiplier`) plus my `_scaled_interval` docstring's `×` (RUF002, one-word fix for the ratchet stage).

## Proposed size-exception entries

None new (engine.py file length is already registered; its line count grew 936 → 1070 from helper signatures and
docstrings).

## Dead code (outside my files / not removed — for the final sweep)

- `StressIndicators.any_critical()` (`logic/decision.py`, not mine).
- `logic/timing.is_within_preferred_hours` — only users are `tests/test_timing.py` (an existing test, so not
  removed here); `constants.DEFAULT_PREFERRED_WATER_HOURS` is its only consumer (+ plugin LOGIC.md).
- `DeviceRegistry.registered_irrigator_keys` / `registered_sensor_keys` — only tests use them.
- `engine._seasonal_overrides`' `isinstance(care, dict)` guard is unreachable (kept by design, §3.2).

## Bugs touched (preserved)

Terminal steps bypass vacation rationing; inclusive cooldown/leak edges; zero-reason decisions; critical stress on
the average — all unchanged and grid-pinned. `ik10pw._start_keepalive` (safety bug #3) untouched.

## Look hardest at

T8.7 (single `_finish` for six exits; `override` computed after `_evaluate_rules`), T8.10 (consumption query moved
ahead of the arithmetic), T8.11 (dict reads after `get_preferences`), T8.9 (lambda filters for dry/wet names).

## Review follow-ups (orchestrator)
- R1 N1 / R2 F1: vacation rationing restored to the base order — the allowance arithmetic runs before the
  consumption query again (`_allowed_cumulative_liters`), so even a corrupt non-numeric `reservoir_l` raises before
  the query exactly as at base (R2 `repro_vacation_order.py`: 25 statements at base and after the fix; 26 before).
- R1 N2: `RainForecast.get_forecast(hours: int = ...)` (Protocol default, typing only).
- R2 F2 (declared seam move): patching `engine.is_within_quiet_hours` / `engine.parse_moisture_target` no longer
  steers the engine (names gone); patch `logic.timing.is_within_quiet_hours` / `plant_needs.parse_moisture_target`.
  No test patches either.

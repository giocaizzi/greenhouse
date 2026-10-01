# 50 — Adversary 5: plan critic (Gate 2)

Scope: `refactor/20-plan.md` + `refactor/20-target-architecture.md`, checked against the code at `d5adefd` and the
committed Phase-1 safety net. I read code and ran small commands only. Every finding cites evidence that can be reproduced.
Scratch configs are in the session scratchpad (`il.toml`, `il2.toml`, `fnlen.py`).

## Blockers

### B1 — T8.13 / T8.14 (move the rules to `logic/rules.py`) will turn a frozen invariant test red
- **Evidence.** `tests/test_invariants_engine.py:232-238`:
  ```python
  imported = {n for n in vars(engine_mod) if n.isupper() and hasattr(constants_mod, n)}
  assert len(imported) >= 50
  ```
  Measured today (AST over `engine.py`):
  - the engine imports **exactly 50** constants;
  - **11** are used inside `IrrigationLogic`;
  - **39** are used *only* by the module-level rule functions that T8.13 moves out.
  
  After the move, `engine.py` keeps roughly 11 constants, plus the constants T8.1 adds and those used by
  `_base_decision` / `_quiet_hours_skip`. That is about 20, well under 50. Ruff `F` (enabled) then flags the
  leftover imports as unused (F401).
- **Why it matters.** Rule 8 forbids editing this test. T8.13 cannot be committed green, and WP8, the last WP, stalls.
- **Fix.** Drop T8.13 and T8.14:
  - decompose the rules in place inside `engine.py`;
  - record "file > 400 lines: pinned by `test_inv5_engine_constants_mirror_constants_module`" in REFACTOR_NOTES.
  
  The only alternative is ~35 `# noqa: F401` keep-alive imports, which games the test. Do not choose it silently;
  escalate it to the owner. Also remove `logic.rules` from §2, §11 and I1.

### B2 — T1.1 (`resolve_server_url`) breaks the strict `env_reads.json` golden, and its gate cannot see it
- **Evidence.**
  - `tests/golden/contracts/env_reads.json` pins `os.environ.get("IRRIGATION_SERVER_URL", "http://localhost:8000")`
    separately for `greenhouse_cli.commands._helpers`, `.auth` and `.tui`.
  - `tests/server/test_contract_settings.py:319-343` builds that list by AST-scanning every module under `libs/`.
  - Merging the three reads into `_helpers` removes 2 golden entries, so the test fails. That golden is strict by the
    REFACTOR_NOTES policy.
  - T1.1's subset (`$CLI tests/cli/test_tui.py`) does not include `test_contract_settings.py`. The break would surface
    only at the integration gate.
- **Fix.** Drop T1.1, and list it under §7.3 "must stay separate (golden-pinned env read per module)". Also add
  `tests/server/test_contract_settings.py` to `$CLI`.

## Major

### M1 — I1 cannot add `logic.rules` to the §11 "Inside logic" contract as written (moot if B1 drops the module)
- **Evidence.**
  - In import-linter, `|` siblings are **independent**. Probe `il2.toml` (layer `engine | plant_needs`) returns
    `BROKEN: greenhouse_core.logic.engine is not allowed to import greenhouse_core.logic.plant_needs`.
  - §11 puts `rules | timing | cleaning | plant_needs` in one layer.
  - §2.1 and §3.3 have `rules.py` import `logic.plant_needs` (`moisture_target_range`).
- **Fix.** Give `rules` its own layer between `fallback|sensors|stress|trends` and `timing|cleaning|plant_needs`. All 10
  contracts as T0.2 would write them **pass today** (`lint-imports --config il.toml`: 10 kept, 0 broken).

### M2 — T0.3 ruff ratchet: "the repo must be clean afterwards" fails on `tests/`
- **Evidence.** Running `ruff check --isolated --select C90,PLR0911,PLR0912,PLR0915 max-complexity=10 tests/` gives
  5 hits, all in Phase-1 files:
  - `tests/engine_grid.py:203` (C901, PLR0912);
  - `tests/test_invariants_engine.py:295` (C901, PLR0911);
  - `tests/cli/test_contract_tui.py:126` (PLR0911).
  
  The baseline offender file has 0 `tests/` lines. The `libs/` offenders do match the baseline exactly (diff empty).
- **Why it matters.** WP0 is serial, so its first ratchet task stops ("stop and report"). Implementers may not edit tests.
- **Fix.** Add a per-file ignore `"tests/**" = ["C90","PLR0911","PLR0912","PLR0915"]`, or scope the rules to `libs/`.

### M3 — WP4 per-task test subsets miss every golden that actually renders the refactored output
- **Evidence.**
  - T4.1–T4.9 use only `D(gs.services.*)`. The direct map lists importers only. None of the D lists for `health`,
    `forecast`, `maintenance`, `data_quality`, `charts` or `insights` contains:
    - `test_contract_web_html.py` (which pins `plant_dashboard*`, `plant_health_fragment*`, `quality_page`, `health_page`);
    - `test_contract_check_all.py` / `test_contract_pipeline.py`;
    - `test_contract_tui_screens.py`.
  - The consumers are `compute_score`: `plant_dashboard.py:78`, `routes/plants.py:278`; `build_report`:
    `web/routes/quality.py:17`; `collect_maintenance_alerts`: `irrigation.py:681`, `alerts.py:97`;
    `predict_next_irrigation`: `analytics.py:119`.
- **Why it matters.** A golden diff surfaces only at the WP gate, several commits later. That breaks "one commit
  green; red → revert".
- **Fix.** Add `$WEB tests/server/test_contract_check_all.py tests/server/test_contract_pipeline.py
  tests/cli/test_contract_tui_screens.py` to T4.1–T4.9. In general, build each task's subset as importers **plus**
  every golden whose route or screen calls the function. Today the plan uses only the import map.

### M4 — Under-delivery on the owner's method-level ask: no measurable Definition of Done, and the size/CC/type targets are unenforced
- **Evidence (no Definition of Done).** The plan has no Definition of Done (grep `Definition of Done|DoD|400 lines`
  finds nothing). §10.7's "≤ 40 lines, CC ≤ 8" applies only to *touched* functions and is report-only
  (G7 says "Do not fail the task"). The ruff ratchet uses `max-complexity = 10`, not 8.
- **Evidence (long functions with no verdict).** `fnlen.py` finds these functions over 40 lines that are neither
  decomposed nor given a reason in §3.12:
  - `charts.build_overlay_payload` (60), `ClusterService.get_cluster_status` (56), `sync.sync_single_sensor` (56);
  - `SyncService._cluster_snapshot` (49), `scheduler.init_scheduler` (49), `profiling.compute_sensor_response` (49);
  - `routes/auth.login` (47), `web/routes/irrigators.create_irrigator` (46), `alerts.sync_cluster_alerts` (46);
  - `WeatherClient.get_forecast` (45), `routes/irrigators.add_irrigator` (44), `routes/operations.irrigate` (43);
  - `routes/clusters.get_cluster_detail` (42), `engine._enforce_leak_hold` (42);
  - `ClusterScreen._render_overview` (63, CC 11) and `ClusterScreen.compose` (50).
- **Evidence (still over target after the plan).** `schedule_pump_watcher` stays about 90 lines (only
  `_watcher_tuning` is extracted).
- **Evidence (files over 400 lines with no reason).** These stay over 400 lines afterwards, and only `repository.py` and
  `schemas.py` have a documented reason: `engine.py`, `services/irrigation.py` (grows), `scheduler.py` (560),
  `client.py` (464, grows), `tui/screens/cluster.py` (800, gains 14 handlers), `models.py`, `health_monitor.py`.
- **Evidence (types).** Typed interfaces are not gated:
  - the strict list is only the 35 modules that are already clean (verified: `mypy --strict` reports 0 issues);
  - touched modules never have to join it. Baseline strict errors: `engine` 20, `scheduler` 16, `repository` 16,
    `cluster.py` 21, `irrigation` 10;
  - there is no "add type annotations" task for `engine` (`_apply_seasonal_multiplier(self, cluster, decision,
    plant_care, evaluated_at)` is untyped), `scheduler`, `irrigation` or `repository`.
- **Fix.**
  1. Add a Definition of Done to I5 that **fails**: every function in `libs/` is ≤ 40 lines and CC ≤ 8, or has a
     reason in REFACTOR_NOTES (one line each). The same applies to files over 400 lines.
  2. Give every function listed above a verdict in §3.12.
  3. Require each high-touch module (engine, irrigation, scheduler, client, cluster screen, health, forecast, trends,
     stress, issues) to finish its WP strict-clean and join the mypy list, with an explicit
     `add type annotations` task per module.

### M5 — 8 parallel worktrees on a 4-core box, each running `-n 4`, invite flaky reds and wrong reverts
- **Evidence.**
  - BRIEF: "The machine has 4 cores".
  - REFACTOR_NOTES: "One TUI contract test failed once under heavy machine load".
  - The plan: "WP1–WP8 … simultaneously in 8 worktrees". `t()` hard-codes `-n 4`; G+ tasks run 74–91-file
    conservative maps; WP7 and WP8 run the full suite.
- **Fix.** Use at most 2 concurrent WPs, or serialize test runs through `flock /tmp/gh-tests.lock`. Make `-n` a
  variable (`${GH_XDIST:-2}`). Rule: "flaky red → rerun once on an idle box before reverting".

## Minor

- **m1 — T7.16: `CheckResult` with a dynamic `detail_key` is not a well-typed `TypedDict` literal.** mypy rejects
  non-literal keys, so the "typed builder" will need a `cast`. Fix: use two explicit builders, or keep the three dict
  literals and annotate them `CheckResult`.
- **m2 — P0 is incomplete. Evidence:** `git tag` prints nothing (no `refactor-gate1`), and `.git/info/exclude` still has
  a `# TEMP` block (`/refactor/gate1/`). WP0 says "branch from `refactor-gate1`". Fix: finish P0 before Gate 2 closes.
- **m3 — The mutation passes (T6.6, T8.∑) have no tool, threshold or timing.** `import mutmut` gives
  `ModuleNotFoundError`, and T0.1 adds only mypy and import-linter. Fix:
  - add `mutmut` to the dev group in T0.1;
  - run the pass on the **pre-refactor** module against the Phase-1 targets (`refactor/10-safety-*.md` "mutation
    targets");
  - require surviving mutants to be killed or justified before the task starts.
- **m4 — T5.15 (`create_app`, which wires auth) uses G+, but the WP5 gate is only a union of subsets.** BRIEF lists
  auth paths as high risk. Fix: run the full suite once at the end of T5.15, or for the WP5 gate.
- **m5 — T7.12 changes the condition the health block tests.** `if alarms:` replaces `if blocked:`. This is equivalent
  only because `is_actuation_blocked` returns `bool(blocking), blocking` (`health_monitor.py:232`), a module owned by
  WP4. With `(True, [])`, today's code raises IndexError; the new code would actuate. Fix: state that dependency in
  T7.12, and keep `blocked, alarms = …; if blocked:`. This is a cheap, exact preservation.

## Nits

- **T8.7: the `or` contradicts the plan's own rule.** §3 allows `or` "only when both sides are calls with no side
  effects before the test", but `_apply_water_warning_rule` and `_apply_critical_stress_rule` mutate `decision`. The
  result is still equivalent through short-circuiting. Reword the rule, or keep the two `if`s.
- **T7.7: `_build_irrigation_service(repo, registry, cloud)` must read the global `_app`.** It needs
  `weather_client`, `plant_db`, `ntfy_notifier` and `health_monitor` from `_app.state`. That is the "hidden global
  read" D8 rejected. Pass `app` explicitly.
- **T0.5 guard #1 ("no textual on CLI import") must run in a subprocess.** In-process, other tests have already
  imported `textual`. Verified today: `textual` is not in `sys.modules` after `import greenhouse_cli.main`.
- **CLAUDE.md conflicts with BRIEF on the plugin docs.** CLAUDE.md requires a `references/LOGIC.md` update when
  `constants.py` changes; BRIEF puts `plugin/` out of scope. Flag this for the owner in the PR body, not only in
  REFACTOR_NOTES.
- **§8 name rule: add the Textual-reserved prefixes.** Add `key_` and the `render*` / `_render` family, or better, a
  guard that new `Screen`/`Widget` methods don't shadow `dir(textual.widget.Widget)`. The proposed handler names were
  checked and do not collide today.

## Verified OK (no action)

- **import-linter.** The §11 contracts minus the pending modules: 10 kept, 2 + 1 ignored.
- **mypy.** The T0.4 seed list is clean under `--strict --follow-imports=silent`.
- **Constants.** The §9 names have no collisions (only `SENSOR_HEALTH_BACKFILL_WINDOW` exists, and the plan says not to
  reuse it).
- **Weekdays.** The three weekday copies are identical tuples.
- **CSV.** The two CSV exporters are line-identical, and `format_timestamp` comes from core (no layering issue).
- **Quiet hours.** The web `quiet_active_now` matches the engine's `_resolve_quiet_window` (same effective-config dict,
  prefs read first).
- **Scheduler.** `_job_session` keeps the session, try, commit, rollback, log and close order of all 5 jobs.
  `func_ref` is unaffected.
- **Repository.** The `_patch_fields` / hasattr-first split matches `repository.py:954-1309`. `update_sensor` keeps its
  `plant_id` pop and the reassignment after the loop.
- **Engine.** `decide_for_cluster` call order matches §3.2. `_finalize` covers the weather, fallback, water-warning,
  stress, window and final exits.
- **TUI surface.** The golden's walker filters out modules with no public UPPERCASE names, so the new `tui/render.py`
  and the `Row` alias are safe. `STATS_DAYS`, `METRIC_ORDER` and `RANGES` stay in the screen.
- **Test maps.** Every `D()`/`C()` key the plan uses exists in the maps. `t()`'s `sort -u` keeps the test paths
  grouped by directory.

## Verdict

**REVISE.** Must fix before execution: **B1** (T8.13/T8.14 vs `test_inv5 >= 50`) and **B2** (T1.1 vs `env_reads.json`),
then **M1** (rules layer contract), **M2** (ruff ratchet on `tests/`), **M3** (WP4 golden-blind subsets), **M4**
(enforceable Definition of Done, plus verdicts for the unaddressed long functions and big files, plus strict typing
of touched modules) and **M5** (test concurrency).

---

# Round 2 — revised plan (Rev 1, `aaf5a5f`)

Re-checked against code at `aaf5a5f`. Probes are in the session scratchpad: `il_r2.toml`, `body.py`, `nest.py`, `mypy.ini`, `cc8.txt`.

## Round-1 findings: resolution status

| Finding | Status | Evidence |
|---|---|---|
| B1 | **Resolved** | T8.13/T8.14 are struck. T8.0 says "every constant import stays (≥ 50 rule)". No WP8 task removes a constant use. |
| B2 | **Resolved** | T1.1 is struck, WP1 owns only `client.py`, and `$CLI` now includes `$SETTINGS`. |
| M1 | **Resolved** | Re-ran the revised §11 TOML (minus `tui.render`): `Contracts: 10 kept, 0 broken`. |
| M2 | **Resolved** | T0.3 adds the `tests/**` per-file ignore. At threshold 8, `libs/` has exactly 27 C901 hits, matching §3.12. |
| M3 | **Resolved** | Every WP4/WP6/WP8 row carries `$RENDER` / `$CHECK`, and the subset rule is written down. One residual slip is m-R2-3. |
| M4 | **Mostly resolved** | My AST scan matches the table's sizes: all 51 functions over 40 body lines and all 27 with CC > 8 have a verdict in §3.12, and the `OK` rows agree. Strict mypy is now a per-task gate. The boundary profile is feasible: with the §10.13 override, the 15 T5.0b/T5.0c route files show only 2 `arg-type` errors (in `plant_dashboard.py`), so no signature needs editing. **But the nesting axis was left out** (see M-R2-2). |
| M5 | **Resolved** | ≤ 2 worktrees, `flock`, `-n 2`, waves. **But the rerun rule adds a hole** (see M-R2-1). |
| m1–m5 and nits | **Resolved** | `cast` with a reason; the TEMP block is removed from `.git/info/exclude`; mutmut is added; T5.15 runs `FULL`; T7.12 is dropped; `app` is passed explicitly; two `if`s are kept; guard 1 runs in a subprocess; guard 4 is added. The `refactor-gate1` tag still does not exist (`git tag` is empty); the orchestrator must create it before WP0. |

## New findings

### M-R2-1 (major) — "rerun once" can turn a real regression into a "flaky" pass (§0.2)
- **Hash-seed-dependent output.** Nothing pins `PYTHONHASHSEED`: there is no hit for `PYTHONHASHSEED` or `randomly` in
  `pyproject.toml`, `tests/conftest.py`, the Makefile or CI. String-set iteration order changes per process:
  `python -c "print(list({'soil','temp','light','humidity'}))"` printed `['light','humidity','temp','soil']` on one
  run and `['soil','light','temp','humidity']` on the next. Iteration order is exactly the drift class this refactor
  must catch. A helper that builds an output through a `set` makes a strict golden fail only *sometimes*, and §0.2
  then rules "green on rerun → flaky, continue".
- **Order- and state-dependent regressions.** The rerun runs only the failing tests, alone and with `-n 0`. That hides
  any regression that depends on other tests' leftover state. This suite has plenty of such state: process-wide
  `set_display_timezone`, scheduler module globals, root log handlers replaced by `create_app`.
- **Fix.**
  1. `export PYTHONHASHSEED=0` inside `t` and `FULL`. Add one extra `PYTHONHASHSEED=1` run of the WP-gate union, and
     of I5.
  2. The rerun must be the **identical subset command**, not just the failing tests.
  3. "Flaky" requires either that the test is on a known-flaky list frozen at Gate 1 (today: the one TUI contract
     test), or that the same failure reproduces on the parent commit under the same command. Anything else is red →
     revert.
  4. Frozen-clock goldens and the decision grid are never eligible for "flaky".

### M-R2-2 (major) — the DoD table has no nesting column, so T0.9 and the WP/final DoDs are inconsistent
- **The checker includes nesting.** §3.12 and §11 define sizecheck as "body > 40 **or nesting > 3**". But the verdict
  table only lists functions over 40 lines or with CC > 8.
- **Uncovered functions.** By the §3.12 nesting definition (`if/for/while/with/try/match`; `elif` not counted), these
  functions have nesting > 3 and no task or EXC row:
  - `services/irrigation.py::rearm_leak_checks`: try→for→for→if = 4. WP7 touches the file, the WP DoD requires the
    whole file to pass, and §3.1 says rearm stays "unchanged".
  - `sync.py::sync_sensor_data`: for→for→try→if = 4. WP8 touches the file.
  - `stats.py::export_csv`: with→…→for→if ≥ 4. WP6 touches the file.
  - `services/bulk.py::stop_all_irrigators`: for→try→if→try = 4. The table marks it **OK**, which is wrong under the
    nesting rule.
  - `tui/screens/forms.py::FormScreen.compose`: with→with→for→with→if = 5.
- **Consequences.**
  - T0.9's claim that "after seeding, `make sizecheck` lists only functions that have a task" is false on day 1.
  - The WP7, WP6 and WP8 DoD gates fail on functions nobody was told to touch, so the implementer has to "stop and
    report".
  - I5 fails on `bulk.py` and `forms.py`.
- **Fix.** Add a nesting column to §3.12, and give the five rows a verdict. Suggested verdicts:
  - EXC for `compose` (declarative tree) and for `bulk` (actuation; B-list `services/bulk.py`);
  - a task or EXC for the other three.
  
  T0.9 should assert the seeded register reproduces `make sizecheck` silence.

### Minor
- **m-R2-1 — the size-exception register is self-certifying.** Implementers append to `refactor/size-exceptions.txt`,
  and sizecheck then skips those entries; nothing requires approval. Fix: any entry not seeded by T0.9 needs the second
  reviewer's or the orchestrator's initials on the line, and I1 rejects entries without them.
- **m-R2-2 — the M-post pass condition cannot be computed.** "No target line killed in M-pre survives" (§0.5) breaks
  once code moves into helpers, because line identity is lost. Fix: key mutants on (original function, operator,
  token) via a mapping the implementer records. Alternatively, require "M-post kill rate on the target behaviours ≥
  M-pre, and ≥ 75 %".
- **m-R2-3 — subset slips.**
  - T5.18 (`plant_dashboard`) omits members of `D(gs.web.routes.plant_dashboard)`: `test_web_charts`,
    `test_web_charts_overlay`, `test_search`, `test_web_crud_actions`. That violates §0.3's own rule.
  - T7.20/T7.21 omit `D(gs.services.leak)` (20 files), and T8.17 omits `D(gc.sync)` (23 files). For these the
    end-of-task C-map runs, but per-commit feedback is late.
- **m-R2-4 — `core/sync.py` is missing from the mutation list.** It is G+, carries invariant 8 and has mutation
  targets in `10-safety-ingress-devices.md`, yet it is not on the §0.5 list. Add it.
- **m-R2-5 — the `mutate_probe.py` "temp copy" won't be imported.** The editable install's `.pth` points at `libs/…`.
  Fix: mutate in place in a dedicated throwaway worktree, never the implementer's, and restore with
  `git checkout -- <file>` in a `finally`.

### Nits
- **Annotation-only tasks may add runtime imports.** Example: `_watcher_tuning(settings: Settings | None)` at module
  level in `services/irrigation.py`, which has no `from __future__ import annotations`, while `Settings` is imported
  lazily today. Require `TYPE_CHECKING` or future annotations for any import added only for typing, so import-time
  behaviour is unchanged.
- **Helpers in boundary-profile modules lose the strict-typing gate.** `disallow_untyped_defs = false` covers the whole
  module, so helpers there are checked only by review. A 10-line AST guard ("every `_`-prefixed def in
  routes/web.routes/commands is fully annotated") would make it a gate.
- **Wave B pairs WP7 (high) with T5.15.** T5.15 is auth wiring, which BRIEF lists as high risk. Book reviewers for both,
  or move T5.15 to wave C.

## Round-2 verdict

**REVISE.** Two majors block, and both are cheap to fix:
- **M-R2-1:** pin `PYTHONHASHSEED`, rerun the identical command, and accept "flaky" only via the Gate-1 list or a
  parent-commit reproduction.
- **M-R2-2:** add a nesting column and verdicts for `rearm_leak_checks`, `sync_sensor_data`, `export_csv`,
  `stop_all_irrigators` and `FormScreen.compose`.

Everything else from round 1 is resolved. The minors and nits do not block.

---

# Round 3 — Revision 2 (`0a5e1ea`)

## M-R2-1 (rerun rule can mask a real regression) — **resolved**
- **Hash seed pinned.** Plan L102–104 set `PYTHONHASHSEED="${GH_SEED:-0}"` inside both `t` and `FULL`. xdist workers
  inherit the environment. `FULL_SEED2` (seed 12345) runs at:
  - every WP gate (T7.∑, T8.∑ and the others);
  - the integration gate;
  - I5.
- **Rerun rule.** A red is re-run with the *identical* command. It counts as flaky only if the test is on
  `refactor/gate1/flaky-tests.txt` (the file exists and is orchestrator-owned) or the failure reproduces on the parent
  commit. Otherwise the commit is reverted. This closes both the hash-order hole and the isolated-rerun hole.

## M-R2-2 (no nesting column in §3.12) — **resolved**
- **Scan re-run.** I re-ran `nest.py` (same definition as §3.12: `if/for/while/with/try/match`, `elif` not counted)
  against the revised table.
- **Result.** All **14** functions with nesting > 3 have a row, and the table's Nesting value equals my scan for every
  one of them:

  | Function | Nesting | Verdict |
  |---|---|---|
  | `ClusterScreen.compose` | 4 | EXC |
  | `FormScreen.compose` | 5 | EXC |
  | `_start_keepalive` | 4 | EXC |
  | `tuya_generic.status` | 5 | EXC |
  | `detect_conflicts` | 4 | T6.8 |
  | `detect_issues` | 4 | T6.9 |
  | `analyze_historical_trends` | 4 | T6.5 |
  | `export_csv` | 4 | T6.12 |
  | `sync_sensor_data` | 4 | T8.18 |
  | `routes/plants.sync_plants` | 4 | T5.7 |
  | `bulk.stop_all_irrigators` | 4 | EXC (the wrong OK is fixed) |
  | `rearm_leak_checks` | 4 | T7.22 |
  | `collect_maintenance_alerts` | 5 | T4.5 |
  | `web/routes/operations.sync_plants` | 4 | T5.8 |

- **Seams in the new tasks.**
  - T7.22 keeps `now = int(_time.time())`, the try/except/finally block and both log lines in `rearm_leak_checks`.
    `_add_leak_check_job` and `_leak_check_done` are still module globals resolved at call time.
  - T8.18 is G+ with `C(gc.sync)`, and its subset includes `D(gc.sync)`.

## Round-2 minors — all addressed
- The size-exception register is now approval-gated.
- Mutant identity is keyed on (operator, snippet, qualname).
- The T5.18 / T7.20–21 / T8.17–18 subsets now include `D(...)`.
- `sync.py` is on the mutation list.
- The probe mutates in place and restores with `git checkout` in `finally`.

## New findings (none blocking)
- **minor — T6.12 targets `stats.export_csv`, which no test covers.** `grep -rn export_csv tests` finds nothing, and
  REFACTOR_NOTES calls the function dead. G-step 1 (the coverage precondition) will therefore stop T6.12 until a
  characterization test exists. Fix: commission that test together with the other mutation-gap tests, before
  `refactor-gate1`, or make `export_csv` an EXC entry (dead public function, frozen import path).
- **nit — reverting in an implementer's worktree is destructive.** The probe now mutates in place in the
  implementer's own worktree. Run it only on a clean tree (`git status --porcelain` empty), so the `git checkout --`
  restore cannot discard uncommitted work.

## Round-3 verdict

**APPROVE.** No blocker or major remains. One condition stays open from earlier rounds: the orchestrator creates
`refactor-gate1` (after the mutation-gap tests land) before WP0 starts. The T6.12 characterization test should land
in the same batch.

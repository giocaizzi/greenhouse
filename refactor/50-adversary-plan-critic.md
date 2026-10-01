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

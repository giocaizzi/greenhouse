# 20 — Execution plan

This plan is executable. Each task is **one small behavior-preserving commit**. The design behind each task (signatures,
order guarantees, why) is in `refactor/20-target-architecture.md`; task rows cite the section (§n). Read that section
before you start a task. Do not re-derive the design. If the code contradicts the section, **stop and report** rather
than improvise.

## 0. Ground rules for implementers

### 0.1 Prerequisites (orchestrator, before any worktree is cut)

- **P0 — Gate 1 commit.**
  1. Commit the Phase-1 safety net. Today it is untracked via `.git/info/exclude` (`tests/**/test_contract_*.py`,
     `tests/golden/`, `tests/engine_grid.py`, `tests/test_invariants_engine.py`, `tests/test_properties_logic.py`,
     the `refactor/10-safety-*.md` files).
  2. Remove the `TEMP` block from `.git/info/exclude`.
  3. Run the full suite green.
  4. Tag the commit `refactor-gate1`.

  **Worktrees created before this would not contain the goldens.**
- **P1.** WP0 is implemented serially on the integration branch `claude/focused-hawking-7to7o3` and merged first.
  Every other WP branches from the WP0 head.

### 0.2 Worktree workflow

```bash
git worktree add ../gh-wp4 -b refactor/wp4-services <WP0-head>      # one worktree per WP
cd ../gh-wp4 && uv sync                                            # per worktree venv
# … one commit per task, in table order …
git fetch && git rebase <integration-branch>                       # before handing back; re-run the WP gate
```

- **Files you must not touch.** After WP0, `pyproject.toml`, `uv.lock`, `Makefile` and `REFACTOR_NOTES.md` are
  **integrator-only**. Implementers never edit them, `tests/golden/**` or any existing test. If you need a ratchet
  change, a new constant or a REFACTOR_NOTES entry, put the request in your hand-off note.
- **Files you own.** Each WP owns a disjoint file set (§1). Touch nothing outside it; if a task seems to need it, stop
  and report.
- **When a test goes red, revert and report:** `git reset --hard HEAD` (or `git revert`). Do not fix forward, and
  never edit a test or golden to match.
- **Commit message format:**
  ```
  refactor(<area>): <technique> — <what>

  Behavior: none. <one line on why it is equivalent, e.g. "same call order: A → B → C">
  Evidence: <exact pytest command(s) run, result>; lint-imports OK; tests/golden unchanged.
  Bugs touched (preserved): B-n … | none
  ```
  End it with the attribution lines from the session's system reminder.
  - `<area>` ∈ {build, lint, test, core, schemas, repo, logic, learning, engine, devices, services, scheduler,
    pipeline, api, web, app, cli, tui}.
  - `<technique>` ∈ {extract function, extract method, introduce constant, introduce parameter object, introduce
    typed result, replace literal, move function, rename, consolidate duplicate, decompose conditional, replace
    conditional with dispatch, add type annotations, add docstrings}.
- **One concern per commit.** Moves, renames, extractions, constant adoption and formatting never share a commit.

### 0.3 Test aliases (paste into your shell)

```bash
CORE="tests/test_contract_imports.py tests/test_contract_constants.py tests/test_contract_schema.py tests/test_contract_packaging.py"
API="tests/server/test_mcp.py tests/server/test_contract_openapi.py tests/server/test_contract_mcp.py tests/server/test_contract_settings.py tests/server/test_contract_auth.py"
WEB="tests/server/test_contract_web_html.py tests/server/test_contract_web_mutations.py tests/server/test_contract_web_errors.py"
CLI="tests/cli/test_contract_help.py tests/cli/test_contract_json_output.py tests/cli/test_completeness.py tests/cli/test_cli.py"
TUI="tests/cli/test_contract_tui.py tests/cli/test_contract_tui_screens.py tests/cli/test_contract_tui_actuation.py tests/cli/test_contract_tui_runtime.py tests/cli/test_tui.py"
ENGINE="tests/test_contract_decision_grid.py tests/test_invariants_engine.py tests/test_properties_logic.py tests/test_logic.py tests/test_engine_timing.py tests/test_leak_hold.py tests/test_vacation_rationing.py tests/test_timing.py tests/server/test_contract_pipeline.py"
SCHED="tests/server/test_contract_scheduler.py tests/server/test_contract_scheduler_registry.py tests/server/test_contract_check_all.py tests/server/test_contract_pump_watcher.py tests/server/test_scheduler.py tests/server/test_scheduler_jobs.py tests/server/test_scheduler_pause.py tests/server/test_scheduler_settings.py tests/server/test_scheduler_shutdown.py tests/server/test_leak_rearm.py tests/test_migrations.py"
PIPE="tests/server/test_contract_pipeline.py tests/server/test_contract_check_all.py tests/server/test_operations.py tests/server/test_decisions.py tests/server/test_irrigators.py tests/server/test_notify.py tests/server/test_health_monitor.py tests/server/test_rate_limit.py tests/server/test_leak_rearm.py tests/server/test_web_operations.py tests/server/test_web_decision_rationale.py tests/server/test_web_dashboard.py tests/test_migrations.py"
DEV="tests/devices/ tests/test_cloud.py tests/server/test_contract_health_monitor.py tests/server/test_contract_sync_service.py tests/test_contract_sync.py"
# D gc.logic.trends  → direct map;  C gc.logic.engine → conservative map   (gc.=greenhouse_core. gs.=greenhouse_server. gcli.=greenhouse_cli.)
tmap() { python3 - "$@" <<'EOF'
import json, sys
kind, mod = sys.argv[1], sys.argv[2]
mod = mod.replace("gcli.", "greenhouse_cli.", 1).replace("gc.", "greenhouse_core.", 1).replace("gs.", "greenhouse_server.", 1)
f = {"D": "refactor/baseline/module-tests-map-direct.json", "C": "refactor/baseline/module-tests-map.json"}[kind]
print(" ".join(json.load(open(f))[mod]))
EOF
}
D() { tmap D "$1"; }; C() { tmap C "$1"; }
t() { uv run pytest -q -n 4 $(echo "$@" | tr ' ' '\n' | sort -u); }   # dedupe + xdist
# example: t $ENGINE $(D gc.logic.trends)
```

In the task tables, `D(x)` / `C(x)` mean `$(D x)` / `$(C x)`, and `$ALIAS` means the variable above.

### 0.4 Gates

- **G0 (WP0 tooling tasks):**
  1. The command in the task's Tests column passes on otherwise unchanged code.
  2. `t $CORE` is green.
  3. `git status --porcelain tests/golden` prints nothing.
  4. `uv run pytest -q -n 4` runs once at the end of WP0. WP0 is the base for every worktree, so it must be
     full-suite green.
- **G (every task):**
  1. `uv run ruff check <touched files> && uv run ruff format --check <touched files>`
  2. `uv run lint-imports`
  3. `t <task subset>` is green
  4. `git status --porcelain tests/golden` prints nothing
  5. `uv run mypy` is green (the strict-list gate from T0.4)
  6. **Coverage precondition (before editing):**
     `uv run pytest -q <subset> --cov=<module> --cov-branch --cov-report=term-missing`. If a line or branch you are
     about to restructure is reported missing, **stop** and request a characterization test (orchestrator, Phase-1
     rules).
  7. **Report for the ratchet:** the CC of each function you decomposed
     (`uv run ruff check --isolated --select C90 --config "lint.mccabe.max-complexity=8" <file>`), and whether
     `uv run mypy --strict --follow-imports=silent <file>` is clean. Do not fail the task on either.
- **G+ (high-risk modules: engine, scheduler, `services/irrigation.py`, devices, `app.py`/auth wiring):** G, plus the
  conservative map `t C(<module>)` once at the end of the task, plus **two reviewers**, one of them an adversary who
  tries to construct an input that distinguishes old from new (the §12 checklist of the target doc).
- **WP gate (before hand-back, after rebase):** `t` over the union of all task subsets in the WP, plus `$CORE`.
  High-risk WPs (WP7, WP8) instead run the full suite once: `uv run pytest -q -n 4`.
- **Integration gate (integrator, after each merge):**
  - full suite `uv run pytest -q -n 4`;
  - one run with `TZ=America/New_York`;
  - `uv run lint-imports`, `make typecheck`, `uv run ruff check libs/ tests/`;
  - then the ratchet commit (I1).

## 1. Work packages

| WP | Scope (phase) | Exclusive files | Branch from | Merge order | Risk | Parallel with |
|---|---|---|---|---|---|---|
| **WP0** | Tooling + shared definitions (0, 1) | `pyproject.toml`, `uv.lock`, `Makefile`, `tests/test_refactor_guards.py` (new), `tests/test_moisture_target_range.py` (new), `greenhouse_core/constants.py`, `greenhouse_core/logic/plant_needs.py` | `refactor-gate1` | 1st | low | — (serial) |
| **WP3** | Data types + repository (1, 2) | `greenhouse_core/schemas.py`, `greenhouse_core/repository.py` | WP0 head | 2nd | med | all |
| **WP4** | Server services (3) | `greenhouse_server/services/{health,forecast,maintenance,data_quality,charts,insights,pump_watcher,health_monitor}.py` | WP0 head | 3rd | med | all |
| **WP7** | Scheduler + irrigation pipeline (3) | `greenhouse_server/scheduler.py`, `greenhouse_server/services/irrigation.py`, `greenhouse_server/config.py` | WP0 head | 4th | **high** | all |
| **WP5** | Route/web glue DRY + app factory (4) | `greenhouse_server/services/cluster.py`, `greenhouse_server/app.py`, `greenhouse_server/routes/*.py`, `greenhouse_server/web/weekdays.py` (new), `greenhouse_server/web/routes/*.py` | WP0 head | 5th | med | all (T5.13 waits for WP3 merge) |
| **WP1** | CLI (5) | `greenhouse_cli/client.py`, `greenhouse_cli/commands/{_helpers,auth,tui}.py` | WP0 head | 6th | low | all |
| **WP2** | TUI (6) | `greenhouse_cli/tui/render.py` (new), `greenhouse_cli/tui/screens/{cluster,system,settings}.py`, `greenhouse_cli/tui/model.py`, `greenhouse_cli/tui/widgets.py` | WP0 head | 7th | med | all |
| **WP6** | Core logic + learning (7a) | `greenhouse_core/utils.py`, `greenhouse_core/logic/{sensors,trends,stress,fallback}.py`, `greenhouse_core/learning/issues.py` | WP0 head | 8th | med-high | all |
| **WP8** | Engine + devices (7b, last) | `greenhouse_core/logic/{engine,timing}.py`, `greenhouse_core/logic/rules.py` (new), `greenhouse_core/devices/**` | WP0 head | 9th | **high** | all (merges last) |
| **INT** | Integrator | `pyproject.toml`, `Makefile`, `uv.lock`, `REFACTOR_NOTES.md`; I2 → `web/routes/clusters.py` (after WP5), I3 → `services/forecast.py` (after WP4) | integration branch | after each merge | — | serial |

- **Parallelism.** After WP0 merges, WP1–WP8 can be developed **simultaneously** in 8 worktrees: their file sets are
  disjoint, and no WP imports a name another WP introduces. The two exceptions are T5.13 (needs T3.7) and I2/I3
  (integrator, post-merge).
- **Merge order.** The integrator merges in the order shown, which implements the required phase order: data/types →
  repository → services/scheduler → glue → CLI → TUI → logic → engine/devices. Each merge is a rebase plus the
  integration gate.
- **Staffing.** If staff is short, one implementer can take WP1+WP2, and another WP3+WP4.
- **Reviewers.** WP7 and WP8 need two reviewers per G+ task. Book reviewers before they start.

**Dependency graph (only real edges):**

```
P0 → WP0 → {WP1, WP2, WP3, WP4, WP5, WP6, WP7, WP8}
WP3.T3.7 → WP5.T5.13
WP5 merged + WP8 merged → I2          WP4 merged + WP8 merged → I3
every merge → I1 (ratchet)            all merged → I5 (final gate)
```

## 2. Tasks

Columns: **ID · Title (commit subject) · Files · Tests · Risk · Gate · Notes.**
Within a WP, tasks are **serial** (each builds on the previous commit) unless marked ∥.

### WP0 — Tooling and shared definitions (serial, integration branch)

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T0.1 | `build(dev): add tooling — mypy and import-linter to the dev group` | `pyproject.toml`, `uv.lock` (dev group only) | `$CORE` | low | G0 | `uv add --group dev mypy import-linter`. Check that `uv run lint-imports --help` and `uv run mypy --version` work. Packaging golden must stay green (dev deps are not package metadata). |
| T0.2 | `build(lint): add import-linter contracts — encode today's layering` | `pyproject.toml` | `uv run lint-imports` | low | G0 | Copy target §11 **without** the `logic.rules` and `tui.render` entries (those modules don't exist yet; I1 adds them). The contracts must pass on unchanged code. If one fails, apply the §11 fallback and record why. Never change code here. |
| T0.3 | `build(lint): add ruff complexity ratchet — C90/PLR0911/PLR0912/PLR0915 with per-file ignores` | `pyproject.toml` | `uv run ruff check libs/ tests/` | low | G0 | `max-complexity = 10`. Per-file ignores = exactly the offenders in `refactor/baseline/ruff-complexity-rules.txt` (minus PLR0913, which is not enabled). The repo must be clean afterwards. |
| T0.4 | `build(types): add mypy strict ratchet — seed list of clean modules + make typecheck` | `pyproject.toml`, `Makefile` | `make typecheck` | low | G0 | Config per target §11. Seed list = modules with zero errors in `refactor/baseline/mypy-strict.txt`: `greenhouse_cli.tui.{formatting,sprites,screens.activity,screens.dashboard,screens.search}`, `greenhouse_core.{auth,constants,models,devices.registry,devices.sensors.tr301z,learning.learner,learning.report,logic.sensors,logic.stress}`, `greenhouse_server.{config,routes.activity,routes.alerts,routes.auth,routes.decisions,routes.efficacy,routes.forecast,routes.health,routes.insights,routes.quality,routes.well_known,services.anomaly,services.bulk,services.data_quality,services.efficacy,services.insights,services.search,services.system_health,services.vacation,web.router,web.templating}`. Drop any that fail under `follow_imports=silent`. `check:` gains `lint-imports` + `typecheck`. |
| T0.5 | `test: add refactor guard tests — no textual on CLI import, no "from time import time", no new public TUI constants` | `tests/test_refactor_guards.py` (new) | that file, run twice + `-n 2` | low | G | Target §11 "Guard tests". The TUI check reads the allowed names from `tests/golden/tui/surface.json`. |
| T0.6 | `refactor(core): introduce constant — new named thresholds (definitions only)` | `greenhouse_core/constants.py` | `$CORE` | low | G | Every name in target §9, with the value **and** type copied from the cited literal. First grep each name for collisions. No call site changes. Add a one-line comment per group naming its consumers. |
| T0.7 | `refactor(logic): extract function — moisture_target_range(care) (definition only)` | `greenhouse_core/logic/plant_needs.py`, `tests/test_moisture_target_range.py` (new) | new test + `$ENGINE` | low | G | `def moisture_target_range(care: Mapping[str, Any]) -> tuple[float, float]: return parse_moisture_target(care.get("soil_moisture_target", DEFAULT_SOIL_MOISTURE_TARGET))`. The new test is a Hypothesis equivalence check against the inline expression (`derandomize=True`). No adoption yet. |

### WP3 — Data types + repository

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T3.1 | `refactor(schemas): consolidate duplicate — shared _parse_json_config for both parse_config validators` | `schemas.py` | `$CORE $API` + `tests/server/test_irrigators.py tests/server/test_sensors.py` | low | G | Target §5. Both classes keep `@field_validator("config", mode="before") @classmethod def parse_config(cls, v): return _parse_json_config(v)`. Keep `import json`. Add no docstring to the classes. |
| T3.2 | `refactor(repo): extract method — _delete_by_id for five delete methods` | `repository.py` | `tests/test_db.py $CORE` + `tests/server/test_clusters.py tests/server/test_sensors.py tests/server/test_irrigators.py tests/server/test_vacation.py tests/server/test_irrigation_windows.py tests/server/test_web_crud_actions.py` | low | G | Target §3.9. `delete_plant` is untouched. |
| T3.3 | `refactor(repo): extract method — _patch_fields (None-first) for vacation and irrigation windows` | `repository.py` | `tests/test_db.py` + `tests/server/test_vacation.py tests/server/test_irrigation_windows.py tests/server/test_web_vacation_edit.py tests/server/test_web_windows.py $WEB` | low | G | |
| T3.4 | `refactor(repo): extract method — _patch_fields(json_fields={"config"}) for irrigator and sensor updates` | `repository.py` | `tests/test_db.py` + `tests/server/test_irrigators.py tests/server/test_sensors.py tests/server/test_sensor_assignments.py tests/server/test_web_irrigator_capacity.py tests/server/test_web_sensor_pages.py` | low | G | The `update_sensor` `plant_id` pop and `reassign_sensor_to_plant` stay in the method. |
| T3.5 | `refactor(repo): extract method — _patch_fields_hasattr_first for preferences, cluster and plant` | `repository.py` | `tests/test_db.py` + `tests/server/test_preferences.py tests/server/test_clusters.py tests/server/test_plants.py tests/server/test_web_preferences.py $WEB` | low | G | Keep the `hasattr(row, key) and value is not None` order. B-15 is preserved. |
| T3.6 | `refactor(repo): extract method — _page cursor pagination for list_all_sensors/irrigators/plants` | `repository.py` | `tests/server/test_sensors.py tests/server/test_irrigators.py tests/server/test_plants.py tests/server/test_system_health.py tests/server/test_anomaly.py tests/server/test_data_quality.py $API` | low | G | The caller's filters come first, then `_page`. |
| T3.7 | `refactor(repo): extract method — additive get_vacation_window(window_id)` | `repository.py` | `tests/test_db.py $CORE` | low | G | Body `return self.session.get(VacationWindow, window_id)`, plus a docstring. Unblocks T5.13. |
| T3.8 | `refactor(repo): replace literal — FULL_WEEKDAY_MASK as add_irrigation_window default` | `repository.py` | `tests/server/test_irrigation_windows.py tests/server/test_web_windows.py $CORE` | low | G | Same default value (127). |
| T3.∑ | WP gate | — | `t $(D gc.repository) $(D gc.schemas) $CORE $API $WEB` | — | WP | ≈ the server suite. |

### WP4 — Server services (∥ to each other after T4.1–T4.2; serial is simplest)

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T4.1 | `refactor(services): replace literal — named constants in maintenance, forecast, health, health_monitor, pump_watcher` | the 5 files | `D(gs.services.maintenance) D(gs.services.forecast) D(gs.services.health) D(gs.services.health_monitor) D(gs.services.pump_watcher) tests/server/test_contract_pump_watcher.py tests/server/test_contract_health_monitor.py $CORE` | low | G | Target §9 rows for these files. `_WEATHER_PRECIP_THRESHOLD_MM = WEATHER_SKIP_PRECIP_MM` (the alias stays). `compute_score(..., days: int = HEALTH_SCORE_WINDOW_DAYS)`. |
| T4.2 | `refactor(services): consolidate duplicate — moisture_target_range in health and forecast` | `health.py`, `forecast.py` | `D(gs.services.health) D(gs.services.forecast)` | low | G | Only the two byte-identical `parse_moisture_target(care.get("soil_moisture_target", …))` sites. |
| T4.3 | `refactor(services): extract method — decompose PlantHealthService.compute_score` | `health.py` | `D(gs.services.health) tests/server/test_charts_plant_health_timeline.py` | med | G | Target §3.6: `HealthScore`, `_HealthBands`, `_empty_score`, `_pooled_clean_readings`, `_in_band_pct`, `_band_percentages`, `_first_profile_efficiency`, `_composite_score`. |
| T4.4 | `refactor(services): extract method — decompose ForecastService.predict_next_irrigation` | `forecast.py` | `D(gs.services.forecast)` | med | G | Target §3.6. `now` is read before the loop; no-data return comes before weather; stable sort. |
| T4.5 | `refactor(services): extract function — per-check helpers in collect_maintenance_alerts` | `maintenance.py` | `D(gs.services.maintenance)` | med | G | Per-sensor append order kept. |
| T4.6 | `refactor(services): extract function — per-entity issue collectors in data_quality.build_report` | `data_quality.py` | `D(gs.services.data_quality)` | med | G | Pass order and per-sensor interleaving kept. |
| T4.7 | `refactor(services): consolidate duplicate — _aggregate_band in charts._threshold_for_cluster` | `charts.py` | `D(gs.services.charts)` | low | G | |
| T4.8 | `refactor(services): consolidate duplicate — repo.get_plant instead of session.get(Plant, …) in charts` | `charts.py` | `D(gs.services.charts)` | low | G | 2 sites (L50, L353). Same body. |
| T4.9 | `refactor(services): consolidate duplicate — _insight_from_alert in cluster_insights` | `insights.py` | `D(gs.services.insights)` | low | G | |
| T4.10 | `refactor(services): introduce typed result — WatchOutcome + _outcome builder in PumpWatcherService.watch` | `pump_watcher.py` | `D(gs.services.pump_watcher) tests/server/test_contract_pump_watcher.py $SCHED` | med | G | The caller computes `elapsed` at the same point (injected clock is call-count sensitive). `_handle_trip` is untouched. |
| T4.11 | `refactor(services): extract method — _raise_if_not_open in health_monitor.backfill_from_history` | `health_monitor.py` | `D(gs.services.health_monitor) tests/server/test_contract_health_monitor.py` | med | G | |
| T4.∑ | WP gate | — | union of the above + `$CORE $API $WEB` | — | WP | |

### WP7 — Scheduler + irrigation pipeline (HIGH RISK; every task G+)

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T7.1 | `refactor(scheduler): replace literal — CHECK_ALL_JOB_ID for the "check_all" literals (declaration hoisted)` | `scheduler.py` | `$SCHED $CORE` | med | G+ | Same name and value. The declaration moves above `_TZ_BOUND_CRON_JOBS`. |
| T7.2 | `refactor(scheduler): replace literal — anomaly interval, health-snapshot time, sync backfill hours` | `scheduler.py` | `$SCHED $CORE` | med | G+ | Target §3.8. The scheduler registry goldens prove the triggers are unchanged. |
| T7.3 | `refactor(scheduler): extract function — _job_session context manager, applied to _anomaly_job` | `scheduler.py` | `$SCHED` | high | G+ | Target §3.8. Pass the current `_app` global at call time. `func_ref` must stay the same (golden). |
| T7.4 | `refactor(scheduler): extract function — _job_session in _health_snapshot_job` | `scheduler.py` | `$SCHED tests/server/test_plants_health.py` | high | G+ | Drop the redundant inner `IrrigationRepository` import. |
| T7.5 | `refactor(scheduler): extract function — _job_session in _sync_job` | `scheduler.py` | `$SCHED tests/server/test_contract_sync_service.py` | high | G+ | The `cloud is None` debug return stays before the `with`. |
| T7.6 | `refactor(scheduler): extract function — _job_session in _health_monitor_job` | `scheduler.py` | `$SCHED tests/server/test_health_monitor.py` | high | G+ | Both guards stay before the `with`. |
| T7.7 | `refactor(scheduler): extract function — _job_session + _build_irrigation_service in _check_job` | `scheduler.py` | `$SCHED $PIPE` | high | G+ | `_app.state.weather_client` is read inside the `with`; `monitor.bind_repo` keeps its position. |
| T7.8 | `refactor(pipeline): replace literal — named constants in services/irrigation and pump-watcher Settings defaults` | `services/irrigation.py`, `config.py` | `$PIPE $SCHED tests/server/test_contract_settings.py $CORE` | med | G+ | `FALLBACK_TEMPERATURE_C` (the label `"fallback (20C)"` stays literal), `MONITOR_*`, `LEAK_CHECK_ACTIVITY_SCAN_LIMIT`, `PUMP_WATCHER_*`, `DEFAULT_SOIL_MOISTURE_TARGET`. The Settings schema golden must be byte-identical. |
| T7.9 | `refactor(pipeline): introduce typed result — PipelineResult, MonitorResult, CheckResult (annotations only)` | `services/irrigation.py` | `$PIPE` | low | G+ | Runtime unchanged. These are never FastAPI annotations. |
| T7.10 | `refactor(pipeline): extract function — _error_result, _decision_result, _decide, _log_decision_skip` | `services/irrigation.py` | `$PIPE tests/cli/test_tui.py` | high | G+ | Target §3.1. Key order unchanged. |
| T7.11 | `refactor(pipeline): extract method — _actuation_target and _with_error` | `services/irrigation.py` | `$PIPE` | high | G+ | Guard order: irrigator → registry → adapter. |
| T7.12 | `refactor(pipeline): extract method — _blocking_alarms and _apply_health_block` | `services/irrigation.py` | `$PIPE` | high | G+ | B-6 is preserved; the comment at L497-501 is rewritten (comment only). |
| T7.13 | `refactor(pipeline): introduce parameter object — _Actuation + _actuate, _event_notes, _on_started, _notify_auto_irrigation, _on_start_failed` | `services/irrigation.py` | `$PIPE tests/server/test_contract_pump_watcher.py` | high | G+ | `started_at = int(_time.time())` comes after `adapter.start`, inside this module (`_time` seam). `result["action"]="irrigated"` is set before notify. |
| T7.14 | `refactor(pipeline): extract method — split _resolve_temperature into indoor/outdoor helpers` | `services/irrigation.py` | `$PIPE tests/server/test_contract_weather.py` | high | G+ | `get_current()` is called on the same paths. |
| T7.15 | `refactor(pipeline): extract function — _latest_soil, _monitor_target_band, _soil_status in monitor_cluster` | `services/irrigation.py` | `$PIPE` | med | G+ | Keep the strict own parse (do **not** use `moisture_target_range`). |
| T7.16 | `refactor(pipeline): extract function — _check_result builder in check_cluster` | `services/irrigation.py` | `$PIPE tests/server/test_alerts.py` | med | G+ | `detail_key: Literal["notes","needs_water"]`. Call order is unchanged. |
| T7.17 | `refactor(pipeline): extract method — _resolve_stale_check_alert and _record_check_failure in check_all_clusters` | `services/irrigation.py` | `$PIPE $SCHED` | high | G+ | Call `_record_check_failure` inside the `except` (so `logger.exception` sees the exception). `self.check_cluster` is still called through `self`. |
| T7.18 | `refactor(pipeline): extract function — _stop_auto_cycle and _left_running_message in handle_watcher_interrupted` | `services/irrigation.py` | `$SCHED tests/server/test_pump_watcher.py` | high | G+ | Log text byte-identical. |
| T7.19 | `refactor(pipeline): extract function — _watcher_tuning in schedule_pump_watcher` | `services/irrigation.py` | `$SCHED tests/server/test_pump_watcher.py` | high | G+ | The closure stays. Lazy scheduler imports stay at call time. `PumpWatcherService` is resolved via `services.pump_watcher`. |
| T7.∑ | WP gate | — | full suite `uv run pytest -q -n 4` | — | WP | Two reviewers sign off on the §12 checklist. |

### WP5 — Route/web glue DRY + app factory

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T5.1 | `refactor(web): consolidate duplicate — web/weekdays.py, adopted in web/routes/clusters` | `web/weekdays.py` (new), `web/routes/clusters.py` | `$WEB tests/server/test_web_cluster_pages.py tests/server/test_web_windows.py` | low | G | Target §7.2. Delete the private copies (no aliases; tests don't reference them). |
| T5.2 | `refactor(web): consolidate duplicate — weekday vocabulary in web/routes/configs` | `web/routes/configs.py` | `$WEB tests/server/test_web_config_pages.py` | low | G | |
| T5.3 | `refactor(web): consolidate duplicate — weekday vocabulary in web/routes/windows` | `web/routes/windows.py` | `$WEB tests/server/test_web_windows.py tests/server/test_web_redirects.py` | low | G | `_parse_weekday_mask` and the error messages are untouched. |
| T5.4 | `refactor(api): replace literal — WINDOW_HOUR_MAX / FULL_WEEKDAY_MASK in API and web window validation` | `routes/windows.py`, `web/routes/windows.py` | `$API $WEB tests/server/test_irrigation_windows.py tests/server/test_web_windows.py` | low | G | Messages stay literal (`"…0..23"`, `"…1..127 (Mon=1, Sun=64)"`). |
| T5.5 | `refactor(services): consolidate duplicate — cluster_events_csv, adopted by routes/operations.stats_export` | `services/cluster.py`, `routes/operations.py` | `$API tests/server/test_operations.py tests/server/test_clusters.py` | low | G | The route keeps `require_cluster` and its own `StreamingResponse`. The lazy imports in the route go away. |
| T5.6 | `refactor(web): consolidate duplicate — cluster_events_csv in web analytics export` | `web/routes/analytics.py` | `$WEB tests/server/test_web_analytics.py` | low | G | Remove now-unused `csv` / `io` / `format_timestamp` imports (the module is not a frozen surface). Route position is unchanged (routes.json). |
| T5.7 | `refactor(services): consolidate duplicate — ClusterService.sync_plants + PlantNotFoundError, adopted by routes/plants` | `services/cluster.py`, `routes/plants.py` | `$API tests/server/test_plants.py tests/cli/test_tui.py` | med | G | Target §7.2: verbatim scan, truthiness kept, commit stays in the route. |
| T5.8 | `refactor(web): consolidate duplicate — ClusterService.sync_plants in web plants sync` | `web/routes/operations.py` | `$WEB tests/server/test_web_operations.py` | med | G | Form parsing stays in the route. |
| T5.9 | `refactor(api): extract function — response mappers in routes/operations.cluster_status` | `routes/operations.py` | `$API tests/server/test_clusters.py tests/server/test_operations.py` | low | G | Name/docstring/`response_model` untouched. |
| T5.10 | `refactor(web): extract function — _rationale_reasons and _window_rows in cluster_detail` | `web/routes/clusters.py` | `$WEB tests/server/test_web_cluster_pages.py tests/server/test_web_decision_rationale.py tests/server/test_web_windows.py` | low | G | **Do not** touch the quiet flag (I2 does that). |
| T5.11 | `refactor(web): introduce parameter object — _plant_form_fields for plant create/update` | `web/routes/plants.py` | `$WEB tests/server/test_web_plant_pages.py tests/server/test_web_crud_actions.py` | low | G | Keys in the current kwarg order. The `Form(...)` signatures are untouched. |
| T5.12 | `refactor(api): consolidate duplicate — repo.get_plant instead of session.get(Plant, …)` | `routes/charts.py`, `web/routes/plant_dashboard.py` | `$API $WEB tests/server/test_charts_plant_health_timeline.py tests/server/test_web_plant_hero.py tests/server/test_web_plant_pages.py` | low | G | |
| T5.13 | `refactor(api): consolidate duplicate — repo.get_vacation_window in routes/vacation` | `routes/vacation.py` | `$API tests/server/test_vacation.py` | low | G | **Rebase onto the WP3 merge first** (needs T3.7). |
| T5.14a–h | `refactor(api|web): consolidate duplicate — deps.require_cluster for exact-form 404 lookups in <module>` | one module per commit: `routes/{alerts,charts,clusters,insights,operations}.py`, `web/routes/{analytics,clusters}.py` | that module's `D(...)` + `$API` or `$WEB` | low | G | **Optional, last.** Only `x = repo.get_cluster(id)` + `if not x: raise HTTPException(404, "Cluster not found")` (same status, detail, no headers). Skip any other shape. |
| T5.15 | `refactor(app): extract function — decompose create_app into ordered setup steps` | `app.py` | `$API $WEB tests/server/test_auth.py tests/server/test_contract_scheduler_registry.py tests/server/test_scheduler.py tests/server/test_scheduler_jobs.py tests/server/test_system_health.py $CORE` | med | G+ | Target §3.7. Router tuple order = today's include order. Every top-level import stays. `_mount_mcp` comes last. |
| T5.∑ | WP gate | — | union + `$CORE` + `t $(D gs.routes.clusters)` | — | WP | |

### WP1 — CLI

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T1.1 | `refactor(cli): consolidate duplicate — resolve_server_url(ctx)` | `commands/_helpers.py`, `commands/auth.py`, `commands/tui.py` | `$CLI tests/cli/test_tui.py` | low | G | Target §3.11. The `IrrigationClient.__init__` default is untouched. `commands/tui.py` keeps the lazy TUI import. |
| T1.2 | `refactor(cli): consolidate duplicate — _drop_none(fields) in client` | `client.py` | `$CLI $TUI` | low | G | Only the `v is not None` comprehensions (9 + `filters`). |
| T1.3 | `refactor(cli): add type annotations — JSONObject + _object/_array typed wrappers over _request` | `client.py` | `$CLI $TUI` | low | G | The wrappers call `self._request` (the instance replacement in `tui_fixtures` must still intercept). Report the strict-mypy status for I1. |
| T1.4 | `refactor(cli): add docstrings — undocumented IrrigationClient methods` | `client.py` | `$CLI` | low | G | They never reach `--help`. |
| T1.∑ | WP gate | — | `$CLI $TUI $(D gcli.client)` | — | WP | |

### WP2 — TUI

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T2.1 | `refactor(tui): move function — tui/render.py with plant_rows/sensor_rows, used by ClusterScreen` | `tui/render.py` (new), `tui/screens/cluster.py` | `$TUI` | med | G | Target §8. No textual import, no public UPPERCASE assignments. The `_plants_loaded` worker trigger stays in the screen. |
| T2.2 | `refactor(tui): move function — decision/history/config/window row builders` | `render.py`, `cluster.py` | `$TUI` | med | G | Same history sort key/reverse. |
| T2.3 | `refactor(tui): move function — irrigator_info, decision_panel, forecast_rows (+ _next_water)` | `render.py`, `cluster.py` | `$TUI` | med | G | |
| T2.4 | `refactor(tui): move function — insights/stats/efficacy/learn builders` | `render.py`, `cluster.py` | `$TUI` | med | G | Keep the `asyncio.gather` order. |
| T2.5 | `refactor(tui): replace conditional with dispatch — per-tab handlers for action_new` | `cluster.py` | `$TUI` (`-k "Crud or ExactRequests"` first) | med | G | Handler names must avoid the Textual prefixes (§8). The fallback `notify` text is verbatim. |
| T2.6 | `refactor(tui): replace conditional with dispatch — per-tab handlers for action_edit` | `cluster.py` | `$TUI` | med | G | |
| T2.7 | `refactor(tui): replace conditional with dispatch — per-tab handlers for action_delete` | `cluster.py` | `$TUI` | med | G | `_detach_irrigator` (not `_delete_cluster`, which already exists). |
| T2.8 | `refactor(tui): move function — SystemScreen.load row builders` | `render.py`, `screens/system.py` | `$TUI` | med | G | |
| T2.9 | `refactor(tui): move function — SettingsScreen.load row builders` | `render.py`, `screens/settings.py` | `$TUI` | med | G | `_selected_vacation` is untouched. |
| T2.10 | `refactor(tui): extract function — _band and _plant_views in model.summarize` | `tui/model.py` | `$TUI` | low | G | |
| T2.11 | `refactor(tui): extract method — MetricChart _draw_event_lines / _set_x_ticks` | `tui/widgets.py` | `$TUI` | med | G | Not handler-prefixed. `SERIES_COLORS[i % len(SERIES_COLORS)]` gives the same value. |
| T2.∑ | WP gate | — | `$TUI $CLI` | — | WP | |

### WP6 — Core logic + learning

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T6.1 | `refactor(core): replace literal — utils imports NIGHT_LUX_THRESHOLD and the seasonal month table from constants` | `utils.py` | `$CORE tests/test_utils.py tests/test_learning.py` | low | G | `utils.NIGHT_LUX_THRESHOLD` must stay importable; `_SEASONAL_LIGHT_FACTOR = SEASONAL_LIGHT_FACTOR_BY_MONTH`. |
| T6.2 | `refactor(logic): replace literal — named constants in sensors, trends, stress, fallback` | the 4 files | `$ENGINE tests/test_cleaning.py` | med | G | `> 15` → `> NIGHT_LUX_THRESHOLD` (same operator). |
| T6.3 | `refactor(learning): replace literal — named constants in learning/issues` | `issues.py` | `tests/test_learning.py $ENGINE tests/server/test_insights.py tests/server/test_alerts.py` | med | G | |
| T6.4 | `refactor(logic): consolidate duplicate — _mean_or_none in get_recent_sensor_data` | `sensors.py` | `$ENGINE tests/test_cleaning.py tests/server/test_sync_snapshot.py` | med | G | |
| T6.5 | `refactor(logic): extract function — functional core for analyze_historical_trends` | `trends.py` | `$ENGINE` | med | G | Target §3.5. B-18 truthiness kept. `delta` is assigned before the label. |
| T6.6 | `refactor(logic): extract function — pure stress detectors in detect_stress_conditions` | `stress.py` | `$ENGINE` | med-high | G+ (C gc.logic.stress) | No direct tests: the grid and properties are the guard. **Request a mutation pass** on `stress.py` before merging. Assign only non-None fields. |
| T6.7 | `refactor(logic): extract function — _config_fallback and _temperature_interval in temperature_based_decision` | `fallback.py` | `$ENGINE` | med | G | B-24 is preserved. |
| T6.8 | `refactor(learning): extract function — per-check helpers in detect_conflicts` | `issues.py` | `tests/test_learning.py $ENGINE tests/server/test_insights.py tests/server/test_alerts.py` | med | G | Target §3.4. Keep the early `return []` quirk and the two separate passes. |
| T6.9 | `refactor(learning): extract function — per-sensor alert helpers in detect_issues` | `issues.py` | same as T6.8 | med | G | Module-global `seasonal_light_factor` lookup (patched by tests). |
| T6.∑ | WP gate | — | `$ENGINE tests/test_learning.py tests/test_cleaning.py tests/test_utils.py $CORE` + `t $(C gc.logic.trends)` | — | WP | |

### WP8 — Engine + devices (HIGH RISK; merges last; every engine task G+ with `C(gc.logic.engine)`)

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T8.1 | `refactor(engine): replace literal — named constants and moisture_target_range in the engine` | `engine.py` | `$ENGINE $CORE` | med | G+ | `"45-65"` → `DEFAULT_SOIL_MOISTURE_TARGET`, via `moisture_target_range` at L684. |
| T8.2 | `refactor(engine): extract method — _tz_name for the three preference lookups` | `engine.py` | `$ENGINE` | med | G+ | Not cached; same 3 call points. |
| T8.3 | `refactor(engine): extract method — _record replaces four persist-and-return blocks` | `engine.py` | `$ENGINE` | med | G+ | |
| T8.4 | `refactor(engine): extract method — _finalize closure becomes _finish(override_window=…)` | `engine.py` | `$ENGINE` | high | G+ | Override reason iff `override_window is not None`, with `override = quiet_window if bypass_quiet_hours else None`. |
| T8.5 | `refactor(engine): extract method — _pre_gates (no-plants → leak hold → cooldown) and _quiet_hours_skip` | `engine.py` | `$ENGINE` | high | G+ | Later gates are not evaluated once one fires. |
| T8.6 | `refactor(engine): introduce parameter object — _EngineInputs, _gather_inputs, _fallback_decision` | `engine.py` | `$ENGINE` | high | G+ | Call order per target §3.2. `get_sensors_in_cluster` stays after the gather. |
| T8.7 | `refactor(engine): extract method — _evaluate_rules, _base_decision, _apply_adjustments; drop unused _apply_window_rule params` | `engine.py` | `$ENGINE` | high | G+ | Pass `cluster_id` explicitly. `or` replaces the two terminal `if`s. |
| T8.8 | `refactor(engine): remove dead parameter — _decision_with_reason never receives snapshot/stress/trends` | `engine.py` | `$ENGINE` | med | G+ | All 6 call sites are in `engine.py`. |
| T8.9 | `refactor(engine): extract function — soil-moisture rule helpers` | `engine.py` | `$ENGINE` | high | G+ | Target §3.3; no `set_dosage`. |
| T8.10 | `refactor(engine): extract function — _vacation_days_left, _binding_max_minutes, _seasonal_overrides` | `engine.py` | `$ENGINE` | high | G+ | Guard order in `_apply_vacation_budget` is kept. `season_for` stays a module-global lookup. |
| T8.11 | `refactor(logic): move function — active_quiet_window into logic/timing; engine delegates` | `timing.py`, `engine.py` | `$ENGINE tests/test_timing.py D(gc.logic.timing)` | high | G+ | Verbatim body. The engine reads config before prefs. |
| T8.12 | `refactor(engine): add type annotations — RainForecast Protocol for weather_client` | `engine.py` | `$ENGINE $CORE` | low | G+ | Annotation only. Not `runtime_checkable`. |
| T8.13 | `refactor(logic): move function — pure decision rules from engine.py to logic/rules.py (names unchanged)` | `engine.py`, `rules.py` (new) | `$ENGINE $CORE` | high | G+ | Move only. `engine.py` keeps `from greenhouse_core.utils import seasonal_light_factor  # noqa: F401 — test-pinned attribute path`. `rules.py` imports `seasonal_light_factor` by name. No logging in `rules.py`. |
| T8.14 | `refactor(logic): rename — public names in logic/rules.py (apply_*_rule, one_reason_decision)` | `rules.py`, `engine.py` | `$ENGINE $CORE` | low | G+ | Rename only. |
| T8.15 | `refactor(devices): add docstrings — correct stale dp_parsers docstrings` | `devices/sensors/tuya_generic.py`, `devices/profile.py`, `devices/sensors/tr301z.py` | `$DEV` | low | G | Docstrings only. No code token changes (`git diff -w` shows only string/comment lines). |
| T8.∑ | WP gate | — | full suite `uv run pytest -q -n 4` + `TZ=America/New_York uv run pytest -q -n 4 $ENGINE` | — | WP | Two reviewers. Mutation pass on `rules.py` + `engine.py` (orchestrator). |

### INT — Integrator tasks (serial, on the integration branch)

| ID | Title | Files | Tests | When |
|---|---|---|---|---|
| I1 | `build(lint): ratchet — drop cleared per-file ignores, extend mypy strict list, register new modules in contracts` | `pyproject.toml` | `uv run ruff check libs/ tests/`, `make typecheck`, `uv run lint-imports` | after every WP merge. Adds `greenhouse_cli.tui.render` (after WP2) and `greenhouse_core.logic.rules` (after WP8) to their §11 contracts. Ignores and the strict list only ever move in the ratchet direction. |
| I2 | `refactor(web): consolidate duplicate — cluster_detail quiet flag via active_quiet_window` | `web/routes/clusters.py` | `$WEB tests/server/test_web_cluster_pages.py tests/server/test_web_irrigator_actions.py` | after WP5 and WP8 are merged. `is_within_quiet_hours` always returns a real `bool` (verified). `prefs = repo.get_preferences()` stays first. |
| I3 | `refactor(services): add type annotations — ForecastService weather_client: RainForecast \| None` | `services/forecast.py` | `D(gs.services.forecast)` | after WP4 and WP8 are merged. |
| I4 | `docs(refactor): update REFACTOR_NOTES` | `REFACTOR_NOTES.md` | — | Continuous. Record: the plugin `LOGIC.md` needs no change (values unchanged); the B-6 comment rewrite; the `engine.seasonal_light_factor` keep-alive import; the deferred repository split (target §5). |
| I5 | Final gate | — | full suite twice (`-n 4` and serial), `TZ=America/New_York` run, `lint-imports`, `make typecheck`, `ruff check`, radon/xenon vs `refactor/baseline/` (report only) | after the last merge. |

## 3. Per-WP hand-off note (what every implementer returns)

1. Commits (hash + subject), each green under its gate, rebased on the integration branch.
2. The exact test commands and results of the WP gate.
3. Ratchet requests for I1: files now clean for C90/PLR09xx, and modules now clean under strict mypy.
4. Anything that contradicted the target doc, any stopped task, any bug site touched (with its B-number), and any
   coverage gap found (task id + lines).

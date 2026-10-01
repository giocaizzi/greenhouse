# 20 — Execution plan (Revision 2)

This plan is executable. Each task is **one small behavior-preserving commit**. The design behind each task (signatures,
order guarantees, why) is in `refactor/20-target-architecture.md`; task rows cite the section (§n). Read that section
before you start a task. Do not re-derive the design. If the code contradicts the section, **stop and report** rather
than improvise.

**Revision 1** applies the Gate-2 critique (`refactor/50-adversary-plan-critic.md`) and the orchestrator's binding
decisions. **Revision 2** applies the round-2 critique ("Round 2" section of the same file). §5 lists every change,
and target §13 "Revision 1" / "Revision 2" give the design side.

## 0. Ground rules for implementers

### 0.1 Prerequisites (orchestrator)

- **P0.** The Phase-1 safety net is committed (Gate 1 evidence `6994bc0`: 2597 passed twice, 5m48s at `-n 4`). The
  orchestrator tags `refactor-gate1`. WP0 branches from that tag.
- **P1.** WP0 is implemented serially on the integration branch `claude/focused-hawking-7to7o3` and merged first.
  Every other WP branches from the WP0 head (or the integration head at the start of its wave).

### 0.2 Concurrency, worktrees and the test lock (M5)

- **At most 2 implementer worktrees are active at once.** The two must have disjoint files, and they are **never both
  high-risk**. The waves are fixed in §1.
- **Every test command runs under one machine-wide lock, with 2 xdist workers and a pinned hash seed** *(Rev 2)*:
  `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n "${GH_XDIST:-2}" …`. With 4 cores and 2
  worktrees, runs queue instead of overlapping, and set-iteration order is reproducible. Use the `t` / `FULL` helpers
  from §0.3; they already do this.
- **Second seed.** WP gates and the integration gate add **one extra full run with `PYTHONHASHSEED=12345`**
  (`FULL_SEED2`). This catches output that depends on hash order.
- **Red-test policy** *(Rev 2; replaces the Rev 1 rerun rule)*.
  1. Re-run the **identical command**: same subset, same `-n`, same seed. Never re-run the failing tests in
     isolation.
  2. Red again → revert (`git reset --hard HEAD`) and report.
  3. Green on the re-run → you may call it **flaky** only if:
     - (a) the test is on the flaky list frozen at Gate 1, `refactor/gate1/flaky-tests.txt`. That list is
       orchestrator-owned, **initially empty**, and seeded only with evidence; or
     - (b) the same failure reproduces on the **parent commit** with the identical command (check out the parent in
       the same worktree, run, return).
  4. Otherwise treat it as a regression and revert.
  5. Never fix forward. Never edit a test or golden.

```bash
git worktree add ../gh-wp4 -b refactor/wp4-services <wave-base>     # one worktree per WP
cd ../gh-wp4 && uv sync                                           # per-worktree venv
# … one commit per task, in table order …
git fetch && git rebase <integration-branch>                      # before hand-back; re-run the WP gate
```

**File ownership:**
- After WP0, only the integrator edits `pyproject.toml`, `uv.lock`, `Makefile` and `REFACTOR_NOTES.md`. Implementers
  never edit those, `tests/golden/**` or any existing test.
- `refactor/mypy-strict.txt` is **shared and append-only**; every task may append to it. Resolve rebase conflicts with
  **union + sort**.
- *(Rev 2)* `refactor/size-exceptions.txt` is **integrator-only**. Implementers **propose** entries in the hand-off
  note (`path::qualname — reason`). The integrator commits approved entries as
  `… — reason — approved: <orchestrator>` before merging the WP. I1 rejects unapproved lines.
- Each WP owns a disjoint set of production files (§1). Touch nothing outside it.

**Commit message format:**
```
refactor(<area>): <technique> — <what>

Behavior: none. <why equivalent, e.g. "same call order: A → B → C">
Evidence: <exact t … commands, results>; lint-imports OK; mypy <files> OK; tests/golden unchanged.
DoD: <each decomposed function: body lines / CC / nesting> | proposed exceptions: <path::qualname — reason> | none
Bugs touched (preserved): B-n … | none
```
End it with the attribution lines from the session's system reminder.
- `<area>` ∈ {build, lint, test, types, core, schemas, repo, logic, learning, engine, devices, services, scheduler,
  pipeline, api, web, app, cli, tui}.
- `<technique>` ∈ {extract function, extract method, introduce constant, introduce parameter object, introduce typed
  result, replace literal, move function, consolidate duplicate, decompose conditional, replace conditional with
  dispatch, add type annotations, add docstrings, remove dead parameter}.
- One concern per commit: moves, extractions, constant adoption, annotations and formatting are never mixed.

### 0.3 Test aliases and the subset rule (M3)

```bash
CORE="tests/test_contract_imports.py tests/test_contract_constants.py tests/test_contract_schema.py tests/test_contract_packaging.py"
API="tests/server/test_mcp.py tests/server/test_contract_openapi.py tests/server/test_contract_mcp.py tests/server/test_contract_settings.py tests/server/test_contract_auth.py"
WEB="tests/server/test_contract_web_html.py tests/server/test_contract_web_mutations.py tests/server/test_contract_web_errors.py"
SETTINGS="tests/server/test_contract_settings.py"
CLI="tests/cli/test_contract_help.py tests/cli/test_contract_json_output.py tests/cli/test_completeness.py tests/cli/test_cli.py $SETTINGS"
TUI="tests/cli/test_contract_tui.py tests/cli/test_contract_tui_screens.py tests/cli/test_contract_tui_actuation.py tests/cli/test_contract_tui_runtime.py tests/cli/test_tui.py"
RENDER="$WEB tests/cli/test_contract_tui_screens.py"                                   # strict goldens that render server data
CHECK="tests/server/test_contract_pipeline.py tests/server/test_contract_check_all.py"   # pipeline / check paths
ENGINE="tests/test_contract_decision_grid.py tests/test_invariants_engine.py tests/test_properties_logic.py tests/test_logic.py tests/test_engine_timing.py tests/test_leak_hold.py tests/test_vacation_rationing.py tests/test_timing.py $CHECK"
SCHED="tests/server/test_contract_scheduler.py tests/server/test_contract_scheduler_registry.py tests/server/test_contract_check_all.py tests/server/test_contract_pump_watcher.py tests/server/test_scheduler.py tests/server/test_scheduler_jobs.py tests/server/test_scheduler_pause.py tests/server/test_scheduler_settings.py tests/server/test_scheduler_shutdown.py tests/server/test_leak_rearm.py tests/test_migrations.py"
PIPE="$CHECK tests/server/test_operations.py tests/server/test_decisions.py tests/server/test_irrigators.py tests/server/test_notify.py tests/server/test_health_monitor.py tests/server/test_rate_limit.py tests/server/test_leak_rearm.py tests/server/test_web_operations.py tests/server/test_web_decision_rationale.py tests/server/test_web_dashboard.py tests/test_migrations.py tests/cli/test_contract_tui_actuation.py"
DEV="tests/devices/ tests/test_cloud.py tests/server/test_contract_health_monitor.py tests/server/test_contract_sync_service.py tests/test_contract_sync.py tests/server/test_contract_raw_vs_clean.py"
# D gc.logic.trends → direct map;  C gc.logic.engine → conservative map   (gc.=greenhouse_core. gs.=greenhouse_server. gcli.=greenhouse_cli.)
tmap() { python3 - "$@" <<'EOF'
import json, sys
kind, mod = sys.argv[1], sys.argv[2]
mod = mod.replace("gcli.", "greenhouse_cli.", 1).replace("gc.", "greenhouse_core.", 1).replace("gs.", "greenhouse_server.", 1)
f = {"D": "refactor/baseline/module-tests-map-direct.json", "C": "refactor/baseline/module-tests-map.json"}[kind]
print(" ".join(json.load(open(f))[mod]))
EOF
}
D() { tmap D "$1"; }; C() { tmap C "$1"; }
t() { PYTHONHASHSEED="${GH_SEED:-0}" flock /tmp/greenhouse-tests.lock uv run pytest -q -n "${GH_XDIST:-2}" $(echo "$@" | tr ' ' '\n' | sort -u); }
FULL() { PYTHONHASHSEED="${GH_SEED:-0}" flock /tmp/greenhouse-tests.lock uv run pytest -q -n "${GH_XDIST:-2}"; }
FULL_SEED2() { GH_SEED=12345 FULL; }                                                   # WP + integration gates only
# example: t $ENGINE $RENDER $(D gc.logic.trends)
```

In the task tables, `D(x)` / `C(x)` mean `$(D x)` / `$(C x)`, and `$ALIAS` means the variable above.

**Subset rule.** It applies to every task; the tables already follow it. The reviewer re-checks it.

A task's subset is:
- `D(m)` for **every** touched module `m` (for a new module, `D` of the module it was extracted from), plus
- **every strict golden that renders or pins the touched code**:
  - code reachable from a web page, fragment or mutation → `$WEB`;
  - code reachable from a TUI screen through the API → `tests/cli/test_contract_tui_screens.py` (TUI modules →
    `$TUI`);
  - code on the irrigate/check/monitor path, or anything the engine consumes → `$CHECK`;
  - API route modules and `app.py` → `$API`;
  - any file containing `os.environ` / `getenv` (today `utils.py`, `client.py`, `plant_db.py`, `devices/gateway.py`,
    the CLI commands) → `$SETTINGS`;
  - any frozen import-surface module (`models`, `schemas`, `repository`, `constants`, `utils`, `plant_db`, `logic`,
    `devices`, `learning`, `sync`, `stats`, `database`, `auth`, `app`, `config`, `deps`, `scheduler`) → `$CORE`.

**Approximate cost at `-n 2`.** These are estimates scaled from Gate 1 (full suite 5m48s at `-n 4`); T0.11 measures
the real numbers.

| Alias | Time |
|---|---|
| full suite / conservative maps | ≈ 11–12 min / 9–11 min |
| `D(gc.repository)`, `D(gc.schemas)` | ≈ 9 min |
| `$WEB` | ≈ 3 min |
| `$TUI` | ≈ 3 min |
| `$PIPE` | ≈ 3 min |
| `$SCHED` | ≈ 2 min |
| `$ENGINE` | ≈ 2 min |
| `$API` | ≈ 1 min |
| `$CORE` | ≈ 1 min |
| `$DEV` | ≈ 1 min |

### 0.4 Gates and Definition of Done (M4)

- **G0 (WP0 tooling tasks):**
  1. The command in the task's Tests column passes.
  2. `t $CORE` is green.
  3. `git status --porcelain tests/golden` prints nothing.
  4. `FULL` once at the end of WP0.
- **G (every task).** Typical test time is 5–12 min.
  1. **Before editing:** coverage precondition.
     `t <subset> --cov=<module> --cov-branch --cov-report=term-missing`. If a line or branch you are about to
     restructure is reported missing, **stop** and request a characterization test (orchestrator).
  2. `uv run ruff check <touched files> && uv run ruff format --check <touched files>`
  3. `uv run lint-imports`
  4. **Strict types are required:** `uv run mypy <every touched .py file>` is clean. The touched modules are listed in
     `refactor/mypy-strict.txt` (they get there through the WP's "add type annotations" task, which runs first), and
     `make typecheck` is green.
  5. `t <task subset>` is green.
  6. `git status --porcelain tests/golden` prints nothing.
  7. **Task DoD:** every function the task decomposes, and every helper it creates, has ≤ 40 body lines and nesting
     ≤ 3 (`uv run python refactor/scripts/sizecheck.py <files>` does not list them) and **CC ≤ 8**
     (`uv run ruff check --isolated --select C90 --config "lint.mccabe.max-complexity=8" <files>` does not list them).
     Otherwise the commit message's DoD line states the reason, and the entry is **proposed** in the hand-off. The
     integrator commits it only after orchestrator approval (Rev 2).
- **G+ (high-risk: engine, scheduler, `services/irrigation.py`, `services/leak.py`, pump-watcher trip/watch, devices,
  `core/sync.py`, `app.py`/auth wiring, `logic/stress.py`).** Typical test time is 20–25 min.
  1. G, plus the conservative map `t $(C <module>)` once at the end of the task. Use `FULL` for T5.15 and for any
     module without a C entry.
  2. Two reviewers, one of them an adversary who tries to construct an input that distinguishes old from new (target
     §12 checklist).
- **WP DoD + gate (before hand-back, after rebase).**
  1. `t` over the union of all task subsets in the WP, plus `$CORE`. WP7 and WP8 use `FULL` instead. *(Rev 2)* Plus
     **`FULL_SEED2`** for every WP.
  2. Every file the WP touched is clean under all three checks below, or has register entries:
     - `sizecheck` on the whole file;
     - ruff `C90/PLR0911/PLR0912/PLR0915` at max-complexity 8 with `--isolated`;
     - `mypy`.
  3. A mutation post-run (§0.5) for every module the WP touched that is on the mutation list.
  4. *(Rev 2)* The function map (old qualname → new helper qualnames) for every decomposed function goes in the
     hand-off. It is needed for the mutant-identity comparison.
- **Integration gate (integrator, after each merge).** `FULL`, `FULL_SEED2` (Rev 2), one more run with
  `TZ=America/New_York`,
  `uv run lint-imports`, `make typecheck`, `uv run ruff check libs/ tests/`, `make sizecheck`; then I1.

### 0.5 Mutation passes (m3)

- **Which modules.** `logic/{engine,stress,trends,sensors,fallback}.py`, `learning/issues.py`,
  `services/{irrigation,leak,pump_watcher}.py`, `scheduler.py` and *(Rev 2)* `greenhouse_core/sync.py`.
- **M-pre.** Before a WP's first commit on such a module, run `uv run mutmut run` on the **unmodified** module (config
  from T0.10). Use the WP's subsets, restricted to the "mutation targets" lines in `refactor/10-safety-*.md`.
  Survivors go to the orchestrator, who kills them with characterization tests or justifies them before the WP
  starts.
- **M-post.** In the WP gate. Pass condition: ≥ 75 % killed on touched code, and **no mutant killed in M-pre
  survives in M-post**.
  - *(Rev 2)* Mutants are matched by **identity**: (operator, original snippet → replacement snippet, enclosing
    function qualname), translated through the WP's function map. Line numbers are never used.
- **Budget and fallback.** ≤ 45 min per module per run, under the test lock with `--max-children 2`. Over budget, use
  a seeded sample of 150 mutants. All runs use `PYTHONHASHSEED=0`.
- **Fallback.** If T0.10 found mutmut unusable, use `refactor/scripts/mutate_probe.py`. *(Rev 2)* It mutates **in
  place in the implementer's own worktree** (the editable install points at `libs/`, so temp copies would never be
  imported). It restores each file with `git checkout -- <file>` in a `finally`, and refuses to run on a dirty target
  file.

## 1. Work packages and waves

| WP | Scope (phase) | Exclusive production files | Risk |
|---|---|---|---|
| **WP0** | Tooling + shared definitions (0, 1) | `pyproject.toml`, `uv.lock`, `Makefile`, `tests/test_refactor_guards.py` (new), `tests/test_moisture_target_range.py` (new), `refactor/scripts/{sizecheck,mutate_probe}.py` (new), `refactor/{mypy-strict,size-exceptions}.txt` (new), `greenhouse_core/constants.py`, `greenhouse_core/logic/plant_needs.py` | low |
| **WP3** | Data types + repository (1, 2) | `greenhouse_core/schemas.py`, `greenhouse_core/repository.py` | med |
| **WP4** | Server services (3) | `greenhouse_server/services/{health,forecast,maintenance,data_quality,charts,insights,pump_watcher,health_monitor,search,anomaly,efficacy,system_health}.py` | med (T4.10/T4.12 G+) |
| **WP7** | Scheduler + pipeline + leak (3) | `greenhouse_server/scheduler.py`, `greenhouse_server/services/{irrigation,leak}.py`, `greenhouse_server/config.py` | **high** |
| **WP5** | Route/web glue + app factory (4) | `greenhouse_server/services/cluster.py`, `greenhouse_server/app.py`, `greenhouse_server/routes/*.py`, `greenhouse_server/web/weekdays.py` (new), `greenhouse_server/web/context.py`, `greenhouse_server/web/routes/*.py` | med (T5.15 G+) |
| **WP1** | CLI client (5) | `greenhouse_cli/client.py` | low |
| **WP2** | TUI (6) | `greenhouse_cli/tui/render.py` (new), `greenhouse_cli/tui/screens/{cluster,system,settings}.py`, `greenhouse_cli/tui/model.py`, `greenhouse_cli/tui/widgets.py` | med |
| **WP6** | Core logic + learning + stats (7a) | `greenhouse_core/{utils,stats}.py`, `greenhouse_core/logic/{sensors,trends,stress,fallback}.py`, `greenhouse_core/learning/{issues,profiling}.py` | med (T6.6 G+) |
| **WP8** | Engine + sync + devices (7b, last) | `greenhouse_core/logic/{engine,timing}.py`, `greenhouse_core/sync.py`, `greenhouse_core/devices/{profile.py,sensors/tuya_generic.py,sensors/tr301z.py}` | **high** |
| **INT** | Integrator | `pyproject.toml`, `Makefile`, `uv.lock`, `REFACTOR_NOTES.md`; I2 → `web/routes/clusters.py`, I3 → `services/forecast.py` (both post-merge) | — |

**Waves (≤ 2 concurrent worktrees, disjoint, never both high-risk; merge order = required phase order):**

| Wave | Parallel WPs | Merge order at wave end | Rough duration (test time dominates) |
|---|---|---|---|
| 0 | WP0 (solo, serial) | WP0 | ≈ ½ day |
| A | WP3 (med) ∥ WP4 (med) | WP3 → WP4 | ≈ 1½ days |
| B | WP7 (**high**) ∥ WP5 (med) | WP7 → WP5 (T5.13 already has WP3) | ≈ 2½ days. *(Rev 2 nit)* T5.15 (auth wiring) gets **its own** two reviewers, separate from WP7's. |
| C | WP1 (low) ∥ WP2 (med) | WP1 → WP2 | ≈ 1½ days |
| D | WP6 (med) — solo, so that the engine-adjacent logic lands before the engine | WP6 | ≈ 1 day |
| E | WP8 (**high**) — solo, last | WP8 → then I2, I3, I5 | ≈ 2 days |

- **Dependencies:** P0 → WP0 → waves A…E. T3.7 (wave A) → T5.13 (wave B). WP5 + WP8 → I2. WP4 + WP8 → I3. Every
  merge → I1. All → I5.
- **Reviewers.** Book two reviewers for waves B and E before they start.

## 2. Tasks

Columns: **ID · Title (commit subject) · Files · Tests · Risk · Gate · Notes.** Within a WP, tasks are serial, in table
order. "Strict" = the task appends the listed modules to `refactor/mypy-strict.txt`. ~~Struck~~ IDs were dropped in
Rev 1 and are kept for traceability.

### WP0 — Tooling and shared definitions (wave 0, serial, integration branch)

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T0.1 | `build(dev): add tooling — mypy, import-linter and mutmut to the dev group` | `pyproject.toml`, `uv.lock` (dev group only) | `$CORE` | low | G0 | `uv add --group dev mypy import-linter mutmut`. The packaging golden stays green. |
| T0.2 | `build(lint): add import-linter contracts — encode today's layering` | `pyproject.toml` | `uv run lint-imports` | low | G0 | Target §11 **as written, without** `greenhouse_cli.tui.render` (I1 adds it). These are the 10 contracts the critic verified pass. Never change code here. |
| T0.3 | `build(lint): add ruff complexity ratchet — C90 at 8, PLR0911/12/15, per-file ignores, tests exempt` | `pyproject.toml` | `uv run ruff check libs/ tests/` | low | G0 | `max-complexity = 8`. Per-file ignores = today's `libs/` offenders at 8 (the 27 C901 hits in target §3.12, plus the PLR hits in `refactor/baseline/ruff-complexity-rules.txt`), plus `"tests/**" = ["C90","PLR0911","PLR0912","PLR0915"]` (M2). The repo must be clean afterwards. |
| T0.4 | `build(types): add mypy strict ratchet — project config, boundary profile, strict list file, make typecheck` | `pyproject.toml`, `Makefile`, `refactor/mypy-strict.txt` (new) | `make typecheck` | low | G0 | Target §10.13 and §11. Seed list = the 35 modules clean at baseline (verified by the critic): `greenhouse_cli/tui/{formatting,sprites,screens/activity,screens/dashboard,screens/search}.py`; `greenhouse_core/{auth,constants,models}.py`, `devices/registry.py`, `devices/sensors/tr301z.py`, `learning/{learner,report}.py`, `logic/{sensors,stress}.py`; `greenhouse_server/config.py`, `routes/{activity,alerts,auth,decisions,efficacy,forecast,health,insights,quality,well_known}.py`, `services/{anomaly,bulk,data_quality,efficacy,insights,search,system_health,vacation}.py`, `web/{router,templating}.py`. Boundary override modules: `greenhouse_server.routes.*`, `greenhouse_server.web.routes.*`, `greenhouse_cli.commands.*`, `greenhouse_cli.main`. `check:` gains `lint-imports`, `typecheck` and `sizecheck`. |
| T0.5 | `test: add refactor guard tests — CLI import without textual, no "from time import time", no new public TUI constants, no Textual shadowing, annotated boundary helpers` | `tests/test_refactor_guards.py` (new) | that file, run twice, plus `-n 2` | low | G | Target §11 guards 1–5. Guard 1 runs in a **subprocess**. The allow-lists for guards 4 and 5 are the baseline state, captured once and hard-coded (guard 5's list only shrinks). |
| T0.6 | `refactor(core): introduce constant — new named thresholds (definitions only)` | `greenhouse_core/constants.py` | `$CORE` | low | G | Target §9: each value **and** type copied from its literal; grep for collisions first. |
| T0.7 | `refactor(types): add type annotations — strict-clean logic/plant_needs` | `logic/plant_needs.py`, `refactor/mypy-strict.txt` | `$CORE $ENGINE $RENDER D(gc.logic.plant_needs)` | low | G | 9 strict errors at baseline. Annotations only. Strict: `plant_needs`. |
| T0.8 | `refactor(logic): extract function — moisture_target_range(care) (definition only)` | `logic/plant_needs.py`, `tests/test_moisture_target_range.py` (new) | new test + `$ENGINE` | low | G | `def moisture_target_range(care: Mapping[str, Any]) -> tuple[float, float]: return parse_moisture_target(care.get("soil_moisture_target", DEFAULT_SOIL_MOISTURE_TARGET))`. The test is a Hypothesis equivalence check against the inline expression (`derandomize=True`). |
| T0.9 | `build(lint): add size checker — refactor/scripts/sizecheck.py, size-exceptions register, make sizecheck` | `refactor/scripts/sizecheck.py` (new), `refactor/size-exceptions.txt` (new), `Makefile` | `make sizecheck` before and after seeding | low | G0 | Target §3.12 metrics (nesting: `elif` at the same depth; nested defs measured separately). Seed the register with every **EXC** row of target §3.12 (function and file rows, each with its reason and `approved: orchestrator (Gate 2)`). *(Rev 2)* **Assert in the commit body** that, after seeding, the set of functions `make sizecheck` lists equals the set of §3.12 rows whose verdict is a task. Any extra hit → stop and report. |
| T0.10 | `build(test): configure mutation testing — mutmut targets, budget, fallback probe` | `pyproject.toml` (`[tool.mutmut]`), `refactor/scripts/mutate_probe.py` (new) | M-pre run on `logic/stress.py` with `$ENGINE` | low | G0 | Targets include `sync.py` (Rev 2). Validate on `stress.py`: record runtime and kill rate in the commit body. If mutmut cannot run against the `libs/` layout, commit the probe as the official tool and say so. The probe works in place and restores via `git checkout -- <file>` (Rev 2). Both tools report mutant **identity** (target §11). |
| T0.11 | `docs(refactor): record measured alias timings` | `refactor/gate2/timings.md` (new) | each alias once under `t` | low | G0 | Replaces the §0.3 estimates; the integrator re-plans waves if they are off by more than 2×. |

### WP3 — Data types + repository (wave A)

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T3.0a | `refactor(types): add type annotations — strict-clean schemas` | `schemas.py` | `$CORE $API $RENDER D(gc.schemas)` | med | G | 8 errors. Validators get `(cls, v: Any) -> Any`. **Never** change a field annotation, `Field(...)` or a class docstring. A collision gets `# type: ignore[code]  # contract: OpenAPI` (target §10.14). Strict: `schemas`. |
| T3.0b | `refactor(types): add type annotations — strict-clean repository` | `repository.py` | `$CORE $RENDER $CHECK D(gc.repository)` | med | G | 16 errors. Public signatures keep their parameter names and defaults; annotations only. Strict: `repository`. |
| T3.1 | `refactor(schemas): consolidate duplicate — shared _parse_json_config for both parse_config validators` | `schemas.py` | `$CORE $API $RENDER tests/server/test_irrigators.py tests/server/test_sensors.py` | low | G | Target §5. |
| T3.2 | `refactor(repo): extract method — _delete_by_id for five delete methods` | `repository.py` | `$CORE $RENDER tests/test_db.py tests/cli/test_tui.py` + `tests/server/test_{clusters,sensors,irrigators,vacation,irrigation_windows,web_crud_actions}.py` | low | G | Target §3.9. `delete_plant` is untouched. |
| T3.3 | `refactor(repo): extract method — _patch_fields (None-first) for vacation and irrigation windows` | `repository.py` | `$CORE $RENDER tests/test_db.py tests/cli/test_tui.py` + `tests/server/test_{vacation,irrigation_windows,web_vacation_edit,web_windows}.py` | low | G | |
| T3.4 | `refactor(repo): extract method — _patch_fields(json_fields={"config"}) for irrigator and sensor updates` | `repository.py` | `$CORE $RENDER tests/test_db.py tests/cli/test_tui.py` + `tests/server/test_{irrigators,sensors,sensor_assignments,web_irrigator_capacity,web_sensor_pages}.py` | low | G | The `plant_id` pop and the reassignment stay in `update_sensor`. |
| T3.5 | `refactor(repo): extract method — _patch_fields_hasattr_first for preferences, cluster and plant` | `repository.py` | `$CORE $RENDER tests/test_db.py tests/cli/test_tui.py` + `tests/server/test_{preferences,clusters,plants,web_preferences}.py` | low | G | B-15 preserved. |
| T3.6 | `refactor(repo): extract method — _page cursor pagination for list_all_*` | `repository.py` | `$CORE $API $RENDER` + `tests/server/test_{sensors,irrigators,plants,system_health,anomaly,data_quality}.py` | low | G | |
| T3.7 | `refactor(repo): extract method — additive get_vacation_window(window_id)` | `repository.py` | `$CORE tests/test_db.py` | low | G | Unblocks T5.13. |
| T3.8 | `refactor(repo): replace literal — FULL_WEEKDAY_MASK as add_irrigation_window default` | `repository.py` | `$CORE $RENDER tests/server/test_irrigation_windows.py` | low | G | |
| T3.∑ | WP gate + DoD | — | `t $(D gc.repository) $(D gc.schemas) $CORE $API $RENDER $CHECK` | — | WP | Both files are register entries for > 400 lines (target §3.12). Every function stays within the DoD. |

### WP4 — Server services (wave A)

M-pre for `services/pump_watcher.py` before T4.10.

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T4.0 | `refactor(types): add type annotations — strict-clean health, forecast, maintenance, charts, pump_watcher, health_monitor` | those 6 files | `$RENDER $CHECK tests/server/test_contract_pump_watcher.py tests/server/test_contract_health_monitor.py` + `D(…)` for the 6 | med | G | 1 + 2 + 2 + 8 + 3 + 2 errors. `ForecastService.weather_client: Any = None` (I3 tightens it later; target §4). The other 6 WP4 modules are already strict. Strict: all 6. |
| T4.1 | `refactor(services): replace literal — named constants in maintenance, forecast, health, health_monitor, pump_watcher` | those 5 files | `$CORE $RENDER $CHECK tests/server/test_contract_pump_watcher.py tests/server/test_contract_health_monitor.py` + `D(…)` for the 5 | low | G | Target §9. `_WEATHER_PRECIP_THRESHOLD_MM` alias. `days: int = HEALTH_SCORE_WINDOW_DAYS`. |
| T4.2 | `refactor(services): consolidate duplicate — moisture_target_range in health and forecast` | `health.py`, `forecast.py` | `$RENDER D(gs.services.health) D(gs.services.forecast)` | low | G | |
| T4.3 | `refactor(services): extract method — decompose PlantHealthService.compute_score` | `health.py` | `$RENDER D(gs.services.health) tests/server/test_charts_plant_health_timeline.py` | med | G | Target §3.6. |
| T4.4 | `refactor(services): extract method — decompose ForecastService.predict_next_irrigation` | `forecast.py` | `$RENDER D(gs.services.forecast)` | med | G | Target §3.6. |
| T4.5 | `refactor(services): extract function — per-check helpers in collect_maintenance_alerts` | `maintenance.py` | `$RENDER $CHECK D(gs.services.maintenance)` | med | G | |
| T4.6 | `refactor(services): extract function — per-entity collectors in data_quality.build_report` | `data_quality.py` | `$RENDER D(gs.services.data_quality)` | med | G | |
| T4.7 | `refactor(services): consolidate duplicate — _aggregate_band in charts._threshold_for_cluster` | `charts.py` | `$RENDER D(gs.services.charts)` | low | G | |
| T4.8 | `refactor(services): consolidate duplicate — repo.get_plant instead of session.get(Plant, …) in charts` | `charts.py` | `$RENDER D(gs.services.charts)` | low | G | |
| T4.17 | `refactor(services): extract function — _bucket_readings / _overlay_datasets in build_overlay_payload` | `charts.py` | `$RENDER D(gs.services.charts) tests/server/test_charts_overlay.py` | med | G | New in Rev 1 (60 lines, CC 11). |
| T4.9 | `refactor(services): consolidate duplicate — _insight_from_alert in cluster_insights` | `insights.py` | `$RENDER D(gs.services.insights)` | low | G | Must end ≤ 40 body lines. |
| T4.10 | `refactor(services): introduce typed result — WatchOutcome, _outcome and _poll_step in PumpWatcherService.watch` | `pump_watcher.py` | `$SCHED $CHECK D(gs.services.pump_watcher)` | high | G+ (`C(gs.services.pump_watcher)`) | Target §3.6: clock and stop-check calls keep their positions and counts. |
| T4.11 | `refactor(services): extract method — _raise_if_not_open in backfill_from_history` | `health_monitor.py` | `$RENDER $CHECK D(gs.services.health_monitor) tests/server/test_contract_health_monitor.py` | med | G | The file stays > 400 lines: register entry. |
| T4.12 | `refactor(services): extract method — one method per best-effort step in PumpWatcherService._handle_trip` | `pump_watcher.py` | `$SCHED $CHECK D(gs.services.pump_watcher)` | high | G+ (`C(gs.services.pump_watcher)`) | New (105 lines). Target §3.6. Each step keeps its own `try/except` and log text. |
| T4.13 | `refactor(services): extract function — per-entity hit builders in search` | `search.py` | `$RENDER tests/server/test_search.py tests/cli/test_tui.py` | med | G | New (118 lines). SQL moved verbatim. |
| T4.14 | `refactor(services): extract method — _stale_alert / _drift_alert in SensorAnomalyService.scan` | `anomaly.py` | `$RENDER D(gs.services.anomaly) tests/server/test_contract_scheduler.py` | med | G | New (115 lines). The scheduler anomaly golden pins the output. |
| T4.15 | `refactor(services): extract function — _event_items in efficacy.score_cluster` | `efficacy.py` | `$RENDER D(gs.services.efficacy)` | med | G | New (63 lines). |
| T4.16 | `refactor(services): extract method — _sensor_devices / _overall_status in SystemHealthService.pulse` | `system_health.py` | `$RENDER D(gs.services.system_health)` | med | G | New (57 lines). |
| T4.∑ | WP gate + DoD + M-post (`pump_watcher.py`) | — | union of the above, plus `$CORE` | — | WP | |

### WP7 — Scheduler + pipeline + leak (wave B, HIGH RISK; every task G+)

M-pre for `scheduler.py`, `services/irrigation.py` and `services/leak.py` before the first commit.

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T7.0a | `refactor(types): add type annotations — strict-clean scheduler` | `scheduler.py` | `$SCHED $CORE` | med | G+ (`C(gs.scheduler)`) | 16 errors. Job function names and module-level names are unchanged (`func_ref` golden). Strict: `scheduler`. |
| T7.0b | `refactor(types): add type annotations — strict-clean services/irrigation and services/leak` | `services/irrigation.py`, `services/leak.py` | `$PIPE $SCHED $RENDER` | med | G+ (`C(gs.services.irrigation)`) | 10 + 3 errors. `_time` and the lazy imports are untouched. Strict: both. |
| T7.1 | `refactor(scheduler): replace literal — CHECK_ALL_JOB_ID for the "check_all" literals (declaration hoisted)` | `scheduler.py` | `$SCHED $CORE` | med | G+ | |
| T7.2 | `refactor(scheduler): replace literal — anomaly interval, health-snapshot time, sync backfill hours` | `scheduler.py` | `$SCHED $CORE` | med | G+ | |
| T7.3 | `refactor(scheduler): extract function — _job_session context manager, applied to _anomaly_job` | `scheduler.py` | `$SCHED` | high | G+ | Target §3.8. |
| T7.4 | `refactor(scheduler): extract function — _job_session in _health_snapshot_job` | `scheduler.py` | `$SCHED $RENDER` | high | G+ | |
| T7.5 | `refactor(scheduler): extract function — _job_session in _sync_job` | `scheduler.py` | `$SCHED tests/server/test_contract_sync_service.py` | high | G+ | |
| T7.6 | `refactor(scheduler): extract function — _job_session in _health_monitor_job` | `scheduler.py` | `$SCHED tests/server/test_health_monitor.py tests/server/test_contract_health_monitor.py` | high | G+ | |
| T7.7 | `refactor(scheduler): extract function — _job_session + _build_irrigation_service(app, …) in _check_job` | `scheduler.py` | `$SCHED $PIPE` | high | G+ | `app` is passed explicitly (Rev 1 nit). |
| T7.8 | `refactor(pipeline): replace literal — named constants in services/irrigation and Settings pump-watcher defaults` | `services/irrigation.py`, `config.py` | `$PIPE $SCHED $SETTINGS $CORE` | med | G+ | The Settings schema golden must be byte-identical. |
| T7.9 | `refactor(pipeline): introduce typed result — PipelineResult, MonitorResult, CheckResult (annotations only)` | `services/irrigation.py` | `$PIPE $RENDER` | low | G+ | |
| T7.10 | `refactor(pipeline): extract function — _error_result, _decision_result, _decide, _log_decision_skip` | `services/irrigation.py` | `$PIPE $RENDER tests/cli/test_tui.py` | high | G+ | Target §3.1. |
| T7.11 | `refactor(pipeline): extract method — _actuation_target and _with_error` | `services/irrigation.py` | `$PIPE $RENDER` | high | G+ | |
| ~~T7.12~~ | **Dropped (Rev 1, m5).** The health-gate block stays inline and verbatim. | — | — | — | — | It becomes a register entry: `run_irrigation_pipeline` ≈ 45 body lines (target §3.1, §3.12). |
| T7.13 | `refactor(pipeline): introduce parameter object — _Actuation + _actuate, _event_notes, _on_started, _notify_auto_irrigation, _on_start_failed` | `services/irrigation.py` | `$PIPE $RENDER tests/server/test_contract_pump_watcher.py` | high | G+ | `_time` seam; `started_at` comes after `start`. |
| T7.14 | `refactor(pipeline): extract method — split _resolve_temperature into indoor/outdoor helpers` | `services/irrigation.py` | `$PIPE $RENDER tests/server/test_contract_weather.py` | high | G+ | |
| T7.15 | `refactor(pipeline): extract function — _latest_soil, _monitor_target_band, _soil_status in monitor_cluster` | `services/irrigation.py` | `$PIPE $RENDER` | med | G+ | Keeps its own parse. |
| T7.16 | `refactor(pipeline): extract function — _check_result builder (cast with documented reason) in check_cluster` | `services/irrigation.py` | `$PIPE $RENDER tests/server/test_alerts.py` | med | G+ | `cast(CheckResult, …)`; the comment gives the reason (Rev 1 m1). |
| T7.17 | `refactor(pipeline): extract method — _resolve_stale_check_alert and _record_check_failure in check_all_clusters` | `services/irrigation.py` | `$PIPE $SCHED` | high | G+ | Called from inside the `except`. |
| T7.18 | `refactor(pipeline): extract function — _stop_auto_cycle and _left_running_message in handle_watcher_interrupted` | `services/irrigation.py` | `$SCHED tests/server/test_pump_watcher.py` | high | G+ | Must end ≤ 40 body lines. |
| T7.19 | `refactor(pipeline): extract function — _watcher_tuning and _run_pump_watcher behind a thin closure in schedule_pump_watcher` | `services/irrigation.py` | `$SCHED $PIPE tests/server/test_pump_watcher.py` | high | G+ | Target §3.1: schedule-time captures vs run-time reads. Both functions end ≤ 40 body lines. |
| T7.20 | `refactor(pipeline): extract method — per-sensor verdict + _record_hold in LeakDetectionService.check_after_irrigation` | `services/leak.py` | `$SCHED $CHECK $RENDER D(gs.services.leak) tests/test_leak_hold.py` | high | G+ (`C(gs.services.leak)`) | New. Invariant 11. `check_after_irrigation` stays a class attribute. |
| T7.21 | `refactor(pipeline): extract method — _moisture_series, _pinned_high, _still_rising in LeakDetectionService._evaluate_sensor` | `services/leak.py` | same as T7.20 | high | G+ | New. Rule order and messages are verbatim. |
| T7.22 | `refactor(pipeline): extract function — _rearm_from_events in rearm_leak_checks` | `services/irrigation.py` | `$SCHED $CHECK D(gs.services.irrigation)` | high | G+ (`C(gs.services.irrigation)`) | New in Rev 2 (nesting 4). Target §3.1: `now = int(_time.time())`, the `try`/`except`/`finally` and both log lines stay in `rearm_leak_checks`. |
| T7.∑ | WP gate + DoD + M-post (scheduler, irrigation, leak) | — | `FULL` + `FULL_SEED2` | — | WP | Two reviewers. `scheduler.py` and `irrigation.py` are > 400-line register entries. |

### WP5 — Route/web glue + app factory (wave B)

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T5.0a | `refactor(types): add type annotations — strict-clean services/cluster, app, web/context` | `services/cluster.py`, `app.py`, `web/context.py` | `$API $RENDER $CORE tests/server/test_contract_scheduler_registry.py D(gs.services.cluster)` | med | G | 4 + 5 + 3 errors. `app.main` keeps its signature (console-script entry point). Strict: the 3 files. |
| T5.0b | `refactor(types): add type annotations — routes/{plants,operations,vacation,charts,windows,alerts,clusters,insights} under the boundary profile` | those route files | `$API $RENDER $CHECK tests/cli/test_tui.py` | med | G | **No route signature is edited** (target §10.13). Only helpers and module code get annotations. Strict: these files. |
| T5.0c | `refactor(types): add type annotations — web/routes/{clusters,configs,windows,operations,analytics,plants,plant_dashboard} under the boundary profile` | those files | `$WEB` + `D(…)` for the 7 | med | G | No return annotations on web routes (they would become a `response_model`). Strict: these files. |
| T5.1 | `refactor(web): consolidate duplicate — web/weekdays.py, adopted in web/routes/clusters` | `web/weekdays.py` (new), `web/routes/clusters.py` | `$WEB tests/server/test_web_cluster_pages.py tests/server/test_web_windows.py` | low | G | Strict: `web/weekdays.py`. |
| T5.2 | `refactor(web): consolidate duplicate — weekday vocabulary in web/routes/configs` | `web/routes/configs.py` | `$WEB tests/server/test_web_config_pages.py` | low | G | |
| T5.3 | `refactor(web): consolidate duplicate — weekday vocabulary in web/routes/windows` | `web/routes/windows.py` | `$WEB tests/server/test_web_windows.py tests/server/test_web_redirects.py` | low | G | |
| T5.4 | `refactor(api): replace literal — WINDOW_HOUR_MAX / FULL_WEEKDAY_MASK in API and web window validation` | `routes/windows.py`, `web/routes/windows.py` | `$API $WEB tests/server/test_irrigation_windows.py tests/cli/test_tui.py` | low | G | The messages stay literal. |
| T5.5 | `refactor(services): consolidate duplicate — cluster_events_csv, adopted by routes/operations.stats_export` | `services/cluster.py`, `routes/operations.py` | `$API $RENDER $TUI tests/server/test_operations.py tests/server/test_clusters.py` | low | G | |
| T5.6 | `refactor(web): consolidate duplicate — cluster_events_csv in web analytics export` | `web/routes/analytics.py` | `$WEB tests/server/test_web_analytics.py` | low | G | Unused imports are removed; the route keeps its position. |
| T5.7 | `refactor(services): consolidate duplicate — ClusterService.sync_plants + PlantNotFoundError, adopted by routes/plants` | `services/cluster.py`, `routes/plants.py` | `$API $RENDER $TUI tests/server/test_plants.py` | med | G | Target §7.2. |
| T5.8 | `refactor(web): consolidate duplicate — ClusterService.sync_plants in web plants sync` | `web/routes/operations.py` | `$WEB tests/server/test_web_operations.py` | med | G | |
| T5.9 | `refactor(api): extract function — response mappers in routes/operations.cluster_status` | `routes/operations.py` | `$API $RENDER $TUI tests/server/test_clusters.py tests/server/test_operations.py` | low | G | The TUI cluster screen renders this endpoint. |
| T5.16 | `refactor(services): extract method — _sensor_status_rows / _irrigator_status in ClusterService.get_cluster_status` | `services/cluster.py` | `$API $RENDER $TUI $CHECK D(gs.services.cluster)` | med | G | New in Rev 1. |
| T5.10 | `refactor(web): extract function — _rationale_reasons, _window_rows (+ helpers until ≤ 40) in cluster_detail` | `web/routes/clusters.py` | `$WEB tests/server/test_web_cluster_pages.py tests/server/test_web_decision_rationale.py tests/server/test_web_windows.py` | low | G | The quiet flag is left for I2. If the function is still > 40 lines because of the quiet block, the register entry is removed by I2. |
| T5.11 | `refactor(web): introduce parameter object — _plant_form_fields for plant create/update` | `web/routes/plants.py` | `$WEB tests/server/test_web_plant_pages.py tests/server/test_web_crud_actions.py` | low | G | |
| T5.12 | `refactor(api): consolidate duplicate — repo.get_plant instead of session.get(Plant, …)` | `routes/charts.py`, `web/routes/plant_dashboard.py` | `$API $RENDER tests/server/test_charts_plant_health_timeline.py tests/server/test_web_plant_hero.py` | low | G | |
| T5.18 | `refactor(web): extract function — context-section helpers in plant_dashboard` | `web/routes/plant_dashboard.py` | `$WEB D(gs.web.routes.plant_dashboard)` *(Rev 2: full direct map, which includes test_web_charts, test_web_charts_overlay, test_search and test_web_crud_actions)* | med | G | New in Rev 1. Coverage precondition (it was 46 % branch at baseline; Gate 1 raised coverage overall). Context keys are verbatim. |
| T5.17 | `refactor(web): extract function — _preference_flags / _auth_enabled in base_context` | `web/context.py` | `$WEB D(gs.web.context)` | med | G | New in Rev 1. The swallow and the `session.close()` placement are kept. |
| T5.13 | `refactor(api): consolidate duplicate — repo.get_vacation_window in routes/vacation` | `routes/vacation.py` | `$API $WEB tests/server/test_vacation.py tests/cli/test_tui.py` | low | G | Needs WP3 merged (wave A). |
| T5.14a–h | `refactor(api\|web): consolidate duplicate — deps.require_cluster for exact-form 404 lookups in <module>` | one module per commit: `routes/{alerts,charts,clusters,insights,operations}.py`, `web/routes/{analytics,clusters}.py` | that module's `D(…)` + `$API` or `$WEB` + `tests/cli/test_tui.py` | low | G | **Optional, last.** Exact form only. |
| T5.15 | `refactor(app): extract function — decompose create_app into ordered setup steps` | `app.py` | `$API $WEB $CORE $SCHED tests/server/test_auth.py tests/server/test_system_health.py` | med | **G+ with `FULL`** (m4) | Target §3.7. Auth wiring is high-risk: two reviewers. |
| T5.∑ | WP gate + DoD | — | union of the above, plus `$CORE` | — | WP | |

### WP1 — CLI client (wave C)

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| ~~T1.1~~ | **Dropped (Rev 1, B2).** No `resolve_server_url`. | — | — | — | — | The 3 env reads are pinned per module by `env_reads.json`. |
| T1.0 | `refactor(types): add type annotations — strict-clean client (JSONObject + _object/_array typed wrappers)` | `client.py` | `$CLI $TUI` | low | G | 174 errors at baseline, mostly `return-value`. Target §3.11. The `_request` replacement in `tui_fixtures` must still intercept. Strict: `client`. (Replaces the old T1.3.) |
| T1.2 | `refactor(cli): consolidate duplicate — _drop_none(fields) in client` | `client.py` | `$CLI $TUI` | low | G | |
| T1.4 | `refactor(cli): add docstrings — undocumented IrrigationClient methods` | `client.py` | `$CLI` | low | G | |
| T1.∑ | WP gate + DoD | — | `$CLI $TUI $(D gcli.client)` | — | WP | `client.py` > 400 lines is a register entry. |

### WP2 — TUI (wave C)

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T2.0 | `refactor(types): add type annotations — strict-clean tui/model, screens/cluster, screens/system, screens/settings, widgets` | those 5 files | `$TUI` | med | G | 8 + 21 + 3 + 5 + 11 errors. No handler is renamed. A Textual override collision gets `# type: ignore[override]  # contract: Textual API`. Strict: all 5. |
| T2.1 | `refactor(tui): move function — tui/render.py with plant_rows/sensor_rows, used by ClusterScreen` | `tui/render.py` (new), `tui/screens/cluster.py` | `$TUI` | med | G | Target §8. Strict: `render`. |
| T2.2 | `refactor(tui): move function — decision/history/config/window row builders` | `render.py`, `cluster.py` | `$TUI` | med | G | |
| T2.3 | `refactor(tui): move function — irrigator_info, decision_panel, forecast_rows (+ _next_water)` | `render.py`, `cluster.py` | `$TUI` | med | G | `_render_overview` ends ≤ 40 / CC ≤ 8. |
| T2.4 | `refactor(tui): move function — insights/stats/efficacy/learn builders` | `render.py`, `cluster.py` | `$TUI` | med | G | |
| T2.5 | `refactor(tui): replace conditional with dispatch — per-tab handlers for action_new` | `cluster.py` | `$TUI` | med | G | Textual name rule (target §8, guard 4). |
| T2.6 | `refactor(tui): replace conditional with dispatch — per-tab handlers for action_edit` | `cluster.py` | `$TUI` | med | G | |
| T2.7 | `refactor(tui): replace conditional with dispatch — per-tab handlers for action_delete` | `cluster.py` | `$TUI` | med | G | |
| T2.8 | `refactor(tui): move function — SystemScreen.load row builders` | `render.py`, `screens/system.py` | `$TUI` | med | G | |
| T2.9 | `refactor(tui): move function — SettingsScreen.load row builders` | `render.py`, `screens/settings.py` | `$TUI` | med | G | |
| T2.10 | `refactor(tui): extract function — _band, _plant_views (+ helpers until ≤ 40 / CC ≤ 8) in model.summarize` | `tui/model.py` | `$TUI` | low | G | |
| T2.11 | `refactor(tui): extract method — MetricChart _draw_event_lines / _set_x_ticks` | `tui/widgets.py` | `$TUI` | med | G | `show_payload` CC ≤ 8. |
| T2.∑ | WP gate + DoD | — | `$TUI $CLI` | — | WP | `cluster.py` stays > 400 lines (≈ 560): register entry. `compose` is a register entry (target §3.12). |

### WP6 — Core logic + learning + stats (wave D)

M-pre for `logic/{stress,trends,sensors,fallback}.py` and `learning/issues.py` first.

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T6.0 | `refactor(types): add type annotations — strict-clean utils, trends, fallback, learning/issues, learning/profiling, stats` | those 6 files | `$CORE $ENGINE $RENDER $SETTINGS tests/test_learning.py tests/test_utils.py tests/test_stats.py` | med | G | 1 + 1 + 8 + 4 + 8 + 14 errors. `utils.py` has an env read → `$SETTINGS`. Strict: all 6. |
| T6.1 | `refactor(core): replace literal — utils imports NIGHT_LUX_THRESHOLD and the seasonal month table from constants` | `utils.py` | `$CORE $ENGINE $RENDER $SETTINGS tests/test_utils.py tests/test_learning.py` | low | G | |
| T6.2 | `refactor(logic): replace literal — named constants in sensors, trends, stress, fallback` | the 4 files | `$ENGINE $RENDER tests/test_cleaning.py` | med | G | |
| T6.3 | `refactor(learning): replace literal — named constants in learning/issues` | `issues.py` | `$ENGINE $RENDER tests/test_learning.py tests/server/test_insights.py tests/server/test_alerts.py` | med | G | |
| T6.4 | `refactor(logic): extract function — _mean_or_none and _per_sensor_snapshot in get_recent_sensor_data` | `sensors.py` | `$ENGINE $RENDER tests/test_cleaning.py tests/server/test_sync_snapshot.py` | med | G | |
| T6.5 | `refactor(logic): extract function — functional core for analyze_historical_trends` | `trends.py` | `$ENGINE $RENDER` | med | G | Target §3.5. |
| T6.6 | `refactor(logic): extract function — pure stress detectors in detect_stress_conditions` | `stress.py` | `$ENGINE $RENDER` | med-high | G+ (`C(gc.logic.stress)`) | Only the grid and property tests guard this module, so the M-pre survivors must be handled first. |
| T6.7 | `refactor(logic): extract function — _config_fallback and _temperature_interval in temperature_based_decision` | `fallback.py` | `$ENGINE $RENDER` | med | G | |
| T6.8 | `refactor(learning): extract function — per-check helpers in detect_conflicts` | `issues.py` | `$ENGINE $RENDER tests/test_learning.py tests/server/test_insights.py tests/server/test_alerts.py` | med | G | Target §3.4. |
| T6.9 | `refactor(learning): extract function — per-sensor alert helpers in detect_issues` | `issues.py` | same as T6.8 | med | G | |
| T6.10 | `refactor(learning): extract function — phase helpers in compute_sensor_response and get_plant_profile` | `learning/profiling.py` | `$ENGINE $RENDER tests/test_learning.py D(gc.learning.profiling)` | med | G | New in Rev 1. Cleaned-view reads stay put. |
| T6.11 | `refactor(core): extract function — aggregation helpers in get_irrigation_stats; section helper in print_stats_report` | `stats.py` | `$CORE $RENDER $TUI D(gc.stats)` | low | G | New in Rev 1. Public signatures and printed text are unchanged. |
| T6.12 | `refactor(core): extract function — _csv_event_row and _write_event_rows in export_csv` | `stats.py` | `$CORE D(gc.stats)` | low | G | New in Rev 2 (nesting 4). A dead public function with a frozen import path. The CSV bytes and the printed line are verbatim. |
| T6.∑ | WP gate + DoD + M-post | — | `$ENGINE $RENDER $CORE tests/test_learning.py tests/test_cleaning.py tests/test_utils.py tests/test_stats.py` + `t $(C gc.logic.trends)` | — | WP | |

### WP8 — Engine + sync + devices (wave E, HIGH RISK, last; every task G+ with `C(gc.logic.engine)` unless noted)

M-pre for `logic/engine.py` and *(Rev 2)* `greenhouse_core/sync.py` first.

| ID | Title | Files | Tests | Risk | Gate | Notes |
|---|---|---|---|---|---|---|
| T8.0 | `refactor(types): add type annotations — strict-clean engine (RainForecast Protocol) and timing` | `engine.py`, `timing.py` | `$ENGINE $RENDER $CORE` | med | G+ | 20 + 2 errors. Absorbs the old T8.12. Every constant import stays (≥ 50 rule). Strict: both. |
| T8.1 | `refactor(engine): replace literal — named constants and moisture_target_range in the engine` | `engine.py` | `$ENGINE $RENDER $CORE` | med | G+ | This only adds constant imports. |
| T8.2 | `refactor(engine): extract method — _tz_name for the three preference lookups` | `engine.py` | `$ENGINE $RENDER` | med | G+ | |
| T8.3 | `refactor(engine): extract method — _record replaces four persist-and-return blocks` | `engine.py` | `$ENGINE $RENDER` | med | G+ | |
| T8.4 | `refactor(engine): extract method — _finalize closure becomes _finish(override_window=…)` | `engine.py` | `$ENGINE $RENDER` | high | G+ | |
| T8.5 | `refactor(engine): extract method — _pre_gates and _quiet_hours_skip` | `engine.py` | `$ENGINE $RENDER` | high | G+ | |
| T8.6 | `refactor(engine): introduce parameter object — _EngineInputs, _gather_inputs, _fallback_decision` | `engine.py` | `$ENGINE $RENDER` | high | G+ | |
| T8.7 | `refactor(engine): extract method — _evaluate_rules, _base_decision, _apply_adjustments; drop unused _apply_window_rule params` | `engine.py` | `$ENGINE $RENDER` | high | G+ | The two sequential terminal `if`s are kept (no `or`). |
| T8.8 | `refactor(engine): remove dead parameter — _decision_with_reason never receives snapshot/stress/trends` | `engine.py` | `$ENGINE $RENDER` | med | G+ | |
| T8.9 | `refactor(engine): extract function — soil-moisture rule helpers (in engine.py)` | `engine.py` | `$ENGINE $RENDER` | high | G+ | Target §3.3. |
| T8.10 | `refactor(engine): extract function — _vacation_days_left, _binding_max_minutes, _seasonal_overrides` | `engine.py` | `$ENGINE $RENDER` | high | G+ | |
| T8.11 | `refactor(logic): move function — active_quiet_window into logic/timing; engine delegates` | `timing.py`, `engine.py` | `$ENGINE $RENDER D(gc.logic.timing)` | high | G+ | |
| ~~T8.12~~ | Merged into T8.0. | — | — | — | — | |
| ~~T8.13~~ / ~~T8.14~~ | **Dropped (Rev 1, B1).** No `logic/rules.py`. | — | — | — | — | `engine.py` > 400 lines is a register entry. |
| T8.16 | `refactor(types): add type annotations — strict-clean core sync and the three docstring-touched device modules` | `sync.py`, `devices/profile.py`, `devices/sensors/tuya_generic.py`, `devices/sensors/tr301z.py` | `$DEV $CORE $SCHED` | med | G+ (`C(gc.sync)`, `C(gc.devices.gateway)`) | 6 + 2 + 1 + 0 errors. Annotations only. Strict: all 4. |
| T8.15 | `refactor(devices): add docstrings — correct stale dp_parsers docstrings` | `devices/profile.py`, `devices/sensors/tuya_generic.py`, `devices/sensors/tr301z.py` | `$DEV` | low | G+ | `git diff -w` shows only docstring/comment lines. |
| T8.17 | `refactor(core): extract function — _sync_window_start, _store_history, _store_live_reading in sync_single_sensor` | `sync.py` | `$DEV $SCHED $CORE $RENDER D(gc.sync)` | high | G+ (`C(gc.sync)`) | New in Rev 1. Invariant 8: no extra Cloud call; logger unchanged. |
| T8.18 | `refactor(core): extract function — _sync_logged and _sync_summary in sync_sensor_data` | `sync.py` | `$DEV $SCHED $CORE $RENDER D(gc.sync)` | high | G+ (`C(gc.sync)`) | New in Rev 2 (nesting 4). Per-sensor `try/except` and every log text verbatim. The stats dict key order is unchanged. |
| T8.∑ | WP gate + DoD + M-post (engine, sync) | — | `FULL` + `FULL_SEED2` + `TZ=America/New_York t $ENGINE` | — | WP | Two reviewers. |

### INT — Integrator tasks (serial, on the integration branch)

| ID | Title | Files | Tests | When |
|---|---|---|---|---|
| I1 | `build(lint): ratchet — drop cleared per-file ignores; register new modules in contracts; commit approved size exceptions` | `pyproject.toml`, `refactor/size-exceptions.txt` | `uv run ruff check libs/ tests/`, `uv run lint-imports`, `make typecheck`, `make sizecheck` | After every WP merge. Adds `greenhouse_cli.tui.render` to the TUI view-model contract after WP2. Ignores only shrink, and the strict list only grows. |
| I2 | `refactor(web): consolidate duplicate — cluster_detail quiet flag via active_quiet_window` | `web/routes/clusters.py` | `$WEB tests/server/test_web_cluster_pages.py tests/server/test_web_irrigator_actions.py` | After WP5 + WP8. `is_within_quiet_hours` returns a real `bool` (verified). Removes `cluster_detail`'s register entry if it now fits the DoD. |
| I3 | `refactor(types): add type annotations — ForecastService weather_client: RainForecast \| None` | `services/forecast.py` | `$RENDER D(gs.services.forecast)` | After WP4 + WP8. |
| I4 | `docs(refactor): update REFACTOR_NOTES` | `REFACTOR_NOTES.md` | — | Continuous. Record: the size-exception register (mirror it, one line per entry); the B-6 comment rewrite; that the plugin `LOGIC.md` needs no change; the deferred repository split. **Flag the CLAUDE.md-vs-BRIEF plugin-docs conflict for the PR body.** |
| I5 | Final gate / final DoD | — | `FULL` twice (`-n 2` and `-n 0`), `FULL_SEED2`, a `TZ=America/New_York` run, `lint-imports`, `make typecheck`, `ruff check`, **`make sizecheck` silent except register entries**, mutation post-runs complete, radon/xenon vs `refactor/baseline/` (report) | After the last merge. |

## 3. Per-WP hand-off note (what every implementer returns)

1. Commits (hash + subject), each green under its gate, rebased on the integration branch.
2. The exact test commands and results of the WP gate, plus any flaky reruns.
3. The DoD table for every function the WP decomposed (body lines / CC / nesting), the **proposed** register entries
   with reasons (Rev 2: the integrator commits them only after approval), and the function map (old qualname → new
   qualnames).
4. Modules appended to `refactor/mypy-strict.txt`, and files now clean of the ruff complexity rules (for I1).
5. M-pre and M-post results for mutation-list modules.
6. Anything that contradicted the target doc, any stopped task, any bug site touched (with its B-number), and any
   coverage gap (task id + lines).

## 4. Task count

| Package | Tasks |
|---|---|
| WP0 | 11 |
| WP3 | 10 |
| WP4 | 18 |
| WP7 | 23 (incl. T7.12 dropped) |
| WP5 | 21 (plus up to 8 optional) |
| WP1 | 3 |
| WP2 | 12 |
| WP6 | 13 |
| WP8 | 16 |
| INT | 5 |

## 5. Revision 1 — changes to this plan

| Finding | Change |
|---|---|
| B1 | T8.13/T8.14 dropped. The rule helpers stay in `engine.py` (T8.9). `engine.py` > 400 lines is a register entry. No frozen test is edited. |
| B2 | T1.1 dropped. `$SETTINGS` added to `$CLI`, plus the subset rule for env-reading files. WP1 owns only `client.py`. |
| M1 | T0.2 is the critic-verified 10 contracts (no `logic.rules`). I1 only adds `tui.render`. |
| M2 | T0.3 adds the `tests/**` per-file ignore. |
| M3 | New `$RENDER` / `$CHECK` aliases and the subset rule (§0.3). **Every** task row was re-audited: services, repo, engine and logic tasks gained `$RENDER` / `$CHECK`; API tasks gained `$TUI` where the TUI renders the endpoint; `$PIPE` gained TUI actuation. |
| M4 | DoD in §0.4 (per task, per WP, final), enforced by `sizecheck`, ruff C90 @ 8 and mypy. T0.9 adds the checker and register. Every WP starts with "add type annotations" tasks, and mypy strict is a gate. New tasks: T4.12–T4.17, T5.16–T5.18, T6.10, T6.11, T7.20, T7.21, T8.16, T8.17; T7.19 extended (`_run_pump_watcher`). The full verdict table is in target §3.12. |
| M5 | §0.2: ≤ 2 worktrees, the waves in §1, a `flock` lock with `-n 2` in `t`/`FULL`, the flaky-rerun rule, and re-timed gates (§0.3, §0.4). T0.11 measures the real timings. |
| m1 | T7.16 uses `cast` with a documented reason. |
| m2 | P0 references the orchestrator's `refactor-gate1` tag. |
| m3 | T0.1 adds mutmut; T0.10 configures it; §0.5 defines the M-pre/M-post protocol, threshold, budget and fallback. |
| m4 | T5.15 is G+ with `FULL` and two reviewers. |
| m5 | T7.12 dropped. The health gate stays inline (register entry). |
| nits | T7.7 passes `app`; T8.7 keeps two `if`s; guard 1 runs in a subprocess; guard 4 covers Textual shadowing and the `key_` prefix; I4 flags the plugin-docs conflict for the PR. |

### Revision 2 — changes to this plan

| Finding | Change |
|---|---|
| M-R2-1 | `PYTHONHASHSEED=0` in `t` / `FULL` and every gate. `FULL_SEED2` (`PYTHONHASHSEED=12345`) added to every WP gate, the integration gate and I5. Red-test policy (§0.2): re-run the identical command, never in isolation. Flaky only via the Gate-1 list `refactor/gate1/flaky-tests.txt` (orchestrator-owned, initially empty) or a reproduction on the parent commit; otherwise revert. |
| M-R2-2 | Target §3.12 gains a Nesting column and a re-scan (14 functions with nesting > 3, all covered). New tasks T6.12 (`export_csv`), T7.22 (`rearm_leak_checks`) and T8.18 (`sync_sensor_data`). New EXC rows for `bulk.stop_all_irrigators` (the wrong OK is fixed) and `FormScreen.compose`. T0.9 asserts the seeded checker lists only functions with a task. |
| m-R2-1 | `refactor/size-exceptions.txt` is integrator-only. Implementers propose entries; orchestrator approval is required; I1 rejects unapproved lines. |
| m-R2-2 | The M-post comparison keys on mutant identity (operator + original/replacement snippet + enclosing function), mapped through each WP's function map. |
| m-R2-3 | T5.18 uses `D(gs.web.routes.plant_dashboard)`. T7.20/T7.21 add `D(gs.services.leak)`; T8.17/T8.18 add `D(gc.sync)`. |
| m-R2-4 | `greenhouse_core/sync.py` added to the mutation list (M-pre in WP8). |
| m-R2-5 | The probe mutates in place in the implementer's own worktree and restores via `git checkout -- <file>` in `finally`. |
| nits | Imports added only for typing go under `TYPE_CHECKING` (target §10.13b). Guard test 5 checks annotations on boundary-module helpers. T5.15 gets its own two reviewers in wave B. |


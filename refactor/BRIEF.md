# Refactor brief (shared by every agent)

Repo: `/home/user/greenhouse` (giocaizzi/greenhouse). Branch: `claude/focused-hawking-7to7o3`, cut from merged `main` @ `a1b2622`
(TUI PR #106 merged). Python >=3.11 (no syntax newer than 3.11). Tooling: `uv` (`uv run …`). Read `CLAUDE.md` first —
its "Key invariants" are frozen behavior.

## Mission
Behavior-preserving refactor: make the codebase cleaner, more readable, better organized with **zero functional change**.

## Non-negotiable rules
1. No functional change. Found a bug? Do NOT fix it — record it in `REFACTOR_NOTES.md` ("Observed bugs (not fixed)") and pin it in a characterization test.
2. Tests first. No production code moves until its behavior is pinned by a passing test.
3. Small steps, always green. One transformation per commit. Red → revert, don't fix forward.
4. Never mix concerns in a commit (moves / renames / extractions / formatting are separate commits).
5. YAGNI beats patterns. Introduce an abstraction only to remove a concrete present smell. Simple function > class > pattern.
6. Public contracts are frozen: public signatures, import paths, CLI interface + `--help`, config/env vars, serialized formats, DB schema/DDL, HTTP routes, OpenAPI, MCP tools. If an import path moves, keep a re-export shim at the old path.
7. Evidence over opinion: test runs, golden diffs, tool reports.
8. Never weaken/edit a characterization or golden test to match refactored output.

## Repo-specific frozen surface (highlights)
- `/api/v1` OpenAPI (`app.openapi()`); MCP tool names = route **function names** (operationId), descriptions = route **docstrings**. Route function names, `response_model`s, and route docstrings are frozen. Pydantic class names/field order/aliases appear in OpenAPI — frozen.
- Web UI `/` rendered HTML, template names and context keys.
- CLI `greenhouse` command tree, `--help` text, options/defaults, JSON output, exit codes. TUI (`greenhouse tui`): bindings, `action_*`/`on_*` names, widget ids/classes (coupled to `app.tcss`), `CSS_PATH`, request bodies sent.
- Console scripts `greenhouse` (`greenhouse_cli.main:app`), `greenhouse-server` (`greenhouse_server.app:main`).
- Env vars + defaults, `Settings` schema; SQLAlchemy DDL + Alembic head; `constants.py` values; `TriggerCode` members.
- Import paths: `greenhouse_core.{models,schemas,repository,constants,utils,plant_db,logic,devices,learning,sync,stats,database,auth}`, `greenhouse_server.{app,config,deps,auth,scheduler}`.
- Out of scope, do not touch: `migrations/versions/*`, `plant_database.json`, `devices/profiles/*.json`, `web/static/*`, templates' rendered output, `plugin/`, `.claude-plugin/`, `CHANGELOG.md`, `release-please-*`, `uv.lock` (except dev-group tooling), `Dockerfile`, `docker-compose.yml`, `.github/workflows/*`.
- High risk (two reviewers, explicit adversary coverage): `devices/`, `logic/engine.py`, auth paths, scheduler.

## Owner decisions (binding)
- **Layout: keep the `libs/` uv workspace as is — NO `src/` layout migration.** Packages stay at
  `libs/greenhouse-{core,server,cli}/greenhouse_{core,server,cli}/`; hatch `packages` and pytest `pythonpath` are unchanged.
  Structural work happens *inside* each package only.
- **Method-level cleanup is first-class scope, not just module moves.** The plan must include a method-by-method pass:
  small single-purpose functions (≤ ~20–40 lines, CC ≤ 8, nesting ≤ 2–3), guard clauses, intention-revealing names,
  clear typed interfaces (explicit params instead of `**kwargs`/dict bags, keyword-only args where call sites are
  ambiguous, parameter objects for long lists, precise return types, Command–Query Separation), and why-docstrings.
  Limits: frozen contracts still win — route function names/docstrings/`response_model`s, public import paths,
  Pydantic class/field names, Textual `action_*`/`on_*`/ids and CLI options do not change. Internal (non-contract)
  method signatures MAY be redesigned when every call site is updated in the same commit and tests prove no change.

- **Non-functional fixes may be queued freely** (owner, 2026-10-02): any change that does not alter runtime behavior
  is in scope; the code may evolve over several steps toward its cleanest form.
- **Doc-contract text may change, reviewed** (owner, 2026-10-02): docstrings of routes (MCP tool descriptions),
  Pydantic schemas (OpenAPI descriptions) and Typer commands (`--help`) MAY be edited — only in the lint-ratchet stage
  (after wave C), each in a dedicated commit that regenerates ONLY the affected goldens (OpenAPI / MCP tools / CLI
  help) with a reviewed diff showing description-text changes only. Route function names (operationIds),
  signatures, `response_model`s, Pydantic class/field names and order, CLI option names stay frozen. Running work
  packages are unaffected (they keep the full freeze).

- **Prune dead code along the way** (owner, 2026-10-02). Rules:
  1. Evidence first: zero references across `libs/`, `tests/`, templates (`*.html`, Jinja filters/globals registered by
     name), `app.tcss`, `plugin/`, entry points, and no dynamic access (`getattr`, Textual `action_*`/`on_*` name
     strings, decorators/registries, `__all__`). vulture output is a lead, never proof.
  2. One dedicated commit per removal group: `refactor(<area>): remove dead code — <what>`, body lists the evidence
     (grep commands + results) and that the full relevant subset is green.
  3. Unreachable branches (provably impossible conditions) may be removed only when the proof is in the commit body
     and no test exercises them.
  4. Dead **public** names on pinned import surfaces (`tests/golden/contracts/imports.json`) and dead code whose only
     users are its own characterization tests: remove in a dedicated commit that also removes those tests / that
     golden name, with the justification in the body — the reviewer checks the golden diff is removal-only.
  5. Never "dead": route/web/Typer handlers, Textual handlers and actions, scheduler job functions, Pydantic models
     referenced by OpenAPI, anything a frozen golden renders.

## Test suite
`uv run pytest` — ~1160 tests, ~12 min full. Use targeted paths / `-x` for inner loops. No real network/hardware ever;
fakes in `tests/fake_devices.py`, `tests/fake_data.py`, fixtures in `tests/conftest.py`, TUI fixtures in `tests/cli/tui_fixtures.py`.
Tests mirror packages: `tests/test_*.py` (core), `tests/server/`, `tests/cli/` (incl. `test_tui.py`), `tests/devices/`.
The machine has 4 cores; do NOT run the full suite unless your brief tells you to.

## Output discipline
Write findings to files under `refactor/` (never only in chat). Return a short conclusion (≤ 25 lines) to the orchestrator.
Do not commit unless your brief says so. Do not touch files outside your stated scope.
- **Sprint mode** (owner, 2026-10-02): up to 3 implementer worktrees at once; integration gate per merge = one
  `FULL` run (seed 0) + lint/typecheck/import contracts; the seed-12345 and `TZ=America/New_York` runs move to the
  final gate; optional tasks skipped; second reviewers only for high-risk code (pipeline/scheduler, engine,
  devices, auth/app wiring). Per-task safety rules (coverage precondition, golden checks, no fix-forward) unchanged.
- **Remove drift everywhere** (owner, 2026-10-02): divergent duplicate copies are unified on this branch as
  clearly labeled behavior-change commits (`fix(drift): …`), one pair per commit, updating only the affected pinned
  tests/goldens with a reviewed diff — see `refactor/45-drift-track.md`. Docs and plugin (`CLAUDE.md`, `plugin/`) are
  synced to the final code (now in scope). Running work packages keep drifted copies untouched until the drift track.

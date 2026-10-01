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

## Test suite
`uv run pytest` — ~1160 tests, ~12 min full. Use targeted paths / `-x` for inner loops. No real network/hardware ever;
fakes in `tests/fake_devices.py`, `tests/fake_data.py`, fixtures in `tests/conftest.py`, TUI fixtures in `tests/cli/tui_fixtures.py`.
Tests mirror packages: `tests/test_*.py` (core), `tests/server/`, `tests/cli/` (incl. `test_tui.py`), `tests/devices/`.
The machine has 4 cores; do NOT run the full suite unless your brief tells you to.

## Output discipline
Write findings to files under `refactor/` (never only in chat). Return a short conclusion (≤ 25 lines) to the orchestrator.
Do not commit unless your brief says so. Do not touch files outside your stated scope.

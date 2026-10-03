# Phase-4 fix pass — consolidated work list

Sources: `40-review-clean-code.md` (CC), `40-review-architecture.md` (A), `40-review-dry-yagni.md` (DY),
`40-review-typing.md` (T). Starts after `refactor/lint` merges (lint touches every file). Three packages with disjoint
file ownership so they can run in parallel; WP8 files (`logic/engine.py`, `logic/timing.py`, `devices/**`, core
`sync.py`) are excluded until WP8 merges. Rules: `BRIEF.md` + `30-implementer-brief.md`; behavior changes are labeled
`fix(drift|consistency)` with `REFACTOR_NOTES.md` entries; dead code per BRIEF dead-code rules (golden diffs removal-only).

Every package: drop the stale refactor-process ids from production comments in the files it owns
(CC "stale references": D-, OD-, WP-, B-, §-ids, "pinned", "exactly as before the extraction") — rewrite as a plain
why-comment or delete.

## FP-S — server (`libs/greenhouse-server/**`, excl. TUI/CLI)
1. A2 one job/read session helper (`job_session(commit=…)`, `read_session`), `_session.py` → `jobs.py` (shim not needed: private).
2. A3 auth: one request session (reuse `deps` session), user CRUD through the repository. Behavior check: CC flags shared
   session as observable → labeled `fix(consistency)` if any test-visible difference.
3. A4 job plumbing out of `services/irrigation.py` → `services/irrigation_jobs.py`; re-export names tests call by path.
4. A7/B9 one "not found" convention: services raise `LookupError` subclass (or return `None` uniformly); routes map via
   `deps.require_*`; irrigate route stops string-matching `"cluster not found"` (CC).
5. CC: extract the health gate from `run_irrigation_pipeline` (side-effect order unchanged), drop its size exception;
   flatten the double-negative dry_run/skip branch.
6. T3 `maybe_notify(fn: Callable[[NtfyClient], object])`, `category: Literal[...]`; remove 4 ignores + bulk error.
   **D20** (labeled `fix(drift)`): move the notify into `stop_all_irrigators` so web stop notifies too.
7. C-DUP-5/T4: typed `app.state` accessor (one module), replaces the 18 `getattr(app.state, …)` + 60 untyped reads;
   `_run_pump_watcher(app: FastAPI)`.
8. A9: `web/forms.py` consolidating the 6 form-parser copies (+ CC `parse_value` CC 11); plant form field list once (DY).
9. DY: one shared vacation/window validation-error → 400 mapper for API + web.
10. A20/T: return annotations on all routes/commands (web routes `-> Response`, never unions); remove the relaxed mypy
    override for routes.
11. A contracts: add the 10 import-linter contracts from the architecture review.
12. Dead: `CreateSchedulerJobRequest`, `AuthUserDep`, `partials/_sensor_row.html`; `SessionDep`/`DeviceGatewayDep`:
    declare next to provider and use (DY).
13. T: `routes/configs.py`, `migrations/env.py`, `routes/bulk.py` into strict.

## FP-C — core non-WP8 (`libs/greenhouse-core/**` minus WP8 files)
1. CC magic numbers → `constants.py` (plant_db care-defaults literal, `hours=24/48/72/6`, `limit=200`, `0.5`, `2`, `12`,
   `8760`); forecast rain-threshold alias → the constant (engine's hardcoded `2.0` after WP8).
2. CC/T TypedDicts: effective config (`get_effective_config`), sync snapshot, weather results, alert findings; remove
   the downstream casts. Repository `update_*(**fields)` → `Unpack[TypedDict]`.
3. T5 StrEnums for event actions/sources/`triggered_by`/entity types, old constant names kept as aliases (golden superset).
4. A5 split `utils.py` (light math vs display-timezone) with re-exports at `greenhouse_core.utils`.
5. A6 lazy/slim package `__init__`s — only if the imports golden stays identical (root re-exports are frozen import
   paths; keep them, make heavy ones lazy via module `__getattr__` only if needed — else skip, YAGNI).
6. A8 repository wraps `IntegrityError` → domain error; `services/inventory.py` stops importing SQLAlchemy.
7. CC `_patch_fields_hasattr_first` → `_patch_fields` (DY: same result — prove with test); `detect_conflicts` rename of
   the low-light/low-humidity emitters (internal names only).
8. T2 `schemas.py` six `dict` → `dict[Any, Any]` (OpenAPI byte-identical — golden proves it).
9. Dead: `set_plant_database`, `EVENT_ACTION_OFF`, `database.logger`, `plant_db` singleton if test-only (rule 4).
10. Test: CLI `ALL_WEEKDAYS` equals core constant.

## FP-U — CLI + TUI (`libs/greenhouse-cli/**`)
1. `tui/render.py` (401 lines) split by widget family; no size exception.
2. Typer option dedupe (shared `Annotated` aliases: Cluster ID ×9, `--yes` ×7, config/irrigator/plant add/update pairs);
   help golden must stay identical.
3. Move command closures out of `register()` → drop its two size exceptions.
4. **Labeled fix**: `SpriteView._animate` shadows `Widget._animate` → rename (`REFACTOR_NOTES` entry + test first).
5. `IrrigationClient` closed (context manager) — check no behavior change in CLI tests; `call(**kwargs)` typed.
6. ~90 TUI method docstrings (why-docstrings, not restating names).

## After WP8 (orchestrator)
D11 quiet-hours helper; D16b `parse_device_config` in gateway; **OD3 device-type aliases — same commit/PR as the web
irrigator form options and CLI `--type` help** (DY ordering risk); `CONFIDENCE_BASELINE` dead check; engine `2.0` rain
constant; WP8 typing (58 errors); stub overrides for `tinytuya`, `apscheduler`, `fastapi_mcp`; OD5 mypy
`files = ["libs"]`.

## Not doing (evidence)
- ruff `TC` rules (autofix breaks FastAPI/Typer/Pydantic at import time) — T.
- 39 broad excepts: reporting only (behavior); `noqa: BLE001` either removed or BLE enabled by lint ratchet.
- `IrrigationLearner` facade stays (public import path).

## Code-side doc drift (from `wp-handoff/DOCS.md`) — doc-contract edits, reviewed golden diffs
- `windows list` and `irrigator --type` Typer help (FP-U; `--type` after OD3 aliases), `start_irrigator` route docstring
  "local protocol" (FP-S; MCP golden), `ConfirmScreen` docstring (FP-U), `tests/golden.py` citing `refactor/BRIEF.md` (OD5).
- After OD5 moves: AGENTS.md "Development" paragraph naming `refactor/` paths must follow.

## OD5 prerequisites (from FP-C hand-off)
- `REFACTOR_NOTES.md` must be self-contained before `refactor/` is deleted: inline titled entries for every bug it cites
  only via `refactor/00-smells.md` / safety reports (e.g. B-18, B-24, the shared `detect_conflicts` gate).
- Deferred from FP-C: enum-typed parameters (~56 signatures, mostly server) and server TypedDicts (weather results,
  alert findings, sync snapshot) — after FP-S merges; engine rain `2.0` — after WP8.

# FP-S hand-off — fix pass, server package

Worktree `/home/user/gh-fp-s`, branch `refactor/fp-s`, base `8a76a3f`. Not pushed, not merged, not rebased.
Scope kept: `libs/greenhouse-server/**` + its tests; one core file (`repository.py`, own commit `52d0c40`); no WP8
files, no `libs/greenhouse-cli/**`, no other core file. pyproject edits: `[tool.importlinter]` and the mypy
override block only, plus one per-file-ignore ratchet removal.

## Commits (work list item → commit)

| # | Item | Commit(s) | Evidence (subset, all green) |
|---|---|---|---|
| 11 | 10 import-linter contracts (A) | `42e49a5`; layer/ignore follow-ups in `f98776b`, `ded7570`, `42f9860`, `7aeb8ee`, `b0d9b15` | `lint-imports` 20 kept, 0 broken |
| — | stale refactor ids (C-1) in server comments | `4b3729a` | grep for D-/OD-/WP-/B-ids, §, "(pinned)", "exactly as before", "PR 1.5" → 0 hits in libs/greenhouse-server |
| — | sizecheck hit `services/charts.py` 401 lines | `c819afc` (then `36fc444` inlines the identity helper) | sizecheck: no server hit left |
| 12 | dead: `AuthUserDep`, `partials/_sensor_row.html` | `35081c4`, `a770b53` (+ `33d93c6` format) | grep evidence in bodies; golden diffs removal-only (imports.json, package_data.json) |
| 12 | `SessionDep` / `DeviceGatewayDep` declared next to provider and used (DRY-7); forecast factory → deps | `edfa511` | tests/server 1656 passed |
| 1 | A2: `_session.py` → `jobs.py`; `job_session(commit=…)`; `read_session` | `f98776b`, `522631a`, `d694e55`; A11 redundant imports `76b7bf5` | $SCHED + wp7 gaps (commit/rollback/close order test) 275 / 382 passed |
| 7 | C-DUP-5/T4 typed `app.state` accessors (`greenhouse_server/state.py`), scheduler `_job_app()` (5 ignores → 1) | `ded7570` | tests/server 1656 passed |
| 2 | A3: providers into `state`, deps re-exports `get_session` (same object), app reuses `get_settings` | `8e34816` | $API + auth/web 414 passed |
| 2 | A3: **labeled** shared request session | `d44ab8f` `fix(consistency)` | new `tests/server/test_auth_session.py` red→green |
| 2 | A3: user CRUD via repository (core, separate commit) | `52d0c40` | new `tests/test_repository_users.py`; $CORE 112 passed |
| 2 | A3: auth + auth routes use the repository / `RepoDep` + `repo.commit()` | `42f9860` | 544 passed; 2 sqlalchemy contract ignores dropped |
| 3 | A4 (partial, see deviations) leak-check plumbing → `services/irrigation_jobs.py` | `7aeb8ee` | 464 passed; irrigation.py 972 → 813 lines |
| 5 | CC S-1/S-3 health gate extracted, dry-run/skip flattened; size exception removed | `24210ca`; ruff ratchet `f69732d` | $CHECK/$PIPE-ish 356 passed |
| 6 | T3 `maybe_notify(fn: Callable[[NtfyClient], object])`, `NotifyCategory` Literal | `8d36239` | 352 passed; 4 ignores + bulk strict error gone |
| 6 | **D20 labeled**: web emergency stop notifies | `cfb423f` `fix(drift)` | new `tests/server/test_web_emergency_notify.py` red→green |
| 4 | A7/B9 one not-found idiom (`services/errors.py`, `deps.not_found_as_404`, irrigate uses `require_cluster`) | `01687d4` | tests/server 1657 passed |
| 8 | A9 `web/forms.py` (`blank_or`, `parsed_or_400`, `tri_bool`) | `b0d9b15` | $WEB + web form tests 363 passed |
| 9 | DY one 400 mapper for vacation / window rules (`deps.require_valid_*`) | `24dfa4f` | 291 passed |
| 10 | A20/T return annotations: web `-> Response`, API actual return types; override lines removed | `cb1b33f`, `413729b` | routes.json/OpenAPI/MCP goldens unchanged; typecheck OK |
| 13 | strict list: `routes/configs.py`, `routes/bulk.py`, `migrations/env.py` (+ new modules) | `52fd627` | `make typecheck` 157 files |
| doc | `start_irrigator`/`stop_irrigator` docstrings (MCP tool text) | `23d239d` `docs(api)` | only openapi.json + mcp_tools.json regenerated; description-stripped documents equal; both fingerprints re-recorded |
| — | four lazy `type: ignore`s (T3 sleep, T4 overlay events, T5 chrome close, T7 walrus) | `6754277` | 294 passed |
| — | A10 analytics module docstring, A9 note on `set_check_all_paused` order | `8e3400a` | — |

## Labeled behavior changes (REFACTOR_NOTES "Labeled behavior changes landed" entries added)
1. `d44ab8f` fix(consistency): an authenticated request opens one DB session (auth dependency reuses `get_session`).
2. `cfb423f` fix(drift) D20: web `POST /bulk/stop-all` sends the same ntfy emergency push as the API.

## Deviations / skipped (and why)
- **A4 partial.** The pump-watcher half stays in `services/irrigation.py`: frozen test
  `test_contract_wp7_gaps::test_watcher_job_session_commit_rollback_close_order` monkeypatches
  `services.irrigation.handle_watcher_interrupted` and needs `_run_pump_watcher` to call that name. Only the
  leak-check jobs moved; ignore `services.irrigation -> scheduler` remains next to the new `irrigation_jobs` one.
  `irrigation_jobs` logs to the pipeline's logger name (tests read it).
- **A3 shape.** `greenhouse_core/auth.py` is FP-C territory, so repository user methods *delegate to* core auth
  (one implementation) instead of core auth becoming wrappers. Public signatures of `require_user`,
  `authenticate`, `bootstrap_admin(engine, settings)` kept (tests call `bootstrap_admin` with an engine), so
  `bootstrap_admin` still opens `Session(engine)`.
- **T3 test call sites.** `tests/server/test_notify.py::TestMaybeNotify` — 4 zero-arg callbacks became one-arg
  (internal signature redesign with every call site updated; assertions unchanged). Reviewer: please confirm.
- **Goldens edited (removal-only, prune rule 4):** imports.json — `AuthUserDep`; `Request` (app), `Generator`
  (deps, auth); `create_user`, `get_user`, `get_user_by_username`, `set_password` (server auth re-imports).
  package_data.json — `_sensor_row.html`. Doc-contract: openapi.json, mcp_tools.json + 2 fingerprints.
- **Skipped `CreateSchedulerJobRequest`** (item 12): lives in core `schemas.py`, outside my allowed files — for
  FP-C / final sweep (grep evidence in 40-review-dry-yagni DC1; golden `imports.json:369`).
- **Skipped DRY-5 plant form dataclass dependency:** a sub-dependency resolves before path params, so a request
  with both an invalid path id and a missing field would list 422 errors in a different order (observable).
- **CLI mypy override entries kept:** `greenhouse_cli.commands.*`/`main` still have 62 strict errors on this
  base; the integrator removes those two lines after FP-U merges.
- **web/context read_session:** not adopted — its open-failure fallback `(None, None)` and caller-side close do
  not fit the helper without changing which errors are swallowed; only the type ignore was removed.
- Not done (out of list/time): S-5 activity-code vocabulary, S-12..S-14 health monitor, C-5 keyword-only params,
  A1 (labeled fix, still in Observed bugs).

## Ratchet / integrator notes
- `size-exceptions.txt`: removed `IrrigationService.run_irrigation_pipeline`. Proposed reason update for
  `services/irrigation.py` file entry: "pipeline + pump-watcher job seam (test patches
  `services.irrigation.handle_watcher_interrupted`, `_time`); leak-check jobs live in irrigation_jobs".
- pyproject per-file ignores: `services/irrigation.py` PLR0915 removed; every other server entry still has hits
  (probed by removing each code and running ruff with JSON output).
- New strict modules: `state.py`, `services/jobs.py` (renamed), `services/irrigation_jobs.py`,
  `services/errors.py`, `web/forms.py`, `routes/configs.py`, `routes/bulk.py`, `migrations/env.py`.
- Import-linter: server layers gain `state` (between scheduler and services); web lowest layer gains `forms`.
- Merge watch: `repository.py` (FP-C also edits it — my insert is the new "Users" section after `flush`);
  `tests/golden/contracts/imports.json` (FP-C removes other names); pyproject mypy override block (FP-U).

## Final gates (this branch head)
`ruff check libs/ tests/` clean · `ruff format --check` clean (330 files) · `make typecheck` OK (157 files) ·
`lint-imports` 20 kept · sizecheck: no server hits (remaining 7 are WP8 files + core `constants.py`) ·
full suite: see the last line of this file.

Full suite at `8e3400a`: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2` → **3049 passed**, 0 failed (11m37s; base had 3039 + 10 new tests: 2 auth-session, 4 repository-users, 3 web emergency notify, +1 net elsewhere: none removed).

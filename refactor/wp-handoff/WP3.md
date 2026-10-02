# WP3 hand-off — data types + repository (PARTIAL: T3.1–T3.5 stopped on the coverage precondition)

Worktree `/home/user/gh-wp3`, branch `refactor/wp3-repo`, based on `5267aa6`. Not pushed, not merged, not rebased.
Every test command ran as `PYTHONHASHSEED=<seed> flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …` (the plan
§0.3 `t` / `FULL` / `FULL_SEED2` helpers). No red test occurred, so no reruns were needed.

**Status:** 5 of 9 tasks done (T3.0a, T3.0b, T3.6, T3.7, T3.8). **T3.1–T3.5 are stopped** under the §0.4 G.1
coverage precondition: the lines/branches they would restructure are not covered by their subsets, nor by the full
suite (`refactor/gate1/coverage.json` reports the same gaps). Characterization tests are needed before they can run;
see "Coverage gaps". T3.6–T3.8 touch other methods than T3.1–T3.5, so I ran them out of table order (deviation 1).

## Commits

| Task | Commit | Evidence |
|---|---|---|
| T3.0a | `078ce13` strict-clean schemas | mypy 8 → 0. Validators `(cls, v: Any) -> Any`. 6 bare-`dict` fields keep their annotation with `# type: ignore[type-arg]  # contract: OpenAPI` (IrrigatorBase.config, SensorBase.config, AlertResponse.data, IrrigateResponse.stress_indicators, UpdateSensorRequest.config, UpdateIrrigatorRequest.config). `t $CORE $API $RENDER D(gc.schemas)`: **1223 passed**. |
| T3.0b | `e5736c6` strict-clean repository | mypy 16 → 0. `**fields: Any` ×9; `dict` → `dict[str, Any]` on 5 params; 2 × `cast("CursorResult[Any]", session.execute(stmt))` for `.rowcount` (CursorResult under `TYPE_CHECKING`). `t $CORE $RENDER $CHECK D(gc.repository)`: **1219 passed**. |
| T3.6 | `0450248` `_page` | Compiled SQL of old vs new is byte-identical for all 36 parameter combinations (literal binds). `t $CORE $API $RENDER test_{sensors,irrigators,plants,system_health,anomaly,data_quality}`: **649 passed**. |
| T3.7 | `4e57e6a` `get_vacation_window` | Additive, `= self.session.get(VacationWindow, window_id)`. `t $CORE tests/test_db.py`: **62 passed**. Unblocks T5.13. |
| T3.8 | `a1dcc49` `FULL_WEEKDAY_MASK` default | `inspect.signature` default is still `127` (int). `t $CORE $RENDER test_irrigation_windows`: **359 passed**. |

Every commit: `ruff check` + `ruff format --check` clean on the touched file, `uv run lint-imports` 10 kept / 0 broken,
`make typecheck` green, `git status --porcelain tests/golden` empty.

## WP gate (on `a1dcc49`)

- `t $(D gc.repository) $(D gc.schemas) $CORE $API $RENDER $CHECK` (seed 0, `-n 2`): **1416 passed** (7m33s).
- `FULL` (seed 0, `-n 2`): **2797 passed** (9m17s).
- `FULL_SEED2` (seed 12345, `-n 2`): **2797 passed** (9m07s).
- No reruns; `git status --porcelain tests/golden` empty; `lint-imports` 10 kept; `make typecheck` 38 modules OK;
  ruff check/format and C90@8 clean on both files.
- This gate covers the 5 landed commits only. It must be re-run after T3.1–T3.5 land (and after rebase).

## Coverage gaps (stopped tasks) — characterization tests requested

Coverage run: union of the T3.1–T3.8 subsets with `--cov-branch --cov-context=test` (883 passed). The Gate-1 full-suite
`coverage.json` lists the **same** missing lines/branches, so no existing test anywhere covers them. Line numbers are
at `5267aa6` (WP0 head); the method names are stable.

| Task | Uncovered site (`5267aa6` line) | What a characterization test must pin |
|---|---|---|
| T3.1 | `schemas.py:120`, `:185` — `return json.loads(v)` in `IrrigatorResponse.parse_config` / `SensorResponse.parse_config` (branches 118→120, 183→185) | Validating each response model from a JSON **string** `config` (e.g. `IrrigatorResponse.model_validate({... "config": '{"a": 1}'})` → `{"a": 1}`), plus the non-str passthrough. |
| T3.2 | `repository.py:1028` `delete_irrigation_window`, `:1282` `delete_sensor`, `:1306` `delete_irrigator` — the not-found `return False` | Each of the three returns `False` for an unknown id (and deletes nothing). The helper's False branch would still be reached through `delete_vacation_window` / `delete_cluster`, but the per-method wiring of the not-found path is unpinned. |
| T3.3 | `update_vacation_window` branch 966→963 (`hasattr` false: unknown key ignored); `update_irrigation_window` `:1016` (not-found `return None`) and branch 1020→1017 (unknown key ignored) | Unknown keys are silently ignored (no error, no attribute set) for both; `update_irrigation_window(unknown_id, …)` → `None`. |
| T3.4 | `update_sensor` `:1260` (not-found), `:1268` (None value skipped), `:1270` (dict `config` JSON-encoded), branch 1271→1266 (unknown key ignored); `update_irrigator` `:1291` (not-found), branch 1297→1292 (unknown key ignored) | Not-found → `None`; `update_sensor(id, name=None)` leaves the name; `update_sensor(id, config={...})` stores `json.dumps(...)`; unknown keys are ignored for both. |
| T3.5 | `update_plant` `:1173` (not-found `return None`) | `update_plant(unknown_id, …)` → `None`. (Whether `hasattr` is false for unknown keys in the hasattr-first variant is not measurable by branch coverage — compound condition; a test with an unknown key for prefs/cluster/plant would also pin B-15's `None`-skip.) |

When the tests land, T3.1–T3.5 apply cleanly on top of this branch: they touch only `parse_config` and the
update/delete methods, none of which T3.6–T3.8 changed.

## DoD / sizecheck / strict list / ratchet (for I1)

- **Sizecheck delta:** 0 → 0 functions listed for both files (neither file had a listed function before; both keep
  their approved file-length register entries: schemas 999 → 1000 lines, repository 1309 → 1323 lines).
- `ruff --isolated --select C90 max-complexity=8`: clean on both files (before and after). Neither file has a
  per-file ignore, so there is no ratchet edit for I1.
- New helper DoD: `_page` 4 body lines / CC 3 / nesting 1; `get_vacation_window` 1 / 1 / 0.
- **Strict list additions:** `libs/greenhouse-core/greenhouse_core/schemas.py`,
  `libs/greenhouse-core/greenhouse_core/repository.py` (38 modules, `make typecheck` green).
- **Function map** (for mutant identity): `IrrigationRepository.list_all_{sensors,irrigators,plants}` →
  same qualname + `IrrigationRepository._page`. No other function was decomposed.
- **Mutation:** neither file is on the §0.5 list; no M-pre/M-post.
- **Proposed size-exception entries:** none.

## Deviations

1. **Task order:** T3.6, T3.7 and T3.8 ran before T3.1–T3.5 (blocked). They touch disjoint methods, so the resulting
   diffs are what table order would produce.
2. **T3.0b uses `typing.cast`** (runtime identity) for the two `.rowcount` reads instead of a pure annotation:
   `Session.execute` is typed `Result[Any]`, the runtime object for an INSERT is a `CursorResult`. The alternative was
   `# type: ignore[attr-defined]`; the cast is narrower and documents the real type (cf. m1 / T7.16).
3. `from typing import Any` (schemas) and `from typing import TYPE_CHECKING, Any, cast` (repository) are runtime
   imports; `typing` is always loaded, and the import-surface golden is a superset check (WP0 deviation 6 precedent).
4. `get_vacation_window` has no dedicated test (the plan gives T3.7 no new test, and implementers don't add tests in
   production commits). It is covered once T5.13 routes `routes/vacation.py` through it.

## For reviewers

- T3.0a: the six `type: ignore[type-arg]` lines — confirm no field annotation changed (OpenAPI golden is green).
- T3.6: WHERE order (caller filters first, then `id > after_id`, then LIMIT) — verified by compiled-SQL equality.

## REFACTOR_NOTES requests (I4)

- Record the T3.1–T3.5 coverage gaps above (and the out-of-order execution) until they are closed.
- Record deviation 2 (cast for `rowcount`).

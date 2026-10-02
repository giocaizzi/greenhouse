# WP3 hand-off — data types + repository (complete)

Worktree `/home/user/gh-wp3`, branch `refactor/wp3-repo`, based on `5267aa6`. Not pushed, not merged, not rebased.
Every test command ran as `PYTHONHASHSEED=<seed> flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …` (the plan
§0.3 `t` / `FULL` / `FULL_SEED2` helpers).

**Status:** all 9 tasks are done. T3.1–T3.5 first stopped on the §0.4 G.1 coverage precondition. The orchestrator then
had me add one test-only characterization commit (`c4f16e6`), and the stopped tasks resumed in table order on top of it.

## Commits

| Task | Commit | Evidence |
|---|---|---|
| T3.0a | `078ce13` strict-clean schemas | mypy 8 → 0. Validators `(cls, v: Any) -> Any`. 6 bare-`dict` fields keep their annotation with `# type: ignore[type-arg]  # contract: OpenAPI` (IrrigatorBase.config, SensorBase.config, AlertResponse.data, IrrigateResponse.stress_indicators, UpdateSensorRequest.config, UpdateIrrigatorRequest.config). `t $CORE $API $RENDER D(gc.schemas)`: **1223 passed**. |
| T3.0b | `e5736c6` strict-clean repository | mypy 16 → 0. `**fields: Any` ×9; `dict` → `dict[str, Any]` on 5 params; 2 × `cast("CursorResult[Any]", session.execute(stmt))` for `.rowcount` (CursorResult under `TYPE_CHECKING`). `t $CORE $RENDER $CHECK D(gc.repository)`: **1219 passed**. |
| T3.6 | `0450248` `_page` | Compiled SQL of old vs new is byte-identical for all 36 parameter combinations (literal binds). Subset: **649 passed**. *(Ran before T3.1–T3.5 while those were blocked.)* |
| T3.7 | `4e57e6a` `get_vacation_window` | Additive, `= self.session.get(VacationWindow, window_id)`. `t $CORE tests/test_db.py`: **62 passed**. Unblocks T5.13. |
| T3.8 | `a1dcc49` `FULL_WEEKDAY_MASK` default | `inspect.signature` default is still `127` (int). Subset: **359 passed**. |
| (doc) | `5d10bca` | Interim hand-off at the coverage stop; this file supersedes it. |
| (test) | `c4f16e6` characterization tests | New `tests/test_contract_repository_gaps.py`, 43 tests; details below. No production change. |
| T3.1 | `11c2eb6` `_parse_json_config` | Both `parse_config` methods keep their decorators and signature, and call the module function. Subset + gaps file: **648 passed**. |
| T3.2 | `a389383` `_delete_by_id` | 4 × `if not row` + 1 × `is None` → `is None`. These are equivalent: none of the 5 classes defines `__bool__` or `__len__` (checked at runtime). Subset + gaps file: **608 passed** on the identical rerun; see "Red test". |
| T3.3 | `8e5f9fd` `_patch_fields` (None-first) | vacation + irrigation windows. The helper has the full target-§3.9 signature, including `json_fields`; with its empty default, the extra test is always False and has no side effects. Subset + gaps file: **550 passed**. |
| T3.4 | `62896c4` `_patch_fields(json_fields={"config"})` | sensor + irrigator. The `plant_id` pop happens before the patch and the reassignment after it, as before. Subset + gaps file: **561 passed**. |
| T3.5 | `2083bfa` `_patch_fields_hasattr_first` | preferences, cluster, plant. B-15 preserved. Subset + gaps file: **576 passed**. |

Every commit: `ruff check` + `ruff format --check` clean on the touched file, `uv run lint-imports` 10 kept / 0 broken,
`make typecheck` green, `git status --porcelain tests/golden` empty.

## Characterization commit `c4f16e6` (orchestrator-requested)

`tests/test_contract_repository_gaps.py` uses `tmp_db`, plus `frozen_clock` where a vacation window's `created_at` is
written. It pins today's behavior for every gap in the interim hand-off, and adds direct `get_vacation_window` tests.

**Proof that the tests can fail:** I applied 18 single-line mutations to the production gap sites, one at a time in
this worktree:
- `json.loads(v)` → `v` (×2);
- not-found `return False/None` → `None/False` (×7);
- `hasattr` → `True` (×6);
- `continue` → `pass` (×2);
- `json.dumps` dropped (×1);
- `get_vacation_window` → `None` (×1).

**18/18 were killed.** Each file was restored with `git checkout --`, and the worktree was clean before and after.

**Determinism:** 43 passed in each of four runs (twice serially, at `-n 2`, and with `TZ=America/New_York`).

**`…_current_behavior` tests for REFACTOR_NOTES (I4):**
- `test_parse_config_non_object_json_fails_dict_validation_current_behavior`: a JSON string that decodes to a list,
  string or number fails with `dict_type`.
- `test_update_sensor_stores_non_dict_config_verbatim_current_behavior`: a non-dict `config` is stored as is, with no
  JSON encoding.
- `test_update_plant_ignores_unknown_keys_and_none_current_behavior`: B-15, `None` cannot clear a field.

## WP gate (on `2083bfa`)

- `t $(D gc.repository) $(D gc.schemas) $CORE $API $RENDER $CHECK tests/test_contract_repository_gaps.py`
  (seed 0, `-n 2`): **1459 passed** (7m27s).
- `FULL` (seed 0, `-n 2`): **2840 passed** (9m03s) = 2797 + the 43 new tests.
- `FULL_SEED2` (seed 12345, `-n 2`): **2840 passed** (8m59s).
- No reruns in the gate. `git status --porcelain tests/golden` is empty, `lint-imports` keeps 10 contracts, and
  `make typecheck` passes on 38 modules. `ruff check libs/ tests/` and C90/PLR at 8 are clean on both files.

## Red test (T3.2) — pre-existing flake, criterion (b)

`tests/cli/test_contract_tui_screens.py::test_screen_render_golden[search_citrus]` failed on the first T3.2 subset
run. The diff is a column-width difference in the TUI search results table; that code path does not reach `delete_*`.
- **Identical rerun:** 608 passed.
- **Parent `11c2eb6`** (change stashed, identical command): run 1 passed (608); run 2 failed with the **byte-identical**
  `search_citrus` diff.

So the flake already exists without the change, and the commit was kept. The same flake was seen by WP0 at
`refactor-gate1`. **Orchestrator:** consider adding it to `refactor/gate1/flaky-tests.txt` with this evidence.

## DoD / sizecheck / strict list / ratchet (for I1)

- **Sizecheck delta:** 0 → 0 functions listed for both files. Both keep their approved file-length entries: schemas
  999 → 1004 lines, repository 1309 → 1307 lines.
- `ruff --isolated --select C90,PLR0911,PLR0912,PLR0915 max-complexity=8`: clean on both files. Neither file has a
  per-file ignore, so I1 has no ratchet edit to make.
- **DoD for the new helpers** (body lines / CC / nesting):

  | Helper | Body lines | CC | Nesting |
  |---|---|---|---|
  | `_parse_json_config` | 3 | 2 | 1 |
  | `_page` | 4 | 3 | 1 |
  | `_delete_by_id` | 5 | 2 | 1 |
  | `_patch_fields` | 7 | 5 | 2 |
  | `_patch_fields_hasattr_first` | 3 | 3 | 2 |
  | `get_vacation_window` | 1 | 1 | 0 |

- **Strict-list additions:** `libs/greenhouse-core/greenhouse_core/schemas.py`,
  `libs/greenhouse-core/greenhouse_core/repository.py` (38 modules, `make typecheck` green).
- **Function map** (old qualname → new helper qualnames):
  - `IrrigatorResponse.parse_config`, `SensorResponse.parse_config` → same + `schemas._parse_json_config`
  - `IrrigationRepository.list_all_{sensors,irrigators,plants}` → same + `IrrigationRepository._page`
  - `IrrigationRepository.delete_{vacation_window,irrigation_window,cluster,sensor,irrigator}` → same + `_delete_by_id`
  - `IrrigationRepository.update_{vacation_window,irrigation_window,sensor,irrigator}` → same + `_patch_fields`
  - `IrrigationRepository.update_{preferences,cluster,plant}` → same + `_patch_fields_hasattr_first`
- **Mutation:** neither file is on the §0.5 list, so there is no M-pre/M-post run.
- **Proposed size-exception entries:** none.

## Deviations

1. **Task order:** T3.6–T3.8 landed before T3.1–T3.5, because those were blocked on coverage. They touch disjoint
   methods, so the diffs are the same as table order would produce.
2. **T3.0b uses `typing.cast`** (a runtime identity) for the two `.rowcount` reads instead of a pure annotation.
   `Session.execute` is typed `Result[Any]`, but the runtime object for an INSERT is a `CursorResult`.
3. **Runtime imports:** `from typing import Any` (schemas) and `from typing import TYPE_CHECKING, Any, cast`
   (repository). `typing` is always loaded, and the import-surface golden is a superset check.
4. **T3.3 defines `_patch_fields` with its final `json_fields` keyword**, as in target §3.9. T3.4 only adds the
   callers.

## For reviewers

- T3.0a: the six `type: ignore[type-arg]` lines. Confirm that no field annotation changed; the OpenAPI golden is green.
- T3.2: the `not row` → `is None` normalization (truthiness check above).
- T3.3/T3.4: per-key evaluation order in `_patch_fields`: None, then `json_fields`, then `hasattr`.

## REFACTOR_NOTES requests (I4)

- Record the three `…_current_behavior` tests above. The B-15 test is already covered by B-15; the other two
  (non-object JSON `config` fails dict validation; non-dict sensor `config` stored verbatim) are new observations.
- Record deviation 2 (the cast for `rowcount`) and the `search_citrus` flake evidence.

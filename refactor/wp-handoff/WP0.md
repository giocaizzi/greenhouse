# WP0 hand-off — tooling + shared definitions (PARTIAL: stopped at T0.9)

Worktree `/home/user/gh-wp0`, branch `refactor/wp0-tooling`, based on tag `refactor-gate1` (`2c7a655`).
Not pushed, not merged. Every test command ran as
`PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …` (the `t` helper of plan §0.3).

**Status: T0.1–T0.8 done. T0.9 stopped as the orchestrator override requires: after seeding, `make sizecheck`
lists one function that is not a §3.12 row.** T0.10 and T0.11 were not started, because the tasks run in order. The T0.9
work is parked, complete, on the side branch `refactor/wp0-t09-wip` (`8f31042`, one commit on top of `1d786bb`). Once the
decision is made, it can be cherry-picked as-is, with one register line added if needed.

## Decision needed (T0.9)

After the register is seeded with every §3.12 **EXC** row (9 function rows and 9 file rows), `make sizecheck` lists
52 functions. 51 of them are exactly the §3.12 rows whose verdict is a task and whose body is > 40 lines or whose
nesting is > 3. The 52nd is an **extra hit**:

```
libs/greenhouse-server/greenhouse_server/services/irrigation.py::schedule_pump_watcher._run   body=42 nesting=2
```

- It is the nested closure at `irrigation.py:168`.
- §3.12 says nested defs are measured separately, and it lists `register.login` that way, but the table has no
  row for `schedule_pump_watcher._run`.
- T7.19 ("`_watcher_tuning` and `_run_pump_watcher` behind a thin closure", "both functions end ≤ 40 body lines")
  would fix it in practice.
- **No exception was added.**

Options for the orchestrator:
- (a) Add a §3.12 row `services/irrigation.py::schedule_pump_watcher._run | 44/42 | ≤8 | 2 | T7.19`. Then the
  T0.9 assertion holds and the WIP commit merges unchanged, with the commit body reworded.
- (b) Approve a register entry for it.

Other §3.12 task rows that sizecheck does **not** list: these are expected, because sizecheck measures size and nesting
only, and CC is ruff's job.
- `ClusterScreen.action_delete`: body exactly 40, CC 9.
- `show_payload`, `print_stats_report`: CC only.
- `routes/operations.py::stats_export`: body 35. Its task is a consolidation, not a size fix.

Measurement note: the two Typer `register` bodies measure 65 and 106 (§3.12 says 64 and 105). The body is counted
from the first decorator of the first nested command, which is the statement's real start. Every other number
matches §3.12.

## Commits

| Task | Commit | Evidence |
|---|---|---|
| T0.1 | `db7b430` build(dev): mypy + import-linter | `uv add --group dev mypy import-linter`; **no mutmut** (override). Parsed lock diff: added ast-serialize, grimp, import-linter 2.15, librt, mypy 2.3.1, mypy-extensions, pathspec; none removed; no existing package entry changed except the root's dev metadata; `resolution-markers` gained a `3.14.*` fork. `t $CORE` 45 passed. |
| T0.2 | `e7446c3` import-linter contracts | `lint-imports`: 199 files, 904 deps, **10 kept, 0 broken** (2 + 1 ignored). `tui.render` omitted (I1 adds it). |
| T0.3 | `bb0e93c` ruff ratchet | `extend-select` C90/PLR0911/12/15, max-complexity 8. Per-file ignores over 24 files: 27 C901 (= §3.12), plus 7 PLR0912, 6 PLR0915, 5 PLR0911 (= baseline file). `tests/**` exempt. `ruff check .` and `ruff check libs/ tests/` clean. |
| T0.4 | `9b069c1` mypy ratchet | `[tool.mypy]` strict plus the boundary override; `refactor/mypy-strict.txt` = 35 seeds; Makefile `typecheck`, `lint-imports`, and `check` runs both. `make typecheck`: 35 OK. |
| T0.5 | `8fe1776` guard tests | `tests/test_refactor_guards.py`, 66 tests: green twice serially and once at `-n 2`. Negative check: one violation per guard made all 6 guard tests fail. |
| T0.6 | `3b8b27d` constants (definitions only) | All 53 §9 names. A script verified value **and** exact type for the 52 scalars, plus the month table (order, all float): 0 mismatches. `t $CORE` 45 passed. |
| T0.7 | `8735112` strict-clean plant_needs | 9 errors → 0, annotations only. Appended to the strict list (36). `t $CORE $ENGINE $RENDER D(gc.logic.plant_needs)`: **867 passed** (4m14s). |
| T0.8 | `1d786bb` `moisture_target_range` | Hypothesis equivalence (`derandomize=True`, 300 examples, NaN-aware, dict and MappingProxyType): 3 passed twice. Negative check: 2 failed. `t new $ENGINE $CORE`: **387 passed** (1m07s). |
| T0.9 | parked `8f31042` on `refactor/wp0-t09-wip` | See "Decision needed". The tool is mypy-strict, ruff clean and formatted. `make sizecheck` exits 1 while any unexcepted hit remains. |
| T0.10 | not started | — |
| T0.11 | not started | — |

Frozen safety net untouched: `git diff refactor-gate1 --stat -- tests/golden 'tests/**/test_contract_*.py'
'tests/test_contract_*.py' tests/engine_grid.py tests/test_invariants_engine.py tests/test_properties_logic.py` is empty.
The `FULL` / `FULL_SEED2` WP gate was **not** run, because WP0 is incomplete.

## Deviations from the plan (for the integrator / REFACTOR_NOTES)

1. **T0.1:** mutmut is not added (orchestrator override; Gate 1 proved it unusable on this layout).
2. **T0.3:** besides `tests/**`, `"refactor/**"` is also exempt from C90/PLR091x. The pre-commit ruff hook lints all
   files, and `refactor/gate1/mutate.py::main` (CC 15) would otherwise fail `make check`. That code is refactor
   tooling, not shipped code.
3. **T0.4:** `mypy_path` is set to the three `libs/` package roots, which §11 does not list. The workspace packages
   have no `py.typed`. Without `mypy_path`, mypy resolves them through the editable `.pth` files as missing
   packages, so with `ignore_missing_imports` **every cross-module import of an unlisted module was silently `Any`**.
   Probe: `reveal_type(constants.MIN_COOLDOWN_HOURS)` printed `Any`; with the setting it prints `int`. The 35 seeds
   are clean either way. `sizecheck` joins `make check` in T0.9, because the target does not exist earlier.
4. **T0.4 / T0.9:** `make check` will exit non-zero from T0.9 on, until I5 clears the task rows. `make sizecheck`
   lists the pending task functions. CI only runs `make coverage`, so CI is unaffected.
5. **T0.6:** `SNAPSHOT_LOOKBACK_HOURS` also exists as `greenhouse_server/services/sync.py`'s own module constant.
   It has the same value (24) and the same concept, and `test_contract_sync_service` pins it. The two names are
   independent; nothing imports across them.
6. **T0.6:** §9 cites `routes/windows.py:24`, but the literal `23` is on line 25 (line 24 is the `def`). The literal is
   identical.
7. **T0.7:** `from typing import Any` is a plain runtime import. `typing` is always loaded, so import-time
   behaviour does not change (§10.13b).
8. **T0.5:** guard 4 compares against the **nearest Textual ancestor** (Screen, ModalScreen, App, Static, Vertical,
   Widget), which is a superset of the four classes named in §11. It skips attributes Textual generates on every
   subclass, and dunders. A new DOMNode subclass fails the test. Stale allow-list entries never fail, so implementers
   never have to edit the file. The integrator shrinks the lists.

## Proposed size-exception entries

None beyond the §3.12 EXC rows seeded on the WIP branch. The extra hit above is waiting for a decision. No exception
was added for it.

## REFACTOR_NOTES requests (I4)

- Record deviations 2–5 above, especially the `mypy_path` finding: before it, default-mode and strict counts
  measured per file under-reported cross-package errors.
- §3.12 omission: `schedule_pump_watcher._run` (body 42).
- `plugin/skills/greenhouse/references/LOGIC.md`: T0.6 changes no values, so it stays correct. Keep flagging the
  CLAUDE.md-vs-BRIEF plugin-docs conflict for the PR body.

## Remaining WP0 work after the decision

T0.9 commit (cherry-pick `8f31042`, rewording the body to match the decision), then T0.10 (extend
`refactor/gate1/mutate.py`: target selection by module, operator-generated mutants with identity = operator +
original/replacement snippet + enclosing qualname, killed/survived report, in-place mutation with a
`git checkout -- <file>` restore, refuses a dirty worktree, `sync.py` in the target list, validation on
`logic/stress.py` with `$ENGINE`), then T0.11 (alias timings), then the WP gate (`FULL` at `-n 4` with seed 0 and
again with 12345), then the final hand-off.

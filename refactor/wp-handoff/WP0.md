# WP0 hand-off — tooling + shared definitions

Worktree `/home/user/gh-wp0`, branch `refactor/wp0-tooling`, based on tag `refactor-gate1` (`2c7a655`). Not pushed,
not merged. Every test command ran as `PYTHONHASHSEED=<seed> flock /tmp/greenhouse-tests.lock uv run pytest -q -n <N> …`.

**Status:** T0.1–T0.11 are done. Both full-suite gate runs are green, but the seed-12345 run passed only on its
identical rerun. One TUI test failed on the first run; see "Flaky-test decision needed".

## Commits

| Task | Commit | Evidence |
|---|---|---|
| T0.1 | `db7b430` build(dev): mypy + import-linter | Added with `uv add --group dev mypy import-linter`. **No mutmut** (override). The parsed lock diff adds ast-serialize, grimp, import-linter 2.15, librt, mypy 2.3.1, mypy-extensions and pathspec. Nothing is removed, and no existing package entry changes except the root's dev metadata. `resolution-markers` gained a `3.14.*` fork. `t $CORE`: 45 passed. |
| T0.2 | `e7446c3` import-linter contracts | `lint-imports`: **10 kept, 0 broken** (2 + 1 ignored imports). `tui.render` is left out; I1 adds it. |
| T0.3 | `bb0e93c` ruff ratchet | C90 at 8 plus PLR0911/12/15. Per-file ignores cover 24 files: 27 C901 hits (= §3.12) and 7 + 6 + 5 PLR hits (= baseline). `tests/**` is exempt. The whole repo is ruff-clean. |
| T0.4 | `9b069c1` mypy ratchet | Strict mode plus the boundary override. 35 seed modules. Makefile gains `typecheck` and `lint-imports`, and `check` runs both. |
| T0.5 | `8fe1776` guard tests | 66 tests: green twice serially and once at `-n 2`. Planting one violation per guard made all 6 guards fail. |
| T0.6 | `3b8b27d` constants | All 53 §9 names. Value **and** exact type checked against each literal: 0 mismatches. |
| T0.7 | `8735112` strict-clean plant_needs | 9 errors → 0, annotations only. The strict list now has 36 modules. Subset: 867 passed. |
| T0.8 | `1d786bb` `moisture_target_range` | Hypothesis equivalence test (`derandomize=True`) passed twice. A deliberately wrong fallback made it fail. `t new $ENGINE $CORE`: 387 passed. |
| (doc) | `06d9220` | Interim hand-off written at the T0.9 stop; this file supersedes it. |
| (doc) | `a208f55` docs: §3.12 row for `schedule_pump_watcher._run` → T7.19 | Orchestrator decision (a). The T7.19 plan row now requires bringing `_run` within the limits. |
| T0.9 | `0758bd0` sizecheck + register + `make sizecheck` | **Assertion holds:** after seeding, sizecheck lists 52 functions, which are exactly the 52 §3.12 task rows over the limits (0 extra, 0 missing; checked by parsing the table). 17 entries are excepted (8 functions + 9 files). Before seeding there were 69 hits. |
| T0.10 | `5252835` mutation tooling (extends `refactor/gate1/mutate.py`) | `stress.py` with `$ENGINE`: 66 mutants, **52 killed / 14 survived = 78.8 %**, **25.5 min** wall. Generation over all 11 targets: 1424 mutants, 0 invalid. The M-post compare was checked on synthetic data (1 regression, 1 unmatched, exit 1). Results are in `refactor/gate2/mutation/stress-wp0-validation.jsonl`. The curated catalogue still applies 473/473. |
| T0.11 | `b796b8c` `refactor/gate2/timings.md` | FULL at `-n 2`: 9m56s. Repository/schemas maps ≈ 5m45s. TUI 2m33s, PIPE 2m06s, ENGINE 1m10s, WEB 1m07s, SCHED 55s, CORE 9s. No alias is slower than estimated, so no wave re-plan is needed. |

## WP gate

- `PYTHONHASHSEED=0 … -n 4`: **2797 passed** (5m30s). That is 2728 existing tests plus 66 guard tests plus 3
  `moisture_target_range` tests.
- `PYTHONHASHSEED=12345 … -n 4`:
  - First run: **1 failed**, 2796 passed. The failure was `tests/cli/test_contract_tui_runtime.py::test_alerts_cursor_stays_on_record_across_auto_refresh` with `WorkerFailed: NoMatches('#cluster-card-2') on DashboardScreen`.
  - Identical rerun: **2797 passed** (5m33s).
- Static checks are clean: `lint-imports` 10 kept, `make typecheck` 36 modules OK, `ruff check .` and
  `ruff format --check .` clean.
- `make sizecheck` exits 1 by design: it lists the 52 task-pending functions (accepted deviation).
- `git status --porcelain tests/golden` is empty.
- `git diff refactor-gate1 --stat -- tests/golden 'tests/**/test_contract_*.py' 'tests/test_contract_*.py'
  tests/engine_grid.py tests/test_invariants_engine.py tests/test_properties_logic.py` is empty.

### Flaky-test decision needed (orchestrator-owned flaky list)

The failure meets neither flaky criterion of §0.2. It is not on `refactor/gate1/flaky-tests.txt`, and that exact
failure did not reproduce on the parent. I ran the identical command twice on `refactor-gate1` in this worktree:
- Run 1 had **a different TUI test failing**: `test_contract_tui_screens.py::test_screen_render_golden[search_citrus]`,
  a render mismatch.
- Run 2 was green: 2728 passed.

So the Gate-1 baseline is itself nondeterministic in TUI tests at `-n 4`. The failing case looks like a race:
pressing `a` leaves the dashboard while its load worker is still querying `#cluster-card-2`. WP0 changes no TUI or
production code that this path touches. Its production edits are constants definitions, plant_needs annotations and
one new helper, so **I did not revert**.

Request: add both node ids to the flaky list, with this evidence, or decide otherwise. This matches the TUI flake
REFACTOR_NOTES already mentions.

## Deviations (accepted by the orchestrator; keep in REFACTOR_NOTES)

1. **T0.1:** mutmut is not added. **T0.10:** there is no `[tool.mutmut]`. The tool is `refactor/gate1/mutate.py`,
   plus the thin wrapper `refactor/scripts/mutate_probe.py`.
2. **T0.3:** `"refactor/**"` is exempt from C90/PLR091x. The pre-commit ruff hook lints all files, and
   `mutate.py::main` would otherwise fail it.
3. **T0.4:** `mypy_path` is set to the three `libs/` roots. Without it, the workspace packages (no `py.typed`) resolve
   as missing modules, so every cross-module import was silently `Any`.
4. **T0.9:** `make check` now includes `sizecheck` and stays red until I5. CI runs only `make coverage`.
5. **T0.6:** `SNAPSHOT_LOOKBACK_HOURS` also exists in `services/sync.py`, with the same value (24) and meaning; the
   two names are independent. The §9 site `routes/windows.py:24` is actually line 25.
6. **T0.7:** the `from typing import Any` runtime import is harmless, because `typing` is always loaded.
7. **T0.5:** guard 4 compares each class against its **nearest Textual ancestor**, a superset of the four bases §11
   names. Stale allow-list entries never fail; the integrator shrinks the lists.
8. **T0.9:** the Typer `register` bodies measure 65 and 106 (§3.12 says 64 and 105), because a decorated first
   statement starts at its decorator. Verdicts are unaffected.

## Mutation tooling notes (for M-pre / M-post)

- **Usage:**
  - `uv run python refactor/gate1/mutate.py --targets` lists the targets, including core `sync.py`.
  - `--module <m> [--tests "…"] [--function q] [--lines A-B] [--sample 150 --seed 0] --results X.jsonl` runs one
    module.
  - M-post: `--module <m> --summary --results post.jsonl --compare pre.jsonl --map fmap.json`. It exits 1 if the
    kill rate is below 75 % or any killed-in-pre identity now survives.
- **Identity:** `operator | original -> replacement | qualname[#n]`. Constants carry their enclosing expression.
  - "Replace literal" tasks (e.g. `20` → `STRESS_HUMIDITY_DEFICIT`) legitimately turn pre identities into
    **UNMATCHED**, which is listed separately and is not a regression. Reviewers should check those by hand.
- **Budget:** big targets (engine 412, irrigation 288 mutants) exceed 45 minutes. Narrow them with
  `--function`/`--lines` from the safety docs, or use `--sample 150`.
- **stress.py M-pre survivors** (WP6, orchestrator-owned, with `$ENGINE`, which excludes the gap-test file):
  - boundary flips on `avg_soil < SOIL_MOISTURE_LOW`, `min_lux_needed > 0`, `avg_light < seasonal_min * 0.4`,
    `avg_soil_moisture > SOIL_MOISTURE_SATURATED` and `soil_moisture_delta < -10` (and its ±1);
  - `hum_range[0] - 20` → `- 21`;
  - ±1 on the `ideal_light_lux_min` default, on `max(..., default=0)` and on `min_lux_needed > 0`.
- **Restore:** the runner mutates in place. Never edit or commit in a worktree while a run is active.

## Proposed size-exception entries

None. The register contains only the §3.12 EXC rows.

## Strict list / ruff ignores (for I1)

`refactor/mypy-strict.txt` gained `libs/greenhouse-core/greenhouse_core/logic/plant_needs.py`. No file left the ruff
per-file-ignore list; WP0 refactored no complex functions.

## REFACTOR_NOTES requests (I4)

- Record deviations 1–8.
- Record the §3.12 addition (`schedule_pump_watcher._run` → T7.19).
- Record the TUI nondeterminism seen at `refactor-gate1` (`search_citrus` render, `alerts_cursor` worker race).
- `plugin/.../LOGIC.md`: T0.6 changes no values, so it needs no change. Keep flagging the CLAUDE.md-vs-BRIEF
  plugin-docs conflict for the PR body.

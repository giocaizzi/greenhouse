# FP-U hand-off — fix pass, CLI + TUI (`refactor/47-fix-pass.md` § FP-U)

Worktree `/home/user/gh-fp-u`, branch `refactor/fp-u`, base `8a76a3f`. Not pushed, not merged, not rebased.
Scope kept to `libs/greenhouse-cli/**`, `tests/cli/**`, plus the allowed shared files: `refactor/mypy-strict.txt`
(path update), `refactor/size-exceptions.txt` (removals only), `pyproject.toml` `[tool.ruff]` per-file ignores
(removals only), `REFACTOR_NOTES.md` (B-U1), and `refactor/gate1/mutate.py` (snippet indentation, see 3623a7b).
Every pytest run: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …`.

## Commits

| # | Commit | Item | What / evidence |
|---|---|---|---|
| 1 | `67e0da0` refactor(tui) | 1 (prep) | `vacation_rows` passes `with_date=True` by keyword; `scheduler_panel_rows(*, paused, health)` keyword-only (one call site). Drops `tui/render.py` ruff ignore `[FBT001, FBT003]`. |
| 2 | `b16f6f5` refactor(tui) move | 1 | `tui/render.py` (396 lines) → package `tui/render/`: `cluster_tabs`, `cluster_panels`, `system`, `settings`, `_rows` (`Row`). `__init__` re-exports all 21 builders + `Row` via `__all__`. `ast.dump` of all 22 functions identical before/after. tests/cli 597 passed. |
| 3 | `7f6e825` refactor(cli) move | 3 | Command closures in `operations.register` (10), `auth.register` (3), `tui.register` (1) → module level; `register()` applies `app.command()` / `app.command("stop-all")` in the same order. Removes size exceptions `commands/auth.py::register`, `commands/operations.py::register` and ruff ignores `auth.py [PLR0915]`, `operations.py [C901, PLR0915]`. |
| 4 | `3623a7b` chore(refactor) | 3 | `mutate.py` cli-18..22 re-indented for the hoisted functions; re-run → all 5 KILLED; `--check` INVALID set = base (80). |
| 5 | `a8826a8` refactor(cli) | 2 | Shared aliases in `commands/_helpers.py`: `ClusterArg` ×12, `ClusterOpt` ×9, `YesOpt` ×7, `ClusterFilterOpt` ×2. Typer copies the annotated `ParameterInfo` per parameter (`typer/utils.py`: `parameter_info = copy(parameter_info)`), so a shared alias is safe. |
| 6 | `4ded0e6` refactor(cli) | 2 | Module aliases: `configs.py` 8 (`config set` / `config global set`), `irrigators.py` 4 (add/update), `plants.py` 3 (add/update). `irrigator --type` left inline on purpose (OD3). Help goldens + `params.json` identical. |
| 7 | `0d7fd6f` test(tui) | 4 | Strict xfail `tests/cli/test_tui.py::TestSpriteView` (`animate=True/False`) + REFACTOR_NOTES **B-U1**. With `--runxfail`: 2 failed, `TypeError: 'bool' object is not callable` (`textual/widget.py:2544`). |
| 8 | `589b176` **fix(consistency)** | 4 | `SpriteView` flag `_animate` → `_animated`; xfail marker removed → 2 passed; the `type: ignore` is gone. REFACTOR_NOTES: B-U1 marked fixed + entry under "Labeled behavior changes landed". tests/cli 665 passed. |
| 9 | `cc0d37e` refactor(cli) | 5 | `IrrigationClient.__enter__/__exit__` (closes `self.http`); `call()`, `login`, `logout` use `with`. **No public `close()`** — `test_every_client_method_has_a_case` and `test_tui_reaches_every_client_capability` pin "public method = endpoint" (a first attempt with `close()` turned both red; not committed). New `TestClientLifecycle` (2 cases): failed on the parent, passes after. **TUI path unchanged**: it keeps one client per session and calls it from worker threads (`asyncio.to_thread`); closing on re-login/logout could cut an in-flight request, so `_login`/`_logout` still just swap the client. |
| 10 | `628ba9f` refactor(cli) | 5 | `call(ctx, fn: Callable[[IrrigationClient], T]) -> T` (unused `*args/**kwargs` dropped). The two `lambda c, cid=cl["id"]: …` loops (plant/sensor list) → `operator.methodcaller("list_plants"/"list_sensors", cl["id"])` (mypy cannot infer a defaulted lambda against the generic). |
| 11 | `905dd26` refactor(cli) | typing | `-> None` on all 62 Typer commands + `main.main`. |
| 12 | `d97bfd8` docs(tui) | 6 | 54 why-docstrings for the `_private` helpers the lint pass left (pydocstyle skips them); 4 weak docstrings sharpened (`fmt.num`, `fmt.styled`, `get_client`, `output`). Left undocumented on purpose: `__init__` (D107) and nested `_after` dialog callbacks. |
| 13 | `9c229ad` docs(tui) | doc drift | `ConfirmScreen` docstring lists what it guards and what bypasses it; `test_contract_tui_actuation.py` module docstring no longer cites CLAUDE.md's old claim (comment only). |
| 14 | `a8c6225` **docs(cli) doc-contract** | doc drift | `windows list` help: "Empty list = every hour allowed (quiet hours still apply)." Regenerated ONLY `tests/golden/cli/help/greenhouse.windows.list.txt`, `greenhouse.windows.txt` (group listing now wraps to two lines) and `tests/golden/cli/params.json` (one `help` string). `params.json` with every `help` key stripped: identical before/after. |
| 15 | `fc2fb4b` docs(cli) | stale ids | "pinned" wording out of two comments added in this pass. |

Stale refactor ids in CLI production code: only two existed (`render.py` docstring "golden-pinned", `widgets.py`
"(pre-existing, kept)"); both gone with commits 2 and 8. `grep -rniE "pinned|golden|refactor|wave|gate1|\bWP[0-9]|\bOD[0-9]|\bB-[0-9]" libs/greenhouse-cli` → only domain words ("gate" = quiet-hours gate).

## Gates (at `fc2fb4b`)

- `uv run ruff check libs/ tests/` → clean; `uv run ruff format --check libs/ tests/` → 328 files formatted.
- `make typecheck` → no issues in 155 files; `uv run lint-imports` → 10 kept, 0 broken.
- `uv run python refactor/scripts/sizecheck.py` → exit 1 with the same 8 non-CLI hits as the base (constants.py 402,
  charts.py 401, engine ×4, sync ×2 — not FP-U files). CLI hits: only registered ones (`client.py`, `screens/cluster.py`
  file + `compose`, `screens/forms.py::FormScreen.compose`). `screens/cluster.py` grew 671 → 700 lines (docstrings;
  file already excepted).
- `mutate.py --check` INVALID set identical to the base (80 entries).
- Full suite (once, under the lock, log `full.log` in the session scratchpad):
  `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2` at `fc2fb4b` →
  **3043 passed, 1 failed** in 525 s. The failure: `tests/cli/test_tui.py::TestResilience::test_auto_refresh_polls_again`
  raised at `run_test` exit with `WorkerFailed: NoMatches("No nodes match '#cluster-grid' on DashboardScreen()")` —
  an auto-refresh (`refresh_seconds=0.2`) dashboard `load` worker still running while the app tears down its DOM
  (a teardown race, not the test's `len(hits) >= 3` assertion).
  Reruns: the test alone 8/8 green; 25/25 green under 3 CPU burners. FP-U touched nothing on that path
  (`DashboardScreen` / `GreenhouseApp` / `DataScreen` changed only by docstrings; the TUI client path is unchanged).
  Not in `refactor/gate1/flaky-tests.txt` and not reproduced on HEAD either, so it does not meet the flaky criteria:
  **reported, not dismissed** — suggested fix (TUI, separate commit): the auto-refresh `reload` / `load` should no-op
  once the screen is unmounting (e.g. guard on `self.is_attached` or cancel the interval in `on_unmount`).
  Test count: 3044 collected = base 3040 (integration gate log after the lint merge) + 2 TestSpriteView + 2 TestClientLifecycle.

## Ratchet

- Size exceptions removed: `commands/auth.py::register`, `commands/operations.py::register`. None added.
- Ruff per-file ignores removed: `tui/render.py [FBT001, FBT003]`, `commands/auth.py [PLR0915]`,
  `commands/operations.py [C901, PLR0915]`. Remaining CLI entries were probed one code at a time: each still fires.
- `refactor/mypy-strict.txt`: `tui/render.py` **replaced** by `tui/render/{__init__,_rows,cluster_panels,cluster_tabs,settings,system}.py`
  (the old path no longer exists; this is a path move, not a removal of coverage).

## For the orchestrator / integrator

1. **import-linter**: no contract change needed. "TUI view-model modules stay widget-free and I/O-free" lists
   `greenhouse_cli.tui.render`; with import-linter's default `as_packages` it covers every `render.*` submodule —
   probed by adding `import textual` to `render/system.py` → contract BROKEN (reverted).
2. **mypy override** (FP-S owns the block, so not edited here): `greenhouse_cli.commands.*` and `greenhouse_cli.main`
   can leave the relaxed `[[tool.mypy.overrides]]` list — probe with both removed: `uv run mypy libs/greenhouse-cli`
   → no issues in 44 files.
3. **Plugin doc follow-up** (out of FP-U scope): `plugin/skills/greenhouse/references/CLI.md:143` says the
   `windows list` help text is stale — drop that clause now that `a8c6225` landed.
4. **Possible further doc drift (not changed, needs a doc-contract decision)**: `greenhouse tui --help` says
   "irrigate / water-now / stop / check / sync actions behind confirmation dialogs" — sync (`S`) runs with no dialog
   and irrigate / water-now use their own dialogs (actuation golden). Would regenerate `greenhouse.tui.txt` +
   `params.json`.
5. `irrigator add/update --type` help untouched (OD3 / after WP8), as instructed.
6. Pre-existing test warning (unchanged by FP-U): `RuntimeWarning: coroutine 'ClusterScreen._load_plant_health'
   was never awaited` in a few TUI tests (worker cancelled at teardown).

## REFACTOR_NOTES

- **B-U1** added (observed bugs) and marked fixed; FP-U line added under "Labeled behavior changes landed".

## Review hardest

- `589b176` (labeled behavior change): only `Widget.animate()` on a sprite changes (TypeError → animates); no caller.
- `cc0d37e`: the CLI now closes its `httpx.Client` after each call (not observable in output); TUI deliberately untouched.
- `a8c6225`: doc-contract golden diff — description text only.
- `b16f6f5`: `greenhouse_cli.tui.render` is now a package; `render.<builder>` call sites unchanged.

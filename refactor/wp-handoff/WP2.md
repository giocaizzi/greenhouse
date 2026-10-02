# WP2 hand-off — TUI (wave C)

Worktree `/home/user/gh-wavec`, branch `refactor/wave-c-cli-tui` (WP1 commits below WP2's). Not pushed, not merged.
Per-task subset: `t $TUI tests/cli/test_contract_wp2_gaps.py tests/test_refactor_guards.py` at `-n 2`, seed 0, under
the lock. Logs: session scratchpad `…/scratchpad/wavec/t2.*.log`.

## Commits

| Task | Commit | Evidence |
|---|---|---|
| gaps (test-only) | `001c280` WP2 coverage-gap pins | 8 tests plus new goldens `tests/golden/cli/wp2_gaps/`. 20/20 temporary in-place mutations killed. Deterministic serially (×2), at `-n 2` and with `TZ=America/New_York`. |
| T2.0 | `e12abf7` strict-clean model/widgets/cluster/system/settings | 48 → 0 mypy errors. 201 passed. |
| T2.1 | `e3f84c6` `tui/render.py`: `plant_rows`/`sensor_rows` | 201 passed. |
| T2.2 | `0c72f2a` decision/history/config/window rows | 201 passed. |
| T2.3 | `39502bd` `irrigator_info`, `decision_panel`, `forecast_rows` (+ `_next_water`) | 201 passed. `_render_overview` is within limits. |
| T2.4 | `b9b8ca4` insights/stats/efficacy/learn builders | 201 passed. `_load_insights` is within limits. |
| T2.5 | `6d41a88` `action_new` dispatch | 201 passed. |
| T2.6 | `0be381b` `action_edit` dispatch | 201 passed. |
| T2.7 | `e3b8cad` `action_delete` dispatch | 201 passed. |
| T2.8 | `98c7f19` `SystemScreen.load` builders | 201 passed. |
| T2.9 | `db6a844` `SettingsScreen.load` builders | 201 passed. `render.py` is 398 lines. |
| T2.10 | `29cd35f` `model.summarize` helpers | 201 passed. |
| T2.11 | `d1deb98` `MetricChart._draw_event_lines` / `_set_x_ticks` | 201 passed. |
| review condition (test-only) | `5cd26fe` `tests/cli/test_contract_tui_search_debounce.py` | Condition from the second review of `5faca04`. Green ×2. With the debounce sleep removed: **red**. Restored with `git checkout -- search.py`: green. 5/5 green under 4 CPU hogs at `-n 4`, run together with the screens file. Log: `debounce-proof.log`. |

Every task also passed: ruff check + format, `lint-imports` (10 kept), `make typecheck` (71 modules), and an empty
`git status --porcelain tests/golden`. The 7 test warnings (`_load_plant_health` "never awaited") predate WP2; T2.0
shows the same count.

## WP gate (sprint mode: union + `$CORE` + `$TUI` + `$RENDER` (+ `$CLI`), then one `FULL` at seed 0)

- **Static:**
  - `ruff check libs/ tests/` and `ruff format --check`: clean.
  - `lint-imports`: 10 kept. `make typecheck`: 71 modules OK.
  - C90/PLR at 8 `--isolated`: clean on all WP files.
  - `git status --porcelain tests/golden`: empty.
- **Union** (`$TUI` + gaps + debounce + guards + `$CORE` + `$RENDER` + `$CLI`): **1067 passed**.
- **`FULL` at seed 0, `-n 2`:** **2897 passed** (9m47s).
- **Red, then green on the identical rerun. Cause found and removed; not flaky.** On run 1, both the union and FULL
  failed on one test, `test_contract_packaging::test_package_data_matches_golden`.
  - Cause: a stray `libs/greenhouse-cli/greenhouse_cli/tui/.mypy_cache/` left by my running `mypy` from inside the
    `tui` directory during T2.0. It is untracked and gitignored, but the package-data scan picks it up.
  - Fix: I deleted the cache, then re-ran the identical gate.
  - Logs: `wp2-gate-run1.log` (red) and `wp2-gate.log` (green).
  - **Integrator:** never run `mypy` with a cwd inside `libs/`.

## Function map (old qualname → new)

- `ClusterScreen._render_plants`: loop → `render.plant_rows`.
- `ClusterScreen._render_sensors` → `render.sensor_rows`.
- `ClusterScreen._load_decisions` → `render.decision_rows`.
- `ClusterScreen._load_history` → `render.history_rows`.
- `ClusterScreen._load_config` → `render.config_rows`, `render.window_rows`.
- `ClusterScreen._render_overview` → `render.irrigator_info`, `render.decision_panel`.
- `ClusterScreen._load_forecast` → `render.forecast_rows`.
- `cluster._next_water` → `render._next_water`.
- `ClusterScreen._load_insights` → `render.insights_text`, `render.stats_rows`, `render.efficacy_rows`,
  `render.learn_report`.
- `ClusterScreen.action_new` → `_new_plant`, `_new_sensor`, `_new_window`, `_attach_irrigator`.
- `ClusterScreen.action_edit` → `_edit_plant`, `_edit_sensor`, `_edit_window`, `_edit_irrigator`, `_edit_config`.
- `ClusterScreen.action_delete` → `_delete_plant`, `_delete_sensor`, `_delete_window`, `_detach_irrigator`.
- `SystemScreen.load` → `render.scheduler_panel_rows`, `render.job_rows`, `render.device_rows`, `render.quality_rows`.
- `SettingsScreen.load` → `render.account_line`, `render.preference_rows`, `render.global_config_rows`,
  `render.vacation_rows`.
- `model.summarize` → `model._band`, `model._present`, `model._moisture_by_plant`, `model._plant_views`,
  `model._driest`.
- `MetricChart.show_payload` / `show_overlay` → `MetricChart._draw_event_lines`, `MetricChart._set_x_ticks`.

There are no mutation-list modules in WP2, so no M-pre or M-post run was needed.

## Sizecheck / complexity

- **Before:** 7 hits (`summarize`, `_render_overview`, `_load_insights`, `action_new`, `action_edit`,
  `SystemScreen.load`, `SettingsScreen.load`) plus the registered `cluster.py` file entry and `compose`. The C90 hits
  were `summarize` 12, `_render_overview` 11 (PLR0912 14), `action_edit` 10, `action_delete` 9 and `show_payload` 9.
- **After:** only the two registered entries remain: `cluster.py` file at 640 lines (≈ 560 expected; the per-tab
  handlers add the difference) and `ClusterScreen.compose`. Ruff C90/PLR at 8 `--isolated` is clean on all 6 files.
- **Ratchet for I1:** drop the per-file ignores for `tui/model.py` (`C901`), `tui/screens/cluster.py` (`C901`,
  `PLR0912`) and `tui/widgets.py` (`C901`). I1 should also add `greenhouse_cli.tui.render` to the TUI view-model
  import contract. `render.py` imports only `rich`, `tui.formatting` and, under `TYPE_CHECKING`, `tui.model`.
- **Strict additions:** `tui/model.py`, `tui/widgets.py`, `tui/screens/{cluster,system,settings}.py`,
  `tui/render.py`.
- **Proposed size exceptions:** none new.

## Deviations / reviewer focus

1. **T2.10 signatures differ from the target §8 sketch.**
   - `_band(chart)`, because the band comes from the chart threshold, not from the plants.
   - `_plant_views(plants, moisture_by_plant, band)`.
   - The single sensor pass became per-metric list comprehensions over the same readings: same members, same order,
     so the min/mean/max results are identical.
2. **T2.0:**
   - `self.gh.action_refresh()` replaces `self.app.action_refresh()`. `gh` returns `self.app`, so it is the same
     call.
   - `KeyValue.show` now takes a `Sequence` instead of a `list`.
   - One `# type: ignore[assignment]` on `SpriteView._animate`, which shadows `DOMNode._animate`. That shadowing
     predates WP2 and is on the guard allow-list.
3. **T2.3:** the if/else ladders in the moved builders became guard clauses that return the identical `Text`.
4. **T2.5:** ruff format removed redundant parentheses around the `add_window` lambda. Formatting only.
5. **New module-level names:** `render.Row` (type alias, not UPPERCASE), `render.account_line` (target §8 called it
   `account_rows`; it returns one markup string).
6. **Test-only commits (second reviewer):** `5faca04` (WP1 hand-off), and `5cd26fe` (debounce) for the review
   condition. Once both are approved, the orchestrator can remove the `search_citrus` line from
   `refactor/gate1/flaky-tests.txt`.

## Dead code

None in WP2 files. Every function and class in the 6 files is referenced besides its definition (grep over `libs/`,
`tests/`, `plugin/`, `*.tcss`; handlers and actions excluded per rule 5).

## REFACTOR_NOTES requests

- `SpriteView.__init__` assigns a bool to `self._animate`, shadowing Textual's `DOMNode._animate` (`BoundAnimator`).
  Calling `SpriteView.animate()` would break. Observed and **not fixed**.
- Search column widths are a high-water mark; see `WP1.md`.

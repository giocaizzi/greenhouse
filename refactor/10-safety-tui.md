# Phase 1 safety net — TUI (`greenhouse tui`), gap items G12 + G13 + G14

New files only:

- `tests/cli/test_contract_tui.py` — shared kit + static surface + sprites (4 tests)
- `tests/cli/test_contract_tui_screens.py` — screen renders, ids, tcss selectors (24 tests)
- `tests/cli/test_contract_tui_actuation.py` — key → dialog → request table (2 tests)
- `tests/cli/test_contract_tui_runtime.py` — G13 threads, G14 cursors, 401 sign-in (6 tests)
- goldens under `tests/golden/tui/`: `surface.json`, `ids_and_selectors.json`, `sprites.json`, `actuation.json`,
  `screens/*.txt` (22 renders)

I did not touch any production file, fixture, conftest or existing test. `tests/cli/test_tui.py`,
`tests/cli/tui_fixtures.py` and `tests/server/conftest.py` are imported but not edited. Nothing is committed.

## What is pinned

| Test | Contract / invariant |
|---|---|
| `test_tui_static_surface_golden` → `surface.json` | Classes are found by walking every module under `greenhouse_cli.tui` and keeping every `DOMNode` subclass defined there: the app, `DataScreen`, 6 screens, 6 modals and 7 widgets. For each class: its direct bases; its own `BINDINGS` in declaration order, with tuples normalized to `Binding` (key, action, description, show, priority); a sha256 of `DEFAULT_CSS`; every other upper-case class attribute (`CSS_PATH`, `MODES`, `DEFAULT_MODE`, `TITLE`, `AUTO_REFRESH`, `RAMP`, `DAYS`, …); `can_focus`; the sorted set of own `action_*` / `on_*` / `_on_*` / `watch_*` methods; and each `@on(...)` handler's message type and selectors. Also pinned: the upper-case constants assigned at the top of each tui module (the names are read from the AST, so re-imported names are left out), and every `resources.*_fields()` form spec (name, label, kind, default, options, required, placeholder). |
| `test_every_screen_and_widget_class_is_in_the_surface` | Guards against a silently empty discovery walk. |
| `test_app_css_path_and_modes` | Readable assert: `CSS_PATH == "app.tcss"`, the five modes, `DEFAULT_MODE`. |
| `test_sprite_mood_frame_mapping_golden` → `sprites.json` | Pixel rows and palette for every category (6), mood (6) and frames 0–5, which covers every sway, sparkle and falling-leaf position. Also: watering frames 0–2, the logo, the watering can (still and pouring 0–1), `MOOD_LABELS` / `MOOD_COLORS`, `mood_for` at the band edges (default band and 55–80), `normalize_category` aliases. |
| `test_screen_render_golden[<name>]` ×22 → `screens/<name>.txt` | Plain-text export of the screen at 120×40, background screens included under a modal. The export is the same as `App.export_screenshot` but writes text instead of SVG. Covered: dashboard; the confirm dialog behind `X`; the new-cluster form; search for "citrus"; alerts, activity, system and settings; cluster 1 on each of its 9 tabs; the irrigate and water-now dialogs; cluster 2 (outdoor) and cluster 3 (sensor-only) on Overview; the login dialog over a 401 dashboard. |
| `test_tour_covers_exactly_the_named_screens` | The tour reached every named state. |
| `test_ids_and_selectors_golden` → `ids_and_selectors.json` | For each mounted screen, the sorted unique `Type#id.class` of every node that has an id or a non-internal class (ids and classes starting with `-` are left out). Also: the `app.tcss` selectors in file order, and the tcss `#id` / `.class` tokens that match no mounted widget. Today that list is `["#global-config"]`. |
| `test_actuating_keys_golden` → `actuation.json` (65 rows) | One row per key press, covering every write-producing key on dashboard, cluster (on each contextual tab), alerts, system and settings. Each row records: the dialog that opened (`ConfirmScreen` / `IrrigateScreen` / `WaterNowScreen` / `FormScreen` / `null`) and its text (message, labels, form fields and prefill); what was filled in; and, after confirming or submitting, the exact mutating requests `(method, path, json)` plus every toast `(severity, message)`. Also included: the "no dialog, no request" branches (Overview `n` when the cluster already has an irrigator, `w` with no irrigator, `u` / `del` on an empty table, `n` / `u` / `del` on the read-only tabs, deleting a built-in scheduler job) and read-only keys (`m`, `[`, `]`, `f`, `E`). |
| `test_dialog_kind_per_actuating_key` | Readable subset of the table: dashboard `c` / `X` open Confirm, `n` opens Form, `S` opens nothing; system `p` (pause) opens Confirm; `p` (resume), `S`, `P`, `H` and `del` on a built-in job open nothing. |
| `test_client_calls_run_off_the_event_loop_thread` (G13) | Every public `IrrigationClient` method is wrapped to record `threading.get_ident()`. The test then tours dashboard, alerts, activity, system, settings, cluster (insights and plants tabs), a plant-DB sync and search. No call ran on the event-loop thread. At least these methods ran: `list_clusters`, `status`, `list_alerts`, `list_activity`, `system_health`, `insights`, `search`, `sync_plants`. |
| `test_alerts_cursor_stays_on_record_across_auto_refresh` (G14) | Uses the real `set_interval` auto-refresh at 0.5 s. Row 1 (alert `1`) is selected, then a new alert is inserted server-side under the fixture's request lock. After the refresh the same key is still selected, and its row index moved 1 → 2. Today's ordering `["3","2","1"]` is pinned. |
| `test_plants_cursor_stays_on_record_across_refresh` (G14) | Plant `2` is selected, then a plant that sorts above it is added. `r` triggers the same `reload()` the timer calls; a fast timer keeps cancelling the heavy cluster load before it finishes. Plant `2` stays selected at its new row 2 (rows `["6","1","2"]`, sorted by species). |
| `test_activity_cursor_current_behavior_resets_to_top_on_refresh` | **Current behavior** (see below): an Activity refresh puts the cursor back on row 0. |
| `test_401_opens_one_sign_in_dialog_then_logs_in` | Covers what `test_tui.py::TestLogin` did not pin exactly. On startup both requests get 401, and exactly one `LoginScreen` opens (the `_login_open` guard). No error toast is shown, and only `/clusters` and `/health/system` are requested. The login sends `POST /api/v1/auth/login` with the body `{"username","password"}` exactly. Then: the toast `Signed in as test-admin`, the token file is written, and the dashboard comes back. |
| `test_401_current_behavior_dashboard_says_cannot_reach_server` | **Current (buggy) behavior** (see below). |

## Determinism measures

- The clock is frozen at `FROZEN_INSTANT` before `seed_greenhouse` runs. Function tests use the `frozen_clock` and `clean_env`
  fixtures. The screen tour is module-scoped, so it uses `time_machine.travel` plus a module-scoped twin of `clean_env`
  (`_hermetic_env`: clears the app env vars, `TZ=UTC`, tmp cwd/HOME/XDG). The tour runs once per module, or once per xdist worker.
- Every app gets `install_offline_weather`. The device gateway stays `None` and devices are faked by `_make_stubbed_app`.
- `GreenhouseApp(animations=False)` is what `greenhouse tui --no-animation` passes. Renders use a fixed `run_test(size=(120, 40))`;
  actuation uses `(160, 50)` so that every dialog button is clickable.
- The only waits are on conditions: `_settle` from `test_tui.py`, or for the auto-refresh tests a condition poll with plain
  `asyncio.sleep`, because `pilot.pause()` waits for an idle screen that a ticking timer can prevent. No fixed-count pauses,
  apart from the 0.4 s search debounce wait that `test_tui.py` also uses.
- Normalization:
  - Screen lines are right-stripped (whitespace only, which keeps the pre-commit `trailing-whitespace` hook happy).
  - In `actuation.json`, the `E` export toast's absolute path is changed: the random pytest tmp cwd becomes `<CWD>`.
  - Nothing else is normalized. No version string appears in any TUI golden.

## Not pinned (and why)

- **Colours and styles.** The text export drops them: the active-tab highlight, focus border colours, severity colours.
  The ids/classes and tcss selector goldens are the style contract instead.
- **Textual's inherited default bindings** (ctrl+q, ctrl+p palette, tab focus). Only each class's own `BINDINGS` are in
  `surface.json`. The footer line in every render does show the `^p palette` hint.
- **Library-version coupling.** Renders, `bases` (e.g. `textual.widgets._static.Static`) and a few Textual-internal ids in
  `ids_and_selectors.json` (`tabs-scroll`, `tabs-list`, `tabs-list-bar`) depend on the locked Textual 8.2.8, plotext 5.3.2
  and rich 14.3.3. A dependency bump means regenerating the goldens in a separate commit, never as part of a refactor commit.
- **System screen jobs table.** It reads the process-global APScheduler. It is reset by every `create_app`
  (`init_scheduler` → `remove_all_jobs`), so it is deterministic unless an earlier test in the same process leaves the
  scheduler *running*. In that case `next_run_time` would appear and the `system` render would differ.
- **Animated frames inside live widgets.** Animations are off; the frames themselves are pinned in `sprites.json`.
- **Already pinned in `test_tui.py`, so not duplicated:** activity pagination cursor, login-failure re-prompt, deleting an
  ad-hoc scheduler job (mock server), server-error toasts, the empty install, and `TestTuiCommand` flag wiring.
- **Read-path GETs per key.** Only mutating requests are recorded. Reload GETs run concurrently in worker threads, so their
  log order is not deterministic.
- **Cross-session coupling in `actuation.json`.** The five sessions share one seeded app in order: dashboard `c` actuates,
  which is why cluster `i` / `c` report cooldown, and why alert ids 6/7 come from alerts that check raised. The whole chain
  runs under the frozen clock and is deterministic.

## Bugs / observations (not fixed — for REFACTOR_NOTES.md)

1. **401 renders as "Cannot reach the server — press r to retry."** `GreenhouseApp.api` returns `None` on 401, and
   `DashboardScreen.load` treats `None` as unreachable, so the message behind the sign-in dialog is wrong. Pinned by
   `test_401_current_behavior_dashboard_says_cannot_reach_server` and `screens/login.txt`.
2. **Dead CSS.** `app.tcss` styles `#global-config`, but no widget has that id (the settings panel is `#global-panel`).
   Pinned in `ids_and_selectors.json` → `tcss_tokens_without_a_mounted_widget`.
3. **The Activity table does not keep its cursor across refresh.** `ActivityScreen.load` calls `table.clear()` and
   re-adds rows instead of using `refill`. Pinned by `test_activity_cursor_current_behavior_resets_to_top_on_refresh`.
   It is harmless today because Activity rows have no row actions.
4. **Doc mismatch.** CLAUDE.md ("every actuating key goes through `ConfirmScreen`") and the `ConfirmScreen` docstring say
   the same thing, but it is not what happens:
   - `i` and `w` open `IrrigateScreen` / `WaterNowScreen`.
   - `S` (dashboard and system), `P` (cluster and system), `H`, alert `k` / `v` / `y` and scheduler resume run with no
     dialog at all.

   `actuation.json` pins the current behavior.

## Determinism evidence

Every run below covered all four new files (36 tests) and was green. I took a sha256 over all of `tests/golden/tui`
(`334309a3…`) before and after the sequence, and it did not change.

| Run | Command | Result |
|---|---|---|
| Sequential, 3× | `uv run pytest tests/cli/test_contract_tui*.py` | 36 passed each time (101–123 s) |
| xdist | `-p xdist -n 2` | 36 passed |
| Other TZ | `TZ=America/New_York` | 36 passed (the fixtures and the tour's `_hermetic_env` force UTC) |
| With existing TUI tests | the new files mixed with `tests/cli/test_tui.py`, `-p xdist -n 2` | 127 passed |
| Lint | `ruff check`, `ruff format --check` | clean |

Flakiness found and fixed while writing the tests:

- The G14 tests first polled with `pilot.pause(0.05)` at a 0.3 s refresh. That timed out about 1 in 3 runs
  (`WaitForScreenTimeout`), because a ticking refresh never lets the screen go idle.
- They now poll with plain `asyncio.sleep` at a 0.5 s refresh, and the plants case triggers the reload with `r`.

The only warning is the known `RuntimeWarning: coroutine 'ClusterScreen._load_plant_health' was never awaited`, which was
already there.

## Production lines these tests guard (mutation targets)

- `tui/app.py`: `BINDINGS`, `MODES` / `DEFAULT_MODE` / `CSS_PATH`; `api()`, specifically `asyncio.to_thread`, the 401 →
  `prompt_login` branch and the `quiet` toast suppression; the `prompt_login` `_login_open` guard; `_login` (`store_token`,
  `client_factory(token)`, the `Signed in as` toast, `action_refresh`); `action_search` / `_open_cluster`; `action_refresh`.
- `tui/screens/base.py`: `on_mount` auto-refresh interval; `confirm_then`, `form_then`, `act` (toast and reload only when
  the result is not None).
- `tui/screens/dashboard.py`: `load` ("Cannot reach…"), `_render_cards` (empty text, focus restore), the four actions and
  their toast lambdas.
- `tui/screens/cluster.py`: the `action_new` / `action_edit` / `action_delete` tab dispatch and its notify fallbacks;
  `_irrigator` and `_selected` guards; `action_irrigate`, `action_water_now` (`minutes or None`), `action_stop`,
  `action_check`, `action_log_manual`, `action_move_plant` / `_move_plant`, `action_plant_sync`, `action_edit_cluster`,
  `action_delete_cluster` / `_delete_cluster`, `_export_stats` (slug and path); all `_render_*` / `_load_*` text (via renders).
- `tui/screens/alerts.py` / `system.py` / `settings.py` / `activity.py`: every `action_*`; the system `toggle_scheduler`
  pause/resume branch and the `delete_job` core-job guard; settings `_selected_vacation` and `_logout`; the activity `load`
  clear-then-fetch.
- `tui/screens/modals.py`: `IrrigateScreen` dismiss dict, `WaterNowScreen._submit` (`int(raw) if raw else 0`),
  `ConfirmScreen` buttons/labels, `LoginScreen._submit`.
- `tui/screens/forms.py`: `parse_value`, `_display`, `FormScreen.compose` ids `field-<name>`, `action_submit`.
- `tui/widgets.py`: `refill` / `selected_key`, `ClusterCard._info`, `Banner.update_health`, `PlantTile`,
  `MetricChart.show_*`, `Heatmap.show`, `KeyValue.show`, `SpriteView` animation gate.
- `tui/sprites.py`: `mood_for`, `plant_sprite`, `_sway`, `_overlay`, `normalize_category`, `watering_can_sprite`, all art and
  palettes.
- `tui/formatting.py`: `ago`, `age`, `clock` (local TZ), `num`, `styled`, `bar`, `weekday_mask` (via renders).
- `tui/resources.py`: every field spec. `tui/model.py`: `summarize`, `is_watering` (via cards and renders).

# Second review: c8fc59f (refactor/tui-test-hardening), parent c4de261

## Verdict: APPROVE

### 1. Assertions / goldens unchanged
- `git diff c4de261 c8fc59f --stat -- tests/golden libs/` is empty.
- I pulled every `assert*`, `assert_golden*`, `pytest.mark/skip/xfail` and `except` line from all 4 contract modules at both commits and diffed them. The assert lines are identical, and so are the golden names and expected values. The only new line is `except WorkerCancelled: pass` in `settle()`, which copies the frozen `test_tui._settle` line for line.
- Every other diff hunk is a `_settle` -> `settle` swap, `_wait_until(pilot, p)` -> `wait_until(p, "msg")`, or the refresh setup described in point 3. `test_tui.py` is not touched.

### 2. Can settle()/wait_until() hide a failure? No.
- `tui.workers.wait_for_complete()` gathers `worker.wait()`, which re-raises `WorkerFailed`. Only `WorkerCancelled` is caught, same as before. Textual's exit_on_error also makes run_test re-raise the failure.
- Both helpers raise AssertionError at the deadline and name what is still pending. Neither has a silent timeout. The timeout went from 10 s to 60 s, which is only slower to fail.
- The conditions are real checks of Textual internals: worker state, message queues, the animator, the batch count, dirty/layout flags, DataTable widths. If a Textual upgrade renames one of them, the helper raises AttributeError. That makes it brittle, but it cannot pass silently.
- `table.check_idle()` only triggers the column measurement Textual would do on its own next idle.
- Mutation M4 below shows a failing refresh worker turns the hardened test red.

### 3. refresh_seconds setup change
- `MODES` holds classes, so AlertsScreen and ActivityScreen are only built and mounted on their first `switch_mode`. `DataScreen.on_mount` reads `gh.refresh_seconds` at mount time. Setting it to 0.5 before the key press therefore installs the real `set_interval(0.5, reload)` on the screen under test.
- Before, the dashboard also auto-refreshed in the background. No assertion or golden covered that, so no pinned contract was lost.

Mutation results. Throwaway worktree at c8fc59f, run with `PYTHONHASHSEED=0 flock ... pytest -q -n 2`. Baseline: 36/36 green.

| Mutation | Change | Result |
|---|---|---|
| M1 | `refill` resets the cursor to row 0 after reload | RED: alerts_auto_refresh, plants_refresh, actuating_keys_golden (3 failed / 29 passed) |
| M2 | `DataScreen.on_mount` installs no auto-refresh timer | RED: alerts_auto_refresh, activity_resets_to_top, both failing with "timed out after 60.0s waiting for: an auto-refresh showing the inserted alert/event" (2 failed / 30 passed) |
| M3 | `refill` restores the cursor by row position, not key (swapped selected record) | RED: alerts_auto_refresh, plants_refresh, actuating_keys_golden (3 failed / 29 passed) |
| M4 | `AlertsScreen.load` raises on its 2nd call (the first auto-refresh) | RED: alerts_auto_refresh with WorkerFailed(RuntimeError), plus actuating_keys_golden (2 failed / 30 passed) |

The worktree was removed afterwards.

### 4. Other weakening: none found
- No new skip, xfail or filterwarnings.
- No broader exception handling.
- No change to normalization: `screen_text` and the export-path toast handling are untouched.

### Non-blocking notes
- The race that was removed from the tests is a real one in production code: overlapping dashboard loads interleave in `DashboardScreen._render_cards` and fail with `NoMatches('#cluster-card-2')` when a load takes longer than `refresh_seconds`. Nothing pinned it before, so this is not a test weakening. It should still be filed as its own issue (the refactor must not fix it silently), or it will be forgotten.
- `settle()` reaches into private Textual internals, so it needs a look whenever Textual is bumped. Any breakage fails loudly.

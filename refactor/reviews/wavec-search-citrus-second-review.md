# Second review: 5faca04 (search_citrus golden, keystroke timing)

**Verdict: APPROVE, on one condition that blocks the merge.** The change is sound synchronization and does not hide real failures. But it does drop one piece of coverage the old test had: the 0.2 s keystroke debounce. A deterministic debounce test must land alongside it (outside the frozen file). Without that, rule 8 ("never weaken") is technically breached.

## 1. Assertions and goldens unchanged: PASS
- `git diff 5faca04^ 5faca04 --stat -- tests/golden libs/` is empty. Only `tests/cli/test_contract_tui_screens.py` changed (+46/-2).
- The diff adds or removes no `assert`, `snap(...)` or golden line. `SCREEN_NAMES`, `test_screen_render_golden`, `test_tour_covers_exactly_the_named_screens` and the `snap("search_citrus")` call site are all unchanged.
- The test still types `pilot.press(*"citrus")` one keystroke at a time. It does not set the Input value directly.

## 2. Still exercises what the golden pins: PASS, with one coverage loss
The test still covers the real Input to `Input.Changed` to `search` worker wiring for every keystroke, plus `exclusive=True` cancellation, the real `client.search(query, limit=50)`, and the table render. The gate only delays the HTTP call. It does not replace it. Mutations, run on the full `test_contract_tui_screens.py` + `test_tui.py` command (baseline 115 passed):
- M1, drop the last hit row (`hits[:-1]`): **red**, `search_citrus` golden fails (test_tui still green).
- M2, `limit=50` changed to `limit=1`: **red**, golden fails.
- M3, `_changed` calls `self.search(event.value[:-1])`: **red**. The `wait_until` for `search('citrus')` times out, so the module fixture errors (24 errors).
- M4, remove `exclusive=True`: **red**, same timeout (24 errors).
- **M5, remove the debounce (`await asyncio.sleep(0.2)` replaced by `pass`): SURVIVES the new test (2/2 runs, 24 passed). It was KILLED by the parent version of the test (2/2 runs, `search_citrus` failed).** With no debounce, the intermediate queries ("c", "ci", ...) render and widen the columns. The gate now hides that by construction.
- No other test covers the debounce. `test_tui.py::test_search_opens_cluster` and the runtime G13 "fern" test only check `row_count >= 1`, opening the cluster, and that calls run off the event-loop thread.
- Is the loss acceptable? The old test detected the debounce only through the same timing artifact that made it flaky (column widths only ever grow). It could not tell "debounce removed" apart from "CPU slow". So making the golden deterministic necessarily makes it blind to the debounce. That is acceptable **only if** a deterministic replacement exists.
- **Required follow-up:** add a test in `tests/cli/test_tui.py` (not frozen). It types "citrus" with a factory that timestamps `client.search` calls, then asserts:
  - the first `search` call comes at least 0.2 s after the first keystroke. This is a lower bound, so CPU load can only make it pass, never fail falsely;
  - the last call's query is "citrus".

## 3. Can it mask a real failure? NO
- `wait_until` raises `AssertionError` after 60 s. It is not trivially true: it needs the exact description `search('citrus')` and no other live worker in the "search" group, so before typing it sees `[] != [...]`.
- `gated` raises `AssertionError` if the gate stays closed for 60 s. That error goes through `app.api`, which only catches `ServerError`, so the worker fails and the test fails. Nothing new is swallowed. (The pre-existing `settle()` still ignores `WorkerCancelled`, which is correct for exclusive workers.)
- Wrong-query and stale-result bugs still fail (M2, M3, M4).
- No deadlock risk. The gate reopens based on worker state only, not on the thread pool. There are 4 CPUs, so 8 pool threads, and at most 5 blocked intermediate calls.
- Cancelled workers' requests still reach the server after the gate opens. They are read-only searches serialized by the fixture lock, and their results are discarded.

## 4. Stability: PASS
- `test_contract_tui_screens.py` run 5 times at `-n 2` under 4 busy-loop CPU hogs: 5/5 green (24 passed each, about 28 s). The hogs were killed afterwards.
- The throwaway worktree `/home/user/gh-review2` has been removed.

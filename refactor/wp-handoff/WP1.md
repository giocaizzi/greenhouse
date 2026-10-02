# WP1 hand-off — CLI client (wave C)

Worktree `/home/user/gh-wavec`, branch `refactor/wave-c-cli-tui`, based on `18fcda8` (integration after WP3/WP4/WP5).
Not pushed, not merged. Every pytest run used `PYTHONHASHSEED=<seed> flock /tmp/greenhouse-tests.lock uv run pytest -q
-n 2 …` unless stated. Logs are in the session scratchpad (`…/scratchpad/wavec/`).

## Commits

| Task | Commit | Evidence |
|---|---|---|
| extra (test-only, **second reviewer required**) | `5faca04` test(tui): search_citrus golden independent of keystroke timing | See "Flaky fix" below. 21/21 green under load. |
| T1.0 | `73ab3c5` strict-clean client (`JSONObject`, `_object`/`_array`) | mypy 174 → 0 errors. `t $CLI $TUI`: 664 passed. |
| T1.0b | `4de7f30` strict-clean `commands/{_helpers,auth,tui}` | Annotated `call`/`output` only. `t $CLI guards`: 603 passed. |
| T1.2 | `89df3ac` `_drop_none(fields)` | 8 kwargs comprehensions + `list_activity` filters. `t $CLI $TUI`: 664 passed. |
| T1.2b | `862ecd5` `_drop_none` for `list_alerts`, `update_vacation`, `update_window` | Extension beyond the plan (deviation 2). `t $CLI $TUI`: 664 passed. |
| T1.4 | `e7d74cb` docstrings | 76 one-line docstrings. `t $CLI`: 537 passed. |

Each task also passed: ruff check + format, `uv run lint-imports` (10 kept), `make typecheck`, and an empty
`git status --porcelain tests/golden`.

## WP gate

- `t $CLI $TUI $(D gcli.client) $CORE tests/test_refactor_guards.py`: **775 passed** (2m52s).
- `uv run ruff check libs/ tests/` and `ruff format --check`: clean. `make typecheck`: 65 modules OK.
- Coverage precondition (`t $CLI $TUI --cov=greenhouse_cli --cov-branch`): `client.py` 99 %. The only lines missed are
  32–33 (`load_stored_token` OSError), which no task touched. There were 0 partial branches, so no WP1 gap-test file
  was needed. `commands/{_helpers,auth,tui}.py` are at 100 %.
- No mutation targets in WP1.

## Flaky fix — `test_screen_render_golden[search_citrus]` (commit `5faca04`)

- **Root cause:** a keystroke-timing race, not a settle gap.
  - `DataTable` column widths are a high-water mark. `clear()` keeps the columns, and `_update_dimensions` only ever
    widens `content_width`.
  - The search dialog debounces each keystroke by 0.2 s. When load spaces two pilot keystrokes more than 0.2 s apart,
    an intermediate query (`c`, `ci`, …) renders its wider hits first, and the final table keeps those widths: Type
    6 → 9, Name 12 → 18.
- **Reproduced** with a probe that pauses 0.5 s between keystrokes. Ungated: exactly the WP0/WP3 diff. Gated: the
  golden bytes.
- **Change:**
  - `_SearchGate` wraps the tour's client factory and holds `client.search` on a `threading.Event` while the test
    types.
  - The test then calls `wait_until()` until `search('citrus')` is the only live search worker, and reopens the gate.
  - Requests are unchanged. No assertion or golden changed.
- **Evidence:** 21/21 green for the screens file at `-n 4` with 4 busy-loop hogs (11 runs at seed 0, 10 at seed 12345).
  Logs: `load-after.log`, `probe-search-gate.log`.
- **Orchestrator:** after the second review, the `search_citrus` line in `refactor/gate1/flaky-tests.txt` can be
  removed.

## Function map / sizecheck / strict list

- **Function map:** none decomposed. New helpers are `client._drop_none`, `IrrigationClient._object` and
  `IrrigationClient._array`, each 1 body line, CC ≤ 2.
- **Sizecheck:** before and after, only the approved register entries are listed. `client.py` grew from 464 to 540
  lines (docstrings), still under its file entry. `commands/auth.py::register` keeps its entry and is untouched.
- **Strict additions:** `greenhouse_cli/client.py`, `commands/_helpers.py`, `commands/auth.py`,
  `commands/tui.py` (the last three under the T0.4 boundary profile).
- **Ratchet for I1:** none. `commands/auth.py` keeps its C901 per-file ignore because `register` is unchanged.
- **Proposed size exceptions:** none.

## Deviations

1. **T1.0b** is not in the plan table. The caller's brief gave WP1 `commands/{_helpers,auth,tui}.py`. The only strict
   error in those three env-reading modules was `auth.py:97` (untyped `output`). I fixed it by annotating the helpers
   only. Typer signatures and `env_reads.json` are untouched.
2. **T1.2b** extends `_drop_none` to the three hand-written `if x is not None` ladders. Key order is unchanged.
   Reviewer: check the dict-literal key order against the old ladders.
3. **T1.0:** `_request`/`stats_export` bind to an annotated local instead of calling `cast`, so there is zero runtime
   difference.

## Dead code

None found in WP1 files. Every `IrrigationClient` method is referenced from `libs/`, `tests/` or `plugin/`.

## REFACTOR_NOTES requests

- Record the `search_citrus` root cause: debounced search plus monotonic `DataTable` column widths. The same
  high-water-mark behaviour exists in production. Typing slowly in the search dialog leaves columns wider than the
  current hits need. This is cosmetic, observed and **not fixed**.

# WP8-SYNC hand-off — `greenhouse_core/sync.py` (T8.16 sync half, T8.17, T8.18)

Worktree `/home/user/gh-sync`, branch `refactor/wp8-sync`, base `85ea3bd` (integration HEAD). Not pushed, not merged,
not rebased. Scope: `libs/greenhouse-core/greenhouse_core/sync.py`, a new gap-test file, `refactor/mypy-strict.txt`
(append), the sync.py line in `pyproject.toml` per-file-ignores (removed), the curated sync snippets in
`refactor/gate1/mutate.py`, and mutation result files. No other production file touched.

## Commits

| Task | Commit | Evidence |
|---|---|---|
| gaps | `3129136` test(core): pin sync summary lines, stats key order and failure isolation before WP8 | Coverage precondition: `greenhouse_core.sync` was already **100 % line + branch** (57 stmts, 24 branches) under the sync tests. One M-pre survivor (`num-plus1 \| new > 0 -> new > 1 \| sync_sensor_data`) plus two unpinned facts (the golden sorts dict keys, so the stats **key order** was unpinned; the combined `"N new from logs, live ✓"` line was never logged) → `tests/test_contract_wp8_sync_gaps.py`, 7 tests, green on the base. |
| T8.16 | `5e2f0cf` refactor(types): add type annotations — strict-clean core sync | `mypy --strict sync.py` 6 → 0 errors; `sync.py` appended to `refactor/mypy-strict.txt`; `-> dict[str, Any]` matches the only consumer `SyncService.sync_all_sensors -> dict[str, Any]`; `Sensor` under `TYPE_CHECKING`. Subset 1477 passed. |
| T8.17 | `ca87899` refactor(core): extract function — _sync_window_start, _store_history, _store_live_reading in sync_single_sensor | Same call order: last-ts read → `get_device_logs` → `group_logs_by_timestamp` → inserts → one `get_live_reading` in the same `try/except Exception: pass`. Subset 1477 passed. |
| T8.18 | `d032bde` refactor(core): extract function — _sync_logged and _sync_summary in sync_sensor_data | Per-sensor `try/except` moved verbatim; `stats` created once and mutated in place (key order unchanged, pinned by the gap test); log texts verbatim. Subset 1477 passed. |
| chore | `6fc15a4` chore(core): drop the stray executable bit on sync.py | 100755 → 100644; EXE002 removed from the per-file ignores. |
| ratchet | `b807477` build(lint): ratchet — sync.py per-file ignores cleared | D202, PLR0915, PLR1702, SIM108 no longer fire; BLE001 / S110 → two reasoned per-line `noqa` (as LINT.md asked); the sync.py per-file-ignores line is **deleted**. |
| chore | `b13e8c9` chore(refactor): mutate.py snippets devices-55/59/60/61 follow T8.17/T8.18 | The moved code made 4 curated snippets INVALID; they now make the same mutation at the new location (7/7 apply). |

Task subset (every task): `$CORE $RENDER $SCHED $DEV D(gc.sync) C(gc.sync)` + the gap file, `PYTHONHASHSEED=0 -n 2`;
`git status --porcelain tests/golden` empty after each run.

## Invariant 8 / frozen points: how each one was checked

- **No extra Cloud call, one live read**: `TestSyncWindow` pins the exact gateway call list per sensor; the golden
  `core_sync_all_clusters.json` pins calls across clusters; the gap test pins that a failing sensor makes no live read.
- **Logger name / log texts**: unchanged (`logging.getLogger(__name__)` in the same module); golden log lines and the
  gap-test summary lines are green.
- **Per-sensor try/except**: same handler body inside `_sync_logged`; failure isolation is pinned by the gap test.
- **Stats key order**: the dict literal is created once in `sync_sensor_data`; `list(stats)` is pinned for both the
  empty and the non-empty case.

## Deviations from the target design (reviewer: look here)

1. `_sync_window_start(last_ts, hours, now)` takes **`now: float`**, not `int` as sketched in target §3.12. With an
   `int`, the first-sync window would lose sub-second precision (`int((time.time() - h*3600) * 1000)`). The frozen
   test clock lands on a whole second, so the tests would not catch that. `float` keeps it identical.
   Side effect: `time.time()` is now read even when a row exists. It is a plain clock read and the value is unused on
   that path.
2. `_store_live_reading` returns `bool`; the caller converts with `int(...)` so `sync_single_sensor` still returns
   `(int, int, int)` (a `True` would leak into the stats sum and into JSON).
3. The curated mutation snippets in `refactor/gate1/mutate.py` were updated (catalogue only). The same was done in
   LINT `e50f667`.

## Mutation (sync tests only: `tests/test_contract_sync.py tests/server/test_contract_sync_service.py` [+ gap file]; `--workers 1`, `MUTATE_NO_LOCK=1`)

| Run | Code | Tests | Result |
|---|---|---|---|
| curated devices-55..61, pre | base | sync tests | **7/7 killed** |
| curated devices-55..61, post | HEAD | sync tests | **7/7 killed** (also 7/7 with the gap file) |
| generated, M-pre | base | sync tests | 60 mutants: **59 killed, 1 survived** (`num-plus1 \| new > 0 -> new > 1 \| sync_sensor_data`) |
| generated, M-pre + gaps | `3129136` | + gap file | 60/60 killed |
| generated, M-post | HEAD | + gap file | **64/64 killed**; `--compare` against M-pre+gaps with `mutation/wp8-sync-function-map.json`: killed-in-pre now surviving **0**; 14 pre identities unmatched (code rewritten: `since_ms = …` → `return …`, `live_saved` → `saved`, annotated `stats`); their post counterparts are all killed. |

The curated runs used a scratchpad wrapper that sets `mutate._TEST_OVERRIDE` for devices-55..61. The curated mode
otherwise uses the whole "devices" group. Files: `refactor/wp-handoff/mutation/wp8-sync-*.jsonl`.

## Size / DoD

`sizecheck` before (base): `sync_sensor_data body=32 nesting=4`, `sync_single_sensor body=52 nesting=3`. After: **no
sync.py hits** (repo total 7 → 5; the rest are engine.py ×4 and services/charts.py, none in this WP).
C90 @ 8: clean. Proposed size exceptions: **none**.

## Ratchet / integrator notes

- `pyproject.toml`: the sync.py per-file-ignores line is gone. Nothing left for I1 on sync.py.
- LINT.md noted that sizecheck's nesting check could be retired once WP8 lands. `sync_sensor_data` is no longer one of
  the six nesting > 3 functions.
- Strict list: `+ libs/greenhouse-core/greenhouse_core/sync.py`.
- REFACTOR_NOTES: none new. B-21 is preserved and still pinned (`test_env_humidity_only_live_reading_current_behavior_is_dropped`).
- Dead code: none found in sync.py.

## Gates at HEAD

`uv run ruff check libs/ tests/` 0 · `uv run ruff format --check libs/ tests/` 0 (331 files) · `make typecheck` 0
(156 files) · `uv run lint-imports` 10 kept · `sizecheck` no sync.py hits · full suite: see below.

Full suite at `b13e8c9`: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2` →
**3056 passed, 0 failed** (11m32s). The integration base had 3039 + 17 new tests: 7 in the gap file, counting the
parametrized cases separately. `tests/golden` unchanged. `FULL_SEED2` was not run (sprint mode: it moves to the final
gate).

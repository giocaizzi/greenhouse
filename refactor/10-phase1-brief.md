# Phase 1 brief — characterization / golden safety net

Read first: `refactor/BRIEF.md`, `CLAUDE.md`, `refactor/00-contracts.md`, `refactor/00-tests.md` (§2 hazards, §3–§4 matrices,
§6 gap list — your work package IDs refer to that table), and `tests/golden.py` + the end of `tests/conftest.py`.

## What you are building
Characterization tests that pin **what the code does today** (not what it should do), so later refactor commits can
prove zero behavior change. If you find a bug: **do not fix it**. Pin current behavior, name the test
`test_<thing>_current_behavior_…`, add a docstring "Pins current (buggy) behavior: … — see REFACTOR_NOTES.md", and list it
in your report file. Do not edit `REFACTOR_NOTES.md` yourself (the orchestrator owns it).

## Hard rules
- **Only create new files** inside your stated paths. Do NOT modify any existing test, fixture, conftest, fake, or any
  production file under `libs/`. If a seam is truly unavoidable, stop and report instead of editing.
- **Do not commit.** The orchestrator reviews and commits per work package.
- **Do not mutate production code** (not even temporarily) — other agents run tests in the same tree. Mutation testing
  happens later in an isolated worktree.
- Hermetic + deterministic: use the shared kit —
  `frozen_clock` fixture (time-machine at `FROZEN_INSTANT` = 2026-04-15T10:00:00Z, `tick=False`), `clean_env` fixture,
  `OfflineWeather` / `install_offline_weather(app)` from `golden.py`, `assert_golden` / `assert_golden_json`.
  For other instants use `time_machine.travel(<aware datetime>, tick=False)` directly. Seed data with explicit
  timestamps derived from the frozen instant, never with a real `time.time()` taken outside the frozen block.
  No network: every app you build must call `install_offline_weather(app)`; `get_device_gateway` stays `None` (or a
  recording fake you write inside your test module).
- Build server apps the way `tests/server/conftest.py::_make_stubbed_app` does (you may import and call it, or use the
  `app` / `client` fixtures — they live under `tests/server/`, so tests that need them must live there too).
- **Goldens:** store under `tests/golden/<your-subdir>/`. Create them by running your tests once with `GOLDEN_UPDATE=1`;
  then run WITHOUT it twice and confirm green. Goldens must be readable text (pretty JSON, `.txt`, `.html`, `.sql`).
  **Normalize the package version** (`5.0.1`, e.g. OpenAPI `info.version`, footers) to `<VERSION>` — release-please bumps
  it on every release and must not break the goldens. Normalize nothing else unless it is genuinely random (and say
  what and why in your report); freeze time instead of normalizing timestamps.
- Golden output must not depend on dict/set iteration order you don't control — sort where the production code doesn't
  define an order, but never sort something whose order IS the behavior (e.g. route registration order, job order,
  decision `reasons` order).
- Prefer a few broad golden tests plus precise invariant tests with clear names over hundreds of trivial asserts.
  Use `pytest.mark.parametrize` and Hypothesis where they add real input coverage (Hypothesis: set
  `derandomize=True`, `deadline=None`, modest `max_examples`, no shrinking-dependent asserts).
- Prove determinism: run your files twice, once with `-p xdist -n 2`, and once with `TZ=America/New_York` exported in
  the shell (your fixtures must isolate it). All green, goldens unchanged (`git status tests/golden` shows only new files).
- Lint: `uv run ruff check <your files>` and `uv run ruff format <your files>` must be clean.
- Run only your own test files (plus, at the end, the existing test files for the same area to make sure you broke
  nothing). Do NOT run the full suite.

## Report
Write `refactor/10-safety-<area>.md`: what is pinned (test → contract/invariant), what is NOT pinned and why, how
determinism was proven (commands + results), bugs observed-not-fixed (with the pinning test name), and a list of the
production lines/branches your tests are meant to guard (so the mutation agent can target them). Return ≤ 20 lines.

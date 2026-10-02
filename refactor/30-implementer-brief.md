# Phase 3 implementer brief (shared by every work package)

You implement ONE work package (WP) of `refactor/20-plan.md` in your own git worktree. The plan is authoritative and
already passed Gate 2; `refactor/20-target-architecture.md` holds the design (signatures, ordering guarantees) for every
task — read the cited § before each task. Do not re-derive the design; if the code contradicts it, **stop and report**.

## Before you start
1. `cd <your worktree>`; use absolute paths under it for every file tool. Never touch `/home/user/greenhouse`.
2. Read `refactor/BRIEF.md` (rules + owner decisions), `CLAUDE.md` (invariants), `REFACTOR_NOTES.md` (observed bugs —
   they are **preserved**, never fixed), `refactor/20-plan.md` §0 (ground rules, lock, red-test policy, aliases, gates),
   your WP's table, and `refactor/wp-handoff/WP0.md` (tooling notes: `make typecheck`, `make sizecheck`,
   `uv run lint-imports`, `refactor/gate1/mutate.py`).
3. Paste the §0.3 aliases (`t`, `FULL`, `FULL_SEED2`, `D`, `C`, `$CORE` …). Every pytest run goes through the lock with a
   pinned seed: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …`.
4. Run your WP's **M-pre** mutation baseline if the plan lists one for your modules (`refactor/gate1/mutate.py`).

## Per task (one commit each, table order)
1. Coverage precondition (§0.4 G.6): if a line/branch you will restructure is not covered → stop, report, ask for a
   characterization test. Do not write it yourself in a production commit.
2. Apply exactly ONE transformation. Moves, renames, extractions, constant adoption, annotations and formatting never
   share a commit.
3. Gate G (or G+ for high-risk files): ruff check + format on touched files; `uv run lint-imports`; `make typecheck`
   (strict list grows — append your modules to `refactor/mypy-strict.txt`, union + sort); `t <task subset incl. the
   golden aliases>`; `git status --porcelain tests/golden` empty; Definition of Done for every function you decompose:
   ≤ 40 body lines, CC ≤ 8 (`uv run ruff check --isolated --select C90 --config "lint.mccabe.max-complexity=8" <file>`),
   nesting ≤ 3 — `make sizecheck` must no longer list it. When your task brings a function within limits, remove its
   per-file `C90`/`PLR` ignore from `pyproject.toml` **only if** the plan assigns that ratchet edit to you; otherwise
   list it in the hand-off for the integrator.
4. Red-test policy (§0.2): identical rerun; red again → `git reset --hard HEAD` and report; green on rerun → flaky only
   if listed in `refactor/gate1/flaky-tests.txt` or reproducible on the parent commit. Never fix forward. Never edit a
   test or golden.
5. Commit message:
   ```
   refactor(<area>): <technique> — <what>

   Behavior: none. <why it is equivalent — e.g. same call order A → B → C, same exception types/messages>
   Evidence: <exact commands + results>; lint-imports OK; typecheck OK; tests/golden unchanged.
   Bugs touched (preserved): B-n … | none

   Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
   Claude-Session: https://claude.ai/code/session_015ndB9xHf1AUdTMftbFLkwD
   ```

## Frozen — never change
Route function names, route docstrings, `response_model`s, Pydantic class names/field order/docstrings, template
names/context keys, Textual `BINDINGS`/`action_*`/`on_*`/`watch_*`/ids/classes/`CSS_PATH`, CLI options/help text,
public import paths, names tests patch at attribute paths (`refactor/00-map.md` "Surprising"), logger names (don't
move logging code to a new module), env vars and defaults, DDL, `constants.py` values, `TriggerCode` members,
`tests/golden/**`, every existing test, and the frozen safety-net files.

## Dead code (owner directive)
Prune dead code you find **in your own files** along the way, following the rules in `refactor/BRIEF.md`
("Prune dead code along the way"): evidence first, one dedicated commit per removal group, evidence in the body.
Dead code outside your files → list it in the hand-off for the final sweep.

## Hand-off
WP gate before hand-back (§0.4): union of task subsets + `$CORE`; high-risk WPs run `FULL` and `FULL_SEED2`.
Then write `refactor/wp-handoff/<WP>.md` (last commit): per task commit hash + evidence, M-pre/M-post mutation
results (by mutant identity), sizecheck before/after, strict-list additions, deviations, proposed size-exception
entries (`path::qualname — reason`), ratchet edits for the integrator, REFACTOR_NOTES requests, and anything a
reviewer should look at hardest. Do not push, do not merge, do not rebase onto other WPs — the integrator merges.

# Lint ratchet plan (queued — runs after wave C, i.e. after the CLI/TUI work packages merge)

## Ruff vs flake8 plugins

Ruff has no plugin system to install: it re-implements the popular flake8 plugins natively as rule families selected
by code prefix in `pyproject.toml` (`B` = bugbear, `SIM` = flake8-simplify, `C90` = mccabe, `PL*` = pylint, `RET`,
`PTH`, `ARG`, `ERA`, `S` = bandit, …). One Rust binary, one pass. What is **not** ported, and how this repo covers it:

| flake8-era tool | Ruff | Here |
|---|---|---|
| flake8-functions `CFQ001` (function length), file length | none (`PLR0915` counts statements only) | `refactor/scripts/sizecheck.py` (≤ 40 body lines, ≤ 400 lines/file) + register |
| nesting depth | `PLR1702` (preview only in ruff 0.15.19) | sizecheck authoritative; opt into `PLR1702` via `explicit-preview-rules` as a cross-check |
| flake8-cognitive-complexity | none | candidate: `complexipy` (Rust) as a dev pre-commit hook — evaluate |
| flake8-expression-complexity, flake8-cohesion, wemake-python-styleguide | none | not adopted (YAGNI) |
| layer contracts | `TID251` bans single APIs only | `import-linter` (10 contracts) |
| type checking | — | `mypy --strict` per-module ratchet |

## Already enforced
- `E, W, F, I, B, C4, UP` (pre-existing)
- `C90` max-complexity 8, `PLR0911/0912/0915` with a shrinking per-file ignore list (WP0)
- `ERA, PGH, SLF, PLE` (clean on `libs/`; `tests/**` exempt from `ERA001/SLF001`) — commit `0cee6a0`

## Queue (one family per commit; offenders listed per file; the list only shrinks)
Counts are findings on `libs/` at the time of queuing.

1. Mechanical, behavior-neutral fixes (each test-backed, reviewed): `SIM` 12, `RET` 4, `PTH` 4, `PERF` 12, `PLW` 5,
   `PIE` 1, `FURB` 1, `N` 1.
2. `RUF` 71 (exclude anything touching frozen strings/serialization).
3. `PLR2004` 47 — magic numbers → `constants.py` (CLAUDE.md invariant 5); values and types unchanged.
4. `S` 17 — review each; real findings → REFACTOR_NOTES, not silent fixes.
5. With frozen-surface exclusions:
   - `TC` 89 — moving imports under `TYPE_CHECKING` changes import-time behavior; only where a test proves no effect.
   - `ARG` 30, `PLR0913` 61, `FBT` 70 — exclude route, Typer command and Textual handler signatures (frozen).
   - `D` 705 — exclude `routes/*` (route docstrings are MCP tool descriptions) and Pydantic schema classes
     (docstrings reach OpenAPI).
6. Report-only (never auto-fixed): `TRY` 27, `BLE` 33 — changing which exceptions are caught is a behavior change;
   findings go to REFACTOR_NOTES as candidates for follow-up PRs.
7. `ANN` 329 — not adopted: redundant with the mypy strict ratchet.

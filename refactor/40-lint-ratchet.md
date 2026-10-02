# Lint ratchet plan (queued — runs after wave C, i.e. after the CLI/TUI work packages merge)

## Ruff vs flake8 plugins

Ruff has no plugin system to install: it re-implements the popular flake8 plugins natively as rule families selected
by code prefix in `pyproject.toml` (`B` = bugbear, `SIM` = flake8-simplify, `C90` = mccabe, `PL*` = pylint, `RET`,
`PTH`, `ARG`, `ERA`, `S` = bandit, …). One Rust binary, one pass. What is **not** ported, and how this repo covers it:

| flake8-era tool | Ruff | Here |
|---|---|---|
| flake8-functions `CFQ001` (function length), file length | none (`PLR0915` counts statements only) | `refactor/scripts/sizecheck.py` (≤ 40 body lines, ≤ 400 lines/file) + register |
| nesting depth | `PLR1702` (preview only in ruff 0.15.19) | sizecheck authoritative; opt into `PLR1702` via `explicit-preview-rules` as a cross-check |
| flake8-cognitive-complexity | none | covered by the refactor + reviews (owner decision); no extra tool |
| flake8-expression-complexity, flake8-cohesion, wemake-python-styleguide | none | not adopted (YAGNI) |
| layer contracts | `TID251` bans single APIs only | `import-linter` (10 contracts) |
| type checking | — | `mypy --strict` per-module ratchet |

## Every ruff complexity / size rule (ruff 0.15.19 registry, 964 rules, filtered)

Findings are on `libs/` at queue time (`ruff check libs/ --isolated --select <code>`). "Ours" = the limit this repo
uses or should use (refactor Definition of Done: CC ≤ 8, ≤ 40 body lines, nesting ≤ 3).

| Code | Name | Status | Option (ruff default → ours) | Findings | Today |
|---|---|---|---|---|---|
| C901 | complex-structure (McCabe) | stable | `mccabe.max-complexity` 10 → **8** | 18 @10, 27 @8 | ✅ enforced @8, per-file ratchet |
| PLR0911 | too-many-return-statements | stable | `pylint.max-returns` 6 | 5 | ✅ enforced, ratchet |
| PLR0912 | too-many-branches | stable | `pylint.max-branches` 12 → **8** | 7 @12, 21 @8 | ✅ enforced @12 — tighten to 8 |
| PLR0915 | too-many-statements | stable | `pylint.max-statements` 50 → **~30** | 6 @50 | ✅ enforced @50 — tighten (proxy for the 40-line rule) |
| PLR0913 | too-many-arguments | stable | `pylint.max-args` 5 | 61 | ⏳ queue; exclude frozen route/Typer/Textual signatures |
| PLR0917 | too-many-positional-arguments | preview | `pylint.max-positional-args` 5 | 48 | ⏳ queue (same exclusions) |
| PLR0914 | too-many-locals | preview | `pylint.max-locals` 15 | 11 | ⏳ queue |
| PLR0916 | too-many-boolean-expressions | preview | `pylint.max-bool-expr` 5 | 0 | ⏳ free to enable |
| PLR1702 | too-many-nested-blocks | preview | `pylint.max-nested-blocks` 5 → **3** | 0 @5, 23 @3 (same functions sizecheck flags) | ⏳ enable @3 with ratchet; replaces sizecheck's nesting check |
| PLR0904 | too-many-public-methods | preview | `pylint.max-public-methods` 20 | 3 (repository facade, …) | ⏳ queue, documented exceptions |
| PLW0717 | too-many-statements-in-try-clause | preview, **new** (not in 0.15.9) | `pylint.max-statements-in-try` 5 | 14 | ⏳ report-only (narrowing a try changes what is caught) |
| SIM102 | collapsible-if | stable | — | 4 | ⏳ queue (nesting reducer) |
| SIM117 | multiple-with-statements | stable | — | 0 | ⏳ free to enable |
| PLW3301 | nested-min-max | stable | — | — | ⏳ with the PLW batch |

Not in ruff at all: function/file **line length** (only statements), **cognitive complexity**, expression complexity,
cohesion, maintainability index → `sizecheck.py` (lines), radon MI in the report. **Owner decision:** cognitive
complexity is covered by the refactor work itself (method-level decomposition + reviewer/adversary passes), not by an
extra tool — `complexipy` is not adopted.

**How preview rules are adopted safely:** `[tool.ruff.lint] preview = true` + `explicit-preview-rules = true`, then list
each preview code by full name. Measured: this adds **0** findings to the rules already selected — only the listed
preview codes activate. Preview rules may change between ruff releases, so the ruff version is pinned in `uv.lock`
and the pre-commit hook is aligned to it (`b2b75fb`, v0.15.19; it was 0.15.9 before).

## Already enforced
- `E, W, F, I, B, C4, UP` (pre-existing)
- `C90` max-complexity 8, `PLR0911/0912/0915` with a shrinking per-file ignore list (WP0)
- `ERA, PGH, SLF, PLE` (clean on `libs/`; `tests/**` exempt from `ERA001/SLF001`) — commit `0cee6a0`

## Queue (one family per commit; offenders listed per file; the list only shrinks)
Counts are findings on `libs/` at the time of queuing.

0. **Complexity first** (config + ratchet entries only, no code change): tighten `max-branches` 12 → 8 and
   `max-statements` 50 → ~30 (pick the value that best matches the 40-body-line rule on the post-refactor code);
   enable `PLR0916`, `SIM117` (0 findings), `PLR1702` @3, `PLR0914`, `PLR0904` via explicit preview; each with a
   shrinking per-file list. Then retire sizecheck's nesting check in favour of `PLR1702` once both agree on the final
   code.

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

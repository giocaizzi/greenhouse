# Gate 1 evidence (safety net)

Commit under test: the head after the seven Phase-1 safety-net commits (see `git log`).

| Check | Result |
|---|---|
| Full suite, run 1 (`-n 4`, branch coverage) | **2597 passed**, 0 failed, 9m06s — `pytest-full-run1.txt` |
| Full suite, run 2 (`-n 4`, different distribution) | **2597 passed**, 0 failed, 5m48s — `pytest-full-run2.txt` |
| Total coverage (line+branch) | 90% → **96%** (`coverage.json` vs `../baseline/coverage.json`) |
| Modules below their baseline coverage | **none** |
| High-risk branch coverage | scheduler 64.3→90.5, engine 88.1→97.8, devices/gateway 73.2→98.2, core/auth 50→100, core/sync 0→100, services/irrigation 76.5→91.2, repository 86.0→89.0 |
| Goldens unchanged by both runs | yes (`git status tests/golden` clean) |
| Secrets scan of branch (`gitleaks detect --log-opts=a1b2622..HEAD`) | no leaks |
| Mutation campaign | 465 non-equivalent mutants (+8 equivalent): **82.6% killed** before gap tests; all 81 survivors then killed by 125 new tests — `mutation.md`, runner `mutate.py` |
| Final full suite after gap tests, `PYTHONHASHSEED=0` | **2728 passed** — `pytest-final-seed0.txt` |
| Final full suite after gap tests, `PYTHONHASHSEED=12345` | **2728 passed** — `pytest-final-seed12345.txt` |
| Flaky list frozen at Gate 1 | empty — `flaky-tests.txt` |

**Gate 1: PASSED.** The safety net is frozen at tag `refactor-gate1`; changes to any `tests/**/test_contract_*.py`,
`tests/golden/**`, `tests/engine_grid.py`, `tests/test_invariants_engine.py`, `tests/test_properties_logic.py` after
this point require written justification in the PR notes and a second reviewer.

Warnings: the two pre-existing ones (Pydantic 2.11 deprecation; `ClusterScreen._load_plant_health` never awaited —
now also surfaced by the new TUI contract tests, same root cause) plus `InsecureKeyLengthWarning` raised deliberately
by `tests/server/test_contract_auth.py` (short test HMAC key).

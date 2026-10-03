# Refactor report (DRAFT — numbers "at 90ef216"; re-measure at the final gate)

Behavior-preserving refactor of greenhouse on `claude/focused-hawking-7to7o3`. Baseline: `main` @ `a1b2622`.
Safety net frozen at the local tag `refactor-gate1`. 502 commits at `90ef216` (219 refactor, 153 docs, 34 fix,
32 build, 29 test, 18 merge, 7 chore, 4 types, 3 style, 2 ci, 1 revert).

> TODO(final): re-measure every number after post-WP8 and types-final merge, and on the final gate.

## 1. Before / after metrics

| Metric | Before (`a1b2622`) | After (`90ef216`) | Source |
|---|---|---|---|
| Tests passing | 1182 | 3082 | `refactor/baseline/pytest-full.txt`; `refactor/integration/after-sync.txt` |
| Coverage | 90% line+branch (91.8% line) | Gate 1 96% line+branch (97.5% line); now 98% line (branch not measured) | `refactor/baseline/coverage.json`, `refactor/gate1/coverage.json`; `pytest --cov=libs` at `164dd69` |
| Ruff rule families | 7 (`E W F I B C4 UP`) | 7 + 26 (`C90 PLR… SIM RET PTH PERF PIE RSE FURB N EXE RUF EM TRY003 FBT ARG A002 D S BLE T201 PLW ERA PGH SLF PLE`); per-file ratchet lists only shrink | `pyproject.toml` both trees |
| mypy strict | not configured; `--strict`: 769 errors in 107/158 files | 171/173 libs modules strict (not yet: `devices/gateway.py`, `ik10pw.py`) | `refactor/baseline/mypy-*.txt`; `refactor/mypy-strict.txt` |
| import-linter contracts | 0 | 20 kept, 0 broken | `uv run lint-imports` |
| sizecheck violations (no exceptions) | 69 (60 functions + 9 files) | 15 (6 + 9), all registered; `make sizecheck` exits 0 | `refactor/scripts/sizecheck.py --no-exceptions` |
| Longest function | 172 (`run_irrigation_pipeline`), then 143 (`detect_conflicts`), 142 (`decide_for_cluster`) | 49 (`ClusterScreen.compose`, excepted), 43, 43 | AST body-line script |
| Functions > 40 body lines | 52 / 978 | 3 / 1227 | same |
| Max radon CC | 36 (`detect_conflicts`) | 20 (`logic/sensors.get_recent_sensor_data`) | `refactor/baseline/radon-cc-C-and-worse.txt`; `uvx radon cc -n C -s libs` |
| Radon blocks C-or-worse / D–F | 50 / 11 | 16 / 0 | same |
| Avg radon CC | 3.00 (1159 blocks) | 2.43 (1460 blocks) | `uvx radon cc -a libs` |
| Route-level `session.commit()` | 68 | 0 (`repo.commit()`) | grep |
| `TypedDict` / `StrEnum` classes | 0 / 5 | 20 / 9 | grep |
| `type: ignore` in libs | 8 | 5 | grep |
| Mutation | Gate 1: 465 non-equivalent mutants, 82.6% killed → all 81 survivors killed by 125 gap tests | per WP, identity-matched, no previously-killed mutant survives: pump watcher 99.1%; WP6 83.8–100%; WP7 131/131; WP8 engine 94.8% (14 proven equivalent); WP8 sync 64/64 | `refactor/gate1/`, hand-offs |

TODO(final gate): full suite with `PYTHONHASHSEED=0`, `=12345`, `TZ=America/New_York`; coverage with `--cov-branch`;
`make check` green; `git status tests/golden` clean; `gitleaks detect --log-opts=a1b2622..HEAD`.

## 2. Final package tree (short)
```
greenhouse_core/  auth constants database models plant_db repository schemas stats sync utils
  logic/ cleaning decision engine fallback plant_needs sensors stress timing trends
  learning/ issues learner models profiling report   devices/ gateway health profile registry irrigators/ sensors/
greenhouse_server/  app auth config deps scheduler state(new)
  routes/ (one module per resource)   web/ … forms(new) weekdays(new)
  services/ … errors(new) inventory(new) irrigation_jobs(new) jobs(new) windows(new)
greenhouse_cli/  client constants(new) main commands/  tui/ … render/(new package) screens/
```

## 3. Patterns introduced
- `services/jobs.job_session(commit=…)` / `read_session(app)`; leak-check jobs in `services/irrigation_jobs.py`.
- Typed `app.state` accessors in `greenhouse_server/state.py`; request providers shared by `deps` and `auth`.
- One not-found idiom: `services/errors.NotFoundError` family, `deps.not_found_as_404`, `deps.require_*`; shared 400
  mappers `deps.require_valid_{vacation_range,window}`.
- Shared validators: `validate_window`, `validate_vacation_range`, `inventory.create_*`, `models.parse_device_config`.
- `web/forms.py` (`blank_or`, `parsed_or_400`, `tri_bool`) replacing six parser copies.
- TypedDict results/patches (`EffectiveConfig`, eight `<Entity>Patch` behind `update_*(**fields: Unpack[…])`, …).
- StrEnums `EntityType`, `ActivitySource`, `EventAction`, `TriggeredBy` (old constant names kept as aliases).
- Engine parameter objects (`_EngineInputs`, `RainForecast` Protocol); `decide_for_cluster` 142 lines/CC 18 → 23 lines.
- Repository domain errors (`DeviceIdExistsError`, `refusing_duplicate_device_id`); one `repo.commit/rollback/flush`
  API; user CRUD through the repository.
- `maybe_notify(fn: Callable[[NtfyClient], object])` with a `NotifyCategory` Literal.
- CLI: shared `Annotated` aliases; `IrrigationClient` as a context manager; typed `call(ctx, fn) -> T`.
- Guards: 20 import contracts, strict mypy, sizecheck + exception register, lint ratchet, `tests/test_refactor_guards.py`.

## 4. Patterns removed
- 68 route-level `session.commit()`; inline get + 404; string-matching "cluster not found".
- Auth's private session/settings copies (two DB sessions per authenticated request).
- Untyped `getattr(app.state, …)` (18 → 9 sites, 4 of them the accessors).
- Drift copies D1–D20; legacy compatibility code (OD3); dead code (18 commits); refactor ids in comments; `log` → `logger`.
- 54 of 60 oversize functions; all 11 radon D–F blocks.

## 5. Behavior changes (labeled) — authoritative list in `REFACTOR_NOTES.md`
Drift: D1/D2, D3/D6, D4, D5, D7, D8, D9, D10/D10b, D12, D13, D14, D15, D16, D17, D20 (+ rollback follow-up).
Consistency: OD4 manual stop `stop`; OD3 three legacy removals; D18; D19; DEBUG logs on silent swallows; TUI config
hint; one DB session per authenticated request; `SpriteView._animated` (B-U1). `ea6116e` restored the leak-check
session code (net none).
Operator upgrade notes: pre-Alembic DB must be stamped; `IRRIGATION_CHECK_INTERVAL_HOURS=N` → `IRRIGATION_CHECK_CRON_HOURS=*/N`;
open legacy `pump_dry_run` alerts resolved by hand; history shows `off` (old) and `stop` (new) manual stops.
TODO(post-WP8): OD3 device-type aliases, D11, D16b, devices strict. TODO(types-final): enum params, server TypedDicts.

## 6. Frozen-contract evidence
`git log --oneline refactor-gate1..HEAD -- tests/golden`: 47 commits, each in a reviewed category — labeled behavior
change; doc-contract description-only (`525ba14`, `bf04b77`, `23d239d`, `a8c6225`, `85ea3bd`); removal-only (12);
additions-only (`001c280`); merges; plus source-shape `0d8ecda` (`env_reads.json`: same-value default as an expression,
two duplicate read sites removed).

## 7. Review and adversary trail
Gate 2 plan critic; Phase-4 lens reviews (architecture, clean code, DRY/YAGNI, typing) → fix pass; TUI hardening and
search-citrus second reviews; WP4 R1/R2 (T4.12 rejected, redone); WP5 R1/R2; WP6 adversary; WP7 R1/R2 (T7.3 rejected,
fixed); drift review; consistency W3 review (`faab47d` rejected, corrected by `ea6116e`); WP8 R1/R2; FP-S R2 (F1 fixed
by `a0e504b`). All final verdicts APPROVE.

## 8. Pending outside work
TODO(final): paste `refactor/90-outside-work.md` §1–§6 verbatim with status notes; add OD5 follow-through and the
network-guard finding.

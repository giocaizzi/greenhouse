# Refactor report

Behavior-preserving refactor of greenhouse on branch `claude/focused-hawking-7to7o3`, from `main` @ `a1b2622` to
`fa89e94`. The safety net (characterization + golden tests, mutation-hardened) was frozen at the local tag
`refactor-gate1` before any production code moved. 566 commits before this report: 246 `refactor`, 169 `docs`, 37 `fix` (labeled behavior
changes), 35 `test`, 35 `build`, 24 merges, 10 `chore`, plus `types`/`style`/`ci` and one revert. Details of every bug,
quirk and labeled change: `REFACTOR_NOTES.md`.

## 1. Before / after

| Metric | Before (`a1b2622`) | After (`fa89e94`) |
|---|---|---|
| Tests | 1182 | 3146 (hash seeds 0 and 12345, and `TZ=America/New_York`: all green) |
| Coverage (line + branch) | 90% | 99% (10,617 statements, 1,948 branches; `--cov-branch`) |
| Network in tests | 21 tests reached the real Open-Meteo API | none: autouse guard blocks outbound HTTP and non-loopback sockets |
| Ruff rule families | 7 (`E W F I B C4 UP`) | 33: + `C90` (max-complexity 8), `PLR0904/0911–0917/1702/2004`, `SIM RET PTH PERF PIE RSE FURB N EXE RUF EM TRY003 FBT ARG A002 D S BLE T201 PLW ERA PGH SLF PLE`; per-file ignores only shrink |
| mypy | not configured (`--strict`: 769 errors in 107/158 files) | `--strict` over all of `libs/` via `[tool.mypy] files` (174 files, 0 errors) |
| import-linter contracts | 0 | 20 kept, 0 broken |
| Size DoD violations (body > 40, nesting > 3, file > 400) | 69 | 15, all in the reviewed register `scripts/size-exceptions.txt`; `make sizecheck` green |
| Longest function | 172 lines (`run_irrigation_pipeline`; `detect_conflicts` 143, `decide_for_cluster` 142) | 49 (`ClusterScreen.compose`, a declarative widget tree, registered) |
| Functions > 40 body lines | 52 | 3 (all registered) |
| Max cyclomatic complexity (radon) | 36 | 20 |
| Radon blocks C-or-worse / D–F | 50 / 11 | 16 / 0 |
| Average radon CC | 3.00 (1159 blocks) | 2.39 (1498 blocks) |
| Route-level `session.commit()` | 68 | 0 (handlers use `repo.commit()`) |
| `TypedDict` / `StrEnum` classes | 0 / 5 | 26 / 9 |
| `type: ignore` in `libs/` | 8 | 2 |
| Mutation testing | Gate 1: 465 non-equivalent mutants, 82.6% killed, then all 81 survivors killed by 125 gap tests | per work package, identity-matched: no mutant killed before survives after (engine 94.8%, the 14 survivors proven equivalent; sync 64/64; scheduler/irrigation 131/131) |

Final gate on `fa89e94`: `pre-commit run --all-files` green (ruff, ruff-format, hygiene, gitleaks; hadolint skipped — no Docker daemon in
the container), `make lint-imports` 20/20, `make typecheck` 174 files clean, `make sizecheck` green, `gitleaks detect`
over `a1b2622..HEAD` clean, full suite 3146 passed with `PYTHONHASHSEED=0` (+ coverage), `PYTHONHASHSEED=12345` and
`TZ=America/New_York`; `tests/golden` unchanged by the runs.

## 2. Structure (inside the unchanged `libs/` uv workspace)

```
greenhouse_core/   auth constants database models plant_db repository schemas stats sync utils
                   logic/ (engine + cleaning decision fallback plant_needs sensors stress timing trends)
                   learning/  devices/ (gateway health profile registry irrigators/ sensors/)  migrations/
greenhouse_server/ app auth config deps scheduler state*
                   routes/ (one module per resource)   web/ (… forms* weekdays*)
                   services/ (… errors* inventory* irrigation_jobs* jobs* windows* chart_payload*)
greenhouse_cli/    client constants* main commands/  tui/ (… render/* package, screens/)
scripts/           sizecheck.py*, size-exceptions.txt*                                   (* = new)
```

## 3. Patterns introduced
- **Transactions:** handlers commit CRUD through one `repo.commit()/rollback()/flush()` API; services commit only before
  a dependent side effect; scheduler jobs use `services/jobs.job_session(commit=…)` / `read_session`.
- **Typed app state:** `greenhouse_server/state.py` accessors; request providers shared by `deps` and `auth`.
- **One not-found idiom:** `services/errors.NotFoundError` family, `deps.require_*`, `deps.not_found_as_404`; shared
  400 mappers for vacation/window validation.
- **Shared rules live once:** `services/inventory`, `validate_window`, `validate_vacation_range`,
  `models.parse_device_config`, `logic/plant_needs.parse_moisture_target`, `logic/timing.active_quiet_window`,
  `web/forms.py` (replacing six parser copies).
- **Typed results:** TypedDict service results and repository patches (`update_*(**fields: Unpack[…])`); StrEnum
  vocabularies `EntityType`, `ActivitySource`, `EventAction`, `TriggeredBy` (old constant names kept as aliases).
- **Engine:** `_EngineInputs` parameter object, `RainForecast` Protocol, one `_finish` for every exit;
  `decide_for_cluster` from 142 lines / CC 18 to 23 lines.
- **Persistence boundary:** repository domain errors (`DeviceIdExistsError`); services import no SQLAlchemy; user CRUD
  goes through the repository.
- **CLI:** shared `Annotated` option aliases, `IrrigationClient` as a context manager, typed `call(ctx, fn) -> T`.
- **Guards:** import contracts, strict mypy, size register, lint ratchet, `tests/test_refactor_guards.py`, hermetic tests.

## 4. Patterns removed
Route-level commits and inline 404s; string-matching on error text; auth's private second DB session; untyped
`getattr(app.state, …)`; 20 divergent duplicate copies (drift pairs); legacy compatibility code (pre-Alembic repair,
`pump_dry_run` alert migration, `IRRIGATION_CHECK_INTERVAL_HOURS`, `tuya_cloud`/`tuya_local` device-type aliases);
dead code (≈30 groups, one commit each with grep evidence); refactor-process ids in comments; every radon D–F block.

## 5. Behavior changes (all labeled `fix(drift|consistency)`, owner-approved)
Full table with locations, pinning tests and commits: `REFACTOR_NOTES.md` → "Labeled behavior changes landed".
- **Drift (one rule per pair, API wins):** web create/update of irrigators/sensors handles duplicate ids (409) and the
  plant-in-cluster rule; one window/404 wording; web check badge uses the API `has_alerts` rule; vacation POST rejects a
  reversed window (400); plant dashboard shows "never"/real age; vacation times in the timezone preference everywhere;
  `irrigator add --device-ip ""` is sent; one soil-target parser; plant sync of an unknown cluster → 404; TUI config
  field order; the monitor commits its freshness sync and the web monitor syncs/404s; malformed stored device config →
  `{}` instead of 500; efficacy `days` ≤ 365; the web kill switch sends the emergency push.
- **Consistency:** manual stop records `stop`; real sensor ages in web cells; stats for a cluster without an irrigator
  → zeros instead of 500; DEBUG logging on silent exception swallows; TUI irrigator config hint; one DB session per
  authenticated request; `SpriteView` no longer shadows `Widget._animate`; legacy device-type aliases removed, with
  the web forms now offering the model keys (fixes the edit form writing legacy values back).
- **Operator upgrade notes:**
  - Alembic revision `a1d3f5b7c902` (data only) rewrites leftover legacy device types on upgrade.
  - A database created before Alembic now fails at startup; compare it with head and `alembic stamp head`.
  - Replace `IRRIGATION_CHECK_INTERVAL_HOURS=N` with `IRRIGATION_CHECK_CRON_HOURS=*/N`.
  - Old open `pump_dry_run` alerts must be resolved by hand.
  - History shows both `off` (old) and `stop` (new) manual stops.

## 6. Contract evidence
Goldens pin OpenAPI, MCP tools, routes, DDL/migrated schema, settings, env reads, import surfaces, loggers, constants,
scheduler jobs, package data, the engine decision grid, orchestration traces, device ingress, web HTML, CLI help/JSON
and TUI renders. Every golden change since `refactor-gate1` falls in one reviewed class: a labeled behavior change
(only the intended lines move), a description-only doc-contract edit (route/schema docstrings, CLI help — documents
identical once descriptions are stripped), a removal-only dead-code prune, an addition of new goldens, a fixture
type-string swap, or one source-shape snapshot (`env_reads.json`, same values).

## 7. Review trail
Every high-risk change had two reviewers, the second one differential (old vs new code side by side, real
SQLAlchemy, fault injection). Rejections that were fixed before merge: the pump-watcher `irrigator.id` read timing,
the scheduler `_job_session` monkeypatch seam, a leak-check rollback path, and three minor review findings
(vacation-ration read order, D20 busy-timeout, a docstring). Engine/devices: 12,000 randomized states, 22,377 fault
runs, 28,792 patch-seam runs and 3,960 device calls with zero differences.

## 8. Pending outside work (not done here)
### 8.1 Safety bugs — each pinned by a `*_current_behavior*` test; fix as labeled PRs
| # | Bug | Where |
|---|---|---|
| S1 | `dry_run_global` ("never actuate") is ignored by every actuation path | preferences: stored/displayed only |
| S2 | IK10PW keep-alive fallback can leave the pump ON until the firmware auto-off (`signal.signal` off the main thread raises after `on()`, so `finally: off()` never runs) | `devices/irrigators/ik10pw.py` `_start_keepalive` |
| S3 | Cooldown blind spot: a cluster that crashes after actuating in `check_all_clusters` is rolled back including its `start` event | `services/irrigation.py` |
| S4 | TUI crash when a dashboard load outlasts `refresh_seconds` (overlapping reloads, also seen once at teardown → `NoMatches`); fix: reload no-ops once the screen is unmounting | `tui/screens/dashboard.py` |
| S5 | Pump watcher: a failed write during a trip makes its own except handlers raise `PendingRollbackError` (pump already stopped) | `services/pump_watcher.py` |
| S6 | Shared `DeviceHealthMonitor`: `bind_repo` swaps the repo under the long-running pump watcher; a NO_WATER alert can land in another job's session or be lost while the cache marks it raised (reproduced) | `services/health_monitor.py`, `scheduler.py`, `pump_watcher.py` |
| — | ≈60 further observed bugs (offline flap every sync, caps never checked in the automatic pipeline, re-raised alerts not re-notified, web 500s on bad input, `WWW-Authenticate` dropped on JSON 401, `local_key` returned in plain text, …) | `REFACTOR_NOTES.md` → "Observed bugs not fixed" |

### 8.2 Security
- MCP bearer token compared with `!=`, not `hmac.compare_digest` (timing side channel on the actuation credential).
- `services/notify.py` opens the configured ntfy URL without a scheme check (ruff S310); default bind host `0.0.0.0`.
- API/CLI/MCP accept any irrigator/sensor `type` on write; an unknown value is refused at actuation (safe) but logged
  with a traceback on every health tick — validate against the registry keys on write (422). When a second device
  model exists, render the web form options from the registry.

### 8.3 Dependencies
- `pip-audit`: 53 advisory rows across 10 locked packages (pyjwt, starlette, urllib3, cryptography, …) — needs a
  dependency-bump PR (runtime dependencies were frozen during this refactor).
- `deptry`: `greenhouse-server` imports `sqlalchemy`/`pydantic` only transitively; `greenhouse-cli` imports `rich`
  transitively — declare them.

### 8.4 CI
- CI runs ruff, `pre-commit run --all-files`, hadolint, gitleaks and `make coverage`, but not `make lint-imports`,
  `make typecheck` or `make sizecheck`; add them so the new gates run on every PR (`.github/workflows/*` was out of scope).
- hadolint could not run in this container (no Docker daemon); CI covers it.

### 8.5 Owner actions
- Push the local tag `refactor-gate1` if you want the frozen safety-net point on GitHub (pushing tags was not authorized).
- Open the PR for `claude/focused-hawking-7to7o3` when you want it reviewed (not opened without your request).
- MCP connectors `firefly`, `greenhouse`, `n8n` need sign-in (claude.ai connector settings); unrelated to this work.

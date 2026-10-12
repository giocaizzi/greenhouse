# Refactor report

Behavior-preserving refactor of greenhouse on branch `claude/focused-hawking-7to7o3`, from `main` @ `a1b2622` to
`fa89e94`. The safety net (characterization + golden tests, mutation-hardened) was frozen at the local tag
`refactor-gate1` before any production code moved. 566 commits before this report: 246 `refactor`, 169 `docs`, 37 `fix` (labeled behavior
changes), 35 `test`, 35 `build`, 24 merges, 10 `chore`, plus `types`/`style`/`ci` and one revert. Details of every bug,
quirk and labeled change: `REFACTOR_NOTES.md`. Sections 1–7 describe the refactor as of `fa89e94`; section 8 covers the
**bug-fix fold-in** that followed in the same PR (#108) and fixed the bugs the refactor had recorded, then the switch to
every ruff rule and to type-checked tests (§8.5); section 9 lists what is still open. At the PR head the branch has 836
commits (47 merges).

## 1. Before / after

| Metric | Before (`a1b2622`) | After (`fa89e94`) |
|---|---|---|
| Tests | 1182 | 3146 (hash seeds 0 and 12345, and `TZ=America/New_York`: all green) |
| Coverage (line + branch) | 90% | 99% (10,617 statements, 1,948 branches; `--cov-branch`) |
| Network in tests | 21 tests reached the real Open-Meteo API | none: autouse guard blocks outbound HTTP and non-loopback sockets |
| Ruff rule families | 7 (`E W F I B C4 UP`) | 33: + `C90` (max-complexity 8), `PLR0904/0911–0917/1702/2004`, `SIM RET PTH PERF PIE RSE FURB N EXE RUF EM TRY003 FBT ARG A002 D S BLE T201 PLW ERA PGH SLF PLE`; per-file ignores only shrink. **At the PR head: `select = ["ALL"]`**, see §8.5 |
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
- **Operator upgrade notes** (refactor and fold-in together; also in `README.md` → "Upgrading an existing deployment"):
  - Alembic revision `a1d3f5b7c902` (data only) rewrites leftover legacy device types on upgrade.
  - A database created before Alembic now fails at startup; compare it with head and `alembic stamp head`.
  - Replace `IRRIGATION_CHECK_INTERVAL_HOURS=N` with `IRRIGATION_CHECK_CRON_HOURS=*/N`.
  - Old open `pump_dry_run` alerts must be resolved by hand; open `sensor_fault` alerts from the old water-warning
    mapping are resolved at startup.
  - History shows both `off` (old) and `stop` (new) manual stops.
  - Rename legacy secret variables: only `GREENHOUSE_MCP_TOKEN`, `GREENHOUSE_NTFY_*` and `GREENHOUSE_AUTH_*` are read
    (bare and `IRRIGATION_` spellings are ignored, with a startup warning per name).
  - A host install binds `127.0.0.1`; set `IRRIGATION_HOST=0.0.0.0` for LAN access (Docker already does).
  - `dry_run_global` is enforced, global per-day caps bind every cluster and automatic runs, a forced irrigate is
    recorded as manual, and API/MCP responses mask `local_key`.
  - The engine judges each sensor's newest reading against its own plant's band (stale sensors ignored), emergency
    runs obey windows and vacation budgets, and time edges are exclusive.

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

## 8. Bug-fix fold-in

After the refactor the owner asked for **every recorded bug to be fixed in the same PR** (2026-10-11). The work ran as
six packages, each on its own branch with a second, differential review before merge: **A** safety + actuation
pipeline, **B** devices / ingress / sync / weather, **C** API / web / auth / security / schema, **D** engine / CLI /
TUI / test infrastructure, **E** dependencies + CI, **F** cleanup. All six are merged, one bug per commit with the
pinning test flipped to the fixed behavior. A, B, C, E and F: `01692d4..190217f`, 87 commits, 73 of them `fix(...)`.
D: merged at `3b75817`, then its two review rounds and the TUI table fix through `f0c7662` (`9a20582..f0c7662`:
53 commits, of which 50 `fix(...)`, 2 dead-code `refactor` and 1 `docs`). Up to `f0c7662` the fold-in has
146 non-merge commits, 123 of them `fix(...)`. At the PR head, with the docs and the ruff `ALL` / typed-tests phase
(§8.5), the fold-in (`01692d4..HEAD`) has 245 non-merge commits: 126 `fix`, 56 `refactor`, 28 `types`, 22 `docs`,
5 `style`, 5 `build`, 2 `test`, 1 `ci`.

### 8.1 Owner decisions

- `dry_run_global` on → no start actuates anywhere (automatic, `/irrigate` incl. `force`, manual from every
  interface); everything is evaluated and logged; stops, stop-all and shutdown stops always work.
- Engine quirks: fix all (critical stress on the driest plant, preference-timezone light month, short-series spike
  guard, validated soil targets, 0 °C / 0 % real, every decision has a reason); emergency runs obey windows and
  vacation budgets; time edges are exclusive. Second review: the soil rule and critical stress judge each sensor's
  newest cleaned reading against its own plant's band, not the 24 h pool.
- Secrets only as `GREENHOUSE_X`; `local_key` masked in irrigator and sensor responses (writes still accept it);
  default bind `127.0.0.1` (Docker opts in).
- A forced run is a manual run (recorded manual, leak check, stopped at shutdown); every real start gets the leak
  check; the IK10PW keep-alive fallback is removed (a start without the local Duration DP is refused).
- TUI confirms scheduler resume and the plant-DB sync; package re-exports resolve lazily.

### 8.2 Fixed, by area

Full table (what changed, where, pinning test): `REFACTOR_NOTES.md` → "Labeled behavior changes landed" →
"Bug-fix fold-in".

| Area | Fixed | Commits |
|---|---|---|
| Safety | S1 / B-1 `dry_run_global` enforced; S2 / B-2 keep-alive can no longer leave the pump ON, then removed (start refused without the local Duration DP); S3 automatic start committed when the device accepts; S5 pump-watcher trip writes; S6 per-job health-monitor view + locked shared cache; compensating stop for an unrecorded start | `e04ce9e`, `c56bd8f`, `cc58fa1`, `a980635`, `fe57ad8`, `4a33bb0`, `a3128d7` |
| Actuation pipeline | B-4 effective caps on every start (gate order health → caps → dry run → start); B-6 gate blocks written to `decision_logs`; forced run = manual + leak check + stopped at shutdown; manual minutes ≥ 1; stop-all reports refusals; vacation-exhausted primary code | `5a31855`, `f86ae95`, `89ea6de`, `6088984`, `a982946`, `60e5926`, `ccf2c4b`, `d2399c7`, `f4188bc` |
| Leak check / pump watcher | every manual start checked (after run + settle; skipped without minutes); B-23 time-bounded "done" scan; negative watch duration | `3d7f906`, `31cb427`, `e145090`, `b14be8d` |
| Device health / alerts | B-3 sensor offline after sync interval + 30 min; B-5 re-opened alerts notify; B-9 cache rebuilt from open alerts at startup; B-10 `water_warning` = soil dry (no SENSOR_FAULT; old alerts resolved); per-device commits and lost-alert recovery | `c13b1a0`, `a2fb694`, `28dfcaf`, `6920994`, `8a57601`, `df518dd`, `00eccfc` |
| Devices / ingress / sync / weather | silent swallows logged; SIGTERM OFF once; per-DP parse errors skipped; DP 111 `"false"` parsed as True (new bug); log rows store `water_warning`; B-21 humidity-only readings stored; B-20 paginated `getdevicelog` (+ cursor/failure guards); forecast hours ≤ 0 and cache key; `/monitor` spike filter | `8b02de9`, `951e657`, `b61fd0e`, `1634816`, `73ee467`, `08a66d7`, `b79190e`, `aff73a1`, `6d20838`, `3770bb8`, `0bb8944` |
| API | B-11 masked `local_key` (irrigators, sensors, web form); B-15 PATCH null semantics; nonexistent default cluster 404; B-22 real status age; cluster / plant delete cascades; unknown device type 422 (validated only on change); NaN / Infinity refused | `578929e`, `a859ecd`, `ac8bcc1`, `7836e76`, `78abbc5`, `5eb5a90`, `4c4e833`, `4bc7823`, `c8e91f7`, `fdef0bc`, `a27d209`, `51994c7` |
| Web | missing-alert ack/resolve 404; non-numeric form 400; unknown-cluster irrigate 404; error format by `Accept` (+ 405 `Allow`); footer clock timezone; no operational banners for anonymous viewers; plant form dependency | `3662b49`, `f0570bb`, `293ceaf`, `fdd0c3a`, `40a1df9`, `ba44321`, `9140079`, `aac74ba` |
| Auth / security / settings | B-12 constant-time MCP token; empty/whitespace token = unset (503), bearer only; `WWW-Authenticate` on JSON 401; corrupt hash → False, unknown user costs a verify; `GREENHOUSE_X`-only secrets + startup warning; ntfy URL validated, credentials redacted; bind `127.0.0.1`; swallowed startup failures logged | `fb8617f`, `6910279`, `3216b6d`, `440bfeb`, `6d1a1a6`, `67e3520`, `2792a7a`, `e269c39`, `f3df4e8`, `db2b659`, `66dca03`, `b388380`, `87fcc6d`, `5d86105` |
| Schema / DB | models declare the migrations' defaults and uniques (no schema drift); in-process Alembic leaves host logging alone | `6758219`, `0159ef9` |
| Engine: quirks | critical stress on the driest plant; `no_action_needed` (no NULL `primary_code`); short-series spike guard; Hampel keeps a confirmed step (a glitch beside a level change is not one); validated soil targets; B-18 0 °C / 0 % real; B-14 applied deltas; B-24 global schedule fallback; light by preference-timezone month; `environment_for` (only `"outdoor"`); emergency runs obey windows and vacation budgets; exclusive edges (cooldown, leak hold, vacation end) | `7bffac8`, `f20ebff`, `2632a54`, `f294cb5`, `3cc9d9e`, `4a195d3`, `dd1395f`, `081e94e`, `9885cb9`, `7175723`, `d08edde`, `0c43ca8`, `e081eb0` |
| Engine: current readings | soil rule and water stress on each sensor's newest cleaned reading; per-linked-plant bands (unlinked → widest band); plant-aware critical threshold (`CRITICAL_STRESS_BELOW_TARGET_MARGIN`, capped 30 %); over-watering or a plant above its band vetoes water stress; the soil-dry alarm is a terminal skip only when over-watering is detected, otherwise noted; conflict wet side per plant; stale sensors (> 4 h) drop out of current checks; water warning = newest current reading carrying the flag | `3801e38`, `7f45387`, `3f72506`, `3db76a6`, `7fc196e`, `ae2182c`, `aba2cfa`, `b97567f`, `b0157b7`, `61905d9`, `e16f4a6`, `f0c7662` |
| Server / web (package D) | forecast and temperature source use `environment_for`; `no_action_needed` icon; scheduler job delete `{job_id:path}` (API and web); freshness sync dates a soil sensor by its newest soil reading | `94e34ed`, `69a3ba1`, `a8493d5`, `0c7bb22`, `8c74cc6` |
| CLI | blank token falls back to the file; B-17 timeouts / empty / non-JSON bodies → `Error:` exit 1; 422 as `field: message` (empty detail → generic message); id `0` is an id; `stats --export` refuses a non-CSV body; job ids percent-encoded; bare groups print help, exit 0 | `fd2d431`, `2eabaa4`, `213fb69`, `7c70c58`, `5f0c659`, `264deca`, `9048809`, `5d4fbf2` |
| TUI | S4 dashboard reload lock (no-op while closing, public Textual API); 401 says "sign in"; Activity cursor kept; add-plant note (B-19); search columns resized; HTTP clients closed on exit; resume and `P` confirm; stale narrow table render (`tui/table.py`) | `dbfb304`, `8da8421`, `f1478d3`, `580a73a`, `85c181a`, `1a642f3`, `363cce7`, `61d45a2`, `9767895` |
| Test infrastructure | server fixtures as a global plugin (mixed paths work); CLI token isolated; `.env` off unless `dotenv` marker | `450eded`, `2a4d6bf`, `6b78e0b` |

Cleanup (package F and leftovers): lazy package re-exports (`greenhouse_core/_lazy.py`; `import greenhouse_core.constants`
~650 ms → 15–25 ms) `0ff00ba`; pump-watcher jobs → `services/pump_watcher_jobs.py` `d3d0ec3`; DP parsers →
`devices/datapoints.py` `ea0726a`; dead code `a00ee93`, `8011987`, `f88e0dc`, `5cafc94`, `dc7d932`, `da9cc6a`
(fallback `temp_range`), `81ec7e3` (duplicate quiet-hours guard). New modules:
`services/{actuation_gates,caps,health_alarms,pump_watcher_jobs}.py`, `devices/datapoints.py`, `greenhouse_core/_lazy.py`,
`greenhouse_cli/tui/table.py`, `tests/server/fixtures.py`.

### 8.3 Dependencies and CI

- `fix(deps)` `8f79282`: `pip-audit` on the lock went from 39 advisories in 9 packages to **none**: anyio 4.13.0 → 4.14.2,
  cryptography 46.0.6 → 50.0.2, idna 3.11 → 3.20, mako 1.3.10 → 1.4.3, mcp 1.27.0 → 1.30.0, pydantic-settings
  2.14.1 → 2.15.0, pyjwt 2.13.0 → 2.15.1, starlette 1.0.0 → 1.7.0, urllib3 2.6.3 → 2.8.0. Floors: server
  `pydantic-settings>=2.14.2`, `pyjwt>=2.15.0`. Root `[tool.uv] constraint-dependencies = ["mcp>=1.28.1,<2"]`: the floor
  clears the mcp advisories; the ceiling is needed because `fastapi-mcp` 0.4.0 breaks on mcp 2 (TypeError at mount).
- `build(deps)` `364e284`: every workspace package declares what it imports (`deptry` clean): server adds
  `sqlalchemy>=2.0.52`, `pydantic>=2.13.4` and drops `httpx`; `jinja2` / `python-multipart` stay as runtime needs
  (`[tool.deptry.per_rule_ignores]`); CLI adds `rich>=14.3.3`.
- `ci` `677986c`: new `static` job (`make lint-imports`, `make typecheck`, `make sizecheck`); the `check` umbrella job
  depends on it.

### 8.4 Gate

Final gate on the PR head (`71bd646`, docs-only commit after it):

- Full suite **3,299 passed** with `PYTHONHASHSEED=0` (+ coverage), `PYTHONHASHSEED=12345` and
  `TZ=America/New_York`; `tests/golden` and the tree unchanged by the runs. Coverage (line + branch) **98%**
  (11,840 statements, 2,090 branches).
- `pre-commit run --all-files` green (ruff, ruff-format, hygiene, gitleaks; hadolint skipped — no Docker daemon in the
  container, CI runs it). `ruff check libs tests scripts` with `select = ["ALL"]`: 0 findings.
- `make lint-imports` 20 kept / 0 broken; `make typecheck` (mypy `--strict`, `libs/` **and `tests/`**) 362 files, 0
  errors; `make sizecheck` green.
- `gitleaks detect` over `a1b2622..HEAD` (787 commits): no leaks.
- CI on the pushed heads: all jobs green (`lint`, `static`, `hygiene`, `dockerfile`, `secrets`, `test`, `check`).

| Metric at the PR head | Value (§1 "after" column: `fa89e94`) |
|---|---|
| Tests | 3,299 (3,146) |
| Coverage | 98% (99%: the fold-in added defensive paths, e.g. compensating stops and gate write-backs) |
| mypy `--strict` | 362 files: all of `libs/` and `tests/` (174, `libs/` only) |
| `type: ignore` | `libs/` 2, `tests/` 7 (2 in `libs/`) |
| Functions > 40 body lines / longest | 0 / 40 (3 / 49) |
| Size register | 7 files, 0 functions (15 entries) |
| Max cyclomatic complexity / average (radon) | 20 / 2.38 over 1,683 blocks (20 / 2.39) |
| Radon blocks C-or-worse / D–F | 13 / 0 (16 / 0) |
| `TypedDict` / `StrEnum` classes | 59 / 9 (26 / 9) |

### 8.5 Ruff `ALL`

Owner decision: ruff selects **every rule**; the only ignores are rules that cannot hold together with something
kept. A dedicated team fixed every finding (five lint packages R1–R5, then four annotation packages T1–T4), each on
its own branch, merged into a staging branch, gated, and pushed only when the whole tree was green.

- **Config** (`[tool.ruff]`, `build(lint)` `8259c5f`): `select = ["ALL"]` plus the four preview size rules already
  enforced (`PLR0904`, `PLR0914`, `PLR0917`, `PLR1702`; `explicit-preview-rules`). Ignores: `COM812` / `ISC001`
  (ruff format), `tests/**` `S101` (pytest asserts), `routes/*` `FAST001` / `D417` (invariant 7: explicit
  `response_model=`, docstrings = MCP descriptions document request parameters only). Every per-file ratchet entry is
  gone. `extend-exclude` the frozen Alembic revisions; `namespace-packages = ["tests"]` (an `__init__.py` would import
  rootdir-imported test modules twice); `allowed-confusables = ["–", "×"]` (frozen user-facing text);
  `flake8-type-checking` runtime-evaluated decorators / base classes keep FastAPI, Pydantic and SQLAlchemy imports real.
- **Findings fixed:** ≈ 4,900 in `libs/` + `scripts/` + `tests/` (R1 server + scripts, R2 core + CLI, R3
  `tests/server` ≈ 2,600, R4 other tests ≈ 1,370, R5 preview size rules), then ≈ 5,560 `ANN` + 4,215 mypy errors when
  the temporary test exemption went away (T1–T4). Behavior-preserving: goldens unchanged except description-only
  `docs(api)` edits (en dash / × in 8 route docstrings, fingerprints re-recorded) and the settings-schema docstring.
- **Structural changes the rules forced:** `IrrigationRepository` composed from per-entity mixins
  (`greenhouse_core/repository/` package, same import path and methods); `IrrigationClient` from per-area endpoint
  mixins (`greenhouse_cli/_client/`, covered by the import contracts); Typer options keyword-only (CLI contract
  byte-identical); FastAPI form/query parameters as dataclass dependencies (`FAST002`, same 422 error lists);
  `ManualStart` / `DeviceSpec` parameter objects. One test class split (14 node ids moved to
  `tests/test_logic.py::TestIrrigationLogicAdjustments`); one duplicate parametrize case removed (`0x01` == `1`).
- **Two labeled fixes found on the way:** the light season outside the engine — learning heuristics (`53645c4`) and the
  maintenance `low_light` check (`1b6efc3`) — now uses the preferences-timezone month like the engine.
- **`# noqa` left:** 165 lines, each one rule with its reason (62 in `libs/` + `scripts/`, 103 in `tests/`). In
  `libs/`: Typer commands whose parameters are the CLI contract (`PLR0913`, `A002`), device-I/O isolation and advisory
  hooks (`BLE001`), deliberate lazy imports (`PLC0415`), pinned public re-exports (`TC001`/`TC002`), the scheduler
  `_app` global (`PLW0603`), `ClusterScreen`'s pinned handler surface (`PLR0904`). In tests: private Textual / rich /
  gateway state with no public accessor (`SLF001`), fake credentials (`S105`), parametrize columns (`PLR0913`).
- **Tests are typed:** `ANN` applies to `tests/**`, and mypy `--strict` covers `tests/` (`explicit_package_bases`,
  `tests` on `mypy_path`). No new `type: ignore`; `cast` only where a test deliberately passes a stand-in or a wrong
  type, each with its reason.

## 9. Pending outside work (not done here)

### 9.1 Open bugs

- Every bug the refactor recorded is fixed; what is left is in `REFACTOR_NOTES.md` → "Observed bugs not fixed".
- A stale `local_key` in an irrigator's config after re-pairing is not recovered automatically (tinytuya error 914
  could drive a Cloud key refresh and retry) — `devices/gateway.py`.
- Small leftovers: the `tr301z.py` docstring names `OFFLINE_AFTER_MINUTES` for sensors; the cleanup candidates in
  `REFACTOR_NOTES.md` → "Dead-code and cleanup candidates".

### 9.2 Dependencies

- Lift the `mcp<2` ceiling once `fastapi-mcp` supports mcp 2.
- `pip-audit` and `deptry` run by hand; add them to CI if advisories should block PRs.

### 9.3 CI

- hadolint could not run in this container (no Docker daemon); CI covers it.

### 9.4 Owner actions

- Push the local tag `refactor-gate1` if you want the frozen safety-net point on GitHub (pushing tags was not authorized).
- Review and merge PR #108 (`claude/focused-hawking-7to7o3`).
- MCP connectors `firefly`, `greenhouse`, `n8n` need sign-in (claude.ai connector settings); unrelated to this work.

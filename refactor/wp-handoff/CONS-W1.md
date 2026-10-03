# CONS-W1 hand-off — consistency group W1 (core)

Worktree `/home/user/gh-cons1`, branch `refactor/consistency-w1`, based on `c6dee08` (audit + OD1–OD5).
Not pushed, not merged, not rebased. Scope: the W1 / core rows of `refactor/46-consistency-audit.md` §3, without
touching WP8 files (`logic/engine.py`, `logic/timing.py`, `devices/**`, `core/sync.py`) or anything under
`greenhouse_server/{routes,web,services}/` or `greenhouse_cli/`.

Every pytest run: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …`, split by test
directory into ≤ 14-file chunks so no single lock hold exceeds ~2 min (helper script in the scratchpad, `trun.sh`).
Grouping by directory also avoids the recorded "fixture 'client' not found" quirk.

## Commits (behavior-preserving first, the one labeled behavior change last)

| # | Audit | Commit | Subset | Result |
|---|---|---|---|---|
| 1 | A1 / C-TYPE-1 | `4af80c3` refactor(types): list 44 already-strict modules (list-only) | `make typecheck` | 123 files OK |
| 2 | A2 / C-TX-1 p1 | `1f7a79f` refactor(repo): `IrrigationRepository.commit/rollback/flush` (+ new `tests/test_repository_unit_of_work.py`) | new test, `$CORE`, test_db, repo gaps, guards | 174 passed |
| 3 | A3 / C-CONST-1/2 | `f95f524` refactor(core): `SECONDS_PER_*`, `FULL_WEEKDAY_MASK` in repository/stats/profiling/schemas/models | D(repository, stats, profiling, schemas, models) + `$CORE $RENDER $CHECK $API` | 1533 passed |
| 4 | A4 / C-CONST-5/6 | `6951a3c` refactor(core): vocabularies (models.py) + service thresholds (constants.py), definitions only | `$CORE` + guards/db/migrations | 133 passed |
| 5 | A4 core adoption | `47642ac` refactor(core): vocabulary + response windows in repository/trends/profiling; mutate.py snippets follow | D(repository, trends, profiling) + `$CORE $RENDER $CHECK $ENGINE` | 1485 passed |
| 6 | A21 / C-TYPE-2 | `1e0c7bf` refactor(types): plant_db, database, logic/cleaning, logic/decision, learning/models → strict list | D(each) + `$CORE $ENGINE $CHECK $RENDER $SETTINGS` + migrations | 1449 passed |
| 7 | A5 / C-DEAD-3/4/5, C-STALE-1 | `486ae98` refactor(core): remove dead code — any_critical, sensor_assignments_for_plant, get_light_needs_info, utils shebang | D(repository, plant_db, decision, utils) + `$CORE $ENGINE $RENDER $CHECK $SETTINGS` | 1606 passed |
| 8 | A18 / C-DEAD-1 | `fd774b0` refactor(core): remove dead code — print_stats_report (+ 3 tests, 1 golden name) | D(stats) + contract_stats + `$CORE $API $WEB` | 685 passed |
| 9 | A19 / C-DEAD-7/8 | `6b31b9c` refactor(core): remove dead code — ENTITY_SYSTEM, DEFAULT_QUIET_* (+ golden names) | `$CORE $ENGINE $CHECK $RENDER` + migrations/db/activity/alerts | 736 passed |
| 10 | C-LEG-9 | `b6c6dd7` refactor(core): remove utils `_SEASONAL_LIGHT_FACTOR` alias; stats.py exec bit | D(utils, stats) + `$CORE $ENGINE $SETTINGS` | 1009 passed |
| 11 | §1.10, C-ENV-2 | `d102398` refactor(core): docstrings (ruff D 13 → 0 in core minus schemas/WP8), stale module docstrings, PLANT_DB_PATH note | `$CORE` + plant_db/db/utils/stats/repo gaps | 219 passed |
| 12 | A11 core / C-RES-3 | `72b6080` refactor(core): `IrrigationStats` / `StatsUnavailable` / `IrrigationRecord` TypedDicts | D(stats) + contract_stats + `$CORE $API $WEB` | 685 passed |
| 13 | OD3 / C-LEG-4 | `1a6e30e` **fix(consistency): drop pre-Alembic database repair (OD3)** | D(database) + `$CORE $API $WEB $SCHED $CHECK` + schema/packaging | 801 passed |

Every commit: `ruff check` + `ruff format --check` on touched files, `uv run lint-imports` (10 kept),
`make typecheck` green, `git status --porcelain tests/golden` empty except the approved removals below.

## End gate (HEAD = `1a6e30e`)

See the "End gate results" section at the bottom (FULL seed 0 in locked chunks — it covers the union of the task
subsets, `$CORE` and `$ENGINE`), plus `uv run ruff check libs/ tests/` OK, `ruff format --check` OK (318 files),
`lint-imports` 10 kept, `make typecheck` 128 files OK, sizecheck on every touched file: only the three pre-approved
file-length exceptions (models, repository, schemas); ruff C90/PLR0911/12/15 @ 8 `--isolated` on touched files: clean.

## Golden diffs (all removal-only, each in its own commit)

- `contracts/imports.json` `greenhouse_core.stats`: − `print_stats_report` (`fd774b0`, rule 4).
- `contracts/imports.json`: − `DEFAULT_QUIET_END_HOUR`, `DEFAULT_QUIET_START_HOUR` (constants), − `ENTITY_SYSTEM`
  (models); `contracts/constants.json`: − the two `DEFAULT_QUIET_*` entries (`6b31b9c`, rule 4, orchestrator-approved).
- `contracts/imports.json` `greenhouse_core.database`: − `Base`, `MigrationContext`, `inspect`, `text` — re-imports that
  only the removed legacy branch used (`1a6e30e`, OD3).

Tests edited/removed: `tests/test_contract_stats.py` (3 `print_stats_report` tests + docstring/import),
`tests/test_migrations.py` (`test_legacy_partial_db_gets_repaired` → `test_pre_alembic_db_is_not_repaired`, docstring).
New: `tests/test_repository_unit_of_work.py` (3 tests for the new unit-of-work methods).

## Strict-list additions (`refactor/mypy-strict.txt`, append-only, union + sort)

44 modules in `4af80c3` (each reported 0 errors alone under the strict profile): `greenhouse_cli/{__init__,main}.py`,
`commands/{__init__,alerts,clusters,configs,decisions,irrigators,operations,plants,preferences,scheduler,sensors,
vacation,windows}.py`, `tui/__init__.py`, `tui/screens/__init__.py`; `greenhouse_core/__init__.py`,
`devices/{__init__,irrigators/__init__,sensors/__init__}.py`, `learning/__init__.py`, `logic/__init__.py`;
`greenhouse_server/__init__.py`, `routes/{__init__,irrigators,preferences,scheduler,search,sensors}.py`,
`services/__init__.py`, `web/__init__.py`, `web/routes/{__init__,activity,alerts,auth,decisions,efficacy,fragments,
health_page,pages,preferences,quality,sensors}.py`. Plus 5 in `1e0c7bf`: `greenhouse_core/{database,plant_db}.py`,
`logic/{cleaning,decision}.py`, `learning/models.py`. All non-WP8 core modules are now strict.

## New vocabulary / thresholds for W3 (A12) to adopt

`greenhouse_core.models`: `SOURCE_{IRRIGATION,SENSOR,PLANT,LEARNING,MAINTENANCE,LEAK,ANOMALY,PUMP,HEALTH}`,
`EVENT_ACTION_{START,STOP,OFF,ATTEMPTED,ABORTED}`, `TRIGGERED_BY_{AUTO,MANUAL,EMERGENCY,SHUTDOWN,PUMP_WATCHER}`.
`greenhouse_core.constants`: `RESPONSE_PRE_WINDOW_SECONDS` (1800 = efficacy `_BEFORE_SECONDS`),
`RESPONSE_POST_WINDOW_SECONDS` (7200), `RESPONSE_MIN_POST_DELAY_SECONDS` (600), `EFFICACY_AFTER_WINDOW_SECONDS` (5400),
`ANOMALY_{MIN_READINGS,WINDOW_READINGS,Z_THRESHOLD,STALE_INTERVAL_MULTIPLIER,MIN_STD}` (= anomaly.py `_MIN_READINGS,
_WINDOW, _Z_THRESHOLD, _STALE_MULTIPLIER, _MIN_STD`), `SYSTEM_HEALTH_{FRESH,STALE,COLD}_SECONDS`,
`SYSTEM_HEALTH_DEVICE_LIMIT`, `DATA_QUALITY_STALE_SECONDS`, `AGE_BADGE_STALE_SECONDS` (web/filters, W2),
`WEATHER_FORECAST_CACHE_TTL_SECONDS`; `services/vacation.py _SECONDS_PER_DAY` → existing `SECONDS_PER_DAY`.
Deliberately **no `SEVERITY_*`**: the vocabulary already exists as `logic.decision.Severity` (StrEnum info/warning/
critical); `services/irrigation.py:958 severity="error"` is the one value outside it (W3 decides). `pump_watcher.
EVENT_ACTION_ABORTED` is pinned (test import) → re-export from models rather than delete.

## Skipped / handed to other lanes

- **W3 (services):** C-DEAD-6 `SOURCE_DECISION`/`SOURCE_SYSTEM` (`services/alerts.py`); A12 adoption; A10 repository
  methods (`search_*`, `list_start_events_since`, `get_sensors_by_ids`, open-alert lookups) — not added here to avoid
  dead methods before a caller exists (orchestrator limited W1's repo part to commit/rollback/flush); W3 adds each with
  its caller after this merge. A15 `log`→`logger` in `services/notify.py` is pinned the same way as below.
- **W2/W3 callers of `repo.commit()/rollback()/flush()`** (C-TX-1 p2, C-TX-2, C-REPO-4).
- **Blocked — needs golden approval:** C-LOG-1 for `database.py`: the attribute name `log` is on the pinned import
  surface (`imports.json` `greenhouse_core.database.public` contains `"log"`); renaming needs a rule-4 golden edit or a
  `log = logger` shim. Same for `services/notify.py` (W3). Since OD3 the database logger emits nothing; it is kept only
  for the pinned logger name.
- **After D14 (drift track):** remove `stats.format_duration` (no prod caller left after `fd774b0`; users then =
  `tests/test_stats.py::TestFormatDuration` + `imports.json`). Expect a trivial merge conflict between D14 and
  `fd774b0` on the `from greenhouse_core.stats import …` line of `tests/test_contract_stats.py`.
- **WP8 / phase D:** `sync.py` is also mode 100755 (chmod -x); `CONFIDENCE_BASELINE`, engine/sync/gateway literals,
  engine `"start"` → `EVENT_ACTION_START` (engine.py:529), `is_within_preferred_hours`, `invalidate_key` as in audit §3 D.
- **Docs (INT):** `plugin/skills/greenhouse/references/LOGIC.md:329` names `DEFAULT_QUIET_START_HOUR/END_HOUR` — reword
  to "baseline migration seed (00:00–05:00)". No doc mentions the pre-Alembic repair.

## REFACTOR_NOTES-ready text

> **OD3 — pre-Alembic database repair removed** (`fix(consistency)`, `1a6e30e`). `init_db` now always runs
> `alembic upgrade head`. A database created before Alembic (tables present, no `alembic_version`) used to be patched
> up (`create_all` + `ALTER TABLE … ADD COLUMN` for missing ORM columns) and stamped at head; it now fails at startup
> with `OperationalError: table … already exists`. Because SQLite DDL is non-transactional, the baseline tables created
> before the failure remain and an empty `alembic_version` table is left behind. Remedy for such a deployment: back up,
> compare the schema with head (e.g. `tests/test_contract_schema.py`'s migrated schema), add any missing columns, then
> `alembic stamp head`. Empty and Alembic-managed databases are unaffected.

> **Observed bug (not fixed), found while typing `stats`:** `GET /api/v1/clusters/{id}/stats` for a cluster without an
> irrigator — `get_irrigation_stats` returns `{"error": "No irrigators in cluster"}`, the route does
> `StatsResponse(cluster_name=…, **result)`, which raises `ValidationError` (missing `period_days`, …) → HTTP 500
> (verified: `StatsResponse(cluster_name="x", error="…")` raises `ValidationError`). The web page handles the `error`
> key. Not pinned yet.

Also for REFACTOR_NOTES "Doc/code mismatches and dead code": the `DEFAULT_QUIET_*` bullet and the
`any_critical` mutation note are resolved by `6b31b9c` / `486ae98`.

## Reviewer focus

- `1a6e30e` (OD3): the removed pre-upgrade reads changed when the connection first opens a transaction; alembic's
  `begin_transaction` commits itself when the connection is idle, and the explicit `conn.commit()` is kept — verified by
  `test_migrations` (empty / idempotent / backfill) and every server test that boots `create_app`.
- `47642ac`: `learning.profiling` lost its module constants `PRE_WINDOW_SEC/POST_WINDOW_SEC/MIN_POST_DELAY_SEC`
  (not on a pinned surface, in-module use only); `mutate.py --check` INVALID set unchanged (72, all pre-existing).
- `1e0c7bf`: `StressIndicators.learning_alerts` typed `list[dict[Any, Any]]` on purpose — `dict[str, Any]` would add
  key validation; JSON schema identical.

## End gate results (HEAD `1a6e30e`, seed 0)

FULL in 12 locked chunks covering all 138 test files (30 core, 93 server, 12 cli, 3 devices):
**3041 passed, 0 failed** (warnings = the two recorded baseline warnings + xdist noise). This includes the union of
every task subset, `$CORE` and `$ENGINE`. `make sizecheck`: only WP8-file entries (engine/sync) remain listed, as at
the base. Seed-12345 and `TZ=America/New_York` runs are left to the final gate (sprint mode).

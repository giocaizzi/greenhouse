# 00 — Metrics baseline (Phase 0)

Commit: `a1b26221ad157454ce8aa35619afeb047b06dd21` (`refactor/baseline/commit.txt`), measured 2026-10-01.
Scope analysed: `libs/greenhouse-core/greenhouse_core libs/greenhouse-server/greenhouse_server libs/greenhouse-cli/greenhouse_cli`,
excluding `migrations/versions/*` (unless noted). All raw output lives in `refactor/baseline/` (one file per tool).
Tool versions: ruff 0.15.19 (project-pinned), mypy 2.3.1 (`uv run --with`), radon/xenon/vulture/pylint/deptry/pip-audit latest via `uvx`, jscpd 4 via `npx`.
Nothing in the repo was modified by these runs (`pyproject.toml` / `uv.lock` untouched — verified with `git status`).

## Summary

| Metric | Value | Raw file |
|---|---|---|
| Size (LOC / SLOC / files) | 23 765 / 15 676 / 158 — core 7 691/4 845/41, server 11 238/7 122/80, cli 4 836/3 709/37 | `radon-raw.txt`, `radon-raw.json` |
| Comment ratio (C+M % L) | 16 % | `radon-raw.txt` |
| `ruff check libs/ tests/` (project rules E,W,F,I,B,C4,UP) | **clean** (exit 0) | `ruff-check.txt` |
| `ruff format --check libs/ tests/` | **clean** — 267 files already formatted | `ruff-format.txt` |
| ruff extended rules, `libs/` | 985 violations (945 excl. migrations/versions); 159 auto-fixable | `ruff-extended-libs.txt`, `ruff-extended-libs-nomigrations.txt` |
| ruff extended rules, `tests/` | 4 698 violations (2 754 are `S101 assert`, 775 `PLR2004`) | `ruff-extended-tests.txt` |
| Cyclomatic complexity: blocks | 951 functions/methods (+208 classes) | `radon-cc.json` |
| CC average (functions/methods) | 3.22 (A); 3.00 incl. classes | `radon-cc.json` |
| CC rank distribution (functions) | A 822 · B 84 · **C 34 · D 10 · E 1** · F 0 | `radon-cc.json`, `radon-cc-C-and-worse.txt` |
| ruff complexity rules (C901 >10, PLR0911/12/13/15) | C901 18 · PLR0913 61 · PLR0912 7 · PLR0915 6 · PLR0911 5 | `ruff-complexity-rules.txt` |
| xenon `--max-absolute B --max-modules B --max-average A` | **FAIL** — 58 errors: 50 blocks (C 36 incl. modules, D 13, E 1) + 8 modules ranked C/D; average passes | `xenon.txt` |
| Maintainability Index | 156 A · 1 B · 1 C (of 158 modules) | `radon-mi.txt` |
| vulture (≥60 %) | 372 findings: 128 unused functions, 92 methods, 139 variables, 11 attributes, 1 class, 1 property; only 6 at 100 % (all unused `cls`/`console` params). Most 60 % hits are framework entry points (FastAPI routes, Typer commands, Textual `action_*`, Pydantic validators) — triage required | `vulture.txt` |
| Duplication — pylint R0801 (≥6 similar lines) | 1 clone: `routes/operations.py:368-389` ↔ `web/routes/analytics.py:86-116` (CSV export) | `pylint-duplicate-code.txt` |
| Duplication — jscpd (≥6 lines, ≥50 tokens) | 16 clones, 201 dup lines (0.9 %), 2 076 dup tokens (1.23 %) | `jscpd.txt`, `jscpd-report.json` |
| mypy default (`--ignore-missing-imports`) | **176 errors** in 29 files — core 56 · server 35 · cli 85 | `mypy-default.txt`, `mypy-tally.txt` |
| mypy `--strict` | **769 errors** in 107 files — core 143 · server 264 · cli 362 | `mypy-strict.txt`, `mypy-tally.txt` |
| deptry (per package) | core 3 · server 24 · cli 13 issues (mostly transitive-import + module-name-mapping; see below) | `deptry.txt` |
| pip-audit (locked env, 243 pins) | 53 advisory rows / 10 packages (pyjwt 13, starlette 5, urllib3 5, cryptography 5, mcp 3, anyio 2, pydantic-settings, mako, idna, click 1 each) — rows include alias duplicates | `pip-audit.txt` |
| Import wall time, `import greenhouse_server.app` | **1.784 s** (min of 3: 1.784/1.853/1.811) | `import-walltime.txt`, `importtime-server.txt` |
| Import wall time, `import greenhouse_cli.main` | **0.204 s** (min of 3); `greenhouse --help` 0.255 s; bare interpreter 0.012 s | `import-walltime.txt`, `importtime-cli.txt` |
| Test coverage (branch) | see "Coverage" section | `coverage.json`, `pytest-full.txt` |

Note: import-time runs happened while the baseline pytest run was executing in parallel (loadavg ≈ 1.6 on 4 cores) — treat as
an upper bound; re-measure on an idle box when comparing.

## Reproducible commands

Run from repo root. `P` and `X` are used throughout:

```bash
P="libs/greenhouse-core/greenhouse_core libs/greenhouse-server/greenhouse_server libs/greenhouse-cli/greenhouse_cli"
X="*/migrations/versions/*"
B=refactor/baseline

# ruff (project rule set) — must be clean
uv run ruff check libs/ tests/                > $B/ruff-check.txt
uv run ruff format --check libs/ tests/       > $B/ruff-format.txt
# ruff extended, report only
R=E,W,F,I,B,C4,UP,SIM,RET,PTH,ARG,PL,C90,N,D,S
uv run ruff check libs/  --select $R --statistics --exit-zero > $B/ruff-extended-libs.txt
uv run ruff check libs/  --extend-exclude 'libs/greenhouse-core/greenhouse_core/migrations/versions' \
    --select $R --statistics --exit-zero > $B/ruff-extended-libs-nomigrations.txt
uv run ruff check tests/ --select $R --statistics --exit-zero > $B/ruff-extended-tests.txt
uv run ruff check libs/ --extend-exclude 'libs/greenhouse-core/greenhouse_core/migrations/versions' \
    --select C901,PLR0912,PLR0915,PLR0911,PLR0913 --output-format concise --exit-zero > $B/ruff-complexity-rules.txt

# radon / xenon / vulture
uvx radon cc -s -a -j -e "$X" $P   > $B/radon-cc.json
uvx radon cc -s -nc -a -e "$X" $P  > $B/radon-cc-C-and-worse.txt
uvx radon mi -s -e "$X" $P         > $B/radon-mi.txt
uvx radon raw -s -e "$X" $P        > $B/radon-raw.txt
uvx radon raw -j -e "$X" $P        > $B/radon-raw.json
uvx xenon --max-absolute B --max-modules B --max-average A -e "$X" $P > $B/xenon.txt 2>&1   # exits 1
uvx vulture $P --exclude "$X" --min-confidence 60 > $B/vulture.txt                           # exits 3

# duplication
uvx pylint --disable=all --enable=duplicate-code --min-similarity-lines=6 \
    --ignore-paths='.*/migrations/versions/.*' $P > $B/pylint-duplicate-code.txt
npx -y jscpd@4 --min-lines 6 --min-tokens 50 --format python --ignore "**/migrations/versions/**" \
    --reporters console,json --output <tmpdir> $P > $B/jscpd.txt   # json copied to $B/jscpd-report.json

# mypy (ephemeral; lockfile untouched)
uv run --with mypy mypy $P --ignore-missing-imports --exclude 'migrations/versions'          > $B/mypy-default.txt
uv run --with mypy mypy $P --ignore-missing-imports --exclude 'migrations/versions' --strict > $B/mypy-strict.txt

# deptry — must run from each package dir, otherwise it reads the ROOT pyproject.toml
for p in core server cli; do (cd libs/greenhouse-$p && uvx deptry . --no-ansi \
    --known-first-party greenhouse_core,greenhouse_server,greenhouse_cli \
    --extend-exclude '.*/migrations/versions/.*'); done > $B/deptry.txt 2>&1

# pip-audit against the locked, non-workspace pins
uv export --no-hashes --frozen --all-packages --no-emit-workspace > <tmp>/req.txt
uvx pip-audit -r <tmp>/req.txt --no-deps --disable-pip --progress-spinner off > $B/pip-audit.txt

# import time
uv run python -X importtime -c "import greenhouse_server.app" 2> $B/importtime-server.txt
uv run python -X importtime -c "import greenhouse_cli.main"   2> $B/importtime-cli.txt
# wall time: 3 subprocess runs each of `.venv/bin/python -c "import <mod>"` timed with perf_counter, min taken
```

## Top offenders

### Worst 20 functions by cyclomatic complexity (radon)

| CC | Rank | Location | Function |
|---|---|---|---|
| 36 | E | `greenhouse-core/greenhouse_core/learning/issues.py:154` | `detect_conflicts` |
| 29 | D | `greenhouse-server/greenhouse_server/services/health.py:20` | `PlantHealthService.compute_score` |
| 29 | D | `greenhouse-core/greenhouse_core/logic/trends.py:11` | `analyze_historical_trends` |
| 27 | D | `greenhouse-core/greenhouse_core/logic/stress.py:11` | `detect_stress_conditions` |
| 25 | D | `greenhouse-server/greenhouse_server/services/forecast.py:35` | `ForecastService.predict_next_irrigation` |
| 24 | D | `greenhouse-core/greenhouse_core/learning/issues.py:21` | `detect_issues` |
| 24 | D | `greenhouse-cli/greenhouse_cli/tui/screens/system.py:53` | `SystemScreen.load` |
| 24 | D | `greenhouse-cli/greenhouse_cli/tui/screens/cluster.py:431` | `ClusterScreen._load_insights` |
| 23 | D | `greenhouse-server/greenhouse_server/services/maintenance.py:22` | `collect_maintenance_alerts` |
| 21 | D | `greenhouse-server/greenhouse_server/services/irrigation.py:422` | `IrrigationService.run_irrigation_pipeline` |
| 21 | D | `greenhouse-core/greenhouse_core/logic/engine.py:678` | `_apply_soil_moisture_rule` |
| 20 | C | `greenhouse-server/greenhouse_server/services/data_quality.py:13` | `build_report` |
| 20 | C | `greenhouse-server/greenhouse_server/services/charts.py:201` | `_threshold_for_cluster` |
| 19 | C | `greenhouse-server/greenhouse_server/services/irrigation.py:610` | `IrrigationService.monitor_cluster` |
| 19 | C | `greenhouse-core/greenhouse_core/logic/sensors.py:10` | `get_recent_sensor_data` |
| 19 | C | `greenhouse-cli/greenhouse_cli/tui/widgets.py:184` | `MetricChart.show_payload` |
| 19 | C | `greenhouse-cli/greenhouse_cli/tui/screens/settings.py:55` | `SettingsScreen.load` |
| 18 | C | `greenhouse-core/greenhouse_core/logic/engine.py:106` | `IrrigationLogic.decide_for_cluster` |
| 18 | C | `greenhouse-cli/greenhouse_cli/tui/model.py:77` | `summarize` |
| 17 | C | `greenhouse-server/greenhouse_server/web/routes/plant_dashboard.py:36` | `plant_dashboard` |

(High-risk per BRIEF: `logic/engine.py` appears twice.) xenon module-level failures (module average rank C/D):
`logic/trends.py` D, `logic/stress.py` D, `learning/issues.py` D, `logic/fallback.py` C, `logic/sensors.py` C,
`services/data_quality.py` C, `services/health.py` C, `services/forecast.py` C.

### Lowest Maintainability Index modules (radon mi)

| Module | Rank | MI |
|---|---|---|
| `greenhouse-cli/greenhouse_cli/tui/screens/cluster.py` | C | 1.08 |
| `greenhouse-core/greenhouse_core/repository.py` | B | 14.48 |
| `greenhouse-core/greenhouse_core/logic/engine.py` | A | 23.51 |
| `greenhouse-cli/greenhouse_cli/tui/widgets.py` | A | 25.27 |
| `greenhouse-cli/greenhouse_cli/client.py` | A | 26.65 |
| `greenhouse-core/greenhouse_core/schemas.py` | A | 30.90 |
| `greenhouse-server/greenhouse_server/services/irrigation.py` | A | 31.96 |
| `greenhouse-cli/greenhouse_cli/tui/screens/settings.py` | A | 34.99 |
| `greenhouse-server/greenhouse_server/services/charts.py` | A | 37.45 |
| `greenhouse-cli/greenhouse_cli/tui/screens/modals.py` | A | 37.71 |
| `greenhouse-cli/greenhouse_cli/tui/screens/alerts.py` | A | 39.89 |
| `greenhouse-cli/greenhouse_cli/tui/screens/system.py` | A | 40.19 |

### Largest modules (LOC / SLOC)

| Module | LOC | SLOC |
|---|---|---|
| `greenhouse-core/greenhouse_core/repository.py` | 1309 | 938 |
| `greenhouse-core/greenhouse_core/schemas.py` | 999 | 573 |
| `greenhouse-core/greenhouse_core/logic/engine.py` | 936 | 718 |
| `greenhouse-cli/greenhouse_cli/tui/screens/cluster.py` | 800 | 726 |
| `greenhouse-server/greenhouse_server/services/irrigation.py` | 760 | 575 |
| `greenhouse-server/greenhouse_server/scheduler.py` | 560 | 296 |
| `greenhouse-cli/greenhouse_cli/client.py` | 464 | 313 |
| `greenhouse-core/greenhouse_core/models.py` | 433 | 246 |
| `greenhouse-server/greenhouse_server/services/health_monitor.py` | 431 | 292 |
| `greenhouse-server/greenhouse_server/routes/operations.py` | 389 | 217 |

### ruff extended — `libs/` top rules

D102 173 · D413 144 · D103 116 · D101 73 · D417 69 · PLR0913 61 · PLR2004 47 · PLC0415 44 · D107 43 · D401 28 ·
ARG001 23 · B008 18 (ignored in project config, FastAPI `Depends`) · C901 18 · D205 18 · S110 8 · ARG002 7 · PLR0912 7 ·
PLR0915 6 · SIM105 6 · PLW0603 5 · S310 4 · SIM102 4 · PTH123 3 · N818 1. Full list in `ruff-extended-libs.txt`.

### Duplication (jscpd, 16 clones)

| Lines | A | B |
|---|---|---|
| 22 | `server/routes/operations.py:368-389` | `server/web/routes/analytics.py:86-106` (CSV export; also the pylint R0801 hit) |
| 18 | `server/routes/irrigators.py:30-47` | `server/routes/sensors.py:21-38` |
| 16 | `server/web/routes/plants.py:100-115` | same file `:60-75` |
| 15 | `cli/commands/irrigators.py:96-110` | same file `:18-28` |
| 15 | `server/web/routes/vacation.py:103-117` | same file `:64-78` |
| 15 | `core/schemas.py:174-188` | same file `:109-123` |
| 14 | `server/services/insights.py:56-69` | same file `:43-56` |
| 13 | `cli/commands/configs.py:75-87` | same file `:25-37` |
| 13 | `server/web/routes/sensors.py:3-15` | `server/web/routes/windows.py:9-20` (import block) |
| 13 | `server/web/routes/plants.py:3-15` | `server/web/routes/windows.py:9-15` (import block) |
| 13 | `server/web/routes/plants.py:86-98` | same file `:46-58` |
| 11 | `cli/commands/plants.py:90-100` | same file `:16-26` |
| 11 | `core/schemas.py:363-373` | same file `:305-315` |
| 11 | `core/schemas.py:857-867` | same file `:49-59` |
| 9 | `server/web/routes/irrigators.py:134-142` | same file `:74-82` |
| 8 | `server/web/routes/windows.py:98-105` | same file `:55-62` |

(Schemas clones are Pydantic validator/field blocks — frozen OpenAPI surface; dedupe only if field order/aliases stay identical.)

### mypy — per-module errors (top 30, default / strict)

Default mode by error code: return-value 83 · union-attr 26 · arg-type 25 · call-overload 12 · operator 9 · attr-defined 5 ·
type-var 4 · index 4 · assignment 3 · misc 2 · var-annotated 2 · list-item 1.
Strict adds: no-untyped-def 279 · type-arg 226 · no-untyped-call 64 · no-any-return 23.

| Module | default | strict |
|---|---|---|
| `greenhouse-cli/greenhouse_cli/client.py` | 79 | 174 |
| `greenhouse-core/greenhouse_core/devices/gateway.py` | 17 | 28 |
| `greenhouse-server/greenhouse_server/scheduler.py` | 12 | 16 |
| `greenhouse-core/greenhouse_core/stats.py` | 10 | 14 |
| `greenhouse-core/greenhouse_core/learning/profiling.py` | 7 | 8 |
| `greenhouse-core/greenhouse_core/logic/plant_needs.py` | 6 | 9 |
| `greenhouse-core/greenhouse_core/sync.py` | 4 | 6 |
| `greenhouse-server/greenhouse_server/services/irrigation.py` | 4 | 10 |
| `greenhouse-cli/greenhouse_cli/tui/screens/cluster.py` | 3 | 19 |
| `greenhouse-core/greenhouse_core/logic/engine.py` | 3 | 11 |
| `greenhouse-server/greenhouse_server/services/manual_control.py` | 3 | 6 |
| `greenhouse-cli/greenhouse_cli/tui/model.py` | 2 | 8 |
| `greenhouse-core/greenhouse_core/devices/irrigators/ik10pw.py` | 2 | 2 |
| `greenhouse-core/greenhouse_core/repository.py` | 2 | 16 |
| `greenhouse-core/greenhouse_core/logic/fallback.py` | 2 | — |
| `greenhouse-core/greenhouse_core/learning/issues.py` | 2 | — |
| `greenhouse-server/greenhouse_server/web/routes/plant_dashboard.py` | 2 | — |
| `greenhouse-server/greenhouse_server/web/routes/clusters.py` | 2 | 17 |
| `greenhouse-server/greenhouse_server/routes/vacation.py` | 2 | — |
| `greenhouse-server/greenhouse_server/routes/plants.py` | 2 | 11 |
| `greenhouse-server/greenhouse_server/routes/configs.py` | 2 | — |
| `greenhouse-cli/greenhouse_cli/commands/operations.py` | 0 | 20 |
| `greenhouse-core/greenhouse_core/plant_db.py` | 0 | 17 |
| `greenhouse-cli/greenhouse_cli/commands/irrigators.py` | 0 | 17 |
| `greenhouse-server/greenhouse_server/web/routes/irrigators.py` | 0 | 17 |
| `greenhouse-cli/greenhouse_cli/commands/plants.py` | 0 | 13 |
| `greenhouse-cli/greenhouse_cli/tui/widgets.py` | 1 | 11 |
| `greenhouse-cli/greenhouse_cli/commands/{configs,clusters,alerts}.py` | 0 | 10 each |
| `greenhouse-server/greenhouse_server/web/routes/analytics.py` | 0 | 10 |
| `greenhouse-core/greenhouse_core/schemas.py` | 0 | 8 |

"—" = not in the strict top-30 list; exact numbers in `mypy-strict.txt` / `mypy-tally.txt`. The `client.py` default-mode
errors are 78 of 79 `return-value` (methods annotated `-> dict` returning the shared helper's `dict | list` union).

## deptry notes (record, do not fix)

- **core**: `argon2` DEP001 + `argon2-cffi` DEP002 — module-name mapping false positive (same package).
- **server**: DEP001 for `sqlalchemy` (13 sites) and `pydantic` (3) — imported directly but only declared transitively via
  `greenhouse-core` (real hygiene finding). DEP001 `jwt`/`dotenv` + DEP002 `pyjwt`/`python-dotenv` are name-mapping false positives;
  DEP002 `jinja2`, `python-multipart`, `httpx` are used indirectly (FastAPI templating/forms, TestClient/weather) — likely false positives.
- **cli**: DEP001 `rich` (13 sites) — used directly but only transitively provided by typer/textual.
- Running deptry from the repo root picks up the root `pyproject.toml` and reports 43 spurious issues — always run from the package dir.

## pip-audit notes (record, do not fix)

53 rows across 10 locked packages (several advisories appear twice under PYSEC/CVE aliases). Highest fix targets:
pyjwt 2.13.0 → ≥2.15.0, starlette 1.0.0 → ≥1.3.1, urllib3 2.6.3 → ≥2.8.0, plus cryptography 46.0.6, mcp 1.27.0, anyio, idna,
click, mako, pydantic-settings. Dependency bumps are out of scope for a behavior-preserving refactor (`uv.lock` frozen).

## Import-time contributors

`greenhouse_server.app` (−X importtime, cumulative): fastapi_mcp ≈ 336 ms, greenhouse_core (via `greenhouse_core.database`) ≈ 313 ms,
fastapi ≈ 286 ms, `greenhouse_server.web.router` ≈ 117 ms, sqlalchemy.engine ≈ 96 ms, `web.exception_handlers` ≈ 87 ms.
`greenhouse_cli.main`: httpx (via `commands.alerts` → `_helpers` → `client`) ≈ 110 ms, typer ≈ 41 ms. Textual is not imported at CLI start (lazy) — keep it that way.

## Coverage

COVERAGE_SECTION_PLACEHOLDER

## Housekeeping

- `git status` after all runs: this agent only added files under `refactor/baseline/` plus `refactor/00-baseline.md`;
  `pyproject.toml` / `uv.lock` untouched. Other changes seen during this phase were **not** produced here and were left alone:
  untracked `openapi.json` and `orm_ddl.sql` at the repo root, and modified `refactor/baseline/module-tests-map*.json`
  (other Phase 0 agents' golden/mapping captures).

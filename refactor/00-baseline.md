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
| Test suite (baseline) | 1182 passed, 2 warnings, 19:28 | `pytest-full.txt` |
| Coverage | line 91.8 % · **branch 79.7 %** · combined 89.5 % (9 826 stmts, 2 296 branches) | `coverage.json` |

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

Source: `refactor/baseline/coverage.json` from the baseline run (`uv run pytest -p no:cacheprovider --cov=greenhouse_core --cov=greenhouse_server --cov=greenhouse_cli --cov-branch --cov-report=json:refactor/baseline/coverage.json -q -W default`, log in `pytest-full.txt`): **1182 passed, 2 warnings in 1168 s (19:28; 21:06 wall incl. startup)**. Migrations/versions excluded from the tables below. Generated from the json with a small script (stmts/branches as reported by coverage.py).

Totals: statements 9826, covered 9023, line % 91.8; branches 2296, covered 1829, branch % 79.7; combined (coverage.py) 89.52 %.

High-risk modules (BRIEF): `greenhouse_server/scheduler.py` line 56.3 % / branch 64.3 % (weakest), `greenhouse_core/auth.py` 86.5 / 50.0, `devices/gateway.py` 88.4 / 73.2, `services/irrigation.py` 81.3 / 76.5, `logic/engine.py` 93.8 / 88.1, `greenhouse_server/auth.py` 93.9 / 88.2. Characterization tests should target these gaps before any move. Near-uncovered: `greenhouse_core/sync.py` (12.3 % line, 0 % branch), `services/weather.py` (51 %), `web/routes/fragments.py` (59 %).

| Package | stmts | line % | branches | branch % |
|---|---|---|---|---|
| greenhouse_cli | 2308 | 95.3 | 534 | 80.1 |
| greenhouse_core | 3298 | 92.6 | 784 | 80.1 |
| greenhouse_server | 4220 | 89.4 | 978 | 79.0 |

Per module, sorted by branch % ascending (modules with no branches last):

| Module | stmts | branch % | line % |
|---|---|---|---|
| `greenhouse_core/sync.py` | 57 | 0.0 | 12.3 |
| `greenhouse_server/web/routes/fragments.py` | 22 | 0.0 | 59.1 |
| `greenhouse_cli/commands/sensors.py` | 24 | 12.5 | 62.5 |
| `greenhouse_cli/commands/plants.py` | 31 | 12.5 | 64.5 |
| `greenhouse_core/devices/sensors/tr301z.py` | 30 | 16.7 | 73.3 |
| `greenhouse_server/services/weather.py` | 51 | 20.0 | 51.0 |
| `greenhouse_server/web/routes/operations.py` | 70 | 33.3 | 75.7 |
| `greenhouse_core/stats.py` | 72 | 41.2 | 62.5 |
| `greenhouse_core/devices/irrigators/tuya_generic.py` | 56 | 43.8 | 76.8 |
| `greenhouse_server/web/routes/plant_dashboard.py` | 88 | 46.2 | 78.4 |
| `greenhouse_server/web/context.py` | 42 | 50.0 | 81.0 |
| `greenhouse_core/auth.py` | 37 | 50.0 | 86.5 |
| `greenhouse_cli/tui/screens/search.py` | 52 | 50.0 | 90.4 |
| `greenhouse_cli/tui/screens/modals.py` | 99 | 50.0 | 91.9 |
| `greenhouse_server/web/routes/sensors.py` | 55 | 50.0 | 92.7 |
| `greenhouse_cli/commands/vacation.py` | 19 | 50.0 | 94.7 |
| `greenhouse_cli/commands/windows.py` | 19 | 50.0 | 94.7 |
| `greenhouse_core/schemas.py` | 573 | 50.0 | 99.7 |
| `greenhouse_server/routes/plants.py` | 95 | 57.1 | 83.2 |
| `greenhouse_cli/tui/screens/alerts.py` | 69 | 57.1 | 95.7 |
| `greenhouse_cli/commands/irrigators.py` | 43 | 58.3 | 88.4 |
| `greenhouse_cli/tui/screens/settings.py` | 82 | 58.3 | 95.1 |
| `greenhouse_core/devices/irrigators/ik10pw.py` | 107 | 60.7 | 59.8 |
| `greenhouse_cli/commands/operations.py` | 56 | 62.5 | 80.4 |
| `greenhouse_server/scheduler.py` | 222 | 64.3 | 56.3 |
| `greenhouse_server/web/routes/configs.py` | 56 | 64.3 | 82.1 |
| `greenhouse_server/services/health_monitor.py` | 191 | 64.7 | 81.2 |
| `greenhouse_server/services/efficacy.py` | 37 | 70.0 | 94.6 |
| `greenhouse_cli/tui/app.py` | 73 | 71.4 | 98.6 |
| `greenhouse_server/app.py` | 140 | 72.2 | 87.1 |
| `greenhouse_core/devices/gateway.py` | 146 | 73.2 | 88.4 |
| `greenhouse_server/services/bulk.py` | 22 | 75.0 | 77.3 |
| `greenhouse_server/web/routes/auth.py` | 42 | 75.0 | 85.7 |
| `greenhouse_server/services/sync.py` | 50 | 75.0 | 86.0 |
| `greenhouse_server/web/routes/preferences.py` | 37 | 75.0 | 89.2 |
| `greenhouse_server/services/forecast.py` | 75 | 75.0 | 90.7 |
| `greenhouse_server/web/routes/analytics.py` | 79 | 75.0 | 96.2 |
| `greenhouse_server/routes/auth.py` | 41 | 75.0 | 97.6 |
| `greenhouse_server/web/routes/plants.py` | 47 | 75.0 | 97.9 |
| `greenhouse_server/services/irrigation.py` | 316 | 76.5 | 81.3 |
| `greenhouse_server/web/routes/irrigators.py` | 126 | 78.6 | 91.3 |
| `greenhouse_server/routes/irrigators.py` | 79 | 78.6 | 93.7 |
| `greenhouse_server/services/anomaly.py` | 54 | 78.6 | 94.4 |
| `greenhouse_cli/tui/screens/cluster.py` | 432 | 78.9 | 95.1 |
| `greenhouse_server/services/maintenance.py` | 52 | 80.0 | 92.3 |
| `greenhouse_core/plant_db.py` | 72 | 80.8 | 91.7 |
| `greenhouse_core/logic/stress.py` | 42 | 81.2 | 88.1 |
| `greenhouse_server/routes/charts.py` | 47 | 81.2 | 93.6 |
| `greenhouse_server/services/insights.py` | 40 | 83.3 | 92.5 |
| `greenhouse_cli/commands/auth.py` | 39 | 83.3 | 94.9 |
| `greenhouse_cli/tui/screens/activity.py` | 44 | 83.3 | 97.7 |
| `greenhouse_cli/tui/widgets.py` | 226 | 83.9 | 95.1 |
| `greenhouse_core/logic/trends.py` | 48 | 84.6 | 93.8 |
| `greenhouse_server/services/health.py` | 68 | 84.6 | 98.5 |
| `greenhouse_server/services/manual_control.py` | 63 | 85.0 | 92.1 |
| `greenhouse_core/repository.py` | 479 | 86.0 | 96.0 |
| `greenhouse_server/services/charts.py` | 181 | 87.2 | 96.1 |
| `greenhouse_server/web/routes/clusters.py` | 110 | 87.5 | 95.5 |
| `greenhouse_server/services/alerts.py` | 69 | 87.5 | 95.7 |
| `greenhouse_server/services/notify.py` | 42 | 87.5 | 97.6 |
| `greenhouse_cli/tui/screens/base.py` | 33 | 87.5 | 100.0 |
| `greenhouse_core/logic/engine.py` | 355 | 88.1 | 93.8 |
| `greenhouse_server/auth.py` | 148 | 88.2 | 93.9 |
| `greenhouse_core/logic/plant_needs.py` | 31 | 90.0 | 90.3 |
| `greenhouse_core/database.py` | 55 | 90.0 | 96.4 |
| `greenhouse_cli/client.py` | 258 | 90.0 | 96.9 |
| `greenhouse_cli/tui/model.py` | 91 | 90.0 | 98.9 |
| `greenhouse_server/routes/operations.py` | 79 | 90.0 | 100.0 |
| `greenhouse_server/services/system_health.py` | 41 | 90.0 | 100.0 |
| `greenhouse_core/logic/timing.py` | 68 | 91.2 | 94.1 |
| `greenhouse_server/services/pump_watcher.py` | 92 | 91.7 | 87.0 |
| `greenhouse_server/web/routes/windows.py` | 60 | 91.7 | 95.0 |
| `greenhouse_server/web/routes/vacation.py` | 70 | 91.7 | 95.7 |
| `greenhouse_server/routes/sensors.py` | 57 | 91.7 | 98.2 |
| `greenhouse_cli/tui/screens/system.py` | 79 | 91.7 | 98.7 |
| `greenhouse_core/learning/issues.py` | 131 | 92.1 | 93.9 |
| `greenhouse_server/web/filters.py` | 90 | 92.9 | 95.6 |
| `greenhouse_core/logic/fallback.py` | 35 | 92.9 | 97.1 |
| `greenhouse_core/learning/profiling.py` | 69 | 92.9 | 98.6 |
| `greenhouse_cli/tui/formatting.py` | 66 | 96.7 | 98.5 |
| `greenhouse_cli/tui/screens/forms.py` | 97 | 97.2 | 97.9 |
| `greenhouse_server/web/routes/alerts.py` | 39 | 100.0 | 87.2 |
| `greenhouse_core/utils.py` | 29 | 100.0 | 89.7 |
| `greenhouse_server/deps.py` | 60 | 100.0 | 96.7 |
| `greenhouse_cli/commands/clusters.py` | 21 | 100.0 | 100.0 |
| `greenhouse_cli/tui/resources.py` | 38 | 100.0 | 100.0 |
| `greenhouse_cli/tui/screens/dashboard.py` | 55 | 100.0 | 100.0 |
| `greenhouse_cli/tui/sprites.py` | 124 | 100.0 | 100.0 |
| `greenhouse_core/devices/registry.py` | 43 | 100.0 | 100.0 |
| `greenhouse_core/learning/report.py` | 31 | 100.0 | 100.0 |
| `greenhouse_core/logic/cleaning.py` | 53 | 100.0 | 100.0 |
| `greenhouse_core/logic/sensors.py` | 35 | 100.0 | 100.0 |
| `greenhouse_server/config.py` | 61 | 100.0 | 100.0 |
| `greenhouse_server/routes/alerts.py` | 47 | 100.0 | 100.0 |
| `greenhouse_server/routes/clusters.py` | 36 | 100.0 | 100.0 |
| `greenhouse_server/routes/configs.py` | 32 | 100.0 | 100.0 |
| `greenhouse_server/routes/insights.py` | 12 | 100.0 | 100.0 |
| `greenhouse_server/routes/vacation.py` | 32 | 100.0 | 100.0 |
| `greenhouse_server/routes/windows.py` | 47 | 100.0 | 100.0 |
| `greenhouse_server/services/cluster.py` | 61 | 100.0 | 100.0 |
| `greenhouse_server/services/data_quality.py` | 46 | 100.0 | 100.0 |
| `greenhouse_server/services/leak.py` | 70 | 100.0 | 100.0 |
| `greenhouse_server/services/search.py` | 26 | 100.0 | 100.0 |
| `greenhouse_server/services/vacation.py` | 28 | 100.0 | 100.0 |
| `greenhouse_server/web/exception_handlers.py` | 26 | 100.0 | 100.0 |
| `greenhouse_server/web/routes/quality.py` | 14 | 100.0 | 100.0 |
| `greenhouse_server/web/templating.py` | 7 | 100.0 | 100.0 |
| `greenhouse_cli/tui/__init__.py` | 3 | n/a | 66.7 |
| `greenhouse_server/routes/scheduler.py` | 35 | n/a | 85.7 |
| `greenhouse_core/devices/sensors/tuya_generic.py` | 18 | n/a | 94.4 |
| `greenhouse_cli/commands/configs.py` | 23 | n/a | 95.7 |
| `greenhouse_core/devices/profile.py` | 36 | n/a | 97.2 |
| `greenhouse_core/logic/decision.py` | 133 | n/a | 99.2 |
| `greenhouse_cli/__init__.py` | 0 | n/a | 100.0 |
| `greenhouse_cli/commands/__init__.py` | 0 | n/a | 100.0 |
| `greenhouse_cli/commands/_helpers.py` | 16 | n/a | 100.0 |
| `greenhouse_cli/commands/alerts.py` | 20 | n/a | 100.0 |
| `greenhouse_cli/commands/decisions.py` | 8 | n/a | 100.0 |
| `greenhouse_cli/commands/preferences.py` | 11 | n/a | 100.0 |
| `greenhouse_cli/commands/scheduler.py` | 12 | n/a | 100.0 |
| `greenhouse_cli/commands/tui.py` | 10 | n/a | 100.0 |
| `greenhouse_cli/main.py` | 35 | n/a | 100.0 |
| `greenhouse_cli/tui/screens/__init__.py` | 0 | n/a | 100.0 |
| `greenhouse_core/__init__.py` | 8 | n/a | 100.0 |
| `greenhouse_core/constants.py` | 100 | n/a | 100.0 |
| `greenhouse_core/devices/__init__.py` | 18 | n/a | 100.0 |
| `greenhouse_core/devices/health.py` | 21 | n/a | 100.0 |
| `greenhouse_core/devices/irrigators/__init__.py` | 4 | n/a | 100.0 |
| `greenhouse_core/devices/irrigators/base.py` | 16 | n/a | 100.0 |
| `greenhouse_core/devices/sensors/__init__.py` | 4 | n/a | 100.0 |
| `greenhouse_core/devices/sensors/base.py` | 12 | n/a | 100.0 |
| `greenhouse_core/learning/__init__.py` | 3 | n/a | 100.0 |
| `greenhouse_core/learning/learner.py` | 19 | n/a | 100.0 |
| `greenhouse_core/learning/models.py` | 32 | n/a | 100.0 |
| `greenhouse_core/logic/__init__.py` | 3 | n/a | 100.0 |
| `greenhouse_core/models.py` | 217 | n/a | 100.0 |
| `greenhouse_server/__init__.py` | 0 | n/a | 100.0 |
| `greenhouse_server/routes/__init__.py` | 0 | n/a | 100.0 |
| `greenhouse_server/routes/activity.py` | 9 | n/a | 100.0 |
| `greenhouse_server/routes/bulk.py` | 11 | n/a | 100.0 |
| `greenhouse_server/routes/decisions.py` | 9 | n/a | 100.0 |
| `greenhouse_server/routes/efficacy.py` | 9 | n/a | 100.0 |
| `greenhouse_server/routes/forecast.py` | 13 | n/a | 100.0 |
| `greenhouse_server/routes/health.py` | 9 | n/a | 100.0 |
| `greenhouse_server/routes/preferences.py` | 16 | n/a | 100.0 |
| `greenhouse_server/routes/quality.py` | 8 | n/a | 100.0 |
| `greenhouse_server/routes/search.py` | 9 | n/a | 100.0 |
| `greenhouse_server/routes/well_known.py` | 10 | n/a | 100.0 |
| `greenhouse_server/services/__init__.py` | 0 | n/a | 100.0 |
| `greenhouse_server/web/__init__.py` | 0 | n/a | 100.0 |
| `greenhouse_server/web/router.py` | 26 | n/a | 100.0 |
| `greenhouse_server/web/routes/__init__.py` | 0 | n/a | 100.0 |
| `greenhouse_server/web/routes/activity.py` | 21 | n/a | 100.0 |
| `greenhouse_server/web/routes/decisions.py` | 11 | n/a | 100.0 |
| `greenhouse_server/web/routes/efficacy.py` | 12 | n/a | 100.0 |
| `greenhouse_server/web/routes/health_page.py` | 12 | n/a | 100.0 |
| `greenhouse_server/web/routes/pages.py` | 10 | n/a | 100.0 |


## Housekeeping

- `git status` after all runs: this agent only added files under `refactor/baseline/` plus `refactor/00-baseline.md`;
  `pyproject.toml` / `uv.lock` untouched. Other changes seen during this phase were **not** produced here and were left alone:
  untracked `openapi.json` and `orm_ddl.sql` at the repo root, and modified `refactor/baseline/module-tests-map*.json`
  (other Phase 0 agents' golden/mapping captures).

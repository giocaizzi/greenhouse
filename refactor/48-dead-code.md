# 48 — Final dead-code sweep (finder report)

Tree: `90ef216` (integration HEAD, clean). Read-only on code: nothing was edited or committed.
Rules: `BRIEF.md` "Prune dead code" (rules 1–5). Excluded on purpose: anything `refactor/post-wp8` (devices/gateway,
registry aliases, web `cluster_detail` quiet flag, test fixtures switched to canonical device keys) or
`refactor/types-final` (StrEnum/TypedDict parameter retypes in services, `filters.cluster_caps`, repository writers)
will obviously change. Those items are in §3 "Deferred" with the reason.

**Run every group below only after both branches have merged.** post-wp8 also edits `tests/engine_grid.py`,
`tests/test_contract_stats.py`, `tests/server/test_pump_watcher.py` and `tests/server/test_health_monitor.py`. The
overlap is small, but the line numbers below are from `90ef216`.

## 0. Method and tool runs

| Tool / sweep | Command | Result |
|---|---|---|
| vulture @80 | `uvx vulture libs/ --min-confidence 80 --exclude "*/migrations/*"` | 8 leads, **all false positives** (§4: quoted `TYPE_CHECKING` annotations ×5, protocol parameters ×3) |
| vulture @60 | same with `--min-confidence 60` | 359 leads: 121 functions, 88 methods, 134 variables, 9 attributes, 5 imports, 1 class, 1 property. After grep/AST checks, **1 is real dead code** (`CreateSchedulerJobRequest`). 9 more are test-only (rule 4) and the rest are false positives (§4). |
| AST usage sweep | scratchpad `sweep2.py`: each module-level def/class/assign, method and class attribute in `libs/` against every `ast.Name`/`ast.Attribute`/keyword/string-literal word in `libs/`+`tests/`, plus raw text of `*.html`, `*.tcss`, `*.js`, `*.json`, `*.toml` and `plugin/**/*.md`. Docstrings, comments and `__all__` strings do **not** count as uses. | 24 non-handler leads. The same set as vulture, plus `ALERT_CODE` and `DEVICE_BLOCKING_CODES`. `sync_sensor_data` showed up only because it is imported under an alias, so it is live. |
| constants.py | each UPPERCASE name: code references in `libs/` outside `constants.py` (comment/docstring lines dropped) | **0 unused**. After group B1, one becomes dead: `DEFAULT_PREFERRED_WATER_HOURS` (see B2). |
| unused imports / noqa | `uv run ruff check libs tests --extend-select RUF100` (F401 and RUF are enabled in config) | **clean**. No unused import and no `noqa` that suppresses nothing. The 4 `noqa: F401` are deliberate (§4). |
| `type: ignore` | `uv run mypy` (strict, so `warn_unused_ignores` is on) on the 5 files that carry one: `jobs.py`, `scheduler.py`, `tui/screens/{base,search,forms}.py` | no "unused ignore". All 5 are still needed. |
| test helpers / fixtures | AST over `tests/**/*.py`: each module-level def/assign/fixture and each non-test method on helper classes, counted as a whole word over `tests/`+`libs/` | 3 dead: `engine_grid.case_with`, `engine_grid.NEW_YORK`, fixture `utc_display`. (`pytestmark` is pytest magic.) No unused fixture in `tests/conftest.py`, `tests/server/conftest.py`, `tests/cli/tui_fixtures.py`, `tests/fake_data.py`, `tests/fake_devices.py`. |
| stale goldens | pytest plugin (`scratchpad/plug/goldtrace.py`) records every `open`/`Path.read_*`/`glob` under `tests/golden/` while running all 43 golden-using test files (`-n 4`, **1790 passed**) | 508/511 files were seen. The other 3 (`web/routes.json`, `web/template_context.json`, `web/mutations/update_window__same_hours.json`) were missed only by the tracer. Each is read by name: `test_contract_web_html.py:540,790`, `test_contract_web_mutations.py:545`. **No stale golden.** |
| templates | each `web/templates/**/*.html` path against `libs/`+`tests/` (`*.py`, `*.html`); Jinja macros; the 12 `ALL_FILTERS` names in templates | every template is referenced; `range_window` is used; all 12 filters are used (`moisture_badge` once, `_plant_latest_card.html:23`) |
| `app.tcss` | each `#id`/`.class` selector against `tui/**/*.py` | 0 unused (C-DEAD-11 `#global-config` is already gone) |
| unimported modules | each `libs/**/*.py` module path against `libs/`, `tests/` and pyproject entry points | 0 |
| earlier leads re-checked | `40-review-dry-yagni` DC1–DC8, WP8 handoff, `90-outside-work` §4, `00-map` ‡ appendix | already removed: `set_plant_database`, `EVENT_ACTION_OFF`, `AuthUserDep`, `_sensor_row.html`, `StressIndicators.any_critical`, `print_stats_report`, `ENTITY_SYSTEM`, `SOURCE_DECISION`, `SOURCE_SYSTEM`, the `#global-config` tcss rule, `DEFAULT_QUIET_*`. `SessionDep`/`DeviceGatewayDep` are now used by the factories (DRY-7). `CONFIDENCE_BASELINE` is now used (`engine.py:16,655`). Still open: `CreateSchedulerJobRequest`, `case_with`, `is_within_preferred_hours`, `invalidate_key`. |

Base grep for every row: `grep -rnw <name> libs tests plugin CLAUDE.md --include=*.py --include=*.html --include=*.tcss --include=*.md --include=*.json | grep -v __pycache__`.
In this report, "golden" means hits under `tests/golden/`. Every other hit is listed.

---

## 1. Removal groups — proposed commits

### A1 `refactor(core): remove dead code — CreateSchedulerJobRequest (+ golden name)`

| What | Where | Evidence | Golden change | Risk |
|---|---|---|---|---|
| `class CreateSchedulerJobRequest(BaseModel)` (7 lines + 2 blank) | `libs/greenhouse-core/greenhouse_core/schemas.py:642-648` | base grep → only the definition and `tests/golden/contracts/imports.json:367`. `grep -c '"CreateSchedulerJobRequest"' tests/golden/contracts/openapi.json` → **0** (not in OpenAPI, so not an MCP tool). `routes/scheduler.py` has only GET/DELETE; there is no POST `/scheduler/jobs`. vulture: "unused class (60%)". No `getattr`, `__all__` or registry use. | `imports.json`: delete line 367 (`"CreateSchedulerJobRequest",` in `greenhouse_core.schemas`). Removal-only (rule 4). | very low. Subset: `uv run pytest tests/test_contract_imports.py tests/server/test_contract_openapi.py tests/server/test_contract_mcp.py -q` |

### B1 `refactor(core): remove dead code — is_within_preferred_hours (+ its own tests)`

| What | Where | Evidence | Golden change | Risk |
|---|---|---|---|---|
| `def is_within_preferred_hours(...)` | `libs/greenhouse-core/greenhouse_core/logic/timing.py:87-100` | base grep → definition plus `tests/test_timing.py:10,25,30,36` (its own unit tests). No other caller in libs, web, CLI or plugin. It was left over from issue #83: the window gate no longer uses preferred hours (CLAUDE.md invariant 9; `engine.py:317` docstring). vulture: unused function (60%). Already queued as C-DEAD-9 "after WP8", and WP8 has merged. | none: `greenhouse_core.logic.timing` is not a frozen module in `imports.json` (`grep -c is_within_preferred_hours tests/golden -r` → 0) | low. `test_timing.py` is not a Gate-1 frozen file. |
| its 3 tests + import line | `tests/test_timing.py:10` (import), `:22-36` (`test_preferred_hours_default_morning_window`, `_rejects_midday`, `_custom_window`) | rule 4: the only users of the dead function | — | Subset: `uv run pytest tests/test_timing.py tests/test_engine_timing.py tests/test_contract_imports.py -q` |
| `DEFAULT_PREFERRED_WATER_HOURS` import in timing | `timing.py:19` | it becomes unused after the function goes (ruff F401 would flag it) | — | — |

### B2 `refactor(core): remove dead code — DEFAULT_PREFERRED_WATER_HOURS (+ golden names, LOGIC.md)` (after B1)

| What | Where | Evidence | Golden change | Risk |
|---|---|---|---|---|
| `DEFAULT_PREFERRED_WATER_HOURS = (6, 10)` | `libs/greenhouse-core/greenhouse_core/constants.py:242` | after B1: `git grep -nw DEFAULT_PREFERRED_WATER_HOURS -- libs` → definition only. `plant_db.get_care_data` does **not** fall back to it (`grep -rn preferred_water_hours libs --include=*.py` → only the `engine.py:317` docstring). The advisory value comes from `plant_database.json` `_category_defaults`. | `contracts/constants.json:36` remove the key, and `contracts/imports.json:54` remove the name. Both are superset goldens, so the test fails with "constants removed" unless the golden is edited. Removal-only (rule 4). | low. `plugin/skills/greenhouse/references/LOGIC.md:348` documents the constant; delete that bullet in the same commit (CLAUDE.md plugin-sync table: `constants.py` → LOGIC.md). Subset: `tests/test_contract_constants.py tests/test_contract_imports.py` |

*Alternative if the owner wants to keep the documented default:* skip B2 and accept one dead constant that is pinned by the golden. Because of the "no dead code" directive, the recommendation is to do B2.

### C1 `test: remove dead code — unused grid/stats test helpers` (Gate-1 frozen files: needs the written justification + second reviewer per `refactor/gate1/README.md`)

| What | Where | Evidence | Golden change | Risk |
|---|---|---|---|---|
| `case_with()` + its `replace` import | `tests/engine_grid.py:1198-1200`; `from dataclasses import dataclass, replace` at `:30` becomes `dataclass` only | `grep -rnw case_with tests refactor/scripts refactor/gate1` → definition only; `replace` is used only in `case_with` (`grep -nw replace tests/engine_grid.py` → 30, 1199 docstring, 1200) | none | nil (test helper; never imported). Already listed as DC7. |
| `NEW_YORK = "America/New_York"` | `tests/engine_grid.py:53` | `grep -rnw NEW_YORK tests refactor/scripts refactor/gate1` → definition only. The `TZ=America/New_York` gate run sets the env var; it does not use this name. | none | nil |
| fixture `utc_display` + `import greenhouse_core.utils as utils_mod` | `tests/test_contract_stats.py:17-21`, `:9` | `grep -rnw utc_display tests` → definition only (no parameter, no `usefixtures`, not autouse). `utils_mod` is used only inside it (`:9`, `:20`). | none | nil; the tests in that file never requested it. Subset: `uv run pytest tests/test_contract_stats.py tests/test_contract_decision_grid.py tests/test_invariants_engine.py -q` |

### D1 `refactor(server): remove dead code — pump_watcher.ALERT_CODE (tests use HealthAlarm.NO_WATER)` (optional, rule 4)

| What | Where | Evidence | Golden change | Risk |
|---|---|---|---|---|
| `ALERT_CODE = HealthAlarm.NO_WATER.value` + its 2-line comment | `libs/greenhouse-server/greenhouse_server/services/pump_watcher.py:58-60` | base grep → definition plus `tests/server/test_pump_watcher.py:23,218,348,431`. No libs reader: the watcher raises through `DeviceHealthMonitor`, which owns the code. `greenhouse_server.services.pump_watcher` is not a frozen module in `imports.json`, and the name is not in `00-map` test-pinned paths. | none | low. Rewrite the 3 assertions to `HealthAlarm.NO_WATER` (a StrEnum, so `==` against `alert.code` still holds). `test_pump_watcher.py` is not frozen. |

### D2 `refactor: remove dead re-exports — names in __all__ with no importer` (optional, tiny)

| What | Where | Evidence | Golden change | Risk |
|---|---|---|---|---|
| `"SOURCE_HEALTH"` in `health_monitor.__all__` | `services/health_monitor.py:382` | AST importer scan: `from greenhouse_server.services.health_monitor import SOURCE_HEALTH` appears only in `tests/server/test_pump_watcher.py:21` and `test_health_monitor.py:26`. Libs import it from `greenhouse_core.models`. The name stays in the module (used at module level); only the re-export goes. | none (module not frozen) | low. Switch those 2 test imports to `greenhouse_core.models` (40-review YAGNI item). |
| `Row` re-export in `tui/render/__init__.py` | `libs/greenhouse-cli/greenhouse_cli/tui/render/__init__.py:8` (import) and `:40` (`__all__`) | `grep -rhoE 'render\.[A-Za-z_]+' libs/greenhouse-cli tests` never shows `render.Row`. Every `Row` user imports `greenhouse_cli.tui.render._rows` directly (`cluster_tabs.py:10`, `system.py:11`, `settings.py:10`). No test imports it. | none | nil |
| `"Reason"` in `logic/engine.__all__` | `libs/greenhouse-core/greenhouse_core/logic/engine.py:1062` | AST importer scan: no `from greenhouse_core.logic.engine import Reason` anywhere. `Reason` is pinned via `greenhouse_core.logic` (package) in `imports.json`, which is unaffected. The engine keeps importing it for its own use. | none (`logic.engine` is not a frozen module; test-pinned engine paths are `season_for`, `time`, `seasonal_light_factor`, `MIN_COOLDOWN_HOURS` only) | nil |

### E1 `refactor(server): remove unreachable branches — anomaly baseline guard, pump_watcher None-monitor arm` (rule 3: proof in body)

| What | Where | Proof | Golden change | Risk |
|---|---|---|---|---|
| `if len(baseline) < ANOMALY_MIN_READINGS - 1: return None` | `services/anomaly.py:58-59` | `:51` already returned when `len(soil_values) < ANOMALY_MIN_READINGS`, so here `len(soil_values) ≥ N`. `baseline = soil_values[1:]`, so `len(baseline) ≥ N-1` and the condition is always false. No test can reach it. (WP4 handoff dead-code list #1.) | none | nil |
| `if monitor is not None:` (false arm), and the `-> DeviceHealthMonitor \| None` return type of `_lazy_monitor` | `services/pump_watcher.py:341`, `:370` | `monitor = self._monitor or self._lazy_monitor()`. `_lazy_monitor` always `return DeviceHealthMonitor(...)` (`:379`) and never returns `None`; a constructor failure raises into the surrounding `except Exception` either way. Tighten the return type to `DeviceHealthMonitor` and dedent the `record(...)` call. (WP4 handoff #2.) | none | low. Behavior is identical. types-final edits other lines of this file. |

---

## 2. Proposed commit order

1. A1 `refactor(core): remove dead code — CreateSchedulerJobRequest (+ golden name)`
2. B1 `refactor(core): remove dead code — is_within_preferred_hours (+ its own tests)`
3. B2 `refactor(core): remove dead code — DEFAULT_PREFERRED_WATER_HOURS (+ golden names, LOGIC.md)`
4. C1 `test: remove dead code — unused grid/stats test helpers` (Gate-1 justification + 2nd reviewer)
5. E1 `refactor(server): remove unreachable branches — anomaly baseline guard, pump_watcher None-monitor arm`
6. D1 *(optional)* `refactor(server): remove dead code — pump_watcher.ALERT_CODE`
7. D2 *(optional)* `refactor: remove dead re-exports — SOURCE_HEALTH, render.Row, engine.Reason`

Golden diffs, all removal-only: `contracts/imports.json` −2 lines (`CreateSchedulerJobRequest`,
`DEFAULT_PREFERRED_WATER_HOURS`) and `contracts/constants.json` −1 line. No rendered, OpenAPI, MCP, CLI-help or TUI
golden changes.

---

## 3. Deferred / decision needed (not in this sweep)

| Item | Where | Why deferred | Proposed resolution |
|---|---|---|---|
| `DeviceGateway.invalidate_key` (C-DEAD-10) | `devices/gateway.py:303-305`; only user is `tests/devices/test_contract_adapters.py:601-602` (frozen) | post-wp8 is editing `gateway.py` | after post-wp8 merges: rule-4 removal (drop the 2 lines from the frozen test, with justification) **or** wire it into the local-auth failure path (behavior change → `fix(consistency)`). The owner chooses. |
| `AbstractSensorAdapter.read_live` / `TuyaSensorAdapter.read_live` | `devices/sensors/base.py:26`, `sensors/tuya_generic.py:34`; users: `tests/fake_devices.py:102`, `tests/devices/test_contract_adapters.py` + golden `ingress_devices/adapter_call_sequences.json:673,688,703`, and `test_contract_health_monitor.py:56` (asserts it is **never** called) | Dead in production: per invariant 8, sync reads via `DeviceGateway.get_live_reading` directly. But it is an ABC contract method pinned by a golden, and devices/ is in post-wp8's area. | owner decision. If removed: rule 4, removal-only golden diff (3 entries) + fake + frozen tests. Medium churn for small value. |
| `DEVICE_BLOCKING_CODES` | `logic/decision.py:99`; `logic/__init__.py` `__all__`; pinned STRICT in `contracts/constants.json:2` + `imports.json:215,230` | No libs reader. `DeviceHealthMonitor.is_actuation_blocked` (`health_monitor.py:220`) hard-codes the same three alarms, so this is duplicated knowledge, not unused design. types-final edits `health_monitor.py`. | **do not remove**. Make it live: `is_actuation_blocked` filters with `HEALTH_ALARM_TO_TRIGGER[alarm] in DEVICE_BLOCKING_CODES`. Same set, so behavior-neutral and no golden change. Hand to the consistency track. |
| `DeviceRegistry.registered_irrigator_keys` / `registered_sensor_keys` | `devices/registry.py:115-119` | test-only, but these are deliberate introspection for the parametrized adapter contract tests (comment at `:113`; `test_adapter_contract.py:68,117`; frozen `test_contract_adapters.py:257-258,327-330`). post-wp8 removes the legacy aliases in this file. | keep (consistent with 46-audit "Not dead") |
| CSS classes in `web/static/app.css` | out of scope (note only) | A naive scan finds 28 classes that never appear literally in templates/JS/py. Most are built dynamically (`badge-{{…}}`, `pill--{{…}}`, `toast--{{…}}`, `dot--…`, `htmx-request` added by htmx, `.contrast`/`.outline` are Pico modifiers). | Possibly unused, needs dynamic-class proof: `.mb-3 .mb-4 .mt-6 .stack .scrollable .row--between .tabs--flush .chart-legend .btn--ghost .bell__badge .cmdk__item*` (check `app.js` string building first). |

---

## 4. Not dead (looked dead) — vulture false-positive classes

| Class (count @60) | Examples | Why it is live |
|---|---|---|
| Typer commands (`commands/*.py`) | every `@app.command` function | registered by decorator; the CLI contract + `--help` goldens (rule 5) |
| API + web route handlers (`routes/`, `web/routes/`) | all route functions | `@router.get/post` registry. Function name = MCP operationId (frozen). |
| Textual handlers/actions (~80) | `compose`, `on_mount`, `on_*`, `action_*`, `SearchScreen._changed/_submitted` | dispatched by name from `BINDINGS` strings / message names, or by `@on(Input.Changed)` (`search.py:43,48`) (rule 5) |
| Textual/Rich class vars and protocols | `TITLE`, `DEFAULT_MODE`, `sub_title`, `display`, `__rich_console__(console, …)`, `__rich_measure__` | read by the framework. `console` is a fixed protocol parameter (vulture 100%). |
| Pydantic `@field_validator` methods | `IrrigatorResponse/SensorResponse.parse_config`, `Settings._validate_sync_interval/_validate_check_cron_hours` | called by Pydantic through the decorator |
| `@app.exception_handler` closures | `web/exception_handlers.py:37,55` | registered by decorator |
| Pydantic / TypedDict / SQLAlchemy fields (~110 vars) | `schemas.py` ×57, `decision.py` (`avg_humidity`, `feels_like`, `learning_alerts`), `stats.py` `IrrigationStats.*`, `WatchOutcome.*`, `vacation.py`, `routes/auth.py` token fields, `models.py` columns, `profile.py` `vendor/transport/dp_parsers/duration_unit` | serialized to OpenAPI / `payload_json` / JSON output / DB DDL (frozen). `profile.py` fields are parsed from the profile JSON and pinned in `adapter_call_sequences`/typed-profile goldens. |
| ORM attribute writes (`unused attribute` ×9) | `repository.py` `last_updated`, `resolved_at`, `acknowledged_at`; `auth.py` `last_login_at`; `engine.py:594` `stress.learning_alerts` | SQLAlchemy column assignments are persisted. `learning_alerts` is a serialized `StressIndicators` field in the decision payload. |
| StrEnum members | `TriggerCode.DAILY_CAP_HIT`, `LEARNING_ALERT` | pinned STRICT in `constants.json` enums, in OpenAPI, and in the icon map (`filters.py:152,169`). Old `decision_logs` payloads may carry them, and LOGIC.md:53 documents `daily_cap_hit` as defined-but-not-emitted. |
| `health_capabilities` class attributes | `irrigators/base.py:29`, `ik10pw.py:96`, `sensors/base.py:23`, `tr301z.py:48` | adapter contract read via the golden `health_capabilities` matrix (`test_contract_adapters.py`) and documented in `DeviceHealthState` |
| `auth.py:85 is_system` | — | asserted by `test_contract_auth.py:399` (Pydantic field) |
| Imports used only in quoted annotations (5 @90) | `repository.py` `CursorResult`, `InstrumentedAttribute`; `app.py` `AsyncIterator`, `AbstractAsyncContextManager`; `scheduler.py` `Job` | `TYPE_CHECKING` imports used in string annotations. mypy needs them. |
| Dunder protocol parameters | `client.py:103 __exit__(*exc_info)` | context-manager protocol |
| Test-support APIs (rule 4 kept) | `database.head_revision`, `plant_db.get_plant_database/reset_plant_database`, `PlantDatabase.list_species/list_categories/get_metadata`, `repository.bulk_add_sensor_readings`, `PixelSprite.height` | used by contract/migration/property tests and the frozen `engine_grid` seeding. `get_/reset_plant_database` are pinned in `imports.json`. Consistent with 46-audit "Not dead". |
| Compatibility re-exports for test-pinned paths | `services/irrigation.py:38-44` (`_add_leak_check_job`, `_leak_check_done`, `_run_leak_check`, `_schedule_leak_check`, `rearm_leak_checks`; `noqa: F401`) | frozen contract tests call `irrigation_mod.<name>` (`test_contract_scheduler.py`, `test_contract_wp7_gaps.py`, `test_leak_rearm.py`). This is a rule-6 shim. |
| Import-for-failure-semantics | `scheduler.py:359-360` local imports (`noqa: F401`) | they force an import failure to escape the job before the session opens (comment at `:356`) |
| Fixture re-export | `tests/cli/test_contract_json_output.py:45` (`noqa: F401`) | pytest resolves fixtures by name from module globals |
| Unreachable-looking guards kept on purpose | `engine._seasonal_overrides` `isinstance(care, dict)` (WP8 §3.2, by design); `health_monitor._alarm_message:352` fallback after the exhaustive `HealthAlarm` chain; `truthy-bool`/`redundant-expr` leads in 40-review-typing | removing them needs `assert_never` or narrows device-data defenses. That is a behavior change, not dead code. |
| `pump_watcher.WatchOutcome` keys, `stats.IrrigationStats.*` | — | TypedDict keys are read as string subscripts (`str=` hits in the AST sweep) and in JSON output goldens |
| `sync.sync_sensor_data` (AST lead) | — | imported as `core_sync` in `services/sync.py:14` |

Side note (not dead code): during the concurrent test runs on this machine, the agent proxy logged one rejected
outbound CONNECT to `api.open-meteo.com:443`. It could not be attributed to a specific test, because other worktrees
were running suites at the same time. If a test reaches the real weather client, that breaks the repo's "no real
network calls" rule, so it is worth checking at the final gate.

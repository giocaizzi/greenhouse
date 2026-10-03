# Phase 4 review: DRY / YAGNI / dead code

Reviewer: DRY/YAGNI + dead-code reviewer. Tree: `3491e23` (clean). Scope: `libs/`. Excluded because WP8 is still
running: `logic/engine.py`, `logic/timing.py`, `devices/**`, `core/sync.py`. Those files were still searched as
*callers*, so no candidate below is live only because of an excluded file. No code was changed and no tests were run.

## Tool runs

| Tool | Command | Result |
|---|---|---|
| jscpd | `npx --yes jscpd --min-lines 6 --reporters console --ignore "**/migrations/**,**/__pycache__/**,**/static/**" --format python,markup,css libs/` | **48 clones**. Python: 11 clones, 122 lines (0.48 %). Templates: 37 clones, 305 lines (7.29 %). |
| vulture | `uvx vulture libs/ --min-confidence 60 --exclude "*/migrations/*"` | 368 leads. After grep checks, **5 are real dead code**. 2 more are test-only. The rest are false positives: Typer/route/Textual handlers, `BINDINGS`/`CSS_PATH`, Pydantic fields, `@field_validator` and `@app.exception_handler` closures, `@on(...)` handlers, and imports that are only used in quoted annotations. |
| custom sweeps | Ref counts for: module-level names (all files), `def` names, repository methods, CLI client methods, `app.tcss` selectors, template files, filters registered in `filters.ALL_FILTERS`, Jinja macros, `Settings` fields, `__all__` entries, test helpers. Scripts are in the session scratchpad and not committed. | Results are in the sections below. |

Checked and **clean** (nothing to remove):
- every `app.tcss` `#id`/`.class` selector matches a widget in `tui/**/*.py`;
- every `Settings` field is read;
- all 12 template filters are used in templates;
- the one Jinja macro (`range_window`) is used 3 times;
- every `__all__` entry resolves;
- every CLI `IrrigationClient` method has a caller;
- the TUI helper modules (`formatting`, `render`, `model`, `widgets`, `sprites`) have no unused functions;
- every fixture in `tests/conftest.py`, `tests/server/conftest.py` and `tests/cli/tui_fixtures.py` is used.

Items already tracked by `46-consistency-audit.md` (C-DEAD-1…11) were checked again. They are removed, except the
two that are waiting on WP8 (C-DEAD-9 `is_within_preferred_hours` and C-DEAD-10 `invalidate_key`).

---

## 1. Dead code (verified)

Every row below was checked against `libs/`, `tests/`, `*.html`, `app.tcss`, `plugin/`, `CLAUDE.md`, entry points and
dynamic access (`getattr`, decorators, registries, `__all__`). The base grep for every row was:
`grep -rnw <name> libs tests plugin CLAUDE.md --include=*.py --include=*.html --include=*.tcss --include=*.md --include=*.json`.

| # | Item | Evidence (results with `tests/golden` excluded) | Action | Behavior | Golden / rule-4 |
|---|---|---|---|---|---|
| DC1 | `schemas.CreateSchedulerJobRequest` (`schemas.py:570`) | 0 refs in libs, tests and plugin. It is not in `openapi.json`: `grep -c '"CreateSchedulerJobRequest"' tests/golden/contracts/openapi.json` → 0. No POST `/scheduler/jobs` route exists (`routes/scheduler.py` has only GET and DELETE). It is left over from #31. | remove | none (not in OpenAPI) | `imports.json:369` → rule 4, removal-only diff |
| DC2 | `deps.SessionDep` (`deps.py:238`), `deps.DeviceGatewayDep` (`deps.py:241`) | 0 uses. The provider factories write out `Annotated[Session, Depends(get_session)]` (`deps.py:35`) and `Annotated[DeviceGateway \| None, Depends(get_device_gateway)]` (`deps.py:193`) inline, because the alias block sits at the bottom of the file. | **Preferred: no removal.** Move each alias right below its provider and use it in the factories (see DRY-7). That makes both aliases live and needs no golden edit. Fallback: remove them. | none (`Annotated` aliases are equal by value) | fallback only: `imports.json:619,637` (rule 4) |
| DC3 | `auth.AuthUserDep = AuthenticatedUser` (`auth.py:354-355`) | 0 uses. The name is misleading: it is a plain class alias, not an `Annotated[..., Depends]`. | remove alias + comment | none | `imports.json:555` (rule 4) |
| DC4 | `plant_db.set_plant_database` (`plant_db.py:175`) | 0 refs in libs, tests and plugin. The server builds its own `PlantDatabase` (`app.py:358-359`), so this singleton setter has no caller. | remove | none | `imports.json:301` (rule 4) |
| DC5 | `models.EVENT_ACTION_OFF = "off"` (`models.py:58`) | 0 refs. OD4 landed: no code writes `"off"`, and `grep -rn '"off"' libs --include=*.py` finds only the configs form parser. Old rows stay valid: they are plain strings, and `plugin/.../CLI.md:110` already documents legacy `off`. | remove the constant; keep a one-line comment next to `EVENT_ACTION_STOP`: "rows recorded before OD4 may carry `off`" | none | not pinned (`grep -rlw EVENT_ACTION_OFF tests/golden` → empty) |
| DC6 | `web/templates/partials/_sensor_row.html` | No `{% include %}`, `TemplateResponse` or dynamic include references it: `grep -rn "_sensor_row" libs tests --include=*.py --include=*.html` → 0. The `sensor_rows` matches are an unrelated TUI function. Last touched in #31. It also hardcodes `moisture_badge(45, 65)`. | delete the file | none (never rendered) | `package_data.json:74` → rule 4, removal-only diff |
| DC7 | `tests/engine_grid.py:1198 case_with` | `grep -rnw case_with tests refactor/scripts` → only the definition | remove | none (test helper) | — |
| DC8 | `constants.CONFIDENCE_BASELINE` (`constants.py:265`) | 0 refs. Already tracked as C-DEAD-12, waiting for WP8 T8.1. | **re-check after WP8.** Remove only if the engine did not adopt it. | none | not in `constants.json` |

Test-only users. These are not dead under rule 1; rule 4 would apply. Classified **keep** unless noted:

| Item | Users | Verdict |
|---|---|---|
| `repository.bulk_add_sensor_readings` (`repository.py:386`) | `tests/test_db.py:249,270`, `tests/engine_grid.py:248,307` (grid seeding). Production uses `add_sensor_reading` only (`sync.py:82,100`). Its insert duplicates `add_sensor_reading`'s `sqlite_insert … on_conflict_do_nothing`. | **optional (rule 4):** move a loop into `engine_grid` and drop the method and its 2 tests. Otherwise keep it and share one `_reading_insert_stmt()` with `add_sensor_reading` (DRY-9). |
| `plant_db.get_plant_database` / `reset_plant_database` | Only tests (30 call sites, `test_plant_db.py`). Not used in libs. | keep (test-support singleton, pinned) |
| `PlantDatabase.list_species` / `list_categories` / `get_metadata` | `test_plant_db.py`; the first two are also data sources in `test_properties_logic.py:278-280`. | keep (read API; removing would rewrite property tests) |
| `database.head_revision` | `test_migrations.py`, `test_contract_schema.py` (asserts Alembic head) | keep (contract-test support, pinned) |
| `PixelSprite.height` (`sprites.py:285`) | `tests/cli/test_tui.py:138-176` only | keep (one line, pairs with `width`) |
| `health_monitor.__all__` re-export of `SOURCE_HEALTH` (`health_monitor.py:383`) | only `tests/server/test_pump_watcher.py:21` and `test_health_monitor.py:26` import it from there | simplify: those tests import from `greenhouse_core.models`, then drop the name from `__all__` |

Vulture leads confirmed as **false positives**. Never remove these:
- Typer commands (`commands/*.py`), API and web route handlers;
- `@app.exception_handler` closures (`web/exception_handlers.py:38,56`);
- `@field_validator` methods (`config.py:49,56`, `schemas.py:124,187`);
- `@on(...)` handlers (`tui/screens/search.py:40,44`);
- Textual `BINDINGS`/`CSS_PATH`/`TITLE`/`DEFAULT_MODE`/`sub_title`/`display`;
- `__rich_console__` parameters;
- Pydantic/ORM fields;
- imports used only in quoted annotations: `repository.py:50-51`, `app.py:61-62`, `scheduler.py:27`, `services/_session.py:10`, `services/irrigation.py:49`.

---

## 2. Duplication left (knowledge, not look-alikes)

| # | Where | Duplicated knowledge | Fix | Behavior |
|---|---|---|---|---|
| DRY-1 | `routes/bulk.py:28-37` vs `web/routes/analytics.py:184` | **Untracked drift:** "an emergency stop notifies ntfy". The rule lives only in the API route. The web kill switch calls `stop_all_irrigators` and **never notifies**. `routes/bulk.py:29` is the only `maybe_notify` call outside `services/` (`grep -rn "maybe_notify(" libs`). | Move the notification into `services/bulk.stop_all_irrigators(repo, registry, notifier)`, which the web handler also uses. Ship as a labeled `fix(drift)` and add it to `45-drift-track.md`. | **BEHAVIOR:** the web stop-all starts notifying. |
| DRY-2 | `web/routes/{configs.py:24-58, plants.py:17, irrigators.py:53, sensors.py:17, vacation.py:41, windows.py:24}`, plus inline `int(x) if x.strip() else None` at `configs.py:82-83,116-117` and `operations.py:40,117-118` | Six copies of "blank form field → None, else parse, else 400". This is C-DUP-3 / A9 and it is **still open**: `web/forms.py` does not exist (CONS-W2 handoff line 93). | `web/forms.py` with `optional_int/optional_float(raw, *, error, non_negative=False)` and `tri_bool`. Keep each site's exact error text. The bare `int()` 500s are recorded bugs: leave them for a separate fix. | none |
| DRY-3 | `routes/vacation.py:18` ≡ `web/routes/vacation.py:33`; `routes/windows.py:26` ≡ `web/routes/windows.py:37` | Four wrappers that each map a domain `ValueError` to `HTTPException(400, str(exc))`. API and web do the same thing, because the web exception handler already turns HTML vs JSON on path. | Two helpers in `deps.py`, next to `require_metric`: `require_valid_vacation_range(starts, ends)` and `require_valid_window(start, end, mask)`. Delete the 4 private copies. | none (same status code and detail) |
| DRY-4 | `commands/configs.py:22-33` ≡ `:73-84` (13 lines); `commands/irrigators.py:35-45` ≡ `:109-119`; `commands/plants.py:16-26` ≡ `:90-100` | The same Typer option definitions (flag, type, help) are written twice. `Annotated[int, typer.Option(help="Cluster ID")]` appears 9 times and `--yes/-y` 7 times. | Module-level `Annotated` aliases, e.g. `ClusterOpt`, `YesOpt`, `ModeOpt`, `QuietStartOpt`, in `commands/_helpers.py`. Help text and flags stay byte-identical. Prove it with the CLI `--help` goldens. | none |
| DRY-5 | `web/routes/plants.py:67-99` vs `:110-143` (jscpd 13+18 lines); `web/routes/irrigators.py:79-87` vs `:136-144` | The plant and irrigator form field lists (9 `Form("")` params) are restated in create and update. | A `@dataclass` form dependency (`PlantForm = Annotated[_PlantForm, Depends()]` with `Form` defaults) feeding `_plant_form_fields`. Field names stay frozen. | none (same form keys and 422 shape: verify against the web goldens) |
| DRY-6 | `services/forecast.py:27` `_WEATHER_PRECIP_THRESHOLD_MM = WEATHER_SKIP_PRECIP_MM`; `forecast.py:26` `_FALLBACK_DRAINAGE_PER_HOUR = -2.0`; engine `engine.py:562` `if precip <= 2.0` | The rain-skip threshold is written in 2 places: forecast uses the constant through a pass-through alias, while the engine hardcodes `2.0`. The drainage fallback is a threshold that lives outside `constants.py` (invariant 5). | Use `WEATHER_SKIP_PRECIP_MM` directly in forecast. **WP8:** the engine adopts it. Move `_FALLBACK_DRAINAGE_PER_HOUR` into `constants.py`. | none (`constants.json` gains one name; owner OK needed) |
| DRY-7 | `deps.py:35,190-232` vs `deps.py:238-248` | Each dependency is spelled twice: inline `Annotated[...]` in the factories, then again as an alias. `routes/forecast.py:12-23` keeps its own `get_forecast_service` + `ForecastServiceDep`, outside `deps.py`. | Declare each alias right after its provider and use it in later factories. Move the forecast factory and alias into `deps.py`. This fixes DC2 without a golden edit. | none |
| DRY-8 | `repository.py:1263` `_patch_fields` vs `:1279` `_patch_fields_hasattr_first` | Two PATCH helpers that give the same result. The only difference is whether `hasattr` runs for `None` values, and `hasattr` on a mapped column is a no-op. Both skip `None` and unknown keys. Routes already `model_dump(exclude_none=True)`. | Merge into `_patch_fields`. `tests/test_contract_repository_gaps.py` covers both through the public `update_*` methods and needs no edit. | none for column keys. Note: an expired instance could see one fewer refresh SELECT (not observable). |
| DRY-9 | `repository.py:326-368` vs `:386-426` | The same `sqlite_insert(SensorReading)…on_conflict_do_nothing` statement is built twice. | Either do the optional rule-4 removal of the bulk method, or extract `_reading_insert_stmt(**cols)`. | none |
| DRY-10 | `web/routes/clusters.py:193,240,256` | "status is None → 404 Cluster not found", 3 times. W2 left these after it introduced `require_*`. | `require_cluster_status(svc, cluster_id)` in `deps.py`. | none |

Accepted duplication (different reasons to change, or forced by the package boundary). **Do not merge:**
- API list routes `routes/{sensors,irrigators}.py` (frozen signatures and docstrings);
- one-line `next_cursor` expressions;
- Pydantic `*Base`/`Update*Request` pairs (OpenAPI field order frozen);
- `models.py` `last_updated` columns (DDL);
- the CLI/TUI mirrors in `greenhouse_cli/constants.py` and `tui/resources.py`, and TUI `formatting.py` vs web `filters.py` (the CLI may not import core: C-DUP-4);
- `rearm_leak_checks` and `_run_leak_check` scaffolding vs `job_session` (documented in `_session.py`);
- the 37 template clones (`clusters/detail.html` ×6, `irrigators/edit.html` ×5, `clusters/edit.html` ×5, …). Jinja macros could fold them, but rendered output is frozen. Low value against whitespace risk, so leave them unless a byte-identical golden run proves it.

Still open from the audit, not new: **C-DUP-5** (18× `getattr(app.state, "<x>", None)`; still 18 by `grep -rn "getattr(.*\.state, \"" libs`) and **C-TX-4/B2** (duplicate `get_session`/`_get_settings` in `auth.py` vs `deps.py`). Both need a shared `state.py`. Schedule them; they are not re-litigated here.

**Cross-WP hazard:** OD3 removal of `LEGACY_IRRIGATOR_ALIASES` (`devices/registry.py:39-43`, WP8) must land **in the same PR** as:
- `irrigators/new.html:39-40,45,78` and `edit.html:34-35,40`, which offer only `tuya_cloud`/`tuya_local`;
- the CLI `irrigator add` help `"tuya_cloud or tuya_local"` (`commands/irrigators.py:34`).

If it lands without them, the web form creates irrigators that `get_irrigator` cannot resolve (`UnknownDeviceModel`).

---

## 3. Over-engineering: abstraction inventory

| Abstraction | Where | Users | Verdict |
|---|---|---|---|
| `job_session` contextmanager | `services/_session.py:21` | 5 scheduler jobs | keep |
| `ClusterStatus`, `ClusterHistory` TypedDict | `services/cluster.py:25,36` | 1 producer each, typed per OD1 | keep |
| `PlantSyncResult`, `StopAllResult` NamedTuple | `services/cluster.py:44`, `services/bulk.py:11` | callers unpack them as tuples | keep |
| `PipelineResult`, `MonitorResult`, `CheckResult` TypedDict | `services/irrigation.py:449-482` | 6, 2 and 8 refs; they feed `IrrigateResponse(**result)` | keep |
| `_Actuation` dataclass (parameter object) | `irrigation.py:520` | the actuation half of the pipeline | keep (it replaces a 7-argument list) |
| `_rearm_from_events` generator that yields `None` to count | `irrigation.py:398` | 1 | **keep, but it is a quirk:** the yield only exists to preserve the partial count on a mid-scan failure (a pinned test). Simplify only if that test is accepted as an incidental pin. |
| `WatchOutcome`, `HealthScore`, `JobInfo` TypedDict | `pump_watcher.py:65`, `health.py:21`, `scheduler.py:429` | 2-3 refs each | keep |
| `_HealthBands` (dataclass + `from_care`) | `health.py:44` | 1 | keep (it names 3 tuple pairs) |
| `ClusterBudget`, `DeviceHealth`, forecast and cleaning dataclasses | various | existing API/read models | keep |
| `IrrigationRecord`, `IrrigationStats`, `StatsUnavailable` | `stats.py:16-38` | `get_irrigation_stats` | keep |
| `IrrigationLearner` facade (4 pure pass-through methods) | `learning/learner.py` | 4 constructors (maintenance ×2, forecast, engine). `analyze_irrigation_response` via the facade has no libs caller. | keep (public `greenhouse_core.learning` surface; engine is WP8). Do not add more facade methods. |
| `deps.get_*_service` factories | `deps.py:190-232` | DI | keep (they are the FastAPI override points). Apply DRY-7. |
| `web/filters.age_seconds` / `time_ago` (one-line wrappers around `format_age` / `relative_age`) | `filters.py:63,71` | filter names are frozen | **simplify:** register `"age_seconds": format_age, "time_ago": relative_age` in `ALL_FILTERS` and delete the wrappers. The defaults are identical. Python callers: `tests/server/test_web_filters*` (5 refs) switch to the registry or the base functions. |
| `routes/configs._request_fields` (wraps `model_dump(exclude_unset=True)`) | `routes/configs.py:18` | 2 | keep (its docstring carries the null-means-clear rule) |
| `web/weekdays.py`, `services/windows.py`, `services/vacation.validate_vacation_range` | — | API + web | keep (the shared rules). DRY-3 removes the wrappers around them. |
| Type aliases `CareData`, `JSONObject`, `_Buckets`, `Metric`, `SpriteFactory`, `ClientFactory` | — | 3-84 refs | keep |
| `_patch_fields_hasattr_first` | `repository.py:1279` | 3 | **remove** (DRY-8) |
| `health_monitor.__all__` re-exporting `SOURCE_HEALTH` | `health_monitor.py:380-384` | tests only | **simplify** (see §1) |

No speculative Protocols, plugin registries or unused extension points were introduced in reviewable scope.
`DeviceRegistry` and the adapter ABCs are in `devices/` (WP8).

---

## 4. Dead-code removal list (ready to execute, one commit each)

Run `uv run pytest tests/server tests/test_db.py tests/test_plant_db.py tests/test_contract_*.py -x -q` (or the
relevant subset) before each commit.

1. `refactor(core): remove dead code — CreateSchedulerJobRequest (+ golden name)`
   - `schemas.py:570-575`, `imports.json:369`.
   - Evidence: `grep -rnw CreateSchedulerJobRequest libs tests plugin` → definition + golden only; openapi count 0.
2. `refactor(core): remove dead code — set_plant_database (+ golden name)`
   - `plant_db.py:175-178`, `imports.json:301`.
3. `refactor(core): remove dead code — EVENT_ACTION_OFF`
   - `models.py:58`; keep the legacy-`off` comment.
   - Evidence: `grep -rnw EVENT_ACTION_OFF libs tests plugin` → definition only.
4. `refactor(server): remove dead code — AuthUserDep alias (+ golden name)`
   - `auth.py:354-355`, `imports.json:555`.
5. `refactor(server): deps — declare each Annotated alias after its provider and use it` (DRY-7)
   - This makes `SessionDep`/`DeviceGatewayDep` live. No golden edit.
   - *Alternative if rejected:* `refactor(server): remove dead code — SessionDep, DeviceGatewayDep (+ golden names)`.
6. `refactor(web): remove dead code — partials/_sensor_row.html (+ package_data golden entry)`
   - Evidence: `grep -rn _sensor_row libs tests --include=*.py --include=*.html` → 0; golden `package_data.json:74`.
7. `test: remove dead code — engine_grid.case_with`
8. *(after WP8)* `refactor(core): remove dead code — CONFIDENCE_BASELINE` (only if unadopted), plus C-DEAD-9 and C-DEAD-10 as already queued.
9. *(optional, rule 4)* `refactor(core): remove dead code — bulk_add_sensor_readings (+ its 2 tests; engine_grid seeds via add_sensor_reading)`.

## 5. Top 10 DRY/YAGNI fixes (by value ÷ risk)

1. **DRY-1:** move the emergency-stop notification into `stop_all_irrigators`. The web kill switch then notifies too. This is a labeled `fix(drift)`; add it to the drift track.
2. **Cross-WP hazard:** pair the WP8 OD3 alias removal with the irrigator type `<option>`s in the web templates and the CLI `--type` help.
3. **DRY-2 / A9:** create `web/forms.py` for the six blank-or-parse form parsers and the inline `int(x) if x.strip()` sites (error text preserved).
4. **DRY-3:** add `require_valid_vacation_range` / `require_valid_window` in `deps.py` and delete the 4 copied 400-mapping wrappers.
5. **DRY-4:** shared Typer `Annotated` option aliases for the `config set`/`global set`, `irrigator add`/`update` and `plant add`/`update` pairs, plus `ClusterOpt`/`YesOpt`. Prove it with the `--help` goldens.
6. **DRY-7:** `deps.py` aliases declared once and reused by the factories; move `get_forecast_service`/`ForecastServiceDep` into `deps.py`.
7. **DRY-8:** fold `_patch_fields_hasattr_first` into `_patch_fields`.
8. **DRY-6:** forecast uses `WEATHER_SKIP_PRECIP_MM` directly; `_FALLBACK_DRAINAGE_PER_HOUR` moves into `constants.py`; WP8 makes the engine use the constant instead of `2.0`.
9. **DRY-5 / DRY-10:** a plant/irrigator form dataclass dependency, and `require_cluster_status` for the 3 repeated None→404 checks.
10. **YAGNI cleanups:**
    - register `format_age`/`relative_age` directly as the `age_seconds`/`time_ago` filters;
    - drop the `SOURCE_HEALTH` re-export from `health_monitor.__all__`;
    - share one reading-insert statement in the repository (DRY-9).
    - Schedule the still-open C-DUP-5 (`state.py` accessors) and C-TX-4.

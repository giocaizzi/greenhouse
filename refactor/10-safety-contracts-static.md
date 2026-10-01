# 10 — Safety net: contracts-static (G1, G2, G7, G8, G9, G10, G11 + scheduler registry)

Work package "contracts-static" from `refactor/00-tests.md` §6. Only new files were created; no production
code, existing test, fixture or conftest was touched. Nothing committed.

## Files

| Test file | Goldens (`tests/golden/contracts/`) |
|---|---|
| `tests/server/test_contract_openapi.py` (G1) | `openapi.json`, `routes.json` |
| `tests/server/test_contract_mcp.py` (G2) | `mcp_tools.json` |
| `tests/server/test_contract_settings.py` (G8) | `settings_schema.json`, `settings_validation_errors.json`, `env_reads.json` |
| `tests/server/test_contract_scheduler_registry.py` | `scheduler_jobs.json` |
| `tests/test_contract_packaging.py` (G7) | `package_data.json` |
| `tests/test_contract_schema.py` (G9) | `ddl.sql`, `orm_columns.json`, `migrated_schema.json`, `schema_drift.json` |
| `tests/test_contract_imports.py` (G10) | `imports.json`, `loggers.json` |
| `tests/test_contract_constants.py` (G11) | `constants.json` |

Plus `refactor/scripts/check_wheels.sh` (not pytest) and its baseline output `refactor/baseline/wheel-contents.txt`
(248 entries: core 59, server 147, cli 42; `uv build` worked offline from cache, ~2 s). Every file in
`package_data.json` was cross-checked to be present in the built wheels.

## What is pinned (test → contract)

**OpenAPI / routes (G1)**
- `test_openapi_document_matches_golden` → full `app.openapi()`, **key order preserved** (path order = router
  include order, property order = Pydantic field order; FastAPI sorts `components.schemas` itself). Checked
  stable across `PYTHONHASHSEED` 1/2/3 before choosing an order-preserving dump.
- `test_openapi_matches_phase0_baseline_fingerprint` → sorted-JSON sha256 equals the Phase-0 value
  `f983a5a8…` from `00-contracts.md`: the golden is the baseline, not a post-hoc capture.
- `test_openapi_header_fields` → title, `info.version == "1.0.0"`, `openapi == "3.1.0"`, the 14 declared tags in order.
- `test_operation_id_is_route_function_name` → operationId == route function name for all 84 operations / 66 paths.
- `test_route_table_matches_golden` → all 177 entries of `app.routes` (flattened through FastAPI 0.141's
  `_IncludedRouter` with `fastapi.routing._iter_routes_with_context`), **in registration order**: kind, methods,
  path, name, response_model (module-free, e.g. `list[ClusterResponse]`), status_code, tags,
  include_in_schema, response_class, route-level dependencies. So it also pins **which routes are gated**
  (`require_user` ×81, `require_web_user` ×82, `require_mcp_token` ×1, none ×13: 3 auth API, 3 web auth,
  2 well-known, 4 docs routes, plus the `/static` mount).
- `test_route_table_counts` → 84 schema ops, 85 web routes, one `/static` mount, `/mcp` = DELETE/GET/POST.

**MCP (G2)** In fastapi-mcp 0.4.0, `FastApiMCP.setup_server` builds `mcp.tools` (`list[mcp.types.Tool]`) and
`mcp.operation_map` once from the app's OpenAPI.
- `test_mcp_surface_matches_golden` → server name/description and filter settings, every tool
  (`model_dump(exclude_none)`: name, description, inputSchema; order as served), and operation → (method, path).
- `test_mcp_tools_match_phase0_baseline_fingerprint` → equals Phase-0 `dc15b6ab…`.
- `test_tools_list_handler_returns_exactly_mcp_tools` → the protocol `tools/list` handler returns the same list.
- `test_tool_names_are_operation_ids_one_to_one` → order and set match the OpenAPI operationIds.
- Auth: `test_mcp.py` already pins 401/503 detail strings and the accept path, so this file only adds the exact
  JSON body for GET/POST/DELETE × {missing, wrong token, `Basic` scheme, empty bearer}, the 503 body on every
  method, and the dropped `WWW-Authenticate` header (bug below).

**Settings + env (G8)**
- `test_settings_schema_and_field_table_match_golden` → `model_json_schema()` plus per-field
  (declaration order, annotation, default, alias, AliasChoices).
- `test_settings_model_config_explicit_keys`, `test_settings_defaults_with_empty_environment` (all 26 defaults).
- `test_each_field_reads_its_env_var` / `test_env_names_are_case_insensitive` → one row per field (26 env names).
  `test_env_table_covers_every_field` keeps that table in step with `Settings.model_fields`.
- `test_aliased_fields_current_behavior_read_three_env_names` and `test_aliased_fields_ignore_double_prefixed_name` (surprise below).
- `test_dotenv_in_cwd_is_loaded_and_env_beats_it` → `.env` in cwd is read; precedence kwargs > env > `.env` >
  default; `_env_file=None` ignores it. `test_extra_inputs_are_ignored`, `test_check_cron_hours_explicit_tracks_any_source`.
- `test_startup_validation_messages_match_golden` → **exact** pydantic `msg` / `type` / `loc` for 13 bad values
  (`test_scheduler_settings.py` only checks substrings), including APScheduler's own error text.
  `test_env_sourced_values_fail_with_the_same_message_as_kwargs`.
- `test_resolve_check_cron_hours_warning_texts` → the two exact deprecation warnings (logger `greenhouse_server.scheduler`).
- `test_direct_env_reads_match_golden` → AST scan of every `os.environ.get` / `os.getenv` / `os.environ[...]` in
  `libs/`: (module, call, var, default literal). That covers the 8 documented vars plus `XDG_CONFIG_HOME`
  (`test_env_read_scan_finds_exactly_the_documented_vars`). Where it was cheap, the vars are also pinned by behavior:
  - `test_tuya_env_vars_feed_device_gateway`: env → `tinytuya.Cloud(apiRegion, apiKey, apiSecret)`, region
    defaults to `"eu"`, exact `ValueError` text when credentials are missing.
  - `test_plant_db_path_env_is_read_at_import_time`: subprocess; unset or empty → the bundled JSON.
  - `test_irrigation_tz_is_display_timezone_fallback`.
  - `test_cli_server_url_env_and_default`: `_helpers.get_client`, `auth._login_client`.
  - `test_cli_api_token_env_and_token_file`: strip / blank → None, XDG and HOME fallback.
  - The migrations-env `IRRIGATION_DB_URL` and the `tui.py` read are pinned only by the AST golden.

**Scheduler registry**
- `test_scheduler_registry_matches_golden` covers 4 scenarios: default, custom cadence, legacy
  `check_interval_hours=6`, and persisted `timezone=Europe/Rome` + `scheduler_paused` followed by a rebuild.
  For each it records:
  - the pending registry right after `create_app`: order, id, name, `func_ref`, trigger type / str / repr /
    fields / interval / start_date / timezone / jitter, executor, args, pending;
  - `get_jobs()` dicts, `core_job_ids()`, `is_check_all_paused()`;
  - after `start_scheduler(paused=True)`: the job-store view (store order = next-run order),
    `misfire_grace_time=None`, `coalesce=True`, `max_instances=1`, `next_run_time`, pause state, running `get_jobs()`.

  The clock is frozen before the app is built (interval `start_date = now + interval`), so nothing is normalized.
- `test_registration_order_and_identity` (literal table, `_app is app`, `_JOB_DEFAULTS`, `_TZ_BOUND_CRON_JOBS`),
  `test_rebuilding_the_app_does_not_duplicate_jobs`, `test_registry_logs_legacy_interval_warning_once_per_build`.

**Packaging (G7)**
- `test_pyproject_console_scripts_and_wheel_packages` → `[project.scripts]`, hatch wheel packages and build
  backend for each of the 3 libs; the root has no scripts.
- `test_installed_console_scripts_resolve` → `importlib.metadata` entry points load to `greenhouse_cli.main.app`
  (a `typer.Typer`) and `greenhouse_server.app.main`.
- `test_server_main_wiring` → call order `load_dotenv()` → `create_app(Settings)` → `uvicorn.run(app, host=, port=)`.
  `Settings` is built after `load_dotenv` (the fake `load_dotenv` sets env vars and the test checks they took effect).
- `test_package_data_matches_golden` → non-.py files plus Alembic scripts per package.
- `test_runtime_resolution_of_package_data` → plant DB resource, `alembic.ini` / `script_location`, device profiles,
  templates / static dirs, TUI `CSS_PATH`.

**Schema (G9)**
- `test_orm_ddl_matches_golden` and `test_orm_ddl_matches_phase0_fingerprint` (`7b6a2d72…`).
- `test_orm_column_metadata_matches_golden` → Python-side defaults / onupdate, flags, FKs, constraints, mapped classes.
- `test_alembic_head_is_literal` (`9f2b5e7c6a31`) and `test_alembic_revision_chain` (all 10 revisions, single head).
- `test_migrated_schema_matches_golden` → reflection after `init_db` on a temp file.
- `test_schema_drift_current_behavior_migrations_differ_from_create_all` (bug below).

**Imports (G10)**
- `test_public_import_surfaces_are_a_superset_of_golden` → 18 modules, public `dir()` plus `__all__` (superset semantics). Each module is captured in
  a fresh forked child of one subprocess, so the result does not depend on what else the test process imported.
- `test_patched_attribute_path_resolves` → 19 dotted paths, each resolving to the expected kind of object
  (`devices.tinytuya[.Cloud]`, `gateway.tinytuya.OutletDevice`, `engine.season_for`,
  `issues.seasonal_light_factor` / `effective_light_threshold`, `cli.tui.run`, `irrigation._time`,
  `repository.time.time`, `engine.time.time`, `scheduler.logger.warning`, `scheduler._resolve_check_cron_hours`,
  `scheduler._app`, `LeakDetectionService.check_after_irrigation`, `IrrigationService.check_cluster`, and
  `server.auth._get_settings` / `_session_from_app` / `_RedirectAuthError`).
- `test_patched_modules_are_the_real_modules`, `test_reexports_point_at_their_definitions`.
- `test_module_level_logger_names_are_kept` → 15 modules (both `logger` and `log` attribute names). Superset semantics, plus an exact assertion on the `services.irrigation` logger.

**Constants (G11)**
- `test_golden_constants_still_exist_with_equal_values` (superset) → 100 UPPERCASE constants as `repr`, which keeps
  int vs float and tuple vs list.
- `test_enums_and_blocking_codes_match_golden_exactly` (strict) → `Action`, `Severity`, `TriggerCode` and
  `HealthAlarm` members in definition order, plus `DEVICE_BLOCKING_CODES`.
- Invariant tests: types of constants, the first 3 enums in `decision.py`, StrEnum value semantics,
  46 TriggerCodes with no aliases, and the CLAUDE.md thresholds.

## Golden comparison semantics (orchestrator policy)

- **Superset / compatibility:**
  - `imports.json`: every golden module still captured; every golden public name still in `dir()`; every golden
    `__all__` entry still in `__all__`.
  - `constants.json` → `constants`: every golden name still exists with an equal `repr`, so the type is equal too.
  - `loggers.json`: every golden module that still exists keeps each golden logger name.
  - New names, constants, modules and enum classes are allowed. Tests:
    `test_public_import_surfaces_are_a_superset_of_golden`, `test_golden_constants_still_exist_with_equal_values`,
    `test_module_level_logger_names_are_kept`.
- **Strict:**
  - Each golden enum's members (name, value, order) and `DEVICE_BLOCKING_CODES`
    (`test_enums_and_blocking_codes_match_golden_exactly`).
  - Everything else: OpenAPI, routes, MCP, settings, validation messages, env reads, DDL, ORM columns, migrated
    schema, drift, scheduler registry and package data are byte-equal.
- `GOLDEN_UPDATE=1` always writes the full observed snapshot.
- Every golden is under 400 KB (largest is `openapi.json` at 206 KB).

## Normalization

- The package version is replaced with `<VERSION>` in the OpenAPI golden and in the wheel listing. It does not
  actually occur in the OpenAPI document: `info.version` is the hard-coded `"1.0.0"` and is pinned verbatim
  because release-please never bumps it.
- `ddl.sql` has trailing spaces stripped per line and a single final newline, so the repo's `trailing-whitespace`
  / `end-of-file-fixer` pre-commit hooks don't rewrite it. The exact bytes are still pinned by the sha256
  fingerprint test.
- Nothing else is normalized: times are frozen, and dicts are sorted only where the production code defines no order.

## NOT pinned (and why)

- `Settings.model_config` beyond the 5 explicitly set keys: the rest are pydantic-settings defaults that change
  with library version, not with our code.
- The bootstrap-admin log messages in `server/auth.py`: outside G8's scope.
- How `IRRIGATION_DB_URL` behaves in `migrations/env.py` (the Alembic CLI path without a connection): only its
  name and default are pinned, via AST.
- Full wheel contents in pytest (too slow). The script plus the baseline file cover it.
- The module paths of route handlers: the route golden uses names only, so moving a handler between modules
  stays legal.
- Whether `mock.patch("…engine.season_for")` actually takes effect: the existing tests exercise that. Here only
  resolution and identity are pinned.

## Determinism proof

Commands, all over the 8 files:

- `uv run pytest <files>` twice;
- `-p xdist -n 2`;
- `TZ=America/New_York uv run pytest <files>`;
- `PYTHONHASHSEED=12345 uv run pytest <files>`.

Results (165 tests; 164 before the superset rework added one constants test):

| Run | Result |
|---|---|
| plain | 164 passed (58 s) |
| plain, second run | 164 passed |
| `-p xdist -n 2` | 164 passed |
| `TZ=America/New_York` | 165 passed |
| `PYTHONHASHSEED=12345` | 165 passed |
| final, after the superset rework: `TZ=America/New_York` + `-p xdist -n 2` | 165 passed (46 s) |

The changed imports and constants files were also run twice more after the rework, and regenerating them with
`GOLDEN_UPDATE=1` reproduced byte-identical goldens.

The golden sha256 values were recorded before these runs and compared afterwards: unchanged. `tests/golden/`
is listed in `.git/info/exclude`, so `git status` cannot show it — compared by checksum instead.
`ruff check` and `ruff format --check` are clean on all 8 files.

Existing tests for the same area still pass:

- Files: `test_mcp`, `test_well_known`, `test_scheduler_settings`, `test_scheduler_jobs`, `test_scheduler`,
  `test_scheduler_pause`, `test_migrations`, `test_db`, `test_utils`, `test_plant_db`, `test_cloud`.
- Result: **158 passed** with `-n 2`.
- Pre-existing pytest quirk, not caused by this work: if the command line lists a `tests/server/` file, then a
  `tests/` core file, then another `tests/server/` file, the later server file errors with
  `fixture 'client' not found`. Reproduced with only pre-existing files: `test_mcp.py test_migrations.py
  test_well_known.py`. Grouping the args by directory avoids it.

## Bugs / surprises observed (not fixed) — for REFACTOR_NOTES.md

1. **`WWW-Authenticate` dropped on MCP 401** (already known from Phase 0, §1.2): pinned by
   `test_mcp_401_current_behavior_drops_www_authenticate_header` (12 parametrized cases).
2. **Aliased settings read from three env names.** `AliasChoices("GREENHOUSE_X", "x")` fields (`mcp_token`,
   `ntfy_*`, `auth_secret_key`, `auth_admin_*`) are also read from the bare `X` and the prefixed `IRRIGATION_X`.
   For example, `MCP_TOKEN` or `IRRIGATION_AUTH_SECRET_KEY` in the environment configures the server.
   Precedence is `GREENHOUSE_X` > `X` > `IRRIGATION_X`. Pinned by
   `test_aliased_fields_current_behavior_read_three_env_names`. (`00-contracts.md` §6.1 lists only the
   `GREENHOUSE_*` names.)
3. **Migrated schema ≠ `create_all` schema.** Migrations add server defaults that the ORM does not declare:
   - `irrigation_windows.weekday_mask` = `'127'`
   - `user_preferences.scheduler_paused` = `0`
   - `user_preferences.notify_*` = `1`
   - `users.is_active` = `1`

   They also create named unique constraints `uq_irrigators_cluster_id` and `uq_users_username` (unnamed in the
   ORM). So `tmp_db`-based tests run on a slightly different schema than production. Pinned by
   `test_schema_drift_current_behavior_migrations_differ_from_create_all` and `schema_drift.json`.
4. **Logging quirk (test-infra).** `init_db` → Alembic `fileConfig` replaces the root handlers, which removes
   pytest's `caplog` handler. Any caplog assertion after a `create_app` sees nothing. Worked around with a
   handler on the module logger in `test_registry_logs_legacy_interval_warning_once_per_build`.

## Production lines / branches these tests guard (mutation targets)

- `s/app.py`:
  - `create_app`: the `FastAPI(...)` kwargs (title, version, description, `openapi_tags`,
    `generate_unique_id_function`), every `include_router` line and its order / `dependencies`, the
    `/static` mount, `include_router(web_router)`, the `FastApiMCP(...)` name / description / `auth_config`,
    `mount_http`;
  - `require_mcp_token`: both branches and the status / detail values;
  - `main()`: the order `load_dotenv` → `Settings` → `create_app` → `uvicorn.run(host, port)`.
- `s/config.py`: every field default, type and alias; `model_config`; the three validators (including the
  suggestion branches n≤0 / n≥24 / else and the `check_cron_hours_explicit` short-circuit); the property.
- `s/scheduler.py`:
  - `_JOB_DEFAULTS`, `init_scheduler` (configure + `remove_all_jobs` + 5 `_add_core_job` calls, their order,
    ids, names, triggers and intervals), `_add_tz_bound_cron_jobs` (hour / minute values);
  - `_resolve_check_cron_hours`: 3 branches and the warning texts;
  - `_resolve_zoneinfo`, `apply_persisted_pause`, `get_jobs`, `_is_paused`, `core_job_ids`, `CHECK_ALL_JOB_ID`,
    `_TZ_BOUND_CRON_JOBS`.
- All `s/routes/*.py` decorators (path, method, `response_model`, `status_code`, tags), route function names and
  docstrings, the Pydantic schemas in `core/schemas.py` (field order, aliases, descriptions), and `s/web/routes/*`
  plus `s/web/router.py` (paths, names, `include_in_schema=False`, the `require_web_user` dependency).
- `core/models.py`: every Column (type, nullable, default, index, unique, FK), every `Index` / `UniqueConstraint`;
  the migration chain under `core/migrations/versions/`; `core/database.py` (`init_db` empty / managed branches,
  `_alembic_config`, `head_revision`).
- `core/constants.py`: all values. `core/logic/decision.py`: the enums and `DEVICE_BLOCKING_CODES`.
  `core/devices/health.py`: `HealthAlarm`.
- Env reads:
  - `core/devices/gateway.py:116-122` (the defaults, the ValueError, the Cloud kwargs);
  - `core/plant_db.py:8-13`;
  - `core/utils.py:get_display_timezone`;
  - `core/migrations/env.py:30`;
  - `cli/commands/_helpers.py:14`, `cli/commands/auth.py:26`, `cli/commands/tui.py:35`;
  - `cli/client.py:_default_token_path` and `load_stored_token`.
- The re-export and alias imports that tests patch: `import tinytuya` in `core/devices/__init__.py` and
  `gateway.py`, `import time as _time` in `s/services/irrigation.py`, `from …timing import season_for` in
  `core/logic/engine.py`, and the `utils` imports in `core/learning/issues.py`.
- Package data: pyproject `[project.scripts]` and the hatch `packages` entries.

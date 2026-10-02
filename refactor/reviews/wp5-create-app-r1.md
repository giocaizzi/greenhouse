# WP5 T5.15 review — Reviewer 1 (behavior preservation)

Commit `19fde2e` (`libs/greenhouse-server/greenhouse_server/app.py`), worktree `/home/user/gh-wp5`, old = `19fde2e^`.
HEAD `59816e3` has no `libs/` changes after `19fde2e`, so the commit's tree is what runs.

**Verdict: APPROVE.** No blocker, major or minor findings. Two nits, neither needs action.

## Method

- `scratchpad/wp5_diff.py` loads the old `app.py` (as `greenhouse_server.app_old`, `__file__` pointed at the real file
  so `/static` resolves) next to the new one. For each, it wraps every side-effecting module global in a recorder
  (Settings, create_db_engine, init_db, create_session_factory, _init_tuya, _startup_timezone, set_display_timezone,
  WeatherClient, _init_ntfy_notifier, _init_plant_db, init_scheduler, init_health_monitor,
  _restore_persisted_scheduler_pause, bootstrap_admin, Path, StaticFiles, register_web_exception_handlers, AuthConfig,
  FastApiMCP, FastAPI), and also wraps `FastAPI.include_router` and `FastAPI.mount`. It builds both apps and compares
  the results.
- `scratchpad/wp5_diff2.py` covers the `create_app()` no-argument defaults path, lifespan behavior, import-time
  modules, and an `ast.dump` statement mapping.

## Checks

1. **Every statement exactly once, same order: PASS.**
   - AST: 51 old `create_app` body statements. 29 appear verbatim (by `ast.dump`) in the new create_app + helpers,
     with 0 duplicates. The 22 that are not verbatim are the expected ones:
     - the `FastAPI(...)` call: every keyword matches by `ast.dump` except `openapi_tags`. The tag literals are equal
       by `literal_eval`.
     - the 21 `include_router` calls, now a loop.
   - New-only statements are just the helper calls, `return lifespan` / `return tz_name` / `return FastAPI(...)`, and
     the `for` loop.
   - Runtime call log is identical old vs new:
     - setup: init_db → FastAPI → create_session_factory → _init_tuya → _startup_timezone → set_display_timezone →
       WeatherClient → _init_ntfy_notifier → _init_plant_db → init_scheduler → init_health_monitor →
       _restore_persisted_scheduler_pause → bootstrap_admin
     - routers: include auth (no deps) → 21 protected routers in the old order, all sharing **one** dependency list
       object `[Depends(require_user)]` → well_known
     - web and MCP: Path → StaticFiles → mount /static → include web_router → register_web_exception_handlers →
       AuthConfig → FastApiMCP. MCP is last.
   - Identical old vs new:
     - `app.state` key insertion order, with `mcp` last
     - the routes list: type, path, methods, name, endpoint module/qualname and dependant deps
     - the `app.openapi()` JSON byte-for-byte (sorted)
     - exception-handler keys and user_middleware
     - the MCP tool names and order, and the MCP auth dependency (`require_mcp_token`)
2. **Defaults and init_db timing: PASS.** The literal `is None` checks are unchanged (lines 178-183). With no
   arguments, both versions run `Settings()` → `create_db_engine(sqlite://)`, using the same settings object, before
   init_db. `init_scheduler` and `init_health_monitor` get the same `settings` object and `tz_name`.
3. **Import-time effects: PASS.**
   - The only new imports (`Callable`, `AbstractAsyncContextManager`, `APIRouter`) are under `TYPE_CHECKING`.
   - Loading the old module after the new one pulls in no extra `sys.modules` entries.
   - New module-level work is two tuples: string dicts, plus `.router` attribute reads of modules that were already
     imported. No test rebinds `routes.<x>.router` (grep of tests/), so reading `.router` at import time instead of
     at create_app time cannot be observed.
4. **Deviation `[dict(tag) for tag in _OPENAPI_TAGS]`: not observable.**
   - Values are equal to the old literals.
   - Two apps in one process share no tag dict, and no app tag dict is a module tuple element. This matches the old
     code, where each call built fresh literal dicts.
   - After `app.openapi()`, neither the module tuple nor the app's tags are mutated. FastAPI's `get_openapi` builds
     `OpenAPI(**output)` and then calls `jsonable_encoder`, which makes copies. fastapi-mcp only reads `app.openapi()`.
   - The OpenAPI and MCP goldens are green.
   - Spec §3.7's `list(_OPENAPI_TAGS)` *would* have made all apps share the same dicts. The deviation is the more
     faithful choice.
5. **`require_mcp_token`: PASS, unchanged.** It is not in the diff (lines 99-128 are byte-identical). Live check,
   same results old and new:
   - token unset → 503 for no header, a wrong token and the "right" token
   - token set → 401 for a missing or wrong token, 200 for the right one
   - per-request read: mutating `app.state.settings.mcp_token` on a running app takes effect on the next request
     (200 → 401). `AuthConfig(dependencies=[Depends(require_mcp_token)])` is unchanged.
6. **Logger names: N/A.** `app.py` has no logger, before or after.
7. **Helper DoD: PASS.**

   | Helper | Body lines | Notes |
   |---|---|---|
   | `create_app` | 19 | |
   | `_make_lifespan` | 10 | |
   | `_new_fastapi` | 11 | |
   | `_init_state` | 19 | |
   | `_init_background` | 3 | |
   | `_include_api_routers` | 11 | |
   | `_mount_web` | 4 | |
   | `_mount_mcp` | 12 | (code only) |

   - Every helper is fully annotated.
   - `ruff --isolated --select C90,PLR0911/12/15` with max-complexity 8 is clean.
   - Max nesting is 2 (`lifespan` def → `if`).
   - `mypy --strict app.py` reports no issues, and `ruff check` is clean.

Lifespan: it captures the same `settings` object. `start_scheduler`, `rearm_leak_checks` and `stop_scheduler` are
looked up as module globals at call time. Patching them after `create_app` is honored, and the sequence is identical
old vs new: start → rearm → serve → stop. With `enable_scheduler=False` it only serves.

## Findings

- **nit** `app.py:226`: the `generate_unique_id_function` lambda's `__qualname__` changes from
  `create_app.<locals>.<lambda>` to `_new_fastapi.<locals>.<lambda>`. The `lifespan` closure's qualname changes the
  same way, to `_make_lifespan.<locals>.lifespan`. Nothing keys on these: operation ids are equal, and no golden
  contains them (grep of tests/golden). Fix: none needed.
- **nit** `app.py:123` (pre-existing, out of scope, B-12 says do not touch): the token comparison
  `creds.credentials != settings.mcp_token` is not constant-time (`hmac.compare_digest`). Record it for a separate,
  behavior-changing commit. Not a regression.

## Test run (under lock)

`PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 <9 files>` → **269 passed**, 4 warnings, 56.9s, exit 0 (clean worktree).

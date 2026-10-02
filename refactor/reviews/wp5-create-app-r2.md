# WP5 review, Reviewer 2 (trying to break behaviour): `19fde2e` create_app decomposition

**Verdict: APPROVE.** Across all 23 app-building configurations, nothing a running server, a test or an MCP client can see differs between the old and new code. Five differences exist. Each is cosmetic, or shows up only through introspection, or needs code that monkeypatches private module state. Nothing in the repo does any of that.

## How it was tested

Harness: `scratchpad/wp5-diff/`. Files: `harness.py`, `conftest_shim.py`, `compare.py`, `compare2.py`, `mixed.py`, `rebind.py`, `lifespan_probe.py`, `imports.py`, `errpath.py`. Output is in `out/`.

- **Loading both versions.** The OLD (`19fde2e^`) and NEW (`19fde2e`) `app.py` are each run as `greenhouse_server.app_{old,new}`. Both have `__file__` set to the real path, so `/static` resolves the same way. They run against the worktree venv: FastAPI 0.141.1, APScheduler 3.11.2.
- **Configurations: 24.** The first 23 build an app; the last fails on purpose.
  - conftest `_make_stubbed_app`: bypass, real-auth and auth-disabled variants
  - raw base
  - MCP token set, and set to empty
  - scheduler on
  - scheduler on with custom cron and sync interval
  - legacy interval
  - legacy interval plus explicit cron
  - ntfy fully configured, and partly configured
  - `plant_db_path`
  - custom weather settings with debug on
  - temp-file SQLite with engine=None
  - temp-file database seeded with tz Europe/Rome and `scheduler_paused`, with the scheduler on
  - seeded invalid tz `Mars/Phobos`
  - seeded paused state with the scheduler off
  - Tuya credentials with a faked `tinytuya.Cloud`, which exercises the registry and the health monitor
  - auth with no admin credentials
  - auth with no secret
  - "everything" on together
  - `settings=None` read from env
  - an invalid db path, which raises the same `OperationalError` in both
- **What each fingerprint covers** (about 8.4k leaves per configuration):
  - `app.openapi()`, byte-exact JSON including tag order
  - every `_IncludedRouter` include context: prefix, tags, dependencies, include_in_schema, unique-id function, responses
  - every effective flattened route: path, methods, endpoint, operation_id/unique_id, tags, dependencies, the full dependant call tree, regex, response model
  - Depends identity counts
  - `vars(app)` and `vars(app.router)`, middleware stack and exception handlers
  - `app.state`: key order, types, values; for MCP, the tools, operation_map and auth_config
  - the scheduler: state, timezone, defaults, jobs with trigger, func, next_run state and paused flag; core ids; `_app`; shutdown event
  - log records emitted during `create_app`, plus every logger's level and handlers afterwards
  - the display timezone
  - a full dump of the DB after creation and after the HTTP probes
  - HTTP probes run inside the TestClient lifespan: `/`, `/login` (GET and POST form), `/api/v1/health`, `/api/v1/health/system`, `/api/v1/clusters`, `/auth/me`, `/scheduler/jobs`, both `.well-known` paths, `/static/*`, `/docs`, `/redoc`, `/openapi.json`, a 404 path, `/mcp` GET and POST (no token, wrong token, right token → initialize and `tools/list`), then the login → bearer and cookie flows
  - scheduler state during and after the lifespan
- **Process setups.** Each configuration ran in a fresh process for each version. Then all 24 ran in one process (`seq`, which reproduces how state accumulates across a test suite). Old and new ran at the same time so the wall clock matched.
- **Two-app checks** (`mixed.py`): sharing of tag lists and dicts, mutation through `app.openapi_tags` and through `app.openapi()["tags"]`, then a third app built fresh, sharing of route, Depends and MCP objects, the sequence of `include_router` calls and their dependency lists, and the order in which 20 collaborators are called (create_db_engine → init_db → FastAPI → … → FastApiMCP). **0 differences.**
- **Import side effects** (fresh subprocess for each version): the same 1378 modules are added. Loggers, root handlers, warnings and threads are identical. The only change is the new private names in the module namespace.
- **Sanity check:** contract tests for openapi, mcp and auth, `test_mcp`, the wp5 gaps tests and the packaging tests all pass (83 passed). The worktree was left clean.

## Result

- **Raw differences:** 575 across the 23 single-run configurations (25 each). The 23-configuration in-process sequence has the same 575.
- **After normalising one qualname (D1):** 0 in the single runs and 0 in the sequence.
- **Normalised away as noise:** argon2 salts, epoch timestamps, rendered "Page rendered HH:MM" and JWTs.
- **One time-dependent false alarm.** When run at different wall-clock times, the running scheduler listed its jobs in a different order. APScheduler sorts jobs by next_run_time: `check_all` fires at the top of the hour and the health monitor at now+5 min. The difference disappeared when old and new ran at the same moment.

## Differences found (none reachable in production)

**D1. The qualname of the unique-id lambda changes.** It was `create_app.<locals>.<lambda>` and is now `_new_fastapi.<locals>.<lambda>`. This accounts for all 575 raw differences: one on `app.router` and 24 include contexts per configuration. The value it produces, `route.name`, is identical, and so are the OpenAPI operationIds and MCP tool names.

**D2. The lifespan qualname changes.** It was `create_app.<locals>.lifespan` and is now `_make_lifespan.<locals>.lifespan`. It sits at the same depth in the merge chain (24) and still captures the same `Settings` object. Startup and shutdown effects are identical: scheduler running or stopped, rearm called, shutdown event set.

**D3. Routers are now captured at import time, not looked up at call time.** `_PROTECTED_API_ROUTERS` holds the router objects themselves, read once at import. Repro: `python rebind.py`. If `greenhouse_server.routes.clusters.router` is rebound, or the `clusters` global in the app module is monkeypatched, before `create_app()` runs, OLD picks up the replacement and NEW does not. `importlib.reload(<route module>)` triggers it the same way.
- No code path in `libs/` or `tests/` rebinds a router, reloads a module or patches those globals (checked with grep).
- Mutating a router in place, such as adding routes, still shows up in both versions, because FastAPI 0.141 keeps a live `original_router` reference.
- Not reachable. The commit message's claim of "only `.router` attribute reads" at import time is accurate, but it doesn't mention this consequence.

**D4. `typing.get_type_hints(greenhouse_server.app)` now raises `NameError: APIRouter`.** The cause is the string annotation guarded by TYPE_CHECKING. In OLD the module had no annotations, so the call returned `[]`. Repro: `python errpath.py`. Nothing in the repo calls it, and mypy passes. Only introspection tools would notice.

**D5. Tracebacks gain one helper frame.** For example, `create_app → _init_background → init_scheduler` when the scheduler is already running. The exception type and message are the same, and `app.state` is populated to the same point before the raise.

**Tag dict sharing, as asked.** Neither version shares tag lists or dicts between apps. Mutating one app's `openapi_tags`, or the `tags` of its generated schema, never reaches a second app or one built later. `_OPENAPI_TAGS` stays clean, because `dict(tag)` copies and the values are only strings. If the commit had used `list(_OPENAPI_TAGS)`, as the original spec text said, this would have become a real cross-app leak. Under `get_openapi`, `schema["tags"]` is a copy of `app.openapi_tags`, but its items are the same dict objects. The deviation the commit chose prevents that. A remaining hazard: code that mutates `_OPENAPI_TAGS[i]` directly would affect every later app. That needs a private-name write, and nothing in the repo does it.

## Nits (not blocking)

- D3: a one-line comment on `_PROTECTED_API_ROUTERS` saying the routers are captured at import time would help.
- D4: `"tuple[APIRouter, ...]"` could take its type from `fastapi.routing.APIRouter` without a TYPE_CHECKING guard, which would keep `get_type_hints` working.

## Re-check: old `19fde2e^` vs new `f1babce` (D3 and D4 follow-ups)

**Verdict: APPROVE.** The full harness was re-run with `wp5-diff/app_new.py` set to the `f1babce` source, which is byte-identical to the worktree file. The earlier new version and its outputs are kept as `app_new_19fde2e.py` and `out_19fde2e/`.

- **23-configuration comparison, fresh processes and one process:** 575 raw differences, all D1 (the unique-id lambda's qualname). After normalising it: **0** in fresh processes and **0** in the one-process sequence. OpenAPI is byte-identical in all 23.
- **Two-app checks (`mixed.py`):** **0** differences. That covers tag isolation, object sharing, `include_router` call sequence and dependency lists, and collaborator call order.
- **D3 is fixed.** `rebind.py` now shows old = new = True for both probes: a rebound `routes.clusters.router`, and a monkeypatched `clusters` global in the app module. Routers are read when `_include_api_routers` runs, at the same point in `create_app` as the old inline includes. One difference remains, with no practical effect: all 21 routers are read before the first include, where old read each one just before its own include. It would only matter if an `include_router` call rebound some module's router, which doesn't happen.
- **D4 is fixed.** `typing.get_type_hints(greenhouse_server.app)` no longer raises; it returns `{'_OPENAPI_TAGS': tuple[dict[str, str], ...]}`. Old returned `{}`. That difference is harmless.
- **Import side effects:** the same 1378 modules are added, with the same logging and warnings. The only change is new private names (`_protected_api_routers` replaces `_PROTECTED_API_ROUTERS`).
- **Still present and cosmetic:** D1 and D2 (qualnames) and D5 (one extra traceback frame, same exception).
- **Sanity check:** the app-level test subset still passes (83 passed). The worktree is clean.

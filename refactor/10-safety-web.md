# 10 — Phase 1 safety net: web UI (work package "web", gap items G3 + G4)

Status: **done, all green, not committed.** New files only:

- `tests/server/test_contract_web_html.py` — seed, app factory, route inventory, GET sweep, state variants, G4 spy (777 lines, ~half is the case table)
- `tests/server/test_contract_web_mutations.py` — every POST/DELETE route + bug pins (749 lines, mostly the case table)
- `tests/server/test_contract_web_errors.py` — error pages, auth wall, JSON-401 bug pin (139 lines)
- `tests/golden/web/` — 265 text goldens (2.3 MB): `routes.json`, `template_context.json`, `get/*.html` (118),
  `errors/*.html` (20), `mutations/*.json` (125)

283 tests: 1 inventory + 109 GET sweep + 9 state variants + 1 G4 + 3 meta (uniqueness/coverage) + 20 error/auth goldens + 125 mutation goldens + 15 named bug/invariant tests.

## Seeded state (one, deterministic)

`seed_greenhouse(repo)` runs inside `frozen_clock` (2026-04-15T10:00Z, a Wednesday) + `clean_env` (`TZ=UTC`), with
`install_offline_weather`. Every timestamp comes from `T = FROZEN_TS`. It seeds:
3 clusters (1 indoor "Indoor Jungle", 2 outdoor "Balcony Orchard", 3 empty, for the empty-state branches); plants
Monstera (≈50 %), Alocasia (8 %, critically dry), Eriobotrya (outdoor); 4 sensors (3 assigned, 1 unassigned ambient
`temp_humidity`), 25 readings each every 2 h over 48 h, newest 30 min old, **one Hampel spike** (Monstera 97 % at
T−10.5 h), battery low/middle/high; 2 irrigators (`rainpoint.ik10pw` with fake ip/local_key + reservoir/flow,
`tuya_cloud`); 6 irrigation events (start/off/manual/`schedule_updated`); 4 decision logs with `reasons` payloads;
3 alerts (open critical, acknowledged, resolved); 4 activity events; 3 plant-health days; a Mon/Wed/Fri 06–09 window on
the outdoor cluster; an upcoming + a past vacation; per-cluster configs + global caps; preferences (dark theme,
refresh 60 s, default cluster, `notify_auto` off). The migration's 00–05 quiet hours stay as seeded (10:00 is outside).

## What is pinned (test → contract)

| Test | Contract / invariant |
|---|---|
| `test_web_route_inventory` → `routes.json` | 85 web routes in **registration order**: methods, path, route name, endpoint `module.qualname`, `include_in_schema` (all `False`), route dependencies (`require_web_user` on 82, none on login/logout). |
| `test_get_route_html_golden[109 cases]` → `get/<case>.html` | Every GET page and HX fragment (all 52 GET routes; `test_get_sweep_covers_every_web_get_route` enforces it), plus query variants (hours/days/limit/metric/filters), empty cluster, 404 / 400 / 422 branches, the 301 legacy redirects and the 303 "already has an irrigator". Golden = request line, status, `content-type`/`location`/`content-disposition`/`set-cookie`/`www-authenticate` + every `HX-*`, full body. Includes `GET /dashboard/hero` (previously unhit). |
| HX variants (`*__hx`) | `is_hx` is only branched on by the error handler: `_error.html` partial vs `error_page.html` for 404 and 422; `dashboard__hx` proves pages do not branch. |
| `test_get_variant_html_golden[9]` | Base-layout banners (global dry-run, scheduler paused, active vacation, light theme), `quiet_active_now=True` on cluster detail, empty alert badge, activity pagination cursor (60 events > `_PAGE_SIZE` 50), `/scheduler` + `/health/badge` with the process scheduler running (started paused). |
| `test_template_context_golden` → `template_context.json` | **G4**: `{case: [[template, sorted context keys]]}` for the whole GET sweep, via a spy on `templates.TemplateResponse` (also catches the error handler's renders). Mutation goldens carry the same `templates` field for every write route. |
| `test_web_mutation_golden[125 cases]` → `mutations/<case>.json` | Every POST/DELETE route (no PUT routes exist; `test_mutation_cases_are_unique_and_cover_every_web_write_route` enforces coverage): happy path + each validation/404/409/503 branch. Pins status, headers (`location`, `set-cookie`, `HX-Toast`…), body lines, templates, **DB effect** (per table: added rows, removed keys, changed `col: [before, after]`), recorded **fake adapter `calls`**, and scheduler job ids after scheduler routes. Each case runs on a **fresh app**. Includes `POST …/plants/{id}/move` and `POST /logout` (previously unhit), real login (deterministic JWT cookie under the frozen clock), offsite `next` sanitisation, auth-disabled login, quiet-hours irrigate with/without `force`, caps 409, device failure, missing registry 503, running-scheduler delete (200 / core 409 / unknown 404 / not-running 503), pause with `check_all` unregistered (404). |
| `test_error_page_golden[9]`, `test_auth_wall_golden[9]`, `test_auth_disabled_mode_golden[2]` → `errors/*.html` | Unknown path / 405 (HTML vs JSON vs HX vs `/api`), route 404/422 with `Accept: application/json`, form 422 HX partial; anonymous browser → `303 /login?next=<quoted path?query>`, HTMX → `204` + `HX-Redirect`, anonymous POST, garbage session cookie, anonymous `/login`, anonymous `/api/v1` JSON 401; auth-disabled chrome (no Sign-out form). |

## Bugs observed, NOT fixed (pinned; orchestrator please add to REFACTOR_NOTES.md)

1. **JSON 401 drops `WWW-Authenticate`** (`web/exception_handlers.py:41-44` rebuilds the response without `exc.headers`; affects `AuthError` and `require_mcp_token`) — `test_json_401_current_behavior_drops_www_authenticate` (API missing token, API bad bearer, `/mcp` wrong token).
2. **Emergency stop reports success when the device says it failed** — `services/bulk.py::stop_all_irrigators` ignores `adapter.stop()`'s `(False, msg)`; still logs `stop/emergency` events and renders "Every device is now off." — `test_bulk_stop_all_current_behavior_reports_success_when_device_stop_fails`.
3. **Ack/resolve of a missing alert → 200 + success `HX-Toast`, empty body** (web route never checks the repo's `None`; the API returns 404) — `test_alert_action_on_missing_alert_current_behavior_returns_200_success_toast[ack|resolve]`.
4. **Bare `int()`/`float()` on form text → unhandled plain-text 500** (no HTML error page): `save_config` `duration_minutes`/`interval_hours`, `save_global_config` `duration_minutes`, `create_plant`/`update_plant` floats, `irrigate` `temp_override`, `plants/sync` `plant_id` (same pattern, untested: global `interval_hours`, `plants/sync` `cluster_id`) — `test_non_numeric_form_value_current_behavior_is_unhandled_500[7]`.
5. **`POST /clusters/999/irrigate` → 500**: the pipeline's `{"action": "error", …}` dict crashes `partials/_decision_panel.html` (`result.temperature` undefined, `is not none` true → `UndefinedError`) — `test_irrigate_missing_cluster_current_behavior_500_from_template`.
6. **Deleting a populated cluster leaves orphans** (irrigation windows, decision logs, alerts, sensor assignments keep pointing at it; SQLite FKs are off) — `test_delete_cluster_current_behavior_leaves_orphan_rows`.
7. **Doc/code mismatch**: `web/exception_handlers.py` docstring says HTML is chosen by `Accept: text/html` / `HX-Request`; the code chooses by path only, and router-level 404/405 (Starlette `HTTPException`) bypass the handler entirely → JSON `{"detail":"Not Found"}` even to browsers; a route-raised 404 is HTML even for `Accept: application/json` — `test_unknown_path_current_behavior_returns_json_404_to_browsers`.

Observation (not a bug, pinned by goldens): `context.base_context` `now_text` uses `time.strftime` → **process-local TZ**;
it is UTC-stable only because `clean_env` pins `TZ=UTC`. `plant_dashboard._relative_time`, `vacation._next_window` and
`clusters.quiet_active_now` read `time.time()` (frozen here).

## Determinism — how it was proven

- No normalization except the package version (`APP_VERSION` → `<VERSION>`; it appears in the footer/asset URLs).
  Timestamps, JWT cookies (HS256, fixed secret, frozen `iat`) and scheduler next-run times are stable under the frozen
  clock. CSV bodies keep `\r` visible as `<CR>` (goldens are read with universal newlines) — a lossless representation,
  not a normalization. No random ids / CSRF tokens exist in the web UI.
- Speed design (documented in the module docstring): building an app costs ~0.5 s (`FastApiMCP` renders the OpenAPI
  schema; migrations + argon2 add ~0.3 s). Apps are built exactly like `_make_stubbed_app` (same Settings, `FakeDeviceWiring`,
  overrides) but on a **byte copy (sqlite backup API) of a DB that a real `_make_stubbed_app` + seed produced inside the
  frozen clock**. GET cases share one per-process app reset before each case (DB restored, fresh fake wiring,
  `init_scheduler` + `set_display_timezone("UTC")` re-applied as `create_app` does). Equivalence was proven: the GET goldens
  were first generated with a fresh app per case and then passed unchanged on the shared, reset app.
- Runs (on the 3 files; the numbers are from the 281-test state, before the 2 auth-disabled cases were added — those were then run twice more, green):
  `uv run pytest <3 files>` twice → 281 passed / 281 passed (~2 min each);
  `-p xdist -n 2` → 281 passed (66 s); `TZ=America/New_York` → 281 passed; `TZ=Asia/Tokyo` → 281 passed;
  `sha256sum -c` over all goldens after the runs → unchanged. `git status tests/golden` shows only new files.
- Regression: my 3 files followed by every `tests/server/test_web_*.py` + `tests/server/test_auth.py` in one process → **566 passed**
  (283 new + 283 existing; also re-confirms all 283 new tests green in their final form). `uv run ruff check` / `ruff format` on the 3 files: clean.
- Cost: ~2 min serial / ~1 min with `-n 2`; ~100 s of it are the 125 fresh-app mutation cases (as the brief requires).

## Not pinned (and why)

- Non-UTC display timezone (`UserPreferences.timezone` ≠ UTC): the display zone is process-global (`utils._display_timezone`);
  flipping it in a test would leak into other tests. `format_ts` tz conversion is covered by `test_web_filters.py`.
- Static assets (`/static`, out of scope) and `HEAD` requests; client-side HTMX/JS behaviour.
- Template context of the error-module requests (G4 covers the GET sweep + every mutation instead).
- `bulk/stop-all` `UnknownDeviceModel` branch and `stop_all` with `registry=None` (not reachable through the stubbed
  registry without a custom fake model); `log_manual` 502/503 (`manual_log` raises only 409).
- Scheduler pages while jobs actually fire (scheduler is only ever started paused).

## Production lines/branches these tests guard (mutation-testing targets)

- `web/router.py` — include order, `protected` dependency on all but `web_auth.router`.
- `web/context.py:base_context` — every key + fallbacks (`theme or "auto"`, prefs/vacation exceptions, `auth_enabled`, `show_chrome`); `is_hx`.
- `web/exception_handlers.py` — `_is_html_request` (`/api/`, `/mcp`), `_error_template` HX switch, JSON branch (header drop), validation 422 detail text.
- `auth.py:require_web_user` / `_redirect_to_login` (query appended) / `render_login_redirect` (HX 204 vs 303, `quote`).
- `web/routes/auth.py` — `_safe_next` (scheme/netloc/leading `/`), auth-disabled shortcut, 401 re-render, cookie set/clear.
- `alerts.py` — badge `count == 0` → `""`, three `HX-Toast` payloads, list filters/limit 200.
- `analytics.py` — history 404, CSV header/rows/`irrigator is None`, scheduler page `running`/`core` flags, delete-job 503/409/404/200, pause/resume 303/404, stop-all fragment.
- `clusters.py` — create/update defaults (`location or None`), 404s, rationale from latest decision log (+ JSON error fallback), `_format_weekday_mask`, `quiet_active_now`, metric whitelist, overlay/heatmap 404, `hours`/`days` bounds.
- `configs.py` — `_parse_optional_hour`, `_parse_non_negative_int`, `_parse_tri_bool`, 301 legacy, redirect anchors.
- `irrigators.py` — new-form 303 when one exists, create 409 re-render, `_parse_capacity` (400s), config merge preserving `local_key`, `_action_result` (503 re-raise vs failed result), minutes parse, log-manual error re-render, 404s.
- `operations.py` — form parsing (`bool(dry_run)`, `force` truthy set), `check_single` 404, `/sync` hours 400, `/plants/sync` plant/cluster/all branches + 404.
- `plant_dashboard.py` — `_get_plant_or_404` cluster mismatch, `_relative_time` buckets, move 404/400/303.
- `plants.py`, `sensors.py` (`_parse_optional_plant_id`), `vacation.py` (`_parse_ts`, `_next_window`, 400s/404s), `windows.py` (`_parse_weekday_mask`, `_validate_window_form`), `preferences.py` (default-cluster fallback, theme 204/400, `saved`).
- `services/bulk.py:stop_all_irrigators`, `services/manual_control.py` (caps, 502/503 mapping, event notes), `repository.delete_cluster` cascade set.

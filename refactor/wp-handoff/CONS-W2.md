# CONS-W2 hand-off — consistency group W2 (routes / web / deps / app / auth)

Worktree `/home/user/gh-cons2`, branch `refactor/consistency-2`, based on `971aa93` (integration after drift + W1).
Not pushed, not merged, not rebased. Files touched: `greenhouse_server/{routes/**,web/**,deps.py,app.py,auth.py}`,
two web templates (D18), tests named below, `refactor/mypy-strict.txt`. No services/, CLI/TUI, config.py or WP8 file.

Every pytest run: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock … pytest -q -n 2`, directory-grouped chunks of
≤ 24 files (each lock hold ≤ ~3 min). To keep editing while the lock queue drained, most commits were tested on a
clean `git archive <commit>` export run with this worktree's venv (`scratchpad/w2/snap.sh`: pytest `pythonpath`
puts the export's `libs/` first — verified `greenhouse_server.__file__` resolves inside the export). Subset per
commit = direct module→tests map of every touched module ∪ `$CORE $API $WEB` ∪ the new gap file (+ extras listed).

## Commits (behavior-preserving first, doc-contract, then the two labeled fixes)

| # | Audit | Commit | Subset run (at commit) | Result |
|---|---|---|---|---|
| 0 | G.6 gap | `73de78e` test(server): pin route branches W2 restructures | new file | 6 passed |
| 1 | A7 / C-TX-1 (API) | `8aa250a` refactor(api): repo.commit() in API routes | D(10 route modules)+aliases | 45+640+284+131+92 = 1192 passed |
| 2 | A7 / C-TX-1 (web) | `47bd519` refactor(web): repo.commit() in web routes | D(11 web modules)+aliases | 45+640+110 = 795 passed |
| 3 | A6 / C-404 (API) | `7ada630` refactor(api): require_* for remaining inline 404s | covered by run 5 (tip `5b5e262`) | — |
| 4 | A6 / C-404 (web) | `621b610` refactor(web): require_* for remaining inline 404s | covered by run 5 | — |
| 5 | A8 / C-RES-5/6 | `5b5e262` refactor(api): model_validate instead of **dict + type: ignore | D(alerts, clusters, vacation, operations, plants, scheduler, configs, web clusters/vacation)+`$CHECK`+aliases | 45+699+303+152+92 = 1291 passed |
| 6 | A8 / C-RES-7 | `0ce56dc` refactor(api): Metric-typed chart metric via `deps.require_metric` | D(deps, charts, web clusters, plant_dashboard)+aliases | 45+639+287+160+92 = 1223 passed |
| 7 | C-CONST-7/8, filters | `b8b892f` refactor(api): replace literal — look-back bound, age thresholds, list limits, 503 | D(deps, charts, alerts, filters, web alerts/clusters/irrigators/plant_dashboard)+aliases | 45+639+287+184+92 = 1247 passed |
| 8 | size DoD | `41ee2fd` refactor(web): extract `_detail_data` from `cluster_detail` | covered by run 9 | — |
| 9 | size DoD | `61f162f` refactor(web): extract plant dashboard chart payloads | D(web clusters, plant_dashboard)+aliases | 45+632+66 = 743 passed |
| 10 | §1.10, C-STALE-2, C-NAME-8, C-TX-5 | `8e27655` refactor(web): docstrings — web handlers, filters, context, deps, stale prose | D(filters, context, exception_handlers, deps, auth, forecast, configs)+aliases | 45+639+287+184+92 = 1247 passed |
| 11 | C-ERR-1 | `46858f0` refactor(app): remove redundant except tuple + function-local re-imports | D(app, auth, exception_handlers, context)+aliases | 45+644+154+45+92 = 980 passed |
| 12 | C-REPO-5 | `312eeac` refactor(auth): bootstrap_admin uses `select()` | D(auth)+test_auth+aliases | 45+570+92 = 707 passed |
| 13 | A21 | `b81adba` refactor(types): deps, auth, web filters/handlers, web irrigators/vacation strict | D(those 6)+aliases | 45+639+287+184+92 = 1247 passed |
| 14 | doc-contract | `525ba14` docs(api): route docstrings | GOLDEN_UPDATE then openapi+mcp+test_mcp | 41 passed |
| 15 | **D18** | `ef2e49f` **fix(consistency)**: one age-filter contract | `$WEB` GOLDEN_UPDATE (288 passed), then snapshot (see end gate) | see end gate |
| 16 | **D19** | `37bc66f` **fix(consistency)**: stats without irrigator → zero totals | GOLDEN_UPDATE openapi/mcp, then snapshot | see end gate |

Every commit: `ruff check` + `ruff format --check` on touched files, `uv run lint-imports` (10 kept), `make typecheck`
green; `tests/golden` unchanged except in `525ba14`, `ef2e49f`, `37bc66f` (below). Commits 3/4 and 8 were not run on
their own: the next commit's snapshot contains them and its subset is a superset of theirs (red-test policy would
have bisected).

## Decisions a reviewer should check

- **"Signatures frozen" vs dropping `SessionDep` params (A7).** Handlers that took `session: SessionDep` only to
  commit now take nothing extra (or `repo: RepoDep` where they had no repository). Dependency parameters are not
  OpenAPI/MCP schema params — `openapi.json`/`mcp_tools.json`/`routes.json` stayed byte-identical through commits
  1–13 — and FastAPI's per-request dependency cache makes `repo.session` the same `Session` the services use. Route
  names, paths, query/body params, decorators and `response_model`s are untouched. Auth routes (`routes/auth.py`,
  `web/routes/auth.py`) keep `session.commit()` on the auth session from `_session_from_app` (core.auth API takes a
  `Session`, there is no repository there); `app.py`/`auth.bootstrap_admin` commit non-request sessions.
- **D18 contract.** The filter is now named after its input: `age_seconds` = an age in seconds (4 call sites,
  unchanged templates), new `time_ago` = a Unix timestamp (2 call sites switched). One formatter
  `web/filters.format_age`; `relative_age(ts)` delegates. Rejected alternative: keep `age_seconds` timestamp-based and
  pass timestamps everywhere — `SystemHealthDevice` only carries `age_seconds` (frozen schema), so health.html cannot.
- **D19 shape.** The API answers the documented `StatsResponse` with `period_days=days`, zero totals and empty
  breakdowns (truthful: nothing was irrigated) instead of a 404/500; the web stats page keeps its explanatory
  "No irrigators in cluster" panel for the same condition. Rejected: 404 "Cluster has no irrigator" (a cluster-scoped
  read on an existing cluster; would also need a CLI exit-code change).
- `deps.require_metric` imports `services.charts.Metric` into `deps` (deps already imports services; lint-imports OK).

## Golden diffs (each inspected)

- `525ba14` docs(api): `contracts/openapi.json`, `contracts/mcp_tools.json` — `"description"` strings only (route
  docstrings; 4 auth schema objects gain a `description` key → trailing commas after `"title"`).
- `ef2e49f` D18: 8 web goldens (`cluster_detail{,__72h,__chrome_flags,__outdoor,__quiet_hours_now}`,
  `cluster_status_fragment{,__outdoor}`, `health_page`) — 21 lines, all `stale` → `30m ago` in age cells.
- `37bc66f` D19: openapi/mcp — the `stats` description gains one sentence.
- Fingerprints: openapi `ee96dba2…` → `f752e84f…` (docs) → `21972789…` (D19); mcp `2a28d5d3…` → `0b5fbae5…` →
  `0949eb9e…`. The fingerprint comment now also names description-only `docs(api)` commits.

Tests edited: `tests/server/test_web_filters.py` (timestamp cases → `TestTimeAgo`, new age cases),
`tests/server/test_contract_mutation_gaps.py` (web-01 asserts both filters), `tests/server/test_contract_cons_w2_gaps.py`
(pinned 500 → D19 behavior), fingerprint constants + comment in `test_contract_{openapi,mcp}.py`. New file:
`tests/server/test_contract_cons_w2_gaps.py`.

## Strict list / size / lint

- `refactor/mypy-strict.txt` + `deps.py`, `auth.py`, `web/filters.py`, `web/exception_handlers.py`,
  `web/routes/irrigators.py`, `web/routes/vacation.py` (130 → 136 files). Every W2 file is now on the list.
- sizecheck: `web/routes/clusters.py::cluster_detail` 58 → 38 and `web/routes/plant_dashboard.py::plant_dashboard`
  42 → 40 — no W2 file is listed any more. Remaining entries are WP8/W3/CLI: engine ×4, sync ×2, `services/leak.py`
  ×2, and **`greenhouse_cli/tui/render.py` file lines = 401** (new since the drift merge, not a W2 file).
- `type: ignore` in routes/web: 12 → 1 (`web/context.py` union-attr on the paired session, documented).
- ruff D (google) on W2 files: 171 → 67, all D417, which flags framework params (`repo`, services) that
  CLAUDE.md invariant 7 says to skip — INT: per-file-ignore D417 for `routes/*` / `web/routes/*` when D is enabled.
- PLR2004 on W2 files: 5 → 0.

## Not done / handed on

- **D11 (after WP8):** the quiet-hours "active now" block in `cluster_detail` is left verbatim for the shared
  `logic.timing` helper.
- **B9 / C-404-9 (W3 first):** service-`None` 404s (`cluster_status`, `history`, `insights`, chart builders, web
  cluster fragments/history) and `"cluster not found"` string matching in `irrigate` stay until services raise one
  `NotFoundError`; same for "Target cluster not found" (move, own wording).
- **A9 (`web/forms.py`) and A20 (route return annotations + dropping the `web.routes.*` mypy override)** — not in this
  brief's scope list; untouched.
- **C-TX-4:** deduping `app._get_settings` into `auth._get_settings` needs `app.py` to stop importing `Request`, which
  is a pinned name in `imports.json` (`greenhouse_server.app`) → needs a rule-4 golden edit or stays. The session-sharing
  half (B2) is a behavior change (two reviewers) — not attempted.
- `app._startup_timezone` / `_restore_persisted_scheduler_pause` duplicate the open/read/commit/close shape; the audit
  keeps them (silent fallback). Their `except Exception` branches are still uncovered.
- `SessionDep` is now unused in `libs/` but is a pinned public alias (`imports.json`, like `DeviceGatewayDep` /
  `AuthUserDep` in the audit's not-dead list) → kept.
- Literal `90` (plant-health history days) in `routes/plants.py`, `web/routes/plant_dashboard.py` (+ `90 * 24`) and
  `services/charts.py` (`90 * 86400`): one core constant (e.g. `PLANT_HEALTH_HISTORY_DAYS`) belongs in `constants.py`
  (INT/W3), then adopt in all three.
- **Plugin / docs sync (INT):** D19 — `plugin/skills/greenhouse/SKILL.md` (stats on a sensor-only cluster: zero
  totals, was HTTP 500) and `references/CLI.md` if it describes `greenhouse stats` failures. D18 and the docs(api)
  rewording have no plugin impact (web-only / description text). `plugin.json`: no capability change.

## REFACTOR_NOTES-ready text

> **D19 (`fix(consistency)`, `37bc66f`) — fixes the stats 500 recorded after W1.** `GET /api/v1/clusters/{id}/stats`
> (CLI `greenhouse stats`, MCP `stats`) on a cluster without an irrigator now returns the documented shape with zero
> totals (`period_days` = requested days) instead of HTTP 500; the web page keeps its "No irrigators" panel.

> **D18 (`fix(consistency)`, `ef2e49f`).** Sensor-age cells on the cluster detail page, live-status panel, sensors
> table and system health page showed "stale" for every age (an age in seconds was fed to a timestamp filter); they now
> show the real age ("30m ago"; still "stale" past 7 days). Filter contract: `age_seconds` = seconds, `time_ago` =
> timestamp.

## End gate (HEAD `37bc66f`, seed 0)

See the section appended below (FULL in locked 14-file chunks over every test file — it contains the union of the
task subsets, `$CORE`, `$API`, `$WEB`, and the D18/D19 subsets).

### End gate results

FULL over all 139 test files (30 core, 3 chunks; 94 server, 7 chunks; 12 cli; 3 devices), clean export of `37bc66f`,
seed 0, `-n 2`, flock: 325 + 327 + 32 + 230 + 536 + 358 + 200 + 135 + 96 + 92 + 597 + 135 = **3063 passed, 0 failed**
(3053 at the base + 6 gap tests + 4 new `age_seconds` cases); no golden written. Labeled-fix subsets at their commits:
D18 `ef2e49f` 45 + 701 + 183 = 929 passed; D19 `37bc66f` 46 + 593 + 92 = 731 passed. `uv run ruff check libs tests`
OK, `ruff format --check` OK (321 files), `lint-imports` 10 kept, `make typecheck` 136 files OK, `make sizecheck`: the
9 entries listed above, none in a W2 file. Seed-12345 and `TZ=America/New_York` runs are left to the final gate.

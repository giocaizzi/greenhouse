# WP5 hand-off — route/web glue + app factory

Worktree `/home/user/gh-wp5`, branch `refactor/wp5-glue`, based on `c05e847` (integration after WP3). Not pushed,
not merged, not rebased. Every test command ran as `PYTHONHASHSEED=<seed> flock /tmp/greenhouse-tests.lock uv run
pytest -q -n 2 …` (the §0.3 `t` / `FULL` helpers; logs under the session scratchpad `wp5runs/`).

**Status:** 19 of the 21 mandatory tasks are done, plus one test-only characterization commit. T5.14a–h (optional
`require_cluster` folds) were **not done** (see "Not done"). No production dead code was found beyond the T5.2
removal.

## Commits

| Task | Commit | Evidence (task subset; all: ruff + format clean, lint-imports 10 kept, mypy OK, goldens untouched) |
|---|---|---|
| T5.0a | `cafc302` strict-clean services/cluster, app, web/context | 914 passed. One `type: ignore[union-attr]  # contract:` on base_context's `finally: session.close()`. |
| T5.0b | `28676c3` routes/{plants,operations,vacation,charts,windows,alerts,clusters,insights} | 698 passed. 5 comment-only ignores with `contract:` reasons (4 Pydantic-validates-ORM/dict, 1 removed again by T5.7). |
| T5.0c | `3724b55` web/routes/{clusters,configs,windows,operations,analytics,plants,plant_dashboard} | 600 passed (incl. guard tests). Helpers fully annotated; 1 ignore removed again by T5.8. |
| (test) | `8c6489d` `tests/server/test_contract_wp5_gaps.py` | 21 tests; see below. |
| T5.1 | `03c842a` web/weekdays.py adopted in web/routes/clusters | 307 passed. |
| T5.2 | `d9a3c24` weekday vocabulary in web/routes/configs | 288 passed. The configs copy was **dead** (zero references), so it is removed, not imported. |
| T5.3 | `9d10fff` weekday vocabulary in web/routes/windows | 306 passed. |
| T5.4 | `043a2b4` WINDOW_HOUR_MAX / FULL_WEEKDAY_MASK | 591 passed. Messages literal. |
| T5.5 | `8752e07` cluster_events_csv, adopted by stats_export | 689 passed (+ gaps file). |
| T5.6 | `1060452` cluster_events_csv in web analytics export | 295 passed. |
| T5.7 | `9dd167e` ClusterService.sync_plants + PlantNotFoundError | 668 passed (+ gaps file). |
| T5.8 | `1979d22` ClusterService.sync_plants in web plants sync | 310 passed (+ gaps file). |
| T5.9 | `6455b60` response mappers in cluster_status | 668 passed. |
| T5.16 | `130fe72` _sensor_status_rows / _irrigator_status | 967 passed. |
| T5.10 | `70061d3` _rationale_reasons, _window_rows, _cluster_chart_payloads | 331 passed (+ gaps file). Still 58 lines → proposed exception. |
| T5.11 | `16de015` _plant_form_fields | 305 passed. |
| T5.12 | `e9bbcda` repo.get_plant | 539 passed (subset **+ tests/server/test_web_charts.py**, the only file covering `GET /api/v1/plants/{id}`). |
| T5.18 | `8afe9a5` plant_dashboard context-section helpers | 361 passed (+ gaps file). |
| T5.17 | `d31bc38` _preference_flags / _auth_enabled | 590 passed (+ gaps file). |
| T5.13 | `1231540` repo.get_vacation_window | 597 passed. |
| T5.15 | `19fde2e` decompose create_app (G+) | subset 733 passed (+ gaps file); **FULL seed 0: 2861 passed** (tree identical to the commit). Needs its two reviewers. |

## Characterization commit `8c6489d` (pre-authorized)

Coverage precondition was measured once for all tasks: a full-suite run with `--cov-context=test` at `3724b55`, then
per-task filtering of contexts to the task's subset (statements + branch arcs inside each target function, the `def`
line excluded). Gaps found: API/web `/plants/sync` (plant-id scan, unknown-cluster `continue`, per-plant `except`),
API stats export without irrigator, cluster_detail undecodable payload, plant_dashboard no-irrigator / relative-time
branches, base_context swallow paths, `create_app()` defaults, and `charts.get_plant` (covered only outside the T5.12
subset). The new file pins all of them (21 tests): **25/25** single-line in-place production mutations killed, each
restored with `git checkout --`; green serially, at `-n 2`, with `TZ=America/New_York` and under coverage. Re-check with
subset + gaps file: 0 missing lines / arcs for every WP5 target.

`…_current_behavior` tests (REFACTOR_NOTES, I4): `test_api_sync_unknown_cluster_current_behavior_reports_zero`,
`test_web_sync_unknown_cluster_current_behavior_reports_zero` (both B-16).

## WP gate

- Union of all task subsets + `$CORE` (+ gaps file, guard tests; 74 paths), seed 0: **1392 passed** (8m03s), no reruns.
- `FULL` seed 0: **2861 passed** (at the T5.15 tree = HEAD production code).
- `FULL_SEED2` (seed 12345): **2861 passed** (9m36s), no reruns.
- `uv run ruff check libs/ tests/` and `ruff format --check` clean; `--select ERA,PGH,SLF,PLE` clean on all touched
  files; C90/PLR0911/12/15 @ 8 (`--isolated`) clean on all 16 touched files; `make typecheck` 55 modules OK;
  `lint-imports` 10 kept; `git status --porcelain tests/golden` empty.
- Mutation (§0.5): no WP5 module is on the mutation list → no M-pre/M-post.

## DoD (measured: sizecheck body lines / ruff CC / nesting)

| Function | Before | After |
|---|---|---|
| `app.create_app` | 130 / ≤8 / 1 | 19 / 3 / 1 |
| `services/cluster.ClusterService.get_cluster_status` | 54 / ≤8 / 1 | 23 / 2 / 1 |
| `routes/operations.cluster_status` | 51 / ≤8 / 1 | 15 / 2 / 1 |
| `routes/operations.stats_export` | 35 / ≤8 / 2 | 7 / 1 / 0 |
| `routes/plants.sync_plants` | 31 / 11 / 4 | 7 / 2 / 1 |
| `web/routes/operations.sync_plants` | 36 / 11 / 4 | 13 / 2 / 1 |
| `web/routes/analytics.cluster_stats_export` | 27 / ≤8 / 2 | 7 / 1 / 0 |
| `web/routes/clusters.cluster_detail` | 87 / ≤8 / 2 | **58** / 2 / 1 (exception proposed) |
| `web/routes/plant_dashboard.plant_dashboard` | 68 / ≤8 / 3 | 40 / 1 / 0 |
| `web/context.base_context` | 46 / ≤8 / 2 | 20 / 1 / 0 |

New helpers (all ≤ 24 lines, CC ≤ 5, nesting ≤ 3): `services/cluster.cluster_events_csv` 24/3/2,
`ClusterService.sync_plants` 7/3/2, `._find_plant_in_clusters` 10/5/3, `._sync_cluster_plants` 13/5/3,
`._sensor_status_rows` 18/2/1, `._irrigator_status` 16/2/1; `routes/operations._status_sensor` 8/1/0,
`_status_irrigator` 9/1/0, `_status_decision` 9/1/0; `web/routes/clusters._cluster_chart_payloads` 7/1/0,
`_rationale_reasons` 10/3/2, `_window_rows` 11/1/0; `web/routes/plants._plant_form_fields` 11/1/0;
`web/routes/plant_dashboard._latest_readings` 5/2/1, `_recent_events` 6/2/1, `_plant_alerts` 2/1/0,
`_last_irrigated_ts` 7/4/3; `web/context._preference_flags` 21/3/2, `_auth_enabled` 6/2/1;
`web/weekdays.format_weekday_mask` 3/2/1; `app._make_lifespan` 11/4/0, `_new_fastapi` 11/1/0, `_init_state` 19/1/0,
`_init_background` 3/1/0, `_include_api_routers` 11/2/1, `_mount_web` 4/1/0, `_mount_mcp` 12/1/0. (Some commit
bodies quote slightly different hand counts; this table is the measured one.)

`make sizecheck` over the WP5 files: 8 hits before → 1 after (`cluster_detail`).

### Proposed size-exception entry (orchestrator approval needed)

`libs/greenhouse-server/greenhouse_server/web/routes/clusters.py::cluster_detail — 58 body lines: a 22-line
template-context return (16 frozen context keys) plus the 18-line quiet-hours block reserved for I2; every other
section is a helper; I2 re-measures (it can shrink the quiet block to ≈ 6 lines, still ≈ 46 > 40)`

### Function map (old qualname → new helper qualnames)

- `routes/operations.stats_export`, `web/routes/analytics.cluster_stats_export` → same + `services.cluster.cluster_events_csv`
- `routes/plants.sync_plants`, `web/routes/operations.sync_plants` → same + `services.cluster.ClusterService.sync_plants`,
  `._find_plant_in_clusters`, `._sync_cluster_plants`, `services.cluster.PlantNotFoundError`
- `routes/operations.cluster_status` → same + `routes.operations._status_sensor`, `_status_irrigator`, `_status_decision`
- `services.cluster.ClusterService.get_cluster_status` → same + `._sensor_status_rows`, `._irrigator_status`
- `web/routes/clusters.cluster_detail` → same + `_cluster_chart_payloads`, `_rationale_reasons`, `_window_rows`
- `web/routes/clusters._format_weekday_mask` (+ the configs copy) → `web.weekdays.format_weekday_mask`
- `web/routes/plants.create_plant`, `update_plant` → same + `_plant_form_fields`
- `web/routes/plant_dashboard.plant_dashboard` → same + `_latest_readings`, `_recent_events`, `_plant_alerts`, `_last_irrigated_ts`
- `web/context.base_context` → same + `_preference_flags`, `_auth_enabled`
- `app.create_app` → same + `_make_lifespan`, `_new_fastapi`, `_init_state`, `_init_background`, `_include_api_routers`,
  `_mount_web`, `_mount_mcp` (+ module constants `_OPENAPI_TAGS`, `_PROTECTED_API_ROUTERS`)

## Strict list / ratchet (for I1)

- `refactor/mypy-strict.txt` additions (17): `app.py`, `services/cluster.py`, `web/context.py`, `web/weekdays.py`,
  `routes/{charts,clusters,operations,plants,vacation,windows}.py`, `web/routes/{analytics,clusters,configs,operations,
  plant_dashboard,plants,windows}.py` (`routes/{alerts,insights}.py` were already listed).
- Per-file ignores now removable from `pyproject.toml` (integrator): `app.py` `PLR0915`; `routes/plants.py` `C901`;
  `web/routes/operations.py` `C901`. All three files are clean under C90/PLR @ 8 `--isolated`.

## Deviations

1. **Typing ignores.** T5.0a–c needed 7 narrow `type: ignore[<code>]` comments, each with a `contract:` reason; two were
   removed again by T5.7/T5.8 when the bodies moved. Remaining: base_context `session.close()` [union-attr];
   vacation list ×2, plant health history, learn alerts [arg-type] (Pydantic validates ORM rows / dicts).
2. **T5.2** removes the dead configs copy instead of importing (an import would be unused).
3. **T5.12** subset extended by `tests/server/test_web_charts.py` (coverage), not by new tests.
4. **T5.15** `openapi_tags=[dict(tag) for tag in _OPENAPI_TAGS]` instead of §3.7's `list(_OPENAPI_TAGS)`: each app keeps
   fresh tag dicts, as the inline literals gave.
5. **T5.7/T5.8** translate `PlantNotFoundError` with `raise HTTPException(...) from None` (ruff B904). Response is
   identical; the old code raised outside any `except`, so there was no context either way that a client could see.
6. **T5.0a** `_get_settings` binds `request.app.state.settings` to an annotated local before returning it (same object).

## Not done

- **T5.14a–h** (optional `deps.require_cluster` folds): skipped. They are low value, and the test lock was heavily
  contended (WP7 mutation runs; several of my runs waited 5–30 min). They can be picked up later as written.

## Dead-code scan (owner directive)

vulture (lead only) over `libs/` + `tests/`, filtered to the WP5 files: every hit was a route/web handler (multi-line
decorators), a `TYPE_CHECKING` import used in a string annotation, or a Pydantic field. One dead group was found and
removed (T5.2, the configs weekday copy, with grep evidence). Leads **outside** WP5 files, not verified or removed:
`auth.py:351 AuthUserDep`, `deps.py:139 DeviceGatewayDep` (vulture 60 %; unused `*Dep` aliases).

## For reviewers (look hardest here)

- **T5.15** (auth wiring, G+):
  - `_PROTECTED_API_ROUTERS` order must equal the old include sequence; the OpenAPI/MCP goldens are green.
  - `_mount_mcp` must stay last.
  - The lifespan closure must capture the same `settings`.
- **T5.7/T5.8:**
  - `self._repo` is the route's repo: the same cached `get_repository` dependency.
  - `plant.species` is still read inside the `except`, after a failed `flush()`.
  - `repo.session.commit()` stays in the routes, after the service returns.
- **T5.17:** every `prefs.*` read still happens before `session.close()`, and the partial-failure semantics hold (pinned).
- **T5.16:** `now = int(time.time())` is the first statement of `_sensor_status_rows`, called right after
  `get_irrigator_for_cluster`, which is the same clock point as before.
- No ORM attribute read, `session.*` call or import moved across a commit / rollback / flush boundary or into or out of
  an `except` in any commit.

## REFACTOR_NOTES requests (I4)

Record the two B-16 `…_current_behavior` tests and deviations 1–6. No new bug was observed.

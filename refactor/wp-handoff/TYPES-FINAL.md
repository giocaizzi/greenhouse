# TYPES-FINAL hand-off — enum-typed parameters, server TypedDicts, last magic numbers, lazy ignores

Worktree `/home/user/gh-pw-b`, branch `refactor/types-final`, base `f63b2f3`. Not pushed, not merged, not rebased.
Sources: `47-fix-pass.md` (FP-C deferrals), `wp-handoff/FP-C.md` "Deferred — server sites", `40-review-typing.md`
§1.2–1.4, `40-review-clean-code.md` C-3. No behavior change, so no `fix(...)` commit and no `REFACTOR_NOTES.md` entry.

Not touched (owned elsewhere): `greenhouse_core/sync.py`, `devices/**`, `web/routes/clusters.py` (cluster_detail
quiet hours), device-type registry/aliases, web irrigator form, CLI `irrigator --type`.

## Commits (branch order)

| Commit | Task | What | Evidence (subset → result) |
|---|---|---|---|
| `09aabbe` | 1 | server: `TriggeredBy` (irrigation shutdown/watcher helpers, `schedule_pump_watcher`, `notify_irrigation`), `ActivitySource` (alerts dedup/raise/auto-resolve, pump trip activity), `EntityType` (health monitor); two `"emergency"` literals → `TRIGGERED_BY_EMERGENCY` | alerts/anomaly/bulk/health/leak/notify/pump/shutdown/irrigators/wp7 278 passed |
| `5d96284` | 1 | core: `add_irrigation_event(action: EventAction, triggered_by: TriggeredBy)`, `add_decision_log(triggered_by)`, `add_activity_event` / `upsert_alert(source: ActivitySource, entity_type: EntityType)`, engine `decide_for_cluster/_finish/_record/_persist(triggered_by)`; defaults `"auto"`/`"cluster"` → equal members | decision grid, repository, db, engine, imports contract; server pipeline/check_all/alerts 134 passed |
| `f2ff08c` | 2 | `CurrentWeather` / `WeatherForecast` (weather results + caches); temperature pickers bind narrowed value with a walrus | weather contract, pipeline, mutation/wp7 gaps, forecast 255 passed |
| `6a6596f` | 2 | `AlertFinding` (learning/maintenance findings) through alerts sync, `CheckResult`, insights, plant page | 121 + 78 passed |
| `943fc9d` | 2 | `ClusterSnapshot` (services-layer sync snapshot: `ensure_fresh_and_read` / `_cluster_snapshot`) through the pipeline | sync service contract, pipeline, sync 171 passed |
| `5389ee0` | 2 | `SensorStatusRow`, `IrrigatorStatus`, `SensorHistory`, `IrrigatorHistory` (cluster status/history); API status mappers typed | 90 passed |
| `c37802c` | 2 | `MonitorSensorRow` (monitor_cluster rows), `ClusterCaps` (web `cluster_caps`) | 353 passed |
| `daa4caa` | 2 | `ChartPayload` / `ChartDataset` / `ChartEvent` / `ChartThreshold` in new `services/chart_payload.py` (keeps `charts.py` ≤ 400 lines); chart-data routes annotate it (response_model unchanged) | charts, web charts, openapi/routes/mcp contracts 62 passed |
| `f2cf83e` | 2 | `PlantFormFields` for the web plant form mapping (mypy checks keys vs `add_plant` / `Unpack[PlantPatch]`) | 291 passed |
| `603e143` | 3 | grouped constants block (14 names, values verbatim) adopted in services sync/manual_control/anomaly/forecast/cluster/health/alerts/leak/efficacy + web plant_dashboard; forecast private aliases removed; `services.sync.SNAPSHOT_LOOKBACK_HOURS` is the core constant (still importable); plugin `LOGIC.md` bullet; `mutate.py` svcsync-02 follows | constants+imports contracts 33; services 281 passed; `mutate.py --check` INVALID set = base |
| `5256862` | 4 | TUI T8/T9: `cast("GreenhouseApp", self.app)` ×2, field widget cast in `FormScreen.action_submit` (3 ignores gone) | TUI 131 passed |
| `6d0fddc` | 4 | `_run_pump_watcher(app: "FastAPI")` (TYPE_CHECKING) + import-linter ignore beside `jobs.py`'s | 140 passed |

## Gates (final, branch head)

- `uv run ruff check libs/ tests/ refactor/` → 0 · `ruff format --check` → 0 (342 files)
- `make typecheck` → 0 (171 files; `services/chart_payload.py` appended to `refactor/mypy-strict.txt`)
- `uv run lint-imports` → 0 (20 kept)
- `sizecheck.py` → exit 1 at head **and at base `f63b2f3`**, same two hits only: core `sync.py::sync_sensor_data` /
  `sync_single_sensor` (not in `size-exceptions.txt`; file owned by another package). No new hit.
- `git diff --stat f63b2f3 -- tests/golden` → empty (OpenAPI / MCP / routes / constants / imports byte-identical).
- FULL: see last line.

## Deliberately kept `str` / not converted (with reason)

- **Pydantic fields** (`schemas.py` `action` / `triggered_by` / `source` / `entity_type`) and **route Query filters**
  (`routes/activity.py`, `web/routes/activity.py`, `repository.list_activity_events`): an enum would add an `enum`
  constraint to OpenAPI and 422 unknown filter values (behavior). DB-read values (`stats.IrrigationRecord.triggered_by`,
  ORM `Mapped[str]`) are plain `str`.
- `add_decision_log(action: str)` carries `Action.value` (decision vocabulary, not one of the four enums).
- `services/irrigation.py` `source: str` in `_decision_result` / `_Actuation` is the *temperature* source, not
  `ActivitySource`.
- `decision_to_view` stays `dict[str, Any]` (keys mirror `IrrigationDecision.model_dump`).
- Free-form JSON payloads (`pump_watcher._trip_payload`, leak `evidence`, activity/alert `payload`): core takes
  `payload: dict[str, Any]`, which a TypedDict is not assignable to; leak evidence is the S-22 redesign (CQS), not typing.
- Accepted casts (typing review "justified/acceptable"): `CursorResult` ×2, `deps` Metric, `_check_result` computed key,
  CLI JSON boundary ×2, comprehension narrowing (`profiling` ×3, `trends`, `tui/model`), engine `Environment`.
  Justified ignores kept: `services/jobs.py:46`, `scheduler.py:294` (a None app must escape as `AttributeError`).

## For the integrator / other packages

- **Core `sync.py` (owner of that file):** `sync_sensor_data` returns bare `dict`, so `SyncService.sync_all_sensors`
  stays `dict[str, Any]`. Proposed: `class SyncStats(TypedDict): total_synced: int; total_new: int; total_live: int;
  errors: list[str]` in `greenhouse_core/sync.py`, then `sync_all_sensors -> SyncStats` (its no-gateway literal has the
  same four keys) and the two route consumers follow.
- **`web/routes/clusters.py` (post-wp8 owns the quiet-hours code there):** `_detail_data` / `_rationale_reasons` /
  `_window_rows` and `effective_config: dict[str, dict[str, Any]]` could take core `EffectiveConfig`; left untouched.
- **`pump_watcher._trip_payload` `"alarm_dp": 105`:** a device DP id; belongs next to the IK10PW profile in `devices/**`
  (off-limits here), not in `constants.py`.
- **pyproject:** one new `ignore_imports` line in "Services are HTTP-free" (`services.irrigation -> fastapi`,
  TYPE_CHECKING only). No per-file ignore added/removed; no `size-exceptions.txt` change.
- **Merge watch:** `constants.py` (block appended at the end), `services/irrigation.py`, `services/sync.py`,
  `refactor/gate1/mutate.py` (svcsync-02), `refactor/mypy-strict.txt` (one line inserted before `services/charts.py`).
- **OD5 prerequisites** (`REFACTOR_NOTES.md` self-contained) were not in this package's task list — unchanged.

## Look hardest at

1. `5d96284`: three runtime defaults change from plain `str` to the equal StrEnum member (`add_decision_log`,
   `upsert_alert`, `decide_for_cluster`). Verified: `hash(member) == hash(value)`, dict lookups both ways, Pydantic `str`
   field stores `str` and dumps `"auto"`, `json.dumps` → `"auto"`; FP-C already made every constant a member (FULL green).
2. `f2ff08c` / `943fc9d`: `x.get(k) is not None` then `x[k]` became a walrus on `.get(k)` — same object for a dict; every
   test fake returns plain dicts (grep of `get_current` / `ensure_fresh_and_read` fakes).
3. `daa4caa`: route return annotations changed on two routes with explicit `response_model` — FastAPI ignores the return
   annotation then; OpenAPI/MCP/routes goldens unchanged.

Full suite at `6d0fddc`: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2` → **3075 passed**, 0 failed, exit 0 (8m47s; log `…/scratchpad/full-types-final.log`). No test added or removed.

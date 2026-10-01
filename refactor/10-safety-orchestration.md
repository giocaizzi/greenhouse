# 10 — Phase 1 safety net: orchestration (high risk)

Work package: `services/irrigation.py`, `services/manual_control.py`, `services/leak.py`, `services/alerts.py`,
`services/notify.py`, `services/cluster.py`, `services/vacation.py`, `scheduler.py`, `routes/operations.py`,
`routes/scheduler.py`, `routes/decisions.py`. Gap items I6, I10, I11, I-ii, I-iii (+ scheduler coverage).
No existing test, fixture, conftest, fake or production file was touched; nothing committed.

## Files created

| File | Tests | Golden(s) |
|---|---:|---|
| `tests/server/test_contract_pipeline.py` | 84 | `tests/golden/orchestration/pipeline/<scenario>.json` (57 files) |
| `tests/server/test_contract_check_all.py` | 6 | `orchestration/check_all_{crash_after_writes,alert_lifecycle,scheduler_job}.json` |
| `tests/server/test_contract_scheduler.py` | 32 | `orchestration/scheduler_{registry,registry_running,registry_after_tz_change,delete,health_snapshot,anomaly,leak_check}.json` |
| `tests/server/test_contract_raw_vs_clean.py` | 3 | `orchestration/raw_vs_clean.json` |

**Deviation from the brief:** the pipeline golden is one file *per scenario* under
`tests/golden/orchestration/pipeline/` instead of a single `pipeline.json`. One combined file made the matrix a
single 75 s serial test (57 apps, each running the Alembic upgrade) that xdist could not split, and every diff landed
in one 180 KB file. Per-scenario files parallelise and keep diffs local; the content is the same.

Shared harness: `Pipeline` in `test_contract_pipeline.py` (imported by the other three files). It builds the app with
`_make_stubbed_app`, calls `install_offline_weather`, swaps `app.state.ntfy_notifier` for a `RecordingNotifier`, and can
install a `RecordingGateway` (fake `DeviceGateway`: empty logs plus a canned live reading) or a canned `RainyWeather`.
It seeds through the API, with readings and events written through the repo at explicit `FROZEN_TS - age` times.
`mark()` records the high-water marks; `observed()` returns the calls, the new rows of the append-only tables
(`irrigation_events`, `decision_logs` with `payload_json` parsed, `activity_events`, `sensor_readings`), the whole
`alerts` table, the fake adapter `calls`, the gateway calls and the notifications.

## What is pinned (test → contract / invariant)

- **Pipeline matrix** (`test_pipeline_golden[...]`, 57 scenarios). Responses, DB writes, adapter and gateway calls,
  and notifications for:
  - soil state: dry, very-dry (critical stress), wet, adequate, no readings (temperature fallback);
  - temperature source: indoor/outdoor with weather offline, outdoor rain forecast (`weather_skip`), indoor `no_sync`
    with weather available, `temp_override`;
  - cooldown: active, 6h ± 60s boundary, a manual start counts, `schedule_updated`/`off` are ignored;
  - quiet hours: global, `force` override, cluster opt-out;
  - leak hold: open, acked, resolved, `force`, expired (25h), beats cooldown, escape hatch;
  - vacation: no capacity, rationing (trim to 1 min), budget exhausted;
  - windows: outside, inside, outside but critical stress overrides;
  - caps: automatic pipeline, manual start/log 409, daily-minutes cap;
  - manual start/stop/log, device failure 502, no registry 503, unknown irrigator 404;
  - dry run (decision says irrigate / skip), `dry_run_global`, `notify_auto` off;
  - freshness: fresh reading → zero gateway calls; stale (>4h) → exactly one `sync_single_sensor` (gateway call
    sequence plus the new live reading); stale with `no_sync` → no call;
  - error paths: no plants, no irrigator, no registry, unknown model (`UnknownDeviceModel`), actuation failure
    (`attempted` event, alert, push);
  - device-health block;
  - 404s;
  - `/clusters/{id}/check`: dry, `auto_run` off, sensor-only;
  - `/check`: mixed 5 clusters, two runs (cooldown), empty;
  - `GET /clusters/{id}/decisions` (newest first, `limit`, 404).
- **I6:** `test_each_evaluation_writes_exactly_one_decision_log` (14 cases). It covers `no_plants`, `leak_hold`
  (also forced), `cooldown`, `quiet_hours`, `weather_skip`, `outside_window`, vacation exhausted, wet skip, irrigate,
  dry run, actuation failed, no irrigator, and the `/check` path. Each case writes exactly one row with
  `evaluated_at == FROZEN_TS`, the expected action and `actuated` flag, a `primary_code` equal to the payload's, and
  a `payload_json` that round-trips through `IrrigationDecision.model_validate`.
  `test_decision_logs_only_come_from_engine_evaluations` shows that 404s, manual actions and `auto_run` off write no
  decision log.
- **I11:** `test_leak_hold_blocks_forced_irrigate_but_not_direct_start`, plus the golden
  `leak_escape_hatch_manual_start`.
- **Follow-up jobs after a start:** `test_successful_auto_start_schedules_leak_check_and_watcher` — an automatic start
  schedules `leak-check-1-<ts>` at +30 min and `pump-watcher-1-<ts>`.
  `test_manual_start_schedules_watcher_but_no_leak_check` — a manual start schedules a watcher only when `minutes` is
  given, and never a leak check.
- **I-ii (`test_contract_check_all.py`):**
  - The failing cluster raises *after* actuating and writing. Its start event, decision log and activity are rolled
    back; the clusters before and after it keep their `IrrigationEvent(action="start")`. The response reports
    `action="error"` with the exception repr and a `check_failed` alert (severity `error`) is raised.
  - The exact transaction sequence is pinned via spies on `Session.commit`/`rollback`:
    `check:1, commit, check:2, rollback, commit, check:3, commit, commit`.
  - A crash in the first cluster does not block the later ones.
  - Alert lifecycle: raised as open → resolved at the next success (`resolved_at=FROZEN_TS`) → the same row
    re-opened with occurrence 2.
  - The scheduler `_check_job` body behaves the same, including the same transaction sequence.
  - In degraded mode `_check_job` writes decision logs only. A total failure is rolled back and logged
    ("Check job failed") and never raises.
- **I-iii (`test_contract_scheduler.py`):**
  - Registry golden: 5 core jobs in registration order, with triggers, `func_ref`, trigger tz and
    `core_job_ids`. On a **stopped** scheduler, pending jobs have *no* `max_instances`/`coalesce`/
    `misfire_grace_time` attributes (pinned as `<unset>`; APScheduler applies the defaults on `start()`). On a
    running scheduler, every core job and every ad-hoc job (leak check, pump watcher, custom) has
    `(1, True, None)`.
  - Running `next_run_time` values are pinned exactly.
  - `init_scheduler` is idempotent when called twice.
  - A second `create_app` rebinds `scheduler._app` (the lazy-import trap).
  - The timezone preference re-adds the two cron jobs in the new zone (they move to the end of the pending list)
    and a pause survives it.
  - The scheduler API surface is list / delete / pause / resume / health only: there is **no** endpoint to add or
    modify jobs.
  - `paused` is true only for `check_all` after an explicit pause and equals `preferences.scheduler_paused`, on both
    a stopped and a running scheduler; pause is idempotent.
  - Web `POST /scheduler/{pause,resume}` and the API both go through the spied `set_check_all_paused` while the
    scheduler is stopped (303 → `/scheduler` vs 200).
  - Pause with `check_all` missing → 404 and nothing persisted. `apply_persisted_pause` edge cases are pinned.
  - Delete: every core job → 409 (detail golden), ad-hoc → 200, unknown → 404. The web delete returns 503 while the
    scheduler is stopped, then 409/200/404.
  - Job bodies called directly with `_app` set:
    - `_sync_job` is a no-op without a gateway (no session opened). With the gateway it syncs every sensor (exact
      gateway calls, new live row) and commits; failure is logged.
    - `_health_snapshot_job` writes a `plant_health_daily` row with `date_key="2026-04-15"`; failure is logged.
    - `_anomaly_job` raises a `sensor_stale` alert and sends a push; failure is logged.
    - `_health_monitor_job` returns when `_app` is None or no monitor is wired. Otherwise it polls every irrigator
      and sensor and raises a NO_WATER alert that blocks actuation; failure is logged.
    - `init_health_monitor` returns without a registry and wires the monitor even when the startup hook fails.
    - `_get_cloud`.
    - `_run_leak_check` raises the hold once plus the `leak_check` marker and is idempotent. When `_app` is None or
      the check fails, no marker is written.
    - `rearm_leak_checks` returns 0 when stopped. When running it re-arms pending (+20 min) and overdue (now) auto
      starts, never manual or older-than-24h ones.
    - `schedule_pump_watcher` guards: duration ≤ 0, stopped scheduler, no registry, feature off.
- **I10 (`test_contract_raw_vs_clean.py`):** the latest row is a 5 % spike (Hampel) and a second sensor holds a 150 %
  value (range gate).
  - Raw side: the spike and the impossible value appear in cluster chart JSON and history, the spike in the
    assignment-aware plant chart, and the raw latest rows in `/status` sensors. The data-quality report counts the
    impossible reading as fresh (no `stale_sensor`).
  - Cleaned side: the `/status` decision, a dry-run `/irrigate` and the persisted `sensor_snapshot` use driest = 44 %
    (`sensor_dry`, 2 min, not very-dry).
  - The anomaly job (raw by design) flags exactly that spike as `sensor_drift`.
- `services/vacation.py` (web-only projection): `test_vacation_budget_projection`.

## Observed bugs / oddities (pinned, NOT fixed — for REFACTOR_NOTES.md)

| Ref | Pinning test | Note |
|---|---|---|
| B-1 | `test_dry_run_global_current_behavior_still_actuates` (+ golden `dry_run_global_preference`) | `/irrigate` and manual start both actuate with `dry_run_global=true`. |
| B-4 | `test_caps_current_behavior_not_checked_by_automatic_pipeline`, `test_global_caps_current_behavior_ignored_by_manual_start` (+ golden `caps_reached_automatic`) | Manual start gets 409, `/irrigate` actuates, `daily_cap_hit` never appears. Global caps are ignored by manual start. |
| B-5 | `test_reraised_alert_current_behavior_is_not_renotified` | Resolved `actuation_failed` re-opens with occurrence 2 and no push. |
| B-6 | `test_device_health_block_current_behavior_not_written_to_decision_log` (+ golden `device_health_block`) | The response is `skip`/`device_no_water`; the log row still says `irrigate`, no `device_no_water`. |
| B-8 | `test_vacation_create_current_behavior_accepts_reversed_window` | `POST /vacation` returns 201 for `ends_at < starts_at`; `PUT` returns 400. |
| new | `test_monitor_current_behavior_short_window_defeats_spike_filter` | `/monitor` cleans only a 2 h slice. At a 30-min cadence that is fewer than 5 samples, so Hampel is skipped and a single spike yields `very_dry` / `needs_water`. |
| new (golden only) | `pipeline/quiet_hours_force.json` | `force=true` writes `decision_logs.triggered_by="manual"`, but the `IrrigationEvent` says `triggered_by="auto"`, the push says "Automated irrigation", and a leak check is scheduled (manual starts never get one). |
| new (golden only) | `check_all_crash_after_writes.json` | When a cluster crashes after `adapter.start`, the pump really ran but its `start` event is rolled back, so the cooldown cannot see it on the next tick. |
| new (golden only) | `pipeline/vacation_budget_exhausted.json` | The decision becomes `skip` but `primary_code` stays `sensor_dry` (the first reason). |
| note | `test_vacation_budget_projection` | `round(5.7/4, 2) == 1.42` (binary float) — display only. |

## Determinism (commands + results)

- Goldens were created once with `GOLDEN_UPDATE=1`. Since then the goldens have not been regenerated, except for
  the scenarios edited in the last step (`check_all_twice_cooldown`, `cluster_not_found`), which went through the
  final runs below.
- `uv run pytest -q tests/server/test_contract_{pipeline,check_all,scheduler,raw_vs_clean}.py`: 124 passed (before
  the last 1-test addition).
- Final runs:
  - `... -p xdist -n 2`: **125 passed**;
  - `TZ=America/New_York uv run pytest ...`: **125 passed**.
- `git status`: no tracked file modified. Only new files exist under `tests/server/`, `tests/golden/orchestration/`
  and `refactor/`.
- Isolation:
  - Time is frozen by `frozen_clock` (`time_machine`, `tick=False`). Two nested `time_machine.travel` calls are used:
    one seeds the devices 2 days earlier (raw_vs_clean, so the plant chart's assignment window covers the series);
    the other runs the second `/check` at +5 min so `decision_logs` ordering has no `evaluated_at` tie.
  - `clean_env` clears env vars and sets `TZ=UTC`. Weather comes from `install_offline_weather`; canned stubs are
    installed per scenario.
  - `get_device_gateway` is `None` or a recording fake. The notifier is a recorder, so no HTTP goes out.
  - The process-wide scheduler is only ever started `paused=True` and is always stopped in `finally`/teardown.
  - Normalization: none. APScheduler's `get_jobs()` order for ad-hoc jobs (by next fire time) is sorted by id in one
    assertion.
- Lint: `uv run ruff check` / `ruff format --check` on the 4 files are clean.
- Existing area tests still pass: the 14 existing files plus the 4 new ones together gave **327 passed**.

## Branch coverage before → after

Each column is measured on the 14 existing area test files (`test_operations`, `test_scheduler*` (5 files),
`test_decisions`, `test_leak_detection`, `test_leak_rearm`, `test_notify`, `test_bulk_stop`, `test_vacation`,
`test_pump_watcher`, `tests/test_leak_hold.py`); the "after" column adds the 4 new files.

| Module | Lines before → after | Branches before → after |
|---|---|---|
| `scheduler.py` | 124/222 (55.9 %) → 215/222 (96.8 %) | 26/42 (61.9 %) → 38/42 (90.5 %) |
| `services/irrigation.py` | 232/316 (73.4 %) → 273/316 (86.4 %) | 63/102 (61.8 %) → 81/102 (79.4 %) |
| `services/leak.py` | 70/70 (100 %) → 100 % | 22/22 (100 %) → 100 % |
| `services/manual_control.py` | 45/63 (71.4 %) → 60/63 (95.2 %) | 8/20 (40.0 %) → 18/20 (90.0 %) |

Still uncovered:
- `scheduler.py` 42, 47 (shutdown helpers, exercised only in the subprocess test); 109–113 (`_resolve_zoneinfo`
  fallbacks); 348; 435.
- `irrigation.py`: the pump-watcher `_run` closure (178–208, which runs inside an APScheduler thread);
  `handle_watcher_interrupted` failure branches (69–70, 117–118); 221–223, 245, 249–250, 326–328, 363–364, 453 (engine
  returns `None` only for a vanished cluster); `monitor_cluster` 614 and 638–647 (bad target strings / unknown
  cluster).

## Not pinned (and why)

- The pump-watcher job body / `handle_watcher_interrupted` branches. These are timing-driven (they run on an
  APScheduler worker thread and sleep on the shutdown event); they are covered by the existing
  `test_pump_watcher.py` and `test_scheduler_shutdown.py`. Driving them synchronously would need a seam.
- The `NtfyClient` HTTP transport (covered by `test_notify.py`): the goldens record *attempted* pushes at the
  `notify_*` boundary.
- Web HTML of `/scheduler` and the operations pages belong to the web work package (G3). Only status codes and
  redirects are pinned here.
- Only one recording-gateway shape (empty device logs + live read) is used. `core/sync.py` details are work-package
  I8b.

## Production lines the mutation agent should target

- **`services/irrigation.py`:**
  - `_resolve_temperature` 401–420 (every source branch);
  - `run_irrigation_pipeline`:
    - 437–439 (404 mapping), 449–450 (`triggered_by` / `bypass_quiet_hours` from `force`);
    - 467–477 (dry-run / skip activity);
    - 481–495 (no irrigator / registry / model);
    - 502–529 (health gate);
    - 543–554 (single `started_at`, `start` vs `attempted`);
    - 556–585 (`set_decision_actuated`, leak check, watcher, auto push);
    - 586–606 (failure activity + alert);
  - `check_cluster` 679–716 (`auto_run` gate, monitor path, `sync_cluster_alerts`);
  - `check_all_clusters` 728–760 (per-cluster commit, rollback, `check_failed` upsert/resolve);
  - `schedule_pump_watcher` 147–163, 212–219;
  - `_leak_check_done` 241–251;
  - `_run_leak_check` 266–295;
  - `_add_leak_check_job` 301–310 (+30 min);
  - `rearm_leak_checks` 347–369.
- **`scheduler.py`:**
  - `_JOB_DEFAULTS` 26;
  - `init_scheduler` 158–194 (`configure`, `remove_all_jobs`, order);
  - `_add_tz_bound_cron_jobs` 205–220;
  - `reschedule_for_timezone` 235–249;
  - `_get_cloud` 289;
  - job bodies 292–409;
  - `init_health_monitor` 423–443;
  - `_is_paused` 459;
  - `get_jobs` 471–485;
  - `delete_job` 503–512;
  - `set_check_all_paused` 533–541;
  - `apply_persisted_pause` 552–560.
- **`services/manual_control.py`:** `check_rate_limits` 52–73; `manual_start` 114–146 (registry before caps, no row on
  failure, watcher only with minutes); `manual_stop` 172–194; `manual_log` 223–243.
- **`services/alerts.py`:** `notify_if_new_alert` 173–184 (`occurrence_count == 1` gate, severities); `raise_alert`
  dedup key 276–278.
- **Routes:**
  - `routes/operations.py` 142–152 (`irrigate` 404 / commit), 199–205 (`has_alerts`), 226–229;
  - `routes/scheduler.py` 71–87;
  - `routes/vacation.py` 33–59 (no order check — B-8).
- **Engine gate order** as seen through the pipeline (owned by the engine work package): leak hold → cooldown → quiet
  hours → weather → water warning / stress → window → adjustments → vacation.

# WP7 review R2: behavior-breaker adversary, `scheduler.py` + `services/irrigation.py` (`c05e847` → `8ffa9d6`)

**Verdict: REJECT (narrow, T7.3 `_job_session` only).** I found one observable difference that reproduces deterministically. The T7.3 commit body explicitly claims the opposite. It only shows up when a test-style seam (monkeypatch) is used, so production impact is nil. The fix is either a two-line change or a one-line declaration. Everything else in WP7 showed **zero differences** across 2,496 differential scenarios: the pipeline, `_actuate`, check_cluster/check_all, monitor, the watcher closure, `handle_watcher_interrupted` and the constants.

## Harness (scratchpad `wp7-diff/`)
- **Trees:** `git worktree add` of `c05e847` (old) and `8ffa9d6` (new), now removed. Only `config.py`, `scheduler.py` and `services/irrigation.py` differ under `libs/`. Each variant runs in its own subprocess, selected by `PYTHONPATH`. Both use the same driver and the same test helpers (`server.conftest._make_stubbed_app`, `fake_devices`).
- **What `harness.py` builds, per scenario:**
  - a fresh real app: in-memory SQLite with StaticPool and real SQLAlchemy;
  - a fake irrigator/sensor registry, also put on `app.state` so the scheduler jobs see it;
  - a recording gateway (none / ok / raising) and a recording notifier (ok / raising / none);
  - weather: offline, rainy, hot, `feels_like=None` or raising;
  - a frozen clock (time-machine);
  - an optional real `DeviceHealthMonitor`, with a NO_WATER health gate;
  - the scheduler stopped, running-paused, or with `check_all` paused.
- **Watcher jobs:** the pump-watcher and leak-check jobs are run inline with a virtual monotonic clock and a fake shutdown event, in four modes: interrupted, completed, tripped, offline/abandoned.
- **Ops:** `/irrigate`, `/clusters/{id}/check`, `/check`, `/monitor` and the direct service calls (pipeline / check_cluster / check_all / monitor); `_check_job`, `_sync_job`, `_anomaly_job`, `_health_snapshot_job`, `_health_monitor_job`; `rearm_leak_checks`; `schedule_pump_watcher` direct, plus the watcher run; `handle_watcher_interrupted` direct; manual start/stop; scheduler jobs/pause/resume; `/sync`.
- **Scenario matrix:** dry/wet/None soil; indoor/outdoor/greenhouse; cooldown edges (6h ±60s); quiet hours including wrap and `force`; leak open/ack/resolved/old; vacation; windows; daily cap and max events; dry_run and `dry_run_global`; stale readings (5h) that force one sync; bogus model; no irrigator or sensor; missing cluster; `auto_run` off; adapter start fail/raise; adapter stop fail/raise.
- **Fault injection:**
  - "database is locked" (and "disk I/O error") on the k-th INSERT/UPDATE/DELETE, or on the k-th COMMIT, with span 1/2/3/1000;
  - a `RuntimeError` on the k-th call of 30 seams: `sync_cluster_alerts` (a cluster crashing after actuation), `maybe_notify`, `raise_alert`, `_schedule_leak_check`, `schedule_pump_watcher`, `handle_watcher_interrupted`, `IrrigationLogic`, `IrrigationService`, the repo methods, `scheduler.IrrigationRepository`, `_get_cloud`, and others.
- **Compared:** every op response/return (including dict key order and value types); raised exceptions (type and message); every DB row of every table, with `payload_json` parsed; adapter, sensor and gateway call logs (order and args); notifications; the scheduler job table (id, name, trigger, next run, func qualname, args, misfire/coalesce/max_instances); every log record (logger, level, message, exc type and message); and **every SQL statement and its params**, per op.
- **Generation:** Hypothesis (`derandomize=True`): 1,500 general scenarios + 700 biased toward actuation and faults that land, plus 296 hand-picked edges. 5,611 ops in the main set. 534 scenarios actually hit an injected fault (220 write, 199 commit, 115 py).
- **Sensitivity check:** four mutants of the new tree were all caught.
  - `20` vs `20.0` fallback temperature: 20 scenarios differ.
  - Swapped leak-check/watcher scheduling order: 235 differ.
  - Repo constructed outside the `try`: 2 differ.
  - `logger.error` instead of `logger.exception`: 64 differ.

## Results
- **2,496 scenarios.** 2,493 show 0 differences; 2 show a real difference (Finding 1); 1 is noise.
- **The noise:** `edge_locked_write_4` once showed one extra `SELECT decision_logs` (identity-map weakref versus cyclic-GC timing). It is not a refactor difference:
  - OLD re-run against OLD shows the same one-SQL difference;
  - an isolated re-run, and a full re-run with or without `gc.collect()` before each op, show old == new.

## Finding 1 (T7.3 `959cab1`, inherited by T7.4–T7.7): an exception before `yield` in `_job_session` now escapes as `RuntimeError("generator didn't yield")`
- **Old behavior:** each job ran `repo = IrrigationRepository(session)` *inside* its `try`. A failure there was rolled back, logged as "<X> job failed", and **swallowed**: the job returned `None`.
- **New behavior:** `_job_session` catches and logs it the same way, but the generator then finishes without yielding. `contextlib` raises `RuntimeError: generator didn't yield` out of `_check_job`, `_sync_job`, `_anomaly_job`, `_health_snapshot_job` and `_health_monitor_job`. In production APScheduler would add a "Job … raised an exception" record and an EVENT_JOB_ERROR.
- **Reproducer:** run `wp7-diff/repro_job_session.py` once per tree (output in `out/repro_job_session.txt`):
  ```
  git worktree add --detach <S>/old c05e847
  git worktree add --detach <S>/new 8ffa9d6
  PYTHONPATH=<S>/<v>/libs/greenhouse-core:<S>/<v>/libs/greenhouse-server .venv/bin/python repro_job_session.py
  ```
  It patches `greenhouse_server.scheduler.IrrigationRepository` to raise.
  - old: 4 × `ERROR … job failed` → `returned None`. The snapshot job is unaffected; see below.
  - new: 5 × `ERROR … job failed` → `RAISED RuntimeError generator didn't yield`.
  - The harness hits the same thing at `edge_py_sched.IrrigationRepository_{1,2}`.
- **Reachability: none in production.** `IrrigationRepository.__init__` is `self.session = session`. Only a monkeypatch, or a future `__init__` that does work, can trigger it.
- **Why reject:** the T7.3 body asserts "commit/rollback/close order and the swallow are identical". That is false on this path. This is the same situation as the WP4 T4.12 precedent: low reachability, but a false equivalence claim in the commit message.
- **Related (T7.4 `2d4bb1b`):** the commit claims the dropped inner import "could neither … change the class used". Old `_health_snapshot_job` looked up `greenhouse_core.repository.IrrigationRepository` at call time; new uses the `scheduler` module binding. Patching the core module attribute reaches old but not new (`repro_snapshot_binding.py`: old "core-module binding used", new "not used"). Same object in production.
- **Fix, either one:**
  - (a) Have `_job_session` yield the *session*, and let each caller build `IrrigationRepository(session)` inside the `with` body. That restores the exact swallow path.
  - (b) Keep the code and declare in T7.3/T7.4 under "Behavior": "a raise while constructing the repo now surfaces as `RuntimeError('generator didn't yield')` after being logged; unreachable, since `__init__` only assigns". Also correct the T7.4 claim.

## Checked and identical (static + harness)
- **Pipeline (`_actuate`):** clock read after `start`; event kwargs with `irrigator.id` first; success order activity → actuated → leak check → watcher → `result["action"]` → notify; `get_preferences` evaluated as an argument; failure order activity → alert → error; `_with_error` mutates in place, so key positions are kept.
- **Pipeline (other paths):**
  - `_actuation_target` keeps the same order: irrigator, then registry, then `UnknownDeviceModel`.
  - The device-health gate is verbatim.
  - `_resolve_temperature` makes the same weather/sensor call order, and `FALLBACK_TEMPERATURE_C` is the float `20.0`.
- **check_cluster:** `monitor.get` / `result.get` now run before `sync_cluster_alerts`, but they read plain dicts. `cluster.name` is still read after. Key order per branch is unchanged.
- **`_record_check_failure`:** runs inside `except`, so exc_info is kept; order is rollback → log → upsert → commit. Commit/flush failure at every commit point, including after a commit-time-flush expiry, behaves the same.
- **Watcher closure:** captures the same objects; settings tuning and the monitor are read at run time; the job id and name are unchanged.
- **`handle_watcher_interrupted`:** stop ok/fail/raise give identical messages, logs and rows.
- **Constants:** every replaced literal has the same value and type (2.0, 5.0, 5, 15, 0, 30, 6, 2, 15, 10, "45-65", 500). The Settings defaults are unchanged.

---

## Re-check: head `e7c638e` (4983b14 corrective + 6ab9807 T7.22)

**Verdict: APPROVE.** Old (`c05e847`) and new (`e7c638e`) behave the same in everything I compared, and both commit bodies' claims hold up.

**What the code changes do (static read of `git diff 8ffa9d6 e7c638e -- libs/`)**
- **4983b14:** `_job_session` now yields the bare session. Each job's first statement inside its `with` body is `repo = IrrigationRepository(session)`, which puts the construction back inside the old `try`. `_health_snapshot_job` once again does `from greenhouse_core.repository import IrrigationRepository` at the same spot, so the name is resolved when the job runs. The order is unchanged: factory → (try) repo → body → commit, or on failure rollback + the same `logger.exception` → close.
- **6ab9807:** `_rearm_from_events` holds the double loop verbatim and yields after each `_add_leak_check_job`. `rearm_leak_checks` consumes it inside the existing `try` and increments `scheduled` once per yield.
  - A raise from inside the generator reaches the caller's `except`, and the count of jobs already added is kept.
  - Because the generator runs lazily, every repo call and every job add happens at the same point as before.
- Under `libs/`, only `config.py`, `scheduler.py` and `services/irrigation.py` differ from `c05e847`.

**Reproducers, old vs new** (`out/recheck_repros.txt`)
- `repro_job_session.py`: identical. In both trees all 5 jobs print "returned None", and the same 4 "… job failed" ERROR logs appear.
- `repro_snapshot_binding.py`: identical. Both print "core-module binding used".

**Full differential harness, re-run on `e7c638e`** (`out/recheck_log.txt`)
- Main set: 1,796 scenarios (1,500 Hypothesis + 296 edges), **0 differences**. This includes `edge_py_sched.IrrigationRepository_{1,2}`, the scenarios that showed the original finding; they now return `None` in both trees.
- Biased set: 700 scenarios, **0 differences**.
- New re-arm / job-body batch: 796 scenarios, **0 differences**.
  - Composition: 600 Hypothesis cases plus 196 edges. The edges inject a raise on the k-th call (k = 1..7, span 1 or 1000) of `list_all_irrigators`, `get_recent_events`, `_add_leak_check_job`, `_leak_check_done`, `list_activity_events`, `scheduler.IrrigationRepository`, `greenhouse_core.repository.IrrigationRepository`, `_get_cloud` and others, plus write/commit "database is locked" faults.
  - Coverage: 136 re-arm runs failed. 45 of those failed mid-scan after adding jobs, so they logged both "Re-arming … failed" and "Re-armed k …" and returned the partial count k. 529 re-arm calls returned a positive count.
- **Total: 3,292 scenarios, 0 differences.**

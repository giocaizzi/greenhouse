---
name: greenhouse
description: |
  Talk to a running greenhouse-server over MCP to read sensors (soil moisture, temperature, humidity, light),
  drive irrigators (start, stop, log manual, emergency stop-all), review typed irrigation decisions, manage
  the alert inbox, inspect plant health and its timeline, monitor device and system health, set vacation and
  per-cluster irrigation windows, run data-quality reports, tune per-cluster config, and toggle push-notification
  (ntfy) preferences. Use this skill
  whenever the user asks anything about their plants, soil, watering, irrigation schedule, sensor readings,
  irrigator status, blocked drips, leaks, alerts, plant health, or system health — even when they don't say
  "greenhouse" or "irrigation".
  Example triggers: "is my monstera thirsty?", "water the living room cluster", "why did it skip the last
  cycle?", "did anything irrigate last night?", "check my plants", "what's the moisture in the kitchen".
metadata:
  author: giocaizzi
  version: "1.0"
---

# Greenhouse

A smart plant irrigation system. **Tuya Cloud is the live source for sensor data; the local SQLite archive is the permanent record. Actuation is local-first over Tuya protocol v3.5** — the run length (Duration DP) and the dry-run safety read (DP 105) are local-only; the on/off switch pulse is the one deliberate Cloud call (the gateway-attached pump can't be kept awake reliably over LAN). There is no keep-alive fallback (removed): if the local Duration DP cannot be written, a timed start is refused before the pump is switched on. This plugin gives you MCP tools that hit the user's running greenhouse-server.

## Mental model

```
Cluster ─┬─ Plants (1..N)        ← species-specific care data drives targets
         ├─ Sensors (0..N)       ← soil moisture / temp / humidity / light
         └─ Irrigator (0..1)     ← at most one Tuya device, local protocol;
                                   optional reservoir_l + flow_rate_l_per_min capacity
              │
              ▼
         Decision engine ─→ IrrigationDecision (typed) ─→ DecisionLog (persisted, every eval)
```

A cluster has **at most one irrigator** (strict 0:1 — the cluster is irrigated by a single device, DB-enforced). The irrigator is a singular, cluster-keyed resource: `GET/POST/PUT/DELETE clusters/{id}/irrigator` manage the one device (POST 409s if one already exists; GET/PUT/DELETE 404 if none), while `irrigators` is the global list and device actions (`irrigators/{id}/start|stop|log-manual`) stay keyed by device id. Cluster `status` and `monitor` expose a single `irrigator` field (object or null), not a list.

Every check produces one `IrrigationDecision` with a `reasons: list[Reason]` trail. Each `Reason` carries a stable `TriggerCode` enum (e.g. `cooldown`, `sensor_dry`, `weather_skip`, `conflict`). **The decision is persisted whether or not it actuated** — that audit trail is your friend when the user asks "why didn't anything happen?".

## How to use the bundled MCP tools

Every `/api/v1` endpoint on the server is exposed as an MCP tool by `fastapi-mcp`. You don't need to memorize names — list the tools at the start of the session if you're unsure. Common moves:

| User intent | What to call |
|---|---|
| "Is my plant thirsty?" / "Check status" | `clusters/{id}/status` or `clusters/{id}/monitor` (never actuates; refreshes stale sensors from the Cloud and stores the readings) |
| "Water cluster X" | `clusters/{id}/irrigate` (smart pipeline) or `irrigators/{id}/start` (manual — same path as the web UI button: per-day caps and the global dry run are checked (409), `minutes` must be ≥ 1 (422); with `minutes` it arms the dry-run pump watcher and a post-watering leak check that runs once the run has ended and settled; ntfy notification) |
| "Why did it skip / why didn't anything run?" | `clusters/{id}/decisions` — read `primary_code` and `reason_text` |
| "Did the schedule fire?" | `clusters/{id}/history` + `clusters/{id}/decisions` |
| "What's wrong?" | `alerts` inbox (filter by `status=open`) |
| "I just watered by hand" | `irrigators/{id}/log-manual` |
| "Show me trends" | `clusters/{id}/chart-data` or `plants/{id}/chart-data` (`hours` look-back, default 72 = 3 days; the web UI and TUI offer 3, 14, 30, 60 and 90 days). Each series is one sensor while it measured one plant: `label` reads `"<species> · <sensor>"`, or `"<sensor> (unassigned)"` with `plant_id` null for a stretch without an assignment, so a moved sensor shows up as several series |
| "I'll be away next week" | Create a `vacation` window (Unix-second `starts_at` strictly before `ends_at`, else 400; the web UI and TUI enter these times in the `timezone` preference); set the cluster's irrigator `reservoir_l` + `flow_rate_l_per_min` so the engine rations the tank across the trip |
| "Only water at night / set allowed hours" | Per-cluster irrigation `windows` (CRUD under `clusters/{id}/windows`) |
| "Don't water overnight / set quiet hours" | `quiet_start_hour`/`quiet_end_hour` on `clusters/{id}/config` (per cluster) or `config/global` (everywhere) |
| "Set system-wide defaults" / "what's inherited?" | `config/global` (read/write defaults); `clusters/{id}/config/effective` (merged view, source per field) |
| "Water it now even though it's quiet hours" | `clusters/{id}/irrigate` with `force=true` — bypasses the quiet gate only, logs `manual_override_quiet_hours`; the run is recorded as **manual** (decision log, event, push) and still gets the leak check and the dry-run watcher |
| "I moved the sensor to another pot" / "the probe was in the wrong pot since 9:00" | `clusters/{id}/sensors/{sid}/move` with `plant_id` (null = detach) and `effective_at` (Unix seconds, default now; future or before the current link → 400). Same plant as now = re-date the last move. Readings are attributed by that history; for 30 min after a move the sensor is not judged (`sensor_settling` in the decision trail). `sensors/{sid}/assignments` shows the history |
| "Remove this sensor / plant" | `DELETE clusters/{id}/sensors/{sid}` (or `…/plants/{pid}`) **archives** it: id, readings, history and the Tuya device id are kept, and it drops out of listings, sync, health checks, snapshots and decisions (an archived sensor's plant link is closed; an archived plant's sensors stay in the cluster, unlinked). Undo with `…/restore`. `?purge=true` hard-deletes an *already archived* row (409 otherwise) and frees the device id; ask the user first, it is irreversible. Listings take `include_archived=true`; re-registering an archived sensor's device id is a 409 naming it. Every archive/restore/purge writes an activity event with the name and Tuya id |
| "How healthy is my monstera?" | `plants/{id}/health`; `plants/{id}/health-timeline` for the trend |
| "What moisture band is my plant judged against?" | `plants/{id}`: `soil_moisture_min` / `soil_moisture_max` and `soil_band_source` (`species`, `category_defaults`, `category`, or `builtin_fallback` = the 45-65 % default) |
| "Refresh the health scores now" | `plants/health/snapshot` (trigger a snapshot) |
| "When will it next water?" | `clusters/{id}/forecast` |
| "How well is irrigation working?" | `clusters/{id}/efficacy` or `clusters/{id}/insights` |
| "Is anything offline / are sensors stale?" / "is the irrigator working?" | `health/system` (device + cloud + scheduler pulse). An irrigator reads `failing` after 3 failed starts in a row (or one while its `actuation_failed` alert is open), with `consecutive_failures` on its row and `irrigators_failing` at the top level; any failing irrigator makes the status `degraded`, and its next successful start puts it back to `ok` |
| "Is my setup configured right?" | `quality/report` (config gaps, stale sensors, duplicate device IDs) |
| "Find …" / search across resources | `search` |
| "Stop / start push alerts" / "notify me when it waters" | `preferences` — `notify_manual` / `notify_emergency` / `notify_alerts` / `notify_auto` toggles |
| "Emergency — stop everything" | `bulk/stop-all` — read `errors`: a device that refused or failed the stop is listed there (it may still be running) and is not counted in `stopped` |

For the full surface, list the MCP tools at the start of the session — every `/api/v1` endpoint is exposed as one. If you need the raw OpenAPI, the server publishes it at `/docs`.

**Push notifications (ntfy):** the server can push to a phone on manual/emergency/auto irrigations and new (warning/critical) alerts. A warning or critical alert that stays open (e.g. a repeating `actuation_failed`) pushes again at its 3rd and 10th occurrence, then at most once a day until it is resolved; acknowledging it buys a quiet day, it does not mute it. A resolved alert that comes back pushes as new. Every day at 09:00 (the `timezone` preference) one digest push lists how many critical alerts are still unresolved (open or acknowledged) and how long the oldest has been open; no push when there are none. Delivery is enabled by server-side env config (`GREENHOUSE_NTFY_SERVER_URL` + `_TOPIC`); the per-category toggles in `preferences` (`notify_*`) only gate *which* categories fire. You don't send notifications — you can read/flip those toggles. If a user says "I'm not getting alerts," check the toggles, but the env config is the on/off switch and lives outside the API.

## Invariants — do not fight these

The engine enforces several rules. Respect them when reasoning; don't try to bypass them on the user's behalf.

1. **6h global cooldown** between irrigations per cluster — any `start` event counts, including a manual start and a logged manual watering (`log-manual`); a start exactly 6 h ago no longer blocks. If the user asks "why didn't it water?" and the decision says `cooldown`, that's working as designed. Don't suggest forcing it unless they ask explicitly — and even then, the right move is a manual `irrigators/{id}/start` call (which the user is consciously taking responsibility for), not a workaround.
2. **Driest plant drives the call** — each sensor's newest (cleaned) soil reading is judged against the band of the plant it is linked to (an unlinked sensor gets the cluster's widest band), and the driest one decides; the average never does. Critical water stress works the same way, with a plant-aware threshold (15 below the plant's minimum, never above 30 %), so a cactus at 28 % is not an emergency. A sensor whose newest reading is older than 4 h is ignored (all stale → the no-data fallback), and so is one moved to another plant less than 30 min ago; a reading taken in the old pot never judges the new plant. When the user has a multi-plant cluster and asks "is the cluster wet?", look at each sensor's latest value against its own plant, not the mean.
3. **Multi-sensor conflict resolution**: one dry + one wet → `conflict` reason → short 1-minute burst, not a normal cycle. Don't surprised when duration is small in this case.
4. **Weather skip**: for non-indoor clusters, if forecast precipitation in the next 6h exceeds 2 mm, the engine skips with `weather_skip` and that's correct.
5. **Actuation gates run before every pipeline start, in order: device health → per-day caps → global dry run.** An open no-water / rain / offline alarm on the irrigator turns the run into a skip (`device_*` code, the API result carries `blocking_alarms`); a start that would exceed `max_events_per_day` / `daily_cap_minutes` (cluster value, else the global one) skips with `daily_cap_hit`; with the `dry_run_global` preference on it skips with `dry_run_global`. The decision log row is rewritten to that skip (`primary_code` = the gate's code), so `decisions` tells you why. `force=true` bypasses none of them. A device-health alarm that was open before a server restart blocks again immediately. Separately, a 15-minute anomaly scan raises `sensor_stale` / `sensor_drift` / `sensor_flatline` warnings; those inform and never block. `sensor_stale` needs the sensor silent past one sync interval + 30 min (readings only land at each sync) and resolves itself once the sensor reports again; `sensor_drift` resolves itself once the latest reading is back within 4σ. `sensor_flatline` means the soil has sat flat at ≤ 2 % or ≥ 98 % for 48h while readings keep arriving: suspect the probe (out of the soil, in water) before trusting that "dry" or "saturated" reading; it resolves once the series moves. A sensor sync that meets Tuya's "IoT Core service subscription has expired" answer raises one critical `tuya_subscription_expired` alert (pushed via ntfy): sync and the Cloud on/off pulse are down until the plan is renewed on the Tuya console; the next sync without errors resolves it. If actuation fails or is blocked, check `alerts` and `activity` to see why.
5b. **A leak / stuck valve holds the cluster for 24h** — the post-irrigation detector runs after every real start: 30 min after an automatic or forced run, and after a manual start with `minutes` once the run has ended plus a 25-minute settle (at least 30 min); a manual start without `minutes` gets none (the activity log says `leak_check_skipped`), a `log-manual` entry gets none. It is re-armed after a server restart and never runs twice; a confirmed finding raises a critical `leak_or_stuck_valve` alert, and *that alert is the hold*: decisions come back `skip` with `leak_hold` until it is resolved (`alerts/{id}/resolve`) or ages out. Acknowledging is not enough, and `force=true` does not bypass it. If the user has checked the hardware and wants water now, resolve the alert (or use `irrigators/{id}/start` deliberately). A hold that keeps coming back on every cycle is a real signal — check the sensor for drift before assuming a burst pipe.
6. **Learning is advisory** — alerts like `blocked_drip` or `chronic_underwatering` inform; they do not block irrigation.
7. **Vacation windows ration, not just pause** — during an active vacation the engine is genuinely enforced. If a cluster's irrigator carries both `reservoir_l` and `flow_rate_l_per_min`, the engine burns the tank down over the trip: it trims a run's duration to fit the per-day budget (`vacation_rationing`) or skips when the budget is spent (`vacation_budget_exhausted`), and tags the decision `vacation_active`. Clusters with no capacity configured irrigate normally. The emergency paths — critical water stress, the device water warning, the no-sensor fallback — are rationed too (and obey irrigation windows): an emergency changes the dose, not the operator's limits. Decisions that end before the engine's rules (cooldown, quiet hours, leak hold, weather, window) carry no `vacation_active` tag. A vacation is over at its `ends_at` second. When the budget is spent, `vacation_budget_exhausted` is the decision's `primary_code`. Check the decision reasons before assuming the system is broken; see references/LOGIC.md for the budget math.
8. **Quiet hours are a hard gate on auto runs** — if a decision says `quiet_hours`, the engine skipped because the local time is inside the configured window (cluster → global config). It only affects automatic runs; a manual `irrigate` with `force=true` (or `irrigators/{id}/start`) overrides it and the decision is tagged `manual_override_quiet_hours` (a forced run is then recorded as manual). Config is hierarchical (cluster overrides global overrides built-in); use `config/effective` to see what actually applies.

When summarizing decisions, lead with the `primary_code` and the human-readable `reason_text` (never empty: a run no rule acted on carries `no_action_needed`). Don't paraphrase — those codes are stable identifiers the user can search for and the developer documents.

## Authentication

The MCP server is bearer-token authenticated. The plugin's `.mcp.json` reads two env vars:

- `GREENHOUSE_SERVER_URL` — base URL of the running server, e.g. `http://192.168.1.50:8000` or `http://localhost:8000`.
- `GREENHOUSE_MCP_TOKEN` — the bearer token (matches the server's `GREENHOUSE_MCP_TOKEN`).

If tools fail with 401 or 503, the most likely cause is a missing/mismatched token: 503 means the server has no token configured (unset, empty or whitespace-only `GREENHOUSE_MCP_TOKEN` — the server reads only that name), 401 (with `WWW-Authenticate: Bearer`) means the bearer header is missing or wrong. The token works only as an `Authorization: Bearer` header, never as a cookie. If the user hasn't set these, point them at the plugin README — don't try to guess the URL.

> The bearer token grants **physical actuation authority** (the server can start/stop real solenoid valves). Treat it like a root credential and never log it.

## When to load each reference

- [references/LOGIC.md](references/LOGIC.md) — decision pipeline, complete `TriggerCode` enum, trust layer, learning engine, confidence scoring, threshold constants. Load when the user asks *why* the engine did or didn't do something, or when interpreting a `DecisionLog`. The `TriggerCode` values returned by MCP tools are stable identifiers — this file is how you turn them into human explanations.
- [references/PLANT_DATABASE.md](references/PLANT_DATABASE.md) — the plant-care data schema and the species → category → defaults fallback rule. Load when the user is adding a plant, asking about target moisture/temperature for a species, or extending the database.
- [references/CLI.md](references/CLI.md) — `greenhouse` CLI surface, server URL resolution, JSON-stdout + exit-code contract, common shell workflows. Load when the user asks for a shell command, wants to write a cron / bash script, or when MCP isn't reachable and the CLI is the fallback transport.

Don't preload these. The endpoint catalogue isn't here on purpose — the MCP tool list is the source of truth and won't drift, so consult that for "does the server have an endpoint for X?" questions.

## Common pitfalls

- **"It silently failed"** is almost never silent. Check `clusters/{id}/decisions` for the latest evaluation — the engine writes a log row even when it skips. If there's no row in the expected window, the scheduler didn't fire — check `health/system` and `scheduler/jobs`. In `scheduler/jobs`, `paused: true` means explicitly paused (only `check_all`, via `scheduler/pause`, mirrored by `preferences.scheduler_paused`); a null `next_run_time` on an un-paused job means the scheduler itself isn't running (`scheduler_running: false` on `health`), not that the job is paused. Pause/resume work (and persist) even while the scheduler is stopped. `core: true` marks a built-in job: `DELETE scheduler/jobs/{id}` refuses it with 409 — pause `check_all` instead.
- **Don't delete built-in scheduler jobs.** `DELETE scheduler/jobs/{id}` returns 409 for the jobs registered at startup (`check_all`, `sensor_sync`, `sensor_anomaly`, `device_health_monitor`, `plant_health_snapshot`, `alert_digest`); to stop automatic irrigation use `scheduler/pause`. Only ad-hoc one-shot jobs (pump watchers, leak checks) are deletable; an id containing `/` works (percent-encode it as one path segment).
- **Don't average sensors in a cluster** when interpreting state — the engine judges each sensor's latest reading against its own plant's band and the driest one decides, and so should you when explaining results back to the user. A device soil-dry alarm (`water_warning`) beside over-watered soil is not acted on (`over_watering` skip, alarm recorded); beside a plant that merely reads above its band it is noted and the soil rule decides.
- **Refusals are explicit, not silent no-ops.** Report the error to the user instead of retrying blindly:
  - 400: a reversed or empty vacation window (create and update).
  - 404: `plants/sync` with an unknown `plant_id` or `cluster_id`; linking a sensor (create or update) to a plant of another cluster (`Plant N not found in cluster`); a `preferences.default_cluster_id` that names no cluster; any unknown id.
  - 409: a duplicate Tuya device id; `irrigators/{id}/start` (and `log-manual`) over a per-day cap (`cluster max_events_per_day reached` / `irrigator daily cap reached`); `irrigators/{id}/start` while the global dry run is on (`global dry run is on: irrigator not started`).
  - 422: an irrigator / sensor `type` that is not a registered model key on create or when changed (the message lists the known keys, e.g. `rainpoint.ik10pw`, `tuya.tr301z`); `irrigators/{id}/start` with `minutes` < 1; `NaN` / `Infinity` in a measurement or threshold; `clusters/{id}/efficacy` `days` outside 1–365.
  - 502: the device refused or failed the start — for the IK10PW also when the local Duration DP is unreachable (wrong IP, network, or a `local_key` the Cloud cannot correct): the timed start is refused before the pump is switched on. A stored key that went stale because the device was re-paired no longer causes this: the server refreshes it from the Tuya Cloud once (at most hourly per device) and retries the Duration write.
  - 500 on a start: the pump started but the start could not be recorded, so the server stopped it again (`the start could not be recorded, so the irrigator was stopped again`); `irrigate` answers 500 in the same case.
  - 503: no device registry (missing Tuya credentials) or an irrigator whose stored type has no adapter.
- **Stop events read `stop`.** Manual, automatic-shutdown and emergency stops all record action `stop` in history/stats; databases older than this change may still contain `off` rows for earlier manual stops — treat both as "stopped". A pump-watcher dry-run abort records `aborted`; an automatic start whose device call failed records `attempted`. A device whose stored `config` is malformed (or not a JSON object) comes back as `config: {}` instead of an HTTP 500.
- **`local_key` and `device_ip` are masked.** Irrigator and sensor responses show `config.local_key` and `config.device_ip` as `••••••••`. Never report that as the key or the IP. Writing the mask back (e.g. updating `config` from a read) keeps the stored value; a new value replaces it; `config: null` clears the whole config, `local_key` included. The server also replaces the stored key by itself when it fetches one from the Tuya Cloud (a missing key, or a stale one after the device was re-paired); the response still shows the mask.
- **PATCH-style updates tell absent from `null`.** Omitted fields stay unchanged; an explicit `null` clears an optional field (cluster `location`, plant `notes`, vacation `notes` / `email`, sensor `plant_id`, `preferences.default_cluster_id`) and is ignored for a required one. Send only what the user asked to change.
- **Stats on a sensor-only cluster are zeros, not an error.** `clusters/{id}/stats` for a cluster without an irrigator returns the normal shape with zero totals (`period_days` = the requested days).
- **`dry_run_global` means nothing starts.** With the preference on, automatic runs and `irrigate` (even with `force=true`) evaluate, log the decision and skip with `dry_run_global`; `irrigators/{id}/start` answers 409. Stops and `bulk/stop-all` always work. It does not stop a pump that is already running — use `irrigators/{id}/stop` or `bulk/stop-all` for that, and `scheduler/pause` to stop automatic checks altogether.
- **Per-day caps guard every start.** `max_events_per_day` / `daily_cap_minutes` resolve cluster → global (`config/effective` shows which applies). A manual start or `log-manual` over the cap answers 409; `irrigate` / `check` / the scheduled run skip with `daily_cap_hit`.
- **No decision row is not always a scheduler failure.** `check` writes no decision for a cluster whose effective `auto_run` is false (`action: "skipped"`) or that has no irrigator (it runs the monitor instead).
- **Sensor "offline" lags by design.** Sensor readings reach the database through the background sync (every `IRRIGATION_SYNC_INTERVAL_MINUTES`, default 180), so a sensor counts as offline only when its newest stored reading is older than one sync interval + 30 min (210 min by default); irrigators go offline after 30 min without a live local read. A sensor's `water_warning` means "soil dry" (it can trigger irrigation), not a probe fault.
- **CSV export is binary**. The `clusters/{id}/stats/export` endpoint returns a file, not JSON. If you call it through MCP, expect a blob you'll need to save and tell the user where it landed.
- **Sensor-only clusters get a watering reminder.** A cluster without an irrigator never waters itself; when one of its sensors has read `dry` / `very_dry` for 3 h, the hourly check opens a `needs_water` alert on that sensor (warning, critical when very dry), pushed once via ntfy unless `notify_alerts` is off, never opened during quiet hours. It resolves on the first check after the soil is back in band. When the user asks "does anything need water?", list open `needs_water` alerts alongside `clusters/{id}/monitor`.
- **A plant on the default band is flagged, not silent.** When neither the species nor the category is in the plant database, the plant is judged against the built-in 45-65 % band: `plants/{id}` and the plant create / update responses say `soil_band_source: builtin_fallback`, and an info `plant_care_defaulted` alert (one per plant, no push) stays open. Fixing the species or category (any case: `Tropical` = `tropical`) resolves it on the write; the hourly check also reconciles plants nobody edited. Moving the plant (`plants/{id}/move`) takes its open alerts to the new cluster; resolved ones stay with the old one. Suggest a species in the plant DB or a matching category rather than living with the default.
- **Plant health is a daily snapshot**, not a live read. If the user wants live conditions, look at sensor readings, not the health score. Use `plants/{id}/health-timeline` for the trend, and `plants/health/snapshot` to force a fresh snapshot on demand.

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

A smart plant irrigation system. **Tuya Cloud is the live source for sensor data; the local SQLite archive is the permanent record. Actuation is local-only over Tuya protocol v3.5 — never via the Cloud.** This plugin gives you MCP tools that hit the user's running greenhouse-server.

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
| "Is my plant thirsty?" / "Check status" | `clusters/{id}/status` or `clusters/{id}/monitor` |
| "Water cluster X" | `clusters/{id}/irrigate` (smart pipeline) or `irrigators/{id}/start` (manual) |
| "Why did it skip / why didn't anything run?" | `clusters/{id}/decisions` — read `primary_code` and `reason_text` |
| "Did the schedule fire?" | `clusters/{id}/history` + `clusters/{id}/decisions` |
| "What's wrong?" | `alerts` inbox (filter by `status=open`) |
| "I just watered by hand" | `irrigators/{id}/log-manual` |
| "Show me trends" | `clusters/{id}/chart-data` or `plants/{id}/chart-data` |
| "I'll be away next week" | Create a `vacation` window; set the cluster's irrigator `reservoir_l` + `flow_rate_l_per_min` so the engine rations the tank across the trip |
| "Only water at night / set allowed hours" | Per-cluster irrigation `windows` (CRUD under `clusters/{id}/windows`) |
| "Don't water overnight / set quiet hours" | `quiet_start_hour`/`quiet_end_hour` on `clusters/{id}/config` (per cluster) or `config/global` (everywhere) |
| "Set system-wide defaults" / "what's inherited?" | `config/global` (read/write defaults); `clusters/{id}/config/effective` (merged view, source per field) |
| "Water it now even though it's quiet hours" | `clusters/{id}/irrigate` with `force=true` — bypasses the quiet gate, logs `manual_override_quiet_hours` |
| "How healthy is my monstera?" | `plants/{id}/health`; `plants/{id}/health-timeline` for the trend |
| "Refresh the health scores now" | `plants/health/snapshot` (trigger a snapshot) |
| "When will it next water?" | `clusters/{id}/forecast` |
| "How well is irrigation working?" | `clusters/{id}/efficacy` or `clusters/{id}/insights` |
| "Is anything offline / are sensors stale?" | `health/system` (device + cloud + scheduler pulse) |
| "Is my setup configured right?" | `quality/report` (config gaps, stale sensors, duplicate device IDs) |
| "Find …" / search across resources | `search` |
| "Stop / start push alerts" / "notify me when it waters" | `preferences` — `notify_manual` / `notify_emergency` / `notify_alerts` / `notify_auto` toggles |
| "Emergency — stop everything" | `bulk/stop-all` |

For the full surface, list the MCP tools at the start of the session — every `/api/v1` endpoint is exposed as one. If you need the raw OpenAPI, the server publishes it at `/docs`.

**Push notifications (ntfy):** the server can push to a phone on manual/emergency/auto irrigations and new (warning/critical) alerts. Delivery is enabled by server-side env config (`GREENHOUSE_NTFY_SERVER_URL` + `_TOPIC`); the per-category toggles in `preferences` (`notify_*`) only gate *which* categories fire. You don't send notifications — you can read/flip those toggles. If a user says "I'm not getting alerts," check the toggles, but the env config is the on/off switch and lives outside the API.

## Invariants — do not fight these

The engine enforces several rules. Respect them when reasoning; don't try to bypass them on the user's behalf.

1. **6h global cooldown** between irrigations per cluster. If the user asks "why didn't it water?" and the decision says `cooldown`, that's working as designed. Don't suggest forcing it unless they ask explicitly — and even then, the right move is a manual `irrigators/{id}/start` call (which the user is consciously taking responsibility for), not a workaround.
2. **Driest plant drives the call** (`min_soil_moisture`, not the average). When the user has a multi-plant cluster and asks "is the cluster wet?", look at the minimum, not the mean.
3. **Multi-sensor conflict resolution**: one dry + one wet → `conflict` reason → short 1-minute burst, not a normal cycle. Don't surprised when duration is small in this case.
4. **Weather skip**: if forecast precipitation ≥ threshold in the next 6h, the engine skips with `weather_skip` and that's correct.
5. **Trust layer runs before every actuation** — sensor drift / stale-data detection. If actuation fails or is blocked, check `alerts` to see why.
5b. **A leak / stuck valve holds the cluster for 24h** — the post-irrigation detector runs 30 min after each cycle; a confirmed finding raises a critical `leak_or_stuck_valve` alert, and *that alert is the hold*: decisions come back `skip` with `leak_hold` until it is resolved (`alerts/{id}/resolve`) or ages out. Acknowledging is not enough, and `force=true` does not bypass it. If the user has checked the hardware and wants water now, resolve the alert (or use `irrigators/{id}/start` deliberately). A hold that keeps coming back on every cycle is a real signal — check the sensor for drift before assuming a burst pipe.
6. **Learning is advisory** — alerts like `blocked_drip` or `chronic_underwatering` inform; they do not block irrigation.
7. **Vacation windows ration, not just pause** — during an active vacation the engine is genuinely enforced. If a cluster's irrigator carries both `reservoir_l` and `flow_rate_l_per_min`, the engine burns the tank down over the trip: it trims a run's duration to fit the per-day budget (`vacation_rationing`) or skips when the budget is spent (`vacation_budget_exhausted`), and tags every decision `vacation_active`. Clusters with no capacity configured irrigate normally. Check the decision reasons before assuming the system is broken; see references/LOGIC.md for the budget math.
8. **Quiet hours are a hard gate on auto runs** — if a decision says `quiet_hours`, the engine skipped because the local time is inside the configured window (cluster → global config). It only affects automatic runs; a manual `irrigate` with `force=true` (or `irrigators/{id}/start`) overrides it and the decision is tagged `manual_override_quiet_hours`. Config is hierarchical (cluster overrides global overrides built-in); use `config/effective` to see what actually applies.

When summarizing decisions, lead with the `primary_code` and the human-readable `reason_text`. Don't paraphrase — those codes are stable identifiers the user can search for and the developer documents.

## Authentication

The MCP server is bearer-token authenticated. The plugin's `.mcp.json` reads two env vars:

- `GREENHOUSE_SERVER_URL` — base URL of the running server, e.g. `http://192.168.1.50:8000` or `http://localhost:8000`.
- `GREENHOUSE_MCP_TOKEN` — the bearer token (matches the server's `GREENHOUSE_MCP_TOKEN`).

If tools fail with 401 or 503, the most likely cause is a missing/mismatched token. If the user hasn't set these, point them at the plugin README — don't try to guess the URL.

> The bearer token grants **physical actuation authority** (the server can start/stop real solenoid valves). Treat it like a root credential and never log it.

## When to load each reference

- [references/LOGIC.md](references/LOGIC.md) — decision pipeline, complete `TriggerCode` enum, trust layer, learning engine, confidence scoring, threshold constants. Load when the user asks *why* the engine did or didn't do something, or when interpreting a `DecisionLog`. The `TriggerCode` values returned by MCP tools are stable identifiers — this file is how you turn them into human explanations.
- [references/PLANT_DATABASE.md](references/PLANT_DATABASE.md) — the plant-care data schema and the species → category → defaults fallback rule. Load when the user is adding a plant, asking about target moisture/temperature for a species, or extending the database.
- [references/CLI.md](references/CLI.md) — `greenhouse` CLI surface, server URL resolution, JSON-stdout + exit-code contract, common shell workflows. Load when the user asks for a shell command, wants to write a cron / bash script, or when MCP isn't reachable and the CLI is the fallback transport.

Don't preload these. The endpoint catalogue isn't here on purpose — the MCP tool list is the source of truth and won't drift, so consult that for "does the server have an endpoint for X?" questions.

## Common pitfalls

- **"It silently failed"** is almost never silent. Check `clusters/{id}/decisions` for the latest evaluation — the engine writes a log row even when it skips. If there's no row in the expected window, the scheduler didn't fire — check `health/system` and `scheduler/jobs`.
- **Don't average sensors in a cluster** when interpreting state — the engine uses the minimum, and so should you when explaining results back to the user.
- **CSV export is binary**. The `clusters/{id}/stats/export` endpoint returns a file, not JSON. If you call it through MCP, expect a blob you'll need to save and tell the user where it landed.
- **Plant health is a daily snapshot**, not a live read. If the user wants live conditions, look at sensor readings, not the health score. Use `plants/{id}/health-timeline` for the trend, and `plants/health/snapshot` to force a fresh snapshot on demand.

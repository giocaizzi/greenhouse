---
name: greenhouse
description: |
  Talk to a running greenhouse-server over MCP to read sensors (soil moisture, temperature, humidity, light),
  drive irrigators (start, stop, log manual, emergency stop-all), review typed irrigation decisions, manage
  the alert inbox, inspect plant health, and tune per-cluster config. Use this skill whenever the user asks
  anything about their plants, soil, watering, irrigation schedule, sensor readings, irrigator status,
  blocked drips, leaks, alerts, or plant health — even when they don't say "greenhouse" or "irrigation".
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
         └─ Irrigators (0..1)    ← Tuya device, local protocol
              │
              ▼
         Decision engine ─→ IrrigationDecision (typed) ─→ DecisionLog (persisted, every eval)
```

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
| "I'll be away next week" | Create a `vacation` window |
| "How healthy is my monstera?" | `plants/{id}/health` |
| "Emergency — stop everything" | `bulk/stop-all` |

For the full endpoint catalog see [references/API.md](references/API.md).

## Invariants — do not fight these

The engine enforces several rules. Respect them when reasoning; don't try to bypass them on the user's behalf.

1. **6h global cooldown** between irrigations per cluster. If the user asks "why didn't it water?" and the decision says `cooldown`, that's working as designed. Don't suggest forcing it unless they ask explicitly — and even then, the right move is a manual `irrigators/{id}/start` call (which the user is consciously taking responsibility for), not a workaround.
2. **Driest plant drives the call** (`min_soil_moisture`, not the average). When the user has a multi-plant cluster and asks "is the cluster wet?", look at the minimum, not the mean.
3. **Multi-sensor conflict resolution**: one dry + one wet → `conflict` reason → short 1-minute burst, not a normal cycle. Don't surprised when duration is small in this case.
4. **Weather skip**: if forecast precipitation ≥ threshold in the next 6h, the engine skips with `weather_skip` and that's correct.
5. **Trust layer runs before every actuation** — sensor drift / stale data / leak / stuck-valve detection. If actuation fails or is blocked, check `alerts` to see why.
6. **Learning is advisory** — alerts like `blocked_drip` or `chronic_underwatering` inform; they do not block irrigation.
7. **Vacation windows pause everything** — check before assuming the system is broken.

When summarizing decisions, lead with the `primary_code` and the human-readable `reason_text`. Don't paraphrase — those codes are stable identifiers the user can search for and the developer documents.

## Authentication

The MCP server is bearer-token authenticated. The plugin's `.mcp.json` reads two env vars:

- `GREENHOUSE_SERVER_URL` — base URL of the running server, e.g. `http://192.168.1.50:8000` or `http://localhost:8000`.
- `GREENHOUSE_MCP_TOKEN` — the bearer token (matches the server's `GREENHOUSE_MCP_TOKEN`).

If tools fail with 401 or 503, the most likely cause is a missing/mismatched token. If the user hasn't set these, point them at the plugin README — don't try to guess the URL.

> The bearer token grants **physical actuation authority** (the server can start/stop real solenoid valves). Treat it like a root credential and never log it.

## When to load each reference

- [references/API.md](references/API.md) — full endpoint inventory grouped by resource. Load when the user asks "what can the server do?" or you need to confirm an endpoint exists.
- [references/LOGIC.md](references/LOGIC.md) — decision pipeline, complete `TriggerCode` enum, trust layer, learning engine, confidence scoring, threshold constants. Load when the user asks *why* the engine did or didn't do something, or when interpreting a `DecisionLog`.
- [references/PLANT_DATABASE.md](references/PLANT_DATABASE.md) — evidence-based plant care data structure, current species coverage, how to add a new species (min 2 sources). Load when the user is adding plants, asking about targets, or wants to extend coverage.
- [references/CLI.md](references/CLI.md) — `greenhouse` CLI surface, server URL resolution, JSON-stdout + exit-code contract, common shell workflows. Load when the user asks for a shell command, wants to write a cron / bash script, or when MCP isn't reachable and the CLI is the fallback transport.

Don't preload these — they're large and most sessions only need one.

## Common pitfalls

- **"It silently failed"** is almost never silent. Check `clusters/{id}/decisions` for the latest evaluation — the engine writes a log row even when it skips. If there's no row in the expected window, the scheduler didn't fire — check `health/system` and `scheduler/jobs`.
- **Don't average sensors in a cluster** when interpreting state — the engine uses the minimum, and so should you when explaining results back to the user.
- **CSV export is binary**. The `clusters/{id}/stats/export` endpoint returns a file, not JSON. If you call it through MCP, expect a blob you'll need to save and tell the user where it landed.
- **Plant health is a daily snapshot**, not a live read. If the user wants live conditions, look at sensor readings, not the health score.

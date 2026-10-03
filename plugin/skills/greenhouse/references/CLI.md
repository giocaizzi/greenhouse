# CLI Reference

The `greenhouse` CLI is a thin Typer client that hits the server's `/api/v1` over HTTP. Same surface, different transport — every CLI command maps to one or more endpoints (and therefore to MCP tools).

## When to prefer the CLI over MCP

Default: **use MCP tools**. They return structured JSON with typed fields, no parsing, no quoting headaches. Reach for the CLI when:

1. **Authoring shell scripts / cron jobs** for the user. The CLI's stdout (JSON) + exit codes are designed to be piped: `greenhouse check --all | jq '.results[].alerts[]'`.
2. **The agent is on a host where the CLI is installed but MCP isn't configured** (no `GREENHOUSE_MCP_TOKEN` set, or the server's `/mcp` endpoint is firewalled while the API is reachable on LAN).
3. **Teaching the human** — they're asking "what's the command to X?" and want copy-pasteable shell.

If none of these apply, the MCP tool is the better call.

## Server URL resolution

The CLI picks the server URL in this order, first match wins:

1. `--server <url>` flag (passed before the subcommand: `greenhouse --server http://10.0.0.5:8000 status 1`)
2. `IRRIGATION_SERVER_URL` env var
3. `http://localhost:8000` (hard default)

Note this is **distinct from the plugin's `GREENHOUSE_SERVER_URL`** — that env var feeds the MCP transport in `.mcp.json`. The CLI uses `IRRIGATION_SERVER_URL`. If you're writing a script that uses both, set both.

## Output and exit codes

Every command pretty-prints JSON to stdout via `rich.print_json`. Errors go to stderr.

| Exit code | Meaning |
|---|---|
| 0 | Success (action completed, no alerts) |
| 1 | Error — server returned non-2xx (`Error: <detail>` on stderr), the server is unreachable, a required argument is missing, or `irrigate` / `check` came back with `action: "error"` |
| 2 | Soft signal — `check --all` found something that needs attention, or `monitor` found a plant that needs water |

Exit code `2` is the interesting one: `greenhouse check --all` exits 2 when the server reports `has_alerts` — any cluster has learning alerts, maintenance items (stale data, low battery, …) **or** plants that need water. A single-cluster `greenhouse check <id>` returns that cluster's result without a `has_alerts` flag, so it exits 0 or 1 only — read its `alerts` / `maintenance` / `needs_water` lists. `greenhouse monitor <cluster>` exits 2 when its `needs_water` list is non-empty. This is what makes both usable in cron — alerting frameworks key on non-zero exits.

```bash
greenhouse check --all > /tmp/check.json
case $? in
  0) ;;                                           # all good
  2) jq '.results[] | select(.alerts != [] or .maintenance != [] or .needs_water != [])' /tmp/check.json | alerter ;;
  *) echo "check failed" >&2; exit 1 ;;
esac
```

## Top-level operation commands

Registered directly on the root app — no sub-app prefix.

```
greenhouse status   <cluster>                           Full cluster overview
greenhouse irrigate <cluster> [--dry-run] [--no-sync] [--temp C] [--force]
                                                        Smart irrigation pipeline
greenhouse check    [<cluster>] [--all]                 Irrigate or monitor + collect alerts
greenhouse monitor  <cluster>                           Moisture check (sensor-only clusters)
greenhouse sync     [--hours 24]                        Sync sensor data from Tuya Cloud
greenhouse learn    <cluster>                           Learning report (efficiency, patterns)
greenhouse history  <cluster> [--hours 24] [--limit 50] Readings + events timeline
greenhouse stats    <cluster> [--days 7] [--export f.csv]  Stats; --export writes CSV
greenhouse health                                       Server health + scheduler status
greenhouse stop-all [--yes/-y]                          Emergency kill switch (every irrigator)
greenhouse login    --username U --password P [--print-token]
                                                        Exchange credentials for a session JWT
greenhouse logout                                       Clear the cached session token
greenhouse whoami                                       Print the authenticated user
greenhouse tui      [--refresh 30] [--no-animation]     Interactive full-screen dashboard (humans only)
```

The root app also takes `--server <url>` (before the subcommand) plus Typer's `--install-completion` / `--show-completion`.

Notable flags:

- `irrigate --dry-run` analyzes without actuating. Use this when the user asks "what would it do?".
- `irrigate --no-sync` skips the freshness read of the cluster's sensors (no targeted Cloud sync of stale sensors before deciding). Faster, but only safe when sync ran recently.
- `irrigate --temp <°C>` overrides the temperature input (no sensor read, no weather call for temperature) — useful for what-if analysis.
- `irrigate --force` bypasses the quiet-hours gate (the decision is tagged `manual_override_quiet_hours`). It does **not** bypass the leak hold, cooldown or any other rule.
- `check` accepts either a cluster ID or `--all`; supplying neither errors with exit 1 (a cluster ID of `0` counts as "not given").
- `monitor` refreshes stale sensors from the Tuya Cloud and **stores** the readings it fetched (so a repeat call does not re-hit the Cloud); it never actuates. Unknown cluster → 404, exit 1.
- `stats --export <path>` writes CSV to disk and echoes the path; no JSON to stdout in that mode. `stats` on a cluster without an irrigator returns zero totals (it used to fail with HTTP 500).
- `tui --refresh <seconds>` (float, ≥ 0, default 30; `0` disables auto-refresh), `--no-animation` renders sprites as still frames.
- `stop-all` fires `POST /bulk/stop-all` — it stops *every* irrigator in the system. Interactive by default; pass `--yes`/`-y` to skip the confirmation prompt (required in scripts). Reach for this on a visible leak or any "stop everything now" request.

### Auth

`login` takes `--username` / `--password` (prompted for when omitted, password hidden), POSTs `/auth/login`, and stores the returned JWT at `~/.config/greenhouse/token` (mode 600; honours `$XDG_CONFIG_HOME`). Subsequent commands send it as a bearer token automatically. `--print-token` skips persistence and emits the JWT to stdout for piping into `$GREENHOUSE_API_TOKEN`. `$GREENHOUSE_API_TOKEN` overrides the on-disk token when set. `logout` deletes the cached token (best-effort server logout). Auth is only needed when the server enforces it; against an open server these are no-ops.

## Resource sub-apps

```
greenhouse cluster    add <name> [--location S] [--environment indoor|outdoor] | list | get <id>
                      | update <id> [--name S] [--location S] [--environment E] | delete <id> [--yes]
greenhouse plant      add <species> --cluster N [--category S] [--water-needs low|medium|high] [--light-needs …]
                          [--temp-min C] [--temp-max C] [--humidity-min %] [--humidity-max %] [--notes S]
                      | list [--cluster N] | sync [--plant-id N] [--cluster N] | move <id> --to-cluster N
                      | update <id> --cluster N [--species S] [same optional flags as add]
                      | delete <id> --cluster N [--yes]
greenhouse irrigator  add --cluster N --device-id S --name S --type T [--device-ip S] [--local-key S]
                          [--reservoir-l L] [--flow-rate-l-per-min R]
                      | list | show <cluster>
                      | update <cluster> [--name S] [--type T] [--device-ip S] [--local-key S] [--reservoir-l L] [--flow-rate-l-per-min R]
                      | delete <cluster> [--yes]
                      | start <irrigator_id> [--minutes M] | stop <irrigator_id>
                      | log-manual <irrigator_id> --minutes M [--notes S]
greenhouse sensor     add --cluster N --device-id S --name S --type T [--plant-id N] | list [--cluster N]
                      | update <id> --cluster N [--name S] [--type T] [--plant-id N] | delete <id> --cluster N [--yes]
greenhouse config     get --cluster N | effective --cluster N
                      | set --cluster N [--mode manual|schedule|smart] [--minutes M] [--interval H] [--auto-run/--no-auto-run]
                            [--daily-cap M] [--max-events N] [--quiet-start H] [--quiet-end H]
greenhouse config global  get | set [same flags as config set, no --cluster]
greenhouse scheduler  pause | resume | status
greenhouse alerts     list [--status S] [--cluster N] [--plant N] [--limit 1..500, default 100] | get <id> | ack <id>
                      | resolve <id> | sync [--cluster N]
greenhouse decisions  list --cluster N [--limit 1..200, default 50]
greenhouse prefs      get | set [--units metric|imperial] [--timezone IANA] [--theme light|dark|auto]
                            [--refresh-interval S] [--dry-run-global/--no-dry-run-global] [--default-cluster N]
greenhouse vacation   list | add --starts-at TS --ends-at TS [--email S] [--notes S]
                      | update <id> [--starts-at TS] [--ends-at TS] [--email S] [--notes S] | delete <id> [--yes]
greenhouse windows    list --cluster N | add --cluster N --start-hour H --end-hour H [--weekday-mask 1..127] [--label S]
                      | update <id> --cluster N [--start-hour H] [--end-hour H] [--weekday-mask M] [--label S]
                      | delete <id> --cluster N [--yes]
```

Patterns to know:

- **Add commands require explicit `--cluster` or positional args** — the CLI never prompts for resource fields (only `login` prompts for missing credentials, and destructive commands confirm unless `--yes`).
- **Child resources are addressed through their cluster.** `plant update/delete`, `sensor update/delete` and `windows update/delete` require `--cluster N` (the cluster the row belongs to); a row from another cluster is a 404, exit 1.
- **`update` / `delete`** exist on every resource sub-app (cluster, plant, irrigator, sensor) plus `vacation` and `windows`. Single-item reads: `cluster get <id>`, `alerts get <id>` and `irrigator show <cluster>`; everything else is read through its `list`. `update` is a partial patch — only the flags you pass are sent. `delete` prompts for confirmation unless you pass `--yes`/`-y`, and on clusters it cascades to all children (plants, sensors, the irrigator, history).
- **A cluster has at most one irrigator** (strict 0:1 — at most one device irrigates a cluster). So the irrigator CRUD sub-app is keyed by **cluster id**, not irrigator id: `irrigator add --cluster <id>` (errors with a non-zero exit if the cluster already has one), `irrigator show <cluster>` (the one irrigator, or an error if none), `irrigator update <cluster>` (partial patch), `irrigator delete <cluster> [--yes]`. The global `irrigator list` is unchanged. The device-action commands (`start` / `stop` / `log-manual`) stay keyed by **irrigator id**.
- **`config set`** patches a cluster's config; every flag is optional and **omitted flags are left unchanged** (no longer forces `--mode`). Fields: `--mode manual|schedule|smart`, `--minutes`, `--interval`, `--auto-run/--no-auto-run`, `--daily-cap`, `--max-events`, and quiet hours `--quiet-start` / `--quiet-end` (0–23, end exclusive; equal values disable quiet hours at the cluster level). Config is hierarchical — a field left unset inherits the global default, then the built-in constant.
- **`config effective`** shows the merged view: each field's resolved value and its `source` (`cluster` / `global` / `default`). Use it to answer "what config actually applies here?".
- **`config global get` / `config global set`** read and patch the singleton global defaults inherited by every cluster (same field set as `config set`, no `--cluster`). Setting a field to blank/clearing it falls through to the built-in constant.
- **`plant sync`** rewrites plant care fields from `plant_database.json`. Run it after editing the database or after `plant add` for a species that needs evidence-based defaults. Scope: `--plant-id` (one plant; wins if both are given), `--cluster` (that cluster's plants), or neither (everything). An unknown plant or cluster is an error (404 → non-zero exit), never a silent "0 synced".
- **`plant move`** takes `--to-cluster N`; health and learning history follow the plant, decision/event/alert logs stay with the source cluster.
- **`irrigator add` / `irrigator update`** accept two optional capacity flags: `--reservoir-l` (usable tank volume in liters) and `--flow-rate-l-per-min` (measured pump throughput in L/min), both floats. They're optional and additive — leaving them unset keeps today's behavior. When **both** are set on a cluster's irrigator, the decision engine can ration runs during an active vacation window so the water lasts (see references/LOGIC.md). `add` is keyed by `--cluster`; `update` is keyed by the cluster id. `--device-ip` / `--local-key` follow one rule on both commands: a flag you pass is sent exactly as typed (an empty string included) inside `config`; flags you omit are not sent. On `update` they overwrite the stored `config` blob, so pass both when switching a device to local control.
- **`irrigator stop`** records a `stop` event (`triggered_by="manual"`) — the same action automatic and emergency stops record. Older databases may still show `off` for manual stops recorded before this change.
- **`irrigator add --type`** is the device model key the server's device registry resolves to an adapter (`rainpoint.ik10pw` for the IK10PW irrigator; sensors use `tuya.tr301z`). The help text still names the legacy values `tuya_cloud` / `tuya_local`; the registry maps both to `rainpoint.ik10pw`, so either works today. An unknown irrigator model is refused at actuation time (no adapter → no water).
- **`irrigator start`** takes an optional duration (`--minutes`) and bypasses the engine (cooldown, quiet hours, leak hold) — it's the manual escape hatch. It still enforces the cluster's per-day caps (`--max-events` / `--daily-cap` set on the **cluster** config; inherited global caps are not checked) with a 409, and arms the dry-run pump watcher only when `--minutes` is given. Document this to the user when you reach for it.
- **`irrigator log-manual`** records that the user watered by hand; it doesn't actuate anything. It is stored as a `start` event (`triggered_by="manual"`), so it feeds the audit log and absorption learning **and starts the 6h cooldown** like a real run.
- **`scheduler pause` / `resume`** toggles the `check_all` cron job at runtime. The pause is **persisted** — it survives a server restart; other jobs (sensor sync, anomaly scan, health snapshot) keep running.
- **`prefs set --dry-run-global` is stored but not enforced.** The preference is saved and shown everywhere, but no actuation path reads it today (recorded bug B-1) — it does **not** stop automatic or manual irrigation. To stop automatic irrigation use `scheduler pause`; to stop a running pump use `irrigator stop` / `stop-all`.
- **`alerts`** drives the inbox: `list` (filter by `--status` / `--cluster` / `--plant`), `get`, `ack`, `resolve`, and `sync` (recompute; `--cluster` scopes to one cluster, default all).
- **`vacation add`** takes `--starts-at` / `--ends-at` as Unix-second timestamps; the window must start strictly before it ends (otherwise the server answers 400 `starts_at must be < ends_at` and the command exits non-zero — `vacation update` applies the same rule to the patched window). The web and TUI enter vacation times in the `timezone` preference. During an active window the engine rations irrigation against the cluster's irrigator's configured reservoir/flow capacity (see references/LOGIC.md); clusters with no capacity configured irrigate normally.
- **`windows add`** takes `--start-hour` / `--end-hour` (0–23, end exclusive) and `--weekday-mask` (Mon=1 … Sun=64, 127 = every day, default). An empty window list means **every hour is allowed** (subject to quiet hours) — there are no global window defaults. `windows update` / `delete` need `--cluster`.

## Common workflows in shell

**Cron a check every 3 hours, alert on non-zero**:

```cron
0 */3 * * * greenhouse check --all > /var/log/greenhouse-last.json 2>&1 || /opt/scripts/notify $?
```

**Stop everything in an emergency** (e.g. visible leak):

```bash
greenhouse stop-all --yes
```

`stop-all` hits `POST /bulk/stop-all` and stops every irrigator in the system. Pass `--yes`/`-y` in scripts to skip the interactive confirmation.

**Backfill a week of stats as CSV**:

```bash
greenhouse stats 1 --days 7 --export ./cluster-1-week.csv
```

**Watch the engine's verdict locally** (for debugging; `--dry-run` never actuates, but every evaluation is still written to `decision_logs`):

```bash
while :; do greenhouse irrigate 1 --dry-run | jq '{action, primary_code: .reasons[0].code}'; sleep 60; done
```

### Interactive TUI (`greenhouse tui`)

A full-screen Textual dashboard for **humans at a terminal** — never invoke it from an agent or a script (it takes over the terminal and produces no JSON). Same transport, auth and server-URL resolution as every other command, so `greenhouse --server http://pi.local:8000 tui` works from any machine that can reach the API; a 401 opens an in-app sign-in dialog that stores the token like `login`.

It covers the whole API surface (a test asserts every `IrrigationClient` capability has a TUI entry point). Most writes go through a form or a dialog (`esc` cancels without sending anything), but **not all of them** — the real behavior, pinned by the TUI actuation golden:

- **Yes/no confirmation:** check all (`c`, dashboard), stop all (`X`), cluster stop (`x`) and check (`c`), delete cluster (`D`), every row delete (`del`: plant, sensor, window, irrigator, vacation, ad-hoc scheduler job), scheduler **pause** (`p`).
- **Own dialog:** smart irrigate (`i`, dry-run preselected) and water now (`w`, optional minutes); create/edit/move/log-manual/config/preferences/global-defaults/vacation open a form.
- **No dialog — runs on the key press:** sensor sync (`S`), plant-DB sync (`P`), plant health snapshot (`H`), scheduler **resume** (`p` while paused), alert acknowledge / resolve / re-scan (`k` / `v` / `y`), log out (`O`), and the stats CSV export (`E`, writes a local file).

Press `?` in the TUI for the live key list.

| Key | Where | Does |
|---|---|---|
| `d` / `a` / `l` / `s` / `o` | everywhere | Dashboard / Alerts / Activity / System / Settings |
| `/` | everywhere | Search clusters, plants, sensors, irrigators, device IDs → jump to the cluster |
| `r`, `q`, `?` | everywhere | Refresh, quit, key help |
| `enter`, arrows | dashboard | Open the focused cluster card |
| `n`, `S`, `c`, `X` | dashboard | New cluster (form), sync sensors (no dialog), check all (confirm), **stop all** (confirm) |
| `i` | cluster | Smart irrigate dialog (dry-run is preselected; skip-sync / force options) |
| `w`, `x`, `c`, `L` | cluster | Water now (own dialog, manual start), stop (confirm), check (confirm), log a manual watering (form) |
| `n` / `u` / `del` | cluster → Plants, Sensors, Windows | Add / edit / delete the selected row |
| `n` / `u` / `del` | cluster → Overview | Attach / edit / detach the irrigator |
| `u` | cluster → Config | Edit the cluster's irrigation config (mode, duration, interval, auto-run, caps, quiet hours) |
| `M`, `P` | cluster → Plants | Move plant to another cluster (form), refresh care data from the plant DB (no dialog) |
| `m`, `[` / `]` | cluster → Charts | Cycle soil / temp / humidity / light / overlay, shorter / longer range (6h → 30d) |
| `m` | cluster → Plants | Toggle the plant chart: 90-day health score ↔ 72h soil moisture |
| `e`, `D`, `E` | cluster | Edit cluster, delete cluster (confirm), export 30-day stats CSV to the current dir |
| `k`, `v`, `f`, `y` | alerts | Acknowledge, resolve, filter by status, re-scan (no dialogs) |
| `f`, `n` | activity | Filter severity, load older |
| `p`, `S`, `P`, `H`, `del` | system | Pause (confirm) / resume (no dialog) scheduler, sync, plant-DB sync (all), plant health snapshot (no dialogs), remove an ad-hoc scheduler job (confirm; built-in jobs are refused) |
| `p`, `g` | settings | Edit preferences, edit global irrigation defaults |
| `n` / `u` / `del`, `O` | settings | Add / edit / delete vacation windows, log out |

Cluster tabs: Overview (plant garden, irrigator, decision trail, forecast), Charts (+ 7×24 heatmap), Plants, Sensors, Insights (care insights, needs-water monitor, 7-day stats, 14-day efficacy, learning report), Decisions, History, Windows, Config (effective config with inheritance source). System also lists device freshness and the data-quality report.

What it shows: animated pixel-art plant sprites per category (tropical, fern, succulent, cacti, fruit tree, generic) coloured by moisture mood (thriving / happy / thirsty / wilting / soaked) — they sway, sparkle, drop leaves when wilting and rain drops while watering; moisture gauges with the ideal band; 24h sparklines; plotext line charts (soil / temperature / humidity / light) with threshold lines and irrigation markers; the 7×24 irrigation heatmap; per-plant 90-day health timeline; decision trail with `TriggerCode`s; forecast; sensors, decisions, history, effective config and windows tables; alerts, activity and system health. Mood colours are display-only — the engine's thresholds stay server-side.

If the user asks you to "show" them the greenhouse interactively, suggest `greenhouse tui`; for anything you need to read yourself, keep using MCP tools or JSON CLI commands.

## Things that look like CLI commands but aren't

These exist as API endpoints / MCP tools but have **no dedicated CLI subcommand** (all of them are browsable in `greenhouse tui`, but not as JSON). If the user needs them in a shell, point them at `curl` against `/api/v1` or suggest using the MCP tools instead:

- Activity timeline (`/activity`)
- Forecast (`/clusters/{id}/forecast`)
- Plant health (`/plants/{id}/health`)
- Insights (`/clusters/{id}/insights`)
- Data quality report (`/quality/report`)
- Efficacy (`/clusters/{id}/efficacy`)
- Global search (`/search`)

This gap is intentional — the CLI prioritizes the high-frequency operational commands; everything else is one HTTP call away.

## Troubleshooting CLI failures

- **`Error: Cannot connect to server: …`** (exit 1) — server isn't running or the URL is wrong. Check `IRRIGATION_SERVER_URL` and `curl $url/api/v1/health`.
- **`Error: <detail>` on stderr, exit 1** — server returned 4xx/5xx; `<detail>` is the FastAPI error message verbatim. Read it; it's usually accurate (missing field, invalid cluster ID, etc.).
- **Hangs** — almost always a network issue, not the CLI. The client has no built-in retry and a 30 s request timeout; one slow request = one slow command. Only connection errors are turned into a clean `Error:` line — a timeout (or an empty 2xx body) currently ends in a Python traceback with exit 1, and a 422 validation error is printed as a Python repr of the server's error list.
- **JSON looks empty / `[]`** — the resource list is genuinely empty. Confirm with the equivalent GET on `/api/v1/...`.

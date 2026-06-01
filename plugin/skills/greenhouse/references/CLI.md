# CLI Reference

The `greenhouse` CLI is a thin Typer client that hits the server's `/api/v1` over HTTP. Same surface, different transport — every CLI command maps to one or more endpoints (and therefore to MCP tools).

## When to prefer the CLI over MCP

Default: **use MCP tools**. They return structured JSON with typed fields, no parsing, no quoting headaches. Reach for the CLI when:

1. **Authoring shell scripts / cron jobs** for the user. The CLI's stdout (JSON) + exit codes are designed to be piped: `greenhouse check --all | jq '.alerts[]'`.
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
| 1 | Error — server returned non-2xx, or required argument missing |
| 2 | Soft signal — alerts present, or cluster needs water (sensor-only) |

Exit code `2` is the interesting one: `greenhouse check --all` exits 2 when any cluster has open alerts. This is what makes it usable in cron — alerting frameworks key on non-zero exits.

```bash
greenhouse check --all > /tmp/check.json
case $? in
  0) ;;                                           # all good
  2) jq '.alerts[]' /tmp/check.json | alerter ;;  # alerts present
  *) echo "check failed" >&2; exit 1 ;;
esac
```

## Top-level operation commands

Registered directly on the root app — no sub-app prefix.

```
greenhouse status   <cluster>                           Full cluster overview
greenhouse irrigate <cluster> [--dry-run] [--no-sync] [--temp F] [--force]
                                                        Smart irrigation pipeline
greenhouse check    [<cluster>] [--all]                 Irrigate or monitor + collect alerts
greenhouse monitor  <cluster>                           Raw moisture check (sensor-only clusters)
greenhouse sync     [--hours 24]                        Sync sensor data from Tuya Cloud
greenhouse learn    <cluster>                           Learning report (efficiency, patterns)
greenhouse history  <cluster> [--hours 24] [--limit 50] Readings + events timeline
greenhouse stats    <cluster> [--days 7] [--export f.csv]  Stats; --export writes CSV
greenhouse health                                       Server health + scheduler status
greenhouse stop-all [--yes/-y]                          Emergency kill switch (every irrigator)
greenhouse login    [--print-token]                     Exchange credentials for a session JWT
greenhouse logout                                       Clear the cached session token
greenhouse whoami                                       Print the authenticated user
```

Notable flags:

- `irrigate --dry-run` analyzes without actuating. Use this when the user asks "what would it do?".
- `irrigate --no-sync` skips the Tuya Cloud fetch and decides on stored readings. Faster, but only safe when sync ran recently.
- `irrigate --temp <F>` overrides temperature input — useful for what-if analysis.
- `irrigate --force` bypasses the quiet-hours gate (the decision is tagged `manual_override_quiet_hours`). It does **not** bypass cooldown or any other rule.
- `check` accepts either a cluster ID or `--all`; supplying neither errors with exit 1.
- `stats --export <path>` writes CSV to disk and echoes the path; no JSON to stdout in that mode.
- `stop-all` fires `POST /bulk/stop-all` — it stops *every* irrigator in the system. Interactive by default; pass `--yes`/`-y` to skip the confirmation prompt (required in scripts). Reach for this on a visible leak or any "stop everything now" request.

### Auth

`login` prompts for `--username` / `--password` (password hidden), POSTs `/auth/login`, and stores the returned JWT at `~/.config/greenhouse/token` (mode 600; honours `$XDG_CONFIG_HOME`). Subsequent commands send it as a bearer token automatically. `--print-token` skips persistence and emits the JWT to stdout for piping into `$GREENHOUSE_API_TOKEN`. `$GREENHOUSE_API_TOKEN` overrides the on-disk token when set. `logout` deletes the cached token (best-effort server logout). Auth is only needed when the server enforces it; against an open server these are no-ops.

## Resource sub-apps

```
greenhouse cluster    add | list | get <id> | update <id> | delete <id> [--yes]
greenhouse plant      add | list | sync [--plant-id N] | move | update <id> | delete <id> [--yes]
greenhouse irrigator  add | list | start <id> | stop <id> | log-manual | update <id> | delete <id> [--yes]
greenhouse sensor     add | list | update <id> | delete <id> [--yes]
greenhouse config     get --cluster N | effective --cluster N | set --cluster N [--mode smart] [--minutes 2] [--interval 12] [--auto-run] [--quiet-start H] [--quiet-end H] [--daily-cap M] [--max-events N]
greenhouse config global  get | set [--mode …] [--quiet-start H] [--quiet-end H] [--auto-run] …
greenhouse scheduler  pause | resume | status
greenhouse alerts     list | get <id> | ack <id> | resolve <id> | sync [--cluster N]
greenhouse decisions  list --cluster N [--limit 50]
greenhouse prefs      get | set [--units …] [--timezone …] [--theme …] [--default-cluster N] …
greenhouse vacation   list | add --starts-at <ts> --ends-at <ts> | update <id> | delete <id> [--yes]
greenhouse windows    list --cluster N | add --cluster N --start-hour H --end-hour H | update <id> | delete <id> [--yes]
```

Patterns to know:

- **Add commands require explicit `--cluster` or positional args** — there's no interactive prompting. The CLI is non-interactive by design.
- **`update` / `delete`** exist on every resource sub-app (cluster, plant, irrigator, sensor) plus `vacation` and `windows`; only `cluster` also has a single-item `get`. `update` is a partial patch — only the flags you pass are sent. `delete` prompts for confirmation unless you pass `--yes`/`-y`, and on clusters it cascades to all children (plants, sensors, irrigators, history).
- **`config set`** patches a cluster's config; every flag is optional and **omitted flags are left unchanged** (no longer forces `--mode`). Fields: `--mode manual|schedule|smart`, `--minutes`, `--interval`, `--auto-run/--no-auto-run`, `--daily-cap`, `--max-events`, and quiet hours `--quiet-start` / `--quiet-end` (0–23, end exclusive; equal values disable quiet hours at the cluster level). Config is hierarchical — a field left unset inherits the global default, then the built-in constant.
- **`config effective`** shows the merged view: each field's resolved value and its `source` (`cluster` / `global` / `default`). Use it to answer "what config actually applies here?".
- **`config global get` / `config global set`** read and patch the singleton global defaults inherited by every cluster (same field set as `config set`, no `--cluster`). Setting a field to blank/clearing it falls through to the built-in constant.
- **`plant sync`** rewrites plant care fields from `plant_database.json`. Run it after editing the database or after `plant add` for a species that needs evidence-based defaults.
- **`plant move`** takes `--to-cluster N`; health and learning history follow the plant, decision/event/alert logs stay with the source cluster.
- **`irrigator start`** takes a duration (`--minutes`) and bypasses cooldown / engine checks — it's the manual escape hatch. Document this to the user when you reach for it.
- **`irrigator log-manual`** records that the user watered by hand; it doesn't actuate anything, just feeds the audit log and absorption learning.
- **`scheduler pause` / `resume`** toggles the `check_all` cron job at runtime. The pause is **persisted** — it survives a server restart; other jobs (sensor sync, anomaly scan, health snapshot) keep running.
- **`alerts`** drives the inbox: `list` (filter by `--status` / `--cluster` / `--plant`), `get`, `ack`, `resolve`, and `sync` (recompute; `--cluster` scopes to one cluster, default all).
- **`vacation add`** takes `--starts-at` / `--ends-at` as Unix-second timestamps; the engine holds during an active window.
- **`windows add`** takes `--start-hour` / `--end-hour` (0–23, end exclusive) and `--weekday-mask` (Mon=1 … Sun=64, 127 = every day, default). An empty window list means global defaults apply.

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

**Watch a check loop locally** (for debugging the engine):

```bash
while :; do greenhouse check 1 | jq '{action, primary_code: .reasons[0].code}'; sleep 60; done
```

## Things that look like CLI commands but aren't

These exist as API endpoints / MCP tools but have **no dedicated CLI subcommand**. If the user needs them in a shell, point them at `curl` against `/api/v1` or suggest using the MCP tools instead:

- Activity timeline (`/activity`)
- Forecast (`/clusters/{id}/forecast`)
- Plant health (`/plants/{id}/health`)
- Insights (`/clusters/{id}/insights`)
- Data quality report (`/quality/report`)
- Efficacy (`/clusters/{id}/efficacy`)
- Global search (`/search`)

This gap is intentional — the CLI prioritizes the high-frequency operational commands; everything else is one HTTP call away.

## Troubleshooting CLI failures

- **`Connection refused`** — server isn't running or the URL is wrong. Check `IRRIGATION_SERVER_URL` and `curl $url/api/v1/health`.
- **`Error: <detail>` on stderr, exit 1** — server returned 4xx/5xx; `<detail>` is the FastAPI error message verbatim. Read it; it's usually accurate (missing field, invalid cluster ID, etc.).
- **Hangs** — almost always a network issue, not the CLI. The client has no built-in retry; one slow request = one slow command.
- **JSON looks empty / `[]`** — the resource list is genuinely empty. Confirm with the equivalent GET on `/api/v1/...`.

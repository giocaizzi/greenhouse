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
greenhouse irrigate <cluster> [--dry-run] [--no-sync] [--temp F]
                                                        Smart irrigation pipeline
greenhouse check    [<cluster>] [--all]                 Irrigate or monitor + collect alerts
greenhouse monitor  <cluster>                           Raw moisture check (sensor-only clusters)
greenhouse sync     [--hours 24]                        Sync sensor data from Tuya Cloud
greenhouse learn    <cluster>                           Learning report (efficiency, patterns)
greenhouse history  <cluster> [--hours 24] [--limit 50] Readings + events timeline
greenhouse stats    <cluster> [--days 7] [--export f.csv]  Stats; --export writes CSV
greenhouse health                                       Server health + scheduler status
```

Notable flags:

- `irrigate --dry-run` analyzes without actuating. Use this when the user asks "what would it do?".
- `irrigate --no-sync` skips the Tuya Cloud fetch and decides on stored readings. Faster, but only safe when sync ran recently.
- `irrigate --temp <F>` overrides temperature input — useful for what-if analysis.
- `check` accepts either a cluster ID or `--all`; supplying neither errors with exit 1.
- `stats --export <path>` writes binary CSV to disk and echoes the path; no JSON to stdout in that mode.

## Resource sub-apps

```
greenhouse cluster    add | list
greenhouse plant      add | list | sync [--plant-id N] | move
greenhouse irrigator  add | list | start <id> | stop <id> | log-manual
greenhouse sensor     add | list
greenhouse config     get --cluster N | set --cluster N --mode smart --minutes 2 --interval 12
greenhouse scheduler  pause | resume | status
```

Patterns to know:

- **Add commands require explicit `--cluster` or positional args** — there's no interactive prompting. The CLI is non-interactive by design.
- **`plant sync`** rewrites plant care fields from `plant_database.json`. Run it after editing the database or after `plant add` for a species that needs evidence-based defaults.
- **`irrigator start`** takes a duration (`--minutes`) and bypasses cooldown / engine checks — it's the manual escape hatch. Document this to the user when you reach for it.
- **`irrigator log-manual`** records that the user watered by hand; it doesn't actuate anything, just feeds the audit log and absorption learning.
- **`scheduler pause` / `resume`** toggles the `check_all` cron job at runtime. The pause is in-memory — server restart clears it.

## Common workflows in shell

**Cron a check every 3 hours, alert on non-zero**:

```cron
0 */3 * * * greenhouse check --all > /var/log/greenhouse-last.json 2>&1 || /opt/scripts/notify $?
```

**Stop everything in an emergency** (e.g. visible leak):

```bash
greenhouse irrigator list --cluster 1 | jq -r '.[].id' | xargs -n1 greenhouse irrigator stop
# or just hit the bulk endpoint via the API directly:
curl -X POST $IRRIGATION_SERVER_URL/api/v1/bulk/stop-all
```

The bulk stop-all endpoint isn't exposed as a CLI subcommand (yet). For one-off shell use, `curl` against the API is fine.

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

- Alert inbox lifecycle (`/alerts`, `/alerts/{id}/acknowledge`, `/alerts/{id}/resolve`)
- Decision audit log (`/clusters/{id}/decisions`)
- Activity timeline (`/activity`)
- Forecast (`/clusters/{id}/forecast`)
- Plant health (`/plants/{id}/health`)
- Insights (`/clusters/{id}/insights`)
- Data quality report (`/quality/report`)
- Efficacy (`/clusters/{id}/efficacy`)
- Preferences (`/preferences`)
- Vacation windows (`/vacation`)
- Bulk stop-all (`/bulk/stop-all`)
- Global search (`/search`)

This gap is intentional — the CLI prioritizes the high-frequency operational commands; everything else is one HTTP call away.

## Troubleshooting CLI failures

- **`Connection refused`** — server isn't running or the URL is wrong. Check `IRRIGATION_SERVER_URL` and `curl $url/api/v1/health`.
- **`Error: <detail>` on stderr, exit 1** — server returned 4xx/5xx; `<detail>` is the FastAPI error message verbatim. Read it; it's usually accurate (missing field, invalid cluster ID, etc.).
- **Hangs** — almost always a network issue, not the CLI. The client has no built-in retry; one slow request = one slow command.
- **JSON looks empty / `[]`** — the resource list is genuinely empty. Confirm with the equivalent GET on `/api/v1/...`.

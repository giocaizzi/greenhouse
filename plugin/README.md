# greenhouse plugin

Claude Code plugin that connects an agent to a running [greenhouse](https://github.com/giocaizzi/greenhouse) server. Bundles:

- A skill (`/greenhouse:greenhouse`) teaching the agent the system's mental model, MCP tool catalogue, and invariants to respect.
- An MCP client that points at your server's `/mcp` endpoint over streamable HTTP.

## Install

From inside Claude Code:

```text
/plugin marketplace add giocaizzi/greenhouse
/plugin install greenhouse@greenhouse
```

Or against a local checkout:

```text
/plugin marketplace add /path/to/greenhouse
/plugin install greenhouse@greenhouse
```

## Configure

The plugin reads two environment variables. Set them in your shell (or in Claude Desktop's `claude_desktop_config.json` under the plugin entry) before starting your agent:

| Variable                  | Example                       | Notes                                                                 |
|---------------------------|-------------------------------|-----------------------------------------------------------------------|
| `GREENHOUSE_SERVER_URL`   | `http://192.168.1.50:8000`    | Base URL of the running greenhouse-server. No trailing slash.         |
| `GREENHOUSE_MCP_TOKEN`    | `<random hex>`                | Bearer token; must match the server's `GREENHOUSE_MCP_TOKEN`.         |

Generate a token on the server side with `openssl rand -hex 32` and keep it out of version control.

> The bearer token grants **physical actuation authority** — the server can start and stop real irrigation valves. Treat it like a root credential, rotate on suspected compromise, and never share across deployments.

## What you get

After install, the agent can:

- Read sensors and explain readings (`clusters/*/status`, `*/monitor`, `*/chart-data`).
- Trigger smart irrigation pipelines or manual actuation (`clusters/*/irrigate`, `irrigators/*/start`, `*/stop`, `*/log-manual`, `bulk/stop-all`).
- Review the decision audit log and explain the `TriggerCode` for any skip or run (`clusters/*/decisions`).
- Manage the alert inbox (`alerts`, `alerts/*/acknowledge`, `*/resolve`).
- Inspect plant health, forecasts, learning insights, efficacy scores.
- Tune per-cluster config, manage vacation windows, edit user preferences.

The full surface is the server's `/api/v1` — every endpoint becomes an MCP tool via `fastapi-mcp`.

## Troubleshooting

- **401 on every call** — bearer token mismatch. Check both sides set `GREENHOUSE_MCP_TOKEN` to the same value.
- **503** — server has `GREENHOUSE_MCP_TOKEN` unset, so `/mcp` is fail-closed. Set it server-side and restart.
- **Connection refused** — confirm the server is reachable from the machine running Claude (`curl $GREENHOUSE_SERVER_URL/api/v1/health`).
- **Tools don't appear** — run `/reload-plugins` after install or after changing env vars.

## License

MIT

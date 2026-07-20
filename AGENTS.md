# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) and other coding agents working in this repository. Also reachable as `AGENTS.md` (same file via symlink).

## What this is

**greenhouse** — smart plant irrigation. Reads Tuya sensors → decides per cluster → acts on Tuya irrigators (local protocol v3.5) → learns from each cycle → persists everything to local SQLite. **Tuya Cloud is the live source; SQLite is the permanent record.** Actuation is **local-first**: cycle bounding (Duration DP 102) and the dry-run safety read (DP 105) are local-only v3.5, and device discovery / `local_key` lookup never touch the Cloud in steady state (keys resolve from `irrigator.config` or the gateway's process cache). The on/off **switch pulse** does go via the Cloud API, because the Zigbee-gateway pump can't be reliably kept awake over LAN — this is the one deliberate Cloud actuation call, with a local keep-alive fallback.

Stack: Python 3.11+, uv workspaces, FastAPI + Pydantic v2 + SQLAlchemy v2, Typer CLI, APScheduler, tinytuya, Jinja2 + HTMX + Chart.js + Pico.css (no build step), `fastapi-mcp`.

## Architecture — four interfaces, one server

The **server** is the only thing that touches the DB and the devices. Four ways to talk to it:

| Interface | URL | Notes |
|-----------|-----|-------|
| REST API | `/api/v1` | Authoritative entry point. OpenAPI schema and interactive docs at `/docs` — that's the source of truth, not a hand-maintained list. |
| Web UI | `/` | HTMX + Jinja2, server-rendered. Calls the service layer **in-process** — shares code with the API, not a client of it. |
| CLI (`greenhouse`) | — | Thin `httpx` client against `/api/v1`. Does not import core, has no DB access; useless when the server is down. |
| MCP | `/mcp` | Every `/api/v1` endpoint auto-published via `fastapi-mcp`. Web routes excluded (they set `include_in_schema=False`). Bearer-token auth, fail-closed. |

Anything new the CLI or an MCP tool should be able to do must first exist as an API endpoint.

### MCP security model

The `require_mcp_token` dependency in `app.py` is wired into `FastApiMCP` via `AuthConfig(dependencies=[...])`:

- `GREENHOUSE_MCP_TOKEN` unset → `/mcp` returns 503.
- Set but missing/wrong `Authorization: Bearer …` → 401, checked against `settings.mcp_token` every request.
- Live `FastApiMCP` instance is stored on `app.state.mcp` for test introspection.

> ⚠️ **MCP grants physical actuation authority** (`/clusters/{id}/irrigate`, `/irrigators/{id}/start`, etc.). Treat the bearer token like a root credential: high entropy (`openssl rand -hex 32`), unique per deployment, never committed, rotated on suspected compromise.

## Packages — `core ← server ← cli`

uv workspace, three packages under `libs/`. Dependency direction is strict; the CLI never imports core, only the HTTP API.

- **`greenhouse-core`** — SQLAlchemy v2 models, Pydantic v2 schemas, repository, the unified **`DeviceGateway`** (one `tinytuya.Cloud` client + local-device factory; merges the former `TuyaCloud`/`TuyaTransport`) and profile-driven device adapters, decision engine (`logic/`), post-irrigation learning (`learning/`), curated `plant_database.json` (in `data/`), project-wide thresholds (`constants.py`).
- **`greenhouse-server`** — FastAPI app. JSON API under `/api/v1` (`routes/`), HTMX/Jinja2 web UI at `/` (`web/`), orchestration in `services/` (cluster, irrigation, sync, maintenance, charts, weather), background jobs via APScheduler (`scheduler.py`). `check_all` is a cron trigger driven by `IRRIGATION_CHECK_CRON_HOURS` (default `"*"` = top of every hour).
- **`greenhouse-cli`** — Typer CLI. Talks to API over HTTP via `IrrigationClient` (`client.py`). Top-level operation commands (`status`, `irrigate`, `sync`, …) plus per-resource sub-apps (`cluster`, `plant`, `irrigator`, `sensor`, `config`). Server URL resolves: `--server` → `$IRRIGATION_SERVER_URL` → `http://localhost:8000`. Output is JSON via `rich.print_json`; `ServerError` → non-zero exit through `commands/_helpers.py:call()`.

Distribution / import / entry-point: `greenhouse-{core,server,cli}` / `greenhouse_{core,server,cli}` / `greenhouse` (CLI), `greenhouse-server` (server). Tests mirror packages: `tests/test_*.py` (core), `tests/server/`, `tests/cli/`.

## Key invariants

1. **Protocol v3.5** for Rainpoint IK10PW (not 3.3).
2. **Driest plant drives the call** — `min_soil_moisture`, not the average.
3. **6h global cooldown** between irrigations (`MIN_COOLDOWN_HOURS` in `constants.py`). Check cadence (`IRRIGATION_CHECK_CRON_HOURS`) is independent of irrigation cadence — the scheduler decides how often to *observe*; the engine cooldown gates *actuation*.
4. **Learning is advisory** — never blocks decisions.
5. **All thresholds live in `constants.py`** — no magic numbers scattered through engines.
6. **`IrrigationDecision` is the canonical engine output** — typed Pydantic with `action`, `duration_minutes`, `interval_hours`, `confidence`, `reasons: list[Reason]`. Each `Reason` carries a stable `TriggerCode` enum so UI / MCP / audit log key on it without parsing free text. Persisted to `decision_logs` for **every** evaluation (acted-on or not): `DecisionLog` stores `primary_code`, `reason_text`, `actuated`, full `payload_json`.
7. **Every `/api/v1` route MUST declare** `response_model=`, a Pydantic request body, and a Google-style docstring (imperative summary, then `Args:` / `Returns:` / `Raises:` — skip framework-level params like `repo`). `fastapi-mcp` derives MCP tool schemas from OpenAPI and uses the docstring as the LLM-facing tool description; untyped/undocumented routes produce tools an LLM cannot reason about safely. Enforced by `tests/server/test_mcp.py` (binary endpoints like CSV export are exempted there).
8. **One Cloud writer, everyone else reads SQLite.** The sync job (`IRRIGATION_SYNC_INTERVAL_MINUTES`, default 180) is the *sole* thing that reads sensor data from the Tuya Cloud — it backfills `getdevicelog` (full-granularity, device-pushed) + one live read per sensor. The health monitor derives sensor health (battery / water-warning / offline-by-staleness) from the latest persisted `SensorReading` via `read_health(sensor, latest)` — **no live Cloud read**. The irrigation pipeline reads the persisted row through `SyncService.ensure_fresh_and_read`, which force-syncs **one** sensor only when its reading is older than `SENSOR_READING_STALE_SECONDS` (`constants.py`, default 4h). `get_live_reading` is single-call: v1.0 `getstatus` fires only when v2.0 shadow *fails*, never on an empty-but-successful read. All device I/O funnels through the one app-scoped `DeviceGateway` (one token); `open_local` resolves `local_key` from config/cache so local reads (health poll, pump watcher) cost zero Cloud calls.
9. **Plant DB timing fields flow JSON → plant_db → engine → decision in three layers.** Source of truth is `libs/greenhouse-core/greenhouse_core/data/plant_database.json`: per-species `preferred_water_hours_local` and `season_frequency_multiplier{,_outdoor}` live on `species[…]`; per-category defaults live in the top-level `_category_defaults` block. `plant_db.get_care_data` (in `plant_db.py`) merges these with precedence species > `_category_defaults[category]` > built-ins from `constants.py`, and surfaces `_category_defaults` verbatim so the engine can keep the category layer distinct. The engine consumes them in `logic/engine.py`: `_apply_window_rule` gates ONLY on per-cluster `IrrigationWindow` rows — **no windows = all hours allowed** (subject to quiet hours; issue #83), so `preferred_water_hours_local` is advisory plant data and never blocks irrigation; `_apply_seasonal_multiplier` picks `season_frequency_multiplier_outdoor` when `cluster.environment == "outdoor"` and feeds both species- and category-level overrides to `logic/timing.seasonal_multiplier` for per-season fallback.

## Development

```bash
make install    # uv sync
make serve      # uv run greenhouse-server (API + web UI on :8000)
make check      # pre-commit (lint/format/hygiene) + coverage gate — CI parity
make test       # uv run pytest
make lint       # ruff check libs/ tests/
make format     # ruff format libs/ tests/
make coverage   # pytest with coverage (fails under 60%)
```

### Tests

All tests use `tests/conftest.py` fixtures and the placeholders in `tests/fake_data.py` (fake device IDs like `fake_tuya_device_aabbccdd`, RFC 5737 IPs `192.0.2.x`, generic names). Server + web tests use FastAPI `TestClient` with in-memory SQLite. CLI tests use Typer `CliRunner` + `httpx.MockTransport`. Web tests assert on rendered HTML / fragment markers, not visual layout. No real network calls.

Run a single test: `uv run pytest tests/server/test_alerts.py::test_acknowledge -v`.

Add tests in the matching tree:

- core → `tests/test_*.py`
- JSON API → `tests/server/test_<resource>.py`
- Web pages / HX fragments / template filters → `tests/server/test_web_*.py`
- CLI → `tests/cli/test_cli.py`

### Adding a plant species

1. Research with at least 2 sources, then update `libs/greenhouse-core/greenhouse_core/data/plant_database.json`.
2. Apply with `greenhouse plant sync`.

## Bundled plugin — keep the skill docs in sync

The repo ships a Claude Code plugin under `plugin/` (marketplace entry in `.claude-plugin/marketplace.json`): a `greenhouse` skill plus an MCP client (`plugin/.mcp.json`) that points at a running server's `/mcp`. The MCP **tool schemas auto-derive from the OpenAPI spec** — no manual upkeep. The **hand-written skill docs do not**, and silently drift:

- `plugin/skills/greenhouse/SKILL.md` — capabilities overview + trigger guidance
- `plugin/skills/greenhouse/references/CLI.md` — CLI command reference
- `plugin/skills/greenhouse/references/LOGIC.md` — decision-engine behavior
- `plugin/skills/greenhouse/references/PLANT_DATABASE.md` — plant-DB schema/fields
- `plugin/.claude-plugin/plugin.json` — plugin description

**Invariant: any change to the surfaces below MUST update the matching plugin doc in the same PR** — treat it like updating a test, not optional follow-up:

| You change… | Update… |
|---|---|
| CLI commands / sub-apps / flags (`libs/greenhouse-cli`) | `references/CLI.md` |
| decision engine / `logic/` / `learning/` / `constants.py` | `references/LOGIC.md` |
| `data/plant_database.json` or `plant_db.py` fields | `references/PLANT_DATABASE.md` |
| new/changed `/api/v1` capabilities (hence MCP tools) | `SKILL.md` + `plugin.json` description |

## Privacy

**Never commit** device IDs, IP addresses, local keys, API credentials, database files, or personal configs. Live data lives in `data/*.db` (gitignored) and `.env` (outside repo). Test data uses the fakes in `tests/fake_data.py`.

Pre-commit sanity:

```bash
git grep -i "bf60\|192.168\|local_key\|api.*key" -- '*.py' '*.md' '*.json'
```

## Releases — project-specific facts only

Releases are automated by release-please (see the `/release-please` skill for the workflow). Commit conventions follow the `/conventional-commits` skill.

What's specific to this repo:

- **`.release-please-manifest.json` is canonical.** The four `pyproject.toml` files (root + `libs/greenhouse-{core,server,cli}/pyproject.toml`) mirror it via `extra-files` in `release-please-config.json` — release-please overwrites them on every release. Do not hand-edit versions; drift will confuse `uv`.
- **`CHANGELOG.md` is generated** — do not hand-edit.
- **`group-pull-request-title-pattern` is the load-bearing release-please key — it MUST include `${version}`.** Because `separate-pull-requests: false`, release-please routes the combined release PR through its **Merge plugin**, whose title comes from `group-pull-request-title-pattern`, **not** `pull-request-title-pattern` (that key is ignored in grouped mode — set both to the same value to avoid confusion). With it unset, the Merge plugin falls back to the hardcoded default `chore: release ${branch}` → `chore: release main` (no version); on merge, release-please finds the PR by the `autorelease: pending` label but parses the *version from the title*, gets nothing, and **silently skips tagging** — no `vX.Y.Z` tag, CD never fires. This is upstream [release-please#2712](https://github.com/googleapis/release-please/issues/2712). It silently broke v2.1.0 and v3.0.0 (both hand-recovered with `git tag … && gh release create … && gh pr edit --add-label "autorelease: tagged"`); #16 and #24 both mis-diagnosed it as `pull-request-title-pattern`. Do not remove either pattern.
- **The repo is COMPONENT-LESS — the root package `.` sets no `component` and no `package-name`. Do not add them.** greenhouse is one product, one version, one bare `vX.Y.Z` tag. If `package-name` is set, release-please derives a component (`greenhouse`) from it; with `include-component-in-tag: false` the tags carry no component, so the two disagree: discovery matches releases to the path *by component* and finds **zero** prior releases (`⚠ Expected 1 releases, only found 0` → `No latest release found … Set(0)`), and the tagging phase rejects the PR (`⚠ PR component: undefined does not match configured component: greenhouse` → tags 0) — release-please aborts, no tag, CD never fires. This (not the title pattern) is why v3.0.1 **and** v3.0.2 needed hand-recovery. Leaving the component empty everywhere makes both phases agree → the next release self-tags. Because there's no `package-name`, the root `pyproject.toml` is bumped via `extra-files` (all four pyprojects are listed). Do **not** "fix" this by *adding* components per dir (code/docs/tests) — that's the monorepo model and would produce prefixed tags (`code-vX.Y.Z`) that break `cd.yml`'s `v*` trigger. (A real monorepo like `interviewer` instead sets `include-component-in-tag: true` + explicit components so tags like `api-vX.Y.Z` carry the matching component — the opposite, also-consistent end of the spectrum.)
- The `vX.Y.Z` tag created by release-please triggers `.github/workflows/cd.yml`: Docker build → GHCR push, cosign signing, SBOM, Trivy scan.

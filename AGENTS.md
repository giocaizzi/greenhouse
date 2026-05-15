# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) and other coding agents working in this repository. Also reachable as `AGENTS.md` (same file via symlink).

## What this is

**greenhouse** — smart plant irrigation. Reads Tuya sensors → decides per cluster → acts on Tuya irrigators (local protocol v3.5) → learns from each cycle → persists everything to local SQLite. **Tuya Cloud is the live source; SQLite is the permanent record. Actuation is local-only, never via the Cloud.**

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

- **`greenhouse-core`** — SQLAlchemy v2 models, Pydantic v2 schemas, repository, Tuya Cloud + local device adapters, decision engine (`logic/`), post-irrigation learning (`learning/`), curated `plant_database.json` (in `data/`), project-wide thresholds (`constants.py`).
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
8. **Plant DB timing fields flow JSON → plant_db → engine → decision in three layers.** Source of truth is `libs/greenhouse-core/greenhouse_core/data/plant_database.json`: per-species `preferred_water_hours_local` and `season_frequency_multiplier{,_outdoor}` live on `species[…]`; per-category defaults live in the top-level `_category_defaults` block. `plant_db.get_care_data` (in `plant_db.py`) merges these with precedence species > `_category_defaults[category]` > built-ins from `constants.py`, and surfaces `_category_defaults` verbatim so the engine can keep the category layer distinct. The engine consumes them in `logic/engine.py`: `_apply_window_rule` falls back to `_resolve_preferred_hours()` when a cluster has no `IrrigationWindow` rows; `_apply_seasonal_multiplier` picks `season_frequency_multiplier_outdoor` when `cluster.environment == "outdoor"` and feeds both species- and category-level overrides to `logic/timing.seasonal_multiplier` for per-season fallback.

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
- The `vX.Y.Z` tag created by release-please triggers `.github/workflows/cd.yml`: Docker build → GHCR push, cosign signing, SBOM, Trivy scan.

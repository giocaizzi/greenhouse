# Drift track hand-off (branch `refactor/drift-track`, base `3d1f43d`)

Behavior-change commits, one pair each, labelled `fix(drift): …` / `fix(consistency): …`. Each commit body lists
before/after, the canonical choice, the user-visible change and the exact tests/goldens touched (each golden diff was
inspected: only the intended lines move). Not pushed, not merged.

## Commits

| Pair | Commit | Canonical behavior | Shared home |
|---|---|---|---|
| D14 | `45481f7` | delete dead `stats.export_csv` (+ `_csv_event_row`, `_write_event_rows`); route CSV stays | `services/cluster.cluster_events_csv` (existing) |
| D9 | `8faca8b` | CLI `irrigator add`/`update` build `config` with one `is not None` rule | `commands/irrigators._device_config` |
| D13 | `5e0d9b0` | TUI config tab + settings global-defaults list fields in repository order (no re-sort) | server payload order |
| D8 | `f849e79` (superseded by `e2ee93f`) | TUI vacation datetimes UTC → now the `timezone` preference everywhere | `tui/screens/forms.py`, `formatting.zone/clock(tz=)`, `web/routes/vacation._preference_zone` |
| D10 | `a77a7aa` | every soil-target reader uses `moisture_target_range` (`parse_moisture_target`) | `logic/plant_needs` (existing) |
| D7 | `eaeb534` (refined by `831183d`) | plant dashboard "irrigated …" uses the shared formatter ("never" / real age) | `web/filters.relative_age` |
| D4 | `e8af945` | web check badge uses the API `has_alerts` rule (alerts ∨ maintenance ∨ needs_water), check-all and single | `services/irrigation.check_has_alerts` |
| D12 | `4034fb6` | plant-DB sync with unknown `cluster_id` → 404 "Cluster not found" (API + web) | `services/cluster.ClusterNotFoundError` |
| D3 | `fe9bf91` | one window validator, API wording, for API and web | `services/windows.validate_window` (new) |
| D5 | `76bc13e` | `starts_at < ends_at` on every vacation write (API POST now 400); API wording | `services/vacation.validate_vacation_range` |
| D1 | `c54e86a` | one irrigator registration path; web duplicate device id → form + 409 (was 500) | `services/inventory.create_irrigator` (new module) |
| D2 | `abbe4a9` | plant-in-cluster rule on sensor create **and update** (API + web); web duplicate id → 409 | `services/inventory.{ensure_plant_in_cluster,create_sensor}` |
| D6 | `63c93a5` | `deps.require_*` lookups, one 404 wording per entity (API's) | `deps.require_{cluster,cluster_irrigator,irrigator,plant,plant_in_cluster,sensor,sensor_in_cluster,window_in_cluster,vacation_window,alert}` |
| D15 | `046a39a` | API + web monitor run the freshness sync and commit it | `IrrigationService.monitor_cluster` + route commit |
| D17 | `cf8dafe` | efficacy `days` bounded 1..365 on the API too | `services/efficacy.EFFICACY_{DEFAULT,MAX}_DAYS` |
| D16 | `4c914e8` | one lenient device-config parser (malformed / non-object → `{}`) for API schemas + web | `greenhouse_core.models.parse_device_config` |
| OD4 | `8053a89` | manual stop records action `"stop"` (new rows only) | `services/manual_control.manual_stop` |

### Follow-up after review (owner decisions 2026-10-03; review APPROVED, `scratchpad/review-drift.md`)

| Item | Commit | Change |
|---|---|---|
| D8 owner decision | `e2ee93f` | vacation times parsed **and** displayed in the `timezone` preference: web date → midnight in the display tz (`get_display_timezone()`), TUI `Field.tz` + `vacation_fields(tz)` / `vacation_rows(tz)` / `formatting.clock(tz=)`; "(UTC)" labels removed; API/CLI take Unix seconds (nothing to parse) |
| D7 owner decision | `831183d` | one formatter `web/filters.relative_age(ts, *, missing, stale_after)`; `age_seconds` filter = freshness defaults (unchanged); dashboard shows "never" / real age ("12d ago") |
| D2 owner decision | — | keep the 404 on a cross-cluster plant (create and update); no change |
| m2 | `4cc4848` | `refactor(services)`: dead `monitor_cluster(no_sync=…)` removed (service method only, not a route param) |
| m3 | `77a5bb6` | web monitor 404s an unknown cluster like the API (new golden `monitor__404`) |
| m4 | `14e3460` | `refactor(web)`: empty `if TYPE_CHECKING: pass` blocks removed |
| m5 | `acc9f91` | web window parse errors drop their trailing "." (2 goldens) |
| m6 | `d74a8e7` | plugin docs: LOGIC.md (D10, D15), CLI.md (D5, D9, D12, OD4, D8), SKILL.md (API/MCP refusals, monitor, stop/off) |
| m7 | `e11bccd` | D15–D17 + owner decisions added to `refactor/45-drift-track.md` |
| m1, m8 | this file | notes below |

**m1 — D16 also changes `"null"`:** a stored config of `"null"` used to come back as `config: null` (200); it now
reads as `{}` like every other non-object value. Practically unreachable (writers always store `json.dumps(dict)`).

**m8 — OD4 side effects:** existing databases keep their historical `off` rows, so history and `stats`
`events_by_type` show both `off` (old manual stops) and `stop` buckets until those rows age out; the TUI now colours
manual stops with `ACTION_STYLES["stop"]` (the old `off` had no style).

**D11 skipped** (quiet-hours "active now" shared helper) — it touches `logic/engine.py`/`logic/timing.py`, owned by
WP8. Plan task I2 / target-architecture §7.2 (`logic.timing.active_quiet_window`) still applies after WP8 merges.

**D16 follow-up for WP8/integrator:** `devices/gateway._coerce_config` (WP8 file, not edited) has exactly the semantics
of `greenhouse_core.models.parse_device_config`; replace it with a call (behavior-preserving `refactor(devices)`).

## Ambiguities resolved by the documented default (reviewer: look here hardest)
- D7 / D8: resolved by the owner after review (see the follow-up table): "never" / real age; `timezone` preference.
- D13: the real drift was the display tables (`render.config_rows` / `global_config_rows` sorted alphabetically);
  `resources.config_fields` already matched repository order.
- D2: applied to API `PUT` sensor too (B-7 assumed the API already rejected cross-cluster plants on update; it did not).
- D3/D5: one wording = the API's ("…must be 0..23", "weekday_mask must be 1..127 (Mon=1, Sun=64)",
  "starts_at must be < ends_at") — the web loses "Select at least one weekday." / "ends_at must be after starts_at.".
- D15 changes Cloud-call frequency: API repeated calls make fewer Cloud calls (rows now persisted); the web monitor
  now makes up to one targeted sync per stale sensor (was zero).

## Contract fingerprints
`PHASE0_OPENAPI_SHA256` / `PHASE0_MCP_TOOLS_SHA256` (tests/server/test_contract_{openapi,mcp}.py) were re-recorded
in each commit that regenerated `openapi.json` / `mcp_tools.json` (D12, D5, D2, D15, D17, OD4); their comment now says
they move only with reviewed behavior-change commits. Final: openapi `ee96dba2…`, mcp `2a28d5d3…`.

## API / MCP / CLI-visible changes (for the plugin + CLAUDE.md doc sync)
- `create_vacation_window` / `greenhouse vacation add`: 400 `starts_at must be < ends_at` for reversed/empty windows.
- `sync_plants` / `greenhouse plant sync --cluster N`: unknown cluster → 404 `Cluster not found` (CLI exit 1).
- `update_sensor`: `plant_id` of another cluster → 404 `Plant N not found in cluster`.
- `monitor` / `greenhouse monitor`: refreshes stale sensors from the Cloud **and stores them**; docstring no longer
  says read-only (`SKILL.md`, `LOGIC.md` if it describes monitor as read-only).
- `cluster_efficacy`: `days` max 365 (422 above).
- `stop_irrigator` / `greenhouse irrigator stop`: event action `stop` (was `off`) in history/stats/CSV.
- Irrigator/sensor responses: malformed or non-object stored `config` → `{}` (was HTTP 500).
- `greenhouse irrigator add --device-ip ""` now sends `config: {"device_ip": ""}` (`references/CLI.md`).
- Vacation wall times (web date fields, TUI) are in the `timezone` preference; API/MCP/CLI stay Unix seconds.
- Plugin docs are already synced on this branch (`d74a8e7`); CLAUDE.md needs no change for these pairs.
- `references/LOGIC.md`: one soil-target parser (first two `-` parts, default 45–65) for monitor, check and learning
  issues (D10).
- Web/TUI only (no plugin impact): D1, D3, D4, D6, D7, D8, D13 wording/badge/order changes above.
- `plugin.json` description: no capability added or removed.

## REFACTOR_NOTES.md updates (integrator)
Proposed text for a new "Drift track (behavior changes, labelled)" section:
- B-7 fixed (D1, D2): web irrigator/sensor create handle duplicate device ids (409) and the plant-in-cluster rule;
  the rule now also guards sensor updates in API and web.
- B-8 fixed (D5): every vacation write path validates `starts_at < ends_at`.
- B-13 fixed (D4): web check badge uses the API `has_alerts` rule.
- B-16 fixed (D12): plant-DB sync of an unknown cluster is a 404.
- B-N1 fixed (D15): API monitor persists its freshness sync; web monitor syncs like the API.
- CLI list: drop "empty `--device-ip` on `irrigator add` is dropped while empty `--local-key` on `irrigator update` is
  sent" (D9: both send what was typed).
- Engine list: `parse_moisture_target` is now the only soil-target parser (D10); its no-validation note still holds.
- Dead code: `stats.export_csv` removed (D14); `print_stats_report` still has no production caller.
- OD4 applied: manual stops record `stop`; historical `off` rows remain in existing databases.
- Window/vacation/404 error strings unified on the API wording (D3, D5, D6).

## New observations (not fixed; for the consistency sweep)
- `age_seconds` filter misuse (still open; the filter itself is unchanged by the D7 follow-up): `_cluster_status.html`, `_sensor_row.html`, `clusters/detail.html` and `health.html`
  pipe an **age in seconds** (`reading_age_seconds` / `dev.age_seconds`) into `age_seconds`, which expects a Unix
  timestamp (`time.time() - ts`) — an age like 300 renders as "stale". (`_alert_row.html` and
  `_plant_latest_card.html` pass timestamps correctly.)
- `services/charts._parse_range` is a 4th soil-target parser ("a-b-c" → no band, source `default`); left out of D10
  because the chart needs a "parsed or not" signal for its `source` label.
- `web/routes/clusters.py::cluster_detail` is over the size DoD (pre-existing at `3d1f43d`; not touched).

## Gate after the follow-up (PYTHONHASHSEED=0, `-n 2`, flock, each hold ≤ 5 min)
FULL in the same 7 chunks: 819 + 1006 + 389 + 242 + 473 + 92 + 32 = **3053 passed, 0 failed** (every per-commit
subset and the affected goldens are inside this union). ruff check/format clean, `lint-imports` 10 kept,
`make typecheck` 81 files OK, `git status --porcelain tests/golden` empty.

## Gate (end of track, PYTHONHASHSEED=0, `-n 2`, flock, each hold ≤ 5 min)
FULL run split into 7 directory chunks covering every test file (`tests/test_*.py tests/devices/`, `tests/server/*`
in three slices, CLI non-TUI, `test_tui.py`, TUI contract screens/actuation/runtime):
819 + 1006 + 389 + 241 + 473 + 92 + 32 = **3052 passed, 0 failed**. This union includes every per-pair subset and
`$CORE $API $WEB $CLI $TUI $CHECK`. `ruff check` / `ruff format --check` (libs, tests) clean; `lint-imports` 10 kept;
`make typecheck` 81 files OK (`refactor/mypy-strict.txt` + `services/windows.py`, `services/inventory.py`);
`git status --porcelain tests/golden` empty; sizecheck on touched modules: only the pre-existing `cluster_detail`.
Seed-12345 and `TZ=America/New_York` runs were not part of this brief (final gate).

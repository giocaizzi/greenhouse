# Drift track — synchronize divergent copies (owner directive 2026-10-02: fold into this refactor)

These commits **change behavior on purpose**, unlike the rest of the branch. Rules:
- Label: `fix(drift): <pair> — <canonical behavior>`; never mixed with refactor commits.
- One pair per commit: one shared implementation (service/deps helper), both callers use it.
- Pin first: the current divergent behavior is already pinned (`*_current_behavior` tests / goldens); the commit
  updates **only** those tests/goldens, with a reviewed diff showing exactly the intended change.
- Default canonical rule: the stricter / validated / API side wins (MCP + CLI depend on the API).
- Runs after wave C + WP6 merge (no overlap with active work packages); reviewed like high-risk tasks.

| # | Pair | Today | Canonical (proposed) | User-visible change |
|---|---|---|---|---|
| D1 | Irrigator create (API vs web) | web lacks `IntegrityError` → 500 on duplicate Tuya id (B-7) | shared `create_irrigator`; web returns the API's conflict as an error toast/page | web: 500 → handled error |
| D2 | Sensor create/update (API vs web) | web lacks plant-in-cluster check + `IntegrityError` (B-7) | shared validation in service | web rejects cross-cluster plant / duplicate id |
| D3 | Window validation messages | `…0..23` (API) vs `…0..23.` (web) | one `validate_window` → one wording (API's) | web message punctuation |
| D4 | `check_all` `has_alerts` | API: alerts ∨ maintenance ∨ needs_water; web: alerts only (B-13) | API rule | web banner shows in more cases |
| D5 | Vacation `starts < ends` | API POST unvalidated; PUT + web validate (B-8) | validate on every write path | API/MCP/CLI POST rejects reversed windows |
| D6 | 404 lookups | `"Cluster not found"` inline 20×, punctuation differs per layer | `deps.require_*` helpers, one wording per entity | error-string punctuation (web/API) |
| D7 | Relative time | `filters.age_seconds` vs `plant_dashboard._relative_time` give different outputs | one formatter | plant dashboard time strings |
| D8 | Vacation datetime semantics | TUI naive local vs web UTC midnight | UTC midnight (server convention) | TUI-created vacations |
| D9 | CLI irrigator device-config | add: truthy check; update: `is not None` | one rule (`is not None`, matching update) | `irrigator add --device-ip ""` now sent |
| D10 | Moisture-target parsing | `monitor_cluster` and `learning/issues.py` parse malformed strings differently | `parse_moisture_target` everywhere | malformed targets handled consistently |
| D11 | Quiet-hours "active now" | web re-implements the engine check (identical logic) | shared helper (plan task I2) | none |
| D12 | Plant-DB sync unknown cluster | silently `synced=0` in API and web (B-16) | 404 in both | API/web 404 instead of 0 |
| D13 | Config field order | repository patch list vs TUI `resources.config_fields` order differ | repository order | TUI form field order |
| D14 | CSV export | route CSV vs dead `stats.export_csv` (different time format) | delete dead copy (dead-code rule) | none |
| D15 | Monitor (API vs web) — audit C-TX-6 / B-N1 | API runs the freshness sync but never commits (rows discarded, Cloud re-hit each call); web skips the sync | one path that syncs stale sensors and commits, used by both; web also 404s an unknown cluster | API stores synced rows (fewer Cloud calls); web refreshes stale sensors |
| D16 | Device `config` parsing — audit C-DUP-2 | schemas raise on bad/non-object JSON (500); web `{}` on bad JSON but passes non-objects; gateway `{}` for both | one lenient `models.parse_device_config` (dict / JSON object, else `{}`); gateway swap left to WP8 | API returns `config: {}` instead of 500 (and for a stored `"null"`) |
| D17 | Efficacy `days` bound — audit C-MISC-1 | API `ge=1`; web `ge=1, le=365` | `le=365` on both (shared constants) | API/MCP 422 above 365 |

Owner decisions after review (2026-10-03): D8 → vacation times are parsed and displayed in the `timezone`
preference on every interface (supersedes "UTC midnight"); D7 → the shared formatter shows "never" and the real age on
the plant dashboard (no "stale"); D2 → the 404 on a cross-cluster plant stays (create and update). OD4 (manual stop
records `stop`) landed as `fix(consistency)`. D11 waits for WP8. Commits and evidence: `refactor/wp-handoff/DRIFT.md`.

## Docs / plugin sync (no behavior change; folded in at the end)
- `CLAUDE.md` module paths and descriptions after the refactor; the ConfirmScreen claim corrected to the real actuation
  table; `AGENTS.md` symlink untouched.
- `plugin/skills/greenhouse/{SKILL.md,references/LOGIC.md,CLI.md,PLANT_DATABASE.md}`, `plugin/.claude-plugin/plugin.json`
  checked against the final code (CLI flags, engine behavior incl. D-changes above, MCP capabilities).
- Each D-change that alters API/MCP/CLI-visible behavior gets its doc line in the same commit.

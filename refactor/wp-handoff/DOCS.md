# Docs / plugin sync hand-off (branch `refactor/docs`, base `3491e23`)

Documentation only: no code, no tests, no goldens, `CHANGELOG.md` untouched. `CLAUDE.md` stays a symlink to
`AGENTS.md` (`git ls-files -s CLAUDE.md` → mode `120000`). Not pushed, not merged.

## Commits

| Commit | Area | What changed |
|---|---|---|
| `ca0c277` | `docs(plugin)` CLI.md | Every flag from `tests/golden/cli/help/*.txt` (required `--cluster` on child update/delete, irrigator add requirements, `log-manual --minutes`, `--limit` ranges, prefs/vacation/windows extras, root `--server`). Exit 2 only for `check --all` (`has_alerts` = alerts ∨ maintenance ∨ needs_water) and `monitor`; jq paths `.results[]`; single-item reads (C-STALE-6); empty window list = all hours (C-STALE-4); real TUI actuation table; `dry_run_global` not enforced (B-1); caps only on manual start, cluster row only (B-4); `log-manual` starts the cooldown; monitor stores its refresh (D15); stats zero totals (D19); client timeout/traceback behavior. |
| `7dcf052` | `docs(plugin)` LOGIC.md | Pipeline as `services/irrigation.py` runs it (auto_run gate, temperature resolution, fallback before stress, no inline anomaly scan, attempted / actuated / notify follow-ups, per-cluster commit). Trust layer = 15-min `sensor_anomaly` job (`sensor_stale` / `sensor_drift`) + leak hold + device-health gate. Device-health block not written to `decision_logs` (B-6). `vacation_active` / rationing only on decisions reaching the final step. `DEFAULT_QUIET_*` removed from the constants list (seeded by migration). New constants named. New "Known quirks" section (critical stress keys on the average, inclusive edges, UTC-month light factor, `force=true` start recorded as `auto`, …). Engine/devices layout marked unchanged (WP8). |
| `ea1efb1` | `docs(plugin)` PLANT_DATABASE.md | `preferred_water_hours_local` advisory (C-STALE-5); `soil_moisture_target` via `parse_moisture_target` (D10); `water_frequency_days`, `light_needs` informational (no engine reader); `ideal_light_lux_min` drives low-light; `IRRIGATION_PLANT_DB_PATH`. |
| `894599b` | `docs(plugin)` SKILL.md | Actuation local-first (Cloud switch pulse), not "local-only"; invariant 5 = device-health gate (B-6) + advisory anomaly scan; cooldown counts every `start`; weather > 2 mm non-indoor; vacation tag/rationing scope; pitfalls: D19 zeros, `aborted` / `attempted`, D16 `{}`, B-1, B-4, no decision row for `auto_run=false` / sensor-only; D8 wall times. `plugin.json` unchanged (no capability added or removed). |
| `f418fb5` | `docs(agents)` AGENTS.md (= CLAUDE.md) | Module map per package incl. `services/_session.py`, `services/inventory.py`, `services/windows.py`, `greenhouse_cli/constants.py`, `tui/render.py`, `deps.require_*`, `repo.commit()/rollback()/flush()`; all `services/` modules and CLI sub-apps (C-STALE-7); import-linter contracts. ConfirmScreen claim → real actuation table. Invariants 3 (OD3 interval var), 6 (B-6, C-STALE-9), 8 (D15 monitor commit), new 12 (OD4 `stop`). "Key facts" (D8, B-4, B-1, Alembic-only init: OD3 pre-Alembic repair + pump_dry_run migration removed). New Conventions section (OD2, job_session, require_*, shared rules, clock C-CLK-1, logging C-LOG-4, naming C-NAME-3, size, docstrings, dead code). Development: `make check` = pre-commit + lint-imports + typecheck + sizecheck + coverage (C-STALE-8), xdist, test layout, golden policy, `tests/golden.py` kit. Release-please facts untouched. |
| `ec21680` | `docs(readme)` README.md + .env.example | Pipeline order, windows optional (was "waters in the morning by default"), actuation transport, TUI confirm caveat, env vars (`IRRIGATION_CHECK_INTERVAL_HOURS` = no longer read; + `IRRIGATION_ENABLE_SCHEDULER`, `IRRIGATION_PLANT_DB_PATH`, `IRRIGATION_WEATHER_LAT/LON`, `GREENHOUSE_NTFY_*`), dev targets. |

## Verification

- CLI flags/commands: compared against `tests/golden/cli/help/*.txt` (all 74 files) and `commands/*.py`.
- API/MCP paths: against `tests/golden/contracts/openapi.json`; refusals against the routes and `refactor/wp-handoff/DRIFT.md` / `CONS-W2.md`.
- Env vars: against `greenhouse_server/config.py` and the direct env reads (`rg os.environ libs`).
- Make targets: against `Makefile`; import contracts against `[tool.importlinter]` in `pyproject.toml`.
- Every backticked path / identifier in AGENTS.md checked to exist (`libs/`, `tests/`, `Makefile`, `pyproject.toml`).
- `uvx pre-commit run --files <changed docs>`: all hooks pass. No test reads these docs (`rg 'SKILL.md|CLI.md|…' tests libs`).

## Re-check after WP8 merges (engine / devices)

1. AGENTS.md "Packages → greenhouse-core": the `logic/` and `devices/` rows and the sentence "`logic/engine.py` and `devices/`
   keep their pre-refactor module layout"; LOGIC.md intro sentence with the same claim.
2. Names quoted in AGENTS.md invariants 8/9/11 and LOGIC.md: `IrrigationLogic.decide_for_cluster`, `_enforce_leak_hold`,
   `_enforce_cooldown`, `_apply_window_rule`, `_apply_seasonal_multiplier`, `_apply_vacation_budget`,
   `logic/timing.seasonal_multiplier`, `DeviceGateway.get_live_reading` / `open_local`, `read_health(sensor, latest)`.
3. Weather skip literals (`hours=6`, `2.0`) → `WEATHER_FORECAST_HOURS` / `WEATHER_SKIP_PRECIP_MM` (LOGIC.md already names the
   constants); `CONFIDENCE_BASELINE` / `is_within_preferred_hours` / `invalidate_key` removals (not documented — nothing to drop).
4. D11 shared quiet-hours helper (`logic.timing`), D16b `devices/gateway._coerce_config` → `parse_device_config`.
5. OD3 / C-LEG-2 legacy device-type aliases (`tuya_cloud` / `tuya_local`, sensor `soil_moisture` / …): if removed, update
   CLI.md "`irrigator add --type`" (it says the legacy values still resolve), README's `sensor add --type soil_moisture`
   example, and the Typer help (code).
6. Any engine behavior change WP8 labels (none expected) → LOGIC.md "Known quirks".

## Code-side doc drift found (out of scope here — code / doc-contract commits)

- Typer help `windows list`: "Empty list = global defaults apply." (C-STALE-4, phase C1 `docs(cli)` not landed). CLI.md
  documents the real rule and mentions the stale help text — drop that clause when C1 lands.
- Typer help `irrigator add/update --type`: "tuya_cloud or tuya_local" (see item 5).
- Route docstring `routes/irrigators.py::start_irrigator`: "Manually start an irrigator over the Tuya local protocol" —
  the IK10PW switch pulse goes via the Cloud API (MCP tool description; doc-contract commit).
- `tui/screens/modals.py` `ConfirmScreen` docstring "every actuating action goes through this"; the comment at
  `tests/cli/test_contract_tui_actuation.py:8` refers to CLAUDE.md's old claim (now corrected).
- `tests/golden.py` docstring cites `refactor/BRIEF.md` rule 8 — dangling once OD5 drops `refactor/`.
- OD5: `make typecheck` reads `refactor/mypy-strict.txt`, `make sizecheck` runs `refactor/scripts/sizecheck.py` with
  `refactor/size-exceptions.txt`. AGENTS.md "Development" names these paths explicitly and says they move — update that
  paragraph in the OD5 commit.
- `plugin/README.md` checked: still accurate, unchanged.

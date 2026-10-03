# LINT hand-off — lint ratchet (refactor/40-lint-ratchet.md)

Worktree `/home/user/gh-lint`, branch `refactor/lint`, based on `3491e23` (integration after CONS-W3). Not pushed,
not merged, not rebased. WP8 files (`logic/engine.py`, `logic/timing.py`, `devices/**`, `sync.py`) were **not edited**:
every newly enabled rule that fires there is a per-file ignore listed below for the integrator to ratchet after WP8.

Every pytest run: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …`, ≤ 5-min holds,
logs tee'd under the scratchpad. After each family: `ruff check libs/ tests/ refactor/`, `ruff format --check`,
`make typecheck` (150 files), `uv run lint-imports` (10 kept), the golden-bearing subset named in each commit body, and
`refactor/gate1/mutate.py --check` (INVALID set compared to the base: 80 stale snippets pre-exist at `3491e23`; the
lint commits added none — the snippets they touched were updated in the same or the next commit).

## Commits

| # | Commit | Family | Notes |
|---|---|---|---|
| 0a | `297797c` style(refactor) | — | `ruff format` of `mutate.py` (was the only unformatted file in `ruff format --check libs/ tests/ refactor/`) |
| 0b | `f5ab8e2` build(lint) | complexity | `max-branches` 12 → 8, `max-statements` 50 → **23**, `PLR0916`, `SIM117`, `preview` + `explicit-preview-rules` with `PLR1702` (max-nested-blocks 3), `PLR0914`, `PLR0904` |
| 1 | `30c06f8` refactor(lint) | SIM | charts ×2 collapsible-if, forecast ternary, context/auth `contextlib.suppress`; `commands/auth.py` C901 entry removed (now CC ≤ 8) |
| 2 | `ad823f1` refactor(lint) | RET | 6 × RET504 (incl. 3 route bodies, names/docstrings untouched), 1 × RET505 |
| 3 | `9aa2e49` refactor(lint) | PTH | `path.chmod`, `Path(export).open("w")`, `self.db_path.open(...)` |
| 4 | `d7acf1a` refactor(lint) | PERF | 9 × PERF401 (comprehension / extend, same order and call order) |
| 5 | `e50f667` chore(refactor) | — | mutate.py snippets cli-03 / web-03 / learning-27 follow 2–4 |
| 6 | `da51210` build(lint) | PLW | config only; PLW0603 ignored by design, PLW0717 (preview) inactive |
| 7 | `bad5d81` refactor(lint) | PIE | `startswith(("/api/", "/mcp"))` |
| 8 | `ef00b4d` refactor(lint) | RSE | `raise AuthError` |
| 9 | `68b6372` build(lint) | FURB | config only (tests-only finding) |
| 10 | `a0fc6f8` build(lint) | N | config only; `devices/registry.UnknownDeviceModel` N818 (WP8, public name) |
| 11 | `8904a83` build(lint) | EXE | +x on the three refactor scripts with shebangs; `sync.py` EXE002 (stray +x, WP8) |
| 12 | `a27fc3f` + `84aa690` build(lint) | BLE | typing-review input: BLE enabled as a **lock**; the 11 existing broad excepts outside WP8 carry `# noqa: BLE001` (comments only), WP8 files per-file; one inert noqa (handler calls `logger.exception`) removed |
| 13 | `126aaab` refactor(lint) | RUF | RUF012 `ClassVar[...]` on Textual BINDINGS/MODES/RAMP/DAYS; RUF003 ASCII comments; RUF022 sorted `health_monitor.__all__`; RUF001 + RUF002 ignored (owner) |
| 14 | `f174e10` refactor(lint) | PLR2004 | 9 new core constants (additions only), 4 CLI time constants, 3 module-private constants; values/types identical; mutate.py learning-05/21/22/26 updated |
| 15 | `cf206ec` build(lint) | S | reasoned noqa on intended uses (comments only); findings below |
| 16 | `18c0cb7` build(lint) | ARG | reasoned noqa ×4; `web/routes/*` ARG001 exclusion (frozen `request` params) |
| 17 | `5f280e1` build(lint) | PLR0913/PLR0917 | `routes/*`, `web/routes/*`, `commands/*` excluded (handler params are the contract); rest ratcheted |
| 18 | `cc3f796` build(lint) | FBT | `commands/*` excluded (Typer flags); rest ratcheted |
| 19 | `1cdfd39` build(lint) | A002 | `commands/*`, `web/routes/*` excluded; `scheduler._add_core_job(id=)` noqa (mirrors APScheduler) |
| 20 | `47484c3` build(lint) | T201 | libs/ has no print; refactor/** exempt |
| 21 | `1537bc4` refactor(lint) | EM + TRY003 | 21 raise sites → `msg = …; raise X(msg)`, text identical; mutate.py auth-01/11, scheduler-14, tui-29 updated |
| 22 | `566341c` build(lint) | D | google convention, D105/D107 ignored, per-file ratchet |
| 23 | `e5adbd5` docs(tui) | D | 95 why-docstrings (TUI screens / widgets / resources); render.py 401 → 396 lines |
| 24 | `61dd9da` build(lint) | D417 | `routes/*`, `web/routes/*` D417 excluded (CLAUDE.md invariant 7: DI params undocumented); `vacation_add` noqa (`ctx` would leak into `--help`) |
| 25 | `bf04b77` **docs(api)** | D (doc-contract) | 67 schema class docstrings; regenerates **only** `tests/golden/contracts/openapi.json` (description-only, verified by stripping every `description` key → documents equal); `PHASE0_OPENAPI_SHA256` 21972789… → d6750d31…; `mcp_tools.json` unchanged |
| 26 | `477a3a2` build(lint) | — | grouping + "not adopted" comment; `ruff --show-settings` rules identical |

TC was **not** enabled (typing review: its autofix breaks FastAPI/Typer/Pydantic at import time). No ANN, COM812,
FAST001, PYI041; no behavior-changing BLE/TRY/DTZ edits.

## Measurements

- **max-statements = 23**: ruff PLR0915 counts (at `max-statements=1`) vs sizecheck body lines over every libs/
  function. Threshold 23 → 3 false positives (`ClusterCard._info`, `PumpWatcherService.watch`, `MetricChart.show_payload`)
  and 1 false negative (`IrrigationLogic._apply_seasonal_multiplier`, 44 lines / 22 statements). 22 → 8 errors,
  24 → 6, 30 → 6 (all misses).
- **Preview**: with `explicit-preview-rules`, the already-selected stable rules gained 0 findings. Preview does refine
  two stable rules adopted later: BLE001 skips handlers that log with the exception / re-raise (33 → 22 repo-wide),
  S310 skips literal http(s) URLs (weather.py's two calls are not flagged).
- **PLR1702 @ 3 vs sizecheck nesting**: identical — the same 6 functions with the same depths (ClusterScreen.compose 4,
  FormScreen.compose 5, IK10PWAdapter._start_keepalive 4, TuyaIrrigatorAdapter.status 5, sync_sensor_data 4,
  stop_all_irrigators 4). Retiring sizecheck's nesting check is safe once WP8 lands (integrator; not done here because
  WP8 is changing three of the six). Note `sync.py::sync_sensor_data` nesting 4 is not in `size-exceptions.txt`.

## Ratchet entries for the integrator (WP8 files — remove each once WP8's version is clean)

```
devices/__init__.py              RUF022
devices/gateway.py               BLE001 EM101 EM102 PLR0915 TRY003
devices/irrigators/ik10pw.py     BLE001 C901 PLR0911 PLR0915 PLR1702 RUF022 S110 SIM105
devices/irrigators/tuya_generic.py  ARG002 BLE001 C901 FBT001 FBT003 PLR0912 PLR1702 S110
devices/profile.py               D205
devices/registry.py              D102 EM102 N818 TRY003   (N818: UnknownDeviceModel is a public name — keep)
devices/sensors/tr301z.py        ARG002 RUF022
devices/sensors/tuya_generic.py  ARG002 BLE001
logic/engine.py                  ARG002 C901 D205 PLR0911 PLR0912 PLR0913 PLR0914 PLR0915 PLR2004 RUF003 RUF046
logic/timing.py                  SIM102
sync.py                          BLE001 D202 EXE002 PLR0915 PLR1702 S110 SIM108   (EXE002: `chmod -x sync.py`)
```
For BLE001 in WP8 files: prefer a per-line `# noqa: BLE001` on each existing handler (as done elsewhere) over the
per-file entry. Merge note: WP8 may re-trigger rules enabled here (PLR2004, EM, D, RUF, ARG …) on new code — run
`uv run ruff check libs/` after merging and extend the per-file list rather than weakening the config.

Remaining non-WP8 ratchet entries (follow-up candidates, all internal signatures): FBT (positional bools — client,
TUI, repository writers, scheduler pause helpers, services irrigation/system_health, routes/scheduler, web analytics),
PLR0913/0917 (repository writers, decision.add_reason, fallback, deps, services, client.update_window, TUI base/widgets),
PLR0904 (client, repository, ClusterScreen), PLR0914 (forecast), PLR0915/PLR1702/C901 as before.
`config.py` D101: a `Settings` docstring would change `contracts/settings_schema.json` (not an allowed doc-contract
golden) — left in the ratchet.

## REFACTOR_NOTES requests (report-only, nothing changed)

- **S310** `services/notify.py` `_publish`: `urllib.request.urlopen` on the operator-configured ntfy URL; the scheme is
  not validated (a `file:` URL would be accepted). Low risk (operator config) — candidate: reject non-http(s) at
  construction.
- **S104** `config.Settings.host = "0.0.0.0"`: the server binds every interface by default (Docker publishing). Intended,
  but worth a line in the deployment docs next to the MCP-token warning.
- **S110** try/except/pass: `app._apply_persisted_pause` (startup), `web/context._preference_flags` (+ WP8:
  ik10pw ×3, tuya_generic, sync) — silent swallow, no log line.
- **BLE001** (stable semantics, 32 in libs/): client 1, tui/widgets 1, devices gateway 1 / ik10pw 5 / tuya_generic
  irrigator 1 + sensor 1, logic engine 2 / plant_needs 1, sync 2, utils 1, app 3, services cluster 1 / irrigation 3 /
  maintenance 1 / notify 2 / pump_watcher 1 / sync 1 / weather 2, web/context 2.
- **TRY** (non-003): TRY301 `tui/screens/forms.py:65`; TRY300 `services/irrigation.py:284`, `services/weather.py:104`,
  WP8 `ik10pw.py:114`; TRY400 WP8 `sync.py:55` (`logger.error` in an except → `logger.exception` would add a traceback).
- **PLW0717** (try body > 5 statements, preview, 12): forms.py:55, ik10pw.py:178, tuya_generic.py:66, sync.py:37/96,
  app.py:323/342, bulk.py:44, irrigation.py:193/250/336, weather.py:80.
- **DTZ**: 0 findings today.
- **Dead-parameter chain**: `services/charts._threshold_for_cluster(plant_db)` is unused; dropping it makes
  `build_cluster_chart_payload(plant_db)` unused, which cascades into the API/web route dependencies — noqa'd, left
  for a dedicated dead-code commit.
- `fallback.temperature_based_decision(temp_range)` is unused but passed by the engine and frozen tests — noqa'd.

## Things a reviewer should look at hardest

1. `bf04b77` (doc-contract): the 62 new OpenAPI schema descriptions are LLM-facing; wording claims checked against the
   code (sync scope, manual start default, data-quality counts per code, vacation rationing, core jobs).
2. `126aaab` RUF012: `MODES: ClassVar[dict[str, str | Callable[[], Screen[Any]]]]` — Textual's own declared type;
   all modules use `from __future__ import annotations`, so nothing is evaluated at runtime; $TUI green.
3. `1537bc4` EM: `config.py` validator messages reach `settings_validation_errors.json` unchanged ($SETTINGS green).
4. `f174e10` PLR2004: `_PRELOAD_HOURS` in `ClusterScreen` names the coupling between the overview's chart fetch and the
   reuse check (`self.hours == 24`); `self.hours = 24` (initial range) intentionally left literal.
5. `size-exceptions.txt` (integrator-owned): the `commands/auth.py::register` reason says "module untouched (B2)" and
   cites CC from the nested defs — the module was touched by `30c06f8` (contextlib.suppress) and `register` is now
   CC ≤ 8; only the body-length part of the exception remains.
6. Plugin docs: `constants.py` gained 9 constants (no value changed); `plugin/.../LOGIC.md` does not enumerate them, so
   no doc update was needed — confirm.

## Gate

FULL at `477a3a2` (seed 0, all 139 test files in 6 sorted chunks, each its own lock hold, `-n 2`):
817 + 936 + 303 + 197 + 348 + 438 = **3039 passed, 0 failed** (same count as the integration gate at `3491e23`).
Final: `ruff check libs/ tests/ refactor/` clean, `ruff format --check` clean (326 files), `make typecheck` OK
(150 files), `lint-imports` 10 kept, no unused per-file-ignore entry, `mutate.py --check` INVALID set = base.

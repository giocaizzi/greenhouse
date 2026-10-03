# Pending outside work (reported in the final report)

Work found during the refactor that is **out of its scope** (behavior changes, other repos/settings, or actions that
need the owner). Kept here so nothing is lost across container restarts. Each item: what, where, why it matters,
suggested follow-up. Details/evidence: `REFACTOR_NOTES.md` and the `refactor/10-safety-*.md` reports.

## 1. Bug-fix PRs (safety first) — not fixed here because the refactor must not change behavior
| # | Bug | Where | Pinned by |
|---|---|---|---|
| S1 | **`dry_run_global` ("never actuate") is ignored** by every actuation path | storage/UI only; no reader | `test_dry_run_global_current_behavior_still_actuates` |
| S2 | **IK10PW keep-alive fallback can leave the pump ON** until the firmware auto-off: `signal.signal` off the main thread raises after `on()`, so the `finally: off()` never runs | `devices/irrigators/ik10pw.py` `_start_keepalive` | `test_keepalive_off_main_thread_current_behavior_leaves_pump_on` |
| S3 | **Cooldown blind spot:** a cluster that crashes after actuating in `check_all_clusters` is rolled back including its `start` event | `services/irrigation.py` | orchestration safety net |
| S4 | **TUI crash** when a dashboard load outlasts `refresh_seconds` (overlapping reloads → `NoMatches('#cluster-card-2')`) | `tui/screens/dashboard.py:68` | found by TUI hardening; reviewer suggested a GitHub issue |
| S5 | Pump watcher: a failed write during a trip makes its own except handlers raise `PendingRollbackError` (commit/rollback never run; pump already stopped) | `services/pump_watcher.py` | WP4 reviewers |
| S6 | **Shared `DeviceHealthMonitor` race (suspected):** `bind_repo` swaps the repo under the minutes-long pump watcher and the alert cache has no lock; a NO_WATER alert can land in another job's session (or be lost) while the cache marks it raised, so it never re-fires | `services/health_monitor.py:117`, `scheduler.py`, `pump_watcher.py:343` | `test_pump_watcher_trip_current_behavior_alert_written_through_rebound_repo_and_cache_suppresses_reraise` (`tests/server/test_health_monitor.py`; reproduced — architecture review A1) |
| B | ~60 further observed bugs (offline flap every sync, caps never checked in the automatic pipeline, re-raised alerts not re-notified, `water_warning` meaning mismatch, web 500s on bad input, `WWW-Authenticate` dropped on JSON 401, `verify_password` raising on truncated hash, `local_key` returned in plain text, settings read from unintended env names, migrated schema ≠ `create_all`, CLI timeout tracebacks, …) | see `REFACTOR_NOTES.md` "Observed bugs" | `*_current_behavior` tests |

- **Security:** the MCP bearer-token check (`require_mcp_token`, `greenhouse_server/app.py`) compares tokens with
  `!=`, not constant-time (`hmac.compare_digest`) — timing side channel on the credential that grants actuation
  authority. Found by the WP5 reviewer; not a regression.

## 2. Dependencies / supply chain
- `pip-audit`: 53 advisory rows across 10 locked packages (pyjwt, starlette, urllib3, cryptography, …) —
  `refactor/baseline/pip-audit.txt`. Needs a dependency-bump PR (runtime deps are frozen in this refactor).
- `deptry`: `greenhouse-server` imports `sqlalchemy`/`pydantic` directly but only gets them via core; `greenhouse-cli`
  imports `rich` transitively — declare them explicitly.

## 3. CI / repo settings (files out of scope here: `.github/workflows/*`)
- CI runs only `make coverage`; it does not run pre-commit (ruff, gitleaks), `lint-imports`, `make typecheck` or
  `make sizecheck`. Wire them into CI so the new gates are enforced on every PR.
- `make check` stays red until the final gate (I5) by design (sizecheck lists functions still scheduled); must be
  green at the end.
- gitleaks' pre-commit hook scans only staged changes; consider `gitleaks detect` on the PR range in CI.

## 4. Docs / plugin drift — **now in scope** (owner: remove drift everywhere; see `refactor/45-drift-track.md`)
- `CLAUDE.md` + `ConfirmScreen` docstring claim every actuating TUI key confirms; `i`/`w` open their own dialogs and
  `S`, `P`, `H`, alert `k`/`v`/`y`, scheduler resume run without one.
- `CLAUDE.md` module paths must be updated if the final structure moved anything (dedicated docs commit).
- Plugin skill docs (`plugin/skills/greenhouse/references/{LOGIC,CLI}.md`, `SKILL.md`) must be checked for drift
  after the refactor — CLAUDE.md requires them to track CLI/engine changes; refactor scope forbids editing `plugin/`.
- Dead code candidates — **now in scope** (owner directive: prune along the way + final sweep): `StressIndicators.any_critical()`,
  `is_within_quiet_hours` duplicate guard, unused `DEFAULT_QUIET_*` constants, `print_stats_report` (no caller),
  `#global-config` CSS rule, the 8 unreferenced public names in `refactor/00-map.md`, two unreachable guards from WP4.

## 5. Owner actions
- **Tag `refactor-gate1`** exists only locally (frozen safety net); pushing tags was not authorized — push it if you
  want it on GitHub.
- **MCP connectors need sign-in:** `firefly`, `greenhouse`, `n8n` (claude.ai connector settings, or `/mcp`). Not used
  by this refactor.
- **Open the PR** for `claude/focused-hawking-7to7o3` when the refactor is done (not opened without your request).

## 6. Queued refactor follow-ups (in scope, scheduled)
- Dead-code sweep after the last wave: vulture + manual evidence over the whole tree, rules in `refactor/BRIEF.md`.
- Lint ratchet after wave C — `refactor/40-lint-ratchet.md` (complexity tightening, then every non-functional ruff
  family; reviewed doc-contract edits allowed).
- TUI `search_citrus` render still flaky (listed in `refactor/gate1/flaky-tests.txt`) — targeted test-side fix in the
  TUI wave.

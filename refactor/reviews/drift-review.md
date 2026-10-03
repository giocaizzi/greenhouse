# Review: drift track (`refactor/drift-track`, 3d1f43d..56b45f4)

Worktree `/home/user/gh-drift`, read-only review. There are 17 commits plus a hand-off doc. They cover pairs D1–D10, D12–D17 and the OD4 owner decision; D11 is deferred to after WP8, as declared.

## Verdict: APPROVE (with owner confirmations listed at the end)

There are no blockers. I mapped every golden line and test-assertion change to a declared change in its commit body. On the canonical side, the API keeps its status codes, `detail` strings, DB effects and evaluation order wherever the commits say it does. Contract names, signatures, `response_model`s and Pydantic classes and fields are unchanged.

## 1. Golden and test changes mapped to declarations

| Commit | Golden / test change | Declared? |
|---|---|---|
| D14 45481f7 | imports.json: `export_csv` removed from `greenhouse_core.stats` (removal only). Export tests in test_stats / test_contract_stats removed. | yes (dead-code rule 4; grep evidence in the body) |
| D9 8faca8b | CLI golden: two case keys renamed. The `add` body gains `"config":{"device_ip":""}`. | yes |
| D13 5e0d9b0 | cluster_1_config.txt, settings.txt: same rows, reordered | yes |
| D8 f849e79 | surface.json: 4 labels gain " (UTC)". settings.txt: vacation header line. New TZ test. | yes |
| D10 a77a7aa | wp7 "40-50-60" → (40,50). wp6 gains a "50" case. New hypothesis agreement test. No goldens. | yes |
| D7 eaeb534 | 3 cases added (30s / 8d / none). Existing cases unchanged. No goldens. | yes |
| D4 e8af945 | check_all / check_single: badge slot line only | yes |
| D12 4034fb6 | two `*_current_behavior_*` tests become 404 tests. openapi/mcp: `sync_plants` description only. Fingerprints re-recorded. | yes |
| D3 fe9bf91 | 4 window mutation goldens: the one `<p>` line each | yes |
| D5 76bc13e | 2 vacation goldens: `<p>` line. Pipeline B-8 test inverted. openapi/mcp: `create_vacation_window` description only. | yes |
| D1 c54e86a | new golden create_irrigator__duplicate_device_id (409, `irrigators/new.html`, empty db_effect). No existing golden touched. | yes |
| D2 abbe4a9 | 3 new web goldens (404/409/404, empty db_effect). New API PUT test. openapi/mcp: `update_sensor` Raises only. | yes |
| D6 63c93a5 | 10 web goldens, one `<p>` line each (5 GET + 5 mutations, verified line by line). Refactor-guards allow-list only shrinks. | yes |
| D15 046a39a | new API+web persistence test. openapi/mcp: `monitor` docstring sentence only. | yes |
| D17 cf8dafe | openapi/mcp: `"maximum": 365` + docstring line. New test. | yes |
| D16 4c914e8 | repository_gaps: null / malformed / non-object → `{}`. imports.json: `json` dropped from schemas. | yes, but see m1 |
| OD4 8053a89 | manual_start_stop_log + stop_irrigator goldens: `off`→`stop`. openapi/mcp: `stop_irrigator` description. | yes |

**Contract diff, base→head.** `openapi.json` and `mcp_tools.json` differ only in:
- 6 description strings: `sync_plants`, `stop_irrigator`, `update_sensor`, `monitor`, `create_vacation_window`, `cluster_efficacy`;
- one `"maximum": 365`.

No operationId, `response_model`, path, schema name or field changed.

**Fingerprints.** I recomputed `sha256(json.dumps(golden, sort_keys=True))` for the OpenAPI golden at every commit that re-records it (4034fb6, 76bc13e, abbe4a9, 046a39a, cf8dafe, 8053a89). Each matches the recorded `PHASE0_OPENAPI_SHA256`. The final value is `ee96dba2…`, as stated in DRIFT.md. The MCP fingerprint `2a28d5d3…` is checked by the test run below.

## 2. Is the canonical side preserved?

- **D1 / D2 (API create).** The API keeps the same 409/404 details and order (cluster check, then plant check, then add). The commit moved out of the `try` block. That is safe because `repo.add_irrigator` / `add_sensor` call `flush()`, so an `IntegrityError` still surfaces inside the service and maps to 409. The capacity follow-up is unchanged. Only the web side changes: 500 becomes 409, and the cross-cluster plant now gets a 404.
- **D2 (API PUT).** The plant-in-cluster check is new on the API (declared). A body without `plant_id` (or with 0) is unaffected.
- **D3 / D5.** API validation order and wording are byte-identical. PUT vacation still says "starts_at must be < ends_at". POST is now validated (declared). The web loses its own wording (declared).
- **D6.** All API swaps are exact: same string, same truthiness, same ordering. On the web, `update_vacation` now checks existence before parsing dates. As a result, a bad date on a missing window gives 404 instead of 400 (declared as "checks existence first, like the API PUT").
- **D15.** `RepoDep` and the `SyncService` inside `IrrigationServiceDep` share the request-cached `get_repository` session, so `repo.session.commit()` commits the sync's rows.
  - `monitor_cluster` writes nothing apart from `ensure_fresh_and_read`. It touches the Cloud only through `sync_single_sensor`: one `getdevicelog` plus one live read per stale sensor, the existing documented path.
  - This matches CLAUDE.md invariant 8 and how `check` / `irrigate` already behave. It also follows OD2: the handler commits a durable side effect.
- **OD4.** Only `manual_stop` changed. No reader keys on `"off"` (grepped .py and .html). Cooldown, caps, efficacy and learning count only `"start"`. Historical rows are untouched.
- **D16.** Well-formed configs are unchanged. `None` still maps to `None`.

## 3. Tests (run under the lock)

```
PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 tests/server/ tests/cli/test_contract_json_output.py tests/cli/test_contract_tui_actuation.py tests/test_contract_imports.py tests/test_contract_constants.py
```

Result: **1947 passed, 0 failed**, 7 warnings, exit 0, 388 s. Note: I ran this as one block, so the lock was held for about 6.5 min. That is over the ≤ 5 min guidance; I should have split it into chunks.

## Findings

### Blocker
None.

### Major — owner decisions, not defects
- **M1 — D8: the TUI vacation UTC choice adds a new cross-surface display mismatch.**
  - The web form *parses* date-only input as UTC midnight, but the web *displays* vacations through `format_ts` → `get_display_timezone()` (the `UserPreferences.timezone` preference, then `IRRIGATION_TZ`). It does not display in UTC.
  - So "as the web form" is only half true. With a non-UTC preference, the TUI table (UTC) and the web vacation table (preference tz) now show different times for the same window. A TUI user typing "08:00" meaning local time gets 08:00 UTC.
  - The "(UTC)" labels make this visible, but it is the most surprising change in the track.
  - Alternative canonical: parse and display in the user's `timezone` preference, which the TUI already loads on the Settings screen. **Owner should confirm** UTC vs the preference timezone.
- **M2 — D7 wording.**
  - "irrigated stale" at 7 days or more reads like a data-quality warning, not an elapsed time.
  - "irrigated —" replaces the clearer "never" when there has been no event in 90 days.
  - One formatter is right, but the filter's "stale" token was designed for sensor freshness. **Owner should confirm**, or accept a template-side mapping (e.g. "irrigated >7d ago" / "never") in a follow-up.

### Minor
- **m1 — D16 under-declared case.** A stored `"null"` config previously returned `config: null` (200), not a 500. It now returns `{}`. The test change lists it, but the "User-visible change" paragraph only mentions malformed / non-object configs that used to give a 500. It is practically unreachable, since writers always store `json.dumps(dict)`.
- **m2 — D15 dead parameter.** No caller passes `monitor_cluster(..., no_sync=True)` any more, so the parameter is dead. That conflicts with the "no dead code" directive. Remove it in a `refactor` commit.
- **m3 — D15 residual divergence.** The web `/clusters/{id}/monitor` still lacks `require_cluster`. An unknown cluster renders an "unknown" panel with 200, while the API returns 404. Queue it for the consistency sweep. The good news: the web button is labelled "Refresh sensors" / "Refresh readings", so D15 makes the web behave as its label always claimed. That lowers the surprise.
- **m4 — D6 leftovers.** `if TYPE_CHECKING: pass` stubs remain in `web/routes/plants.py` and `web/routes/windows.py` (stale code).
- **m5 — D3 mixed punctuation.** The web window form now mixes styles: shared messages have no period, but the web-only parse errors still end with "." ("weekday_mask values must be integers."). The empty-weekday message "weekday_mask must be 1..127 (Mon=1, Sun=64)" is developer-speak on a checkbox form.
- **m6 — Docs not yet synced.** CLAUDE.md says plugin docs MUST change in the same PR when `logic/` changes (D10 → `references/LOGIC.md`) or CLI behavior changes (D9, D5, D12 → `references/CLI.md`, e.g. `vacation add` rejecting reversed windows). DRIFT.md lists these for the end-of-branch doc sync. They must land before merge.
- **m7 — Tracking-doc gap.** `45-drift-track.md` still lists only D1–D14. D15–D17 come from the consistency audit (C-TX-6, C-DUP-2, C-MISC-1) and appear only in DRIFT.md. Add them to the table so they can be traced.
- **m8 — OD4 side effects.**
  - The TUI now colours manual stops (`ACTION_STYLES["stop"]`); the old "off" had no style.
  - In existing databases, `events_by_type` will show both `off` and `stop` buckets.
  - Both are consequences of the decision as accepted ("existing rows unchanged"); noted for the report.

## Flagged calls (task item 5)

| Item | Likely to surprise or break a workflow? | Confirm? |
|---|---|---|
| D7 dashboard strings | Cosmetic, but "irrigated stale" reads wrong (M2) | yes (wording) |
| D8 TUI vacation UTC | Yes, for non-UTC users. Silent shift for anyone used to local input; also differs from the web display tz (M1) | **yes** |
| D13 config order | No. Rows now match the edit form. | no |
| D2 extended to API PUT sensor | Low risk. It only rejects a cross-cluster link, which the data model never meant to allow. Possible impact on scripts or MCP agents that "move" a sensor by pointing it at another cluster's plant: they now get 404. | brief yes (API/MCP-visible) |
| D15 web monitor syncs | No. The button is labelled "Refresh readings". It is manual (no polling), and the cost is at most one targeted sync per stale sensor. Net Cloud calls drop for API/TUI callers, because rows now persist. An offline sensor stays stale and is retried on each click, as `check` already does. | no (FYI) |

## Owner should confirm
1. D8: UTC vs the `UserPreferences.timezone` preference for TUI vacation entry and display.
2. D7: accept "irrigated stale" / "irrigated —", or map them in the template.
3. D2: the API/MCP `PUT` sensor now 404s on a cross-cluster `plant_id`.
4. (FYI) D15 Cloud-call profile; D17 API `days` > 365 → 422; D5 POST reversed window → 400; D12 unknown cluster → 404 (CLI exit 1).

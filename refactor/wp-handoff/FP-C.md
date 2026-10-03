# FP-C hand-off — core non-WP8 fix pass (`refactor/47-fix-pass.md` § FP-C)

Worktree `/home/user/gh-fp-c`, branch `refactor/fp-c`, base `8a76a3f`. Not pushed, not merged, not rebased.
WP8 files (`logic/engine.py`, `logic/timing.py`, `devices/**`, core `sync.py`) were **not edited**. Server edits are
limited to one forced call-site change (`services/inventory.py`, item 6). No CLI code touched; no `[tool.importlinter]`
edit; no `constants.py` value changed (one new constant, appended in its own block). No behavior change, so no
`fix(...)` commit and no `REFACTOR_NOTES.md` entry.

Every pytest run: `PYTHONHASHSEED=0 flock /tmp/greenhouse-tests.lock uv run pytest -q -n 2 …`.

## Commits (table order = branch order)

| Commit | Item | What | Evidence (subset → result) |
|---|---|---|---|
| `a0fa75f` | 9 | remove dead `models.EVENT_ACTION_OFF` (legacy `off` documented on `EVENT_ACTION_STOP`) | grep → definition only, not in goldens; `$CORE` + test_db 62 passed |
| `8561462` | 9 | remove dead `plant_db.set_plant_database` (+ `imports.json` name, rule 4, removal-only) | grep → definition + golden only; `$CORE $SETTINGS` test_plant_db 144 passed |
| `6b89b76` | 9 | remove dead `database.logger` (+ `loggers.json` entry and `logging` from `imports.json`, rule 4, removal-only) | grep → no reader, no caplog filter; 67 passed |
| `c8736ab` | stale ids | OD2 / B-24 / B-18 / "quirk preserved" / "pinned import surface" comments → plain why | comments only; mutate `--check` = base |
| `7f6fcb6` | stale docs | `get_plant_profile` docstring claimed "≥ 3 events" (false; gate is in `issues`), `moisture_target_range` history docstring, what-comments in `learning/issues.py` | comments only |
| `a879d29` | 1 | plant-care fallback literal → `constants.PLANT_CARE_FALLBACK` (`copy.deepcopy` per call: same keys/order/values/types, fresh `sources` list) | `$(D gc.plant_db) $CORE $CHECK $SETTINGS` 915 passed |
| `2baf310` | 1 | `profiling._response_from` `or 2` → `DEFAULT_DURATION_MINUTES`; mutate learning-19 snippet follows | 428 passed |
| `73d772a` | 8 | six bare `dict` schema fields → `dict[Any, Any]`, six `type: ignore` removed | OpenAPI golden **and** `PHASE0_OPENAPI_SHA256` fingerprint green (byte-identical); `$API $CORE $CHECK` + … 827 passed |
| `2939f47` | 7 (test first) | new `tests/test_repository_patch_fields.py`: None skipped / unknown keys ignored / rest set, for update_cluster / update_plant / update_preferences | 3 passed on unchanged code |
| `a8cbcdc` | 7 | `_patch_fields_hasattr_first` merged into `_patch_fields` | 344 passed |
| `4fcc36c` | 7 | `detect_conflicts` docstring lists the three alert families + shared gate (emitters already have intention-revealing names; a wrapper was tried and dropped — it pushed `issues.py` over 400 lines) | docs only |
| `e944334` | 10 | new `tests/cli/test_constants_mirror.py`: `ALL_WEEKDAYS == FULL_WEEKDAY_MASK`, SECONDS_PER_HOUR/DAY mirrors (own file to avoid conflicts with FP-U's `test_cli.py`) | 2 passed |
| `d88074e` | 6 | `DeviceIdExistsError` moves to `repository` (re-exported by `services/inventory`, `__all__` kept); `repo.refusing_duplicate_device_id(id)` context manager does rollback → raise `from None`; inventory no longer imports SQLAlchemy | 307 passed (duplicate-device 409s, web mutation `create_*__duplicate_device_id`) |
| `24aa2a5` | 2 | `get_effective_config -> EffectiveConfig` (`dict[str, EffectiveField]` TypedDict); 2 casts in `logic/fallback.py` removed | `$CORE $ENGINE` + configs 401 passed; engine strict errors 11 → 9 (untouched) |
| `18ce4e7` | 2 | `update_*` / `set_irrigation_config` / `update_global_irrigation_config` → `**fields: Unpack[<Entity>Patch]` (8 TypedDicts) | every call site type-checks; 396 passed |
| `9018e47` | 3 | `EntityType`, `ActivitySource`, `EventAction`, `TriggeredBy` StrEnums in `models.py`; old constant names alias the members | **FULL** suite 3045 passed |
| `4401e18` | — | fallback quirk comment back to one line (c8736ab had pushed `temperature_based_decision` to 41 body lines) | sizecheck clean |

## Gates (final)

- FULL (seed 0, `-n 2`) at `9018e47` (last code commit; later commits are comment/doc only):
  **3045 passed**, log `…/scratchpad/full-strenum.log`.
- `uv run ruff check libs/ tests/` 0 · `ruff format --check libs/ tests/` 0 (325 files) · `make typecheck` 0 (150 files) ·
  `uv run lint-imports` 0 (10 kept) · `ruff check refactor/` 0.
- `sizecheck`: no new hit in FP-C files. `constants.py` was already over (402 lines at base, not excepted) and is now
  417 (+15 for `PLANT_CARE_FALLBACK`) — see size-exception proposal.
- `mutate.py --check`: INVALID set = base **minus** `fallback-06` (that snippet targeted the pre-cast line and applies
  again after `24aa2a5`; not re-run — a timed-out run was killed and its mutation reverted by `git checkout`).
- `tests/golden` diffs: removal-only (`imports.json` −2 names, `loggers.json` −1 entry).

## Skipped (with reason)

- **Item 4 (split `utils.py`)** — frozen tests patch the module globals in place: `monkeypatch.setattr(utils,
  "_display_timezone", …)` then call `utils.get_display_timezone()` (test_contract_settings.py:361, test_contract_stats.py:20,
  test_web_vacation.py:58), and `monkeypatch.setattr("greenhouse_core.utils.seasonal_light_factor", …)` relies on
  `effective_light_threshold` resolving that global inside `utils` (test_contract_wp6_gaps.py:57/454). With the functions
  moved behind a re-export shim those patches stop taking effect → red frozen tests (rule 8). Also `env_reads.json` pins
  the `IRRIGATION_TZ` read in `greenhouse_core.utils`.
- **Item 5 (lazy `__init__`)** — no measured production cost: `import greenhouse_core.constants` costs ≈0.49 s
  (36 core modules, tinytuya, alembic) but the server imports the whole core anyway and the CLI never imports core.
  `greenhouse_core.{logic,devices,learning}` `dir()` (incl. eagerly imported submodules) is pinned in `imports.json`, so
  only the unpinned root could go lazy — YAGNI.
- **Item 9 plant_db singleton** — `get_plant_database` / `reset_plant_database` are used as the fixture by ~30 tests that
  test `PlantDatabase` itself; removing them means rewriting frozen tests → kept (DRY review agrees).
- **K-9** (pass `plant_care` into the light/humidity emitters) — not a refactor: tests call `detect_conflicts(..., {})`
  with a plant DB that returns care data, so the result would change.

## Deferred — server sites (outside FP-C file scope; for FP-S / integrator)

- Item 1 magic numbers in server files: `services/sync.py:77 hours=6`, `services/manual_control.py:66,74 hours=24`,
  `services/anomaly.py:93 hours=72`, `services/forecast.py:127 hours=24`, `:26 _FALLBACK_DRAINAGE_PER_HOUR = -2.0`,
  `:27 _WEATHER_PRECIP_THRESHOLD_MM` alias → use `WEATHER_SKIP_PRECIP_MM`, `services/cluster.py:134,153 hours=24/48`,
  `services/alerts.py:117,127` + `services/leak.py:248 limit=200`, `services/pump_watcher.py:103 alarm_dp 105`,
  `web/routes/plant_dashboard.py:107,133`. (`deps.MAX_LOOKBACK_HOURS = 8760` is already named.) New constants go in a
  grouped block at the end of `constants.py`.
- Item 2 TypedDicts in server: weather results (`services/weather.py`), alert findings (`services/maintenance.py`
  `collect_*_alerts`), sync snapshot (`services/sync.py`, wraps WP8 `core/sync.py` — do with WP8).
- Item 3 parameter typing with the new enums: core repository writers (`add_irrigation_event`, `add_activity_event`,
  `add_alert`) are called from server code that threads `triggered_by: str` / `source: str` through ~50 server
  signatures (`services/irrigation.py`, `notify.py`, `health_monitor.py`, `alerts.py`, `pump_watcher.py`), so typing the
  core params forces a server cascade — do it after FP-S merges.
- `services/inventory.py` re-exports `DeviceIdExistsError`; FP-S's planned import-linter ignore
  `services.inventory -> sqlalchemy` (architecture review A8) is no longer needed.

## Deferred — post-WP8

- engine `2.0` rain literal → `WEATHER_SKIP_PRECIP_MM`; engine's remaining 9 strict errors (3 `int(object)` already gone
  via `EffectiveConfig`). `CONFIDENCE_BASELINE` dead check (unchanged here).

## Proposed size-exception (integrator)

```
libs/greenhouse-core/greenhouse_core/constants.py — flat registry of named thresholds; CLAUDE.md invariants #5/#9 require every threshold and the plant-care built-ins in this one module, and tests bind names from it (417 lines; grows with WP8 additions)
```

## Strict list / ratchet

- No new modules (every touched core module was already in `refactor/mypy-strict.txt`; `migrations/env.py` is FP-S item 13).
- No per-file ignore added or removed.

## REFACTOR_NOTES request (report only)

`REFACTOR_NOTES.md` cites B-3…B-25 only through `refactor/00-smells.md`, which OD5 deletes. Comments now describe the
quirks in place ("known quirk: …") for B-18 (trends drop 0 °C), B-24 (fallback needs a cluster config row) and the
shared `detect_conflicts` gate; the orchestrator may want titled entries for them in the kept notes.

## Look hardest at

1. `9018e47` StrEnum aliases: the constants change type (str → str-subclass members). Verified equivalences are listed
   in the commit body; FULL suite green. `repr()` would differ — nothing reprs them today.
2. `d88074e`: the context manager re-raises `DeviceIdExistsError(...) from None` from inside a generator; exception
   type, args, rollback order and suppressed context match the old inline handler.
3. `a879d29`: `copy.deepcopy` keeps a fresh `sources` list per call (the old literal did too).

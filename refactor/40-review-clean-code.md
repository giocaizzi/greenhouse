# Phase 4 review: clean code

Reviewer: clean-code reviewer (Phase 4). Read-only on code. Target: the current integration branch
`claude/focused-hawking-7to7o3` @ `3491e23` (after the consistency W3 gate).
Scope: everything under `libs/` **except** `greenhouse_core/logic/engine.py`, `logic/timing.py`,
`greenhouse_core/devices/**` and `greenhouse_core/sync.py` (WP8 is still in progress, so those get reviewed later).
Yardstick: `refactor/BRIEF.md` owner decisions ("full clean consistent end state", "method-level cleanup with
slick typed interfaces") and `CLAUDE.md` invariants, in particular #5 (all thresholds in `constants.py`).

## How I checked

| Check | Command | Result (in scope) |
|---|---|---|
| Size / nesting DoD | `python3 refactor/scripts/sizecheck.py` | **exit 1.** One un-excepted in-scope violation: `tui/render.py` has 401 lines (limit 400). The other hits are excepted or WP8. |
| Complexity | `uv run ruff check --isolated --select C90,PLR0911,PLR0912,PLR0915 --config lint.mccabe.max-complexity=8 libs/` | `commands/auth.py::register` CC 9 and `commands/operations.py::register` CC 19 / 54 statements (both size-excepted); `tui/screens/forms.py::parse_value` CC 11 and 8 returns; `services/health_monitor.py::_alarm_message` 8 returns |
| Smell scan | custom AST pass in my scratchpad: params > 4, positional bools, `**kwargs`, `data`/`info`/`tmp`/`d` names, private helpers used once, bool functions not named as predicates | listed in the findings below |
| Comment rot | grep for refactor-process ids (`D\d+`, `OD\d`, `WP\d`, `B-\d+`, `§`, "pinned", "exactly as before", "characterization") | 20+ hits in production code (C-1) |
| Magic numbers | grep for `hours=24`, `3600`, `limit=200` and literal thresholds | about 15 in-scope sites (C-2) |

**Behavior impact** uses two values. "none" means the change is a pure refactor (the `refactor(...)` lane).
"would change behavior" means it needs a labeled `fix(drift|consistency)` commit and goldens reviewed per the brief.

Severity counts: blocker 0 · major 13 · minor 38 · nit 25.

---

## Cross-cutting findings

**C-1 (major). Refactor-process references left in production comments and docstrings.**
These point at documents in `refactor/`, which OD5 deletes at the end. They also describe *history*
("exactly as before the extraction", "pre-existing, kept") instead of *why*. Sites:

- `services/efficacy.py:16` (D17), `services/charts.py:33` (D10), `routes/operations.py:45` (D19) and `:197` (D15)
- `repository.py:91` (OD2), `database.py:13` (OD3), `models.py:58` (OD4)
- `services/irrigation.py:209` (WP4) and `:402` ("partial-count characterization test")
- `scheduler.py:341-342` ("exactly as before the extraction"), `services/pump_watcher.py:359-360` ("exactly as before", "pre-existing behavior")
- `logic/fallback.py:97` (B-24), `logic/trends.py:59` (B-18), `learning/issues.py:386` ("quirk preserved")
- `tui/widgets.py:37` ("pre-existing, kept"), `web/context.py:73` ("contract: target §3.6")
- `services/_session.py:27-31` ("as every job did inline", "exactly like the old inline try")
- six `# type: ignore[...]  # ... (pinned)` tails: `scheduler.py:298/313/347/360`, `_session.py:40`

None of the B-ids appear in the kept `REFACTOR_NOTES.md`.
Fix: restate each one as a plain *why* (for example, fallback.py:97 → "A configured schedule is used only when the cluster
has its own config row; a global-only config falls through to NO_DATA."). Where the quirk is a known bug, cite the
`REFACTOR_NOTES.md` "Observed bugs" entry by its title, not by an internal id. Keep durable references such as
`issue #103`. **Behavior impact: none.**

**C-2 (major). Magic numbers break invariant #5 ("all thresholds live in `constants.py`").**
| Site | Literal |
|---|---|
| `plant_db.py:112-122` | the whole care fallback (`water_frequency_days: 7`, 18/27 °C, 50/70 %, `"45-65"`); CLAUDE.md #9 says these come from `constants.py` |
| `learning/profiling.py:35` | `or 2` default duration |
| `learning/profiling.py:125` | `d > 2` "positive response" |
| `learning/profiling.py:187` | `0.1 < hours < 12` |
| `learning/report.py:37` | efficiency `< 0.5` (different from `LEARNING_MIN_EFFICIENCY = 0.3`) |
| `logic/plant_needs.py:53-55` | 1.5 / 2.5 averaging cut-offs |
| `services/sync.py:24` | `SNAPSHOT_LOOKBACK_HOURS = 24` defined in the service |
| `services/sync.py:77` | `hours=6` freshness backfill |
| `services/manual_control.py:66,74` | `hours=24` cap window, twice |
| `services/anomaly.py:91` | `hours=72` |
| `services/forecast.py:127` | `hours=24` |
| `services/cluster.py:134,153` | `hours=24`, `hours=48` |
| `services/alerts.py:118,128`, `services/leak.py:248` | `limit=200` |
| `deps.py:171` | `MAX_LOOKBACK_HOURS = 8760` |
| `services/pump_watcher.py:103` | `"alarm_dp": 105` |

Fix: add named constants (new names only; existing values stay frozen) and reference them. Docstrings that repeat the
numbers (`anomaly.py:75-81`, `profiling.py:148` "at least 3 events", `issues.py:110/193` "# 7 days" and "# latest 3")
should name the constant instead, so they cannot go stale. **Behavior impact: none.**

**C-3 (major). Untyped dict bags at core service seams, against the "slick typed interfaces" decision.**
- `IrrigationRepository.get_effective_config()` returns `dict[str, dict[str, object]]`. Consumers index
  `["auto_run"]["value"]` and need `cast(int | None, …)` (`logic/fallback.py:39-42`, `services/irrigation.py:904`,
  `web/routes/clusters.py:206-212`).
- `SyncService.ensure_fresh_and_read` / `_cluster_snapshot` return `dict[str, Any] | None`. Consumers call
  `sensor_data.get("soil_moisture")` (irrigation.py:533-541, 627-650).
- `WeatherClient.get_current` / `get_forecast` return `dict[str, Any]`.
- `collect_learning_alerts` / `collect_maintenance_alerts` rows are read with `raw.get("type", "alert")`
  (`alerts.py:98-100`, `insights.py:57`).
- `plant_db.CareData = dict[str, Any]`, while `logic/plant_needs.py` and `logic/stress.py` spell it `list[dict[str, Any]]`.

Fix: add `TypedDict`s (`EffectiveField{value, source}`, `ClusterSnapshot`, `CurrentWeather`, `WeatherForecast`,
`FindingRow`) and use the `CareData` alias everywhere. Runtime objects stay plain dicts, matching OD1.
**Behavior impact: none.**

**C-4 (minor). `**kwargs` / `**fields` passthroughs where the brief asks for explicit parameters.**
- `cli/client.py`: 13 methods (`update_cluster` :159, `add_plant` :170, `update_plant` :178, `add_irrigator` :203, `update_irrigator`,
  `add_sensor`, `update_sensor`, `set_config` :256, `update_global_config` :273, `update_preferences` :460,
  `list_activity` :360, …). Fix: a `TypedDict` per body plus `**fields: Unpack[...]` (Python 3.11 `typing.Unpack`).
- `repository.py`: `update_*`, `set_irrigation_config`, `update_global_irrigation_config`, `update_preferences`. Fix: the same.
- `commands/_helpers.py:25`: `call(ctx, fn, *args, **kwargs)`. No caller passes `args` or `kwargs` (every call is
  `call(ctx, lambda c: …)`). Fix: `def call(ctx, fn: Callable[[IrrigationClient], T]) -> T`.

**Behavior impact: none.**

**C-5 (minor). Long positional parameter lists on internal APIs.**
- `repository.add_plant` (10), `add_decision_log` (11), `add_sensor_reading` (8), `add_sensor` (7), `add_irrigator` (5): make them keyword-only after the first id.
- `IrrigationService.__init__` (7) and `run_irrigation_pipeline(cluster_id, temp_override, dry_run, no_sync, force)`: positional bools.
- `_resolve_temperature(cluster_id, is_indoor, temp_override, no_sync)`: positional bools.
- `manual_start` / `manual_log` (5), `charts.build_*_chart_payload` (5), `learning.detect_conflicts` (5), `logic.detect_stress_conditions` (5).
- TUI: `PlantTile(...)` with 6 positional args (`tui/screens/cluster.py:178-186`), `BaseScreen.form_then` (6).

The two places that wire `IrrigationService` disagree on style: `deps.py:225-233` passes arguments positionally,
`scheduler.py:328-336` passes keywords. Fix: add `*` after the identity parameter and update every call site in the
same commit. **Behavior impact: none.**

**C-6 (minor). Mixed annotation style.** 86 modules use `from __future__ import annotations`. 16 modules use
`TYPE_CHECKING` with string-quoted annotations (`services/irrigation.py`, `manual_control.py`, `pump_watcher.py`,
`scheduler.py`, …). Section dividers are also mixed: `# ── X ──` in most files, `# --- X ---` in `deps.py`.
Fix: pick one style (the future import is the majority) and apply it per package in a formatting-only commit.
**Behavior impact: none.** One caveat: watch Pydantic/FastAPI models, which need real annotations. Leave
`schemas.py` and route modules out.

**C-7 (nit). Single-letter and generic names in builders.** `p`, `s`, `r`, `d`, `ev`, `w` loop variables
(`tui/render.py`, `logic/plant_needs.py:16-49`); `s = summary` aliases (`render.py:152`, `widgets.py:107/111`,
`screens/cluster.py:172`); `data =` results (`cli/commands/*`, `tui/screens/*`, `weather.py:48/82`); `info`
(`render.irrigator_info`, `widgets.Banner.update_health`, `ClusterCard._info`, `charts.py:183`); `care2`
(`learning/issues.py:349`); `f` for a forecast (`screens/cluster.py:199`) and for a `Field` (`forms.py:42`).
**Behavior impact: none**, except that public builder names such as `irrigator_info` are golden-pinned, so rename locals only.

---

## greenhouse-core

| # | Sev | Where | Evidence | Suggested fix | Behavior |
|---|---|---|---|---|---|
| K-1 | major | `learning/issues.py:375-391` `detect_conflicts` | The name says "conflicts", but it also produces the **low-light** and **low-humidity** alerts. All three are gated together on `len(moisture) < 2`. The function does three jobs under a misleading name. | Keep the public name (import surface) but make the body read as what it is: `_overwater_conflict_alerts(...)` plus `_environment_alerts(...)`, with the shared gate explicit at the top and its why in a comment. Optionally rename to `detect_cluster_issues` and keep a re-export shim. | none |
| K-2 | major | `repository.py:1263-1283` | `_patch_fields` and `_patch_fields_hasattr_first` differ only in whether `hasattr` runs before or after the `None` check. Neither ordering is observable on ORM rows. `_hasattr_first` names the implementation, not the intent (leftover from extraction). | One `_patch_fields(row, fields, *, json_fields=frozenset())` for all four `update_*`. | none (hasattr on mapped attributes has no side effects) |
| K-3 | major | `plant_db.py:112-122` | Built-in care defaults are an inline literal (see C-2). | Move them to `constants.PLANT_CARE_FALLBACK` and build `merged = dict(PLANT_CARE_FALLBACK)`. | none |
| K-4 | minor | `plant_db.py:39-71` `lookup_species` | Three-pass matcher in one body. The `_spec_name` loop variable has an underscore but is used (L63). What-comments ("# Exact match", …). `species in aliases or species.lower() in [...]`: the first test is a subset of the second. | Split into `_exact`, `_alias`, `_fuzzy` helpers, or one loop per pass with intention-revealing names. Drop the redundant test. | none |
| K-5 | minor | `plant_db.py:124-142` `get_care_data` | `merged.get("_category_defaults", {})` (L136) always hits; `merged["category"]` is set twice. | Merge layers with `for layer in (cat_basics, cat_defaults, species_data): merged.update(layer or {})`, then set the two meta keys once. Keep the order of the `category` key assignment (dict order is visible in API JSON). | none, if key insertion order is kept (check against goldens) |
| K-6 | minor | `logic/plant_needs.py:14-29` | `get_ideal_temp_range` and `get_ideal_humidity_range` are copy-paste. | `_care_range(care, min_key, max_key)`. | none |
| K-7 | minor | `logic/plant_needs.py:32-38` | `parse_moisture_target` uses a bare `except Exception`. The `moisture_target_range` docstring says "six call sites repeat" (history). | Catch `(ValueError, IndexError, AttributeError)`. Restate the docstring as a why. | none, provided the narrowed set covers every current input (`None` → AttributeError) |
| K-8 | minor | `learning/issues.py:40` | The `_SensorReading = tuple[Sensor, float, float]` alias reads like the `SensorReading` model. It is unpacked as `dry_s, dry_m, dry_target`. | `class _BandPosition(NamedTuple): sensor; moisture; band_edge`. | none |
| K-9 | minor | `learning/issues.py:309,349` | `_low_light_alerts` and `_low_env_humidity_alerts` call `plant_db.get_care_data` again, although `plant_care` is already computed and passed into `detect_conflicts`. | Pass `plant_care` down. | none (pure lookup) |
| K-10 | minor | `learning/issues.py:353` | The humidity check uses `LEARNING_DRAINAGE_LUX_LOOKBACK_HOURS`, a constant named for drainage/lux. | Add an alias constant `LEARNING_ENV_LOOKBACK_HOURS = LEARNING_DRAINAGE_LUX_LOOKBACK_HOURS`. | none |
| K-11 | minor | `learning/profiling.py:146-149` | The docstring says "Needs at least 3 irrigation events". The code returns a profile from 1 event; the ≥ 3 gate lives in `issues.py` (`LEARNING_MIN_EVENTS`). Stale docstring. | Correct the docstring. | none |
| K-12 | minor | `models.py:58` | `EVENT_ACTION_OFF = "off"` has zero references in `libs/`, `tests/` and goldens since OD4. The comment is stale. | Remove it in a dead-code commit with the grep evidence. | none |
| K-13 | minor | `database.py:13` | The module `logger` is never used ("kept for pinned surface"). | Remove it under prune rule 4 with a golden diff, or keep it but delete the OD3 reference. | none |
| K-14 | minor | `repository.py:638-658`, `694` | `get_effective_config` is an untyped bag (C-3). `set_decision_actuated(log_id, actuated=True)` takes a positional bool. | `EffectiveField` TypedDict; make `actuated` keyword-only. | none |
| K-15 | minor | `repository.py:1392-1410` `update_sensor` | `plant_id_in_fields` plus a conditional `pop` dance. | `new_plant_id = fields.pop("plant_id", _UNSET)` with a sentinel. | none |
| K-16 | nit | `learning/report.py:21-46` | `# Alerts` what-comment; magic 0.5 (C-2); `lines = []` untyped. | | none |
| K-17 | nit | `learning/issues.py:110,165,173,193,314,316,318` | What-comments (`# 7 days`, `# Not enough data`, `# exclude night readings`, `# latest 3 readings`). | Delete them or fold them into constant names. | none |
| K-18 | nit | `plant_db.py:144` | `get_water_needs_info` uses the "info" naming. It is a public name, so add an alias only if it is worth it. | | none |

## greenhouse-server

| # | Sev | Where | Evidence | Suggested fix | Behavior |
|---|---|---|---|---|---|
| S-1 | major | `services/irrigation.py:811-843` | The health gate is inline (28 lines, nesting 3). It is the only reason `run_irrigation_pipeline` holds a size exception. The exception rationale ("moving it would reorder side effects") does not hold for a straight method extraction: the call stays at the same point. Its activity row also duplicates `_log_decision_skip` with a different severity and payload. | `_apply_health_gate(cluster_id, irrigator, decision, result) -> PipelineResult \| None`, returning the skip result or `None`. Generalize `_log_decision_skip(cluster_id, decision, *, severity="info", payload=None)`. Drop the exception. | none (same calls, same order) |
| S-2 | major | `services/irrigation.py:493-495` and `routes/operations.py:163` | The route tells "cluster missing" apart by string-matching the service's free-text reason `"cluster not found"`. The docstring of `_error_result` documents this coupling. | At minimum a shared constant `CLUSTER_NOT_FOUND_REASON`. Better: the service raises `ClusterNotFound` and both the API and web routes map it. | none with the constant; the exception variant also none if both routes catch it before `commit()` |
| S-3 | major | `services/irrigation.py:800-803` | `if dry_run or decision.action.value == "skip": if not dry_run: …` is a nested double negative. It compares `.value == "skip"` while L826 uses `Action.SKIP`. | `if dry_run: return result` then `if decision.action is Action.SKIP: self._log_decision_skip(...); return result`. | none |
| S-4 | major | `services/manual_control.py:141-151, 193-202, 242-252`, `irrigation.py:739-749`, `alerts.py:65-70` | Five `maybe_notify(notifier, repo.get_preferences(), kind, lambda: notifier.notify_…(...))` sites. Four carry the same `# type: ignore[union-attr]` because the lambda cannot see the None check. | `maybe_notify(notifier, prefs, kind, lambda n: n.notify_irrigation(...))`: pass the narrowed notifier into the callback. That removes all the ignores. Keep `get_preferences()` evaluated eagerly (it may insert). | none |
| S-5 | minor | `services/irrigation.py:59,62,290-294`; activity/alert codes | Module constants are scattered mid-file. Codes are half constants (`CHECK_FAILED_ALERT_CODE`, `LEAK_CHECK_ACTIVITY_CODE`), half literals (`"decision_skip"` ×2, `"irrigated"`, `"actuation_failed"` ×2). `"leak_hold"` is a literal in `irrigation.py:294` and in `leak.py:127`. | Move the constants to the top. Put the activity codes in one vocabulary next to `SOURCE_*` in `models.py`, or a server `codes.py`. | none |
| S-6 | minor | `services/irrigation.py:889-918, 948-972`, `_check_result` :570-592 | The `detail_key` tuple juggling and the `cast` exist to build one dict with a computed key. Two other branches (L893, L967) bypass the helper and build literals. | Two small builders, `_monitored_result(...)` and `_acted_result(...)` (plus `_check_error(...)`), each returning a TypedDict literal in the response key order. | none (key order kept) |
| S-7 | minor | `services/irrigation.py:189`, `scheduler.py:310` | Redundant function-local `from greenhouse_core.repository import IrrigationRepository`, already imported at module top. | Delete. | none |
| S-8 | minor | `services/irrigation.py:209` + `services/pump_watcher.py:126` | `sleep: Callable[[float], None]` forces a `type: ignore[arg-type]` when the scheduler passes `wait_for_shutdown -> bool`. | Type it `Callable[[float], object]`. | none |
| S-9 | minor | `services/irrigation.py:616-632` | `_resolve_temperature` returns a 3-tuple. `"fallback (20C)"` hard-codes the value of `FALLBACK_TEMPERATURE_C`. | `NamedTuple TemperatureReading(value, source, snapshot)`; `f"fallback ({FALLBACK_TEMPERATURE_C:.0f}C)"`. | none while the constant is 20 |
| S-10 | minor | `services/irrigation.py:533-549` | The comment in `_soil_note` repeats its docstring. `_event_notes(act, soil_note)` threads a value it could derive from `act`. | Let `_event_notes(act)` call `_soil_note(act.sensor_data)`. | none |
| S-11 | minor | `services/irrigation.py:251, 327, 429`, `services/irrigation.py:189` | Services reach into the scheduler's **private** `_app` global through lazy imports. | Use a narrow accessor. The scheduler's `dir()` is frozen, so a public `current_app()` needs a golden diff. Otherwise document `_app` as the sanctioned seam in one place. | none |
| S-12 | minor | `services/health_monitor.py:334-352` | `_alarm_message` is an if-chain with 8 returns (PLR0911). | Template map `_ALARM_MESSAGES: dict[HealthAlarm, str]` with `.format(label=…, pct=…)`, plus the default. | none |
| S-13 | minor | `services/health_monitor.py:354-370` | `_infer_cluster_id` and `_infer_label` duplicate the same entity lookup. `record()` calls both, which costs two lookups. | `_lookup_entity(entity_type, id) -> Irrigator \| Sensor \| None` used by both. | none (fewer queries, same output) |
| S-14 | minor | `services/health_monitor.py:207-222` | `is_actuation_blocked` returns `(bool(blocking), blocking)`, which is redundant. The blocking set is an inline tuple. | Return `list[HealthAlarm]` (empty = not blocked) and add `_BLOCKING_ALARMS = frozenset(...)`. Update `irrigation.py:817`. | none |
| S-15 | nit | `services/health_monitor.py:226-246, 380-384` | `if not readings: continue` is subsumed by the length check. `_raise_if_not_open` gets `cluster_id` and `sensor` separately. `__all__` re-exports `SOURCE_HEALTH` from models (one test imports it from here). | Simplify, and point the test at `models`. | none |
| S-16 | minor | `services/pump_watcher.py:179-209` | `self._clock() - (deadline - duration_seconds)` is repeated 4 times. `alarm_raw` extraction is duplicated at L207 and L261. | `origin = deadline - duration_seconds` once, plus `_alarm_raw(state)`. | none |
| S-17 | nit | `services/pump_watcher.py:287, 316-318, 371-380` | `_log_trip_activity(source=SOURCE_PUMP)` always gets the same constant. `_lazy_monitor` is typed `\| None` but never returns None, so the `if monitor is not None` check is dead. The module docstring cites "PR 1.5"; the `ALERT_CODE` comment is history. | Drop the parameter, fix the type, restate the comments as why. | none |
| S-18 | minor | `services/manual_control.py:43-77, 120-121` | `check_rate_limits` mixes two caps over two irrigator ids with literal `hours=24` (C-2). The registry check is duplicated before `_adapter` to preserve error order. | Add `_starts_in_last_day(repo, irrigator_id)` and `_require_registry(registry)` used by both. | none |
| S-19 | minor | `services/sync.py:36-44` | Constructor parameter `cloud: DeviceGateway` is stored as `self._gateway`. "Cloud" is pre-merge vocabulary (TuyaCloud became DeviceGateway). The same applies to `scheduler._get_cloud()` (`scheduler.py:279`). | Rename to `gateway` / `_get_gateway`. Every caller is positional; check test patches of `_get_cloud`. | none |
| S-20 | nit | `services/sync.py:68-73` | `# type: ignore[union-attr]  # None short-circuits`. | `if (r := latest[s.id]) is None or now - r.timestamp > …`. | none |
| S-21 | minor | `services/weather.py:33-110` | The cache-check, URL-build and fetch boilerplate is duplicated in `get_current` and `get_forecast`. `_FORECAST_CACHE_TTL` also governs `get_current`. Results are dict bags (C-3). The cache ignores `hours`, which is already in REFACTOR_NOTES. | `_cached(slot)` / `_fetch_json(query)` helpers and `_CACHE_TTL` naming. | none (do not fix the `hours` cache key here; that one would change behavior) |
| S-22 | minor | `services/leak.py:143-214` | `_evaluate_sensor` encodes three states as `tuple[str \| None, dict] \| None`. `_never_settled_reason` both returns a value and mutates `evidence` (breaks command-query separation). | `LeakVerdict` dataclass (`inconclusive` / `settled` / `confirmed(reason, evidence)`). The rule returns its evidence fields instead of mutating. | none |
| S-23 | minor | `services/insights.py:56-68` | Two identical dedupe loops. | `for alert in chain(maintenance, learning):`. | none (same order) |
| S-24 | minor | `services/anomaly.py:112-192` | `_stale_alert` and `_drift_alert` wrap the whole body in `if cond:` and then `return None`. The docstring hard-codes the thresholds (C-2). | Guard clause first (`if not …: return None`). | none |
| S-25 | minor | `services/forecast.py:65, 174` | `weather_client: Any`. The "next 6h" text is hard-coded next to `WEATHER_FORECAST_HOURS`. | `WeatherClient \| None`; build the text from the constant. | none while the constant is 6 |
| S-26 | minor | `deps.py:215-233` vs `scheduler.py:317-336` | `IrrigationService` is wired twice, with different argument styles. | One `build_irrigation_service(state, repo, …)` used by both, or at least keyword-only arguments (C-5). | none |
| S-27 | minor | `app.py:100` and `auth.py:142` | Identical `_get_settings` helpers. | Share one, for example `deps.get_settings`. | none |
| S-28 | minor | `auth.py:148` vs `deps.py:25` | `_session_from_app` duplicates `get_session`. | **Do not merge naively.** FastAPI caches dependencies by function identity, so merging would make auth and the route share one session. Add a one-line why-docstring ("separate session on purpose: …"). | would change behavior if merged |
| S-29 | minor | `web/context.py:49-85` | `_preference_flags` returns a 4-tuple. `_auth_enabled` uses an assign-then-try-pass pattern. | `ChromeFlags` NamedTuple; `getattr(getattr(request.app.state, "settings", None), "auth_enabled", True)`, keeping the `bool()` cast. | none |
| S-30 | minor | `web/routes/clusters.py:204-215` | The quiet-hour conversion `int(x["value"]) if x["value"] is not None else None` is written out twice. `_plants_by_id -> dict[int, object]`. | `_optional_int(...)` or a typed `EffectiveField`; return `dict[int, Plant]`. | none |
| S-31 | minor | `web/filters.py:186-265` | `cluster_caps(obj: Any)` and `_present`. The capability result is an untyped dict. | `cluster: ClusterStatus \| Cluster \| None`, a `ClusterCaps` TypedDict, and rename `_present` to `_has_any`. | none |
| S-32 | nit | `scheduler.py:110` | The `id=` keyword shadows the builtin. `set_check_all_paused(repo, paused: bool) -> bool` is a command that also returns state, and is called with a positional bool (`apply_persisted_pause(True)`, L246). | Pinned surface: just name the arguments at call sites. | none |
| S-33 | nit | `services/cluster.py:149-166` | `_irrigator_status` uses an assign-then-if instead of a guard clause. | `if irrigator is None: return None`. | none |
| S-34 | nit | `services/irrigation.py:805, 823, 903, 908` | `# Execute` what-comment; redundant parentheses around an f-string; `monitor.get("needs_water", [])` on a total TypedDict; `result.get("action", "error")` on a `Required` key. | Use plain indexing. | none |
| S-35 | nit | codes `sensor_stale` (`anomaly.py:123`) vs `stale_sensor` (maintenance) | Two spellings for neighbouring concepts. | Leave as is: the codes are persisted. | would change behavior |

## greenhouse-cli

| # | Sev | Where | Evidence | Suggested fix | Behavior |
|---|---|---|---|---|---|
| L-1 | major | `tui/render.py` | 401 lines, the only **un-excepted** in-scope sizecheck failure. The gate exits 1. | Remove the `s = summary` alias (L152) and one blank line, or move the forecast/insight builders into `render_insights.py` with a re-export. | none (golden-pinned outputs unchanged) |
| L-2 | major | `tui/widgets.py:37` | `SpriteView` sets `self._animate = animate`, which shadows `textual.widget.Widget._animate` (a `BoundAnimator \| None` cache, `widget.py:444/2541`). Any `sprite_view.animate(...)` call would invoke `True(...)` and raise TypeError. The `type: ignore` and "(pre-existing, kept)" hide a latent defect. | Rename to `_animated` in a labeled commit. Record it in REFACTOR_NOTES first, per rule 1. | would change behavior (only on the currently-crashing `animate()` path) |
| L-3 | major | `commands/configs.py:22-101` (also `plants.py` add/update, `irrigators.py` add/update, `windows.py`, `vacation.py`) | `config set` and `global set` repeat eight identical `Annotated[..., typer.Option(...)]` declarations verbatim. The same goes for the other add/update pairs. | Module-level option aliases (`ModeOpt = Annotated[str \| None, typer.Option(help=…)]`). The `--help` text is identical by construction; prove it with the CLI help golden. | none |
| L-4 | minor | `commands/operations.py:10-118`, `commands/auth.py:28-95` | Commands are defined as closures inside `register(app)`. That is the only source of two size exceptions plus ruff C901/PLR0915. Every other command module uses module-level `@x_app.command`. | Module-level functions, with `register(app)` doing `app.command()(status)` and so on in the same order. Prove it with the `--help` and command-tree goldens, then drop both exceptions. | none |
| L-5 | minor | `tui/screens/forms.py:42-69` | `parse_value` has CC 11 and 8 returns. `Field.kind: str` is stringly typed. The `Field` docstring has a broken reflow ("→ Unix seconds). Blank optional"). | `_PARSERS: dict[str, Callable[[str, Field], Any]]` for int/float/datetime/json, plus `Kind = Literal[...]`. Reflow the docstring. | none |
| L-6 | minor | `commands/_helpers.py:25-31` | `*args, **kwargs` are never used; `Any` in and out. | Generic `call(ctx, fn: Callable[[IrrigationClient], T]) -> T` (C-4). | none |
| L-7 | minor | `client.py` | `"/api/v1"` is repeated 79 times. `timeout=30.0` is magic. | `_API = "/api/v1"` (or `_path(...)`) and a `REQUEST_TIMEOUT_SECONDS` constant in `cli/constants.py`. | none |
| L-8 | minor | TUI Python outside `sprites.py` | 45 hard-coded hex colours (`#7ed957` ×15, `#e0c341` ×11, `#ff5f5f` ×8, `#4fb3ff` ×8, …) alongside the existing `fmt.*_STYLES` maps. | A palette in `tui/formatting.py` (`OK`, `WARN`, `DANGER`, `WATER`) referenced everywhere. | none |
| L-9 | minor | `tui/screens/cluster.py:145,157,241-242,262` | Literal look-backs (`hours=24`, `72`, `24 * 30`, `limit=100/200`) while `STATS_DAYS` is already a named constant. | Screen-level constants. | none |
| L-10 | minor | `tui/render.py:150-171` vs `tui/widgets.py:111-143` | The "watering now / last event / sensor-only" phrasing is built twice with slightly different wording. | Leave it: the strings are golden-pinned and differ. Add a comment saying the divergence is deliberate, or unify as a labeled drift item. | would change behavior if unified |
| L-11 | nit | `tui/screens/cluster.py:399` and similar | Curried `lambda v: lambda c: c.add_plant(cid, **v)` is hard to read. | A named `def _add_plant(values): return lambda c: …`, or a `partial`. | none |
| L-12 | nit | `commands/*` | `data = call(...); output(data)` vs `output(call(...))` are used interchangeably. | One spelling, except where `data` is inspected afterwards (`irrigate`). | none |

---

## Top 20 fixes (ranked by value / risk)

| Rank | ID | Fix | Value | Risk | Behavior |
|---|---|---|---|---|---|
| 1 | L-1 | Bring `tui/render.py` to 400 lines or fewer (gate is red) | high | trivial | none |
| 2 | C-1 | Rewrite refactor-process references as plain *why* before `refactor/` is dropped | high | trivial | none |
| 3 | S-3 | Flatten the `dry_run`/skip double negative; compare with `Action.SKIP` | high | low | none |
| 4 | S-1 | Extract `_apply_health_gate` and drop the `run_irrigation_pipeline` size exception | high | low | none |
| 5 | S-4 | Have `maybe_notify` pass the narrowed notifier (removes 4 `type: ignore`s) | high | low | none |
| 6 | K-2 | Unify `_patch_fields` / `_patch_fields_hasattr_first` | med | low | none |
| 7 | C-2 | Move the magic numbers into `constants.py` (invariant #5), starting with K-3's care fallback | high | low | none |
| 8 | S-2 | Replace the `"cluster not found"` string match with a constant or exception | med | low | none |
| 9 | L-3 | Shared Typer option aliases for the duplicated add/update/set commands | med | low (help golden proves it) | none |
| 10 | L-4 | Move CLI command closures to module level and drop two size exceptions plus the C901 hits | med | low (help golden) | none |
| 11 | C-3 | TypedDicts for effective config, cluster snapshot, weather and findings; removes `cast`s | high | low | none |
| 12 | K-1 | Make `detect_conflicts`' three jobs explicit (env alerts plus the shared gate) | med | low | none |
| 13 | K-12/K-13 | Remove dead `EVENT_ACTION_OFF` and `database.logger` (dead-code commits with evidence) | med | low | none |
| 14 | S-5 | One vocabulary for activity/alert codes; module constants at the top | med | low | none |
| 15 | C-5 | Keyword-only parameters on `repository.add_*`, `IrrigationService.__init__`, `run_irrigation_pipeline` and `_resolve_temperature` | med | low | none |
| 16 | L-5 | Parser table for `parse_value` (CC 11 → 3) | med | low | none |
| 17 | S-12/S-13/S-14 | Health monitor: message map, one entity lookup, `is_actuation_blocked` returns a list | med | low | none |
| 18 | C-4/L-6 | Replace `**kwargs` with `Unpack[TypedDict]` in client/repository; typed `call()` | med | low | none |
| 19 | S-19 | `cloud` → `gateway` naming in `SyncService` and `scheduler._get_cloud` | low | low | none |
| 20 | L-2 | Un-shadow Textual's `Widget._animate` in `SpriteView` (labeled fix, note first) | med | low | **would change behavior** (latent-crash path only) |

Not ranked, but avoid: S-28 (merging the auth session would share the request session), S-35 / L-10 (renaming persisted
codes or unifying pinned strings). All three need the labeled drift lane if they are ever wanted.

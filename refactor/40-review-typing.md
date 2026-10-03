# Phase 4 review — Typing & Pythonic idioms

Reviewer: Typing & Pythonic (read-only on code). Base: `3491e23` (after consistency W3).
Scope: `libs/**` excluding WP8 files (`logic/engine.py`, `logic/timing.py`, `devices/**`, `greenhouse_core/sync.py`)
for the idiom review; the strictness plan (§3) covers **every** libs module, WP8 included, so it can be scheduled.

Evidence commands (all run on this tree):

- `make typecheck` → `Success: no issues found in 150 source files`.
- Per-module: `uv run mypy --strict --follow-imports=silent <file>` for every libs `.py` not in `refactor/mypy-strict.txt`.
- Relaxed-profile cost: mypy with the same settings **minus** the `[[tool.mypy.overrides]]` block over
  `routes/`, `web/routes/`, `cli/commands/`, `cli/main.py`.
- Final-config trial: the TOML in §3.3 (with `warn_unreachable` + extra codes for the leads), checking all of `libs` in 11 s.
- `get_type_hints` sweep over every TypedDict / dataclass / NamedTuple and every `routes`/`web` function.
- Ruff with extra rule families (`RUF012,RUF100,PERF,PTH,SIM,TRY,BLE,TC,DTZ,G,UP`) to collect leads. These are leads only, not proposals to enable.

Severity: **H** = correctness/contract risk or blocks the strict goal · **M** = type hole that hides real errors ·
**L** = consistency/idiom · **I** = info / keep as is.

---

## 1. Typing

### 1.1 Modules not yet strict

| Module | strict errors | Notes |
|---|---:|---|
| `server/routes/configs.py` | **0** | Clean already but **missing from the list**, so it is not enforced. Add it now. |
| `server/routes/bulk.py` | **1** | `:33` `notifier.notify_irrigation` on `NtfyClient \| None` inside the `maybe_notify` lambda (see T2). |
| `core/devices/gateway.py` (WP8) | 28 | `self._cloud` is `object \| Any` (8 union-attr), `int(object)`/`float(object)` in the DP converter table (6), bare `dict` (8), no-any-return (3). |
| `core/devices/irrigators/tuya_generic.py` (WP8) | 3 | bare `dict` |
| `core/devices/irrigators/ik10pw.py` (WP8) | 2 | `:110,:223` pass `float \| None` to `open_local(timeout: float)` (a real latent mismatch) |
| `core/devices/profile.py` (WP8) | 2 | bare `dict`, `json.load` Any return |
| `core/devices/{health,irrigators/base,sensors/base,sensors/tuya_generic}.py` (WP8) | 1 each | bare `dict` |
| `core/logic/engine.py` (WP8) | 11 | 4 untyped defs (`_apply_window_rule`, `_apply_seasonal_multiplier`, :292, :321), 3× `int(object)` from `get_effective_config`, `environment: str` vs `Literal['indoor','outdoor']` (:362), bare `dict` |
| `core/logic/timing.py` (WP8) | 2 | bare `dict` |
| `core/sync.py` (WP8) | 6 | returns bare `dict`, so per-cluster stats are `object` (`+`, `.append` errors), plus 1 untyped def |
| `core/migrations/env.py` | 0 | Not in the list. Include it; keep `migrations/versions/*` excluded (out of scope, digit-led names). |
| **Relaxed "framework profile"** (`routes.*`, `web.routes.*`, `cli.commands.*`, `cli.main`) | **210** | 200 are *missing return annotation*. 3 untyped params, 6 bare `dict`/`list`, 1 union-attr (bulk). By group: web/routes 90, routes 58, cli 62. |

Total if the final config (§3.3) were switched on today: **283** errors in 65 files. That is 210 relaxed + 58 WP8
+ 7 `import-untyped` (tinytuya ×2, apscheduler ×4, fastapi_mcp ×1) + 8 opt-in-code leads (§1.6).

### 1.2 `# type: ignore` inventory (23 outside WP8; all carry codes, so ruff `PGH` passes)

| # | Location | Verdict | Fix (behavior impact) |
|---|---|---|---|
| T1 | `core/schemas.py:109,173,427,447,875,882` `dict \| None  # type: ignore[type-arg]  # contract: OpenAPI` | **Lazy (M).** The stated reason is false. Pydantic emits `{"type":"object","additionalProperties":true}` for `dict`, `dict[str, Any]` **and** `dict[Any, Any]` (verified with `model_json_schema()`). | Use `dict[Any, Any] \| None`, which matches bare `dict` for both schema and validation. Avoid `dict[str, Any]`: lax mode rejects non-str keys on Python-side construction. **None**, and the OpenAPI golden stays byte-identical. |
| T2 | `services/manual_control.py:145,197,246`, `services/irrigation.py:743` `[union-attr]` + the remaining strict error in `routes/bulk.py:33` | **Workaround (M).** `maybe_notify(notifier, prefs, category, fn: Callable[[], object])` narrows `notifier` internally, but the lambdas close over the un-narrowed value. | Change it to `fn: Callable[[NtfyClient], object]`, call `fn(notifier)`, and write the call sites as `lambda n: n.notify_irrigation(...)`. Type `category` as `Literal["manual","emergency","alerts","auto"]`: it is used in `getattr(prefs, f"notify_{category}")`, so a typo silently disables notifications. Removes 4 ignores plus bulk's error. 7 call sites, all internal. **None.** |
| T3 | `services/irrigation.py:209` `sleep=sleep  # [arg-type] … the bool is ignored` | **Lazy (L).** | Type `PumpWatcherService.__init__(sleep: Callable[[float], object])` (pump_watcher.py:126). **None.** |
| T4 | `services/charts.py:275` `events=raw_events  # [arg-type]`, **no reason** | **Lazy (L).** `list[dict]` is passed to `list[ChartEventResponse]` and Pydantic coerces it. | Have `_build_event_list` return a `TypedDict` list and call `ChartEventResponse.model_validate` per item, or build `ChartEventResponse` directly. Same JSON. **None** (golden chart tests). |
| T5 | `web/context.py:74` `session.close()  # [union-attr]` | Justified by the "repo and session set together" invariant, but the comment does not say why it is None-safe. | Return a `(repo, session)` pair typed as `tuple[IrrigationRepository, Session] \| tuple[None, None]`, or narrow with `if session is not None`. The latter is behavior-identical only because the invariant holds; keep the ignore if that is in doubt. |
| T6 | `scheduler.py:298,313,347,360` `[union-attr]`, `:351` `[arg-type]`; `services/_session.py:40` | **Justified (I).** A None `_app` must escape as `AttributeError` (pinned). | Optional: centralise in one `_state() -> State` accessor with a single ignore (5 → 1). Do not add `assert` or raise: that changes the exception type. |
| T7 | `services/sync.py:72` `[union-attr]  # None short-circuits` | **Justified-ish (L).** mypy does not narrow `latest[s.id]` through the subscript. | Bind `r = latest[s.id]` and then `if r is None or now - r.timestamp > …`. **None.** |
| T8 | `tui/screens/base.py:25` `[return-value]`, `tui/screens/search.py:57` `[assignment]` | **Boundary (I/L).** Textual types `self.app` as `App[Any]`. | Use `cast("GreenhouseApp", self.app)` once in `base.gh`. `SearchScreen` is a `ModalScreen`, so give it the same `gh` property or a shared mixin rather than a second ignore. |
| T9 | `tui/screens/forms.py:139` `widget.value  # [attr-defined]` | **Lazy (L).** | `self.query_one(f"#field-{f.name}", Input \| Select \| Checkbox)`, a typed query that is a runtime no-op in Textual. Note that the expect-type argument does an `isinstance` check and raises `WrongType` on mismatch; the widgets built at :103-118 are exactly these three. |
| T10 | `tui/widgets.py:37` `[assignment]  # shadows DOMNode._animate` | **Justified (I)**, pre-existing. | Renaming it would be cleaner (`_animate_enabled`) but it is a Textual internal clash. Rename only with a TUI test run. |

### 1.3 `cast(...)` inventory (13 outside WP8)

| Location | Verdict |
|---|---|
| `repository.py:356,424` `cast("CursorResult[Any]", session.execute(stmt))` | Justified. Standard SQLAlchemy idiom for `rowcount`. |
| `deps.py:184` `cast("Metric", metric)` after the membership check | Justified (could be a `TypeGuard`; optional). |
| `services/irrigation.py:582` computed-key TypedDict | Justified and commented. |
| `cli/client.py:120,124` JSON → `JSONObject` / `list[JSONObject]` | Justified HTTP boundary. |
| `learning/profiling.py:36,37,80`, `logic/trends.py:93`, `tui/model.py:124` (`Optional` → value after a list filter) | Acceptable. Narrowing is lost through comprehensions. Lower-noise alternative: filter into a typed local (`[(r, r.soil_moisture) for r in … if r.soil_moisture is not None]`). |
| `logic/fallback.py:41,42` `cast(int \| None, effective["…"]["value"])` | **M: the root cause is `get_effective_config() -> dict[str, dict[str, object]]`** (repository.py:638, 10 callers). A `TypedDict` (`EffectiveField{value, source}` per known key, or a `Mapping[str, EffectiveField]` whose `value: int \| str \| bool \| None`) removes these 2 casts, the 3 `int(object)` errors in `engine.py:284-289` and the quiet-hour handling in `web/routes/clusters.py`. Same runtime dict. |

### 1.4 `Any` (≈330 occurrences in 48 files outside WP8)

- **Justified boundaries (I).** JSON over HTTP in the CLI/TUI (`client.py` 19, `tui/render.py` 21, `tui/*` ≈50) uses the
  `JSONObject` alias, so keep it. Device `config` dicts are free-form JSON (`repository.py:166,202`, `parse_device_config`).
  `payload_json` is also free-form.
- **M: Starlette `app.state` is an untyped attribute bag.** It has 42 attribute reads plus 18 `getattr(…state, …)`:
  `session_factory` ×11, `settings` ×7, `plant_db` ×5, `device_gateway` ×5, `weather_client` ×4, `health_monitor` ×4,
  `device_registry` ×3, `mcp` ×2, `ntfy_notifier` ×1. Every value read from it is `Any`, which is the largest silent hole in
  the server. Fix: typed accessors in one module, e.g. `greenhouse_server/state.py` with
  `def session_factory(app) -> sessionmaker[Session]: return app.state.session_factory`, or keep the `getattr(…, None)`
  forms verbatim where the None-default semantics are pinned. **None** if each accessor reproduces the exact attribute
  or getattr expression.
- **M: `services/irrigation.py:178` `_run_pump_watcher(app: Any, …)` is lazy.** Use `FastAPI` (a TYPE_CHECKING import is fine here because the function is not introspected).
- **M: `repository.py` has 8 `update_*(…, **fields: Any)` / `set_irrigation_config(**fields: Any)`.** This is the exact
  "dict bag" smell the brief names. Python 3.11 has `typing.Unpack`, so use `**fields: Unpack[ClusterPatch]`
  (`TypedDict, total=False`) per entity. The runtime signature and behavior are unchanged, and mypy then checks every
  route/web/service call site's keys.
- **L: dict-shaped results still `dict[str, Any]`.** OD1 says these become TypedDict. Remaining public ones outside WP8:
  `services/charts.py:44,73` (chart payloads), `services/cluster.py:51 decision_to_view`,
  `services/maintenance.py:27,108` (alert dicts, which feed `CheckResult.alerts/maintenance` and `MonitorResult`),
  `services/weather.py:33,62` (Open-Meteo result), `services/sync.py:46,52` (wrap WP8 `core/sync.py`, so do them with WP8).
  Inner `list[dict[str, Any]]` fields of the existing TypedDicts (`PipelineResult.reasons/stress_indicators`,
  `MonitorResult.sensors`, `CheckResult.alerts/maintenance`) should follow.

### 1.5 Structural-type consistency (OD1)

- TypedDict (11): `ClusterStatus`, `ClusterHistory`, `WatchOutcome`, `PipelineResult`, `MonitorResult`, `CheckResult`,
  `HealthScore`, `JobInfo`, `IrrigationRecord`, `IrrigationStats`, `StatsUnavailable`. These are consistent dict results.
- NamedTuple (2): `PlantSyncResult`, `StopAllResult`. Fine, because callers tuple-unpack (`stopped, errors = …`). Keep.
- Dataclasses (14). The mix is inconsistent: `frozen=True, slots=True` (2: `_Actuation`, `_HealthBands`), `frozen=True` only
  (4: `AuthenticatedUser`, `ClusterBudget`, `_Cached`, `PixelSprite`) and mutable (8: `_SensorForecast`, `CleanedReading`,
  `learning.models.{IrrigationResponse,PlantProfile,Alert}`, `tui.model.{PlantView,ClusterSummary}`, `forms.Field`).
  **L:** add `slots=True` to the 4 frozen-only ones. Nothing uses `asdict`/`vars`/`__dict__` in libs (grep: 0 hits), so
  this has no behavior change. Make the mutable ones `frozen=True` **only** after mypy (which flags assignments to frozen
  fields) proves no mutation. Value objects like `CleanedReading` and `PlantView` are the likely candidates.
- Pydantic read models stay (OD1), with no change.

### 1.6 Leftovers, `TYPE_CHECKING`, forward refs

- `Optional[` / `Union[` / `typing.List|Dict|…`: **0**. `X | None` is used everywhere.
- **L: two forward-reference conventions.** 64 of 148 modules use `from __future__ import annotations`, while 17 modules
  (mostly `services/*`, `app.py`, `scheduler.py`, `repository.py`, `schemas.py`, `plant_db.py`) use **83 quoted
  annotations** (`"Irrigator"`, `"Select[Any]"`, …) instead. Pick one. Recommendation: add `from __future__ import annotations`
  and unquote in the non-introspected modules (services, repository, scheduler, app helpers). In `schemas.py`/`models.py`
  it also works because the referenced names are module-level, but it gains nothing there, so leave those two.
- `get_type_hints` sweep: **no introspected surface is broken.** No route or web handler fails. The 17 failures are
  private helpers plus two classes whose fields reference TYPE_CHECKING-only names:
  `services/cluster.py ClusterStatus` (`IrrigationConfig`) and `services/irrigation.py _Actuation` (`Irrigator`).
  **L (latent trap):** if `ClusterStatus` is ever used as a `response_model` or Pydantic field, FastAPI fails at import.
  Move that one import out of `TYPE_CHECKING`, or add a comment.
- **H (process):** do **not** enable ruff `TC00x` (it would flag 92 imports). Its autofix moves imports under
  `TYPE_CHECKING` and breaks FastAPI/Typer/Pydantic signature introspection at import time. If it is ever wanted, set
  `runtime-evaluated-base-classes = ["pydantic.BaseModel", "greenhouse_core.models.Base"]` and
  `runtime-evaluated-decorators` for the routers first.
- Opt-in mypy codes produce leads, not proposed config. Each hit guards device/DB data whose declared type is narrower
  than what the device may actually send, so removing the guard changes behavior and needs owner rule 3's proof:
  `warn_unreachable` → `health_monitor.py:352` (fallback after an exhaustive enum chain); `redundant-expr` →
  `pump_watcher.py:207,217,261` (`isinstance(state.raw, dict)`); `truthy-bool` → `engine.py:282,307,335`,
  `web/routes/clusters.py:216` (`if prefs` on a non-Optional `UserPreferences`).

## 2. Pythonic idioms

| # | Sev | Finding (evidence) | Fix | Behavior |
|---|---|---|---|---|
| P1 | L | **String vocabularies are bare `str` constants** (`models.py:38-67`: `ENTITY_*`, `SOURCE_*`, `EVENT_ACTION_*`, `TRIGGERED_BY_*`), not `Final`, used 117×. Meanwhile **56** parameters are typed `triggered_by: str` / `source: str` / `action: str` / `entity_type: str`. The repo already uses `StrEnum` for `Action`, `Severity`, `TriggerCode`, `HealthAlarm` and `Mood`. | Add `class EventAction(StrEnum)`, `TriggeredBy`, `AlertSource` and `EntityType` in `models.py`, keep the old constant names as aliases (`EVENT_ACTION_START = EventAction.START`, since `models` is a pinned import path), and type the 56 parameters with the enums. The SQLAlchemy columns stay `String`; read models stay `str`. | **None.** Verified: a `StrEnum` member binds to sqlite as `'start'` (typeof `text`), `json.dumps`, f-string and `str()` give `start`, and a Pydantic `str` field stores `<class 'str'>` and dumps `"start"`. Values read back from the DB are plain `str` and compare equal to members. |
| P2 | L | `EVENT_ACTION_OFF = "off"  # manual stop (OD4 unifies it on stop)` (`models.py:58`) has **0 references** in libs and tests, so its comment is stale now that OD4 has landed. | Remove it in a dedicated dead-code commit, or keep it documented as "legacy value of pre-OD4 rows". | None |
| P3 | L | **Broad excepts: 39 `except Exception`, 0 bare.** All but 3 log (`logger.exception`/`debug(exc_info=True)`) or record the error. Silent swallows are at `web/context.py:45` (`return None, None`), `web/context.py:70` (`pass`) and `app.py:332` (`return "UTC"`). These are best-effort fallbacks and pinned behavior, so **report only**. | Optional: add `logger.debug(..., exc_info=True)` to the 3 silent ones. That changes log output only, so do it as a labeled commit if at all. | Log-only |
| P4 | L | `# noqa: BLE001` at `services/irrigation.py:71,161` and `services/bulk.py:61`, but `BLE` is not selected, so these are dead noqa (`RUF100`). The other 36 broad excepts carry none. | Drop the 3 noqa comments, or select `BLE` and annotate all of them. | None |
| P5 | L | Manual `session = …session_factory(); try: … finally: session.close()` at 6 sites: `services/irrigation.py:192,335,435`, `scheduler.py:401`, `app.py:324,343`. `_job_session` exists for jobs, and ea6116e deliberately kept `_run_leak_check` on its own scaffolding. | Where the sequence is just open → body → close, use `with app.state.session_factory() as session:`. `Session.__exit__` calls `close()`, so this is the same order. Keep the explicit `rollback()` lines. | None |
| P6 | L | `cli/client.py:96` `IrrigationClient.http = httpx.Client(...)` is never closed. There is no `close()` or `__enter__`. | Add `close()`/`__enter__`/`__exit__`. The CLI is a short-lived process; the TUI holds one client for the session. | None |
| P7 | L | 17× `RUF012`: Textual `BINDINGS = [...]` / `CSS`-style class lists without `ClassVar` (`tui/app.py:36,44`, `screens/*`, `widgets.py:95,280,281`). | Annotate as `ClassVar[list[BindingType]]`. This is the Textual idiom and documents that the list is shared on purpose. | None |
| P8 | I | Logging is consistent: 0 f-string logger calls and 22 %-style calls. `getLogger(__name__)` everywhere. | none | — |
| P9 | I | Mutable default args: 0 (ruff B006 already gates them). pathlib: `os.path` 0. `open()` is always used inside `with` (2 PTH123 leads are style-only). `cli/client.py:61` uses `os.chmod` (PTH101); `Path.chmod` is equivalent. | optional | None |
| P10 | I | Datetime: 0 naive `now()`/`utcnow()`. 6 `from datetime import UTC`, 0 `timezone.utc`. `tui/formatting.py:95` passing `tz=None` → local time is deliberate (TUI display). Two ZoneInfo fallback helpers exist, `scheduler._resolve_zoneinfo` (UTC on `ZoneInfoNotFoundError`) and `tui/formatting.zone` (`ValueError` too), plus `web/routes/vacation.py:55`. Their semantics differ slightly, so do not unify silently; that would be a drift item. | none (drift track if desired) | — |
| P11 | I | `match` candidates: none. No if/elif chain of 4 or more branches on one subject (AST scan). Comprehension leads: 9 `PERF401` (`tui/render.py:78,136,299,317`, `screens/alerts.py:60`, `services/alerts.py:92,94`, `services/maintenance.py:128`, `learning/report.py:46`). Convert only where the loop body is a single append; it is readability only. | optional | None |

## 3. Plan: every libs module strict, and the final config (OD5)

Rule for every step: one commit per bullet. Run `uv run mypy` on the touched files plus the relevant test subset; route
steps also run the OpenAPI, MCP-tools, CLI-help and web goldens. Return annotations on FastAPI routes **do not change
OpenAPI when `response_model=` is set** (verified on FastAPI 0.141.1: `response_model=M` with `-> N` still emits `M`).

### 3.1 Order (errors cleared per step)

1. **Now (no WP8 dependency).**
   a. Add `routes/configs.py` and `migrations/env.py` to coverage (0 errors).
   b. T2 `maybe_notify` refactor: `routes/bulk.py` 1 error plus 4 ignores, so bulk joins strict.
   c. T1 `dict[Any, Any]` in `schemas.py`: removes 6 ignores, golden-identical.
   d. T3, T4, T7, T9 lazy ignores: 4 ignores.
   e. `EffectiveConfig` TypedDict for `get_effective_config`: 2 casts now, and it pre-clears 3 engine errors.
   f. Typed `app.state` accessors plus `_run_pump_watcher(app: FastAPI)`.
2. **With or after WP8** (58 errors).
   a. `devices/*` small modules: 10 errors, mostly `dict` → `dict[str, Any]` and the `ik10pw` `float | None` timeout.
      Fix that one at the call site's declared type and verify `open_local`'s None handling; do **not** change the default.
   b. `devices/gateway.py`: 28 errors. Add a `_TuyaCloudClient` `Protocol` (`cloudrequest`, `getstatus`, `getdevicelog`,
      `sendcommand`, `getdevices` → `dict[str, Any]`) for `self._cloud`. Type the DP converter table as
      `dict[str, Callable[[Any], tuple[str, object]]]`, since its values are raw cloud JSON.
   c. `logic/timing.py`: 2 errors.
   d. `core/sync.py`: 6 errors, plus `SyncStats`/per-cluster TypedDicts, which also type `services/sync.py:46`.
   e. `logic/engine.py`: 11 errors. Annotate the 4 helpers, use `Literal` for `cluster.environment` via a narrow at the
      call, and `dict[str, Any]` at :678.
3. **Retire the framework-boundary override** (210 errors, mechanical).
   a. `cli/commands/*` + `cli/main.py` (62): add `-> None` to Typer commands. Typer ignores return annotations, and the CLI help golden must be unchanged.
   b. `server/routes/*` (58): annotate `-> <the declared response_model type>`. Where a handler returns a service dict,
      annotate the TypedDict/`dict[str, Any]` it actually returns; `response_model` keeps winning. Binary or CSV
      endpoints get `-> Response`. Fix the 3 untyped params and 6 bare generics.
   c. `web/routes/*` (90): annotate `-> HTMLResponse`, `-> RedirectResponse` or `-> Response`. **Never annotate a union
      of Response classes** (`HTMLResponse | RedirectResponse`): FastAPI raises `FastAPIError: Invalid args for response
      field!` at import (verified). Use the common base `Response` for the 11 modules that mix redirects and HTML.
   d. Delete the `[[tool.mypy.overrides]]` framework block.
4. **Third-party stubs.** Replace the global `ignore_missing_imports` with a targeted override for `tinytuya.*`,
   `apscheduler.*` and `fastapi_mcp.*`, the only 3 untyped imports found. This way a new untyped dependency fails loudly
   instead of silently becoming `Any`.
5. **Flip to OD5.** Put the config below in place, set the Makefile to `typecheck: ## mypy strict over libs` → `uv run mypy`,
   delete `refactor/mypy-strict.txt`, and update the `[tool.mypy]` comment. `follow_imports = "silent"` goes away: it was
   only needed while checking a subset.

Optional after step 5, each with owner-rule-3 proof per hit: enable `warn_unreachable` and
`enable_error_code = ["redundant-expr", "truthy-bool"]` (8 hits listed in §1.6). These hits are defensive guards on device
data, not type cleanups.

### 3.2 Interim (if step 3 lands later than step 5)

Keep the override block, but scope it to `disallow_untyped_defs = false` and `disallow_incomplete_defs = false`. Drop
`disallow_any_generics = false`: only 6 bare generics remain, and they are fixable in step 3b/3c.

### 3.3 Final `[tool.mypy]` (replaces `refactor/mypy-strict.txt`)

```toml
[tool.mypy]
# Strict over the whole workspace (OD5). `make typecheck` == `uv run mypy`.
python_version = "3.11"
strict = true
files = ["libs"]
# Alembic revisions are frozen and have digit-led (non-importable) names.
exclude = ['^libs/greenhouse-core/greenhouse_core/migrations/versions/']
# The workspace packages ship no py.typed; resolve them from source so they type-check instead of becoming Any.
mypy_path = ["libs/greenhouse-core", "libs/greenhouse-server", "libs/greenhouse-cli"]
explicit_package_bases = true
enable_error_code = ["ignore-without-code"]

[[tool.mypy.overrides]]
# Third-party packages with neither stubs nor py.typed.
module = ["tinytuya", "tinytuya.*", "apscheduler.*", "fastapi_mcp", "fastapi_mcp.*"]
ignore_missing_imports = true
```

This config was trial-run as-is (plus the opt-in leads codes). It discovers all 164 libs source files (excluding the
revisions), takes 11 s, and its only errors are the 283 itemised in §1.1/§1.6. There were no module-discovery or
duplicate-module issues.

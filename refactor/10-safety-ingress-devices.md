# 10 — Phase 1 safety net: ingress-devices (devices/, sync, weather, health monitor, pump watcher, auth)

Work package **ingress-devices** (high risk: hardware-facing). Gap items closed: **I1, I8a, I8b, I8c, W1**, plus
adapter contract coverage, pump-watcher shutdown hand-off, and the auth gaps. No production file, existing test,
fixture, conftest or fake was touched. Nothing committed.

## New files

| File | Tests | Goldens (`tests/golden/ingress_devices/`) |
|---|---:|---|
| `tests/devices/test_contract_adapters.py` | 90 | `profiles_parsers_registry.json`, `adapter_call_sequences.json` |
| `tests/test_contract_sync.py` (core) | 24 | `core_sync_all_clusters.json` |
| `tests/server/test_contract_sync_service.py` | 15 | — |
| `tests/server/test_contract_health_monitor.py` | 40 | `health_monitor_sensor_transitions.json`, `health_monitor_alarm_rows.json` |
| `tests/server/test_contract_pump_watcher.py` | 22 | — |
| `tests/server/test_contract_weather.py` | 22 | — |
| `tests/server/test_contract_auth.py` | 81 | — |
| **Total** | **294** | 5 pretty-JSON goldens (sorted keys; list order = behavior) |

Doubles are written inside each test module: `RecordingCloud` (injected via `DeviceGateway(raw=…)`) + a recording
`OutletDevice` patched at `greenhouse_core.devices.tinytuya.OutletDevice`, sharing **one call log** so the interleaving
of local and Cloud traffic is part of the contract; `FakeGateway`/`RecordingGateway` for sync; `NoCloud` (fails on
any attribute access) for the health monitor; a fake APScheduler + fake `_app` for the pump-watcher job body; a
`urlopen` recorder + fake `time.monotonic` for weather. Branch coverage of the target modules from these files alone:
`core/sync.py` 100 % (was 0 %), `services/weather.py` 100 % (20 %), `services/sync.py` 100 %, `core/auth.py` 100 %
(50 %), `server/auth.py` 99 %, `devices/gateway.py` 99 %, `ik10pw.py` 97 % (61 %), `tuya_generic.py` 99 % (44 %),
`tr301z.py` 100 % (17 %), `health_monitor.py` 93 %, `pump_watcher.py` 75 % (rest pinned by existing `test_pump_watcher.py`).

## What is pinned (test → contract / invariant)

**Devices (`test_contract_adapters.py`)**
- `test_invariant_1_ik10pw_profile_is_protocol_3_5`, `TestAdapterInvariants::test_ik10pw_local_paths_use_profile_protocol_version`
  → **invariant #1** (JSON + typed profile = 3.5; start/status/read_health all call `open_local(irr, 3.5)`; DP 1/102/105, bitmask 0x01).
- `test_profiles_parsers_and_registry_golden` → both profile JSONs, typed profiles, `DATAPOINT_PARSERS` code order and
  the full parser matrix on 12 sample raws (incl. `bool("false") is True`, `int(12.7)==12`, which raws raise), registry
  keys, legacy aliases, per-adapter `health_capabilities`, `KEEP_ALIVE_INTERVAL=20`, `LOCAL_TIMEOUT=5`.
- `test_adapter_call_sequences_golden` → 31 scenarios: return tuple/dict **verbatim** + exact call log for on/off/stop,
  start(None) / start(5) / start(2.5) (DP 102 = `int(minutes*60)`), local DP ok + Cloud switch fails, `set_value`
  error payload / `None`, status local / no-`dps` / local raises / no IP + Cloud fails, read_health (bit set, string DP,
  `None` status, local raises, no IP), generic cloud-only and local-without-capability adapters, TR301Z `read_live` v2 / errors.
- `TestAdapterInvariants::test_ik10pw_bounded_start_is_local_dp102_over_3_5_then_cloud_switch`,
  `::test_switch_pulse_goes_via_cloud_only` → the recipe order: `OutletDevice → set_version(3.5) → set_socketTimeout(5)
  → set_value(102, s) → Cloud sendcommand switch=True`.
- `TestLiveReadSingleCall` → **invariant #8** single-call: v2 success = 1 call; v2 empty-but-successful (3 shapes) = 1
  call, `{}`; v1 `getstatus` only for `success:false` / missing key / `None` / exceptions; both fail → exact
  `RuntimeError("Cloud API error: …")`; v2 parser error is *not* caught; later DP wins per canonical key.
- `TestLocalKeyResolution` → **invariant #8** `open_local`: config dict / JSON-string key → **zero Cloud calls**;
  cold lookup once then process cache; `refresh=True` forces `getdevices`; resolution order config → cache → Cloud;
  unknown device not cached; hook failure logged; exact `ConnectionError` messages; no-IP never touches Cloud.
- `TestDeviceLogs` → exact `getdevicelog(id, start, end=0, evtype=7, size=100)`, default window from frozen now,
  parse-error fallback, sort; grouping tolerance boundary (5000 same / 5001 new), key-less intermediate group kept,
  trailing one dropped. `TestGatewayConstruction` → one `tinytuya.Cloud(apiRegion, apiKey, apiSecret)`, env fallback,
  region default `eu`, `raw=` injection, exact missing-credentials message.
- `TestKeepAliveMainThread` → keep-alive (Cloud re-pulse every 20 s): exact sleeps `[20,20,20]` / remainder
  `[25,25,10]`, on×3 + off, SIGTERM handler restored; SIGTERM mid-cycle → **off sent twice**, exact "interrupted after
  ~20s" message; `KeyboardInterrupt`; initial-on failure installs nothing; swallowed errors; `minutes=0`; fallback log line.
- TR301Z `read_health` matrix (battery buckets incl. case-insensitivity/unknown, water_warning → SENSOR_FAULT,
  `last_seen_ts`, raw payloads) with an **empty call log**; `alarm_indicates_no_water` custom bitmasks + Hypothesis
  (int and decimal-string agree with `value & mask`). Registry: fresh adapter per lookup sharing one gateway, exact
  `UnknownDeviceModel` text, exact unknown-sensor warning.

**Core sync (`test_contract_sync.py`)** — I8b: first-sync window `(now − hours·3600)·1000`, later window
`(last_ts − 60)·1000`; exact gateway call sequence (logs → group → **one** live read) per sensor; dedup on
`(sensor_id, timestamp)` (archived row wins); readings with falsy timestamp skipped but counted; log-derived rows do
**not** store `water_warning` (live rows do); live guard key set; live errors swallowed; log errors propagate; stats
dict shape; error string `"<name>: <exc>"`; cluster order by name, empty clusters skipped; exact INFO/ERROR log lines
(golden); sync never commits.

**SyncService (`test_contract_sync_service.py`)** — I8a: fresh → zero gateway calls; boundary at exactly
`SENSOR_READING_STALE_SECONDS` is **fresh** (strict `>`); stale → exactly one `sync_single_sensor(…, hours=6)` for that
sensor only; missing reading → stale; all stale → name order; no Cloud → no sync; failure swallowed with exact DEBUG
line; snapshot aggregation; `sync_all_sensors` payloads.

**Health monitor (`test_contract_health_monitor.py`)** — I8c: newest persisted row handed to `read_health`, `NoCloud`
untouched, fake adapters record no `read_live`; transition golden (battery low → water warning → recovery → stale at
exactly 30 min (not offline) / +1 s (offline) → back online) with alert rows + notifications after each poll; every
alarm's title/message/severity/payload (golden); threshold boundaries (battery 20/19/5/4, signal 30/29, staleness
1800/1801, offline flag); actuation gate set; label/cluster inference and fallbacks; poll/alert-write failure logging;
back-fill windows (newest 5 only, ≥ 7 days ignored, no duplicates, cache not seeded).

**Pump watcher (`test_contract_pump_watcher.py`)** — every `schedule_pump_watcher` skip branch; exact job
(`"date"`, run_date, id, name, `replace_existing`); watcher built from settings / defaults, `stop_requested` /
`sleep` = scheduler shutdown hooks, monitor re-bound; **shutdown hand-off**: auto → stop + `IrrigationEvent(stop,
shutdown)` + activity + WARNING; manual → untouched + activity + WARNING; failed / raising auto stop → ERROR +
activity, no event; completed watch records nothing; deleted irrigator; job failure rolled back + logged; exact
interrupted result; exact trip notes/message/payload; every trip side-effect failure isolated and logged.

**Weather (`test_contract_weather.py`)** — W1: exact URLs incl. `&timezone=<tz>` (unencoded), timeout 8; current /
forecast mapping and aggregates; window sized by precipitation length; empty → `None` (not cached); failures → `None`;
TTL strict at 600 s; independent caches; app wiring from `weather_lat/lon` + startup tz.

**Auth (`test_contract_auth.py`)** — Argon2 matrix with fixed-salt hashes (verify/needs_rehash incl. argon2i, old
params, garbage); user helpers under frozen clock; JWT claims/header, `now`/TTL, **expiry boundary** (exp−1 valid,
exp expired), 8 "Invalid session" variants, 503 `auth_secret_key is not set`; `require_user` 11-case HTTP matrix
(header beats cookie, MCP token accepted as bearer **and as cookie**, non-bearer scheme → "Not authenticated");
login body/cookie string/`last_login_at`/rehash/validation 422s; logout cookie deletion; web redirect quoting
(`/login?next=/clusters%3Fa%3D1%26b%3Dx%2520y`) and HX 204; bootstrap; MCP gate 503/401 matrix incl. `mcp_token=""`
→ 401 (closed, not 503), stripped header value, case-insensitive scheme.

## Observed bugs / quirks — pinned, NOT fixed (orchestrator: please record in REFACTOR_NOTES.md)

| Bug | Pinning test |
|---|---|
| **B-2** keep-alive `signal.signal` off main thread → `ValueError` after ON; OFF never sent | `test_keepalive_off_main_thread_current_behavior_leaves_pump_on`, `test_start_off_main_thread_current_behavior_raises_after_switching_on` |
| **B-3** sensor flagged DEVICE_OFFLINE 31 min after every 180-min sync (reproduced; alert re-opens, `occurrence_count` 2, notifies once) | `test_sensor_offline_flap_current_behavior_flags_offline_31min_after_every_sync` |
| **B-21** live reading with only `env_humidity` dropped | `test_env_humidity_only_live_reading_current_behavior_is_dropped` |
| NEW: `verify_password` raises `VerificationError` on a truncated hash (docstring promises `False`) → login 500 | `test_verify_password_current_behavior_raises_on_truncated_hash` |
| NEW: forecast cache not keyed by `hours` | `test_forecast_cache_ignores_hours_current_behavior` |
| Known (00-contracts §1.2): JSON handler drops `WWW-Authenticate` | `test_api_401_current_behavior_drops_www_authenticate_header` |

Other quirks pinned as plain behavior: SIGTERM path sends OFF twice; v2 parser errors uncaught; log rows never store
`water_warning`; `"humidity"`-only live row stored with all metrics `None`; negative `hours` slices weather from the
end; `mcp_token=""` → 401 not 503; MCP token accepted via cookie.

## Not pinned (and why)
- Real SIGTERM delivery / real 30 s device auto-off — hardware/OS; handler invoked synchronously instead.
- Scheduler job bodies `_sync_job` / `_health_monitor_job` / `init_health_monitor` — scheduler work package (I-iii).
- `migrate_legacy_pump_alerts`, abandon/warm-up loop — already pinned by existing tests.
- Exact JWT strings — compared by decoded claims (library serialization is not ours).

## Determinism (all green, goldens unchanged)
- `GOLDEN_UPDATE=1` once, then `uv run pytest <7 files>` ×2 → 293 passed each; `-p xdist -n 2` → 293 passed;
  `TZ=America/New_York` → 293 passed; `PYTHONHASHSEED=1` and `=4242` (frozenset order) → green. (+1 test added after: 22/22.)
- No normalization used. Alerts sorted by dedup key and frozensets sorted (hash-seed dependent); ids never asserted.
- Existing area tests (`tests/devices`, `test_cloud`, `test_health_monitor`, `test_pump_watcher`, `test_auth`, `test_mcp`,
  `test_rate_limit`, `test_sync_snapshot`, `test_scheduler_shutdown`, `test_golden_kit`) → 229 passed.
- `ruff check` + `ruff format` clean. Note: `tests/golden/` and `tests/**/test_contract_*.py` are in `.git/info/exclude`.

## Production lines these tests guard (mutation targets)
- `devices/gateway.py`: 116-121 (env + creds check), 124-131 (single Cloud ctor), 156-183 (v2 → v1 fallback; success
  gate; RuntimeError text), 189 + 201-206 (default window, `end=0`, `evtype=7`, `size`), 226-235 (parse guard), 245 sort,
  70-94 (grouping `>` tolerance, `len>1`), 251-254 (send_command tuple), 276-293 (key order, cache, hook), 296-301,
  307-333 (IP/key errors, `set_version`, `set_socketTimeout(LOCAL_TIMEOUT)`).
- `irrigators/ik10pw.py`: 33, 54-68, 109-116, 131-150, 160-202 (B-2 lines 160/177/190-195), 221-250.
- `irrigators/tuya_generic.py`: 34-37, 64-91, 97, 107-110. `sensors/tr301z.py`: 68-72, 118-148. `sensors/tuya_generic.py`: 185-188.
- `registry.py`: aliases, 387, 399-404. `profile.py`: `dp`, `has_capability`. Profile JSONs (`protocol_version`, `dp_map`).
- `core/sync.py`: 26-57 (stats, error string, log parts), 64-70 (windows), 73-92 (dedup/count, `if not ts`), 96-115 (guard keys, `now`, `water_warning`, swallow).
- `services/sync.py`: 58-76 (`> SENSOR_READING_STALE_SECONDS`, `hours=6`, swallow+debug, flush), 89-92.
- `services/health_monitor.py`: 152-158, 189-213, 224-232, 243-274, 306-320 (thresholds/`>`), 332-359, 381-416.
- `services/irrigation.py`: 30-120 (`handle_watcher_interrupted`), 123-232 (`schedule_pump_watcher` + `_run`).
- `services/pump_watcher.py`: 77-83, 117-180, 213-297. `services/weather.py`: all (URLs 32-37/64-70, TTL `<` 29/61, 80-95).
- `core/auth.py`: 21-76. `server/auth.py`: 141-206, 228-296, 299-353, 359-373, 379-423. `app.py`: 97-120 (`is None` → 503, `!=` → 401).

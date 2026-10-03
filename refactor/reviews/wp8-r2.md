# WP8 review R2: adversary, differential testing (`327ee12` → `15dcd2e`, `refactor/wp8-engine`)

**Verdict: APPROVE, no blockers.** I found one observable difference, and only with corrupt data (F1). Two monkeypatch seams moved, and the commit bodies declare both (F2). Everything else showed **zero differences**.

R1 already covered the grid plus repo-call faults (`wp8-r1.md`), so this review targets what R1 did not cover:
- randomized states well beyond the grid;
- SQL-level read/flush faults and plant-DB faults;
- the device call logs;
- every patch seam on `engine`.

## Setup

- **Trees:** two detached worktrees, `/home/user/gh-r2-base` (`327ee12`) and `/home/user/gh-r2-head` (`15dcd2e`). Both are now removed.
- **Interpreter:** one interpreter, `/home/user/gh-wp8/.venv`. `PYTHONPATH` selects which tree's `libs/greenhouse-core` is loaded.
- **Shared harness:** both trees use the *same* harness and the base `tests/engine_grid.py`.
- **Constants:** `constants.py` is byte-identical in the two trees.

### Harness files (scratchpad `/tmp/claude-0/-home-user-greenhouse/13425f2d-7c47-5b83-96d4-7045d3907ad2/scratchpad/wp8r2/`)

- **`harness.py`:** the main driver. Each run uses:
  - real SQLAlchemy on in-memory SQLite (StaticPool, savepoint session);
  - a frozen clock (`time_machine`, `tick=False`);
  - `engine_grid._seed` to build the state.

  It also wraps three things:
  - **`RecordingRepo`:** a subclass whose every public method records name, nesting depth, args, result, exception and fault, using load-free summaries via `sa_inspect(...).identity`.
  - **`PlantOverlay`:** malformed targets, seasonal tables and `_category_defaults`.
  - **A recording or raising weather client.**

  It captures every SQL statement and its params (`before_cursor_execute`), and it can make the *k*-th statement raise `sqlite3.OperationalError("database is locked")`.

  **Compared per run:**
  - the full `IrrigationDecision` dump: action, duration, interval, confidence, and reasons with code, text, order, icon and deltas;
  - `decision_log_id`;
  - the exception type and message;
  - `session.is_active` and `in_transaction()`;
  - **every row of every table**, with `payload_json` parsed;
  - the ordered repo/plant-DB/weather call log;
  - the ordered SQL log;
  - every `greenhouse*` log record, including exc type and message.
- **`compare.py` and `compare_grouped.py`:** record-level structural diff (the grouped version tolerates a different number of fault runs per case).
- **`seams.py` and `seam_compare.py`:** the patch-seam differential.
- **`ast_cmp.py`:** the device AST check.
- **`devrec.py` and `devnorm.py`:** the device call-log check.
- **Reproducers:** `repro_vacation_order.py` and `repro_seams.py`.
- **Output:** everything lands in `out/`, including `compare.txt`, `mutants.txt`, `seams-compare.txt` and `repro_*.txt`.

### Generator (`rand_case`, seed 20261003)

- **Time:** instants spread over 2025–2027, about 30% of them within ±2 h of a DST or season boundary.
- **Timezone prefs:** `None`, UTC, Rome, New York, Sydney, Kolkata, Chatham, and an invalid zone.
- **Environment:** indoor, outdoor or greenhouse.
- **Plants:** 0–3 per cluster, from 12 species/category pairs (including unknown species and bad categories).
- **Plant-DB overlays:**
  - `soil_moisture_target` values `"abc"`, `None`, `""`, `"70-40"`, `"50"`, `45`, `"-5-20"`, or deleted;
  - season tables (partial, empty, `None` or deleted), indoor and `_outdoor` variants;
  - `water_needs` values that are bogus or `None`.
- **Sensors:** 0–3 per cluster with 0–8 readings each. Readings include:
  - ages across the 24 h snapshot edge and stale readings (3 d and 8 d);
  - soil on every band boundary, `None`, −1 and 101;
  - temp, humidity and light extremes;
  - `water_warning`;
  - readings inserted in descending order.
- **Events:** cooldown history at 6 h exactly and ±1 s, with actions start, stop, `schedule_updated` and skip.
- **Quiet hours:** global and cluster level, including wrap-around and `start == end`.
- **Cluster config rows:** mode, duration, interval, `auto_run`, caps.
- **Irrigation windows:** none, or 1–2 windows (including mask 0 and `end == 24`).
- **Vacations:** start and end offsets on the boundaries, with reservoir capacity (`None`, 0, negative, small, large) × flow (`None`, 0, small, large) and spent consumption.
- **Leak alerts:** open, acknowledged or resolved; 24 h ±1 s; other cluster.
- **Weather:** none, offline, a raising client, or precipitation `None`, 0, 2.0, 2.0001, negative or missing.
- **Call arguments:** `current_temp`, `bypass_quiet_hours`, `triggered_by`.
- **Learning seeds:** `blocked_drip`, `rapid_drainage`, `conflict`.
- **Other:** `expire_all` before the evaluation, and a missing cluster.
- **Deep-path bias:** 60% of cases switch the terminal gates off, so the soil, seasonal and vacation rules are reached.

Every `TriggerCode` the engine can emit appeared, including `vacation_rationing` and `vacation_budget_exhausted`.

**Boundary set:** 53 hand-picked cases written with raw SQL. They store values the API would reject:
- reservoir or flow as `'abc'`, `'inf'`, `1e309`, `1e-320`, negative or a blob;
- quiet bounds as `'abc'`, `'3'`, `2.7`, `24` or −1;
- timezone as `''` or a bogus zone, and odd environments;
- precipitation as a str, list, dict, bool, NaN or inf;
- odd leak `last_seen_at` and odd vacation timestamps.

**Sensitivity:** I applied 5 mutants to a copy of the head engine and ran 800 random cases (1,600 runs) against base. All 5 were caught:
- `get_preferences` moved before `get_effective_config`: 1,344 runs differ.
- floor instead of ceil in the days-left calculation: 102 differ.
- `_record` called before the override reason is added: 593 differ.
- quiet-skip severity changed: 112 differ.
- `get_sensors_in_cluster` moved before `_gather_inputs`: 1,112 differ.

## Results

| Area | Runs | Differences |
|---|---|---|
| Golden grid (1,432 cases × persist on/off) | 2,864 | **0** |
| Random cases (6,000 × persist on/off; 260 runs raise) | 12,000 | **0** |
| Boundary set (53 × 2) | 106 | **6**, all F1 |
| Fault injection, random (400 cases): 10,002 repo-call faults, 10,760 SQL faults (9,321 on SELECT, **661 on INSERT/flush**, i.e. decision-log and prefs-row flush failures), 1,183 plant-DB faults | 22,377 | **0** |
| Fault injection, boundary (53 cases) | 2,164 | **27**, all F1 |
| Patch seams: 89 seams × 244 cases | 28,792 | **0 on names present in both trees** |
| Device call log (384 tests per tree, 3,960 recorded calls) | — | **0** |

### Notes on the fault injection

- **Commit:** the engine never commits; `add_decision_log` and `get_preferences` only flush. The INSERT faults therefore cover the flush-failure path:
  - savepoint rollback;
  - `_persist`'s best-effort swallow, with the warning log identical in both trees;
  - a `get_preferences` flush failure propagating.
- **Specific paths:** faults at every repo-call and SQL index cover `_record`, `_finish(override_window=…)`, `_pre_gates`, `_quiet_hours_skip`, `_apply_vacation_ration` and `_tz_name`. The exceptions, the rows left behind and the session state are identical in both trees.

### F1 (T8.10 `5b66434`, informational, non-blocking): with a non-numeric `irrigators.reservoir_l`, head runs the consumption query before raising

- **When:** only when `reservoir_l` is stored as TEXT or BLOB (for example `'abc'`) in its REAL column.
- **Base:** raises `TypeError` in `usable_l = reservoir_l * 0.8` *before* `irrigator_consumption_liters`.
- **Head:** issues that repo call and its `SELECT … irrigation_events` first, then raises the **same** `TypeError` from `_binding_max_minutes`.
- **What is the same:** the exception, the persisted rows and the session state are identical. Only the repo-call and SQL trace has one extra read. Under fault injection that shifts the call indices: there is one extra `repo25` fault run that hits the consumption query.
- **Reproducer:** `repro_vacation_order.py` (output in `out/repro_vacation_order.txt`):
  - base: `TypeError … statements: 25`, last statements `SELECT vacation_windows`, `SELECT irrigators`;
  - head: same `TypeError`, `statements: 26`, last statement `SELECT irrigation_events`.
- **Reachability:** none through the app. The API and Pydantic validate floats, and SQLite REAL affinity converts numeric text such as `'5'` to 5.0. Numeric extremes (inf, NaN, negative, tiny flow) raise or behave identically in both trees, because base's `floor` also came after the query.
- **Why not a blocker:** the commit body's claim is explicitly scoped to "the truthy **numeric** capacity", and it holds as stated. Suggested hand-off wording: "non-numeric capacity raises the same TypeError one read later".

### F2 (T8.1 `d1df481`, T8.11 `3cd1ecb`, declared, non-blocking): two monkeypatch seams relocated

`repro_seams.py`, output in `out/repro_seams.txt`:

| Patch target | base | head |
|---|---|---|
| `engine.is_within_quiet_hours` | steers the engine | attribute gone (`monkeypatch.setattr` raises `AttributeError`) |
| `timing.is_within_quiet_hours` | no effect | now steers the engine |
| `engine.parse_moisture_target` | steers the engine | attribute gone |
| `plant_needs.parse_moisture_target` | no effect | now steers the engine |

- **Declared:** both commit bodies state that the engine import was dropped and that no test patches the name. A grep confirms it: neither name is patched anywhere in `tests/`.
- **Not stated:** the reverse direction (patching `timing` or `plant_needs` now reaches the engine). This is worth one line in the hand-off.
- **New names on `engine`:** `SECONDS_PER_HOUR`, `SNAPSHOT_LOOKBACK_HOURS`, `WEATHER_*`, `CONFIDENCE_BASELINE` and the extracted helpers are new, additive seams.

### Patch seams requested (task 4): identical

The seams were applied after seeding, in both trees, with the same values. Every one gave 0 differences over 244 cases:
- `engine.time.time`, set on the shared `time` module the way `test_scheduler` does it;
- `engine.season_for` forced to each of the 4 seasons;
- all **50 constants** in `vars(engine)` that both trees share, each perturbed (int → 2v+1, float → v/2+0.05, str → `"10-20"`);
- **32 shared callables**, each wrapped with a call counter. These include `_decision_with_reason`, `_apply_soil_moisture_rule`, `seasonal_multiplier`, `get_recent_sensor_data` and `temperature_based_decision`. The wrappers fire with identical counts and at identical points in the trace.

### Devices (task 3): annotation and docstring changes only

- **AST check (`ast_cmp.py`):** I stripped docstrings, annotations and `TYPE_CHECKING` blocks from the 6 changed files. The ASTs are identical apart from two kinds of change:
  - `from typing import Any`, added in 5 files;
  - `profile.load_profile_json`, where `return json.loads(…)` became `raw = …; return raw`.
- **Dataclass field:** the one annotated dataclass field (`DeviceHealthState.raw`) already existed; only its type annotation changed.
- **Call log (`devrec.py`):** this pytest plugin wraps every class in `greenhouse_core.devices.*` and `tinytuya.{Cloud, Device, OutletDevice, BulbDevice, XenonDevice}`. I ran it over 11 files: `tests/devices`, `test_cloud`, `test_contract_sync`, and these server tests: `pump_watcher`, `contract_health_monitor`, `health_monitor`, `system_health`, `contract_pipeline`, `contract_sync_service`, `sensors`, `irrigators`.
  - Result: 3,960 calls, identical in both trees once the wall-clock `observed_at` values are normalised.
  - Two tests failed identically in both trees under the plugin. The cause is their Cloud-guard object meeting my recorder; both pass without the plugin.

## Verdict

**APPROVE.** There are no blockers. As non-blocking hand-off notes:
- reword the F1 sentence;
- add a line for the reverse direction of F2.

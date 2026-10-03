# WP6 adversarial review: core logic + learning + stats

**Verdict: APPROVE.** I found no observable difference between OLD and NEW.

- Base (OLD): `090b5fe` (merge-base with `claude/focused-hawking-7to7o3`).
- Head (NEW): `e59e971` (branch tip; production code is identical to `8647612`, and the last two commits only add tests and docs).
- Production files changed: `utils.py`, `stats.py`, `logic/{sensors,trends,stress,fallback}.py`, `learning/{issues,profiling}.py`. `profiling.py` is also in the diff even though the brief did not list it, so I covered it too.

## Method

I made scratch worktrees of base and head under `scratchpad/wp6-diff/{old,new}` and removed them afterwards. Each tree ran in its own subprocess, with `PYTHONPATH` pointing at that tree's `libs/*` and `tests` (I checked the import resolution), `PYTHONHASHSEED=0`, and the shared `.venv`.

### 1. Engine-level differential (`tests/engine_grid.py`)
- I ran all **1,432** cases from `build_cases()` through `run_case()` on each side. That covers the full `IrrigationDecision` and the `decision_logs` rows.
- `grid-old.json` and `grid-new.json` are **byte-identical** (sha256 `9cbbdcd2…1435`). No case raised an exception on either side.

### 2. Function-level differential (Hypothesis, `derandomize=True`, generate phase only, `database=None`)
Inputs were generated once by `gen.py`/`gen2.py`, written to JSON, and replayed by `run.py` in each tree. Results were written as canonical JSONL. The encoding keeps the int/float distinction, uses exact float `repr`, keeps dict and list ordering, records Pydantic `model_fields_set` and dataclass fields, and for each call records the exception type and message plus captured stdout.

| Corpus | Examples | Result |
|---|---|---|
| `world` (random DB-backed worlds) | 2,000 | identical |
| `stress` (synthetic snapshot/trends, real and fake plant DB, every month) | 3,000 | identical |
| `utils` (seasonal factor at months −2..15 and None, `effective_light_threshold`, `daytime_lux_readings`, `format_timestamp` with valid/invalid tz and env fallback, `format_duration`) | 2,000 | identical |
| `stats` (`print_stats_report` on synthetic dicts, including the error dict) | 2,000 | identical |
| `rich` worlds (biased toward conflicts, low light/humidity, drainage, blocked drip) | 3,000 | identical |
| deterministic sweep (cadence: 0–30 starts × durations × 7d edge; chronic: target_min ±1e-9/±1/equal, count 4/5/6, all-None soil, empty 7d, zero) | 1,425 | identical |

Total: **13,425** function-level worlds/examples, with zero differences (`full-*.jsonl` `ddfa2ab8…`, `rich-*.jsonl` `8c3ac8b0…`, `det-*.jsonl` `dc576dcc…`, matching pairwise). No example failed inside the harness itself.

For each world I called:
- `get_recent_sensor_data` (hours 0/1/6/24/48 and the default).
- `analyze_historical_trends`.
- `detect_stress_conditions`, fed the computed snapshot and trends.
- `temperature_based_decision`, both with and without trends/stress.
- `get_plant_profile` (default lookback and 7 days), `compute_drainage_rate`, `compute_sensor_response` for every event, and `analyze_irrigation_response`.
- `detect_issues`, twice: once with real learned profiles, and once with `issues.get_plant_profile` patched to return synthetic profiles. The patch drives blocked-drip, drainage and chronic checks at their exact thresholds.
- `detect_conflicts`, with real profiles and with synthetic ones, plus `plant_care` overrides that include unparsable targets.
- `get_irrigation_stats`, `print_stats_report`, and `export_csv` (file content and stdout).
- The same functions on a missing cluster.

After the calls I checked that the input objects were unchanged (snapshot, trends, stress, profiles, `plant_care`, fake plant-DB dicts, and the order of plant-DB calls) and that the DB was unchanged (readings fingerprint, event count, alert count).

What the corpus exercised (OLD and NEW counts are identical):
- Every alert type, including `unresolvable_conflict` 196, `chronic_underwatering` 703, `low_light` 1,429 and `low_env_humidity` 1,429.
- Every stress field and every trend label.
- Both cadence flags.
- All three fallback codes.
- The parity exceptions: a stress `TypeError` when care data holds `None` values, and a stats `ZeroDivisionError` at `days=0`.

Boundaries checked against T6.6 and the handoff's requests (a) and (b):
- **Equality:** when `max_recent == target_min`, no alert fires on either side.
- **All moisture values None:** `max_recent` stays the **int** `0` (not `0.0`) on both sides.
- **Positive-comparison guards:** behave the same at each threshold ±1e-9 for the −10 steep-decline delta, +5 °C heat offset, −20 humidity deficit, 0.4 light fraction, and saturated-soil threshold. The `elif` chains for water stress and over-watering behave the same.

### 3. Static checks
- **Module surface:** no public or private name was removed, and every function signature (parameter names, kinds, defaults) is unchanged. The only additions are helpers, constants, `Any`/`cast`/`TYPE_CHECKING`, and `annotations` (from `from __future__ import annotations`).
- **Gap tests:** `tests/test_contract_wp6_gaps.py` and `gaps2.py`, run against the **base** code, give 65 passed. They are genuine characterization tests.
- I diffed every constant used against the literal it replaces: 15, 48, 7, 1, 2, 3, 4, 6, 20, 0.4, −10, 5, 6, 168, 3, 5, 5, 5, 15, 0.5, `"45-65"`, and the seasonal table. All match.

## Non-blocking notes (not behavioral)
1. In modules that now use `from __future__ import annotations`, `__annotations__` are stored as strings. `typing.get_type_hints()` on those functions would fail for names imported only under `TYPE_CHECKING` (for example `Sensor` and `CleanedReading`). No code in `libs/` or `tests/` introspects them.
2. `utils._SEASONAL_LIGHT_FACTOR` is now the *same dict object* as `constants.SEASONAL_LIGHT_FACTOR_BY_MONTH`, not a separate copy. That only matters if a caller mutates or patches one of the two, and nothing in the repo does.
3. The helper `_SensorReading` type alias exists only under `TYPE_CHECKING`. That is fine at runtime because annotations are not evaluated.

Artifacts are in `scratchpad/wp6-diff/`: `gen.py`, `gen2.py`, `gen3.py`, `run.py`, `grid.py`, `cov.py`, `surface.py`, `specs*.json`, `*-old/new.jsonl`, `grid-*.json` and `surf-*.json`.

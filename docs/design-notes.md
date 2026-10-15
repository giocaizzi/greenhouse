# Design notes

Behaviour that looks like a gap but is deliberate, each pinned by a test. Changing one is a labeled behavior change that updates exactly that test.

## Engine

- **Thresholds are bound at import.** Patching `constants.X` does not steer the engine; patch the name where it is used (`logic.timing.is_within_quiet_hours`, `logic.plant_needs.parse_moisture_target`). Pinned by `tests/test_invariants_engine.py::test_inv5_thresholds_are_bound_at_import_current_behavior`.
- **Cleaning is not idempotent.** Removing a spike changes the neighbours' medians (a Hampel property). Pinned by `tests/test_properties_logic.py::test_clean_readings_is_not_idempotent_current_behavior`.
- **A lone high glitch at the newest end of a short series is kept** (`10, 10, 99`), so a dry plant can skip one cycle. Dropping a newest-end rise is not safe either: a genuine rise (rain outdoors, an unlogged manual watering) looks the same. The next reading settles it: a revert makes the 99 an interior spike, which the guard drops. See `cleaning._short_series_outlier_mask` and `tests/test_cleaning.py::test_short_series_keeps_steps_trends_and_unconfirmed_end_points`.

## Actuation

- **A manual start without `minutes`** switches the pump on with the device's own timer as the only bound: no dry-run watcher (it needs a deadline) and no leak check (a `leak_check_skipped` activity row says why).

## Tests

- **The wall clock is read at call time** (`int(time.time())`); tests freeze it. The baseline migration seeds quiet hours 00:00–05:00 into every app, so golden tests use `FROZEN_INSTANT` outside them.
- **The server `app` fixture does not stub the weather client.** The hermetic guard gives such tests the offline 20 °C fallback; contract tests use `install_offline_weather`.

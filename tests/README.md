# Tests

93 tests across 8 suites. Run with `make check` or `uv run pytest`.

| Suite | Tests | Coverage |
|---|---|---|
| `test_db.py` | 16 | DB ops, dedup, readings-around, bulk insert, environment, migrations |
| `test_logic.py` | 16 | Decisions, multi-sensor conflict, water needs, cooldown, stress |
| `test_devices.py` | 8 | Device control, sensor parsing, error handling |
| `test_cloud.py` | 8 | Cloud API parsing, log grouping, v2 shadow, credentials |
| `test_learning.py` | 9 | Absorption profiles, drainage, reports |
| `test_utils.py` | 13 | Seasonal light, timestamp formatting, timezone |
| `test_plant_db.py` | 12 | Species/category lookup, fallback, singleton |
| `test_stats.py` | 8 | Statistics aggregation, CSV export, duration formatting |

All tests use fake data from `fake_data.py` and temp databases (no real API calls).

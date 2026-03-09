# Tests

49 tests across 5 suites. Run with `./test.sh` or `python3 tests/run_tests.py`.

| Suite | Tests | Coverage |
|---|---|---|
| `test_db.py` | 12 | DB ops, dedup, readings-around, bulk insert, environment |
| `test_logic.py` | 14 | Decisions, multi-sensor conflict, water needs, stress |
| `test_devices.py` | 8 | Device control, sensor parsing, error handling |
| `test_cloud.py` | 6 | Cloud API parsing, log grouping, credentials |
| `test_learning.py` | 9 | Absorption profiles, drainage, reports |

All tests use fake data from `fake_data.py` and temp databases (no real API calls).

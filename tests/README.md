# Test Suite

Test coverage for smart irrigation system using Python's `unittest` framework.

## Running Tests

```bash
# All tests
cd ~/.openclaw/workspace/skills/tuya-irrigation
python3 tests/run_tests.py

# Specific test file
python3 -m unittest tests.test_db
python3 -m unittest tests.test_logic
python3 -m unittest tests.test_devices

# Specific test case
python3 -m unittest tests.test_db.TestDatabase.test_cluster_creation
```

## Test Coverage

### Database Tests (`test_db.py`) - 9 tests
- Cluster creation & listing
- Plant profiles
- Irrigator & sensor registration
- Sensor readings storage
- Irrigation event logging
- Config management & updates
- Unique constraint enforcement

### Logic Tests (`test_logic.py`) - 11 tests
- Temperature-based fallback decisions
- Soil moisture-driven irrigation
- Confidence scoring (sensor vs no-sensor)
- Water needs adjustments (low/medium/high)
- Edge cases (no plants, nonexistent cluster)
- Temperature thresholds (cold/moderate/hot)

### Device Tests (`test_devices.py`) - 8 tests
- Credential validation
- Irrigator control (on/off/start/stop)
- Sensor reading & parsing
- Error handling
- Local vs cloud mode routing
- Duration parameter passing

**Total: 28 tests, ~1.7s runtime**

## Test Philosophy

✅ **Test behaviors, not implementation**
- Focus on contracts and outcomes
- Mock external dependencies (Tuya API)
- Use temporary databases for isolation

✅ **Clear and maintainable**
- Descriptive test names
- One assertion per behavior
- Minimal setup/teardown

✅ **Fast and reliable**
- No network calls (mocked)
- No real devices needed
- Isolated database per test

## Adding New Tests

When adding features, add tests that cover:

1. **Happy path** - feature works as intended
2. **Edge cases** - empty data, boundary values
3. **Error handling** - invalid input, failed operations

Example:
```python
def test_new_feature(self):
    """Feature works with valid input."""
    result = do_something_new()
    self.assertEqual(result, expected_value)

def test_new_feature_edge_case(self):
    """Feature handles empty input gracefully."""
    result = do_something_new(empty_input)
    self.assertIsNone(result)
```

## CI/CD Integration

To run tests in CI:

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    cd skills/tuya-irrigation
    python3 tests/run_tests.py
```

## Test Data

Tests use:
- Temporary SQLite databases (auto-cleaned)
- Mocked Tuya API responses
- Synthetic sensor readings
- No persistent state

## Known Limitations

- No integration tests with real Tuya devices (requires hardware)
- Parsing logic tested with mocked output (format may vary by device)
- CLI not tested (would require subprocess mocking)

Add integration tests when:
- Running on actual hardware
- Testing real Tuya device responses
- Validating end-to-end workflows

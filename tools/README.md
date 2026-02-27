# Tools Directory

Utility scripts for development, testing, and setup.

## Scripts

### `setup_kez_cluster.py`

Initialize your irrigation cluster with plants and irrigator.

```bash
python3 tools/setup_kez_cluster.py
```

Creates:
- Cluster name from `cluster.local.json`
- 4 plants (Monstera, Areca palm, Dracaena, Nespolo)
- Rainpoint IK10PW irrigator
- Initial irrigation config (2min every 12h)

### `sync_plant_data.py`

Sync existing plants with evidence-based data from plant database.

```bash
# Update all plants
python3 tools/sync_plant_data.py

# Update specific plant
python3 tools/sync_plant_data.py --plant-id 1

# Update specific cluster
python3 tools/sync_plant_data.py --cluster-id 1
```

Uses `data/plant_database.json` to populate temperature ranges, humidity requirements, and water needs.

### `test_trends.py`

Test historical trend analysis and stress detection with simulated data.

```bash
# Create test scenario and analyze
python3 tools/test_trends.py 1 --populate

# Analyze existing data
python3 tools/test_trends.py 1
```

Creates:
- 48h of declining soil moisture data (55% → 27%)
- Sparse irrigation pattern
- Demonstrates water stress detection

Output shows:
- Detected trends (soil moisture, temperature)
- Stress indicators (water stress, over-watering)
- Smart logic decision with confidence

### `add_test_data.py`

Populate database with fake irrigation events for testing reports.

```bash
# Add 7 days of test data
python3 tools/add_test_data.py 1 --days 7

# Custom duration
python3 tools/add_test_data.py 1 --days 30
```

Generates:
- 2 irrigations per day
- Alternating auto/manual triggers
- Varying durations (2-3 minutes)

Useful for:
- Testing statistics and reports
- Debugging logging system
- Demo purposes

## Usage

All tools use the irrigation package and work with the production database by default. Use `--db` flag to specify a different database for testing.

```bash
# Use test database
python3 tools/sync_plant_data.py --db /tmp/test.db
```

## Development

Add new tools following this pattern:

```python
#!/usr/bin/env python3
"""Tool description."""

import sys
from pathlib import Path

# Add src to path for package imports
skill_root = Path(__file__).parent.parent
sys.path.insert(0, str(skill_root / "src"))

from irrigation.db import IrrigationDB
from irrigation.logic import IrrigationLogic

def main():
    # Tool logic here
    pass

if __name__ == "__main__":
    sys.exit(main())
```

## Notes

- All tools modify the database — use with caution in production
- Test data tools (add_test_data.py) should only be used in dev/test environments
- setup_kez_cluster.py is idempotent — safe to run multiple times
- sync_plant_data.py updates plants in-place — no data loss

---

These tools streamline development and testing workflows. 🛠️

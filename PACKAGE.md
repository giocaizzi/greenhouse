# Package Structure & Development Guide

Modern Python package structure with `uv` and `ruff`.

## Package Structure

```
tuya-irrigation/
├── pyproject.toml          # Package configuration (uv + ruff)
├── src/                    # Package source (installed as 'tuya_irrigation')
│   └── tuya_irrigation/
│       ├── __init__.py     # Package exports
│       ├── cli.py          # Main CLI entry point
│       ├── db.py           # Database management
│       ├── devices.py      # Tuya device control
│       ├── logic.py        # Smart irrigation logic
│       ├── logger_daemon.py # Sensor logger daemon
│       ├── models.py       # Data models
│       ├── plant_db.py     # Plant care database
│       ├── report.py       # Report generator
│       └── stats.py        # Statistics module
├── scripts/                # OpenClaw compatibility wrappers
│   ├── main.py             # Wrapper for irrigation CLI
│   ├── logger.py           # Wrapper for logger daemon
│   ├── stats.py            # Wrapper for stats
│   └── report.py           # Wrapper for report
├── tests/                  # Test suite
│   ├── test_db.py
│   ├── test_logic.py
│   └── test_devices.py
├── data/                   # Plant database + SQLite
│   ├── plant_database.json
│   └── irrigation.db
└── docs/                   # Documentation
    ├── SKILL.md
    ├── PLANT_DATABASE.md
    ├── LOGGING.md
    └── TRENDS.md
```

**Package naming convention:**
- Distribution name: `tuya-irrigation` (PyPI-style with hyphen)
- Import name: `tuya_irrigation` (Python module with underscore)
- CLI entry points: `tuya-irrigation`, `tuya-irrigation-logger`

## OpenClaw Compatibility

The `scripts/` directory contains **wrapper scripts** that maintain full OpenClaw skill compatibility:

```python
# scripts/main.py (wrapper)
import sys
from pathlib import Path
skill_root = Path(__file__).parent.parent
sys.path.insert(0, str(skill_root / "src"))
from tuya_irrigation.cli import main
sys.exit(main())
```

**OpenClaw continues to call:** `python3 scripts/main.py [args]`

**Package users can call:** `tuya-irrigation [args]` (via entry point)

## Development Setup

### Prerequisites

- Python 3.11+
- `uv` (installed automatically if needed)

### Install for Development

```bash
# Clone/navigate to skill directory
cd ~/.openclaw/workspace/skills/tuya-irrigation

# Install with uv (creates .venv automatically)
uv sync

# Activate venv (optional, uv handles it)
source .venv/bin/activate

# Or run directly with uv
uv run irrigation --help
```

### Run Without Installation (OpenClaw Mode)

```bash
# Use wrapper scripts (no venv needed)
python3 scripts/main.py --help
python3 scripts/logger.py --help
python3 scripts/stats.py --help
```

## Code Quality

### Linting with Ruff

```bash
# Check for issues
uv run ruff check src/ tests/

# Auto-fix issues
uv run ruff check src/ tests/ --fix

# Format code
uv run ruff format src/ tests/
```

### Configuration

`pyproject.toml` includes ruff config:
- Line length: 120
- Target: Python 3.11+
- Enabled rules: pycodestyle, pyflakes, isort, flake8-bugbear, pyupgrade
- Formatstyle: double quotes, 4-space indent

## Testing

```bash
# Run test suite
./test.sh

# Or with uv
uv run python tests/run_tests.py

# Or specific tests
uv run python -m unittest tests.test_db
uv run python -m unittest tests.test_logic
```

All tests use package imports:
```python
from irrigation.db import IrrigationDB
from irrigation.logic import IrrigationLogic
```

## Package Usage

### As OpenClaw Skill (Current)

```bash
# All existing commands work unchanged
python3 scripts/main.py cluster list
python3 scripts/main.py analyze 1 --temp 23
python3 scripts/main.py auto-irrigate 1 --temp 23
```

### As Installed Package

```bash
# Install in other projects
pip install git+https://github.com/kezclaw/kezclaw.git#subdirectory=skills/tuya-irrigation

# Use CLI
irrigation cluster list
irrigation analyze 1 --temp 23

# Or programmatically
from irrigation import IrrigationDB, IrrigationLogic, TuyaDeviceManager

db = IrrigationDB()
logic = IrrigationLogic(db)
decision = logic.decide_for_cluster(cluster_id=1, current_temp=23.0)
```

## Entry Points

Defined in `pyproject.toml`:

```toml
[project.scripts]
tuya-irrigation = "tuya_irrigation.cli:main"
tuya-irrigation-logger = "tuya_irrigation.logger_daemon:main"
```

After installation, these become global commands.

## Adding New Modules

1. **Create module** in `src/tuya_irrigation/`
2. **Export in `__init__.py`** (if public API)
3. **Update imports** in dependent modules
4. **Add wrapper** in `scripts/` if needed for OpenClaw
5. **Write tests** in `tests/`
6. **Run ruff** to format/lint

Example:

```python
# src/tuya_irrigation/new_feature.py
"""New feature module."""
from tuya_irrigation.db import IrrigationDB

def new_function():
    ...

# src/tuya_irrigation/__init__.py
from tuya_irrigation.new_feature import new_function
__all__ = [..., "new_function"]

# tests/test_new_feature.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from tuya_irrigation.new_feature import new_function
```

## Publishing (Future)

When ready to publish to PyPI:

```bash
# Build package
uv build

# Publish (requires PyPI credentials)
uv publish
```

For now, the package is used locally within OpenClaw skills.

## Upgrading Dependencies

```bash
# Update all dependencies
uv lock --upgrade

# Sync to venv
uv sync
```

## Why This Structure?

✅ **Modern Python practices**: `uv` for fast dependency management, `ruff` for linting/formatting
✅ **Clean separation**: src/tuya_irrigation/ for package, scripts/ for OpenClaw compatibility
✅ **Testable**: Package imports work consistently in tests and production
✅ **Installable**: Can be installed as a proper Python package (`tuya-irrigation`)
✅ **Maintainable**: Clear structure, documented conventions
✅ **OpenClaw compatible**: Wrapper scripts ensure existing workflows continue to work
✅ **Standard naming**: PyPI name `tuya-irrigation`, import name `tuya_irrigation`

---

The system is now a **proper Python package** while maintaining full OpenClaw skill compatibility. 🦞📦✨

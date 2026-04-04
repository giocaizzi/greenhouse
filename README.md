# tuya-irrigation

Smart irrigation system with Tuya Cloud sensors, evidence-based plant care, and self-learning efficiency analysis.

## Features

- Evidence-based plant care from scientific literature
- Tuya Cloud sensor sync (soil moisture, temperature) -> local SQLite archive
- Multi-sensor conflict resolution for single-irrigator clusters
- Historical trend analysis and stress detection
- Self-learning irrigation profiles (absorption, drainage, efficiency)
- Automatic alerts (blocked drips, under-watering, unresolvable conflicts)
- Indoor/outdoor cluster support (sensor-primary vs Open-Meteo)
- Tuya irrigator control (cloud + local mode)

## Quick Start

```bash
# Setup
cp tools/cluster.local.json.example tools/cluster.local.json
# Edit with your device IDs and Tuya credentials

# Install
uv sync

# Initialize cluster
uv run tuya-irrigation cluster setup

# Run
uv run tuya-irrigation status 1
uv run tuya-irrigation irrigate 1
uv run tuya-irrigation history 1 --hours 24
uv run tuya-irrigation learn 1
```

## Documentation

| File | Content |
|---|---|
| [SKILL.md](SKILL.md) | Architecture, CLI reference, operational logic |
| [AGENTS.md](AGENTS.md) | Development guide, package structure, testing |
| [references/PLANT_DATABASE.md](references/PLANT_DATABASE.md) | Evidence-based plant care data |

## Testing

```bash
make check   # 93 tests + ruff lint
```

## License

Private repository.

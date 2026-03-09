# tuya-irrigation

Smart irrigation system with Tuya Cloud sensors, evidence-based plant care, and self-learning efficiency analysis.

## Features

- 🌱 Evidence-based plant care from scientific literature
- 📊 Tuya Cloud sensor sync (soil moisture, temperature) → local SQLite archive
- 🧠 Multi-sensor conflict resolution for single-irrigator clusters
- 📈 Historical trend analysis and stress detection
- 🔬 Self-learning irrigation profiles (absorption, drainage, efficiency)
- 🚨 Automatic alerts (blocked drips, under-watering, unresolvable conflicts)
- 🌡️ Indoor/outdoor cluster support (sensor-primary vs Open-Meteo)
- 💧 Tuya irrigator control (cloud + local mode)

## Quick Start

```bash
# Setup
cp tools/cluster.local.json.example tools/cluster.local.json
# Edit with your device IDs and Tuya credentials

# Initialize
cd scripts && python3 setup_cluster.py

# Analyze
python3 main.py analyze 1

# View data
python3 main.py log readings --cluster 1 --hours 24
python3 main.py learn report 1
```

## Documentation

See **[SKILL.md](SKILL.md)** for full architecture, CLI reference, and configuration.

## Testing

```bash
./test.sh   # 49 tests + ruff lint
```

## License

Private repository.

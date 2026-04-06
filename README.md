# tuya-irrigation

Smart plant irrigation system with Tuya Cloud IoT sensors, evidence-based plant care, and self-learning efficiency analysis.

## Features

- Evidence-based plant care from scientific literature
- Tuya Cloud sensor sync (soil moisture, temperature, humidity, light)
- Smart irrigation decisions with multi-sensor conflict resolution
- Self-learning irrigation profiles (absorption, drainage, efficiency)
- Background scheduling (sensor sync every 30min, checks every 6h)
- REST API with OpenAPI docs at `/docs`
- Indoor/outdoor cluster support

## Quick Start

```bash
# Install
uv sync

# Start server
uv run tuya-irrigation-server

# Use CLI (in another terminal)
uv run tuya-irrigation cluster add "My Plants" --environment indoor
uv run tuya-irrigation status 1
uv run tuya-irrigation check --all
uv run tuya-irrigation health
```
#!/usr/bin/env python3
"""Setup an irrigation cluster from local config or interactive prompts.

Usage:
    # Use local config file (recommended, gitignored):
    cp tools/cluster.local.json.example tools/cluster.local.json
    # edit cluster.local.json with your values
    python3 tools/setup_cluster.py

    # Or pass env vars directly:
    TUYA_DEVICE_ID=xxx python3 tools/setup_cluster.py
"""

import json
import os
import sys
from pathlib import Path

# Add src to path for package imports
skill_root = Path(__file__).parent.parent
sys.path.insert(0, str(skill_root / "src"))  # noqa: E402

from tuya_irrigation.db import IrrigationDB  # noqa: E402


def load_local_config() -> dict:
    """Load cluster.local.json if present, otherwise return empty dict."""
    config_path = Path(__file__).parent / "cluster.local.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def main():
    """Initialize a cluster from local config or environment variables."""
    local = load_local_config()

    tuya_device_id = os.environ.get("TUYA_DEVICE_ID") or local.get("irrigator", {}).get("tuya_device_id")
    tuya_device_ip = os.environ.get("TUYA_DEVICE_IP")
    tuya_local_key = os.environ.get("TUYA_LOCAL_KEY")

    if not tuya_device_id or tuya_device_id == "YOUR_DEVICE_ID":
        print("❌ Set TUYA_DEVICE_ID in environment or tools/cluster.local.json")
        print("   See tools/cluster.local.json.example for reference")
        return 1

    cluster_name = local.get("cluster_name", "My Indoor Plants")
    cluster_location = local.get("cluster_location", "Indoor")
    plants_data = local.get("plants", [
        {"species": "Monstera deliciosa", "category": "tropical", "water_needs": "medium", "light_needs": "medium", "notes": None},
    ])
    irr_cfg = local.get("irrigator", {})
    schedule_minutes = irr_cfg.get("schedule_minutes", 2)
    schedule_interval_hours = irr_cfg.get("schedule_interval_hours", 12)
    irrigator_name = irr_cfg.get("name", "Main Irrigator")

    db = IrrigationDB()
    print(f"🌱 Setting up cluster: {cluster_name!r}...")

    cluster_id = db.add_cluster(
        name=cluster_name,
        location=cluster_location,
    )
    print(f"✅ Cluster created (ID: {cluster_id})")

    for plant in plants_data:
        plant_id = db.add_plant(cluster_id=cluster_id, **plant)
        print(f"  🌿 Added {plant['species']} (ID: {plant_id})")

    irrigator_config = {"device_id": tuya_device_id}
    if tuya_device_ip:
        irrigator_config["device_ip"] = tuya_device_ip
    if tuya_local_key:
        irrigator_config["local_key"] = tuya_local_key

    irrigator_type = "tuya_local" if tuya_device_ip and tuya_local_key else "tuya_cloud"
    irrigator_id = db.add_irrigator(
        cluster_id=cluster_id,
        tuya_device_id=tuya_device_id,
        name=irrigator_name,
        irrigator_type=irrigator_type,
        config=irrigator_config,
    )
    print(f"✅ Irrigator added (ID: {irrigator_id}, type: {irrigator_type})")

    db.set_irrigation_config(
        cluster_id=cluster_id,
        mode="schedule",
        duration_minutes=schedule_minutes,
        interval_hours=schedule_interval_hours,
        auto_run=True,
    )
    print("✅ Initial config set (mode: schedule)")

    print("\n🎉 Setup complete!")
    print(f"\nCluster ID: {cluster_id}")
    print(f"Irrigator ID: {irrigator_id}")
    print("\nYou can now use:")
    print(f"  python3 scripts/main.py analyze {cluster_id}")
    print(f"  python3 scripts/main.py auto-irrigate {cluster_id} --temp <temp>")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

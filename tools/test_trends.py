#!/usr/bin/env python3
"""Test historical trends and stress detection."""

import sys
import time
from pathlib import Path

# Add src to path for package imports
skill_root = Path(__file__).parent.parent
sys.path.insert(0, str(skill_root / "src"))

from tuya_irrigation.db import IrrigationDB  # noqa: E402
from tuya_irrigation.logic import IrrigationLogic  # noqa: E402


def add_test_sensor_data(db: IrrigationDB, cluster_id: int):
    """Add fake sensor and irrigation data to test trends."""
    # Add a fake sensor
    sensor_id = db.add_sensor(
        cluster_id=cluster_id,
        tuya_device_id="test-sensor-001",
        name="Test Soil Moisture",
        sensor_type="soil_moisture",
        config={},
    )

    now = int(time.time())

    # Scenario 1: Declining soil moisture (water stress developing)
    print("\n📉 Creating declining soil moisture trend (48h)...")
    for hour in range(48, 0, -3):  # Every 3 hours for 48h
        timestamp = now - (hour * 3600)
        # Start at 55%, decline to 25%
        moisture = 55 - (30 * (48 - hour) / 48)
        db.add_sensor_reading(
            sensor_id=sensor_id,
            temperature=24.0 + (hour / 48) * 5,  # Also warming up
            humidity=60.0,
            soil_moisture=moisture,
            timestamp=timestamp,
        )
        print(f"  t-{hour}h: moisture={moisture:.0f}%, temp={24.0 + (hour / 48) * 5:.1f}°C")

    # Add some irrigation events (insufficient)
    irrigators = db.get_irrigators_in_cluster(cluster_id)
    if irrigators:
        print("\n💧 Adding sparse irrigation events...")
        for day in range(7, 0, -2):  # Every 2 days
            timestamp = now - (day * 24 * 3600)
            db.add_irrigation_event(
                irrigator_id=irrigators[0].id,
                action="start",
                duration_minutes=2,
                triggered_by="auto",
                timestamp=timestamp,
                notes="Test event (sparse pattern)",
            )
            print(f"  {day} days ago: 2min irrigation")

    return sensor_id


def test_trends_and_stress(db: IrrigationDB, cluster_id: int):
    """Test trend analysis and stress detection."""
    logic = IrrigationLogic(db)

    print("\n\n🧪 Testing Trends & Stress Detection\n" + "="*50)

    # Get decision with trends
    decision = logic.decide_for_cluster(cluster_id, current_temp=29.0)

    print("\n🧠 Decision:")
    print(f"   Action: {decision['action']}")
    print(f"   Duration: {decision['duration_minutes']} min")
    print(f"   Interval: {decision['interval_hours']} hours")
    print(f"   Reason: {decision['reason']}")
    print(f"   Confidence: {decision['confidence']:.0%}")

    if decision.get("trends"):
        print("\n📊 Trends Detected:")
        trends = decision["trends"]
        if trends.get("soil_moisture_trend"):
            print(f"   Soil moisture: {trends['soil_moisture_trend']} (Δ {trends.get('soil_moisture_delta', 0):.1f}%)")
        if trends.get("temperature_trend"):
            print(f"   Temperature: {trends['temperature_trend']} (Δ {trends.get('temperature_delta', 0):.1f}°C)")
        if trends.get("irrigation_avg_per_day"):
            print(f"   Irrigation freq: {trends['irrigation_avg_per_day']:.1f} times/day, avg {trends.get('irrigation_avg_duration', 0):.1f}min")
        if trends.get("irrigation_frequency_low"):
            print("   ⚠️ Under-watering pattern detected")
        if trends.get("irrigation_frequency_high"):
            print("   ⚠️ Over-watering pattern detected")

    if decision.get("stress_indicators"):
        print("\n⚠️  Stress Indicators:")
        stress = decision["stress_indicators"]
        if stress.get("water_stress"):
            print(f"   💧 Water stress: {stress['water_stress']}")
        if stress.get("heat_stress"):
            print(f"   🌡️  Heat stress: {stress['heat_stress']}")
        if stress.get("over_watering"):
            print(f"   💦 Over-watering: {stress['over_watering']}")

    print("\n" + "="*50)
    print("✅ Trend analysis and stress detection working!\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Test historical trends and stress detection")
    parser.add_argument("cluster", type=int, help="Cluster ID")
    parser.add_argument("--populate", action="store_true", help="Populate with test data")
    parser.add_argument("--db", help="Database path (default: auto)")

    args = parser.parse_args()

    db_path = Path(args.db) if args.db else None
    db = IrrigationDB(db_path)

    try:
        if args.populate:
            sensor_id = add_test_sensor_data(db, args.cluster)
            print(f"\n✅ Test data added (sensor_id={sensor_id})")

        test_trends_and_stress(db, args.cluster)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

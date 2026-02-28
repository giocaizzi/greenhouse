#!/usr/bin/env python3
"""Main CLI for smart irrigation system."""

import argparse
import sys
import time
from pathlib import Path

from tuya_irrigation.db import IrrigationDB
from tuya_irrigation.devices import TuyaDeviceManager
from tuya_irrigation.logic import IrrigationLogic
from tuya_irrigation.utils import format_timestamp


def cmd_cluster_add(args, db: IrrigationDB):
    """Add a new cluster."""
    cluster_id = db.add_cluster(args.name, args.location)
    print(f"✅ Cluster created: {args.name} (ID: {cluster_id})")


def cmd_cluster_list(args, db: IrrigationDB):
    """List all clusters."""
    clusters = db.list_clusters()
    if not clusters:
        print("No clusters found.")
        return

    print("📦 Clusters:")
    for c in clusters:
        loc = f" ({c.location})" if c.location else ""
        print(f"  [{c.id}] {c.name}{loc}")


def cmd_plant_add(args, db: IrrigationDB):
    """Add a plant to a cluster."""
    plant_id = db.add_plant(
        cluster_id=args.cluster,
        species=args.species,
        category=args.category,
        water_needs=args.water_needs,
        light_needs=args.light_needs,
        ideal_temp_min=args.temp_min,
        ideal_temp_max=args.temp_max,
        ideal_humidity_min=args.humidity_min,
        ideal_humidity_max=args.humidity_max,
        notes=args.notes,
    )
    print(f"✅ Plant added: {args.species} (ID: {plant_id})")


def cmd_plant_list(args, db: IrrigationDB):
    """List plants in a cluster."""
    if args.cluster:
        clusters = [db.get_cluster(args.cluster)]
    else:
        clusters = db.list_clusters()

    for cluster in clusters:
        if not cluster:
            continue
        plants = db.get_plants_in_cluster(cluster.id)
        if not plants:
            print(f"\n📦 {cluster.name}: no plants")
            continue

        print(f"\n📦 {cluster.name}:")
        for p in plants:
            water = f"water:{p.water_needs}" if p.water_needs else ""
            cat = f"[{p.category}]" if p.category else ""
            print(f"  🌿 {p.species} {cat} {water}")
            if p.ideal_temp_min and p.ideal_temp_max:
                print(f"     temp: {p.ideal_temp_min}-{p.ideal_temp_max}°C")
            if p.ideal_humidity_min and p.ideal_humidity_max:
                print(f"     humidity: {p.ideal_humidity_min}-{p.ideal_humidity_max}%")


def cmd_irrigator_add(args, db: IrrigationDB):
    """Add an irrigator device to a cluster."""
    config = {}
    if args.device_ip:
        config["device_ip"] = args.device_ip
    if args.local_key:
        config["local_key"] = args.local_key
    if args.interval:
        config["interval_hours"] = args.interval

    irrigator_id = db.add_irrigator(
        cluster_id=args.cluster,
        tuya_device_id=args.device_id,
        name=args.name,
        irrigator_type=args.type,
        config=config,
    )
    print(f"✅ Irrigator added: {args.name} (ID: {irrigator_id})")


def cmd_irrigator_list(args, db: IrrigationDB):
    """List irrigators."""
    if args.cluster:
        clusters = [db.get_cluster(args.cluster)]
    else:
        clusters = db.list_clusters()

    for cluster in clusters:
        if not cluster:
            continue
        irrigators = db.get_irrigators_in_cluster(cluster.id)
        if not irrigators:
            continue

        print(f"\n📦 {cluster.name}:")
        for irr in irrigators:
            print(f"  💧 {irr.name} [{irr.type}] (ID: {irr.id}, Device: {irr.tuya_device_id})")


def cmd_irrigator_status(args, db: IrrigationDB, dm: TuyaDeviceManager):
    """Get irrigator status."""
    irrigator = db.get_irrigator(args.id)
    if not irrigator:
        print(f"❌ Irrigator {args.id} not found")
        return 1

    print(f"💧 {irrigator.name} status:")
    status = dm.irrigator_status(irrigator)
    if "error" in status:
        print(f"  ❌ Error: {status['error']}")
        return 1

    print(f"  Running: {status.get('running', '?')}")
    if "time_remaining_minutes" in status:
        print(f"  Time remaining: {status['time_remaining_minutes']} min")
    if "battery_percentage" in status:
        print(f"  Battery: {status['battery_percentage']}%")


def cmd_irrigator_on(args, db: IrrigationDB, dm: TuyaDeviceManager):
    """Turn irrigator ON."""
    irrigator = db.get_irrigator(args.id)
    if not irrigator:
        print(f"❌ Irrigator {args.id} not found")
        return 1

    success, output = dm.irrigator_on(irrigator)
    if success:
        db.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="on",
            triggered_by="manual",
            notes="Manual ON via CLI",
        )
        print(f"✅ {irrigator.name} turned ON")
    else:
        print(f"❌ Failed: {output}")
        return 1


def cmd_irrigator_off(args, db: IrrigationDB, dm: TuyaDeviceManager):
    """Turn irrigator OFF."""
    irrigator = db.get_irrigator(args.id)
    if not irrigator:
        print(f"❌ Irrigator {args.id} not found")
        return 1

    success, output = dm.irrigator_off(irrigator)
    if success:
        db.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="off",
            triggered_by="manual",
            notes="Manual OFF via CLI",
        )
        print(f"✅ {irrigator.name} turned OFF")
    else:
        print(f"❌ Failed: {output}")
        return 1


def cmd_irrigator_start(args, db: IrrigationDB, dm: TuyaDeviceManager):
    """Start irrigation with optional duration."""
    irrigator = db.get_irrigator(args.id)
    if not irrigator:
        print(f"❌ Irrigator {args.id} not found")
        return 1

    success, output = dm.irrigator_start(irrigator, args.minutes)
    if success:
        db.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="start",
            duration_minutes=args.minutes,
            triggered_by="manual",
            notes=f"Manual START via CLI ({args.minutes} min)" if args.minutes else "Manual START via CLI",
        )
        msg = f"for {args.minutes} min" if args.minutes else "manually"
        print(f"✅ {irrigator.name} started {msg}")
    else:
        print(f"❌ Failed: {output}")
        return 1


def cmd_sensor_add(args, db: IrrigationDB):
    """Add a sensor device to a cluster."""
    config = {}
    if args.device_ip:
        config["device_ip"] = args.device_ip
    if args.local_key:
        config["local_key"] = args.local_key

    sensor_id = db.add_sensor(
        cluster_id=args.cluster,
        tuya_device_id=args.device_id,
        name=args.name,
        sensor_type=args.type,
        config=config,
    )
    print(f"✅ Sensor added: {args.name} (ID: {sensor_id})")


def cmd_sensor_list(args, db: IrrigationDB):
    """List sensors."""
    if args.cluster:
        clusters = [db.get_cluster(args.cluster)]
    else:
        clusters = db.list_clusters()

    for cluster in clusters:
        if not cluster:
            continue
        sensors = db.get_sensors_in_cluster(cluster.id)
        if not sensors:
            continue

        print(f"\n📦 {cluster.name}:")
        for sensor in sensors:
            print(f"  📊 {sensor.name} [{sensor.type}] (ID: {sensor.id}, Device: {sensor.tuya_device_id})")


def cmd_sensor_read(args, db: IrrigationDB, dm: TuyaDeviceManager):
    """Read sensor data."""
    sensors = db.get_sensors_in_cluster(args.cluster) if args.cluster else []
    if not sensors:
        print("No sensors found in cluster")
        return 1

    for sensor in sensors:
        print(f"\n📊 {sensor.name}:")
        data = dm.read_sensor(sensor)
        if "error" in data:
            print(f"  ❌ Error: {data['error']}")
            continue

        if data.get("temperature") is not None:
            print(f"  Temperature: {data['temperature']:.1f}°C")
        if data.get("humidity") is not None:
            print(f"  Humidity: {data['humidity']:.0f}%")
        if data.get("soil_moisture") is not None:
            print(f"  Soil moisture: {data['soil_moisture']:.0f}%")
        if data.get("light") is not None:
            print(f"  Light: {data['light']} lux")


def cmd_config_set(args, db: IrrigationDB):
    """Set irrigation config for a cluster."""
    db.set_irrigation_config(
        cluster_id=args.cluster,
        mode=args.mode,
        duration_minutes=args.minutes,
        interval_hours=args.interval,
        auto_run=args.auto_run,
    )
    print(f"✅ Irrigation config updated for cluster {args.cluster}")


def cmd_config_get(args, db: IrrigationDB):
    """Get irrigation config for a cluster."""
    config = db.get_irrigation_config(args.cluster)
    if not config:
        print(f"No config found for cluster {args.cluster}")
        return

    print(f"⚙️  Irrigation config (cluster {args.cluster}):")
    print(f"  Mode: {config.mode}")
    print(f"  Duration: {config.duration_minutes} min")
    print(f"  Interval: {config.interval_hours} hours")
    print(f"  Auto-run: {config.auto_run}")


def cmd_analyze(args, db: IrrigationDB):
    """Analyze and suggest irrigation for a cluster."""
    logic = IrrigationLogic(db)
    decision = logic.decide_for_cluster(args.cluster, args.temp)

    if not decision:
        print("❌ Cannot analyze: cluster not found or no data")
        return 1

    print(f"\n🧠 Smart Analysis (cluster {args.cluster}):")
    print(f"  Action: {decision['action']}")
    print(f"  Duration: {decision['duration_minutes']} min")
    print(f"  Interval: {decision['interval_hours']} hours")
    print(f"  Reason: {decision['reason']}")
    print(f"  Confidence: {decision['confidence']:.0%}")


def cmd_auto_irrigate(args, db: IrrigationDB, dm: TuyaDeviceManager):
    """Automatically irrigate based on smart logic."""
    logic = IrrigationLogic(db)
    decision = logic.decide_for_cluster(args.cluster, args.temp)

    if not decision:
        print("❌ Cannot decide: cluster not found or no data")
        return 1

    print(f"🧠 Decision: {decision['action']} ({decision['reason']})")
    print(f"   Confidence: {decision['confidence']:.0%}")

    if decision["action"] == "skip":
        print("⏭️  Skipping irrigation")
        # Still log the decision
        irrigators = db.get_irrigators_in_cluster(args.cluster)
        for irrigator in irrigators:
            db.add_irrigation_event(
                irrigator_id=irrigator.id,
                action="skip_decision",
                triggered_by="auto",
                notes=f"Smart logic: {decision['reason']} (confidence: {decision['confidence']:.0%})",
            )
        return 0

    # Apply decision to all irrigators in cluster
    irrigators = db.get_irrigators_in_cluster(args.cluster)
    if not irrigators:
        print("❌ No irrigators in cluster")
        return 1

    for irrigator in irrigators:
        print(f"\n💧 Applying to {irrigator.name}...")

        if irrigator.type == "tuya_local":
            success, output = dm.irrigator_set_schedule(
                irrigator,
                decision["duration_minutes"],
                decision["interval_hours"],
                auto_run=True,
            )
            action = "schedule_updated"
        else:
            success, output = dm.irrigator_start(irrigator, decision["duration_minutes"])
            action = "start"

        if success:
            db.add_irrigation_event(
                irrigator_id=irrigator.id,
                action=action,
                duration_minutes=decision["duration_minutes"],
                triggered_by="auto",
                notes=f"Smart logic: {decision['reason']} (confidence: {decision['confidence']:.0%}, interval: {decision['interval_hours']}h)",
            )
            print(f"✅ {irrigator.name}: {decision['duration_minutes']}min every {decision['interval_hours']}h")
        else:
            print(f"❌ {irrigator.name} failed: {output}")
            db.add_irrigation_event(
                irrigator_id=irrigator.id,
                action="error",
                triggered_by="auto",
                notes=f"Failed: {output}",
            )


def cmd_log_readings(args, db: IrrigationDB):
    """Show recent sensor readings."""
    sensors = db.get_sensors_in_cluster(args.cluster) if args.cluster else []
    if not sensors:
        print("No sensors found")
        return

    for sensor in sensors:
        readings = db.get_recent_readings(sensor.id, hours=args.hours)
        if not readings:
            continue

        print(f"\n📊 {sensor.name} (last {args.hours}h, {len(readings)} readings):")
        for r in readings[:10]:  # Show last 10
            ts = format_timestamp(r.timestamp)
            parts = [ts]
            if r.temperature is not None:
                parts.append(f"temp={r.temperature:.1f}°C")
            if r.humidity is not None:
                parts.append(f"hum={r.humidity:.0f}%")
            if r.soil_moisture is not None:
                parts.append(f"soil={r.soil_moisture:.0f}%")
            if r.light is not None:
                parts.append(f"light={r.light}lux")
            print(f"  {' | '.join(parts)}")


def cmd_log_events(args, db: IrrigationDB):
    """Show recent irrigation events with summary."""
    irrigators = db.get_irrigators_in_cluster(args.cluster) if args.cluster else []
    if not irrigators:
        print("No irrigators found")
        return

    total_duration = 0
    total_irrigations = 0

    for irrigator in irrigators:
        events = db.get_recent_events(irrigator.id, hours=args.hours)
        if not events:
            continue

        print(f"\n💧 {irrigator.name} (last {args.hours}h, {len(events)} events):")
        for e in events[:10]:  # Show last 10
            ts = format_timestamp(e.timestamp)
            dur = f" ({e.duration_minutes}min)" if e.duration_minutes else ""
            print(f"  {ts} | {e.action}{dur} [{e.triggered_by}]")
            if e.notes:
                print(f"    → {e.notes}")

            # Count irrigation time
            if e.action in ("start", "schedule_updated") and e.duration_minutes:
                total_duration += e.duration_minutes
                total_irrigations += 1

    # Summary
    if total_irrigations > 0:
        print(f"\n📊 Summary ({args.hours}h):")
        print(f"   Total irrigations: {total_irrigations}")
        print(f"   Total water time: {total_duration}min")
        print(f"   Average per irrigation: {total_duration / total_irrigations:.1f}min")


def main():
    parser = argparse.ArgumentParser(
        description="Smart irrigation system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", help="Database path (default: auto)")

    sub = parser.add_subparsers(dest="command", required=True)

    # Cluster commands
    p_cluster = sub.add_parser("cluster", help="Manage clusters")
    cluster_sub = p_cluster.add_subparsers(dest="cluster_cmd", required=True)
    p_cluster_add = cluster_sub.add_parser("add", help="Add a cluster")
    p_cluster_add.add_argument("name", help="Cluster name")
    p_cluster_add.add_argument("--location", help="Location description")
    cluster_sub.add_parser("list", help="List clusters")

    # Plant commands
    p_plant = sub.add_parser("plant", help="Manage plants")
    plant_sub = p_plant.add_subparsers(dest="plant_cmd", required=True)
    p_plant_add = plant_sub.add_parser("add", help="Add a plant")
    p_plant_add.add_argument("--cluster", type=int, required=True, help="Cluster ID")
    p_plant_add.add_argument("species", help="Plant species")
    p_plant_add.add_argument("--category", help="Category (tropical, succulent, etc.)")
    p_plant_add.add_argument("--water-needs", choices=["low", "medium", "high"])
    p_plant_add.add_argument("--light-needs", choices=["low", "medium", "high"])
    p_plant_add.add_argument("--temp-min", type=float, help="Ideal temp min (°C)")
    p_plant_add.add_argument("--temp-max", type=float, help="Ideal temp max (°C)")
    p_plant_add.add_argument("--humidity-min", type=float, help="Ideal humidity min (%)")
    p_plant_add.add_argument("--humidity-max", type=float, help="Ideal humidity max (%)")
    p_plant_add.add_argument("--notes", help="Additional notes")
    p_plant_list = plant_sub.add_parser("list", help="List plants")
    p_plant_list.add_argument("--cluster", type=int, help="Filter by cluster ID")

    # Irrigator commands
    p_irr = sub.add_parser("irrigator", help="Manage irrigators")
    irr_sub = p_irr.add_subparsers(dest="irrigator_cmd", required=True)
    p_irr_add = irr_sub.add_parser("add", help="Add an irrigator")
    p_irr_add.add_argument("--cluster", type=int, required=True, help="Cluster ID")
    p_irr_add.add_argument("--device-id", required=True, help="Tuya device ID")
    p_irr_add.add_argument("--name", required=True, help="Irrigator name")
    p_irr_add.add_argument("--type", required=True, choices=["tuya_cloud", "tuya_local"])
    p_irr_add.add_argument("--device-ip", help="Device IP (for local)")
    p_irr_add.add_argument("--local-key", help="Local key (for local)")
    p_irr_add.add_argument("--interval", type=int, help="Default interval hours")
    p_irr_list = irr_sub.add_parser("list", help="List irrigators")
    p_irr_list.add_argument("--cluster", type=int, help="Filter by cluster ID")
    p_irr_status = irr_sub.add_parser("status", help="Get irrigator status")
    p_irr_status.add_argument("id", type=int, help="Irrigator ID")
    p_irr_on = irr_sub.add_parser("on", help="Turn irrigator ON")
    p_irr_on.add_argument("id", type=int, help="Irrigator ID")
    p_irr_off = irr_sub.add_parser("off", help="Turn irrigator OFF")
    p_irr_off.add_argument("id", type=int, help="Irrigator ID")
    p_irr_start = irr_sub.add_parser("start", help="Start irrigation")
    p_irr_start.add_argument("id", type=int, help="Irrigator ID")
    p_irr_start.add_argument("--minutes", type=int, help="Duration in minutes")

    # Sensor commands
    p_sensor = sub.add_parser("sensor", help="Manage sensors")
    sensor_sub = p_sensor.add_subparsers(dest="sensor_cmd", required=True)
    p_sensor_add = sensor_sub.add_parser("add", help="Add a sensor")
    p_sensor_add.add_argument("--cluster", type=int, required=True, help="Cluster ID")
    p_sensor_add.add_argument("--device-id", required=True, help="Tuya device ID")
    p_sensor_add.add_argument("--name", required=True, help="Sensor name")
    p_sensor_add.add_argument("--type", required=True, help="Sensor type (temp_humidity, soil_moisture, light)")
    p_sensor_add.add_argument("--device-ip", help="Device IP (for local)")
    p_sensor_add.add_argument("--local-key", help="Local key (for local)")
    p_sensor_list = sensor_sub.add_parser("list", help="List sensors")
    p_sensor_list.add_argument("--cluster", type=int, help="Filter by cluster ID")
    p_sensor_read = sensor_sub.add_parser("read", help="Read sensor data")
    p_sensor_read.add_argument("--cluster", type=int, required=True, help="Cluster ID")

    # Config commands
    p_config = sub.add_parser("config", help="Manage irrigation config")
    config_sub = p_config.add_subparsers(dest="config_cmd", required=True)
    p_config_set = config_sub.add_parser("set", help="Set config")
    p_config_set.add_argument("--cluster", type=int, required=True, help="Cluster ID")
    p_config_set.add_argument("--mode", required=True, choices=["manual", "schedule", "smart"])
    p_config_set.add_argument("--minutes", type=int, help="Duration minutes")
    p_config_set.add_argument("--interval", type=int, help="Interval hours")
    p_config_set.add_argument("--auto-run", type=bool, default=True, help="Auto-run enabled")
    p_config_get = config_sub.add_parser("get", help="Get config")
    p_config_get.add_argument("cluster", type=int, help="Cluster ID")

    # Analysis commands
    p_analyze = sub.add_parser("analyze", help="Analyze and suggest irrigation")
    p_analyze.add_argument("cluster", type=int, help="Cluster ID")
    p_analyze.add_argument("--temp", type=float, help="Current temperature (°C)")

    p_auto = sub.add_parser("auto-irrigate", help="Automatically irrigate based on smart logic")
    p_auto.add_argument("cluster", type=int, help="Cluster ID")
    p_auto.add_argument("--temp", type=float, help="Current temperature (°C)")

    # Log commands
    p_log = sub.add_parser("log", help="View logs")
    log_sub = p_log.add_subparsers(dest="log_cmd", required=True)
    p_log_readings = log_sub.add_parser("readings", help="Show sensor readings")
    p_log_readings.add_argument("--cluster", type=int, required=True, help="Cluster ID")
    p_log_readings.add_argument("--hours", type=int, default=24, help="Hours to look back")
    p_log_events = log_sub.add_parser("events", help="Show irrigation events with summary")
    p_log_events.add_argument("--cluster", type=int, required=True, help="Cluster ID")
    p_log_events.add_argument("--hours", type=int, default=24, help="Hours to look back")

    p_log_stats = log_sub.add_parser("stats", help="Show irrigation statistics")
    p_log_stats.add_argument("--cluster", type=int, required=True, help="Cluster ID")
    p_log_stats.add_argument("--days", type=int, default=7, help="Days to analyze")
    p_log_stats.add_argument("--export", help="Export to CSV file")

    args = parser.parse_args()

    # Initialize DB
    db_path = Path(args.db) if args.db else None
    db = IrrigationDB(db_path)

    # Initialize device manager for commands that need it
    dm = None
    if (
        args.command in ("irrigator", "sensor", "auto-irrigate")
        and args.__dict__.get("irrigator_cmd")
        in (
            "status",
            "on",
            "off",
            "start",
        )
        or args.__dict__.get("sensor_cmd") == "read"
        or args.command == "auto-irrigate"
    ):
        dm = TuyaDeviceManager()

    # Dispatch commands
    try:
        if args.command == "cluster":
            if args.cluster_cmd == "add":
                cmd_cluster_add(args, db)
            elif args.cluster_cmd == "list":
                cmd_cluster_list(args, db)

        elif args.command == "plant":
            if args.plant_cmd == "add":
                cmd_plant_add(args, db)
            elif args.plant_cmd == "list":
                cmd_plant_list(args, db)

        elif args.command == "irrigator":
            if args.irrigator_cmd == "add":
                cmd_irrigator_add(args, db)
            elif args.irrigator_cmd == "list":
                cmd_irrigator_list(args, db)
            elif args.irrigator_cmd == "status":
                return cmd_irrigator_status(args, db, dm)
            elif args.irrigator_cmd == "on":
                return cmd_irrigator_on(args, db, dm)
            elif args.irrigator_cmd == "off":
                return cmd_irrigator_off(args, db, dm)
            elif args.irrigator_cmd == "start":
                return cmd_irrigator_start(args, db, dm)

        elif args.command == "sensor":
            if args.sensor_cmd == "add":
                cmd_sensor_add(args, db)
            elif args.sensor_cmd == "list":
                cmd_sensor_list(args, db)
            elif args.sensor_cmd == "read":
                return cmd_sensor_read(args, db, dm)

        elif args.command == "config":
            if args.config_cmd == "set":
                cmd_config_set(args, db)
            elif args.config_cmd == "get":
                cmd_config_get(args, db)

        elif args.command == "analyze":
            return cmd_analyze(args, db)

        elif args.command == "auto-irrigate":
            return cmd_auto_irrigate(args, db, dm)

        elif args.command == "log":
            if args.log_cmd == "readings":
                cmd_log_readings(args, db)
            elif args.log_cmd == "events":
                cmd_log_events(args, db)
            elif args.log_cmd == "stats":
                # Import and use stats module
                from stats import export_csv, get_irrigation_stats, print_stats_report

                cluster = db.get_cluster(args.cluster)
                if not cluster:
                    print(f"❌ Cluster {args.cluster} not found")
                    return 1
                if args.export:
                    export_csv(db, args.cluster, args.days, args.export)
                else:
                    stats = get_irrigation_stats(db, args.cluster, args.days)
                    print_stats_report(stats, cluster.name)

    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

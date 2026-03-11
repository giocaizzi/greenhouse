#!/usr/bin/env python3
"""Main CLI for smart irrigation system.

Command structure:
  OPERATIONS (one call = full picture)
    status <cluster>          Full cluster status (sensors, config, history, alerts)
    irrigate <cluster>        Smart irrigation (analyze + execute)
    sync [cluster]            Sync sensor data from cloud
    learn <cluster>           Learning report + efficiency alerts

  SETUP (infrequent, CRUD)
    cluster add/list
    plant add/list
    irrigator add/list/start/stop/on/off/log-manual
    sensor add/list
    config set/get

  DATA
    history <cluster>         Sensor readings + irrigation events
    stats <cluster>           Statistics + CSV export
"""

import argparse
import sys
from pathlib import Path

from tuya_irrigation.db import IrrigationDB
from tuya_irrigation.devices import TuyaDeviceManager
from tuya_irrigation.logic import IrrigationLogic
from tuya_irrigation.plant_db import get_plant_database
from tuya_irrigation.utils import format_timestamp

# ── Helper Functions ──────────────────────────────────────────────────────────


def _get_irrigator_or_exit(db: IrrigationDB, irrigator_id: int):
    irrigator = db.get_irrigator(irrigator_id)
    if not irrigator:
        print(f"❌ Irrigator {irrigator_id} not found")
        sys.exit(1)
    return irrigator


# ── Operation Commands (one call = full picture) ──────────────────────────────


def cmd_status(args, db: IrrigationDB, dm: TuyaDeviceManager | None):
    """Full cluster status in one call."""
    cluster = db.get_cluster(args.cluster)
    if not cluster:
        print(f"❌ Cluster {args.cluster} not found")
        return 1

    print(f"📦 {cluster.name} [{cluster.environment}]")
    if cluster.location:
        print(f"   📍 {cluster.location}")

    # Config
    config = db.get_irrigation_config(args.cluster)
    if config:
        print(f"\n⚙️  Mode: {config.mode} | {config.duration_minutes}min / {config.interval_hours}h | auto: {'ON' if config.auto_run else 'OFF'}")

    # Plants
    plants = db.get_plants_in_cluster(args.cluster)
    if plants:
        print(f"\n🌿 Plants ({len(plants)}):")
        for p in plants:
            extras = []
            if p.water_needs:
                extras.append(f"water:{p.water_needs}")
            if p.category:
                extras.append(f"[{p.category}]")
            print(f"   {p.species} {' '.join(extras)}")

    # Sensors (live + last DB reading)
    sensors = db.get_sensors_in_cluster(args.cluster)
    if sensors:
        print(f"\n📊 Sensors ({len(sensors)}):")
        for sensor in sensors:
            plant_info = f" → plant {sensor.plant_id}" if sensor.plant_id else ""
            print(f"   {sensor.name} [{sensor.type}]{plant_info}")

            # Live reading
            if dm:
                try:
                    data = dm.read_sensor(sensor)
                    parts = []
                    if data.get("temperature") is not None:
                        parts.append(f"{data['temperature']:.1f}°C")
                    if data.get("soil_moisture") is not None:
                        parts.append(f"soil:{data['soil_moisture']:.0f}%")
                    if data.get("humidity") is not None:
                        parts.append(f"hum:{data['humidity']:.0f}%")
                    if data.get("battery_state"):
                        parts.append(f"🔋{data['battery_state']}")
                    if parts:
                        print(f"     Live: {' | '.join(parts)}")
                except Exception as e:
                    print(f"     Live: ❌ {e}")

            # Last DB reading
            readings = db.get_recent_readings(sensor.id, hours=24)
            if readings:
                r = readings[0]
                ts = format_timestamp(r.timestamp)
                parts = [ts]
                if r.temperature is not None:
                    parts.append(f"{r.temperature:.1f}°C")
                if r.soil_moisture is not None:
                    parts.append(f"soil:{r.soil_moisture:.0f}%")
                print(f"     Last: {' | '.join(parts)} ({len(readings)} readings/24h)")
    else:
        print("\n📊 No sensors")

    # Irrigators + recent events
    irrigators = db.get_irrigators_in_cluster(args.cluster)
    if irrigators:
        print(f"\n💧 Irrigators ({len(irrigators)}):")
        for irr in irrigators:
            print(f"   {irr.name} [{irr.type}]")
            events = db.get_recent_events(irr.id, hours=48)
            irrigations = [e for e in events if e.action == "start"]
            if irrigations:
                last = irrigations[0]
                ts = format_timestamp(last.timestamp)
                dur = f" ({last.duration_minutes}min)" if last.duration_minutes else ""
                print(f"     Last: {ts}{dur} [{last.triggered_by}]")
                print(f"     Events: {len(events)} in 48h ({len(irrigations)} irrigations)")
            else:
                print("     No recent irrigation")

    # Smart analysis (quick)
    logic = IrrigationLogic(db)
    decision = logic.decide_for_cluster(args.cluster)
    if decision:
        print(f"\n🧠 Smart: {decision['action']} — {decision['reason']}")
        print(f"   Confidence: {decision['confidence']:.0%} | Duration: {decision['duration_minutes']}min / {decision['interval_hours']}h")

        # Learning alerts
        stress = decision.get("stress_indicators", {})
        alerts = stress.get("learning_alerts", [])
        if alerts:
            print("\n🚨 Alerts:")
            for a in alerts:
                print(f"   [{a['severity'].upper()}] {a['message']}")

    return 0


def _fetch_open_meteo(lat: float = 45.464, lon: float = 9.189) -> dict | None:
    """Fetch current weather from Open-Meteo."""
    import json
    import urllib.request

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,apparent_temperature,precipitation,relative_humidity_2m"
        f"&timezone=Europe/Rome"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
            current = data.get("current", {})
            return {
                "temperature": current.get("temperature_2m"),
                "feels_like": current.get("apparent_temperature"),
                "precipitation": current.get("precipitation"),
                "humidity": current.get("relative_humidity_2m"),
            }
    except Exception as e:
        print(f"⚠️  Open-Meteo: {e}", file=sys.stderr)
        return None


def _sync_and_read_sensors(db: IrrigationDB, cluster_id: int) -> dict | None:
    """Sync sensor data from Tuya Cloud and return live reading."""
    sensors = db.get_sensors_in_cluster(cluster_id)
    if not sensors:
        return None
    try:
        from tuya_irrigation.cloud import TuyaCloud
        from tuya_irrigation.logger_daemon import _sync_single_sensor

        cloud = TuyaCloud()
        for sensor in sensors:
            try:
                _sync_single_sensor(db, cloud, sensor, hours=6)
            except Exception:
                pass
        live = cloud.get_live_reading(sensors[0].tuya_device_id)
        return live if live else None
    except Exception as e:
        print(f"⚠️  Cloud sync: {e}", file=sys.stderr)
        return None


def cmd_irrigate(args, db: IrrigationDB, dm: TuyaDeviceManager):
    """Full pipeline: sync sensors → fetch weather → decide → execute.

    --temp: override temperature (skips sync + weather)
    --dry-run: analyze only, don't execute
    --no-sync: skip sensor sync (use DB data)
    """
    cluster = db.get_cluster(args.cluster)
    if not cluster:
        print("❌ Cluster not found")
        return 1

    is_indoor = cluster.environment == "indoor"

    # Step 1: Sync sensors (unless --temp override or --no-sync)
    sensor_data = None
    if args.temp is None and not getattr(args, "no_sync", False):
        sensor_data = _sync_and_read_sensors(db, args.cluster)

    # Step 2: Determine temperature based on environment
    temp = args.temp
    weather = None
    source = "override"

    if temp is None:
        if is_indoor:
            if sensor_data and sensor_data.get("temperature") is not None:
                temp = sensor_data["temperature"]
                source = "sensor"
            else:
                weather = _fetch_open_meteo()
                if weather and weather.get("feels_like") is not None:
                    temp = weather["feels_like"]
                    source = "open-meteo (fallback)"
        else:
            weather = _fetch_open_meteo()
            if weather and weather.get("feels_like") is not None:
                temp = weather["feels_like"]
                source = "open-meteo"
            elif sensor_data and sensor_data.get("temperature") is not None:
                temp = sensor_data["temperature"]
                source = "sensor (weather unavailable)"

    if temp is None:
        temp = 20.0
        source = "fallback (20°C)"

    # Step 3: Run decision logic
    logic = IrrigationLogic(db)
    decision = logic.decide_for_cluster(args.cluster, current_temp=temp)
    if not decision:
        print("❌ No data for decision")
        return 1

    action = decision["action"]
    reason = decision["reason"]
    confidence = decision["confidence"]
    duration = decision["duration_minutes"]

    # Step 4: Output
    print(f"🧠 Decision: {action} — {reason}")
    info = [f"Temp: {temp:.1f}°C ({source})"]
    if sensor_data and sensor_data.get("soil_moisture") is not None:
        info.append(f"Soil: {sensor_data['soil_moisture']:.0f}%")
    if weather and weather.get("precipitation") and weather["precipitation"] > 0:
        info.append(f"Rain: {weather['precipitation']}mm")
    info.append(f"Confidence: {confidence:.0%} | {duration}min / {decision['interval_hours']}h")
    for line in info:
        print(f"   {line}")

    stress = decision.get("stress_indicators", {})
    for key in ("water_stress", "heat_stress", "over_watering"):
        if key in stress:
            print(f"   ⚠️ {key}: {stress[key]}")
    alerts = stress.get("learning_alerts", [])
    for a in alerts:
        print(f"   🚨 [{a['severity']}] {a['message']}")

    if args.dry_run:
        print("\n⏹️  Dry run — no action taken")
        return 0

    if action == "skip":
        return 0

    # Step 5: Execute
    irrigators = db.get_irrigators_in_cluster(args.cluster)
    if not irrigators:
        print("❌ No irrigators")
        return 1

    for irrigator in irrigators:
        success, output = dm.irrigator_start(irrigator, duration) if dm else (False, "No device manager")
        soil_info = f", soil={sensor_data['soil_moisture']:.0f}%" if sensor_data and sensor_data.get("soil_moisture") is not None else ""
        db.add_irrigation_event(
            irrigator_id=irrigator.id,
            action="start" if success else "attempted",
            duration_minutes=duration,
            triggered_by="auto",
            notes=f"temp={temp:.1f}°C ({source}){soil_info}, confidence={confidence:.0%}, reason={reason}",
        )
        if success:
            print(f"   ✅ {irrigator.name}: started ({duration}min)")
        else:
            print(f"   ❌ {irrigator.name}: {output}")
            return 1

    return 0



def cmd_monitor(args, db: IrrigationDB):
    """Monitor sensor-only cluster — low-level, human-readable output.

    For interactive use. Outputs status per sensor with soil moisture vs target.
    For automated use (cron), use `check --all` instead.

    Exit codes: 0 = ok, 2 = needs water, 1 = error
    """
    cluster = db.get_cluster(args.cluster)
    if not cluster:
        print(f"❌ Cluster {args.cluster} not found")
        return 1

    irrigators = db.get_irrigators_in_cluster(args.cluster)
    if irrigators:
        print("⚠️  Cluster has irrigators — use 'irrigate' instead of 'monitor'")

    print(f"🌿 Monitoring: {cluster.name}")

    # Step 1: Sync sensor data
    if not getattr(args, "no_sync", False):
        try:
            from tuya_irrigation.cloud import TuyaCloud
            from tuya_irrigation.logger_daemon import sync_sensor_data
            cloud = TuyaCloud()
            stats = sync_sensor_data(db, cloud, hours=2)
            new = stats.get("total_new", 0)
            print(f"   Sync: {new} new readings")
        except Exception as e:
            print(f"   ⚠️  Sync failed: {e}")

    # Step 2: Get sensors and recent readings
    sensors = db.get_sensors_in_cluster(args.cluster)
    if not sensors:
        print("❌ No sensors in cluster")
        return 1

    plant_db_instance = get_plant_database()
    plants_by_id = {p.id: p for p in db.get_plants_in_cluster(args.cluster)}

    needs_water = []

    for sensor in sensors:
        readings = db.get_recent_readings(sensor.id, hours=2)
        if not readings:
            print(f"   📊 {sensor.name}: no recent data")
            continue

        latest_soil = next((r.soil_moisture for r in readings if r.soil_moisture is not None), None)
        latest_temp = next((r.temperature for r in readings if r.temperature is not None), None)

        parts = []
        if latest_temp is not None:
            parts.append(f"{latest_temp:.1f}°C")
        if latest_soil is None:
            print(f"   📊 {sensor.name}: {' | '.join(parts) or 'no data'}")
            continue
        parts.append(f"soil:{latest_soil:.0f}%")

        plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
        care = plant_db_instance.get_care_data(species=plant.species if plant else None)
        target_raw = care.get("soil_moisture_target", "45-65")
        try:
            t_min, t_max = (float(x) for x in target_raw.split("-"))
        except Exception:
            t_min, t_max = 45.0, 65.0

        if latest_soil < t_min - 15:
            label, severity = "🚨 VERY DRY", "critical"
        elif latest_soil < t_min:
            label, severity = "⚠️ dry", "warning"
        elif latest_soil > t_max + 10:
            label, severity = "💧 wet", "ok"
        else:
            label, severity = "✅ ok", "ok"

        print(f"   📊 {sensor.name}: {' | '.join(parts)} → target {t_min:.0f}-{t_max:.0f}% [{label}]")

        if severity in ("critical", "warning"):
            plant_name = plant.species if plant else sensor.name
            needs_water.append({
                "sensor": sensor.name,
                "plant": plant_name,
                "soil": latest_soil,
                "t_min": t_min,
                "severity": severity,
            })

    if not needs_water:
        print("   ✅ All plants ok")
        return 0

    # Print structured ALERT lines for the agent to parse and forward
    print()
    print(f"ALERT: {cluster.name}")
    for item in needs_water:
        emoji = "🚨" if item["severity"] == "critical" else "⚠️"
        print(f"ALERT_ITEM: {emoji} {item['sensor']} ({item['plant']}): soil {item['soil']:.0f}% (target ≥{item['t_min']:.0f}%)")
    print("ALERT_END")

    return 2



def _collect_learning_alerts(db: IrrigationDB, cluster_id: int) -> list[dict]:
    """Return learning alerts for a cluster (advisory, never raises)."""
    try:
        from tuya_irrigation.learning import IrrigationLearner
        learner = IrrigationLearner(db)
        issues = learner.detect_issues(cluster_id)
        return [{"severity": a.severity, "message": a.message} for a in issues]
    except Exception:
        return []


def _check_cluster_irrigated(cluster_id: int, db: IrrigationDB, dm: TuyaDeviceManager | None) -> dict:
    """Run irrigation logic for a cluster with irrigator.

    Returns:
      {"action": "irrigated"|"skipped"|"error", "notes": str, "alerts": [...]}
    """

    cluster = db.get_cluster(cluster_id)
    if not cluster:
        return {"action": "error", "notes": "cluster not found", "alerts": []}

    is_indoor = cluster.environment == "indoor"

    # Sync sensors
    try:
        from tuya_irrigation.cloud import TuyaCloud
        from tuya_irrigation.logger_daemon import _sync_single_sensor
        cloud = TuyaCloud()
        for sensor in db.get_sensors_in_cluster(cluster_id):
            try:
                _sync_single_sensor(db, cloud, sensor, hours=6)
            except Exception:
                pass
        sensor_live = None
        sensors = db.get_sensors_in_cluster(cluster_id)
        if sensors:
            sensor_live = cloud.get_live_reading(sensors[0].tuya_device_id)
    except Exception:
        sensor_live = None

    # Temperature
    temp, source = None, "unknown"
    if is_indoor and sensor_live and sensor_live.get("temperature") is not None:
        temp, source = sensor_live["temperature"], "sensor"
    else:
        weather = _fetch_open_meteo()
        if weather and weather.get("feels_like") is not None:
            temp, source = weather["feels_like"], "open-meteo"
    if temp is None:
        temp, source = 20.0, "fallback"

    # Decide
    logic = IrrigationLogic(db)
    decision = logic.decide_for_cluster(cluster_id, current_temp=temp)
    if not decision:
        return {"action": "error", "notes": "no data for decision", "alerts": []}

    action = decision["action"]
    duration = decision["duration_minutes"]
    reason = decision["reason"]
    confidence = decision["confidence"]

    # Learning alerts
    alerts = _collect_learning_alerts(db, cluster_id)

    if action == "skip":
        return {
            "action": "skipped",
            "notes": f"{reason} (confidence {confidence:.0%})",
            "alerts": alerts,
        }

    # Execute
    irrigators = db.get_irrigators_in_cluster(cluster_id)
    if not irrigators:
        return {"action": "error", "notes": "no irrigators found", "alerts": alerts}

    if dm is None:
        return {"action": "error", "notes": "no device manager", "alerts": alerts}

    irrigator = irrigators[0]
    success, output = dm.irrigator_start(irrigator, duration)
    soil_note = f", soil={sensor_live['soil_moisture']:.0f}%" if sensor_live and sensor_live.get("soil_moisture") is not None else ""
    db.add_irrigation_event(
        irrigator_id=irrigator.id,
        action="start" if success else "attempted",
        duration_minutes=duration,
        triggered_by="auto",
        notes=f"temp={temp:.1f}°C ({source}){soil_note}, confidence={confidence:.0%}, reason={reason}",
    )

    if not success:
        return {"action": "error", "notes": f"irrigator failed: {output}", "alerts": alerts}

    notes_parts = [f"{duration}min", f"temp={temp:.1f}°C", f"confidence={confidence:.0%}"]
    if sensor_live and sensor_live.get("soil_moisture") is not None:
        notes_parts.append(f"soil={sensor_live['soil_moisture']:.0f}%")
    return {
        "action": "irrigated",
        "notes": "; ".join(notes_parts),
        "alerts": alerts,
    }


def _check_cluster_monitor(cluster_id: int, db: IrrigationDB) -> dict:
    """Run monitor logic for a cluster without irrigator.

    Returns:
      {"action": "monitored", "needs_water": [...], "alerts": [...]}
    """
    # Sync
    try:
        from tuya_irrigation.cloud import TuyaCloud
        from tuya_irrigation.logger_daemon import sync_sensor_data
        cloud = TuyaCloud()
        sync_sensor_data(db, cloud, hours=2)
    except Exception:
        pass

    sensors = db.get_sensors_in_cluster(cluster_id)
    if not sensors:
        return {"action": "monitored", "needs_water": [], "alerts": []}

    plant_db_instance = get_plant_database()
    plants_by_id = {p.id: p for p in db.get_plants_in_cluster(cluster_id)}
    needs_water = []

    for sensor in sensors:
        readings = db.get_recent_readings(sensor.id, hours=2)
        if not readings:
            continue
        latest_soil = next((r.soil_moisture for r in readings if r.soil_moisture is not None), None)
        if latest_soil is None:
            continue

        plant = plants_by_id.get(sensor.plant_id) if sensor.plant_id else None
        care = plant_db_instance.get_care_data(species=plant.species if plant else None)
        target_raw = care.get("soil_moisture_target", "45-65")
        try:
            t_min = float(target_raw.split("-")[0])
        except Exception:
            t_min = 45.0

        if latest_soil < t_min - 15:
            severity = "critical"
        elif latest_soil < t_min:
            severity = "warning"
        else:
            severity = "ok"

        if severity in ("critical", "warning"):
            plant_name = plant.species if plant else sensor.name
            needs_water.append({
                "sensor": sensor.name,
                "plant": plant_name,
                "soil": latest_soil,
                "t_min": t_min,
                "severity": severity,
            })

    alerts = _collect_learning_alerts(db, cluster_id)
    return {"action": "monitored", "needs_water": needs_water, "alerts": alerts}


def cmd_check(args, db: IrrigationDB, dm: TuyaDeviceManager | None):
    """Unified check: irrigate or monitor all clusters, collect all alerts.

    Auto-detects cluster type:
      - Has irrigator → irrigation logic
      - No irrigator  → monitor logic (soil check only)
    Always collects learning alerts (drainage, efficiency).

    Output format (for agent parsing):
      ACTION: irrigated|skipped|monitored|error <cluster_name> [<notes>]
      ALERT: <cluster_name> <type>
      ALERT_ITEM: <emoji> <message>
      ALERT_END

    Exit codes:
      0 = all ok / skipped, nothing to report
      2 = alerts present (agent should forward)
      1 = one or more errors
    """
    # Determine clusters to check
    if getattr(args, "all", False):
        clusters = db.list_clusters()
    else:
        c = db.get_cluster(args.cluster)
        clusters = [c] if c else []

    if not clusters:
        print("❌ No clusters found")
        return 1

    has_alerts = False
    has_errors = False

    for cluster in clusters:
        irrigators = db.get_irrigators_in_cluster(cluster.id)

        if irrigators:
            result = _check_cluster_irrigated(cluster.id, db, dm)
        else:
            result = _check_cluster_monitor(cluster.id, db)

        # Print ACTION line
        notes = result.get("notes", "")
        notes_str = f" — {notes}" if notes else ""
        print(f"ACTION: {result['action']} {cluster.name}{notes_str}")

        # Watering reminders (monitor clusters)
        needs_water = result.get("needs_water", [])
        if needs_water:
            has_alerts = True
            print(f"ALERT: {cluster.name} needs_water")
            for item in needs_water:
                emoji = "🚨" if item["severity"] == "critical" else "⚠️"
                print(f"ALERT_ITEM: {emoji} {item['sensor']} ({item['plant']}): soil {item['soil']:.0f}% (target ≥{item['t_min']:.0f}%)")
            print("ALERT_END")

        # Learning alerts
        learning_alerts = result.get("alerts", [])
        if learning_alerts:
            has_alerts = True
            print(f"ALERT: {cluster.name} learning")
            for a in learning_alerts:
                emoji = "🚨" if a["severity"] == "critical" else "⚠️"
                print(f"ALERT_ITEM: {emoji} [{a['severity']}] {a['message']}")
            print("ALERT_END")

        if result["action"] == "error":
            has_errors = True

    if has_errors:
        return 1
    if has_alerts:
        return 2
    return 0


def cmd_sync(args, db: IrrigationDB):
    """Sync sensor data from Tuya Cloud."""
    from tuya_irrigation.cloud import TuyaCloud
    from tuya_irrigation.logger_daemon import sync_sensor_data

    cloud = TuyaCloud()
    stats = sync_sensor_data(db, cloud, hours=args.hours)
    print(f"\n📊 Synced: {stats['total_new']} new readings")
    if stats["errors"]:
        for err in stats["errors"]:
            print(f"   ⚠️ {err}")
    return 0


def cmd_learn(args, db: IrrigationDB):
    """Learning report + alerts in one call."""
    from tuya_irrigation.learning import IrrigationLearner

    learner = IrrigationLearner(db)
    print(learner.generate_report(args.cluster))
    return 0


# ── Data Commands ─────────────────────────────────────────────────────────────


def cmd_history(args, db: IrrigationDB):
    """Sensor readings + irrigation events combined."""
    # Sensor readings
    sensors = db.get_sensors_in_cluster(args.cluster)
    for sensor in sensors:
        readings = db.get_recent_readings(sensor.id, hours=args.hours)
        if not readings:
            continue
        print(f"📊 {sensor.name} (last {args.hours}h, {len(readings)} readings):")
        for r in readings[:args.limit]:
            ts = format_timestamp(r.timestamp)
            parts = [ts]
            if r.temperature is not None:
                parts.append(f"temp={r.temperature:.1f}°C")
            if r.soil_moisture is not None:
                parts.append(f"soil={r.soil_moisture:.0f}%")
            if r.humidity is not None:
                parts.append(f"hum={r.humidity:.0f}%")
            print(f"  {' | '.join(parts)}")

    # Irrigation events
    irrigators = db.get_irrigators_in_cluster(args.cluster)
    total_duration = 0
    total_irrigations = 0
    for irrigator in irrigators:
        events = db.get_recent_events(irrigator.id, hours=args.hours)
        if not events:
            continue
        print(f"\n💧 {irrigator.name} (last {args.hours}h, {len(events)} events):")
        for e in events[:args.limit]:
            ts = format_timestamp(e.timestamp)
            dur = f" ({e.duration_minutes}min)" if e.duration_minutes else ""
            print(f"  {ts} | {e.action}{dur} [{e.triggered_by}]")
            if e.notes:
                print(f"    → {e.notes}")
            if e.action in ("start", "schedule_updated") and e.duration_minutes:
                total_duration += e.duration_minutes
                total_irrigations += 1

    if total_irrigations > 0:
        print(f"\n📊 Summary: {total_irrigations} irrigations, {total_duration}min total, {total_duration / total_irrigations:.1f}min avg")

    return 0


def cmd_stats(args, db: IrrigationDB):
    """Statistics + optional CSV export."""
    from tuya_irrigation.stats import export_csv, get_irrigation_stats, print_stats_report

    cluster = db.get_cluster(args.cluster)
    if not cluster:
        print(f"❌ Cluster {args.cluster} not found")
        return 1

    if args.export:
        export_csv(db, args.cluster, args.days, args.export)
    else:
        stats = get_irrigation_stats(db, args.cluster, args.days)
        print_stats_report(stats, cluster.name)
    return 0


# ── Setup Commands (CRUD) ────────────────────────────────────────────────────


def cmd_cluster_add(args, db: IrrigationDB):
    env = getattr(args, "environment", "indoor") or "indoor"
    cluster_id = db.add_cluster(args.name, args.location, environment=env)
    print(f"✅ Cluster created: {args.name} (ID: {cluster_id}, env: {env})")


def cmd_cluster_list(_args, db: IrrigationDB):
    clusters = db.list_clusters()
    if not clusters:
        print("No clusters.")
        return
    for c in clusters:
        loc = f" ({c.location})" if c.location else ""
        print(f"  [{c.id}] {c.name}{loc} [{c.environment}]")


def cmd_plant_add(args, db: IrrigationDB):
    plant_id = db.add_plant(
        cluster_id=args.cluster, species=args.species, category=args.category,
        water_needs=args.water_needs, light_needs=args.light_needs,
        ideal_temp_min=args.temp_min, ideal_temp_max=args.temp_max,
        ideal_humidity_min=args.humidity_min, ideal_humidity_max=args.humidity_max,
        notes=args.notes,
    )
    print(f"✅ Plant added: {args.species} (ID: {plant_id})")


def cmd_plant_list(args, db: IrrigationDB):
    clusters = [db.get_cluster(args.cluster)] if args.cluster else db.list_clusters()
    for cluster in clusters:
        if not cluster:
            continue
        plants = db.get_plants_in_cluster(cluster.id)
        if not plants:
            continue
        print(f"\n📦 {cluster.name}:")
        for p in plants:
            water = f" water:{p.water_needs}" if p.water_needs else ""
            cat = f" [{p.category}]" if p.category else ""
            print(f"  🌿 [{p.id}] {p.species}{cat}{water}")


def cmd_irrigator_add(args, db: IrrigationDB):
    config = {}
    if args.device_ip:
        config["device_ip"] = args.device_ip
    if args.local_key:
        config["local_key"] = args.local_key
    if args.interval:
        config["interval_hours"] = args.interval
    irrigator_id = db.add_irrigator(
        cluster_id=args.cluster, tuya_device_id=args.device_id,
        name=args.name, irrigator_type=args.type, config=config,
    )
    print(f"✅ Irrigator added: {args.name} (ID: {irrigator_id})")


def cmd_irrigator_list(args, db: IrrigationDB):
    clusters = [db.get_cluster(args.cluster)] if args.cluster else db.list_clusters()
    for cluster in clusters:
        if not cluster:
            continue
        irrigators = db.get_irrigators_in_cluster(cluster.id)
        if not irrigators:
            continue
        print(f"\n📦 {cluster.name}:")
        for irr in irrigators:
            print(f"  💧 [{irr.id}] {irr.name} [{irr.type}] (device: {irr.tuya_device_id})")


def cmd_irrigator_start(args, db: IrrigationDB, dm: TuyaDeviceManager):
    irrigator = _get_irrigator_or_exit(db, args.id)
    success, output = dm.irrigator_start(irrigator, args.minutes)
    if success:
        db.add_irrigation_event(
            irrigator_id=irrigator.id, action="start", duration_minutes=args.minutes,
            triggered_by="manual",
            notes=f"Manual START via CLI ({args.minutes} min)" if args.minutes else "Manual START via CLI",
        )
        msg = f"for {args.minutes} min" if args.minutes else "manually"
        print(f"✅ {irrigator.name} started {msg}")
    else:
        print(f"❌ Failed: {output}")
        return 1


def cmd_irrigator_stop(args, db: IrrigationDB, dm: TuyaDeviceManager):
    irrigator = _get_irrigator_or_exit(db, args.id)
    success, output = dm.irrigator_off(irrigator)
    if success:
        db.add_irrigation_event(
            irrigator_id=irrigator.id, action="off", triggered_by="manual", notes="Manual OFF via CLI",
        )
        print(f"✅ {irrigator.name} stopped")
    else:
        print(f"❌ Failed: {output}")
        return 1


def cmd_irrigator_log_manual(args, db: IrrigationDB):
    irrigator = _get_irrigator_or_exit(db, args.id)
    db.add_irrigation_event(
        irrigator_id=irrigator.id, action="start", duration_minutes=args.minutes,
        triggered_by="manual", notes=args.notes or f"Manual ({args.minutes} min)",
    )
    print(f"✅ Logged: {irrigator.name} for {args.minutes} min")


def cmd_sensor_add(args, db: IrrigationDB):
    config = {}
    if getattr(args, "device_ip", None):
        config["device_ip"] = args.device_ip
    if getattr(args, "local_key", None):
        config["local_key"] = args.local_key
    sensor_id = db.add_sensor(
        cluster_id=args.cluster, tuya_device_id=args.device_id,
        name=args.name, sensor_type=args.type, config=config,
        plant_id=getattr(args, "plant_id", None),
    )
    plant_info = f" → plant {args.plant_id}" if getattr(args, "plant_id", None) else ""
    print(f"✅ Sensor added: {args.name} (ID: {sensor_id}){plant_info}")


def cmd_sensor_list(args, db: IrrigationDB):
    clusters = [db.get_cluster(args.cluster)] if args.cluster else db.list_clusters()
    for cluster in clusters:
        if not cluster:
            continue
        sensors = db.get_sensors_in_cluster(cluster.id)
        if not sensors:
            continue
        print(f"\n📦 {cluster.name}:")
        for s in sensors:
            plant = f" → plant {s.plant_id}" if s.plant_id else ""
            print(f"  📊 [{s.id}] {s.name} [{s.type}]{plant}")


def cmd_config_set(args, db: IrrigationDB):
    db.set_irrigation_config(
        cluster_id=args.cluster, mode=args.mode,
        duration_minutes=args.minutes, interval_hours=args.interval,
        auto_run=args.auto_run,
    )
    print(f"✅ Config updated: cluster {args.cluster} → mode={args.mode}")


def cmd_config_get(args, db: IrrigationDB):
    config = db.get_irrigation_config(args.cluster)
    if not config:
        print(f"No config for cluster {args.cluster}")
        return
    print(f"⚙️  Cluster {args.cluster}: mode={config.mode} | {config.duration_minutes}min / {config.interval_hours}h | auto={'ON' if config.auto_run else 'OFF'}")


# ── CLI Parser ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Smart irrigation system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Operations (one call = full picture):
  status <cluster>     Full cluster overview
  irrigate <cluster>   Smart irrigation (--dry-run for analysis only)
  sync                 Sync sensor data from cloud
  learn <cluster>      Learning report + alerts
  history <cluster>    Readings + events timeline
  stats <cluster>      Statistics + CSV export

Setup (CRUD):
  cluster, plant, irrigator, sensor, config
""",
    )
    parser.add_argument("--db", help="Database path")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── Operations ──

    p_status = sub.add_parser("status", help="Full cluster status")
    p_status.add_argument("cluster", type=int, help="Cluster ID")

    p_irrigate = sub.add_parser("irrigate", help="Sync + weather + decide + execute")
    p_irrigate.add_argument("cluster", type=int, help="Cluster ID")
    p_irrigate.add_argument("--temp", type=float, help="Override temperature (skips sync + weather)")
    p_irrigate.add_argument("--dry-run", action="store_true", help="Analyze only, don't execute")
    p_irrigate.add_argument("--no-sync", action="store_true", help="Skip sensor sync (use DB data)")

    p_check = sub.add_parser("check", help="Unified check: irrigate or monitor all clusters + all alerts")
    check_group = p_check.add_mutually_exclusive_group(required=True)
    check_group.add_argument("cluster", nargs="?", type=int, help="Cluster ID")
    check_group.add_argument("--all", action="store_true", help="Check all clusters")

    p_monitor = sub.add_parser("monitor", help="Monitor sensor-only cluster (low-level, human-readable)")
    p_monitor.add_argument("cluster", type=int, help="Cluster ID")
    p_monitor.add_argument("--no-sync", action="store_true", help="Skip sensor sync")

    p_sync = sub.add_parser("sync", help="Sync sensor data from Tuya Cloud")
    p_sync.add_argument("--hours", type=int, default=24, help="History window (default: 24)")

    p_learn = sub.add_parser("learn", help="Learning report + efficiency alerts")
    p_learn.add_argument("cluster", type=int, help="Cluster ID")

    p_history = sub.add_parser("history", help="Readings + events timeline")
    p_history.add_argument("cluster", type=int, help="Cluster ID")
    p_history.add_argument("--hours", type=int, default=24, help="Hours (default: 24)")
    p_history.add_argument("--limit", type=int, default=15, help="Max entries per section (default: 15)")

    p_stats = sub.add_parser("stats", help="Statistics + CSV export")
    p_stats.add_argument("cluster", type=int, help="Cluster ID")
    p_stats.add_argument("--days", type=int, default=7, help="Days (default: 7)")
    p_stats.add_argument("--export", help="Export to CSV file")

    # ── Setup: Cluster ──

    p_cluster = sub.add_parser("cluster", help="Manage clusters")
    cluster_sub = p_cluster.add_subparsers(dest="cluster_cmd", required=True)
    p_ca = cluster_sub.add_parser("add", help="Add cluster")
    p_ca.add_argument("name", help="Cluster name")
    p_ca.add_argument("--location", help="Location")
    p_ca.add_argument("--environment", choices=["indoor", "outdoor"], default="indoor")
    cluster_sub.add_parser("list", help="List clusters")

    # ── Setup: Plant ──

    p_plant = sub.add_parser("plant", help="Manage plants")
    plant_sub = p_plant.add_subparsers(dest="plant_cmd", required=True)
    p_pa = plant_sub.add_parser("add", help="Add plant")
    p_pa.add_argument("--cluster", type=int, required=True)
    p_pa.add_argument("species", help="Species name")
    p_pa.add_argument("--category")
    p_pa.add_argument("--water-needs", choices=["low", "medium", "high"])
    p_pa.add_argument("--light-needs", choices=["low", "medium", "high"])
    p_pa.add_argument("--temp-min", type=float)
    p_pa.add_argument("--temp-max", type=float)
    p_pa.add_argument("--humidity-min", type=float)
    p_pa.add_argument("--humidity-max", type=float)
    p_pa.add_argument("--notes")
    p_pl = plant_sub.add_parser("list", help="List plants")
    p_pl.add_argument("--cluster", type=int)

    # ── Setup: Irrigator ──

    p_irr = sub.add_parser("irrigator", help="Manage irrigators")
    irr_sub = p_irr.add_subparsers(dest="irrigator_cmd", required=True)
    p_ia = irr_sub.add_parser("add", help="Add irrigator")
    p_ia.add_argument("--cluster", type=int, required=True)
    p_ia.add_argument("--device-id", required=True)
    p_ia.add_argument("--name", required=True)
    p_ia.add_argument("--type", required=True, choices=["tuya_cloud", "tuya_local"])
    p_ia.add_argument("--device-ip")
    p_ia.add_argument("--local-key")
    p_ia.add_argument("--interval", type=int)
    p_il = irr_sub.add_parser("list", help="List irrigators")
    p_il.add_argument("--cluster", type=int)
    p_is = irr_sub.add_parser("start", help="Start irrigation")
    p_is.add_argument("id", type=int)
    p_is.add_argument("--minutes", type=int)
    p_ist = irr_sub.add_parser("stop", help="Stop irrigation")
    p_ist.add_argument("id", type=int)
    p_ilm = irr_sub.add_parser("log-manual", help="Log manual irrigation (no device)")
    p_ilm.add_argument("id", type=int)
    p_ilm.add_argument("--minutes", type=int, required=True)
    p_ilm.add_argument("--notes")

    # ── Setup: Sensor ──

    p_sensor = sub.add_parser("sensor", help="Manage sensors")
    sensor_sub = p_sensor.add_subparsers(dest="sensor_cmd", required=True)
    p_sa = sensor_sub.add_parser("add", help="Add sensor")
    p_sa.add_argument("--cluster", type=int, required=True)
    p_sa.add_argument("--device-id", required=True)
    p_sa.add_argument("--name", required=True)
    p_sa.add_argument("--type", required=True)
    p_sa.add_argument("--plant-id", type=int)
    p_sl = sensor_sub.add_parser("list", help="List sensors")
    p_sl.add_argument("--cluster", type=int)

    # ── Setup: Config ──

    p_config = sub.add_parser("config", help="Irrigation config")
    config_sub = p_config.add_subparsers(dest="config_cmd", required=True)
    p_cs = config_sub.add_parser("set", help="Set config")
    p_cs.add_argument("--cluster", type=int, required=True)
    p_cs.add_argument("--mode", required=True, choices=["manual", "schedule", "smart"])
    p_cs.add_argument("--minutes", type=int)
    p_cs.add_argument("--interval", type=int)
    p_cs.add_argument("--auto-run", type=bool, default=True)
    p_cg = config_sub.add_parser("get", help="Get config")
    p_cg.add_argument("cluster", type=int)

    args = parser.parse_args()

    # ── Init ──

    db_path = Path(args.db) if args.db else None
    db = IrrigationDB(db_path)

    # Device manager: only init when needed (requires Tuya credentials)
    dm = None
    needs_dm = args.command in ("status", "irrigate", "check") or (
        args.command == "irrigator" and args.irrigator_cmd in ("start", "stop")
    )
    if needs_dm:
        try:
            dm = TuyaDeviceManager()
        except ValueError:
            if args.command == "irrigate":
                print("❌ Missing Tuya credentials")
                return 1
            # status can work without dm (no live sensor reads)

    # ── Dispatch ──

    try:
        if args.command == "status":
            return cmd_status(args, db, dm)
        elif args.command == "check":
            return cmd_check(args, db, dm)
        elif args.command == "monitor":
            return cmd_monitor(args, db)
        elif args.command == "irrigate":
            return cmd_irrigate(args, db, dm)
        elif args.command == "sync":
            return cmd_sync(args, db)
        elif args.command == "learn":
            return cmd_learn(args, db)
        elif args.command == "history":
            return cmd_history(args, db)
        elif args.command == "stats":
            return cmd_stats(args, db)

        elif args.command == "cluster":
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
            elif args.irrigator_cmd == "start":
                return cmd_irrigator_start(args, db, dm)
            elif args.irrigator_cmd == "stop":
                return cmd_irrigator_stop(args, db, dm)
            elif args.irrigator_cmd == "log-manual":
                return cmd_irrigator_log_manual(args, db)

        elif args.command == "sensor":
            if args.sensor_cmd == "add":
                cmd_sensor_add(args, db)
            elif args.sensor_cmd == "list":
                cmd_sensor_list(args, db)

        elif args.command == "config":
            if args.config_cmd == "set":
                cmd_config_set(args, db)
            elif args.config_cmd == "get":
                cmd_config_get(args, db)

    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

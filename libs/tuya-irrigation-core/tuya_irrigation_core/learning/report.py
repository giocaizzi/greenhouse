"""Human-readable learning report generation."""

from tuya_irrigation_core.learning.issues import detect_issues
from tuya_irrigation_core.learning.profiling import get_plant_profile
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository


def generate_report(
    db: IrrigationRepository,
    plant_db: PlantDatabase,
    cluster_id: int,
) -> str:
    """Generate a human-readable learning report for a cluster."""
    lines = []
    sensors = db.get_sensors_in_cluster(cluster_id)

    if not sensors:
        return "No sensors in cluster."

    lines.append("📊 Irrigation Learning Report")
    lines.append("=" * 40)

    for sensor in sensors:
        profile = get_plant_profile(db, sensor)
        if not profile:
            lines.append(f"\n🌱 {sensor.name}: insufficient data (need more irrigation cycles)")
            continue

        lines.append(f"\n🌱 {sensor.name}")
        lines.append(f"   Data points: {profile.response_count} irrigation events")
        lines.append(f"   Absorption: {profile.avg_absorption_per_minute:+.1f}%/min of irrigation")
        lines.append(f"   Drainage: {profile.avg_drainage_per_hour:.1f}%/hr (natural drying)")
        lines.append(f"   Response range: {profile.min_delta:+.0f}% to {profile.max_delta:+.0f}%")
        lines.append(f"   Efficiency: {profile.efficiency_score:.0%}")

        if profile.efficiency_score < 0.5:
            lines.append("   ⚠️ Low efficiency — check drip positioning")

    # Alerts
    alerts = detect_issues(db, plant_db, cluster_id)
    if alerts:
        lines.append(f"\n{'=' * 40}")
        lines.append("🚨 Alerts")
        for alert in alerts:
            lines.append(f"   [{alert.severity.upper()}] {alert.message}")

    return "\n".join(lines)

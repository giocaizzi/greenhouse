"""Cluster-level care insights aggregated from learning + maintenance + decisions."""

from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository
from tuya_irrigation_core.schemas import CareInsight, ClusterInsightsResponse
from tuya_irrigation_server.services.maintenance import collect_learning_alerts, collect_maintenance_alerts

_ALERT_TYPE_META: dict[str, tuple[str, str, str]] = {
    "stale_data": ("warning", "Stale sensor data", "Check Wi-Fi connection and sensor battery."),
    "battery_low": ("warning", "Battery low", "Replace or recharge the sensor battery soon."),
    "low_env_humidity": ("warning", "Low ambient humidity", "Move a humidifier closer or mist leaves regularly."),
    "low_light": ("warning", "Insufficient light", "Relocate the plant to a brighter spot or add a grow light."),
    "blocked_drip": ("critical", "Blocked drip detected", "Inspect and clear the drip emitter or nozzle."),
    "rapid_drainage": ("warning", "Rapid drainage", "Check for root-bound conditions or soil mix porosity."),
    "chronic_underwatering": ("warning", "Chronic underwatering", "Increase irrigation frequency or duration."),
    "unresolvable_conflict": ("warning", "Sensor conflict", "Verify all sensors are correctly assigned to plants."),
}


class InsightsService:
    """Aggregate learning and maintenance data into structured CareInsight items."""

    def __init__(self, repo: IrrigationRepository, plant_db: PlantDatabase):
        self._repo = repo
        self._plant_db = plant_db

    def cluster_insights(self, cluster_id: int) -> ClusterInsightsResponse | None:
        """Return structured insights for a cluster.

        Args:
            cluster_id: Cluster to analyse.

        Returns:
            ClusterInsightsResponse with deduplicated CareInsight list, or None if cluster not found.
        """
        cluster = self._repo.get_cluster(cluster_id)
        if not cluster:
            return None

        insights: list[CareInsight] = []
        seen_codes: set[str] = set()

        for alert in collect_maintenance_alerts(self._repo, cluster_id, self._plant_db):
            code = alert["type"]
            if code in seen_codes:
                continue
            seen_codes.add(code)
            meta = _ALERT_TYPE_META.get(code)
            severity = meta[0] if meta else alert.get("severity", "warning")
            title = meta[1] if meta else code.replace("_", " ").title()
            suggestion = meta[2] if meta else None
            insights.append(
                CareInsight(code=code, severity=severity, title=title, message=alert["message"], suggestion=suggestion)
            )

        for alert in collect_learning_alerts(self._repo, cluster_id, self._plant_db):
            code = alert["type"]
            if code in seen_codes:
                continue
            seen_codes.add(code)
            meta = _ALERT_TYPE_META.get(code)
            severity = meta[0] if meta else alert.get("severity", "warning")
            title = meta[1] if meta else code.replace("_", " ").title()
            suggestion = meta[2] if meta else None
            insights.append(
                CareInsight(code=code, severity=severity, title=title, message=alert["message"], suggestion=suggestion)
            )

        logs = self._repo.list_decision_logs(cluster_id, limit=1)
        if logs:
            log = logs[0]
            insights.append(
                CareInsight(
                    code=log.primary_code or "last_decision",
                    severity="info",
                    title=f"Last decision: {log.action}",
                    message=log.reason_text,
                    suggestion=None,
                )
            )

        return ClusterInsightsResponse(
            cluster_id=cluster_id,
            cluster_name=cluster.name,
            insights=insights,
            forecast=None,
        )

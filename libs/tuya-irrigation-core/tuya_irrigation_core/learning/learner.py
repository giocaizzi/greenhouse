"""Thin facade class for the learning engine."""

from tuya_irrigation_core.learning.issues import detect_issues
from tuya_irrigation_core.learning.models import Alert, IrrigationResponse, PlantProfile
from tuya_irrigation_core.learning.profiling import analyze_irrigation_response, get_plant_profile
from tuya_irrigation_core.learning.report import generate_report
from tuya_irrigation_core.models import IrrigationEvent, Sensor
from tuya_irrigation_core.plant_db import PlantDatabase
from tuya_irrigation_core.repository import IrrigationRepository


class IrrigationLearner:
    """Learns from historical irrigation data."""

    def __init__(self, db: IrrigationRepository, plant_db: PlantDatabase):
        self.db = db
        self.plant_db = plant_db

    def analyze_irrigation_response(self, event: IrrigationEvent) -> list[IrrigationResponse]:
        """Analyze soil moisture changes for all sensors after an irrigation event."""
        return analyze_irrigation_response(self.db, event)

    def get_plant_profile(self, sensor: Sensor, days: int = 30) -> PlantProfile | None:
        """Build a learned profile for a plant based on historical irrigation responses."""
        return get_plant_profile(self.db, sensor, days)

    def detect_issues(self, cluster_id: int) -> list[Alert]:
        """Detect efficiency issues and unresolvable conflicts."""
        return detect_issues(self.db, self.plant_db, cluster_id)

    def generate_report(self, cluster_id: int) -> str:
        """Generate a human-readable learning report for a cluster."""
        return generate_report(self.db, self.plant_db, cluster_id)

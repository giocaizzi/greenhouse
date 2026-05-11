"""Data-quality report: configuration gaps, stale sensors, duplicate device IDs."""

from fastapi import APIRouter

from greenhouse_core.schemas import DataQualityReport
from greenhouse_server.deps import PlantDbDep, RepoDep
from greenhouse_server.services.data_quality import build_report

router = APIRouter(tags=["operations"])


@router.get("/quality/report", response_model=DataQualityReport)
def quality_report(repo: RepoDep, plant_db: PlantDbDep) -> DataQualityReport:
    """Scan the database and return a structured data-quality report.

    Detects: sensors with no plant assignment, plants with no sensor, irrigators
    in plant-less clusters, clusters without an irrigation config, sensors with
    no reading in the last 24 hours, duplicate Tuya device IDs (critical), and
    plant species absent from the plant database.

    Returns:
        DataQualityReport with a flat list of DataQualityIssue items and a
        per-code count dict summarising how many instances of each issue type
        were found.
    """
    return build_report(repo, plant_db)

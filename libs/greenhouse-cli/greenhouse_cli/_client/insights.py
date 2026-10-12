"""Read-model endpoints: forecast, activity, insights, search, plant health and charts."""

from greenhouse_cli._client.transport import JSONObject, Transport, drop_none


class InsightEndpoints(Transport):
    """Forecast, activity, insight, search, health and chart calls."""

    def forecast(self, cluster_id: int) -> JSONObject:
        """Fetch a cluster's next-watering forecast (``GET /clusters/{id}/forecast``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/forecast")

    def list_activity(self, limit: int = 100, before: int | None = None, **filters: str | int | None) -> JSONObject:
        """Page through the activity log; ``None`` filters are omitted (``GET /activity``)."""
        params: dict[str, int | str] = {"limit": limit}
        if before is not None:
            params["before"] = before
        params.update(drop_none(filters))
        return self._object("GET", "/api/v1/activity", params=params)

    def insights(self, cluster_id: int) -> JSONObject:
        """Fetch care insights for a cluster (``GET /clusters/{id}/insights``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/insights")

    def efficacy(self, cluster_id: int, days: int = 14) -> JSONObject:
        """Fetch per-event irrigation efficacy for the last ``days`` (``GET /clusters/{id}/efficacy``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/efficacy", params={"days": days})

    def quality_report(self) -> JSONObject:
        """Fetch the data-quality report (``GET /quality/report``)."""
        return self._object("GET", "/api/v1/quality/report")

    def search(self, query: str, limit: int = 20) -> JSONObject:
        """Search clusters, plants, sensors and irrigators (``GET /search``)."""
        return self._object("GET", "/api/v1/search", params={"q": query, "limit": limit})

    def plant_health(self, plant_id: int) -> JSONObject:
        """Fetch a plant's current health score (``GET /plants/{plant_id}/health``)."""
        return self._object("GET", f"/api/v1/plants/{plant_id}/health")

    def health_snapshot(self) -> JSONObject:
        """Record a plant-health snapshot for every plant now (``POST /plants/health/snapshot``)."""
        return self._object("POST", "/api/v1/plants/health/snapshot")

    # ── Charts ──

    def cluster_chart_data(self, cluster_id: int, hours: int = 24, metric: str = "soil_moisture") -> JSONObject:
        """Fetch one metric's per-sensor series for a cluster (``GET /clusters/{id}/chart-data``)."""
        return self._object(
            "GET", f"/api/v1/clusters/{cluster_id}/chart-data", params={"hours": hours, "metric": metric}
        )

    def plant_chart_data(self, plant_id: int, hours: int = 24, metric: str = "soil_moisture") -> JSONObject:
        """Fetch one metric's series for a plant's sensors (``GET /plants/{plant_id}/chart-data``)."""
        return self._object("GET", f"/api/v1/plants/{plant_id}/chart-data", params={"hours": hours, "metric": metric})

    def cluster_overlay(self, cluster_id: int, hours: int = 72) -> JSONObject:
        """Fetch the normalised soil/humidity/light overlay (``GET /clusters/{id}/overlay``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/overlay", params={"hours": hours})

    def cluster_heatmap(self, cluster_id: int, days: int = 30) -> JSONObject:
        """Fetch the weekday-by-hour irrigation heatmap (``GET /clusters/{id}/heatmap``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/heatmap", params={"days": days})

    def plant_health_timeline(self, plant_id: int) -> JSONObject:
        """Fetch a plant's health-score timeline (``GET /plants/{plant_id}/health-timeline``)."""
        return self._object("GET", f"/api/v1/plants/{plant_id}/health-timeline")

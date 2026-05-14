"""HTTP client for the greenhouse server API."""

import httpx


class ServerError(Exception):
    """Raised when the server returns an error response."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Server error {status_code}: {detail}")


class IrrigationClient:
    """Thin HTTP client wrapping the greenhouse REST API."""

    def __init__(self, base_url: str = "http://localhost:8000", **kwargs):
        self.http = httpx.Client(base_url=base_url, timeout=30.0, **kwargs)

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        try:
            resp = self.http.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise ServerError(0, f"Cannot connect to server: {e}") from None

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise ServerError(resp.status_code, detail)

        if resp.headers.get("content-type", "").startswith("text/csv"):
            return {"csv": resp.text}

        return resp.json()

    # ── Clusters ──

    def create_cluster(self, name: str, location: str | None = None, environment: str = "indoor") -> dict:
        body = {"name": name, "location": location, "environment": environment}
        return self._request("POST", "/api/v1/clusters", json=body)

    def list_clusters(self) -> list:
        return self._request("GET", "/api/v1/clusters")

    def get_cluster(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}")

    # ── Plants ──

    def add_plant(self, cluster_id: int, **kwargs) -> dict:
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/plants", json=kwargs)

    def list_plants(self, cluster_id: int) -> list:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/plants")

    def sync_plants(self, plant_id: int | None = None, cluster_id: int | None = None) -> dict:
        body = {"plant_id": plant_id, "cluster_id": cluster_id}
        return self._request("POST", "/api/v1/plants/sync", json=body)

    def move_plant(self, plant_id: int, target_cluster_id: int) -> dict:
        body = {"target_cluster_id": target_cluster_id}
        return self._request("POST", f"/api/v1/plants/{plant_id}/move", json=body)

    # ── Irrigators ──

    def add_irrigator(self, cluster_id: int, **kwargs) -> dict:
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/irrigators", json=kwargs)

    def list_irrigators(self, cluster_id: int) -> list:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/irrigators")

    def start_irrigator(self, irrigator_id: int, minutes: int | None = None) -> dict:
        return self._request("POST", f"/api/v1/irrigators/{irrigator_id}/start", json={"minutes": minutes})

    def stop_irrigator(self, irrigator_id: int) -> dict:
        return self._request("POST", f"/api/v1/irrigators/{irrigator_id}/stop")

    def log_manual(self, irrigator_id: int, minutes: int, notes: str | None = None) -> dict:
        return self._request(
            "POST", f"/api/v1/irrigators/{irrigator_id}/log-manual", json={"minutes": minutes, "notes": notes}
        )

    # ── Sensors ──

    def add_sensor(self, cluster_id: int, **kwargs) -> dict:
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/sensors", json=kwargs)

    def list_sensors(self, cluster_id: int) -> list:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/sensors")

    # ── Config ──

    def set_config(self, cluster_id: int, **kwargs) -> dict:
        return self._request("PUT", f"/api/v1/clusters/{cluster_id}/config", json=kwargs)

    def get_config(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/config")

    # ── Operations ──

    def status(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/status")

    def irrigate(
        self,
        cluster_id: int,
        temp_override: float | None = None,
        dry_run: bool = False,
        no_sync: bool = False,
    ) -> dict:
        body = {"temp_override": temp_override, "dry_run": dry_run, "no_sync": no_sync}
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/irrigate", json=body)

    def monitor(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/monitor")

    def check(self, cluster_id: int | None = None) -> dict:
        if cluster_id is None:
            return self._request("POST", "/api/v1/check")
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/check")

    def sync(self, hours: int = 24) -> dict:
        return self._request("POST", "/api/v1/sync", json={"hours": hours})

    def learn(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/learn")

    def history(self, cluster_id: int, hours: int = 24, limit: int = 50) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/history", params={"hours": hours, "limit": limit})

    def stats(self, cluster_id: int, days: int = 7) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/stats", params={"days": days})

    def stats_export(self, cluster_id: int, days: int = 7) -> str:
        result = self._request("GET", f"/api/v1/clusters/{cluster_id}/stats/export", params={"days": days})
        return result.get("csv", "")

    def health(self) -> dict:
        return self._request("GET", "/api/v1/health")

    def scheduler_jobs(self) -> list:
        return self._request("GET", "/api/v1/scheduler/jobs")

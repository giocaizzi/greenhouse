"""HTTP client for the greenhouse server API."""

import os
from pathlib import Path

import httpx


def _default_token_path() -> Path:
    """Resolve the on-disk session token location.

    Honours $XDG_CONFIG_HOME and falls back to ``~/.config/greenhouse/token``.
    """
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "greenhouse" / "token"


def load_stored_token() -> str | None:
    """Return the session JWT cached on disk by ``greenhouse login``, if any.

    ``$GREENHOUSE_API_TOKEN`` wins when set; otherwise the disk file is read.
    Whitespace is stripped, empty files are treated as absent.
    """
    env_token = os.environ.get("GREENHOUSE_API_TOKEN")
    if env_token:
        return env_token.strip() or None
    path = _default_token_path()
    if not path.exists():
        return None
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def store_token(token: str) -> Path:
    """Persist a session JWT under the user config directory.

    Args:
        token: The bearer token returned by ``POST /api/v1/auth/login``.

    Returns:
        Path to the file that was written.
    """
    path = _default_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def clear_stored_token() -> bool:
    """Delete the cached session JWT file.

    Returns:
        ``True`` if a file was removed, ``False`` if no token was on disk.
    """
    path = _default_token_path()
    if not path.exists():
        return False
    path.unlink()
    return True


class ServerError(Exception):
    """Raised when the server returns an error response."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Server error {status_code}: {detail}")


class IrrigationClient:
    """Thin HTTP client wrapping the greenhouse REST API."""

    def __init__(self, base_url: str = "http://localhost:8000", token: str | None = None, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        resolved = token if token is not None else load_stored_token()
        if resolved:
            headers["Authorization"] = f"Bearer {resolved}"
        self.http = httpx.Client(base_url=base_url, timeout=30.0, headers=headers, **kwargs)

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

    # ── Auth ──

    def login(self, username: str, password: str) -> dict:
        """Exchange username/password for a session JWT."""
        return self._request("POST", "/api/v1/auth/login", json={"username": username, "password": password})

    def logout(self) -> dict:
        """Clear the server-side session cookie."""
        return self._request("POST", "/api/v1/auth/logout")

    def whoami(self) -> dict:
        """Return the authenticated user record."""
        return self._request("GET", "/api/v1/auth/me")

    # ── Clusters ──

    def create_cluster(self, name: str, location: str | None = None, environment: str = "indoor") -> dict:
        body = {"name": name, "location": location, "environment": environment}
        return self._request("POST", "/api/v1/clusters", json=body)

    def list_clusters(self) -> list:
        return self._request("GET", "/api/v1/clusters")

    def get_cluster(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}")

    def update_cluster(self, cluster_id: int, **kwargs) -> dict:
        body = {k: v for k, v in kwargs.items() if v is not None}
        return self._request("PUT", f"/api/v1/clusters/{cluster_id}", json=body)

    def delete_cluster(self, cluster_id: int) -> dict:
        return self._request("DELETE", f"/api/v1/clusters/{cluster_id}")

    # ── Plants ──

    def add_plant(self, cluster_id: int, **kwargs) -> dict:
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/plants", json=kwargs)

    def list_plants(self, cluster_id: int) -> list:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/plants")

    def update_plant(self, cluster_id: int, plant_id: int, **kwargs) -> dict:
        body = {k: v for k, v in kwargs.items() if v is not None}
        return self._request("PUT", f"/api/v1/clusters/{cluster_id}/plants/{plant_id}", json=body)

    def delete_plant(self, cluster_id: int, plant_id: int) -> dict:
        return self._request("DELETE", f"/api/v1/clusters/{cluster_id}/plants/{plant_id}")

    def sync_plants(self, plant_id: int | None = None, cluster_id: int | None = None) -> dict:
        body = {"plant_id": plant_id, "cluster_id": cluster_id}
        return self._request("POST", "/api/v1/plants/sync", json=body)

    def move_plant(self, plant_id: int, target_cluster_id: int) -> dict:
        body = {"target_cluster_id": target_cluster_id}
        return self._request("POST", f"/api/v1/plants/{plant_id}/move", json=body)

    # ── Irrigators ──

    def add_irrigator(self, cluster_id: int, **kwargs) -> dict:
        body = {k: v for k, v in kwargs.items() if v is not None}
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/irrigators", json=body)

    def list_irrigators(self, cluster_id: int) -> list:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/irrigators")

    def update_irrigator(self, cluster_id: int, irrigator_id: int, **kwargs) -> dict:
        body = {k: v for k, v in kwargs.items() if v is not None}
        return self._request("PUT", f"/api/v1/clusters/{cluster_id}/irrigators/{irrigator_id}", json=body)

    def delete_irrigator(self, cluster_id: int, irrigator_id: int) -> dict:
        return self._request("DELETE", f"/api/v1/clusters/{cluster_id}/irrigators/{irrigator_id}")

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

    def update_sensor(self, cluster_id: int, sensor_id: int, **kwargs) -> dict:
        body = {k: v for k, v in kwargs.items() if v is not None}
        return self._request("PUT", f"/api/v1/clusters/{cluster_id}/sensors/{sensor_id}", json=body)

    def delete_sensor(self, cluster_id: int, sensor_id: int) -> dict:
        return self._request("DELETE", f"/api/v1/clusters/{cluster_id}/sensors/{sensor_id}")

    # ── Config ──

    def set_config(self, cluster_id: int, **kwargs) -> dict:
        body = {k: v for k, v in kwargs.items() if v is not None}
        return self._request("PUT", f"/api/v1/clusters/{cluster_id}/config", json=body)

    def get_config(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/config")

    def get_effective_config(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/config/effective")

    def get_global_config(self) -> dict:
        return self._request("GET", "/api/v1/config/global")

    def update_global_config(self, **kwargs) -> dict:
        body = {k: v for k, v in kwargs.items() if v is not None}
        return self._request("PUT", "/api/v1/config/global", json=body)

    # ── Operations ──

    def status(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/status")

    def irrigate(
        self,
        cluster_id: int,
        temp_override: float | None = None,
        dry_run: bool = False,
        no_sync: bool = False,
        force: bool = False,
    ) -> dict:
        body = {"temp_override": temp_override, "dry_run": dry_run, "no_sync": no_sync, "force": force}
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/irrigate", json=body)

    def monitor(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/monitor")

    def check(self, cluster_id: int | None = None) -> dict:
        if cluster_id is None:
            return self._request("POST", "/api/v1/check")
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/check")

    def sync(self, hours: int = 24) -> dict:
        return self._request("POST", "/api/v1/sync", json={"hours": hours})

    def bulk_stop_all(self) -> dict:
        return self._request("POST", "/api/v1/bulk/stop-all")

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

    def scheduler_pause(self) -> dict:
        return self._request("POST", "/api/v1/scheduler/pause")

    def scheduler_resume(self) -> dict:
        return self._request("POST", "/api/v1/scheduler/resume")

    # ── Alerts ──

    def list_alerts(
        self,
        status: str | None = None,
        cluster_id: int | None = None,
        plant_id: int | None = None,
        limit: int = 100,
    ) -> dict:
        params: dict[str, int | str] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if cluster_id is not None:
            params["cluster_id"] = cluster_id
        if plant_id is not None:
            params["plant_id"] = plant_id
        return self._request("GET", "/api/v1/alerts", params=params)

    def get_alert(self, alert_id: int) -> dict:
        return self._request("GET", f"/api/v1/alerts/{alert_id}")

    def acknowledge_alert(self, alert_id: int) -> dict:
        return self._request("POST", f"/api/v1/alerts/{alert_id}/acknowledge")

    def resolve_alert(self, alert_id: int) -> dict:
        return self._request("POST", f"/api/v1/alerts/{alert_id}/resolve")

    def sync_alerts(self, cluster_id: int | None = None) -> dict:
        if cluster_id is None:
            return self._request("POST", "/api/v1/alerts/sync")
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/alerts/sync")

    # ── Decisions ──

    def list_decisions(self, cluster_id: int, limit: int = 50) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/decisions", params={"limit": limit})

    # ── Preferences ──

    def get_preferences(self) -> dict:
        return self._request("GET", "/api/v1/preferences")

    def update_preferences(self, **kwargs) -> dict:
        body = {k: v for k, v in kwargs.items() if v is not None}
        return self._request("PUT", "/api/v1/preferences", json=body)

    # ── Vacation ──

    def list_vacation(self) -> dict:
        return self._request("GET", "/api/v1/vacation")

    def add_vacation(
        self,
        starts_at: int,
        ends_at: int,
        contact_email: str | None = None,
        notes: str | None = None,
    ) -> dict:
        body = {
            "starts_at": starts_at,
            "ends_at": ends_at,
            "contact_email": contact_email,
            "notes": notes,
        }
        return self._request("POST", "/api/v1/vacation", json=body)

    def update_vacation(
        self,
        window_id: int,
        starts_at: int | None = None,
        ends_at: int | None = None,
        contact_email: str | None = None,
        notes: str | None = None,
    ) -> dict:
        body: dict = {}
        if starts_at is not None:
            body["starts_at"] = starts_at
        if ends_at is not None:
            body["ends_at"] = ends_at
        if contact_email is not None:
            body["contact_email"] = contact_email
        if notes is not None:
            body["notes"] = notes
        return self._request("PUT", f"/api/v1/vacation/{window_id}", json=body)

    def delete_vacation(self, window_id: int) -> dict:
        return self._request("DELETE", f"/api/v1/vacation/{window_id}")

    # ── Irrigation windows ──

    def list_windows(self, cluster_id: int) -> dict:
        return self._request("GET", f"/api/v1/clusters/{cluster_id}/windows")

    def add_window(
        self,
        cluster_id: int,
        start_hour: int,
        end_hour: int,
        weekday_mask: int = 127,
        label: str | None = None,
    ) -> dict:
        body = {
            "start_hour": start_hour,
            "end_hour": end_hour,
            "weekday_mask": weekday_mask,
            "label": label,
        }
        return self._request("POST", f"/api/v1/clusters/{cluster_id}/windows", json=body)

    def update_window(
        self,
        cluster_id: int,
        window_id: int,
        start_hour: int | None = None,
        end_hour: int | None = None,
        weekday_mask: int | None = None,
        label: str | None = None,
    ) -> dict:
        body: dict = {}
        if start_hour is not None:
            body["start_hour"] = start_hour
        if end_hour is not None:
            body["end_hour"] = end_hour
        if weekday_mask is not None:
            body["weekday_mask"] = weekday_mask
        if label is not None:
            body["label"] = label
        return self._request("PUT", f"/api/v1/clusters/{cluster_id}/windows/{window_id}", json=body)

    def delete_window(self, cluster_id: int, window_id: int) -> dict:
        return self._request("DELETE", f"/api/v1/clusters/{cluster_id}/windows/{window_id}")

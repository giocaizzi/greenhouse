"""HTTP client for the greenhouse server API."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import httpx

from greenhouse_cli.constants import ALL_WEEKDAYS, DEFAULT_SERVER_URL

JSONObject = dict[str, Any]


def _drop_none(fields: Mapping[str, Any]) -> JSONObject:
    """Omit unset (``None``) fields so the server applies its default or keeps the current value; order is kept."""
    return {k: v for k, v in fields.items() if v is not None}


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

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Server error {status_code}: {detail}")


class IrrigationClient:
    """Thin HTTP client wrapping the greenhouse REST API."""

    def __init__(self, base_url: str = DEFAULT_SERVER_URL, token: str | None = None, **kwargs: Any) -> None:
        """Build the HTTP session; ``token=None`` falls back to the stored token, ``""`` sends no bearer header."""
        headers = dict(kwargs.pop("headers", {}) or {})
        resolved = token if token is not None else load_stored_token()
        if resolved:
            headers["Authorization"] = f"Bearer {resolved}"
        self.http = httpx.Client(base_url=base_url, timeout=30.0, headers=headers, **kwargs)

    def _request(self, method: str, path: str, **kwargs: Any) -> JSONObject | list[Any]:
        """Send one request and decode it; HTTP >= 400 and connection errors raise :class:`ServerError`."""
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

        data: JSONObject | list[Any] = resp.json()
        return data

    def _object(self, method: str, path: str, **kwargs: Any) -> JSONObject:
        """Send a request whose endpoint answers with a JSON object."""
        return cast(JSONObject, self._request(method, path, **kwargs))

    def _array(self, method: str, path: str, **kwargs: Any) -> list[JSONObject]:
        """Send a request whose endpoint answers with a JSON array of objects."""
        return cast(list[JSONObject], self._request(method, path, **kwargs))

    # ── Auth ──

    def login(self, username: str, password: str) -> JSONObject:
        """Exchange username/password for a session JWT."""
        return self._object("POST", "/api/v1/auth/login", json={"username": username, "password": password})

    def logout(self) -> JSONObject:
        """Clear the server-side session cookie."""
        return self._object("POST", "/api/v1/auth/logout")

    def whoami(self) -> JSONObject:
        """Return the authenticated user record."""
        return self._object("GET", "/api/v1/auth/me")

    # ── Clusters ──

    def create_cluster(self, name: str, location: str | None = None, environment: str = "indoor") -> JSONObject:
        """Create a cluster (``POST /clusters``)."""
        body = {"name": name, "location": location, "environment": environment}
        return self._object("POST", "/api/v1/clusters", json=body)

    def list_clusters(self) -> list[JSONObject]:
        """List every cluster (``GET /clusters``)."""
        return self._array("GET", "/api/v1/clusters")

    def get_cluster(self, cluster_id: int) -> JSONObject:
        """Fetch one cluster record (``GET /clusters/{id}``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}")

    def get_cluster_detail(self, cluster_id: int) -> JSONObject:
        """Fetch a cluster with its child resources inlined (``GET /clusters/{id}/detail``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/detail")

    def update_cluster(self, cluster_id: int, **kwargs: Any) -> JSONObject:
        """Patch a cluster; ``None`` fields are left unchanged (``PUT /clusters/{id}``)."""
        body = _drop_none(kwargs)
        return self._object("PUT", f"/api/v1/clusters/{cluster_id}", json=body)

    def delete_cluster(self, cluster_id: int) -> JSONObject:
        """Delete a cluster and everything attached to it (``DELETE /clusters/{id}``)."""
        return self._object("DELETE", f"/api/v1/clusters/{cluster_id}")

    # ── Plants ──

    def add_plant(self, cluster_id: int, **kwargs: Any) -> JSONObject:
        """Add a plant to a cluster; fields are sent as given (``POST /clusters/{id}/plants``)."""
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/plants", json=kwargs)

    def list_plants(self, cluster_id: int) -> list[JSONObject]:
        """List a cluster's plants (``GET /clusters/{id}/plants``)."""
        return self._array("GET", f"/api/v1/clusters/{cluster_id}/plants")

    def update_plant(self, cluster_id: int, plant_id: int, **kwargs: Any) -> JSONObject:
        """Patch a plant; ``None`` fields are left unchanged (``PUT /clusters/{id}/plants/{plant_id}``)."""
        body = _drop_none(kwargs)
        return self._object("PUT", f"/api/v1/clusters/{cluster_id}/plants/{plant_id}", json=body)

    def delete_plant(self, cluster_id: int, plant_id: int) -> JSONObject:
        """Remove a plant from a cluster (``DELETE /clusters/{id}/plants/{plant_id}``)."""
        return self._object("DELETE", f"/api/v1/clusters/{cluster_id}/plants/{plant_id}")

    def sync_plants(self, plant_id: int | None = None, cluster_id: int | None = None) -> JSONObject:
        """Refresh plant care data from the plant DB, optionally for one plant or cluster (``POST /plants/sync``)."""
        body = {"plant_id": plant_id, "cluster_id": cluster_id}
        return self._object("POST", "/api/v1/plants/sync", json=body)

    def move_plant(self, plant_id: int, target_cluster_id: int) -> JSONObject:
        """Move a plant to another cluster (``POST /plants/{plant_id}/move``)."""
        body = {"target_cluster_id": target_cluster_id}
        return self._object("POST", f"/api/v1/plants/{plant_id}/move", json=body)

    # ── Irrigators ──

    def list_irrigators(self) -> list[JSONObject]:
        """List every irrigator (``GET /irrigators``)."""
        return self._array("GET", "/api/v1/irrigators")

    def add_irrigator(self, cluster_id: int, **kwargs: Any) -> JSONObject:
        """Attach an irrigator to a cluster; ``None`` fields are omitted (``POST /clusters/{id}/irrigator``)."""
        body = _drop_none(kwargs)
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/irrigator", json=body)

    def get_irrigator(self, cluster_id: int) -> JSONObject:
        """Fetch a cluster's irrigator (``GET /clusters/{id}/irrigator``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/irrigator")

    def update_irrigator(self, cluster_id: int, **kwargs: Any) -> JSONObject:
        """Patch a cluster's irrigator; ``None`` fields are left unchanged (``PUT /clusters/{id}/irrigator``)."""
        body = _drop_none(kwargs)
        return self._object("PUT", f"/api/v1/clusters/{cluster_id}/irrigator", json=body)

    def delete_irrigator(self, cluster_id: int) -> JSONObject:
        """Detach a cluster's irrigator (``DELETE /clusters/{id}/irrigator``)."""
        return self._object("DELETE", f"/api/v1/clusters/{cluster_id}/irrigator")

    def start_irrigator(self, irrigator_id: int, minutes: int | None = None) -> JSONObject:
        """Start watering now; ``minutes=None`` uses the device default (``POST /irrigators/{id}/start``)."""
        return self._object("POST", f"/api/v1/irrigators/{irrigator_id}/start", json={"minutes": minutes})

    def stop_irrigator(self, irrigator_id: int) -> JSONObject:
        """Stop an irrigator now (``POST /irrigators/{id}/stop``)."""
        return self._object("POST", f"/api/v1/irrigators/{irrigator_id}/stop")

    def log_manual(self, irrigator_id: int, minutes: int, notes: str | None = None) -> JSONObject:
        """Record a hand watering without actuating anything (``POST /irrigators/{id}/log-manual``)."""
        return self._object(
            "POST", f"/api/v1/irrigators/{irrigator_id}/log-manual", json={"minutes": minutes, "notes": notes}
        )

    # ── Sensors ──

    def add_sensor(self, cluster_id: int, **kwargs: Any) -> JSONObject:
        """Add a sensor to a cluster; fields are sent as given (``POST /clusters/{id}/sensors``)."""
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/sensors", json=kwargs)

    def list_sensors(self, cluster_id: int) -> list[JSONObject]:
        """List a cluster's sensors (``GET /clusters/{id}/sensors``)."""
        return self._array("GET", f"/api/v1/clusters/{cluster_id}/sensors")

    def update_sensor(self, cluster_id: int, sensor_id: int, **kwargs: Any) -> JSONObject:
        """Patch a sensor; ``None`` fields are left unchanged (``PUT /clusters/{id}/sensors/{sensor_id}``)."""
        body = _drop_none(kwargs)
        return self._object("PUT", f"/api/v1/clusters/{cluster_id}/sensors/{sensor_id}", json=body)

    def delete_sensor(self, cluster_id: int, sensor_id: int) -> JSONObject:
        """Remove a sensor; its readings are kept (``DELETE /clusters/{id}/sensors/{sensor_id}``)."""
        return self._object("DELETE", f"/api/v1/clusters/{cluster_id}/sensors/{sensor_id}")

    # ── Config ──

    def set_config(self, cluster_id: int, **kwargs: Any) -> JSONObject:
        """Patch a cluster's irrigation config; ``None`` fields are left unchanged (``PUT /clusters/{id}/config``)."""
        body = _drop_none(kwargs)
        return self._object("PUT", f"/api/v1/clusters/{cluster_id}/config", json=body)

    def get_config(self, cluster_id: int) -> JSONObject:
        """Fetch a cluster's own irrigation config (``GET /clusters/{id}/config``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/config")

    def get_effective_config(self, cluster_id: int) -> JSONObject:
        """Fetch the merged cluster/global config with each value's source (``GET .../config/effective``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/config/effective")

    def get_global_config(self) -> JSONObject:
        """Fetch the global irrigation defaults (``GET /config/global``)."""
        return self._object("GET", "/api/v1/config/global")

    def update_global_config(self, **kwargs: Any) -> JSONObject:
        """Patch the global defaults; ``None`` fields are left unchanged (``PUT /config/global``)."""
        body = _drop_none(kwargs)
        return self._object("PUT", "/api/v1/config/global", json=body)

    # ── Operations ──

    def status(self, cluster_id: int) -> JSONObject:
        """Fetch a cluster's live status: readings, plants, irrigator, decision (``GET /clusters/{id}/status``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/status")

    def irrigate(
        self,
        cluster_id: int,
        temp_override: float | None = None,
        dry_run: bool = False,
        no_sync: bool = False,
        force: bool = False,
    ) -> JSONObject:
        """Run the irrigation pipeline for a cluster, optionally as a dry run (``POST /clusters/{id}/irrigate``)."""
        body = {"temp_override": temp_override, "dry_run": dry_run, "no_sync": no_sync, "force": force}
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/irrigate", json=body)

    def monitor(self, cluster_id: int) -> JSONObject:
        """Fetch per-sensor moisture status and the plants that need water (``GET /clusters/{id}/monitor``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/monitor")

    def check(self, cluster_id: int | None = None) -> JSONObject:
        """Run the scheduled check for one cluster, or for all when ``cluster_id`` is ``None``."""
        if cluster_id is None:
            return self._object("POST", "/api/v1/check")
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/check")

    def sync(self, hours: int = 24) -> JSONObject:
        """Backfill sensor readings from the cloud for the last ``hours`` (``POST /sync``)."""
        return self._object("POST", "/api/v1/sync", json={"hours": hours})

    def bulk_stop_all(self) -> JSONObject:
        """Stop every irrigator at once (``POST /bulk/stop-all``)."""
        return self._object("POST", "/api/v1/bulk/stop-all")

    def learn(self, cluster_id: int) -> JSONObject:
        """Fetch a cluster's learning report (``GET /clusters/{id}/learn``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/learn")

    def history(self, cluster_id: int, hours: int = 24, limit: int = 50) -> JSONObject:
        """Fetch a cluster's irrigation events per irrigator (``GET /clusters/{id}/history``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/history", params={"hours": hours, "limit": limit})

    def stats(self, cluster_id: int, days: int = 7) -> JSONObject:
        """Fetch irrigation statistics for the last ``days`` (``GET /clusters/{id}/stats``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/stats", params={"days": days})

    def stats_export(self, cluster_id: int, days: int = 7) -> str:
        """Download the statistics CSV as text (``GET /clusters/{id}/stats/export``)."""
        result = self._object("GET", f"/api/v1/clusters/{cluster_id}/stats/export", params={"days": days})
        csv: str = result.get("csv", "")
        return csv

    def health(self) -> JSONObject:
        """Fetch the server health summary (``GET /health``)."""
        return self._object("GET", "/api/v1/health")

    def scheduler_jobs(self) -> list[JSONObject]:
        """List the scheduler's jobs (``GET /scheduler/jobs``)."""
        return self._array("GET", "/api/v1/scheduler/jobs")

    def delete_scheduler_job(self, job_id: str) -> JSONObject:
        """Remove a scheduler job (``DELETE /scheduler/jobs/{job_id}``)."""
        return self._object("DELETE", f"/api/v1/scheduler/jobs/{job_id}")

    def scheduler_pause(self) -> JSONObject:
        """Pause automatic irrigation (``POST /scheduler/pause``)."""
        return self._object("POST", "/api/v1/scheduler/pause")

    def scheduler_resume(self) -> JSONObject:
        """Resume automatic irrigation (``POST /scheduler/resume``)."""
        return self._object("POST", "/api/v1/scheduler/resume")

    def system_health(self) -> JSONObject:
        """Fetch system health incl. per-device freshness (``GET /health/system``)."""
        return self._object("GET", "/api/v1/health/system")

    def forecast(self, cluster_id: int) -> JSONObject:
        """Fetch a cluster's next-watering forecast (``GET /clusters/{id}/forecast``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/forecast")

    def list_activity(self, limit: int = 100, before: int | None = None, **filters: Any) -> JSONObject:
        """Page through the activity log; ``None`` filters are omitted (``GET /activity``)."""
        params: dict[str, int | str] = {"limit": limit}
        if before is not None:
            params["before"] = before
        params.update(_drop_none(filters))
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

    # ── Alerts ──

    def list_alerts(
        self,
        status: str | None = None,
        cluster_id: int | None = None,
        plant_id: int | None = None,
        limit: int = 100,
    ) -> JSONObject:
        """List alerts; ``None`` filters are omitted (``GET /alerts``)."""
        params: dict[str, int | str] = {"limit": limit}
        params.update(_drop_none({"status": status, "cluster_id": cluster_id, "plant_id": plant_id}))
        return self._object("GET", "/api/v1/alerts", params=params)

    def get_alert(self, alert_id: int) -> JSONObject:
        """Fetch one alert (``GET /alerts/{alert_id}``)."""
        return self._object("GET", f"/api/v1/alerts/{alert_id}")

    def acknowledge_alert(self, alert_id: int) -> JSONObject:
        """Acknowledge an alert (``POST /alerts/{alert_id}/acknowledge``)."""
        return self._object("POST", f"/api/v1/alerts/{alert_id}/acknowledge")

    def resolve_alert(self, alert_id: int) -> JSONObject:
        """Resolve an alert (``POST /alerts/{alert_id}/resolve``)."""
        return self._object("POST", f"/api/v1/alerts/{alert_id}/resolve")

    def sync_alerts(self, cluster_id: int | None = None) -> JSONObject:
        """Re-derive alerts for one cluster, or for all when ``cluster_id`` is ``None``."""
        if cluster_id is None:
            return self._object("POST", "/api/v1/alerts/sync")
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/alerts/sync")

    # ── Decisions ──

    def list_decisions(self, cluster_id: int, limit: int = 50) -> JSONObject:
        """List a cluster's logged engine decisions (``GET /clusters/{id}/decisions``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/decisions", params={"limit": limit})

    # ── Preferences ──

    def get_preferences(self) -> JSONObject:
        """Fetch the user preferences (``GET /preferences``)."""
        return self._object("GET", "/api/v1/preferences")

    def update_preferences(self, **kwargs: Any) -> JSONObject:
        """Patch the preferences; ``None`` fields are left unchanged (``PUT /preferences``)."""
        body = _drop_none(kwargs)
        return self._object("PUT", "/api/v1/preferences", json=body)

    # ── Vacation ──

    def list_vacation(self) -> JSONObject:
        """List vacation windows and the active one (``GET /vacation``)."""
        return self._object("GET", "/api/v1/vacation")

    def add_vacation(
        self,
        starts_at: int,
        ends_at: int,
        contact_email: str | None = None,
        notes: str | None = None,
    ) -> JSONObject:
        """Create a vacation window (``POST /vacation``)."""
        body = {
            "starts_at": starts_at,
            "ends_at": ends_at,
            "contact_email": contact_email,
            "notes": notes,
        }
        return self._object("POST", "/api/v1/vacation", json=body)

    def update_vacation(
        self,
        window_id: int,
        starts_at: int | None = None,
        ends_at: int | None = None,
        contact_email: str | None = None,
        notes: str | None = None,
    ) -> JSONObject:
        """Patch a vacation window; ``None`` fields are left unchanged (``PUT /vacation/{window_id}``)."""
        body = _drop_none({"starts_at": starts_at, "ends_at": ends_at, "contact_email": contact_email, "notes": notes})
        return self._object("PUT", f"/api/v1/vacation/{window_id}", json=body)

    def delete_vacation(self, window_id: int) -> JSONObject:
        """Delete a vacation window (``DELETE /vacation/{window_id}``)."""
        return self._object("DELETE", f"/api/v1/vacation/{window_id}")

    # ── Irrigation windows ──

    def list_windows(self, cluster_id: int) -> JSONObject:
        """List a cluster's irrigation windows (``GET /clusters/{id}/windows``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/windows")

    def add_window(
        self,
        cluster_id: int,
        start_hour: int,
        end_hour: int,
        weekday_mask: int = ALL_WEEKDAYS,
        label: str | None = None,
    ) -> JSONObject:
        """Add an irrigation window; ``weekday_mask`` 127 = every day (``POST /clusters/{id}/windows``)."""
        body = {
            "start_hour": start_hour,
            "end_hour": end_hour,
            "weekday_mask": weekday_mask,
            "label": label,
        }
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/windows", json=body)

    def update_window(
        self,
        cluster_id: int,
        window_id: int,
        start_hour: int | None = None,
        end_hour: int | None = None,
        weekday_mask: int | None = None,
        label: str | None = None,
    ) -> JSONObject:
        """Patch an irrigation window; ``None`` fields are left unchanged (``PUT .../windows/{window_id}``)."""
        body = _drop_none(
            {"start_hour": start_hour, "end_hour": end_hour, "weekday_mask": weekday_mask, "label": label}
        )
        return self._object("PUT", f"/api/v1/clusters/{cluster_id}/windows/{window_id}", json=body)

    def delete_window(self, cluster_id: int, window_id: int) -> JSONObject:
        """Delete an irrigation window (``DELETE /clusters/{id}/windows/{window_id}``)."""
        return self._object("DELETE", f"/api/v1/clusters/{cluster_id}/windows/{window_id}")

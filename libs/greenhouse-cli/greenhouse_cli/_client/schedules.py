"""Alert, decision, preference, vacation and irrigation-window endpoints."""

from typing import TypedDict, Unpack

from greenhouse_cli._client.transport import JSONObject, Transport, drop_none
from greenhouse_cli.constants import ALL_WEEKDAYS


class WindowPatch(TypedDict, total=False):
    """Fields ``update_window`` may change (``None`` or absent = keep the current value)."""

    start_hour: int | None
    end_hour: int | None
    weekday_mask: int | None
    label: str | None


class ScheduleEndpoints(Transport):
    """Alert inbox, decision log, preferences, vacation and window calls."""

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
        params.update(drop_none({"status": status, "cluster_id": cluster_id, "plant_id": plant_id}))
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

    def update_preferences(self, **kwargs: object) -> JSONObject:
        """Patch the preferences; ``None`` fields are left unchanged (``PUT /preferences``)."""
        body = drop_none(kwargs)
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
        body = drop_none({"starts_at": starts_at, "ends_at": ends_at, "contact_email": contact_email, "notes": notes})
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
        **patch: Unpack[WindowPatch],
    ) -> JSONObject:
        """Patch an irrigation window; ``None`` fields are left unchanged (``PUT .../windows/{window_id}``)."""
        body = drop_none({key: patch.get(key) for key in ("start_hour", "end_hour", "weekday_mask", "label")})
        return self._object("PUT", f"/api/v1/clusters/{cluster_id}/windows/{window_id}", json=body)

    def delete_window(self, cluster_id: int, window_id: int) -> JSONObject:
        """Delete an irrigation window (``DELETE /clusters/{id}/windows/{window_id}``)."""
        return self._object("DELETE", f"/api/v1/clusters/{cluster_id}/windows/{window_id}")

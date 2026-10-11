"""Operation endpoints: status, irrigate, check, sync, history, stats, health and the scheduler."""

from urllib.parse import quote

from greenhouse_cli._client.transport import JSONObject, ServerError, Transport, is_csv


class OperationEndpoints(Transport):
    """Pipeline, reporting and scheduler calls."""

    # ── Operations ──

    def status(self, cluster_id: int) -> JSONObject:
        """Fetch a cluster's live status: readings, plants, irrigator, decision (``GET /clusters/{id}/status``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/status")

    def irrigate(
        self,
        cluster_id: int,
        *,
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
        """Download the statistics CSV as text (``GET /clusters/{id}/stats/export``).

        A response that is not ``text/csv`` raises :class:`ServerError` instead of
        yielding ``""`` — otherwise ``stats --export`` wrote an empty file and
        reported success.
        """
        resp = self._send("GET", f"/api/v1/clusters/{cluster_id}/stats/export", params={"days": days})
        if not is_csv(resp):
            content_type = resp.headers.get("content-type") or "no content type"
            raise ServerError(resp.status_code, f"Expected a CSV export from the server, got {content_type}")
        return resp.text

    def health(self) -> JSONObject:
        """Fetch the server health summary (``GET /health``)."""
        return self._object("GET", "/api/v1/health")

    def scheduler_jobs(self) -> list[JSONObject]:
        """List the scheduler's jobs (``GET /scheduler/jobs``)."""
        return self._array("GET", "/api/v1/scheduler/jobs")

    def delete_scheduler_job(self, job_id: str) -> JSONObject:
        """Remove a scheduler job (``DELETE /scheduler/jobs/{job_id}``).

        Job ids are free text, so the id is percent-encoded as one path segment —
        a ``/``, ``?`` or space must not change which URL is called.
        """
        return self._object("DELETE", f"/api/v1/scheduler/jobs/{quote(job_id, safe='')}")

    def scheduler_pause(self) -> JSONObject:
        """Pause automatic irrigation (``POST /scheduler/pause``)."""
        return self._object("POST", "/api/v1/scheduler/pause")

    def scheduler_resume(self) -> JSONObject:
        """Resume automatic irrigation (``POST /scheduler/resume``)."""
        return self._object("POST", "/api/v1/scheduler/resume")

    def system_health(self) -> JSONObject:
        """Fetch system health incl. per-device freshness (``GET /health/system``)."""
        return self._object("GET", "/api/v1/health/system")

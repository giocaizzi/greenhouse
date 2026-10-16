"""Irrigator, sensor and irrigation-config endpoints."""

from greenhouse_cli._client.transport import JSONObject, Transport, drop_none


class DeviceEndpoints(Transport):
    """Irrigator, sensor and config calls."""

    # ── Irrigators ──

    def list_irrigators(self) -> list[JSONObject]:
        """List every irrigator (``GET /irrigators``)."""
        return self._array("GET", "/api/v1/irrigators")

    def add_irrigator(self, cluster_id: int, **kwargs: object) -> JSONObject:
        """Attach an irrigator to a cluster; ``None`` fields are omitted (``POST /clusters/{id}/irrigator``)."""
        body = drop_none(kwargs)
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/irrigator", json=body)

    def get_irrigator(self, cluster_id: int) -> JSONObject:
        """Fetch a cluster's irrigator (``GET /clusters/{id}/irrigator``)."""
        return self._object("GET", f"/api/v1/clusters/{cluster_id}/irrigator")

    def update_irrigator(self, cluster_id: int, **kwargs: object) -> JSONObject:
        """Patch a cluster's irrigator; ``None`` fields are left unchanged (``PUT /clusters/{id}/irrigator``)."""
        body = drop_none(kwargs)
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

    def add_sensor(self, cluster_id: int, **kwargs: object) -> JSONObject:
        """Add a sensor to a cluster; fields are sent as given (``POST /clusters/{id}/sensors``)."""
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/sensors", json=kwargs)

    def list_sensors(self, cluster_id: int, *, include_archived: bool = False) -> list[JSONObject]:
        """List a cluster's sensors, archived ones too on request (``GET /clusters/{id}/sensors``)."""
        params = {"include_archived": "true"} if include_archived else {}
        return self._array("GET", f"/api/v1/clusters/{cluster_id}/sensors", params=params)

    def update_sensor(self, cluster_id: int, sensor_id: int, **kwargs: object) -> JSONObject:
        """Patch a sensor; ``None`` fields are left unchanged (``PUT /clusters/{id}/sensors/{sensor_id}``)."""
        body = drop_none(kwargs)
        return self._object("PUT", f"/api/v1/clusters/{cluster_id}/sensors/{sensor_id}", json=body)

    def delete_sensor(self, cluster_id: int, sensor_id: int, *, purge: bool = False) -> JSONObject:
        """Archive a sensor, or purge an archived one (``DELETE /clusters/{id}/sensors/{sensor_id}``)."""
        params = {"purge": "true"} if purge else {}
        return self._object("DELETE", f"/api/v1/clusters/{cluster_id}/sensors/{sensor_id}", params=params)

    def restore_sensor(self, cluster_id: int, sensor_id: int) -> JSONObject:
        """Bring an archived sensor back (``POST /clusters/{id}/sensors/{sensor_id}/restore``)."""
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/sensors/{sensor_id}/restore")

    def move_sensor(
        self, cluster_id: int, sensor_id: int, plant_id: int | None, effective_at: int | None = None
    ) -> JSONObject:
        """Move a sensor to a plant (``None`` detaches), dated ``effective_at`` (``POST .../sensors/{id}/move``)."""
        body: dict[str, object] = {"plant_id": plant_id}
        if effective_at is not None:
            body["effective_at"] = effective_at
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/sensors/{sensor_id}/move", json=body)

    # ── Config ──

    def set_config(self, cluster_id: int, **kwargs: object) -> JSONObject:
        """Patch a cluster's irrigation config; ``None`` fields are left unchanged (``PUT /clusters/{id}/config``)."""
        body = drop_none(kwargs)
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

    def update_global_config(self, **kwargs: object) -> JSONObject:
        """Patch the global defaults; ``None`` fields are left unchanged (``PUT /config/global``)."""
        body = drop_none(kwargs)
        return self._object("PUT", "/api/v1/config/global", json=body)

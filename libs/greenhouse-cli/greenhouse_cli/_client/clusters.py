"""Auth, cluster and plant endpoints."""

from greenhouse_cli._client.transport import JSONObject, Transport, drop_none


class ClusterEndpoints(Transport):
    """``/auth``, ``/clusters`` and ``/plants`` calls."""

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

    def update_cluster(self, cluster_id: int, **kwargs: object) -> JSONObject:
        """Patch a cluster; ``None`` fields are left unchanged (``PUT /clusters/{id}``)."""
        body = drop_none(kwargs)
        return self._object("PUT", f"/api/v1/clusters/{cluster_id}", json=body)

    def delete_cluster(self, cluster_id: int) -> JSONObject:
        """Delete a cluster and everything attached to it (``DELETE /clusters/{id}``)."""
        return self._object("DELETE", f"/api/v1/clusters/{cluster_id}")

    # ── Plants ──

    def add_plant(self, cluster_id: int, **kwargs: object) -> JSONObject:
        """Add a plant to a cluster; fields are sent as given (``POST /clusters/{id}/plants``)."""
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/plants", json=kwargs)

    def list_plants(self, cluster_id: int, *, include_archived: bool = False) -> list[JSONObject]:
        """List a cluster's plants, archived ones too on request (``GET /clusters/{id}/plants``)."""
        params = {"include_archived": "true"} if include_archived else {}
        return self._array("GET", f"/api/v1/clusters/{cluster_id}/plants", params=params)

    def update_plant(self, cluster_id: int, plant_id: int, **kwargs: object) -> JSONObject:
        """Patch a plant; ``None`` fields are left unchanged (``PUT /clusters/{id}/plants/{plant_id}``)."""
        body = drop_none(kwargs)
        return self._object("PUT", f"/api/v1/clusters/{cluster_id}/plants/{plant_id}", json=body)

    def delete_plant(self, cluster_id: int, plant_id: int, *, purge: bool = False) -> JSONObject:
        """Archive a plant, or purge an archived one (``DELETE /clusters/{id}/plants/{plant_id}``)."""
        params = {"purge": "true"} if purge else {}
        return self._object("DELETE", f"/api/v1/clusters/{cluster_id}/plants/{plant_id}", params=params)

    def restore_plant(self, cluster_id: int, plant_id: int) -> JSONObject:
        """Bring an archived plant back (``POST /clusters/{id}/plants/{plant_id}/restore``)."""
        return self._object("POST", f"/api/v1/clusters/{cluster_id}/plants/{plant_id}/restore")

    def sync_plants(self, plant_id: int | None = None, cluster_id: int | None = None) -> JSONObject:
        """Refresh plant care data from the plant DB, optionally for one plant or cluster (``POST /plants/sync``)."""
        body = {"plant_id": plant_id, "cluster_id": cluster_id}
        return self._object("POST", "/api/v1/plants/sync", json=body)

    def move_plant(self, plant_id: int, target_cluster_id: int) -> JSONObject:
        """Move a plant to another cluster (``POST /plants/{plant_id}/move``)."""
        body = {"target_cluster_id": target_cluster_id}
        return self._object("POST", f"/api/v1/plants/{plant_id}/move", json=body)

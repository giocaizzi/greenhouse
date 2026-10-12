"""Device registration rules shared by the JSON API and the web UI.

Both surfaces register irrigators (and sensors) through these functions so the
validation, the duplicate-device handling and the capacity follow-up are one
implementation. Each caller keeps its own I/O mapping (status code, detail or
template) and commits on success; on a refusal the session is already rolled back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from greenhouse_core.devices import IK10PW_PROFILE, TR301Z_PROFILE
from greenhouse_core.models import parse_device_config
from greenhouse_core.repository import DeviceIdExistsError, IrrigationRepository, IrrigatorExistsError
from greenhouse_core.schemas import LOCAL_KEY_MASK

if TYPE_CHECKING:
    from greenhouse_core.devices import DeviceRegistry

DeviceKind = Literal["irrigator", "sensor"]

# The model keys ``build_default_registry`` wires, for when the app has no live
# registry (no Tuya credentials): writes are still checked against the real set.
_CANONICAL_TYPES: dict[DeviceKind, tuple[str, ...]] = {
    "irrigator": (IK10PW_PROFILE.model_key,),
    "sensor": (TR301Z_PROFILE.model_key,),
}

__all__ = [
    "DeviceIdExistsError",
    "DeviceSpec",
    "IrrigatorCapacity",
    "IrrigatorExistsError",
    "PlantNotInClusterError",
    "UnknownDeviceTypeError",
    "create_irrigator",
    "create_sensor",
    "ensure_plant_in_cluster",
    "keep_masked_local_key",
    "known_device_types",
    "require_known_device_type",
]


class PlantNotInClusterError(LookupError):
    """A sensor was linked to a plant that does not belong to the sensor's cluster."""

    def __init__(self, plant_id: int) -> None:
        """Keep the offending ``plant_id`` so each surface words its own error."""
        super().__init__(plant_id)
        self.plant_id = plant_id


class UnknownDeviceTypeError(ValueError):
    """A device ``type`` that no registered adapter handles; the message lists the known keys."""

    def __init__(self, kind: DeviceKind, value: str, known: tuple[str, ...]) -> None:
        """Build the shared message (API wording) and keep its parts for callers that render their own."""
        super().__init__(f"Unknown {kind} type '{value}' (known: {', '.join(known) or 'none'})")
        self.kind, self.value, self.known = kind, value, known


def known_device_types(registry: DeviceRegistry | None, kind: DeviceKind) -> tuple[str, ...]:
    """The model keys a ``kind`` device may be registered with: the live registry's, else the canonical set."""
    if registry is None:
        return _CANONICAL_TYPES[kind]
    return registry.registered_irrigator_keys() if kind == "irrigator" else registry.registered_sensor_keys()


def require_known_device_type(
    registry: DeviceRegistry | None, kind: DeviceKind, value: str, *, stored: str | None = None
) -> None:
    """Refuse a device ``type`` no adapter handles, so a typo never becomes a device that cannot be driven.

    ``stored`` is the row's current type on an update: keeping it unchanged is always
    allowed, so a legacy row's other fields stay editable; only a new or changed type
    must be a registered model key.

    Raises:
        UnknownDeviceTypeError: ``value`` is new / changed and not one of :func:`known_device_types`.
    """
    if stored is not None and value == stored:
        return
    known = known_device_types(registry, kind)
    if value not in known:
        raise UnknownDeviceTypeError(kind, value, known)


def keep_masked_local_key(config: dict[str, Any], stored_config: object) -> dict[str, Any]:
    """Resolve a ``local_key`` that arrives as the response mask back to the stored key.

    Responses show the key as ``LOCAL_KEY_MASK``, so a client that edits the
    config it read (TUI JSON form, MCP agent, script) sends the mask back.
    That means "unchanged": the stored key is kept (dropped when none is
    stored). Any other value — a new key, or ``""`` — is taken as given.
    """
    if config.get("local_key") != LOCAL_KEY_MASK:
        return config
    resolved = dict(config)
    stored_key = parse_device_config(stored_config).get("local_key") if stored_config else None
    if stored_key:
        resolved["local_key"] = stored_key
    else:
        resolved.pop("local_key")
    return resolved


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    """A device to register: what the API body and the web form both carry for an irrigator or sensor."""

    tuya_device_id: str
    name: str
    model: str  # the registry model key (the API's ``type``)
    config: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IrrigatorCapacity:
    """An irrigator's optional capacity columns (``None``: not set)."""

    reservoir_l: float | None = None
    flow_rate_l_per_min: float | None = None


def create_irrigator(
    repo: IrrigationRepository,
    cluster_id: int,
    device: DeviceSpec,
    *,
    registry: DeviceRegistry | None,
    capacity: IrrigatorCapacity | None = None,
) -> int:
    """Add the cluster's irrigator, then persist any capacity values; return its id.

    ``add_irrigator`` does not take the capacity columns, so they are written in a
    follow-up update when either is supplied. The caller commits.

    Raises:
        UnknownDeviceTypeError: ``device.model`` is not a registered model key (nothing written).
        IrrigatorExistsError: the cluster already has an irrigator (session rolled back).
        DeviceIdExistsError: the Tuya device id is already registered (session rolled back).
    """
    require_known_device_type(registry, "irrigator", device.model)
    try:
        with repo.refusing_duplicate_device_id(device.tuya_device_id):
            irrigator_id = repo.add_irrigator(
                cluster_id=cluster_id,
                tuya_device_id=device.tuya_device_id,
                name=device.name,
                irrigator_type=device.model,
                config=keep_masked_local_key(device.config, None),
            )
    except IrrigatorExistsError:
        repo.rollback()
        raise
    capacity = capacity or IrrigatorCapacity()
    if capacity.reservoir_l is not None or capacity.flow_rate_l_per_min is not None:
        repo.update_irrigator(
            irrigator_id, reservoir_l=capacity.reservoir_l, flow_rate_l_per_min=capacity.flow_rate_l_per_min
        )
    return irrigator_id


def ensure_plant_in_cluster(repo: IrrigationRepository, cluster_id: int, plant_id: int | None) -> None:
    """Refuse a sensor's plant link unless the plant is one of the cluster's plants.

    A falsy ``plant_id`` (``None`` / ``0``) means "no plant" and always passes.

    Raises:
        PlantNotInClusterError: the plant is unknown or lives in another cluster.
    """
    if plant_id and not any(p.id == plant_id for p in repo.get_plants_in_cluster(cluster_id)):
        raise PlantNotInClusterError(plant_id)


def create_sensor(
    repo: IrrigationRepository,
    cluster_id: int,
    device: DeviceSpec,
    *,
    registry: DeviceRegistry | None,
    plant_id: int | None = None,
) -> int:
    """Register a sensor under a cluster, optionally linked to one of its plants; return its id.

    The caller commits.

    Raises:
        UnknownDeviceTypeError: ``device.model`` is not a registered model key (nothing written).
        PlantNotInClusterError: ``plant_id`` is not one of the cluster's plants (nothing written).
        DeviceIdExistsError: the Tuya device id is already registered (session rolled back).
    """
    require_known_device_type(registry, "sensor", device.model)
    ensure_plant_in_cluster(repo, cluster_id, plant_id)
    with repo.refusing_duplicate_device_id(device.tuya_device_id):
        return repo.add_sensor(
            cluster_id=cluster_id,
            tuya_device_id=device.tuya_device_id,
            name=device.name,
            sensor_type=device.model,
            config=keep_masked_local_key(device.config, None),
            plant_id=plant_id,
        )

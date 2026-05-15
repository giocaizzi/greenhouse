"""Declarative device profiles.

A ``DeviceProfile`` captures the *data* shape of a device model: DP map,
protocol version, capability flags, parser table. The *behaviour* (how to
start an irrigation, how to parse an alarm bitmap) lives in adapter classes
under :mod:`greenhouse_core.devices.irrigators` and
:mod:`greenhouse_core.devices.sensors`. The split lets the common case stay
declarative while leaving room for per-model overrides where firmware quirks
demand imperative code.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Transport = Literal["tuya_cloud", "tuya_local", "tuya_hybrid"]
DurationUnit = Literal["seconds", "minutes"]

# A parser converts a raw DP value into a (canonical_key, value) tuple.
SensorParser = Callable[[Any], tuple[str, Any]]


_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"


@dataclass(frozen=True, slots=True)
class IrrigatorProfile:
    """Declarative description of an irrigator model.

    Attributes:
        model_key: ``vendor.model`` identifier (``rainpoint.ik10pw``).
        vendor: Vendor brand name.
        transport: Which Tuya transport(s) the adapter uses.
        protocol_version: tinytuya local protocol version (3.5 for IK10PW).
            ``None`` for cloud-only devices.
        dp_map: Logical name → DP id mapping (``{"switch": 1, "duration": 102}``).
        capabilities: Free-form flags consulted by the engine / pump watcher
            (``"has_local_duration_dp"``, ``"has_water_shortage_dp"``,
            ``"needs_keepalive_fallback"``).
        duration_unit: Unit for the Duration DP if any.
        alarm_bitmask: Bit to AND against the alarm DP value when checking
            for a no-water condition (``0x01`` on IK10PW). ``None`` when the
            model has no alarm DP.
    """

    model_key: str
    vendor: str
    transport: Transport
    protocol_version: float | None
    dp_map: dict[str, int] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    duration_unit: DurationUnit | None = None
    alarm_bitmask: int | None = None

    def has_capability(self, name: str) -> bool:
        """True when ``name`` is in this profile's capability set."""
        return name in self.capabilities

    def dp(self, name: str) -> int:
        """Resolve a logical DP name to its numeric id. Raises ``KeyError``
        when the DP is not declared for this model — callers must guard with
        :meth:`has_capability` first.
        """
        return self.dp_map[name]


@dataclass(frozen=True, slots=True)
class SensorProfile:
    """Declarative description of a sensor model.

    Attributes:
        model_key: ``vendor.model`` identifier (``tuya.tr301z``).
        vendor: Vendor brand name.
        transport: Tuya transport in use (typically ``tuya_cloud``).
        capabilities: Free-form flags such as ``"reports_soil_moisture"``,
            ``"reports_temperature"``, ``"reports_lux"``, ``"reports_battery"``.
        dp_parsers: Tuya DP code (e.g. ``"humidity"``) → parser callable.
            The parser returns a ``(canonical_key, value)`` tuple consumed by
            ``TuyaSensorAdapter.read_live``.
    """

    model_key: str
    vendor: str
    transport: Transport
    capabilities: frozenset[str] = field(default_factory=frozenset)
    dp_parsers: dict[str, SensorParser] = field(default_factory=dict)

    def has_capability(self, name: str) -> bool:
        """True when ``name`` is in this profile's capability set."""
        return name in self.capabilities


def load_profile_json(filename: str) -> dict:
    """Load the raw JSON body of a profile sidecar bundled with the package.

    Adapter modules are responsible for mapping the dict into a typed
    profile (irrigator vs sensor have different shapes and parsers can't be
    expressed declaratively).
    """
    path = _PROFILES_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))

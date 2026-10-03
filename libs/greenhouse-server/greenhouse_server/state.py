"""Typed reads of what ``create_app`` stores on ``app.state``.

Starlette's ``app.state`` is an untyped attribute bag, so every value read straight off it is
``Any``. Reading through these accessors gives each value its type and spells the two access
rules in one place:

* always set by ``create_app`` (session factory, settings, plant DB, weather client) — read
  directly, so a missing one still raises ``AttributeError``;
* set only when configured (device gateway / registry, health monitor, ntfy client) — read
  with a ``None`` default, the "feature unavailable" value every caller already handles.

Sits below the scheduler and above the services in the server layers, so the scheduler, the
DI providers and the web layer share it; services receive these values as arguments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.orm import Session, sessionmaker

    from greenhouse_core.devices import DeviceGateway, DeviceRegistry
    from greenhouse_core.plant_db import PlantDatabase
    from greenhouse_server.config import Settings
    from greenhouse_server.services.health_monitor import DeviceHealthMonitor
    from greenhouse_server.services.notify import NtfyClient
    from greenhouse_server.services.weather import WeatherClient


def session_factory(app: FastAPI) -> sessionmaker[Session]:
    """The app's SQLAlchemy session factory."""
    factory: sessionmaker[Session] = app.state.session_factory
    return factory


def settings(app: FastAPI) -> Settings:
    """The live server settings."""
    value: Settings = app.state.settings
    return value


def plant_db(app: FastAPI) -> PlantDatabase:
    """The app-scoped plant care database."""
    value: PlantDatabase = app.state.plant_db
    return value


def weather_client(app: FastAPI) -> WeatherClient:
    """The app-scoped weather client (rebuilt when the timezone preference changes)."""
    value: WeatherClient = app.state.weather_client
    return value


def device_gateway(app: FastAPI) -> DeviceGateway | None:
    """The one shared Tuya gateway, or ``None`` in degraded mode (no credentials)."""
    value: DeviceGateway | None = getattr(app.state, "device_gateway", None)
    return value


def device_registry(app: FastAPI) -> DeviceRegistry | None:
    """The device registry, or ``None`` in degraded mode (no credentials)."""
    value: DeviceRegistry | None = getattr(app.state, "device_registry", None)
    return value


def health_monitor(app: FastAPI) -> DeviceHealthMonitor | None:
    """The device-health monitor singleton, or ``None`` when no registry was wired at startup."""
    value: DeviceHealthMonitor | None = getattr(app.state, "health_monitor", None)
    return value


def ntfy_notifier(app: FastAPI) -> NtfyClient | None:
    """The ntfy client, or ``None`` when notifications are unconfigured."""
    value: NtfyClient | None = getattr(app.state, "ntfy_notifier", None)
    return value

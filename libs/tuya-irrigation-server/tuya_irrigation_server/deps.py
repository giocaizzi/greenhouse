"""FastAPI dependency injection."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from tuya_irrigation_core.devices import TuyaDeviceManager
from tuya_irrigation_core.repository import IrrigationRepository

_session_factory = None
_device_manager: TuyaDeviceManager | None = None
_device_manager_initialized = False


def set_session_factory(factory) -> None:
    global _session_factory
    _session_factory = factory


def set_device_manager(dm: TuyaDeviceManager | None) -> None:
    global _device_manager, _device_manager_initialized
    _device_manager = dm
    _device_manager_initialized = True


def get_session() -> Generator[Session, None, None]:
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def get_repository(session: Annotated[Session, Depends(get_session)]) -> IrrigationRepository:
    return IrrigationRepository(session)


def get_device_manager() -> TuyaDeviceManager | None:
    global _device_manager, _device_manager_initialized
    if not _device_manager_initialized:
        try:
            _device_manager = TuyaDeviceManager()
        except (ValueError, Exception):
            _device_manager = None
        _device_manager_initialized = True
    return _device_manager


RepoDep = Annotated[IrrigationRepository, Depends(get_repository)]
SessionDep = Annotated[Session, Depends(get_session)]
DeviceManagerDep = Annotated[TuyaDeviceManager | None, Depends(get_device_manager)]

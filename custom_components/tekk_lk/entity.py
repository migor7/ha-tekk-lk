"""Общая база сущностей ТЕКК."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import billing
from .const import BASE_URL, DOMAIN
from .coordinator import TekkCoordinator


class TekkEntity(CoordinatorEntity[TekkCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: TekkCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=entry.title,
            manufacturer="ТЕКК",
            model="Личный кабинет lk.tekk.pro",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"{BASE_URL}/user/index.xhtml",
        )

    @property
    def data(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    @property
    def index(self) -> dict[str, Any]:
        return self.data.get("index") or {}

    def schedule(self) -> dict[str, Any]:
        return billing.schedule(self.data, dt_util.now().date())

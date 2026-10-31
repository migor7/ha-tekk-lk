"""Бинарные сенсоры ТЕКК: минус на балансе, услуга не активна, обещанный платёж."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import billing
from .entity import TekkEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities([NegativeBalance(c), NotActive(c), PromiseActive(c)])


class NegativeBalance(TekkEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, c) -> None:
        super().__init__(c, "negative_balance")

    @property
    def is_on(self) -> bool | None:
        bal = billing.balance(self.data)
        return None if bal is None else bal < billing.threshold(self.data)


class NotActive(TekkEntity, BinarySensorEntity):
    """Включён, если статус абонента в кабинете не «Активен» (блокировка, отпуск)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, c) -> None:
        super().__init__(c, "not_active")

    @property
    def is_on(self) -> bool | None:
        status = self.index.get("status")
        return None if not status else not status.lower().startswith("актив")

    @property
    def extra_state_attributes(self):
        return {"статус": self.index.get("status")}


class PromiseActive(TekkEntity, BinarySensorEntity):
    _attr_icon = "mdi:hand-coin"

    def __init__(self, c) -> None:
        super().__init__(c, "promise")

    @property
    def is_on(self) -> bool | None:
        return (self.data.get("promise") or {}).get("active")

    @property
    def extra_state_attributes(self):
        p = self.data.get("promise") or {}
        until = p.get("until")
        return {
            "действует_до": until.isoformat() if until else None,
            "можно_подключить": p.get("can_activate"),
        }

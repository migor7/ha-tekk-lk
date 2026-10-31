"""Сенсоры ТЕКК: баланс, абонплата, дата и сумма списания, дней до окончания оплаты."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change

from . import billing
from .entity import TekkEntity

RUB = "RUB"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities(
        [
            BalanceSensor(c),
            StatusSensor(c),
            TariffSensor(c),
            FeeSensor(c),
            PaydaySensor(c),
            NextChargeSensor(c),
            ShortfallSensor(c),
            DaysLeftSensor(c),
            LastChargeSensor(c),
            LastPaymentSensor(c),
        ]
    )


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


class _Daily(TekkEntity, SensorEntity):
    """Сенсор, значение которого зависит от сегодняшней даты: пересчёт в полночь."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=5)
        )

    @callback
    def _midnight(self, _now) -> None:
        self.async_write_ha_state()


class _Money(TekkEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = RUB
    _attr_suggested_display_precision = 2


class BalanceSensor(_Money):
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:cash"

    def __init__(self, c) -> None:
        super().__init__(c, "balance")

    @property
    def native_value(self) -> float | None:
        return billing.balance(self.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        i = self.index
        return {
            "статус": i.get("status"),
            "порог_отключения": i.get("threshold"),
            "схема_расчётов": i.get("scheme"),
            "непрочитанные_сообщения": i.get("unread_messages"),
        }


class StatusSensor(TekkEntity, SensorEntity):
    _attr_icon = "mdi:account-check"

    def __init__(self, c) -> None:
        super().__init__(c, "status")

    @property
    def native_value(self) -> str | None:
        return self.index.get("status")


class TariffSensor(TekkEntity, SensorEntity):
    _attr_icon = "mdi:web"

    def __init__(self, c) -> None:
        super().__init__(c, "tariff")

    @property
    def native_value(self) -> str | None:
        return billing.main_service(self.data).get("tariff")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "услуги": [
                {"услуга": s.get("name"), "тариф": s.get("tariff"), "абонплата": s.get("fee")}
                for s in billing.services(self.data)
            ]
        }


class FeeSensor(_Money):
    """Абонплата в месяц по всем услугам."""

    _attr_icon = "mdi:cash-multiple"

    def __init__(self, c) -> None:
        super().__init__(c, "fee")

    @property
    def native_value(self) -> float | None:
        return billing.monthly_fee(self.data)


class PaydaySensor(_Daily):
    """Дата следующего списания абонплаты."""

    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, c) -> None:
        super().__init__(c, "payday")

    @property
    def native_value(self) -> date | None:
        return self.schedule()["next_date"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.schedule()
        return {
            "день_списания": s["charge_day"],
            "схема": "раз в месяц" if s["mode"] == "monthly" else "посуточно",
            "откуда_дата": s["source"],
        }


class NextChargeSensor(_Money):
    _attr_icon = "mdi:cash-clock"

    def __init__(self, c) -> None:
        super().__init__(c, "next_charge")

    @property
    def native_value(self) -> float | None:
        return self.schedule()["next_amount"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.schedule()
        bal = billing.balance(self.data)
        after = None if bal is None or s["next_amount"] is None else round(bal - s["next_amount"], 2)
        return {"дата": _iso(s["next_date"]), "баланс_после_списания": after}


class ShortfallSensor(_Money, _Daily):
    """Сколько не хватает до ближайшего списания: max(0, сумма − (баланс − порог))."""

    _attr_icon = "mdi:cash-minus"

    def __init__(self, c) -> None:
        super().__init__(c, "shortfall")

    @property
    def native_value(self) -> float | None:
        s = self.schedule()
        bal = billing.balance(self.data)
        if bal is None or s["next_amount"] is None:
            return None
        return round(max(0.0, s["next_amount"] - (bal - billing.threshold(self.data))), 2)


class DaysLeftSensor(_Daily):
    """Сколько дней услуга будет оплачена, учитывая баланс и будущие списания."""

    _attr_icon = "mdi:calendar-end"
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, c) -> None:
        super().__init__(c, "days_left")

    @property
    def native_value(self) -> int | None:
        return self.schedule()["days_left"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.schedule()
        until = s["paid_until"]
        fee = billing.monthly_fee(self.data)
        bal = billing.balance(self.data)
        top_up = None
        if until and fee and bal is not None:
            # сколько внести до даты, когда денег не хватит, чтобы оплатить и её
            top_up = round(max(0.0, fee - ((bal - billing.threshold(self.data)) % fee)), 2)
        return {
            "оплачено_по": _iso(until - timedelta(days=1)) if until else None,
            "не_хватит_на_списание": _iso(until),
            "пополнить_на": top_up,
        }


class _LastRecord(_Money):
    source = ""

    @property
    def record(self) -> dict | None:
        recs = billing.charges(self.data) if self.source == "discardings" else [
            r for r in self.data.get("payments") or [] if r.get("date")
        ]
        return recs[0] if recs else None

    @property
    def native_value(self) -> float | None:
        r = self.record
        return abs(r["amount"]) if r and r.get("amount") is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        r = self.record or {}
        cells = r.get("cells") if isinstance(r.get("cells"), dict) else {}
        out = {"дата": r["date"].date().isoformat() if r.get("date") else None}
        for key in ("Услуга", "Тариф", "Описание", "Платежная система"):
            if cells.get(key):
                out[key.lower().replace(" ", "_")] = cells[key]
        return out


class LastChargeSensor(_LastRecord):
    source = "discardings"
    _attr_icon = "mdi:cash-remove"

    def __init__(self, c) -> None:
        super().__init__(c, "last_charge")


class LastPaymentSensor(_LastRecord):
    source = "payments"
    _attr_icon = "mdi:cash-plus"

    def __init__(self, c) -> None:
        super().__init__(c, "last_payment")

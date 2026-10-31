"""Диагностика: разобранные данные и сырые фрагменты страниц без личных данных."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

REDACTED = "**REDACTED**"

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    "contract",
    "account",
    "address",
    "Абонент",
    "Договор",
    "Номер лицевого счета",
    "Основной адрес",
    "Адрес предоставления",
    "Документ",
}


def _secrets(coordinator) -> list[str]:
    """Значения, которые нельзя показывать нигде, даже внутри текста."""
    out = [coordinator.entry.data.get(CONF_USERNAME)]
    index = (coordinator.data or {}).get("index") or {}
    info = index.get("info") or {}
    out += [info.get(k) for k in ("Абонент", "Номер лицевого счета", "Основной адрес")]
    out += [index.get("contract")]
    out += [s.get("address") for s in index.get("services") or []]
    return sorted({s for s in out if s and len(s) >= 3}, key=len, reverse=True)


def _scrub(obj: Any, secrets: list[str], depth: int = 0) -> Any:
    if depth > 10:
        return "…"
    if isinstance(obj, dict):
        return {k: _scrub(v, secrets, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v, secrets, depth + 1) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, str):
        for s in secrets:
            obj = obj.replace(s, REDACTED)
        return obj
    return obj


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    secrets = _secrets(coordinator)
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "data": _scrub(async_redact_data(coordinator.data or {}, TO_REDACT), secrets),
        "raw": _scrub(async_redact_data(coordinator.api.raw, TO_REDACT), secrets),
    }

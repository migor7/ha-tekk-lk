"""ТЕКК (lk.tekk.pro): баланс и абонплата в Home Assistant."""

from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import TekkApi
from .coordinator import TekkCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Отдельная сессия со своей cookie-jar: JSESSIONID кабинета не смешивается с другими.
    session = async_create_clientsession(hass)
    api = TekkApi(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = TekkCoordinator(hass, entry, api)
    # первый опрос (вход + 4 страницы) не дольше 45 с, иначе HA повторит запуск сам
    try:
        async with asyncio.timeout(45):
            await coordinator.async_config_entry_first_refresh()
    except TimeoutError as err:
        raise ConfigEntryNotReady("lk.tekk.pro не ответил за 45 с") from err
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

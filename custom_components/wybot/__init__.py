"""The WyBot integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import CONF_WIFI_PASSWORD, CONF_WIFI_SSID
from .wybot_coordinator import WyBotCoordinator
from wybot import WybotAuthError, WybotConnectionError
from wybot import WyBotHTTPClient

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.VACUUM,
]
_LOGGER = logging.getLogger(__name__)

type WyBotConfigEntry = ConfigEntry[WyBotCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: WyBotConfigEntry) -> bool:
    """Set up WyBot from a config entry."""
    wybot_http_client = WyBotHTTPClient(
        entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )

    try:
        await hass.async_add_executor_job(wybot_http_client.authenticate)
    except WybotAuthError as err:
        raise ConfigEntryAuthFailed("Invalid WyBot credentials") from err
    except WybotConnectionError as err:
        raise ConfigEntryNotReady("Unable to reach the WyBot API") from err

    coordinator = WyBotCoordinator(
        hass, wybot_http_client=wybot_http_client, config_entry=entry
    )
    entry.runtime_data = coordinator

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Stop coordinator resources (MQTT) on unload.
    entry.async_on_unload(coordinator.async_stop)

    # Refresh stored WiFi credentials when options are updated.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: WyBotConfigEntry) -> None:
    """Handle options update by refreshing stored WiFi credentials."""
    entry.runtime_data.set_wifi_credentials(
        entry.data.get(CONF_WIFI_SSID), entry.data.get(CONF_WIFI_PASSWORD)
    )


async def async_unload_entry(hass: HomeAssistant, entry: WyBotConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

"""The WyBot integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from wybot.exceptions import WybotAuthError, WybotConnectionError
from wybot.http_client import WyBotHTTPClient

from .const import CONF_WIFI_PASSWORD, CONF_WIFI_SSID, DOMAIN
from .wybot_coordinator import WyBotCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VACUUM,
]
_LOGGER = logging.getLogger(__name__)

type WyBotConfigEntry = ConfigEntry[WyBotCoordinator]


def _device_identifiers(coordinator: WyBotCoordinator) -> set[tuple[str, str]]:
    """Return the device-registry identifiers currently backed by the account."""
    identifiers: set[tuple[str, str]] = set()
    for idx, group in coordinator.data.items():
        identifiers.add((DOMAIN, str(idx)))
        if getattr(group, "docker", None):
            identifiers.add((DOMAIN, f"{idx}_dock"))
    return identifiers


@callback
def _async_purge_stale_devices(
    hass: HomeAssistant, entry: WyBotConfigEntry, coordinator: WyBotCoordinator
) -> None:
    """Remove registry devices no longer backed by the WyBot account."""
    registry = dr.async_get(hass)
    current = _device_identifiers(coordinator)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & current:
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def async_setup_entry(hass: HomeAssistant, entry: WyBotConfigEntry) -> bool:
    """Set up WyBot from a config entry."""
    wybot_http_client = WyBotHTTPClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        session=async_get_clientsession(hass),
    )

    try:
        await wybot_http_client.authenticate()
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

    # Remove devices that are no longer present on the account (stale-devices),
    # now and on every subsequent coordinator update.
    def _purge() -> None:
        _async_purge_stale_devices(hass, entry, coordinator)

    _purge()
    entry.async_on_unload(coordinator.async_add_listener(_purge))

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


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: WyBotConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow manual removal of a device no longer provided by the account."""
    return not device.identifiers & _device_identifiers(entry.runtime_data)

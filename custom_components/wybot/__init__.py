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
    for idx in coordinator.data:
        identifiers.add((DOMAIN, str(idx)))
        if coordinator.has_dock(idx):
            identifiers.add((DOMAIN, f"{idx}_dock"))
    return identifiers


@callback
def _async_link_robots_to_docks(
    hass: HomeAssistant, entry: WyBotConfigEntry, coordinator: WyBotCoordinator
) -> None:
    """Record in the device registry that each robot is reached via its dock.

    This used to be the ``via_device`` key of the robot's DeviceInfo, which HA
    deprecated in favour of ``via_device_id`` (a registry id, removed in HA
    2027.8). Linking here works on every supported HA version, and runs after
    the platforms have registered both devices. Devices are looked up within
    this entry because identifiers are no longer unique across entries.
    """
    registry = dr.async_get(hass)
    by_identifier = {
        identifier: device
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
        for identifier in device.identifiers
    }
    for idx in coordinator.data:
        if not coordinator.has_dock(idx):
            continue
        robot = by_identifier.get((DOMAIN, str(idx)))
        dock = by_identifier.get((DOMAIN, f"{idx}_dock"))
        if robot is None or dock is None or robot.via_device_id == dock.id:
            continue
        registry.async_update_device(robot.id, via_device_id=dock.id)


@callback
def _async_purge_stale_devices(
    hass: HomeAssistant, entry: WyBotConfigEntry, coordinator: WyBotCoordinator
) -> None:
    """Remove registry devices no longer backed by the WyBot account."""
    registry = dr.async_get(hass)
    current = _device_identifiers(coordinator)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & current:
            # Not async_update_device(remove_config_entry_id=...), which HA
            # deprecated (removed in 2027.8) now that a device belongs to a
            # single config entry. These devices only ever belong to this one.
            registry.async_remove_device(device.id)


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

    # Remove devices that are no longer present on the account (stale-devices)
    # and link robots to their docks, now and on every coordinator update.
    def _purge() -> None:
        _async_purge_stale_devices(hass, entry, coordinator)
        _async_link_robots_to_docks(hass, entry, coordinator)

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

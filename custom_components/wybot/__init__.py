"""The WyBot integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .wybot_http_client import WyBotHTTPClient
from .wybot_mqtt_client import WyBotMQTTClient
from .wybot_coordinator import WyBotCoordinator

PLATFORMS: list[Platform] = [Platform.VACUUM]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up WyBot from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    wybot_http_client = WyBotHTTPClient(
        entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )

    authed = await hass.async_add_executor_job(wybot_http_client.authenticate)
    if not authed:
        return False
    coordinator = WyBotCoordinator(hass, wybot_http_client=wybot_http_client)

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def stop_mqtt(event):
        """Stop MQTT client on Home Assistant stop."""
        await coordinator.async_stop()

    hass.bus.async_listen_once("homeassistant_stop", stop_mqtt)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_stop()
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

"""The WyBot integration."""

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .wybot_http_client import WyBotHTTPClient
from .wybot_mqtt_client import WyBotMQTTClient
from .wybot_coordinator import WyBotCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.VACUUM]
_LOGGER = logging.getLogger(__name__)

# Retry configuration for initial setup
MAX_SETUP_RETRIES = 3
SETUP_RETRY_DELAY = 2.0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up WyBot from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    wybot_http_client = WyBotHTTPClient(
        entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )

    # Retry authentication with exponential backoff
    delay = SETUP_RETRY_DELAY
    last_error = None
    for attempt in range(MAX_SETUP_RETRIES):
        try:
            authed = await hass.async_add_executor_job(wybot_http_client.authenticate)
            if authed:
                break
            else:
                _LOGGER.warning("Authentication failed (attempt %d/%d)",
                              attempt + 1, MAX_SETUP_RETRIES)
                if attempt < MAX_SETUP_RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    raise ConfigEntryAuthFailed("Invalid username or password")
        except ConfigEntryAuthFailed:
            # Re-raise auth failures immediately
            raise
        except Exception as err:
            last_error = err
            _LOGGER.warning("Authentication error (attempt %d/%d): %s",
                          attempt + 1, MAX_SETUP_RETRIES, err)
            if attempt < MAX_SETUP_RETRIES - 1:
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise ConfigEntryNotReady(
                    f"Failed to authenticate after {MAX_SETUP_RETRIES} attempts"
                ) from err

    coordinator = WyBotCoordinator(hass, wybot_http_client=wybot_http_client)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        # Re-raise auth failures
        raise
    except Exception as err:
        raise ConfigEntryNotReady(
            "Failed to connect to WyBot API during setup"
        ) from err

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

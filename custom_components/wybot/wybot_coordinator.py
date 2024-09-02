import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .wybot_client import WyBotClient
from .wybot_responses import Group

_LOGGER = logging.getLogger(__name__)


class WyBotCoordinator(DataUpdateCoordinator):
    """Coordinates data between WyBot and Homeassistant."""

    wybot_client: WyBotClient
    hass: HomeAssistant
    data: dict[str, Group]

    def __init__(self, hass: HomeAssistant, wybot_client: WyBotClient) -> None:
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="WyBot Coordinator",
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=30),
        )
        self.wybot_client = wybot_client
        self.hass = hass

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with asyncio.timeout(10):
                return await self.hass.async_add_executor_job(
                    self.wybot_client.get_indexed_current_grouped_devices
                )
        except any as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    @property
    def vacuums(self) -> list[str]:
        """Return a list of vacuum device ids.

        Right now we only support WyBot vacuums so we return everything, but this could be expanded
        """
        return [deviceId for [deviceId, device] in self.data.items()]

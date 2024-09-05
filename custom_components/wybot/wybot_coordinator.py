import asyncio
import time
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .wybot_mqtt_client import WyBotMQTTClient
from .wybot_http_client import WyBotHTTPClient
from .wybot_models import Group, Command, Device, Docker
from .wybot_dp_models import GenericDP

_LOGGER = logging.getLogger(__name__)


class WyBotCoordinator(DataUpdateCoordinator):
    """Coordinates data between WyBot and Homeassistant."""

    wybot_http_client: WyBotHTTPClient
    wybot_mqtt_client: WyBotMQTTClient
    hass: HomeAssistant
    data: dict[str, Group]
    initial_load = False

    def __init__(
        self,
        hass: HomeAssistant,
        wybot_http_client: WyBotHTTPClient,
    ) -> None:
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="WyBot Coordinator",
            # Polling interval. Will only be polled if there are subscribers and if no new mqtt data came in,
            # because of this we set polling to a high value
            update_interval=timedelta(seconds=120),
        )
        self.wybot_http_client = wybot_http_client
        self.wybot_mqtt_client = WyBotMQTTClient(self.on_message)
        self.wybot_mqtt_client.connect()
        self.hass = hass

    async def async_stop(self):
        """Stop the MQTT client."""
        _LOGGER.info("Stopping MQTT client")
        self.wybot_mqtt_client.disconnect()

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:
            async with asyncio.timeout(10):
                # First up-date, fill the array from HTTP and then subscribe to MQTT
                if self.initial_load is False:
                    self.initial_load = True
                    await self.http_refresh_data()

                if self.wybot_mqtt_client.is_connected() is False:
                    self.wybot_mqtt_client.reconnect()

                return self.data
        except any as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def http_refresh_data(self):
        self.data = await self.hass.async_add_executor_job(
            self.wybot_http_client.get_indexed_current_grouped_devices
        )
        self.subscribe_mqtt(self.data)

    def subscribe_mqtt(self, data: dict[str, Group]):
        """Subscribe to MQTT updates for a device."""
        for [deviceId, device] in data.items():
            self.wybot_mqtt_client.subscribe_for_device(device.device.device_id)
            if device.docker is not None:
                self.wybot_mqtt_client.subscribe_for_device(device.docker.docker_id)

    def on_message(self, topic: str, data: dict[str, any]):
        """Handle a message from MQTT."""
        if topic.startswith("/will/"):
            deviceId = topic[6:]
            _LOGGER.debug(
                f"Received device online {deviceId} with {data['online'] == '1'}"
            )
        if topic.startswith("/device/DATA/send_transparent_data/"):
            deviceId = topic[35:]
            command_response = Command(**data)
            group = self.get_group(deviceId)
            if group.docker is not None and group.docker.docker_id == deviceId:
                group.docker.dps = {
                    **group.docker.dps,
                    **command_response.get_dps_as_keyed_dict(),
                }
                _LOGGER.debug(
                    f"SEND RESPONSE ---- docker - {deviceId} ---- Current DPs: {group.docker.dps}"
                )
            else:
                group.device.dps = {
                    **group.device.dps,
                    **command_response.get_dps_as_keyed_dict(),
                }
                _LOGGER.debug(
                    f"SEND RESPONSE ---- device - {deviceId} ---- Current DPs: {group.device.dps}"
                )
            self.data[group.id] = group

        if topic.startswith("/device/DATA/recv_transparent_query_data/"):
            deviceId = topic[41:]
            command_response = Command(**data)
            _LOGGER.debug(f"Query CMD ---- {deviceId} ----- {command_response}")
        if topic.startswith("/device/DATA/recv_transparent_cmd_data/"):
            deviceId = topic[39:]
            command_response = Command(**data)
            _LOGGER.debug(f"SEND CMD ---- {deviceId} ----- {command_response}")

        self.hass.add_job(self.async_set_updated_data, self.data)

    def get_device_or_docker(self, deviceId: str) -> Device | Docker:
        """loops through the self.data and find the device matching the deviceId"""
        for [device_id, device] in self.data.items():
            if device.device.device_id == deviceId:
                return device.device
            if device.docker is not None and device.docker.docker_id == deviceId:
                return device.docker
        return None

    def get_group(self, deviceId: str) -> Group:
        for [device_id, device] in self.data.items():
            if device.device.device_id == deviceId:
                return device
            if device.docker is not None and device.docker.docker_id == deviceId:
                return device
        return None

    def send_write_command(self, group: Group, dp: GenericDP):
        """Send a command to a group. First send to the device, then send to the docker if it exists."""
        command = {"ts": time.time(), "cmd": 4, "dp": [dp.dict()]}
        self.wybot_mqtt_client.send_write_command_for_device(
            group.device.device_id, command
        )
        if group.docker is not None:
            self.wybot_mqtt_client.send_write_command_for_device(
                group.docker.docker_id, command
            )

    @property
    def vacuums(self) -> list[str]:
        """Return a list of vacuum device ids.

        Right now we only support WyBot vacuums so we return everything, but this could be expanded
        """
        return [deviceId for [deviceId, device] in self.data.items()]

import asyncio
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .wybot_dp_models import GenericDP
from .wybot_http_client import WyBotHTTPClient
from .wybot_models import Command, Device, Docker, Group
from .wybot_mqtt_client import WyBotMQTTClient

_LOGGER = logging.getLogger(__name__)

# Retry configuration
MAX_HTTP_RETRIES = 3
INITIAL_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 10.0


class WyBotCoordinator(DataUpdateCoordinator):
    """Coordinates data between WyBot and Homeassistant."""

    wybot_http_client: WyBotHTTPClient
    wybot_mqtt_client: WyBotMQTTClient
    hass: HomeAssistant
    data: dict[str, Group]
    initial_load: bool = False
    _connection_available: bool = True
    _http_failure_count: int = 0
    _mqtt_failure_count: int = 0
    _last_status_query_time: float = 0.0
    _online_devices: set[str] = set()
    _initial_query_start_time: float = 0.0

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
        self.data = {}

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
            async with asyncio.timeout(30):
                # First update, fill the array from HTTP and then subscribe to MQTT
                if self.initial_load is False:
                    self.initial_load = True
                    await self.http_refresh_data()
                    self._initial_query_start_time = time.time()
                    # Query all devices immediately after subscription (like iOS app does on open)
                    if self.wybot_mqtt_client.is_connected():
                        await self.query_all_known_devices()
                        self._last_status_query_time = time.time()

                # Ensure MQTT connection is active
                if not self.wybot_mqtt_client.is_connected():
                    _LOGGER.debug("MQTT not connected, attempting reconnection")
                    reconnected = await self.wybot_mqtt_client.async_reconnect()
                    if not reconnected:
                        self._mqtt_failure_count += 1
                        if self._mqtt_failure_count >= 3:
                            _LOGGER.warning(
                                "MQTT connection failed multiple times, marking as unavailable"
                            )
                            self._connection_available = False
                    else:
                        self._mqtt_failure_count = 0
                        if not self._connection_available:
                            _LOGGER.info("MQTT connection restored")
                            self._connection_available = True
                else:
                    self._mqtt_failure_count = 0
                    if not self._connection_available:
                        _LOGGER.info("Connection restored")
                        self._connection_available = True
                        # Query all devices immediately when connection is restored (like iOS app does on open)
                        if self.wybot_mqtt_client.is_connected():
                            await self.query_all_known_devices()
                            self._last_status_query_time = time.time()

                # Periodically query device status to replicate phone app behavior
                # Query more frequently initially (every 5 seconds for first minute), then every 10 seconds
                current_time = time.time()
                time_since_initial = (
                    current_time - self._initial_query_start_time
                    if self._initial_query_start_time > 0
                    else 60.0
                )
                query_interval = 5.0 if time_since_initial < 60.0 else 10.0

                if current_time - self._last_status_query_time >= query_interval:
                    if self.wybot_mqtt_client.is_connected():
                        # Use async version with delays to match iOS app behavior
                        await self.query_online_devices_async()
                        self._last_status_query_time = current_time

                return self.data
        except TimeoutError as err:
            _LOGGER.error("Timeout updating data: %s", err)
            self._connection_available = False
            raise UpdateFailed("Timeout communicating with API") from err
        except Exception as err:
            _LOGGER.error("Error communicating with API: %s", err)
            self._connection_available = False
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def http_refresh_data(self):
        """Refresh data from HTTP API with retry logic."""
        delay = INITIAL_RETRY_DELAY
        last_error = None

        for attempt in range(MAX_HTTP_RETRIES):
            try:
                data = await self.hass.async_add_executor_job(
                    self.wybot_http_client.get_indexed_current_grouped_devices
                )
                if data:
                    self.data = data
                    self.subscribe_mqtt(self.data)
                    self._http_failure_count = 0
                    self._connection_available = True
                    return
                _LOGGER.warning(
                    "HTTP refresh returned empty data (attempt %d/%d)",
                    attempt + 1,
                    MAX_HTTP_RETRIES,
                )
                if attempt < MAX_HTTP_RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    raise UpdateFailed("Failed to get device data after retries")
            except Exception as err:
                last_error = err
                _LOGGER.warning(
                    "HTTP refresh failed (attempt %d/%d): %s",
                    attempt + 1,
                    MAX_HTTP_RETRIES,
                    err,
                )
                self._http_failure_count += 1

                # Check if it's an authentication error
                if "401" in str(err) or "authentication" in str(err).lower():
                    _LOGGER.error("Authentication failed, credentials may be invalid")
                    raise ConfigEntryAuthFailed("Authentication failed") from err

                if attempt < MAX_HTTP_RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    self._connection_available = False
                    if self._http_failure_count >= MAX_HTTP_RETRIES:
                        _LOGGER.error(
                            "HTTP connection failed multiple times, marking as unavailable"
                        )
                        raise ConfigEntryNotReady(
                            "Failed to connect to WyBot API after retries"
                        ) from err
                    raise UpdateFailed(f"Failed to refresh data: {err}") from err

        # If we get here, all retries failed
        self._connection_available = False
        if last_error:
            raise ConfigEntryNotReady("Failed to connect to WyBot API") from last_error
        raise UpdateFailed("Failed to refresh data after retries")

    def subscribe_mqtt(self, data: dict[str, Group]):
        """Subscribe to MQTT updates for devices.

        Subscribes to all topics (initial and response) for all devices upfront,
        matching iOS app behavior where all subscriptions happen at once.
        """
        for [deviceId, device] in data.items():
            # Subscribe to all topics for both device and docker upfront
            # This matches iOS app behavior where both devices' topics are subscribed at once
            self.wybot_mqtt_client.subscribe_for_device_initial(device.device.device_id)
            self.wybot_mqtt_client.subscribe_for_device(device.device.device_id)
            if device.docker is not None:
                self.wybot_mqtt_client.subscribe_for_device_initial(
                    device.docker.docker_id
                )
                self.wybot_mqtt_client.subscribe_for_device(device.docker.docker_id)

    def on_message(self, topic: str, data: dict[str, any]):
        """Handle a message from MQTT."""
        data_updated = False

        if topic.startswith("/will/"):
            deviceId = topic[6:]
            is_online = data.get("online") == "1"
            _LOGGER.debug("Received device online %s with %s", deviceId, is_online)
            # Track online status
            if is_online:
                self._online_devices.add(deviceId)
                # Query immediately when device comes online using iOS app pattern
                # This implements: initial query -> subscribe to responses -> remaining queries
                # Must use add_job since we're in MQTT callback thread
                if self.wybot_mqtt_client.is_connected():
                    self.hass.add_job(
                        self.wybot_mqtt_client.query_device_ios_pattern,
                        deviceId,
                    )
            else:
                self._online_devices.discard(deviceId)
            # Will messages indicate device availability changes, always trigger update
            data_updated = True

        if topic.startswith("/device/DATA/send_transparent_data/"):
            deviceId = topic[35:]
            command_response = Command(**data)
            group = self.get_group(deviceId)
            if (
                group is not None
                and group.docker is not None
                and group.docker.docker_id == deviceId
            ):
                group.docker.dps = {
                    **group.docker.dps,
                    **command_response.get_dps_as_keyed_dict(),
                }
                _LOGGER.debug(
                    "SEND RESPONSE ---- docker - %s ---- Current DPs: %s",
                    deviceId,
                    group.docker.dps,
                )
                data_updated = True
            elif group is not None and group.device is not None:
                group.device.dps = {
                    **group.device.dps,
                    **command_response.get_dps_as_keyed_dict(),
                }
                _LOGGER.debug(
                    "SEND RESPONSE ---- device - %s ---- Current DPs: %s",
                    deviceId,
                    group.device.dps,
                )
                data_updated = True
            if group is not None:
                self.data[group.id] = group

        if topic.startswith("/device/DATA/recv_transparent_query_data/"):
            deviceId = topic[41:]
            command_response = Command(**data)
            _LOGGER.debug("Query CMD ---- %s ----- %s", deviceId, command_response)

        if topic.startswith("/device/DATA/recv_transparent_cmd_data/"):
            deviceId = topic[39:]
            command_response = Command(**data)
            _LOGGER.debug("SEND CMD ---- %s ----- %s", deviceId, command_response)

        # Always trigger update when we receive MQTT messages to ensure state is written
        # This ensures history is recorded even if computed state values don't change
        if data_updated or topic.startswith("/device/DATA/"):
            self.hass.add_job(self.async_set_updated_data, self.data)

    def get_device_or_docker(self, deviceId: str) -> Device | Docker | None:
        """Loops through the self.data and find the device matching the deviceId"""
        for [device_id, device] in self.data.items():
            if device.device.device_id == deviceId:
                return device.device
            if device.docker is not None and device.docker.docker_id == deviceId:
                return device.docker
        return None

    def get_group(self, deviceId: str) -> Group | None:
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

    def query_all_device_status(self) -> None:
        """Query status for all devices by sending individual DP queries."""
        for device_id in self.data:
            group = self.data[device_id]
            if self.wybot_mqtt_client.is_connected():
                self.wybot_mqtt_client.ensure_device_sends_statuses(
                    group.device.device_id
                )
                if group.docker is not None:
                    self.wybot_mqtt_client.ensure_device_sends_statuses(
                        group.docker.docker_id
                    )

    def query_online_devices(self) -> None:
        """Query status only for online devices by sending individual DP queries.

        If no devices are tracked as online yet, query all devices (initial state).
        Note: This is a sync method, so queries are sent immediately without delays.
        For async version with delays, use query_online_devices_async.
        """
        for device_id in self.data:
            group = self.data[device_id]
            if self.wybot_mqtt_client.is_connected():
                # If we haven't tracked any online devices yet, query all (initial state)
                # Otherwise, only query if device is online
                if (
                    not self._online_devices
                    or group.device.device_id in self._online_devices
                ):
                    self.wybot_mqtt_client.ensure_device_sends_statuses(
                        group.device.device_id
                    )
                # Always query docker if it exists and (we're in initial state or it's online)
                if group.docker is not None:
                    if (
                        not self._online_devices
                        or group.docker.docker_id in self._online_devices
                    ):
                        self.wybot_mqtt_client.ensure_device_sends_statuses(
                            group.docker.docker_id
                        )

    async def query_all_known_devices(self) -> None:
        """Query all known devices using iOS app pattern.

        This queries both device and docker for all groups, matching the iOS app
        behavior where all devices are queried immediately after subscription.
        """
        for device_id in self.data:
            group = self.data[device_id]
            if self.wybot_mqtt_client.is_connected():
                # Query device using iOS pattern
                await self.wybot_mqtt_client.query_device_ios_pattern(
                    group.device.device_id
                )
                # Query docker if it exists
                if group.docker is not None:
                    await self.wybot_mqtt_client.query_device_ios_pattern(
                        group.docker.docker_id
                    )

    async def query_online_devices_async(self) -> None:
        """Query status only for online devices with delays matching iOS app behavior."""
        for device_id in self.data:
            group = self.data[device_id]
            if self.wybot_mqtt_client.is_connected():
                # If we haven't tracked any online devices yet, query all (initial state)
                # Otherwise, only query if device is online
                if (
                    not self._online_devices
                    or group.device.device_id in self._online_devices
                ):
                    await self.wybot_mqtt_client.query_device_ios_pattern(
                        group.device.device_id
                    )
                # Always query docker if it exists and (we're in initial state or it's online)
                if group.docker is not None:
                    if (
                        not self._online_devices
                        or group.docker.docker_id in self._online_devices
                    ):
                        await self.wybot_mqtt_client.query_device_ios_pattern(
                            group.docker.docker_id
                        )

    @property
    def available(self) -> bool:
        """Return if the coordinator is available."""
        return self._connection_available and bool(self.data)

    @property
    def vacuums(self) -> list[str]:
        """Return a list of vacuum device ids.

        Right now we only support WyBot vacuums so we return everything, but this could be expanded
        """
        return [deviceId for [deviceId, device] in self.data.items()]

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from wybot.ble_client import WyBotBLEClient
from wybot.dp_models import GenericDP
from wybot.exceptions import WybotAuthError, WybotConnectionError
from wybot.http_client import WyBotHTTPClient
from wybot.models import Command, Device, Docker, Group
from wybot.mqtt_client import WyBotMQTTClient

from .bluetooth_adapter import HomeAssistantBluetoothAdapter
from .const import (
    BLE_MAX_CONSECUTIVE_FAILURES,
    CONF_WIFI_PASSWORD,
    CONF_WIFI_SSID,
    F1_DEVICE_TYPE,
)

_LOGGER = logging.getLogger(__name__)

# Retry configuration
MAX_HTTP_RETRIES = 3
INITIAL_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 10.0

# Overall update timeout — safety net; individual operations have their own timeouts
UPDATE_TIMEOUT = 120

# BLE failure recovery — re-enable BLE after this many seconds
BLE_RECOVERY_SECONDS = 300  # 5 minutes


class WyBotCoordinator(DataUpdateCoordinator):
    """Coordinates data between WyBot and Homeassistant.

    Architecture: BLE-primary with MQTT fallback
    - BLE polling is attempted first for all devices in range
    - MQTT is used only when BLE fails or device is out of range
    - HTTP session refresh keeps MQTT session alive for fallback
    """

    wybot_http_client: WyBotHTTPClient
    wybot_mqtt_client: WyBotMQTTClient
    wybot_ble_client: WyBotBLEClient
    hass: HomeAssistant
    data: dict[str, Group]
    initial_load: bool = False
    _connection_available: bool = True
    _http_failure_count: int = 0
    _mqtt_failure_count: int = 0
    _last_status_query_time: float = 0.0
    _online_devices: set[str] = set()
    _initial_query_start_time: float = 0.0
    _ble_wake_enabled: bool = True

    # BLE command tracking
    _ble_command_enabled: bool = True
    _ble_command_failures: dict[str, int]  # device_id -> consecutive failure count
    _ble_disabled_at: dict[str, float]  # device_id -> time.time() when BLE was disabled

    # WiFi credentials for manual provisioning via diagnostic button
    _wifi_ssid: str | None = None
    _wifi_password: str | None = None

    # Track last MQTT data received
    _last_mqtt_data: dict[str, datetime]  # device_id -> last MQTT data time

    # Track last BLE poll time
    _last_ble_poll: dict[str, datetime]  # device_id -> last BLE poll time

    # Track data source per device (for diagnostics and logging)
    _data_source: dict[str, str]  # device_id -> "ble" | "mqtt"

    # Track BLE availability per device
    _ble_available: dict[str, bool]  # device_id -> last BLE poll succeeded

    # MQTT lazy connection state
    _mqtt_connected: bool = False
    # Time of the most recent successful MQTT (re)connect (diagnostic)
    _mqtt_last_connected_at: datetime | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        wybot_http_client: WyBotHTTPClient,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="WyBot Coordinator",
            update_interval=timedelta(seconds=30),
        )
        self.wybot_http_client = wybot_http_client
        self.wybot_mqtt_client = WyBotMQTTClient(self.on_message)
        # DON'T connect MQTT here - use lazy connection when needed as fallback
        self.wybot_ble_client = WyBotBLEClient(HomeAssistantBluetoothAdapter(hass))
        self.data = {}

        # Initialize data tracking
        self._last_mqtt_data = {}
        self._last_ble_poll = {}
        self._data_source = {}
        self._ble_available = {}

        # Initialize BLE command tracking
        self._ble_command_failures = {}
        self._ble_disabled_at = {}

        # Load WiFi credentials from config entry (for manual provisioning)
        self._wifi_ssid = config_entry.data.get(CONF_WIFI_SSID)
        self._wifi_password = config_entry.data.get(CONF_WIFI_PASSWORD)
        if self._wifi_ssid:
            _LOGGER.debug(
                "WiFi credentials loaded for SSID: %s (manual provisioning only)",
                self._wifi_ssid,
            )

    def set_wifi_credentials(
        self, ssid: str | None, password: str | None
    ) -> None:
        """Update stored WiFi credentials (for manual provisioning)."""
        self._wifi_ssid = ssid
        self._wifi_password = password
        if ssid:
            _LOGGER.debug("WiFi credentials updated for SSID: %s", ssid)

    async def async_stop(self) -> None:
        """Stop the MQTT client."""
        if self._mqtt_connected:
            _LOGGER.info("Stopping MQTT client")
            await self.wybot_mqtt_client.disconnect()
            self._mqtt_connected = False

    async def _ensure_mqtt_connected(self) -> bool:
        """Lazy connect to MQTT (only when needed as fallback).

        Self-healing: if our flag claims connected but paho disagrees, reset
        and reconnect. This catches silent drops where on_disconnect didn't
        fire or paho's auto-retry is wedged.

        Returns:
            True if MQTT is connected, False otherwise
        """
        if self._mqtt_connected:
            if self.wybot_mqtt_client.is_connected():
                return True
            _LOGGER.warning(
                "MQTT state drift detected — flag says connected, paho says no; reconnecting"
            )
            self._mqtt_connected = False

        _LOGGER.info("Connecting to MQTT (fallback mode)")
        try:
            await self.wybot_mqtt_client.connect()
            self._mqtt_connected = True
            self._mqtt_last_connected_at = datetime.now(timezone.utc)
            # Subscribe to topics for all known devices
            if self.data:
                await self.subscribe_mqtt(self.data)
            _LOGGER.info("MQTT connected successfully (fallback ready)")
            return True
        except Exception as err:
            _LOGGER.warning("MQTT connection failed: %s", err)
            self._mqtt_connected = False
            return False

    def _record_mqtt_data_received(self, device_id: str) -> None:
        """Record that MQTT data was received from a device.

        Marks data_source="mqtt" only when a real MQTT message arrives, so
        the diagnostic sensor doesn't flap on every poll cycle that *tried*
        MQTT fallback.

        Args:
            device_id: The device ID that sent data
        """
        self._last_mqtt_data[device_id] = datetime.now(timezone.utc)
        self._data_source[device_id] = "mqtt"
        _LOGGER.debug("Recorded MQTT data received from device %s", device_id)

    def get_last_ble_communication(self, device_id: str) -> datetime | None:
        """Get the last successful BLE communication time for a device.

        Args:
            device_id: The device ID to query

        Returns:
            datetime of last BLE communication, or None if never communicated via BLE
        """
        return self._last_ble_poll.get(device_id)

    def get_last_mqtt_communication(self, device_id: str) -> datetime | None:
        """Get the last MQTT data received time for a device.

        Args:
            device_id: The device ID to query

        Returns:
            datetime of last MQTT data, or None if never received MQTT data
        """
        return self._last_mqtt_data.get(device_id)

    def get_data_source(self, device_id: str) -> str | None:
        """Get the current data source for a device.

        Args:
            device_id: The device ID to query

        Returns:
            "ble" or "mqtt" depending on last successful poll, or None if unknown
        """
        return self._data_source.get(device_id)

    def is_ble_available(self, device_id: str) -> bool | None:
        """Check if BLE is available for a device.

        Args:
            device_id: The device ID to query

        Returns:
            True if last BLE poll succeeded, False if failed, None if never tried
        """
        return self._ble_available.get(device_id)

    def _get_device_ble_info(self, group: Group) -> tuple[str | None, str | None]:
        """Get the BLE name and device ID for a group.

        Prefers docker BLE name since dock relays to robot.

        Args:
            group: The device group

        Returns:
            Tuple of (ble_name, device_id) or (None, None) if no BLE available
        """
        if group.docker and group.docker.ble_name:
            return group.docker.ble_name, group.docker.docker_id
        if group.device and group.device.ble_name:
            return group.device.ble_name, group.device.device_id
        return None, None

    def _update_device_dps_from_ble(
        self, group: Group, device_id: str, dps: list[dict[str, Any]]
    ) -> None:
        """Update device DPs from BLE response.

        Args:
            group: The device group to update
            device_id: The device ID
            dps: List of DP dicts from BLE response
        """
        if not dps:
            return

        # Create a command-like structure to reuse existing DP processing
        cmd_data: dict[str, Any] = {"cmd": 5, "ts": 0, "dp": dps}
        command = Command(**cmd_data)
        dp_dict: dict[str, Any] = command.get_dps_as_keyed_dict()

        if group.docker and group.docker.docker_id == device_id:
            group.docker.dps = {**group.docker.dps, **dp_dict}
            _LOGGER.debug(
                "Updated docker %s DPs from BLE: %s",
                device_id,
                list(dp_dict.keys()),
            )
        elif group.device:
            group.device.dps = {**group.device.dps, **dp_dict}
            _LOGGER.debug(
                "Updated device %s DPs from BLE: %s",
                device_id,
                list(dp_dict.keys()),
            )

    def _maybe_recover_ble(self, device_id: str) -> None:
        """Re-enable BLE for a device if enough time has passed since it was disabled."""
        disabled_at = self._ble_disabled_at.get(device_id)
        if disabled_at is not None and (time.time() - disabled_at) >= BLE_RECOVERY_SECONDS:
            _LOGGER.info(
                "Re-enabling BLE for device %s after %ds recovery period",
                device_id,
                BLE_RECOVERY_SECONDS,
            )
            self._ble_command_failures.pop(device_id, None)
            self._ble_disabled_at.pop(device_id, None)

    async def _poll_all_devices_via_ble(self) -> list[str]:
        """Poll all devices via BLE (primary data source).

        BLE provides faster, more reliable local communication when the device
        is in range. This is the PRIMARY data source in the BLE-first architecture.

        Returns:
            List of device IDs that need MQTT fallback (failed BLE or no BLE name)
        """
        devices_needing_mqtt: list[str] = []
        now = datetime.now(timezone.utc)

        for group_id, group in self.data.items():
            ble_name, device_id = self._get_device_ble_info(group)

            if not ble_name or not device_id:
                # No BLE name available, will need MQTT
                if group.device:
                    devices_needing_mqtt.append(group.device.device_id)
                continue

            # Check if BLE should be recovered for this device
            self._maybe_recover_ble(device_id)

            _LOGGER.debug(
                "BLE polling device %s via %s (primary)",
                device_id,
                ble_name,
            )

            try:
                dps = await self.wybot_ble_client.query_status(ble_name)

                if dps:
                    _LOGGER.debug(
                        "BLE poll success for %s: %d DPs received",
                        device_id,
                        len(dps),
                    )
                    self._update_device_dps_from_ble(group, device_id, dps)
                    self.data[group_id] = group

                    # Track successful BLE poll
                    self._last_ble_poll[device_id] = now
                    self._data_source[device_id] = "ble"
                    self._ble_available[device_id] = True
                else:
                    # BLE returned no data, need MQTT fallback
                    _LOGGER.debug(
                        "BLE poll for %s returned no data, using MQTT fallback",
                        device_id,
                    )
                    devices_needing_mqtt.append(device_id)
                    self._ble_available[device_id] = False

            except Exception as err:
                _LOGGER.debug(
                    "BLE poll failed for %s: %s, using MQTT fallback",
                    device_id,
                    err,
                )
                devices_needing_mqtt.append(device_id)
                self._ble_available[device_id] = False

        return devices_needing_mqtt

    async def _poll_devices_via_mqtt(self, device_ids: list[str]) -> None:
        """Poll specific devices via MQTT (fallback).

        Only called when BLE fails or device is out of range.

        Args:
            device_ids: List of device IDs that need MQTT polling
        """
        if not device_ids:
            return

        # Ensure MQTT is connected (lazy connection)
        if not await self._ensure_mqtt_connected():
            _LOGGER.warning(
                "MQTT fallback unavailable for %d devices",
                len(device_ids),
            )
            return

        _LOGGER.debug(
            "Using MQTT fallback for %d devices: %s",
            len(device_ids),
            device_ids,
        )

        for device_id in device_ids:
            await self.wybot_mqtt_client.ensure_device_sends_statuses(device_id)
            self._last_status_query_time = time.time()

    async def _maybe_refresh_http_session(self) -> None:
        """Periodically refresh HTTP session to keep MQTT fallback ready.

        The WyBot cloud may only relay MQTT data for "active" users,
        so we need to periodically refresh the HTTP session.
        """
        current_time = time.time()
        if not hasattr(self, "_last_http_refresh_time"):
            self._last_http_refresh_time = 0.0

        # Refresh every 60 seconds
        if current_time - self._last_http_refresh_time < 60.0:
            return

        _LOGGER.debug("Refreshing HTTP session to keep MQTT fallback ready")
        try:
            await self.wybot_http_client.register_presence()
            await self.wybot_http_client.get_devices_and_status()
            self._last_http_refresh_time = current_time
        except WybotAuthError:
            # Credentials became invalid during normal operation — let this
            # propagate so _async_update_data can trigger the reauth flow.
            raise
        except Exception as err:
            _LOGGER.debug("HTTP refresh failed (non-critical): %s", err)

        # MQTT keepalive: keep the fallback layer warm so silent drops are
        # detected within ~60s instead of "the next time BLE happens to fail".
        # _ensure_mqtt_connected is drift-aware and idempotent when healthy.
        if self.data:
            try:
                await self._ensure_mqtt_connected()
            except Exception as err:
                _LOGGER.debug("MQTT keepalive failed (non-critical): %s", err)

    async def _async_update_data(self) -> dict[str, Group]:
        """Fetch data using BLE-primary with MQTT fallback architecture.

        Priority:
        1. BLE polling (primary) - for devices in Bluetooth range
        2. MQTT polling (fallback) - for devices that failed BLE
        3. HTTP session refresh - keeps MQTT fallback ready
        """
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                # First update: HTTP setup to get device list
                if not self.initial_load:
                    self.initial_load = True
                    _LOGGER.info("Initial load: fetching device list from HTTP API")
                    await self.wybot_http_client.register_presence()
                    await self.http_refresh_data()

                    # Log device BLE names for debugging
                    for group_id, group in self.data.items():
                        if group.device:
                            _LOGGER.info(
                                "Device %s BLE name: %s, type: %s",
                                group.device.device_id,
                                group.device.ble_name,
                                group.device.device_type,
                            )
                        if group.docker:
                            _LOGGER.info(
                                "Docker %s BLE name: %s, type: %s",
                                group.docker.docker_id,
                                group.docker.ble_name,
                                group.docker.docker_type,
                            )
                    self._initial_query_start_time = time.time()

                # BLE FIRST - try all devices via BLE (primary)
                devices_needing_mqtt = await self._poll_all_devices_via_ble()

                # MQTT FALLBACK - only for devices that failed BLE
                if devices_needing_mqtt:
                    await self._poll_devices_via_mqtt(devices_needing_mqtt)

                # Keep HTTP session active for MQTT fallback readiness
                await self._maybe_refresh_http_session()

                # Update connection availability based on data sources
                if self.data:
                    self._connection_available = True
                    return self.data

                # No data from any source — surface as a failed update so the
                # DataUpdateCoordinator logs once now and once on recovery
                # (log-when-unavailable) and entities go unavailable.
                self._connection_available = False
                raise UpdateFailed("No data received from any source (BLE/MQTT/HTTP)")

        except (ConfigEntryAuthFailed, WybotAuthError) as err:
            # Credentials are no longer valid — trigger the reauth flow.
            self._connection_available = False
            if isinstance(err, ConfigEntryAuthFailed):
                raise
            raise ConfigEntryAuthFailed("Authentication failed") from err
        except ConfigEntryNotReady:
            self._connection_available = False
            raise
        except TimeoutError as err:
            _LOGGER.error("Timeout updating data: %s", err)
            self._connection_available = False
            raise UpdateFailed("Timeout communicating with API") from err
        except Exception as err:
            _LOGGER.error("Error communicating with API: %s", err)
            self._connection_available = False
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def http_refresh_data(self) -> None:
        """Refresh data from HTTP API with retry logic.

        Note: Does not subscribe to MQTT here since MQTT uses lazy connection.
        MQTT subscription happens in _ensure_mqtt_connected() when needed as fallback.
        """
        delay = INITIAL_RETRY_DELAY
        last_error = None

        for attempt in range(MAX_HTTP_RETRIES):
            try:
                data = await self.wybot_http_client.get_indexed_current_grouped_devices()
                if data:
                    self.data = data
                    # Note: MQTT subscription deferred to _ensure_mqtt_connected()
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
            except WybotAuthError as err:
                _LOGGER.error("Authentication failed, credentials may be invalid")
                raise ConfigEntryAuthFailed("Authentication failed") from err
            except Exception as err:
                last_error = err
                _LOGGER.warning(
                    "HTTP refresh failed (attempt %d/%d): %s",
                    attempt + 1,
                    MAX_HTTP_RETRIES,
                    err,
                )
                self._http_failure_count += 1

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

    async def subscribe_mqtt(self, data: dict[str, Group]) -> None:
        """Subscribe to MQTT updates for a device."""
        for [deviceId, device] in data.items():
            await self.wybot_mqtt_client.subscribe_for_device(device.device.device_id)
            if device.docker is not None:
                await self.wybot_mqtt_client.subscribe_for_device(
                    device.docker.docker_id
                )

    def on_message(self, topic: str, data: dict[str, Any]) -> None:
        """Handle a message from MQTT."""
        data_updated = False

        _LOGGER.debug(
            "MQTT on_message - Topic: %s, cmd: %s",
            topic,
            data.get("cmd") if isinstance(data, dict) else "N/A",
        )

        if topic.startswith("/will/"):
            deviceId = topic[6:]
            is_online = data.get("online") == "1"
            _LOGGER.debug(
                "Device %s online status: %s",
                deviceId,
                "online" if is_online else "offline",
            )
            # Record that we received MQTT data from this device
            self._record_mqtt_data_received(deviceId)
            # Update device online status
            group = self.get_group(deviceId)
            if group is not None:
                if group.device.device_id == deviceId:
                    group.device.online = is_online
                elif group.docker is not None and group.docker.docker_id == deviceId:
                    group.docker.online = is_online
                self.data[group.id] = group
                # Track online devices
                if is_online:
                    self._online_devices.add(deviceId)
                    _LOGGER.debug("Device %s came online, querying status", deviceId)
                    # on_message runs in the event loop (from the MQTT listen
                    # task) but is sync; schedule the async status query.
                    self.hass.async_create_task(
                        self.wybot_mqtt_client.ensure_device_sends_statuses(deviceId)
                    )
                else:
                    self._online_devices.discard(deviceId)
            # Will messages indicate device availability changes, always trigger update
            data_updated = True

        if topic.startswith("/device/DATA/send_transparent_data/"):
            deviceId = topic[35:]
            # Record that we received MQTT data from this device
            self._record_mqtt_data_received(deviceId)
            _LOGGER.debug(
                "Processing send_transparent_data for device %s",
                deviceId,
            )
            try:
                command_response = Command(**data)
                _LOGGER.debug(
                    "Parsed Command: cmd=%s, dp_count=%s",
                    command_response.cmd,
                    len(command_response.dp),
                )
            except Exception as err:
                _LOGGER.error("Failed to parse Command from data: %s, error: %s", data, err)
                command_response = None

            if command_response is None:
                return

            group = self.get_group(deviceId)
            incoming_dps: dict[str, Any] = command_response.get_dps_as_keyed_dict()
            if (
                group is not None
                and group.docker is not None
                and group.docker.docker_id == deviceId
            ):
                group.docker.dps = {
                    **group.docker.dps,
                    **incoming_dps,
                }
                _LOGGER.debug(
                    "Updated docker %s DPs from send_transparent_data",
                    deviceId,
                )
                data_updated = True
            elif group is not None and group.device is not None:
                group.device.dps = {
                    **group.device.dps,
                    **incoming_dps,
                }
                _LOGGER.debug(
                    "Updated device %s DPs from send_transparent_data",
                    deviceId,
                )
                data_updated = True
            if group is not None:
                self.data[group.id] = group

        if topic.startswith("/device/DATA/recv_transparent_query_data/"):
            deviceId = topic[41:]
            # Record that we received MQTT data from this device
            self._record_mqtt_data_received(deviceId)
            command_response = Command(**data)
            _LOGGER.debug("Query CMD ---- %s ----- %s", deviceId, command_response)

        if topic.startswith("/device/DATA/recv_transparent_cmd_data/"):
            deviceId = topic[39:]
            # Record that we received MQTT data from this device
            self._record_mqtt_data_received(deviceId)
            command_response = Command(**data)
            _LOGGER.debug("SEND CMD ---- %s ----- %s", deviceId, command_response)

            # Update device DPs with the received data (cmd=4 contains actual values)
            group = self.get_group(deviceId)
            if group is not None:
                cmd_dps: dict[str, Any] = command_response.get_dps_as_keyed_dict()
                if (
                    group.docker is not None
                    and group.docker.docker_id == deviceId
                ):
                    group.docker.dps = {
                        **group.docker.dps,
                        **cmd_dps,
                    }
                    _LOGGER.debug(
                        "Updated docker %s DPs from cmd_data",
                        deviceId,
                    )
                elif group.device is not None:
                    group.device.dps = {
                        **group.device.dps,
                        **cmd_dps,
                    }
                    _LOGGER.debug(
                        "Updated device %s DPs from cmd_data",
                        deviceId,
                    )
                self.data[group.id] = group
                data_updated = True

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

    async def send_write_command(self, group: Group, dp: GenericDP) -> None:
        """Send a command to a group. First send to the device, then send to the docker if it exists."""
        command = {"ts": int(time.time()), "cmd": 4, "dp": [dp.dict()]}
        await self.wybot_mqtt_client.send_write_command_for_device(
            group.device.device_id, command
        )
        if group.docker is not None:
            await self.wybot_mqtt_client.send_write_command_for_device(
                group.docker.docker_id, command
            )

    async def async_send_command(self, group: Group, dp: GenericDP) -> bool:
        """Send a command using BLE-first strategy with MQTT fallback.

        Attempts to send commands via BLE first (more reliable when WiFi is flaky).
        Falls back to MQTT if BLE fails or is disabled for this device.

        Args:
            group: The device group to send the command to
            dp: The GenericDP data point to send

        Returns:
            True if command was sent successfully via either BLE or MQTT
        """
        device_id = group.device.device_id
        ble_name = None

        # Prefer docker BLE name (dock has BLE, relays to robot)
        if group.docker and group.docker.ble_name:
            ble_name = group.docker.ble_name
        elif group.device and group.device.ble_name:
            ble_name = group.device.ble_name

        # Check if BLE should be recovered for this device
        self._maybe_recover_ble(device_id)

        # Check if BLE commands are enabled for this device
        ble_enabled = (
            self._ble_command_enabled
            and ble_name is not None
            and self._ble_command_failures.get(device_id, 0) < BLE_MAX_CONSECUTIVE_FAILURES
        )

        if ble_enabled:
            # ble_enabled is only True when ble_name is not None; narrow for typing.
            assert ble_name is not None
            _LOGGER.debug(
                "Attempting BLE-first command for device %s via %s (DP id=%d)",
                device_id,
                ble_name,
                dp.id,
            )
            try:
                ble_success, ble_dps = await self.wybot_ble_client.send_command(ble_name, dp)

                if ble_success:
                    _LOGGER.debug(
                        "BLE command succeeded for device %s",
                        device_id,
                    )
                    # Reset failure count on success
                    self._ble_command_failures[device_id] = 0

                    # Update state from BLE response if we got DPs
                    if ble_dps:
                        self._update_from_ble_dps(group, device_id, ble_dps)

                    return True

                # BLE failed, increment failure count
                failures = self._ble_command_failures.get(device_id, 0) + 1
                self._ble_command_failures[device_id] = failures
                _LOGGER.warning(
                    "BLE command failed for device %s (failure %d/%d), falling back to MQTT",
                    device_id,
                    failures,
                    BLE_MAX_CONSECUTIVE_FAILURES,
                )

                if failures >= BLE_MAX_CONSECUTIVE_FAILURES:
                    self._ble_disabled_at[device_id] = time.time()
                    _LOGGER.warning(
                        "BLE commands disabled for device %s after %d consecutive failures (will retry in %ds)",
                        device_id,
                        failures,
                        BLE_RECOVERY_SECONDS,
                    )

            except Exception as err:
                # BLE failed with exception, increment failure count
                failures = self._ble_command_failures.get(device_id, 0) + 1
                self._ble_command_failures[device_id] = failures
                _LOGGER.warning(
                    "BLE command exception for device %s: %s (failure %d/%d), falling back to MQTT",
                    device_id,
                    err,
                    failures,
                    BLE_MAX_CONSECUTIVE_FAILURES,
                )
                if failures >= BLE_MAX_CONSECUTIVE_FAILURES:
                    self._ble_disabled_at[device_id] = time.time()
        else:
            if ble_name is None:
                _LOGGER.debug(
                    "No BLE name available for device %s, using MQTT",
                    device_id,
                )
            elif not self._ble_command_enabled:
                _LOGGER.debug(
                    "BLE commands globally disabled, using MQTT for device %s",
                    device_id,
                )
            else:
                _LOGGER.debug(
                    "BLE commands disabled for device %s (too many failures), using MQTT",
                    device_id,
                )

        # Fallback to MQTT
        _LOGGER.debug(
            "Sending MQTT command for device %s (DP id=%d)",
            device_id,
            dp.id,
        )
        await self.send_write_command(group, dp)
        return True  # MQTT is fire-and-forget, assume success

    def reset_ble_command_failures(self, device_id: str | None = None) -> None:
        """Reset BLE command failure count for a device or all devices.

        Args:
            device_id: Specific device to reset, or None to reset all
        """
        if device_id:
            self._ble_command_failures.pop(device_id, None)
            _LOGGER.info("Reset BLE command failures for device %s", device_id)
        else:
            self._ble_command_failures.clear()
            _LOGGER.info("Reset BLE command failures for all devices")

    def _update_from_ble_dps(
        self, group: Group, device_id: str, dps: list[dict[str, Any]]
    ) -> None:
        """Update device state from BLE response DPs.

        Args:
            group: The device group to update
            device_id: The device ID
            dps: List of DP dicts from BLE response
        """
        if not dps:
            return

        try:
            from wybot.models import Command

            # Create a command-like structure to reuse existing DP processing
            cmd_data: dict[str, Any] = {"cmd": 5, "ts": 0, "dp": dps}
            command = Command(**cmd_data)

            dp_dict: dict[str, Any] = command.get_dps_as_keyed_dict()

            # Update the appropriate device/docker
            if group.docker and group.docker.docker_id == device_id:
                group.docker.dps = {**group.docker.dps, **dp_dict}
                _LOGGER.debug(
                    "Updated docker %s DPs from BLE response",
                    device_id,
                )
            elif group.device:
                group.device.dps = {**group.device.dps, **dp_dict}
                _LOGGER.debug(
                    "Updated device %s DPs from BLE response",
                    device_id,
                )

            # Record that we got data
            self._last_mqtt_data[device_id] = datetime.now(timezone.utc)

            # Trigger a state update
            self.async_set_updated_data(self.data)

        except Exception as err:
            _LOGGER.warning("Error updating state from BLE DPs: %s", err)

    async def query_all_device_status(self) -> None:
        """Query status for all devices by sending individual DP queries."""
        for device_id in self.data.keys():
            group = self.data[device_id]
            if self.wybot_mqtt_client.is_connected():
                await self.wybot_mqtt_client.ensure_device_sends_statuses(
                    group.device.device_id
                )
                if group.docker is not None:
                    await self.wybot_mqtt_client.ensure_device_sends_statuses(
                        group.docker.docker_id
                    )

    async def async_wake_devices_ble(self) -> dict[str, bool]:
        """Wake all offline devices via BLE.

        Returns:
            Dict mapping device BLE name to wake success status
        """
        if not self._ble_wake_enabled:
            _LOGGER.debug("BLE wake is disabled")
            return {}

        # Collect BLE names for all devices that are offline
        ble_names_to_wake = []
        for group_id, group in self.data.items():
            # Check if device is offline and has BLE name
            if group.device and group.device.ble_name:
                if not group.device.online:
                    ble_names_to_wake.append(group.device.ble_name)
            if group.docker and group.docker.ble_name:
                if not group.docker.online:
                    ble_names_to_wake.append(group.docker.ble_name)

        if not ble_names_to_wake:
            _LOGGER.debug("No offline devices to wake via BLE")
            return {}

        _LOGGER.info("Attempting to wake %d offline devices via BLE", len(ble_names_to_wake))
        results = await self.wybot_ble_client.wake_devices(ble_names_to_wake)

        # Log results
        for ble_name, success in results.items():
            if success:
                _LOGGER.info("Successfully woke device %s via BLE", ble_name)
            else:
                _LOGGER.warning("Failed to wake device %s via BLE", ble_name)

        return results

    async def async_wake_device_ble(self, ble_name: str) -> bool:
        """Wake a specific device via BLE.

        Args:
            ble_name: The BLE name of the device to wake

        Returns:
            True if wake was successful
        """
        if not self._ble_wake_enabled:
            _LOGGER.debug("BLE wake is disabled")
            return False

        _LOGGER.info("Attempting to wake device %s via BLE", ble_name)
        return await self.wybot_ble_client.wake_device(ble_name)

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

    def is_f1(self, idx: str) -> bool:
        """Return whether the group at ``idx`` is an F1 skimmer.

        Platforms use this to skip creating F1-only entities on DS20 robots,
        which would otherwise show up permanently unknown.
        """
        group = self.data.get(idx) if self.data else None
        if group is None or group.device is None:
            return False
        return group.device.device_type == F1_DEVICE_TYPE

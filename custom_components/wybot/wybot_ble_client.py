"""BLE client for waking up WyBot devices using Home Assistant Bluetooth."""

import asyncio
import logging
from typing import Any

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_discovered_service_info,
    async_scanner_count,
)
from homeassistant.core import HomeAssistant

from .const import BLE_COMMAND_HOLD_TIME, BLE_COMMAND_TIMEOUT, DOMAIN
from .wybot_dp_models import GenericDP

_LOGGER = logging.getLogger(__name__)

# =============================================================================
# BLE Service and Characteristic UUIDs
# =============================================================================
# DS20 Solar Dock actual UUIDs (discovered via BLE scan):
# - Service 000000ee has characteristic 0000ee01 (read/write/notify)
# - Service 000000ff has characteristic 0000ff01 (read/write/notify)
#
# APK K1 series UUIDs (from k1/AbstractC0300a.java) - different device model:
# - Service 00001000 with characteristics 00001001-00001005
# =============================================================================

# DS20 Solar Dock Service UUIDs (ACTUAL - verified via BLE scan)
SERVICE_UUID_EE = "000000ee-0000-1000-8000-00805f9b34fb"
SERVICE_UUID_FF = "000000ff-0000-1000-8000-00805f9b34fb"

# DS20 Solar Dock Characteristic UUIDs
CHAR_UUID_EE01 = "0000ee01-0000-1000-8000-00805f9b34fb"  # read/write/notify
CHAR_UUID_FF01 = "0000ff01-0000-1000-8000-00805f9b34fb"  # read/write/notify

# K1 series UUIDs (from APK - may be used by different models)
SERVICE_UUID_K1 = "00001000-0000-1000-8000-00805f9b34fb"
CHAR_UUID_1001 = "00001001-0000-1000-8000-00805f9b34fb"
CHAR_UUID_1002 = "00001002-0000-1000-8000-00805f9b34fb"  # Notifications/Data

# Notification descriptor UUID (CCCD)
CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"

# All known WyBot service UUIDs for matching
KNOWN_SERVICE_UUIDS = [
    SERVICE_UUID_EE.lower(),
    SERVICE_UUID_FF.lower(),
    SERVICE_UUID_K1.lower(),
]

# Connection timeout (APK uses 3000ms, we use longer for reliability)
CONNECTION_TIMEOUT = 15.0

# Wake hold time - how long to maintain connection for device to fully wake
WAKE_HOLD_TIME = 5.0

# WiFi configuration hold time - allow device to process WiFi config
WIFI_CONFIG_HOLD_TIME = 3.0

# =============================================================================
# Wake Commands - various patterns to try to wake the device
# =============================================================================
# Simple wake byte
WAKE_CMD_SIMPLE = bytes([0x01])

# Tuya-style header with query command (cmd=9)
WAKE_CMD_TUYA_QUERY = bytes([0x55, 0xAA, 0x00, 0x09, 0x00, 0x00, 0x09])

# Tuya-style header with status request (cmd=0)
WAKE_CMD_TUYA_STATUS = bytes([0x55, 0xAA, 0x00, 0x00, 0x00, 0x00, 0x00])

# Generic ping/wake patterns
WAKE_CMD_PING = bytes([0x00])
WAKE_CMD_FF = bytes([0xFF])

# =============================================================================
# Binary AA55 Format Commands (Verified working via PacketLogger capture Jan 2026)
# =============================================================================
# The WyBot app uses binary format sent to EE01 characteristic, NOT JSON!

# Pre-built binary commands
CMD_START_CLEANING = bytes.fromhex("aa5500040400000401030f")  # DP 0 = 03
CMD_STOP_CLEANING = bytes.fromhex("aa5500040400000401010d")  # DP 0 = 01
CMD_RETURN_TO_DOCK = bytes.fromhex("aa55000404000b04010118")  # DP 11 = 01

# Cleaning mode commands (DP 1)
CMD_MODE_FLOOR = bytes.fromhex("aa5500040400010401000d")  # Mode 00
CMD_MODE_WALL = bytes.fromhex("aa5500040400010401010e")  # Mode 01
CMD_MODE_WALL_THEN_FLOOR = bytes.fromhex("aa5500040400010401020f")  # Mode 02
CMD_MODE_ADVANCED_FULL = bytes.fromhex("aa55000404000104010310")  # Mode 03
CMD_MODE_WATER_LINE = bytes.fromhex("aa55000404000104010411")  # Mode 04
CMD_MODE_TURBO_FLOOR = bytes.fromhex("aa55000404000104010512")  # Mode 05
CMD_MODE_ECO_FLOOR = bytes.fromhex("aa55000404000104010613")  # Mode 06

# WiFi configuration commands
CMD_WIFI_INIT = bytes.fromhex("aa55002d020000002e")  # WiFi init/reset
CMD_WIFI_APPLY = bytes.fromhex("aa550003000002")  # Apply configuration

# Query command (binary format)
CMD_QUERY_STATUS = bytes.fromhex("aa5500090000000a")  # cmd=9, no payload


class WyBotBLEClient:
    """BLE client for waking up WyBot devices using HA Bluetooth integration.

    Based on APK analysis of WyBot app (k1/AbstractC0300a.java):
    - Device wakes when BLE connection is established (onConnectionStateChange state=2)
    - After wake, device auto-connects to MQTT broker
    - No special wake command byte needed - connection itself triggers wake
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the BLE client.

        Args:
            hass: Home Assistant instance for accessing Bluetooth integration
        """
        self._hass = hass
        self._wake_in_progress: bool = False
        self._last_notification_data: bytes | None = None
        self._last_status_data: bytes | None = None  # Status broadcasts (cmd=0x05)

    def _bluetooth_missing_warning_logged(self) -> bool:
        """Return whether the missing-Bluetooth warning has already been logged."""
        domain_data = self._hass.data.setdefault(DOMAIN, {})
        return bool(domain_data.get("_wybot_bluetooth_missing_warning_logged", False))

    def _mark_bluetooth_missing_warning_logged(self) -> None:
        """Remember that the missing-Bluetooth warning was already logged."""
        domain_data = self._hass.data.setdefault(DOMAIN, {})
        domain_data["_wybot_bluetooth_missing_warning_logged"] = True

    def _build_binary_command(
        self, cmd: int, dp_id: int, dp_type: int, dp_len: int, dp_value: bytes
    ) -> bytes:
        """Build binary AA55 format command for BLE.

        The WyBot app uses binary format sent to EE01 characteristic.
        Format: AA55 + cmd(2, big) + len(2, little) + dp_data + checksum(1)

        Args:
            cmd: Command type (4=write, 9=query)
            dp_id: Data point ID
            dp_type: Data point type (0=raw, 2=32-bit, 4=enum, 5=string)
            dp_len: Length of dp_value
            dp_value: The value bytes

        Returns:
            Complete binary command with checksum
        """
        dp_data = bytes([dp_id, dp_type, dp_len]) + dp_value
        header = b"\xaa\x55"
        cmd_bytes = cmd.to_bytes(2, "big")
        payload_len = len(dp_data).to_bytes(2, "little")  # Little endian per protocol
        packet = header + cmd_bytes + payload_len + dp_data
        checksum = (sum(packet[2:]) - 1) & 0xFF  # Subtract 1 per protocol
        return packet + bytes([checksum])

    def _build_binary_query(self, dp_id: int) -> bytes:
        """Build a binary query command for a single DP.

        Format: aa55 + 0009 + len_le + dp_id + checksum

        Args:
            dp_id: The DP ID to query (e.g., 1 for CleaningMode)

        Returns:
            Binary command bytes ready to send to EE01

        Example:
            _build_binary_query(1) -> aa5500090100010a (query CleaningMode)
        """
        header = b"\xaa\x55"
        cmd = b"\x00\x09"  # Query command
        length = (1).to_bytes(2, "little")  # 1 byte payload, little-endian
        payload = bytes([dp_id])
        packet = header + cmd + length + payload
        checksum = (sum(packet[2:]) - 1) & 0xFF  # Subtract 1 per protocol
        return packet + bytes([checksum])

    def _build_wifi_ssid_command(self, ssid: str) -> bytes:
        """Build WiFi SSID command (cmd=0x2A).

        Args:
            ssid: WiFi network name

        Returns:
            Binary command for sending SSID
        """
        ssid_bytes = ssid.encode("utf-8")
        # Pad SSID to fixed length (observed ~34 bytes in captures)
        padded_ssid = ssid_bytes.ljust(34, b"\x00")
        header = b"\xaa\x55"
        cmd_bytes = (0x2A).to_bytes(2, "big")
        payload_len = len(padded_ssid).to_bytes(2, "big")
        packet = header + cmd_bytes + payload_len + padded_ssid
        checksum = sum(packet[2:]) & 0xFF
        return packet + bytes([checksum])

    def _build_wifi_password_command(self, password: str) -> bytes:
        """Build WiFi password command (cmd=0x2B).

        Args:
            password: WiFi password

        Returns:
            Binary command for sending password
        """
        password_bytes = password.encode("utf-8")
        header = b"\xaa\x55"
        cmd_bytes = (0x2B).to_bytes(2, "big")
        payload_len = len(password_bytes).to_bytes(2, "big")
        packet = header + cmd_bytes + payload_len + password_bytes
        checksum = sum(packet[2:]) & 0xFF
        return packet + bytes([checksum])

    def _is_bluetooth_available(self) -> bool:
        """Check if Bluetooth is available (local or proxy)."""
        try:
            count = async_scanner_count(self._hass, connectable=True)
            _LOGGER.debug("Bluetooth scanner count: %d", count)
            return count > 0
        except Exception as err:
            _LOGGER.debug("Error checking Bluetooth availability: %s", err)
            return False

    def _notification_handler(self, sender: Any, data: bytearray) -> None:
        """Handle BLE notifications from device.

        Args:
            sender: The characteristic that sent the notification (BleakGATTCharacteristic or int)
            data: The notification data
        """
        data_bytes = bytes(data)
        self._last_notification_data = data_bytes

        # Store status broadcasts (cmd=0x05) separately so ACKs don't overwrite them
        if len(data_bytes) >= 4 and data_bytes[:2] == b"\xaa\x55" and data_bytes[3] == 0x05:
            self._last_status_data = data_bytes
            _LOGGER.info(
                "Received BLE STATUS broadcast (cmd=05): %s (%d bytes)",
                data_bytes.hex(),
                len(data_bytes),
            )
        else:
            # sender can be BleakGATTCharacteristic object or int depending on backend
            sender_str = str(getattr(sender, "uuid", sender))
            _LOGGER.info(
                "Received BLE notification from %s: %s (%d bytes)",
                sender_str,
                data_bytes.hex() if data_bytes else "empty",
                len(data_bytes) if data_bytes else 0,
            )

    async def _log_device_services(self, client: BleakClient) -> dict[str, Any]:
        """Log all services and characteristics for debugging.

        Args:
            client: Connected BleakClient

        Returns:
            Dict with service discovery information
        """
        discovery_info = {
            "services": [],
            "found_primary_service": False,
            "found_notification_char": False,
        }

        _LOGGER.info("=== BLE Service Discovery ===")
        for service in client.services:
            service_uuid = str(service.uuid).lower()
            is_known = service_uuid in KNOWN_SERVICE_UUIDS

            service_info = {
                "uuid": service_uuid,
                "is_wybot_service": is_known,
                "characteristics": [],
            }

            if is_known:
                _LOGGER.info("✓ Found WyBot service: %s", service.uuid)
                discovery_info["found_primary_service"] = True
            else:
                _LOGGER.debug("  Service: %s", service.uuid)

            for char in service.characteristics:
                char_uuid = str(char.uuid).lower()
                char_info = {
                    "uuid": char_uuid,
                    "properties": list(char.properties),
                }
                service_info["characteristics"].append(char_info)

                # Check for notification characteristic
                is_notify_char = "1002" in char_uuid or char_uuid == CHAR_UUID_1002.lower()
                if is_notify_char and "notify" in char.properties:
                    discovery_info["found_notification_char"] = True
                    _LOGGER.info(
                        "  ✓ Notification char: %s (properties: %s)",
                        char.uuid,
                        char.properties,
                    )
                elif is_known:
                    _LOGGER.info(
                        "    Char: %s (properties: %s)", char.uuid, char.properties
                    )
                else:
                    _LOGGER.debug(
                        "    Char: %s (properties: %s)", char.uuid, char.properties
                    )

            discovery_info["services"].append(service_info)

        _LOGGER.info("=== End Service Discovery ===")
        return discovery_info

    async def _enable_notifications(self, client: BleakClient) -> bool:
        """Enable notifications on the data characteristic.

        DS20 devices receive notifications on FF01 (verified via PacketLogger).
        K1 devices use 1002 characteristic.
        Note: EE01 is for writing commands only, not notifications.

        Args:
            client: Connected BleakClient

        Returns:
            True if notifications were enabled successfully
        """
        # Priority order for notification characteristics
        # FF01 is the correct notification characteristic for DS20 (verified)
        target_chars = [
            CHAR_UUID_FF01.lower(),  # DS20 notifications (verified correct)
            CHAR_UUID_1002.lower(),  # K1 series
        ]

        for service in client.services:
            service_uuid = str(service.uuid).lower()

            # Only check WyBot services
            if service_uuid not in KNOWN_SERVICE_UUIDS:
                continue

            for char in service.characteristics:
                char_uuid = str(char.uuid).lower()

                # Check if this is a target notification characteristic
                is_target = char_uuid in target_chars or (
                    service_uuid in KNOWN_SERVICE_UUIDS and "notify" in char.properties
                )

                if is_target and "notify" in char.properties:
                    try:
                        _LOGGER.info(
                            "Enabling notifications on %s (service: %s)",
                            char.uuid,
                            service.uuid,
                        )
                        await client.start_notify(char.uuid, self._notification_handler)
                        _LOGGER.info("✓ Notifications enabled successfully")
                        return True
                    except Exception as err:
                        _LOGGER.warning(
                            "Failed to enable notifications on %s: %s",
                            char.uuid,
                            err,
                        )

        _LOGGER.debug("No suitable notification characteristic found")
        return False

    async def _write_binary_command_to_ee01(
        self, client: BleakClient, command: bytes
    ) -> bool:
        """Write a binary command to the EE01 characteristic.

        The WyBot app only writes commands to EE01 (verified via PacketLogger).
        Commands written to FF01 are ignored.

        Args:
            client: Connected BleakClient
            command: Binary command bytes to write

        Returns:
            True if write succeeded
        """
        target_char = CHAR_UUID_EE01.lower()

        for service in client.services:
            service_uuid = str(service.uuid).lower()

            # Only write to WyBot EE service
            if service_uuid != SERVICE_UUID_EE.lower():
                continue

            for char in service.characteristics:
                char_uuid = str(char.uuid).lower()

                if char_uuid == target_char:
                    can_write = (
                        "write" in char.properties
                        or "write-without-response" in char.properties
                    )
                    if can_write:
                        use_response = "write" in char.properties
                        try:
                            _LOGGER.info(
                                "BLE write to EE01 (%d bytes): %s",
                                len(command),
                                command.hex(),
                            )
                            await client.write_gatt_char(
                                char.uuid,
                                command,
                                response=use_response,
                            )
                            _LOGGER.info("✓ BLE write to EE01 succeeded")
                            return True
                        except (BleakError, OSError) as err:
                            _LOGGER.warning("BLE write to EE01 failed: %s", err)
                            return False

        _LOGGER.warning("EE01 characteristic not found for writing")
        return False

    async def _write_wake_commands(self, client: BleakClient) -> bool:
        """Write wake commands to writable characteristics.

        Tries multiple wake command patterns on the EE01 characteristic
        to trigger the device to wake up and connect to MQTT.

        Args:
            client: Connected BleakClient

        Returns:
            True if at least one write succeeded
        """
        wake_commands = [
            ("simple 0x01", WAKE_CMD_SIMPLE),
            ("Tuya query", WAKE_CMD_TUYA_QUERY),
            ("Tuya status", WAKE_CMD_TUYA_STATUS),
            ("ping 0x00", WAKE_CMD_PING),
        ]

        # Only write to EE01 - verified as the correct write characteristic
        target_chars = [
            CHAR_UUID_EE01.lower(),  # DS20 write characteristic (verified)
        ]

        any_write_succeeded = False

        for service in client.services:
            service_uuid = str(service.uuid).lower()

            # Only write to WyBot services
            if service_uuid not in KNOWN_SERVICE_UUIDS:
                continue

            for char in service.characteristics:
                char_uuid = str(char.uuid).lower()

                # Check if this characteristic is writable
                can_write = "write" in char.properties or "write-without-response" in char.properties
                is_target = char_uuid in target_chars

                if is_target and can_write:
                    use_response = "write" in char.properties

                    for cmd_name, cmd_bytes in wake_commands:
                        try:
                            _LOGGER.info(
                                "Writing wake command '%s' (%s) to %s",
                                cmd_name,
                                cmd_bytes.hex(),
                                char.uuid,
                            )
                            await client.write_gatt_char(
                                char.uuid,
                                cmd_bytes,
                                response=use_response,
                            )
                            _LOGGER.info(
                                "✓ Wake command '%s' written successfully to %s",
                                cmd_name,
                                char.uuid,
                            )
                            any_write_succeeded = True

                            # Small delay between commands
                            await asyncio.sleep(0.5)

                        except Exception as err:
                            _LOGGER.debug(
                                "Failed to write '%s' to %s: %s",
                                cmd_name,
                                char.uuid,
                                err,
                            )

        if not any_write_succeeded:
            _LOGGER.warning("No wake commands could be written to any characteristic")

        return any_write_succeeded

    async def scan_for_device(self, ble_name: str) -> BLEDevice | None:
        """Scan for a WyBot device by its BLE name using HA Bluetooth.

        This uses Home Assistant's Bluetooth integration which supports:
        - Local Bluetooth adapters
        - ESPHome Bluetooth proxies
        - Shelly BLE proxies

        Args:
            ble_name: The BLE advertisement name (usually MAC address like CCBA97932A96)

        Returns:
            The BLEDevice if found, None otherwise
        """
        if not self._is_bluetooth_available():
            if not self._bluetooth_missing_warning_logged():
                _LOGGER.warning("No Bluetooth adapters or proxies available")
                self._mark_bluetooth_missing_warning_logged()
            return None

        _LOGGER.debug("Scanning for WyBot device with BLE name: %s", ble_name)

        # Get all discovered devices from HA Bluetooth
        try:
            service_infos = async_discovered_service_info(self._hass, connectable=True)
            _LOGGER.debug("Found %d BLE devices in scan", len(service_infos))

            for service_info in service_infos:
                device = service_info.device
                _LOGGER.debug(
                    "Checking BLE device: name=%s, address=%s",
                    device.name,
                    device.address,
                )

                # Check if device name matches (could be in name or address)
                if device.name and ble_name.upper() in device.name.upper():
                    _LOGGER.info(
                        "Found WyBot device: %s at %s", device.name, device.address
                    )
                    return device

                # Also check if ble_name matches the MAC address format
                # ble_name is like "CCBA97932A96", address is like "CC:BA:97:93:2A:96"
                clean_ble_name = ble_name.upper().replace(":", "")
                clean_address = device.address.upper().replace(":", "")
                if clean_ble_name in clean_address or clean_address in clean_ble_name:
                    _LOGGER.info(
                        "Found WyBot device by MAC: %s at %s",
                        device.name,
                        device.address,
                    )
                    return device

        except Exception as err:
            _LOGGER.warning("Error during BLE scan: %s", err)

        _LOGGER.debug("WyBot device with BLE name %s not found", ble_name)
        return None

    async def wake_device(self, ble_name: str) -> bool:
        """Wake up a WyBot device by establishing a BLE connection.

        Based on APK analysis: The device wakes when BLE connection is established.
        The onConnectionStateChange callback with state=2 (connected) triggers wake.
        After waking, the device automatically connects to the MQTT broker.

        Sequence (matching APK behavior):
        1. Scan for device by BLE name (or use MAC directly if scan fails)
        2. Connect to device (this triggers wake via state change)
        3. Discover services (validates connection)
        4. Enable notifications on 1002 characteristic (mimics app)
        5. Hold connection for device to fully wake
        6. Disconnect gracefully - device should now connect to MQTT

        Args:
            ble_name: The BLE advertisement name (usually MAC address like CCBA97932A96)

        Returns:
            True if wake was successful, False otherwise
        """
        if self._wake_in_progress:
            _LOGGER.debug("Wake already in progress, skipping")
            return False

        self._wake_in_progress = True
        self._last_notification_data = None

        try:
            # Step 1: Try to scan for the device first
            device = await self.scan_for_device(ble_name)

            # If scan didn't find it, try using the BLE name as MAC address directly
            # WyBot BLE names are typically MAC addresses without colons
            if not device:
                _LOGGER.info(
                    "Device %s not found in scan, trying direct MAC connection", ble_name
                )
                # Convert BLE name to MAC format (e.g., "3C8427565A1A" -> "3C:84:27:56:5A:1A")
                if len(ble_name) == 12 and all(c in '0123456789ABCDEFabcdef' for c in ble_name):
                    mac_address = ':'.join(ble_name[i:i+2] for i in range(0, 12, 2)).upper()
                    _LOGGER.info("Converted BLE name to MAC: %s", mac_address)
                else:
                    mac_address = ble_name

                # Try to get device from HA's bluetooth by address
                ble_device = async_ble_device_from_address(
                    self._hass, mac_address, connectable=True
                )
                if ble_device:
                    _LOGGER.info(
                        "Found device via direct MAC lookup: %s", mac_address
                    )
                    # Create a simple device-like object for the rest of the flow
                    class DirectDevice:
                        def __init__(self, addr):
                            self.address = addr
                            self.name = f"WyBot-{addr}"
                    device = DirectDevice(mac_address)
                else:
                    _LOGGER.warning(
                        "Could not find WyBot device %s via scan or direct MAC", ble_name
                    )
                    return False

            _LOGGER.info(
                "Found WyBot device, initiating wake connection to %s (%s)",
                getattr(device, 'name', 'Unknown'),
                device.address,
            )

            try:
                # Get BLE device through HA's bluetooth module (supports proxies)
                ble_device = async_ble_device_from_address(
                    self._hass, device.address, connectable=True
                )

                if not ble_device:
                    _LOGGER.warning(
                        "Could not get BLE device for address %s", device.address
                    )
                    return False

                # Step 2: Connect to device using bleak_retry_connector
                # This properly handles ESPHome proxy connections
                # According to APK, device wakes on onConnectionStateChange state=2
                _LOGGER.info("Establishing BLE connection via bleak_retry_connector...")
                client = await establish_connection(
                    BleakClient,
                    ble_device,
                    ble_device.name or ble_device.address,
                    max_attempts=3,
                )

                try:
                    if not client.is_connected:
                        _LOGGER.warning("Failed to establish BLE connection")
                        return False

                    _LOGGER.info(
                        "✓ Connected to WyBot device - device should be waking"
                    )

                    # Step 3: Discover and log services (for debugging)
                    discovery_info = await self._log_device_services(client)

                    if discovery_info["found_primary_service"]:
                        _LOGGER.info(
                            "✓ Found expected WyBot BLE service (00001000)"
                        )
                    else:
                        _LOGGER.warning(
                            "⚠ Primary WyBot service (00001000) not found - "
                            "device may use different UUIDs. Check logs above."
                        )

                    # Step 4: Enable notifications (mimics APK behavior)
                    notifications_enabled = await self._enable_notifications(client)
                    if notifications_enabled:
                        _LOGGER.info("✓ Notifications enabled on data characteristic")
                    else:
                        _LOGGER.debug(
                            "Could not enable notifications - wake may still work"
                        )

                    # Step 5: Write wake commands to characteristics
                    _LOGGER.info("Writing wake commands to trigger device...")
                    wake_written = await self._write_wake_commands(client)
                    if wake_written:
                        _LOGGER.info("✓ Wake commands written to characteristics")
                    else:
                        _LOGGER.warning(
                            "Could not write wake commands - connection alone may wake device"
                        )

                    # Step 6: Hold connection for device to fully wake
                    # APK uses 3000ms timeout, we use longer for reliability
                    _LOGGER.info(
                        "Holding connection for %.1fs to allow device to wake...",
                        WAKE_HOLD_TIME,
                    )
                    await asyncio.sleep(WAKE_HOLD_TIME)

                    # Check if we received any notification data
                    if self._last_notification_data:
                        _LOGGER.info(
                            "✓ Received notification data during wake: %s",
                            self._last_notification_data.hex(),
                        )

                    # Step 7: Disconnect gracefully
                    _LOGGER.info(
                        "✓ Wake sequence complete for %s - device should connect to MQTT",
                        ble_name,
                    )
                    return True

                finally:
                    # Always disconnect
                    if client.is_connected:
                        await client.disconnect()

            except asyncio.TimeoutError:
                # Timeout may still result in device wake - the BLE connection
                # ATTEMPT itself triggers wake (per APK analysis: onConnectionStateChange)
                _LOGGER.info(
                    "BLE connection timed out for %s - device may still wake from the attempt",
                    device.address,
                )
                # Return True since connection attempt was made (may have woken device)
                return True
            except BleakError as err:
                err_str = str(err)
                # Timeout errors during connection still trigger device wake
                if "timeout" in err_str.lower() or "Timeout" in err_str:
                    _LOGGER.info(
                        "BLE connection timeout for %s: %s - device may still wake",
                        device.address,
                        err,
                    )
                    # Return True since connection attempt was made
                    return True
                _LOGGER.warning("BLE connection error: %s", err)
                return False

        except Exception as err:
            _LOGGER.error("Unexpected error during BLE wake: %s", err)
            return False
        finally:
            self._wake_in_progress = False

    async def wake_devices(self, ble_names: list[str]) -> dict[str, bool]:
        """Wake up multiple WyBot devices.

        Args:
            ble_names: List of BLE names to wake

        Returns:
            Dict mapping BLE name to wake success status
        """
        results = {}
        for ble_name in ble_names:
            results[ble_name] = await self.wake_device(ble_name)
            # Small delay between wake attempts
            await asyncio.sleep(1.0)
        return results

    async def _write_wifi_config(
        self, client: BleakClient, ssid: str, password: str
    ) -> bool:
        """Write WiFi configuration using binary AA55 format to EE01.

        Sends the documented WiFi configuration sequence:
        1. WiFi init (cmd=0x2D)
        2. Send SSID (cmd=0x2A)
        3. Send password (cmd=0x2B)
        4. Apply config (cmd=0x03)

        Args:
            client: Connected BleakClient
            ssid: WiFi network name
            password: WiFi password

        Returns:
            True if all writes succeeded
        """
        _LOGGER.info("Sending WiFi configuration via binary protocol")

        # Step 1: WiFi init/reset
        _LOGGER.info("Step 1: WiFi init (cmd=0x2D)")
        if not await self._write_binary_command_to_ee01(client, CMD_WIFI_INIT):
            _LOGGER.warning("WiFi init command failed")
            return False
        await asyncio.sleep(0.5)

        # Step 2: Send SSID
        ssid_cmd = self._build_wifi_ssid_command(ssid)
        _LOGGER.info("Step 2: Send SSID (cmd=0x2A): %s", ssid)
        if not await self._write_binary_command_to_ee01(client, ssid_cmd):
            _LOGGER.warning("WiFi SSID command failed")
            return False
        await asyncio.sleep(0.5)

        # Step 3: Send password
        password_cmd = self._build_wifi_password_command(password)
        _LOGGER.info("Step 3: Send password (cmd=0x2B)")
        if not await self._write_binary_command_to_ee01(client, password_cmd):
            _LOGGER.warning("WiFi password command failed")
            return False
        await asyncio.sleep(0.5)

        # Step 4: Apply configuration
        _LOGGER.info("Step 4: Apply WiFi config (cmd=0x03)")
        if not await self._write_binary_command_to_ee01(client, CMD_WIFI_APPLY):
            _LOGGER.warning("WiFi apply command failed")
            return False

        _LOGGER.info("✓ WiFi configuration sequence complete")
        return True

    # Legacy method removed - was _build_wifi_config_payloads with guessed encodings
    # Now using documented binary AA55 format in _write_wifi_config

    async def configure_wifi(self, ble_name: str, ssid: str, password: str) -> bool:
        """Configure WiFi credentials on a WyBot device via BLE.

        This method connects to the device and sends WiFi configuration
        using the documented binary AA55 protocol.

        Args:
            ble_name: The BLE advertisement name (usually MAC address like CCBA97932A96)
            ssid: WiFi network name to configure
            password: WiFi password

        Returns:
            True if WiFi configuration was sent successfully, False otherwise
        """
        if self._wake_in_progress:
            _LOGGER.debug("Wake/config already in progress, skipping WiFi config")
            return False

        self._wake_in_progress = True
        self._last_notification_data = None

        try:
            _LOGGER.info(
                "Attempting WiFi configuration for device %s (SSID: %s)",
                ble_name,
                ssid,
            )

            # Step 1: Find the device
            device = await self.scan_for_device(ble_name)

            if not device:
                _LOGGER.info(
                    "Device %s not found in scan, trying direct MAC connection",
                    ble_name,
                )
                # Convert BLE name to MAC format
                if len(ble_name) == 12 and all(
                    c in "0123456789ABCDEFabcdef" for c in ble_name
                ):
                    mac_address = ":".join(
                        ble_name[i : i + 2] for i in range(0, 12, 2)
                    ).upper()
                    _LOGGER.info("Converted BLE name to MAC: %s", mac_address)
                else:
                    mac_address = ble_name

                ble_device = async_ble_device_from_address(
                    self._hass, mac_address, connectable=True
                )
                if ble_device:
                    _LOGGER.info("Found device via direct MAC lookup: %s", mac_address)

                    class DirectDevice:
                        def __init__(self, addr: str) -> None:
                            self.address = addr
                            self.name = f"WyBot-{addr}"

                    device = DirectDevice(mac_address)
                else:
                    _LOGGER.warning(
                        "Could not find WyBot device %s for WiFi configuration",
                        ble_name,
                    )
                    return False

            _LOGGER.info(
                "Found WyBot device, initiating WiFi configuration for %s (%s)",
                getattr(device, "name", "Unknown"),
                device.address,
            )

            try:
                # Get BLE device through HA's bluetooth module
                ble_device = async_ble_device_from_address(
                    self._hass, device.address, connectable=True
                )

                if not ble_device:
                    _LOGGER.warning(
                        "Could not get BLE device for address %s", device.address
                    )
                    return False

                # Step 2: Connect to device
                _LOGGER.info("Establishing BLE connection for WiFi configuration...")
                client = await establish_connection(
                    BleakClient,
                    ble_device,
                    ble_device.name or ble_device.address,
                    max_attempts=3,
                )

                try:
                    if not client.is_connected:
                        _LOGGER.warning("Failed to establish BLE connection")
                        return False

                    _LOGGER.info("✓ Connected to WyBot device for WiFi configuration")

                    # Step 3: Discover services
                    await self._log_device_services(client)

                    # Step 4: Enable notifications to receive any response
                    await self._enable_notifications(client)

                    # Step 5: Write WiFi configuration
                    _LOGGER.info("Sending WiFi configuration to device...")
                    config_written = await self._write_wifi_config(
                        client, ssid, password
                    )

                    if config_written:
                        _LOGGER.info("✓ WiFi configuration sent to device")
                    else:
                        _LOGGER.warning("Could not write WiFi configuration")

                    # Step 6: Hold connection to allow device to process
                    _LOGGER.info(
                        "Holding connection for %.1fs for device to process WiFi config...",
                        WIFI_CONFIG_HOLD_TIME,
                    )
                    await asyncio.sleep(WIFI_CONFIG_HOLD_TIME)

                    # Check for any notification response
                    if self._last_notification_data:
                        _LOGGER.info(
                            "Received notification after WiFi config: %s",
                            self._last_notification_data.hex(),
                        )

                    _LOGGER.info(
                        "✓ WiFi configuration complete for %s - device should reconnect",
                        ble_name,
                    )
                    return config_written

                finally:
                    if client.is_connected:
                        await client.disconnect()

            except TimeoutError:
                _LOGGER.warning(
                    "BLE connection timed out during WiFi configuration for %s",
                    ble_name,
                )
                return False
            except BleakError as err:
                _LOGGER.warning("BLE error during WiFi configuration: %s", err)
                return False

        except (BleakError, OSError) as err:
            _LOGGER.error("Unexpected error during WiFi configuration: %s", err)
            return False
        finally:
            self._wake_in_progress = False

    def _build_command_payload_binary(self, dp: GenericDP) -> bytes:
        """Build command payload using binary AA55 format.

        The WyBot app uses binary format for BLE commands (verified via PacketLogger).
        Format: AA55 + cmd(2) + len(2) + dp_data + checksum(1)

        Args:
            dp: The GenericDP data point to send

        Returns:
            Binary command payload
        """
        # Convert hex data string to bytes
        dp_value = bytes.fromhex(dp.data) if dp.data else b""
        return self._build_binary_command(
            cmd=4,  # Write command
            dp_id=dp.id,
            dp_type=dp.type,
            dp_len=dp.len,
            dp_value=dp_value,
        )

    async def send_command(self, ble_name: str, dp: GenericDP) -> tuple[bool, list[dict] | None]:
        """Send a command to a WyBot device via BLE.

        Uses binary AA55 format (verified working via PacketLogger capture).
        Commands are written to the EE01 characteristic.

        Args:
            ble_name: The BLE advertisement name (usually MAC address like CCBA97932A96)
            dp: The GenericDP data point to send

        Returns:
            Tuple of (success: bool, parsed_dps: list[dict] | None)
            - success: True if command was sent successfully
            - parsed_dps: List of DP dicts from the response if available
        """
        if self._wake_in_progress:
            _LOGGER.debug("Wake/command already in progress, skipping BLE command")
            return (False, None)

        self._wake_in_progress = True
        self._last_notification_data = None

        try:
            _LOGGER.info(
                "BLE command: device=%s, DP id=%d, type=%d, len=%d, data=%s",
                ble_name,
                dp.id,
                dp.type,
                dp.len,
                dp.data,
            )

            # Step 1: Find the device
            device = await self.scan_for_device(ble_name)

            if not device:
                _LOGGER.info(
                    "Device %s not found in scan, trying direct MAC connection",
                    ble_name,
                )
                # Convert BLE name to MAC format
                if len(ble_name) == 12 and all(
                    c in "0123456789ABCDEFabcdef" for c in ble_name
                ):
                    mac_address = ":".join(
                        ble_name[i : i + 2] for i in range(0, 12, 2)
                    ).upper()
                else:
                    mac_address = ble_name

                ble_device = async_ble_device_from_address(
                    self._hass, mac_address, connectable=True
                )
                if ble_device:
                    _LOGGER.info("Found device via MAC: %s", mac_address)

                    class DirectDevice:
                        def __init__(self, addr: str) -> None:
                            self.address = addr
                            self.name = f"WyBot-{addr}"

                    device = DirectDevice(mac_address)
                else:
                    _LOGGER.warning("Device %s not found for BLE command", ble_name)
                    return (False, None)

            _LOGGER.info("Connecting to %s for BLE command", device.address)

            try:
                # Get BLE device through HA's bluetooth module
                ble_device = async_ble_device_from_address(
                    self._hass, device.address, connectable=True
                )

                if not ble_device:
                    _LOGGER.warning("Could not get BLE device for %s", device.address)
                    return (False, None)

                # Step 2: Connect to device with timeout
                async with asyncio.timeout(BLE_COMMAND_TIMEOUT):
                    client = await establish_connection(
                        BleakClient,
                        ble_device,
                        ble_device.name or ble_device.address,
                        max_attempts=2,
                    )

                    try:
                        if not client.is_connected:
                            _LOGGER.warning("BLE connection failed")
                            return (False, None)

                        _LOGGER.info("✓ BLE connected to %s", device.address)

                        # Step 3: Enable notifications to receive response
                        await self._enable_notifications(client)

                        # Step 4: Build and write command (binary AA55 format to EE01)
                        payload = self._build_command_payload_binary(dp)
                        command_written = await self._write_binary_command_to_ee01(
                            client, payload
                        )

                        if not command_written:
                            _LOGGER.warning("BLE command write failed")
                            return (False, None)

                        # Step 5: Wait briefly for response/acknowledgment
                        await asyncio.sleep(BLE_COMMAND_HOLD_TIME)

                        # Parse response if available
                        parsed_dps = None
                        if self._last_notification_data:
                            _LOGGER.info(
                                "BLE response: %s",
                                self._last_notification_data.hex(),
                            )
                            parsed_dps = self._parse_ble_response(self._last_notification_data)
                            if parsed_dps:
                                _LOGGER.info("Parsed %d DPs from BLE response", len(parsed_dps))

                        _LOGGER.info("✓ BLE command complete for %s", ble_name)
                        return (True, parsed_dps)

                    finally:
                        if client.is_connected:
                            await client.disconnect()

            except TimeoutError:
                _LOGGER.warning(
                    "BLE command timed out for %s (%.1fs)",
                    ble_name,
                    BLE_COMMAND_TIMEOUT,
                )
                return (False, None)
            except BleakError as err:
                _LOGGER.warning("BLE error: %s", err)
                return (False, None)

        except (BleakError, OSError) as err:
            _LOGGER.error("BLE command error: %s", err)
            return (False, None)
        finally:
            self._wake_in_progress = False

    def _is_status_response(self, data: bytes) -> bool:
        """Check if BLE response is a status broadcast (cmd=0x05).

        Args:
            data: Raw BLE response bytes

        Returns:
            True if this is a status response with DP data
        """
        if len(data) < 6:
            return False
        # Check for aa55 header and cmd=0x05 (status)
        return data[:2] == b"\xaa\x55" and data[3] == 0x05

    def _parse_ble_response(self, data: bytes) -> list[dict]:
        """Parse BLE response containing DP data.

        WyBot BLE response format (verified via live testing):
        - Header: aa55 (2 bytes)
        - Version: 00 (1 byte)
        - Cmd: 05 = status, 12 = device info, 1c = ack (1 byte)
        - Length: 2 bytes (big-endian, payload length)
        - DP data in format: [dp_id 1 byte][type 1 byte][len 1 byte][data...]
        - Checksum (last byte)

        Args:
            data: Raw BLE response bytes

        Returns:
            List of DP dicts: [{"id": int, "type": int, "len": int, "data": str}, ...]
        """
        dps = []

        if len(data) < 7:
            return dps

        try:
            # Check for WyBot header (aa55)
            if data[:2] == b'\xaa\x55':
                version = data[2]
                cmd = data[3]
                payload_len = (data[4] << 8) | data[5]
                offset = 6  # Start of DP data
                end_offset = min(6 + payload_len, len(data) - 1)  # Exclude checksum

                _LOGGER.debug(
                    "BLE response: header=aa55, version=%02x, cmd=%02x, payload_len=%d",
                    version, cmd, payload_len
                )

                # Only parse DP data for status responses (cmd=0x05)
                if cmd != 0x05:
                    _LOGGER.debug("Non-status response (cmd=%02x), skipping DP parse", cmd)
                    return dps

            elif data[:2] == b'\x55\xaa':
                # Standard Tuya format: 55aa + version + cmd + 2 len bytes + data
                offset = 6
                end_offset = len(data) - 1
                _LOGGER.debug("BLE response: header=55aa (Tuya format)")
            else:
                # Try parsing as raw DP data
                offset = 0
                end_offset = len(data)
                _LOGGER.debug("BLE response: no header, trying raw DP parse")

            # Parse DP data: [dp_id][type][len][data...]
            # All types use 1-byte length
            dp_count = 0
            while offset < end_offset - 2:
                dp_id = data[offset]
                dp_type = data[offset + 1]
                dp_len = data[offset + 2]
                dp_data_start = offset + 3

                if dp_data_start + dp_len > end_offset:
                    _LOGGER.debug(
                        "DP parse: not enough data for DP %d (need %d, have %d)",
                        dp_id, dp_len, end_offset - dp_data_start
                    )
                    break

                dp_data = data[dp_data_start:dp_data_start + dp_len].hex()
                dp_count += 1

                dps.append({
                    "id": dp_id,
                    "type": dp_type,
                    "len": dp_len,
                    "data": dp_data,
                })

                _LOGGER.info(
                    "Parsed DP %d from BLE: id=%d, type=%d, len=%d, data=%s",
                    dp_count, dp_id, dp_type, dp_len, dp_data
                )

                offset = dp_data_start + dp_len

            if dp_count > 0:
                _LOGGER.info("Successfully parsed %d DPs from BLE response", dp_count)

        except (IndexError, ValueError) as err:
            _LOGGER.warning("Error parsing BLE response: %s", err)

        return dps

    async def query_status(self, ble_name: str) -> list[dict] | None:
        """Query device status via BLE.

        Sends a query command and parses the response to get current DP values.

        Args:
            ble_name: The BLE advertisement name

        Returns:
            List of DP dicts if successful, None if failed
        """
        if self._wake_in_progress:
            _LOGGER.debug("BLE operation in progress, skipping status query")
            return None

        self._wake_in_progress = True
        self._last_notification_data = None

        try:
            _LOGGER.info("BLE status query for device %s", ble_name)

            # Find the device
            device = await self.scan_for_device(ble_name)
            if not device:
                # Try direct MAC
                if len(ble_name) == 12 and all(c in "0123456789ABCDEFabcdef" for c in ble_name):
                    mac_address = ":".join(ble_name[i:i+2] for i in range(0, 12, 2)).upper()
                else:
                    mac_address = ble_name

                ble_device = async_ble_device_from_address(self._hass, mac_address, connectable=True)
                if not ble_device:
                    if self._is_bluetooth_available():
                        _LOGGER.warning("Device %s not found for status query", ble_name)
                    return None

                class DirectDevice:
                    def __init__(self, addr: str) -> None:
                        self.address = addr
                        self.name = f"WyBot-{addr}"
                device = DirectDevice(mac_address)

            try:
                ble_device = async_ble_device_from_address(self._hass, device.address, connectable=True)
                if not ble_device:
                    return None

                async with asyncio.timeout(BLE_COMMAND_TIMEOUT):
                    client = await establish_connection(
                        BleakClient,
                        ble_device,
                        ble_device.name or ble_device.address,
                        max_attempts=2,
                    )

                    try:
                        if not client.is_connected:
                            return None

                        _LOGGER.info("✓ BLE connected for status query")

                        # Clear previous status data
                        self._last_status_data = None

                        # Enable notifications
                        await self._enable_notifications(client)

                        # Send query command (binary AA55 format to EE01)
                        await self._write_binary_command_to_ee01(
                            client, CMD_QUERY_STATUS
                        )

                        # Wait for status broadcast (cmd=0x05)
                        # The notification handler stores cmd=0x05 in _last_status_data
                        # so ACKs (cmd=0x1c) don't overwrite it
                        dps = []
                        max_wait_time = 5.0  # Max seconds to wait for status
                        poll_interval = 0.3  # Check every 300ms
                        elapsed = 0.0

                        while elapsed < max_wait_time:
                            await asyncio.sleep(poll_interval)
                            elapsed += poll_interval

                            if self._last_status_data:
                                _LOGGER.info(
                                    "Processing status broadcast: %s",
                                    self._last_status_data.hex()
                                )
                                dps = self._parse_ble_response(self._last_status_data)
                                if dps:
                                    _LOGGER.info(
                                        "Parsed %d DPs from status broadcast", len(dps)
                                    )
                                    break

                        if not dps:
                            _LOGGER.warning(
                                "No status broadcast received after %.1fs", max_wait_time
                            )

                        # Query CleaningMode (DP 1) separately - not included in general status
                        if dps:
                            await asyncio.sleep(0.5)  # Brief delay between queries
                            self._last_status_data = None  # Clear for next query

                            cleaning_mode_query = self._build_binary_query(1)
                            _LOGGER.debug(
                                "Querying CleaningMode (DP 1): %s",
                                cleaning_mode_query.hex()
                            )
                            await self._write_binary_command_to_ee01(
                                client, cleaning_mode_query
                            )

                            # Wait for CleaningMode status response
                            elapsed = 0.0
                            while elapsed < max_wait_time:
                                await asyncio.sleep(poll_interval)
                                elapsed += poll_interval

                                if self._last_status_data:
                                    cleaning_mode_dps = self._parse_ble_response(
                                        self._last_status_data
                                    )
                                    if cleaning_mode_dps:
                                        dps.extend(cleaning_mode_dps)
                                        _LOGGER.info(
                                            "Added CleaningMode from separate query: %s",
                                            cleaning_mode_dps
                                        )
                                        break

                        return dps if dps else None

                    finally:
                        if client.is_connected:
                            await client.disconnect()

            except TimeoutError:
                _LOGGER.warning("BLE status query timed out")
                return None
            except BleakError as err:
                _LOGGER.warning("BLE error during status query: %s", err)
                return None

        except (BleakError, OSError) as err:
            _LOGGER.error("BLE status query error: %s", err)
            return None
        finally:
            self._wake_in_progress = False

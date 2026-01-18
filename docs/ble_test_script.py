#!/usr/bin/env python3
"""BLE test script for WyBot devices.

This script can be run standalone to test BLE communication with WyBot devices.
It discovers services, captures notifications, and tests commands.

Usage:
    python ble_test_script.py scan                    # Scan for WyBot devices
    python ble_test_script.py discover <MAC>          # Discover services
    python ble_test_script.py wake <MAC>              # Wake device via BLE
    python ble_test_script.py query <MAC>             # Query device status
    python ble_test_script.py monitor <MAC> [seconds] # Monitor notifications
    python ble_test_script.py start <MAC>             # Start cleaning (JSON format - may not work when docked)
    python ble_test_script.py start <MAC> --mode 1    # Start cleaning with mode (0=Floor, 1=Wall, etc)
    python ble_test_script.py stop <MAC>              # Stop cleaning (JSON format)
    python ble_test_script.py undock <MAC>            # Undock/return from dock
    python ble_test_script.py full-clean <MAC>        # Full sequence: undock, wait, start cleaning
    python ble_test_script.py start-binary <MAC>      # Start cleaning (binary format to EE01 - WORKS!)
    python ble_test_script.py stop-binary <MAC>       # Stop cleaning (binary format to EE01)
    python ble_test_script.py return-binary <MAC>     # Return to dock (binary format to EE01)
    python ble_test_script.py set-mode <MAC> <0-6>    # Set cleaning mode (0=Floor...6=Eco)
"""

import argparse
import asyncio
import json
import logging
import sys
import time

try:
    from bleak import BleakClient, BleakScanner
    from bleak.backends.device import BLEDevice
except ImportError:
    print("Error: bleak library not installed. Run: pip install bleak")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_LOGGER = logging.getLogger(__name__)

# =============================================================================
# BLE UUIDs (from protocol documentation)
# =============================================================================

# DS20 Solar Dock
SERVICE_UUID_EE = "000000ee-0000-1000-8000-00805f9b34fb"
SERVICE_UUID_FF = "000000ff-0000-1000-8000-00805f9b34fb"
CHAR_UUID_EE01 = "0000ee01-0000-1000-8000-00805f9b34fb"
CHAR_UUID_FF01 = "0000ff01-0000-1000-8000-00805f9b34fb"

# K1/S1/S2 Series
SERVICE_UUID_K1 = "00001000-0000-1000-8000-00805f9b34fb"
CHAR_UUID_1001 = "00001001-0000-1000-8000-00805f9b34fb"
CHAR_UUID_1002 = "00001002-0000-1000-8000-00805f9b34fb"

KNOWN_SERVICE_UUIDS = [
    SERVICE_UUID_EE.lower(),
    SERVICE_UUID_FF.lower(),
    SERVICE_UUID_K1.lower(),
]

# Connection settings
CONNECTION_TIMEOUT = 15.0
WAKE_HOLD_TIME = 5.0


def build_binary_command(cmd: int, dp_id: int, dp_type: int, dp_len: int, dp_value: bytes) -> bytes:
    """Build a binary AA55 format command for BLE.

    The WyBot app uses binary format, not JSON, for BLE commands.
    Format: AA55 + cmd(2) + len(2) + DP data + checksum

    Args:
        cmd: Command type (4 = write, 9 = query)
        dp_id: Data Point ID
        dp_type: Data Point type (4 = enum)
        dp_len: Length of dp_value
        dp_value: The value bytes

    Returns:
        Complete binary command with header and checksum
    """
    # Build DP data: id + type + len + value
    dp_data = bytes([dp_id, dp_type, dp_len]) + dp_value

    # Build packet: header + cmd + payload_len + dp_data
    header = b'\xaa\x55'
    cmd_bytes = cmd.to_bytes(2, 'big')
    payload_len = len(dp_data).to_bytes(2, 'big')

    packet = header + cmd_bytes + payload_len + dp_data

    # Calculate checksum (sum of all bytes after header, mod 256)
    checksum = sum(packet[2:]) & 0xFF

    return packet + bytes([checksum])


# Pre-built binary commands (captured from WyBot app via PacketLogger)
# IMPORTANT: Must be sent to EE01 characteristic, NOT FF01!
BINARY_CMD_START_CLEANING = bytes.fromhex('aa5500040400000401030f')  # DP 0 = 03 → Status: Cleaning
BINARY_CMD_STOP_CLEANING = bytes.fromhex('aa5500040400000401010d')   # DP 0 = 01 → Status: Stopped
BINARY_CMD_RETURN_TO_DOCK = bytes.fromhex('aa55000404000b04010118')  # DP 11 = 01 → Status: 04 (Returning)

# Cleaning mode commands (DP 1) - captured from WyBot app
BINARY_CMD_MODE_FLOOR = bytes.fromhex('aa5500040400010401000d')           # Mode 0: Floor
BINARY_CMD_MODE_WALL = bytes.fromhex('aa5500040400010401010e')            # Mode 1: Wall
BINARY_CMD_MODE_WALL_THEN_FLOOR = bytes.fromhex('aa5500040400010401020f') # Mode 2: Wall Then Floor
BINARY_CMD_MODE_ADVANCED = bytes.fromhex('aa55000404000104010310')        # Mode 3: Advanced Full Pool
BINARY_CMD_MODE_WATERLINE = bytes.fromhex('aa55000404000104010411')       # Mode 4: Water Line
BINARY_CMD_MODE_TURBO = bytes.fromhex('aa55000404000104010512')           # Mode 5: Turbo Floor
BINARY_CMD_MODE_ECO = bytes.fromhex('aa55000404000104010613')             # Mode 6: Eco Floor

BINARY_MODE_COMMANDS = {
    0: BINARY_CMD_MODE_FLOOR,
    1: BINARY_CMD_MODE_WALL,
    2: BINARY_CMD_MODE_WALL_THEN_FLOOR,
    3: BINARY_CMD_MODE_ADVANCED,
    4: BINARY_CMD_MODE_WATERLINE,
    5: BINARY_CMD_MODE_TURBO,
    6: BINARY_CMD_MODE_ECO,
}

MODE_NAMES = {
    0: "Floor",
    1: "Wall",
    2: "Wall Then Floor",
    3: "Advanced Full Pool",
    4: "Water Line",
    5: "Turbo Floor",
    6: "Eco Floor",
}

# The correct characteristic for WRITE commands on DS20 Solar Dock
# - EE01 (0000ee01-...) = Write commands (handle 0x002e)
# - FF01 (0000ff01-...) = Notifications/status updates
CHAR_UUID_EE01_WRITE = "0000ee01-0000-1000-8000-00805f9b34fb"
CHAR_UUID_FF01_NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"


def ble_name_to_mac(ble_name: str) -> str:
    """Convert BLE name (CCBA97932A96) to MAC (CC:BA:97:93:2A:96)."""
    if len(ble_name) == 12 and all(c in "0123456789ABCDEFabcdef" for c in ble_name):
        return ":".join(ble_name[i : i + 2] for i in range(0, 12, 2)).upper()
    return ble_name


def is_wybot_device(device: BLEDevice) -> bool:
    """Check if a device is likely a WyBot device."""
    if not device.name:
        return False
    name = device.name.upper()
    # WyBot devices advertise with MAC address as name (12 hex chars)
    if len(name) == 12 and all(c in "0123456789ABCDEF" for c in name):
        return True
    # DS20 Solar Dock format: "DS20-XXXXXXXXXXXX"
    if name.startswith("DS20-"):
        return True
    # Also check for "WyBot" prefix
    if "WYBOT" in name:
        return True
    return False


class BLETestClient:
    """BLE test client for WyBot devices."""

    def __init__(self):
        self.notifications: list[dict] = []
        self.notification_event = asyncio.Event()

    def _notification_handler(self, sender, data: bytearray) -> None:
        """Handle BLE notifications."""
        notification = {
            "time": time.time(),
            "sender": str(sender),
            "data_hex": data.hex(),
            "data_raw": bytes(data),
        }
        self.notifications.append(notification)
        self.notification_event.set()

        _LOGGER.info(
            "NOTIFICATION from %s: %s (%d bytes)",
            sender,
            data.hex(),
            len(data),
        )

        # Try to parse as JSON
        try:
            json_data = json.loads(data.decode("utf-8"))
            _LOGGER.info("  Parsed JSON: %s", json.dumps(json_data, indent=2))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Try to parse as binary DP data
            self._parse_binary_response(data)

    def _parse_binary_response(self, data: bytes) -> None:
        """Parse binary DP response."""
        if len(data) < 3:
            return

        # Check for headers
        if data[:2] == b"\xaa\x55":
            _LOGGER.info("  Header: AA55 (WyBot format)")
            if len(data) > 5:
                payload_len = (data[4] << 8) | data[5]
                _LOGGER.info("  Payload length: %d", payload_len)
                self._parse_dp_data(data[6:])
        elif data[:2] == b"\x55\xaa":
            _LOGGER.info("  Header: 55AA (Tuya format)")
            self._parse_dp_data(data[6:])
        else:
            _LOGGER.info("  No header, trying raw DP parse")
            self._parse_dp_data(data)

    def _parse_dp_data(self, data: bytes) -> None:
        """Parse DP data entries."""
        offset = 0
        dp_count = 0

        while offset < len(data) - 2:
            dp_id = data[offset]
            dp_type = data[offset + 1]
            dp_len = data[offset + 2]
            dp_data_start = offset + 3

            if dp_data_start + dp_len > len(data):
                break

            dp_data = data[dp_data_start : dp_data_start + dp_len].hex()
            dp_count += 1

            _LOGGER.info(
                "  DP[%d]: id=%d, type=%d, len=%d, data=%s",
                dp_count,
                dp_id,
                dp_type,
                dp_len,
                dp_data,
            )

            # Interpret known DPs
            self._interpret_dp(dp_id, dp_type, dp_len, dp_data)

            offset = dp_data_start + dp_len

    def _interpret_dp(self, dp_id: int, dp_type: int, dp_len: int, dp_data: str) -> None:
        """Interpret known DP values."""
        interpretations = {
            0: self._interpret_cleaning_status,
            1: self._interpret_cleaning_mode,
            11: self._interpret_dock,
            50: self._interpret_battery,
            131: self._interpret_solar_energy,
            213: self._interpret_dock_connection_status,
            214: self._interpret_dock_info,
            221: self._interpret_solar_battery,
            222: self._interpret_solar_status,
        }

        if dp_id in interpretations:
            try:
                interpretations[dp_id](dp_data)
            except Exception as e:
                _LOGGER.debug("    Error interpreting DP %d: %s", dp_id, e)

    def _interpret_cleaning_status(self, data: str) -> None:
        statuses = {"01": "Stopped", "02": "Returning", "03": "Cleaning", "04": "Returning to Dock"}
        status = statuses.get(data, f"Unknown ({data})")
        _LOGGER.info("    -> CleaningStatus: %s", status)

    def _interpret_cleaning_mode(self, data: str) -> None:
        modes = ["Floor", "Wall", "Wall Then Floor", "Advanced Full Pool", "Water Line", "Turbo Floor", "Eco Floor"]
        try:
            mode_idx = int(data, 16)
            mode = modes[mode_idx] if mode_idx < len(modes) else f"Unknown ({mode_idx})"
            _LOGGER.info("    -> CleaningMode: %s", mode)
        except (ValueError, IndexError):
            pass

    def _interpret_dock(self, data: str) -> None:
        _LOGGER.info("    -> Dock: %s", data)

    def _interpret_dock_connection_status(self, data: str) -> None:
        statuses = {"00": "Undocked", "01": "Docked"}
        status = statuses.get(data, f"Unknown ({data})")
        _LOGGER.info("    -> DockConnectionStatus: %s", status)

    def _interpret_battery(self, data: str) -> None:
        if len(data) >= 4:
            charge_state = int(data[:2], 16)
            battery_level = int(data[2:4], 16)
            states = {0: "Not plugged", 1: "Charging", 2: "Charged"}
            state_str = states.get(charge_state, f"Unknown ({charge_state})")
            _LOGGER.info("    -> Battery: %d%%, State: %s", battery_level, state_str)

    def _interpret_solar_energy(self, data: str) -> None:
        if len(data) >= 8:
            energy_wh = int.from_bytes(bytes.fromhex(data), byteorder="little")
            _LOGGER.info("    -> SolarEnergy: %d Wh (%.2f kWh)", energy_wh, energy_wh / 1000)

    def _interpret_dock_info(self, data: str) -> None:
        dock_types = {"01": "Standard", "05": "Solar"}
        dock_type = dock_types.get(data, f"Unknown ({data})")
        _LOGGER.info("    -> DockType: %s", dock_type)

    def _interpret_solar_battery(self, data: str) -> None:
        if len(data) >= 4:
            battery_pct = int(data, 16)
            _LOGGER.info("    -> SolarDockBattery: %d%%", battery_pct)

    def _interpret_solar_status(self, data: str) -> None:
        is_charging = int(data, 16) == 1
        _LOGGER.info("    -> SolarCharging: %s", "Yes" if is_charging else "No")


async def scan_devices() -> list[BLEDevice]:
    """Scan for WyBot devices."""
    _LOGGER.info("Scanning for BLE devices (10 seconds)...")

    devices = await BleakScanner.discover(timeout=10.0)
    wybot_devices = []

    _LOGGER.info("\n=== All Discovered Devices ===")
    for device in devices:
        is_wybot = is_wybot_device(device)
        marker = " [WYBOT]" if is_wybot else ""
        _LOGGER.info("  %s (%s)%s", device.name or "Unknown", device.address, marker)
        if is_wybot:
            wybot_devices.append(device)

    _LOGGER.info("\n=== WyBot Devices Found: %d ===", len(wybot_devices))
    for device in wybot_devices:
        _LOGGER.info("  Name: %s", device.name)
        _LOGGER.info("  Address: %s", device.address)
        _LOGGER.info("")

    return wybot_devices


async def discover_services(address: str) -> dict:
    """Discover and print all services/characteristics for a device."""
    address = ble_name_to_mac(address)
    _LOGGER.info("Connecting to %s for service discovery...", address)

    discovery_info = {
        "address": address,
        "services": [],
        "wybot_services_found": [],
    }

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected successfully!")
            _LOGGER.info("\n=== Service Discovery ===")

            for service in client.services:
                service_uuid = str(service.uuid).lower()
                is_wybot = service_uuid in KNOWN_SERVICE_UUIDS

                service_info = {
                    "uuid": service_uuid,
                    "is_wybot": is_wybot,
                    "characteristics": [],
                }

                marker = " [WYBOT]" if is_wybot else ""
                _LOGGER.info("\nService: %s%s", service.uuid, marker)

                if is_wybot:
                    discovery_info["wybot_services_found"].append(service_uuid)

                for char in service.characteristics:
                    char_info = {
                        "uuid": str(char.uuid),
                        "properties": list(char.properties),
                        "descriptors": [str(d.uuid) for d in char.descriptors],
                    }
                    service_info["characteristics"].append(char_info)

                    _LOGGER.info("  Characteristic: %s", char.uuid)
                    _LOGGER.info("    Properties: %s", ", ".join(char.properties))

                    for desc in char.descriptors:
                        _LOGGER.info("    Descriptor: %s", desc.uuid)

                discovery_info["services"].append(service_info)

            _LOGGER.info("\n=== Summary ===")
            _LOGGER.info("Total services: %d", len(discovery_info["services"]))
            _LOGGER.info("WyBot services found: %s", discovery_info["wybot_services_found"])

    except Exception as e:
        _LOGGER.error("Connection failed: %s", e)
        discovery_info["error"] = str(e)

    return discovery_info


async def wake_device(address: str) -> bool:
    """Wake a WyBot device via BLE connection."""
    address = ble_name_to_mac(address)
    _LOGGER.info("Attempting to wake device at %s...", address)

    test_client = BLETestClient()

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected! Device should be waking...")

            # Find notification characteristic
            notify_char = None
            for service in client.services:
                service_uuid = str(service.uuid).lower()
                if service_uuid in KNOWN_SERVICE_UUIDS:
                    for char in service.characteristics:
                        if "notify" in char.properties:
                            notify_char = char.uuid
                            break
                    if notify_char:
                        break

            if notify_char:
                _LOGGER.info("Enabling notifications on %s", notify_char)
                await client.start_notify(notify_char, test_client._notification_handler)

            _LOGGER.info("Holding connection for %.1f seconds...", WAKE_HOLD_TIME)
            await asyncio.sleep(WAKE_HOLD_TIME)

            if test_client.notifications:
                _LOGGER.info("Received %d notifications during wake", len(test_client.notifications))
            else:
                _LOGGER.info("No notifications received (device may have woken silently)")

            _LOGGER.info("Wake sequence complete. Device should now connect to MQTT.")
            return True

    except Exception as e:
        _LOGGER.error("Wake failed: %s", e)
        return False


async def query_device(address: str) -> list[dict]:
    """Query device status via BLE."""
    address = ble_name_to_mac(address)
    _LOGGER.info("Querying device status at %s...", address)

    test_client = BLETestClient()
    results = []

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected!")

            # Find write and notify characteristics
            write_char = None
            notify_char = None

            for service in client.services:
                service_uuid = str(service.uuid).lower()
                if service_uuid in KNOWN_SERVICE_UUIDS:
                    for char in service.characteristics:
                        char_uuid = str(char.uuid).lower()
                        if "notify" in char.properties and not notify_char:
                            notify_char = char.uuid
                        if ("write" in char.properties or "write-without-response" in char.properties) and not write_char:
                            write_char = char.uuid

            if notify_char:
                _LOGGER.info("Enabling notifications on %s", notify_char)
                await client.start_notify(notify_char, test_client._notification_handler)

            if write_char:
                # Send query command for key DPs
                query_dps = [0, 1, 11, 50, 131, 209, 212, 213, 214, 221, 222]
                query_cmd = json.dumps(
                    {
                        "ts": int(time.time()),
                        "cmd": 9,
                        "dp": [{"id": dp_id} for dp_id in query_dps],
                    },
                    separators=(",", ":"),
                ).encode()

                _LOGGER.info("Sending query command: %s", query_cmd.decode())
                await client.write_gatt_char(write_char, query_cmd, response=True)
                _LOGGER.info("Query sent, waiting for response...")

                # Wait for responses
                await asyncio.sleep(3.0)

                results = test_client.notifications
                _LOGGER.info("Received %d notification(s)", len(results))
            else:
                _LOGGER.warning("No writable characteristic found")

    except Exception as e:
        _LOGGER.error("Query failed: %s", e)

    return results


async def monitor_device(address: str, duration: int = 30) -> list[dict]:
    """Monitor BLE notifications from a device."""
    address = ble_name_to_mac(address)
    _LOGGER.info("Monitoring device at %s for %d seconds...", address, duration)

    test_client = BLETestClient()

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected!")

            # Enable notifications on all WyBot characteristics
            for service in client.services:
                service_uuid = str(service.uuid).lower()
                if service_uuid in KNOWN_SERVICE_UUIDS:
                    for char in service.characteristics:
                        if "notify" in char.properties:
                            _LOGGER.info("Enabling notifications on %s", char.uuid)
                            try:
                                await client.start_notify(char.uuid, test_client._notification_handler)
                            except Exception as e:
                                _LOGGER.warning("Failed to enable notifications on %s: %s", char.uuid, e)

            _LOGGER.info("\nMonitoring for %d seconds (press Ctrl+C to stop)...\n", duration)

            # Wait and collect notifications
            start_time = time.time()
            while time.time() - start_time < duration:
                await asyncio.sleep(1.0)
                elapsed = int(time.time() - start_time)
                if elapsed % 10 == 0:
                    _LOGGER.info("  [%d/%d seconds] Notifications received: %d", elapsed, duration, len(test_client.notifications))

            _LOGGER.info("\n=== Monitoring Complete ===")
            _LOGGER.info("Total notifications: %d", len(test_client.notifications))

    except Exception as e:
        _LOGGER.error("Monitoring failed: %s", e)

    return test_client.notifications


async def send_dp_command(
    address: str,
    dp_id: int,
    dp_type: int,
    dp_len: int,
    dp_data: str,
    description: str = "command",
) -> tuple[bool, list[dict]]:
    """Send a DP command and wait for response.

    Args:
        address: Device MAC address or BLE name
        dp_id: Data Point ID
        dp_type: Data Point type (4 = enum)
        dp_len: Data length
        dp_data: Hex string data to send
        description: Human-readable description for logging

    Returns:
        Tuple of (success, notifications)
    """
    address = ble_name_to_mac(address)
    _LOGGER.info("Sending %s to %s...", description, address)

    test_client = BLETestClient()
    success = False

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected!")

            # Find write and notify characteristics
            write_char = None
            notify_char = None

            for service in client.services:
                service_uuid = str(service.uuid).lower()
                if service_uuid in KNOWN_SERVICE_UUIDS:
                    for char in service.characteristics:
                        char_uuid = str(char.uuid).lower()
                        if "notify" in char.properties and not notify_char:
                            notify_char = char.uuid
                        if ("write" in char.properties or "write-without-response" in char.properties) and not write_char:
                            write_char = char.uuid

            if notify_char:
                _LOGGER.info("Enabling notifications on %s", notify_char)
                await client.start_notify(notify_char, test_client._notification_handler)

            if write_char:
                # Build command
                cmd = json.dumps(
                    {
                        "ts": int(time.time()),
                        "cmd": 4,
                        "dp": [{"id": dp_id, "type": dp_type, "len": dp_len, "data": dp_data}],
                    },
                    separators=(",", ":"),
                ).encode()

                _LOGGER.info("Sending command: %s", cmd.decode())
                await client.write_gatt_char(write_char, cmd, response=True)
                _LOGGER.info("Command sent, waiting for response...")

                # Wait for response
                await asyncio.sleep(3.0)

                if test_client.notifications:
                    _LOGGER.info("Received %d notification(s)", len(test_client.notifications))
                    success = True
                else:
                    _LOGGER.warning("No response received")
            else:
                _LOGGER.warning("No writable characteristic found")

    except Exception as e:
        _LOGGER.error("%s failed: %s", description, e)

    return success, test_client.notifications


async def start_cleaning(address: str, mode: int = 0) -> bool:
    """Send start cleaning command via BLE.

    Args:
        address: Device MAC address or BLE name
        mode: Cleaning mode (0=Floor, 1=Wall, 2=Wall Then Floor, etc)

    Returns:
        True if command was sent successfully
    """
    address = ble_name_to_mac(address)
    _LOGGER.info("Starting cleaning on %s (mode=%d)...", address, mode)

    test_client = BLETestClient()

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected!")

            # Find write and notify characteristics
            write_char = None
            notify_char = None

            for service in client.services:
                service_uuid = str(service.uuid).lower()
                if service_uuid in KNOWN_SERVICE_UUIDS:
                    for char in service.characteristics:
                        char_uuid = str(char.uuid).lower()
                        if "notify" in char.properties and not notify_char:
                            notify_char = char.uuid
                        if ("write" in char.properties or "write-without-response" in char.properties) and not write_char:
                            write_char = char.uuid

            if notify_char:
                _LOGGER.info("Enabling notifications on %s", notify_char)
                await client.start_notify(notify_char, test_client._notification_handler)

            if not write_char:
                _LOGGER.error("No writable characteristic found")
                return False

            # First, query current state to check dock status
            query_cmd = json.dumps(
                {
                    "ts": int(time.time()),
                    "cmd": 9,
                    "dp": [{"id": 0}, {"id": 213}],  # CleaningStatus, DockConnectionStatus
                },
                separators=(",", ":"),
            ).encode()

            _LOGGER.info("Querying current state: %s", query_cmd.decode())
            await client.write_gatt_char(write_char, query_cmd, response=True)
            await asyncio.sleep(2.0)

            # Check if docked (warn user)
            for notification in test_client.notifications:
                data = notification.get("data_raw", b"")
                # Look for DP 213 = 01 (docked)
                if b"\xd5\x04\x01\x01" in data:  # DP 213, type 4, len 1, value 01
                    _LOGGER.warning("=" * 60)
                    _LOGGER.warning("⚠️  Robot is DOCKED on Solar Dock (DP 213 = 01)")
                    _LOGGER.warning("⚠️  KNOWN LIMITATION: BLE start cleaning commands are")
                    _LOGGER.warning("⚠️  IGNORED when robot is docked on DS20 Solar Dock.")
                    _LOGGER.warning("⚠️  ")
                    _LOGGER.warning("⚠️  Workaround: Use the WyBot mobile app to start cleaning,")
                    _LOGGER.warning("⚠️  or manually remove the robot from the dock first.")
                    _LOGGER.warning("=" * 60)

            # Set mode first if not default
            if mode != 0:
                mode_hex = f"{mode:02x}"
                mode_cmd = json.dumps(
                    {
                        "ts": int(time.time()),
                        "cmd": 4,
                        "dp": [{"id": 1, "type": 4, "len": 1, "data": mode_hex}],
                    },
                    separators=(",", ":"),
                ).encode()
                _LOGGER.info("Setting cleaning mode to %d: %s", mode, mode_cmd.decode())
                await client.write_gatt_char(write_char, mode_cmd, response=True)
                await asyncio.sleep(1.0)

            # Send start cleaning command: DP 0 = 03
            start_cmd = json.dumps(
                {
                    "ts": int(time.time()),
                    "cmd": 4,
                    "dp": [{"id": 0, "type": 4, "len": 1, "data": "03"}],
                },
                separators=(",", ":"),
            ).encode()

            _LOGGER.info("Sending start cleaning: %s", start_cmd.decode())
            await client.write_gatt_char(write_char, start_cmd, response=True)

            # Wait for response
            await asyncio.sleep(3.0)

            _LOGGER.info("Received %d notification(s)", len(test_client.notifications))
            _LOGGER.info("Start cleaning command sent. Check robot behavior.")
            return True

    except Exception as e:
        _LOGGER.error("Start cleaning failed: %s", e)
        return False


async def stop_cleaning(address: str) -> bool:
    """Send stop cleaning command via BLE.

    Args:
        address: Device MAC address or BLE name

    Returns:
        True if command was sent successfully
    """
    success, _ = await send_dp_command(
        address,
        dp_id=0,
        dp_type=4,
        dp_len=1,
        dp_data="01",  # 01 = Stopped
        description="stop cleaning (DP 0 = 01)",
    )
    return success


async def start_cleaning_binary(address: str) -> bool:
    """Send start cleaning command using binary format to EE01.

    This is the CORRECT method discovered via PacketLogger capture.
    The WyBot app uses binary AA55 format sent to EE01 characteristic.

    Args:
        address: Device MAC address or BLE name

    Returns:
        True if robot started cleaning
    """
    address = ble_name_to_mac(address)
    _LOGGER.info("=" * 60)
    _LOGGER.info("START CLEANING (Binary format to EE01)")
    _LOGGER.info("=" * 60)
    _LOGGER.info("Connecting to %s...", address)

    success = False

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected!")

            # Set up notification handler
            cleaning_started = False
            undocked = False

            def handle_notify(sender, data: bytearray) -> None:
                nonlocal cleaning_started, undocked
                if b"\x00\x04\x01\x03" in data:
                    cleaning_started = True
                    _LOGGER.info(">>> CleaningStatus: CLEANING! <<<")
                if b"\xd5\x04\x01\x00" in data:
                    undocked = True
                    _LOGGER.info(">>> DockStatus: UNDOCKED! <<<")

            # Enable notifications on FF01 (status updates)
            _LOGGER.info("Enabling notifications on FF01...")
            await client.start_notify(CHAR_UUID_FF01_NOTIFY, handle_notify)
            await asyncio.sleep(2)

            # Send binary command to EE01
            _LOGGER.info("Sending binary command to EE01: %s", BINARY_CMD_START_CLEANING.hex())
            await client.write_gatt_char(CHAR_UUID_EE01_WRITE, BINARY_CMD_START_CLEANING, response=True)
            _LOGGER.info("Command sent!")

            # Wait for response
            _LOGGER.info("Waiting for status update...")
            await asyncio.sleep(5)

            if cleaning_started:
                _LOGGER.info("=" * 60)
                _LOGGER.info("SUCCESS! Robot is cleaning!")
                _LOGGER.info("=" * 60)
                success = True
            elif undocked:
                _LOGGER.info("Robot undocked but cleaning status not confirmed")
                success = True
            else:
                _LOGGER.warning("No status change detected")

    except Exception as e:
        _LOGGER.error("Error: %s", e)

    return success


async def stop_cleaning_binary(address: str) -> bool:
    """Send stop cleaning command using binary format to EE01."""
    address = ble_name_to_mac(address)
    _LOGGER.info("Stopping cleaning (binary format to EE01)...")

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected!")
            await client.write_gatt_char(CHAR_UUID_EE01_WRITE, BINARY_CMD_STOP_CLEANING, response=True)
            _LOGGER.info("Stop command sent: %s", BINARY_CMD_STOP_CLEANING.hex())
            await asyncio.sleep(2)
            return True
    except Exception as e:
        _LOGGER.error("Error: %s", e)
        return False


def build_binary_query(dp_id: int) -> bytes:
    """Build a binary query command for a single DP."""
    # Query format: aa55 + cmd(0009) + len_le + dp_id + checksum
    header = b'\xaa\x55'
    cmd = b'\x00\x09'  # Query command
    length = b'\x01\x00'  # 1 byte payload, little-endian
    payload = bytes([dp_id])
    packet = header + cmd + length + payload
    checksum = (sum(packet[2:]) - 1) & 0xFF  # Checksum pattern: sum - 1
    return packet + bytes([checksum])


async def query_device_binary(address: str) -> dict:
    """Query device status via BLE using binary format.

    Returns dict of DP values.
    """
    address = ble_name_to_mac(address)
    _LOGGER.info("Querying device status (binary format)...")

    results = {}

    # DPs to query
    query_dps = [0, 1, 11, 50, 131, 209, 212, 213, 214, 221, 222]

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected!")

            # Notification handler to capture responses
            def handle_notify(sender, data: bytearray):
                hex_data = data.hex()
                # Parse DP responses
                if data[:2] == b'\xaa\x55' and len(data) > 6:
                    # Skip header (2) + cmd (2) + len (2)
                    payload = data[6:-1]  # Exclude checksum
                    offset = 0
                    while offset < len(payload) - 2:
                        dp_id = payload[offset]
                        dp_type = payload[offset + 1]
                        dp_len = payload[offset + 2]
                        if offset + 3 + dp_len <= len(payload):
                            dp_data = payload[offset + 3:offset + 3 + dp_len].hex()
                            results[dp_id] = {"type": dp_type, "len": dp_len, "data": dp_data}
                            _LOGGER.info("  DP %d: %s", dp_id, dp_data)
                        offset += 3 + dp_len

            await client.start_notify(CHAR_UUID_FF01_NOTIFY, handle_notify)
            await asyncio.sleep(1)

            # Query each DP
            for dp_id in query_dps:
                query_cmd = build_binary_query(dp_id)
                await client.write_gatt_char(CHAR_UUID_EE01_WRITE, query_cmd, response=True)
                await asyncio.sleep(0.3)

            await asyncio.sleep(2)

            # Interpret results
            _LOGGER.info("\n=== Status Summary ===")
            if 0 in results:
                status_map = {"01": "Stopped", "02": "Returning", "03": "Cleaning", "04": "Returning to Dock"}
                _LOGGER.info("CleaningStatus: %s", status_map.get(results[0]["data"], results[0]["data"]))
            if 1 in results:
                mode_map = {0: "Floor", 1: "Wall", 2: "Wall Then Floor", 3: "Advanced Full Pool",
                           4: "Water Line", 5: "Turbo Floor", 6: "Eco Floor"}
                mode_val = int(results[1]["data"], 16)
                _LOGGER.info("CleaningMode: %s", mode_map.get(mode_val, f"Unknown ({mode_val})"))
            if 50 in results:
                data = results[50]["data"]
                if len(data) >= 4:
                    charge = int(data[:2], 16)
                    level = int(data[2:4], 16)
                    charge_map = {0: "Not plugged", 1: "Charging", 2: "Charged"}
                    _LOGGER.info("Battery: %d%%, %s", level, charge_map.get(charge, f"Unknown ({charge})"))
            if 213 in results:
                dock_map = {"00": "Undocked", "01": "Docked"}
                _LOGGER.info("DockStatus: %s", dock_map.get(results[213]["data"], results[213]["data"]))
            if 221 in results:
                data = results[221]["data"]
                if len(data) >= 4:
                    battery_pct = int(data[2:4], 16)
                    _LOGGER.info("SolarDockBattery: %d%%", battery_pct)

    except Exception as e:
        _LOGGER.error("Query failed: %s", e)

    return results


async def set_mode_binary(address: str, mode: int) -> bool:
    """Set cleaning mode using binary format to EE01.

    Args:
        address: Device MAC address or BLE name
        mode: Cleaning mode (0-6)
            0 = Floor
            1 = Wall
            2 = Wall Then Floor
            3 = Advanced Full Pool
            4 = Water Line
            5 = Turbo Floor
            6 = Eco Floor

    Returns:
        True if command was sent successfully
    """
    address = ble_name_to_mac(address)

    if mode not in BINARY_MODE_COMMANDS:
        _LOGGER.error("Invalid mode %d. Must be 0-6.", mode)
        return False

    mode_name = MODE_NAMES.get(mode, f"Unknown ({mode})")
    cmd = BINARY_MODE_COMMANDS[mode]

    _LOGGER.info("Setting cleaning mode to %d (%s)...", mode, mode_name)

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected!")
            await client.write_gatt_char(CHAR_UUID_EE01_WRITE, cmd, response=True)
            _LOGGER.info("Mode command sent: %s", cmd.hex())
            await asyncio.sleep(2)
            _LOGGER.info("Mode set to %s", mode_name)
            return True
    except Exception as e:
        _LOGGER.error("Error: %s", e)
        return False


async def return_to_dock_binary(address: str) -> bool:
    """Send return to dock command using binary format to EE01.

    Sends DP 11 = 01 which triggers status 04 (Returning to Dock).
    """
    address = ble_name_to_mac(address)
    _LOGGER.info("Sending return to dock (binary format to EE01)...")

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected!")
            await client.write_gatt_char(CHAR_UUID_EE01_WRITE, BINARY_CMD_RETURN_TO_DOCK, response=True)
            _LOGGER.info("Return command sent: %s", BINARY_CMD_RETURN_TO_DOCK.hex())
            await asyncio.sleep(2)
            _LOGGER.info("Robot should now be returning to dock (status 04)")
            return True
    except Exception as e:
        _LOGGER.error("Error: %s", e)
        return False


async def undock_device(address: str) -> bool:
    """Send undock/return command via BLE.

    This makes the robot leave the dock. The robot should undock and
    be ready to receive cleaning commands.

    Args:
        address: Device MAC address or BLE name

    Returns:
        True if command was sent successfully
    """
    success, _ = await send_dp_command(
        address,
        dp_id=11,
        dp_type=4,
        dp_len=1,
        dp_data="01",  # 01 = Return/Undock
        description="undock (DP 11 = 01)",
    )
    return success


async def full_clean(address: str, mode: int = 0, undock_wait: int = 10) -> bool:
    """Execute full cleaning sequence: undock, wait, start cleaning.

    This is the recommended sequence when robot is docked:
    1. Send undock command (DP 11 = 01)
    2. Wait for robot to undock
    3. Verify undocked state (DP 213 = 00)
    4. Send start cleaning command (DP 0 = 03)

    Args:
        address: Device MAC address or BLE name
        mode: Cleaning mode (0=Floor, 1=Wall, etc)
        undock_wait: Seconds to wait after undock command

    Returns:
        True if cleaning started successfully
    """
    address = ble_name_to_mac(address)
    _LOGGER.info("=" * 60)
    _LOGGER.info("FULL CLEAN SEQUENCE: %s", address)
    _LOGGER.info("=" * 60)

    test_client = BLETestClient()

    try:
        async with BleakClient(address, timeout=CONNECTION_TIMEOUT) as client:
            _LOGGER.info("Connected!")

            # Find write and notify characteristics
            write_char = None
            notify_char = None

            for service in client.services:
                service_uuid = str(service.uuid).lower()
                if service_uuid in KNOWN_SERVICE_UUIDS:
                    for char in service.characteristics:
                        char_uuid = str(char.uuid).lower()
                        if "notify" in char.properties and not notify_char:
                            notify_char = char.uuid
                        if ("write" in char.properties or "write-without-response" in char.properties) and not write_char:
                            write_char = char.uuid

            if notify_char:
                await client.start_notify(notify_char, test_client._notification_handler)

            if not write_char:
                _LOGGER.error("No writable characteristic found")
                return False

            # Step 1: Query current state
            _LOGGER.info("\n[Step 1] Querying current state...")
            query_cmd = json.dumps(
                {
                    "ts": int(time.time()),
                    "cmd": 9,
                    "dp": [{"id": 0}, {"id": 11}, {"id": 50}, {"id": 213}],
                },
                separators=(",", ":"),
            ).encode()
            await client.write_gatt_char(write_char, query_cmd, response=True)
            await asyncio.sleep(2.0)

            # Check initial dock status
            is_docked = True  # Assume docked initially
            for notification in test_client.notifications:
                data = notification.get("data_raw", b"")
                if b"\xd5\x04\x01\x00" in data:  # DP 213 = 00 (undocked)
                    is_docked = False
                    _LOGGER.info("Robot is already UNDOCKED")

            if is_docked:
                _LOGGER.info("Robot is DOCKED - will send undock command")

            # Step 2: Send undock command
            _LOGGER.info("\n[Step 2] Sending undock command (DP 11 = 01)...")
            undock_cmd = json.dumps(
                {
                    "ts": int(time.time()),
                    "cmd": 4,
                    "dp": [{"id": 11, "type": 4, "len": 1, "data": "01"}],
                },
                separators=(",", ":"),
            ).encode()
            _LOGGER.info("Command: %s", undock_cmd.decode())
            await client.write_gatt_char(write_char, undock_cmd, response=True)
            await asyncio.sleep(2.0)

            # Step 3: Wait for robot to undock
            _LOGGER.info("\n[Step 3] Waiting %d seconds for robot to undock...", undock_wait)
            for i in range(undock_wait):
                await asyncio.sleep(1.0)
                _LOGGER.info("  Waiting... %d/%d seconds", i + 1, undock_wait)

            # Step 4: Verify undocked state
            _LOGGER.info("\n[Step 4] Verifying undocked state...")
            test_client.notifications.clear()
            query_cmd = json.dumps(
                {
                    "ts": int(time.time()),
                    "cmd": 9,
                    "dp": [{"id": 0}, {"id": 213}],
                },
                separators=(",", ":"),
            ).encode()
            await client.write_gatt_char(write_char, query_cmd, response=True)
            await asyncio.sleep(2.0)

            # Check dock status after undock
            still_docked = True
            for notification in test_client.notifications:
                data = notification.get("data_raw", b"")
                if b"\xd5\x04\x01\x00" in data:  # DP 213 = 00 (undocked)
                    still_docked = False
                    _LOGGER.info("✓ Robot confirmed UNDOCKED (DP 213 = 00)")

            if still_docked:
                _LOGGER.warning("⚠️  Robot may still be docked - proceeding anyway")

            # Step 5: Set cleaning mode if needed
            if mode != 0:
                _LOGGER.info("\n[Step 5] Setting cleaning mode to %d...", mode)
                mode_hex = f"{mode:02x}"
                mode_cmd = json.dumps(
                    {
                        "ts": int(time.time()),
                        "cmd": 4,
                        "dp": [{"id": 1, "type": 4, "len": 1, "data": mode_hex}],
                    },
                    separators=(",", ":"),
                ).encode()
                await client.write_gatt_char(write_char, mode_cmd, response=True)
                await asyncio.sleep(1.0)

            # Step 6: Send start cleaning command
            _LOGGER.info("\n[Step 6] Sending start cleaning command (DP 0 = 03)...")
            test_client.notifications.clear()
            start_cmd = json.dumps(
                {
                    "ts": int(time.time()),
                    "cmd": 4,
                    "dp": [{"id": 0, "type": 4, "len": 1, "data": "03"}],
                },
                separators=(",", ":"),
            ).encode()
            _LOGGER.info("Command: %s", start_cmd.decode())
            await client.write_gatt_char(write_char, start_cmd, response=True)
            await asyncio.sleep(3.0)

            # Step 7: Verify cleaning started
            _LOGGER.info("\n[Step 7] Verifying cleaning started...")
            test_client.notifications.clear()
            query_cmd = json.dumps(
                {
                    "ts": int(time.time()),
                    "cmd": 9,
                    "dp": [{"id": 0}, {"id": 213}],
                },
                separators=(",", ":"),
            ).encode()
            await client.write_gatt_char(write_char, query_cmd, response=True)
            await asyncio.sleep(2.0)

            is_cleaning = False
            for notification in test_client.notifications:
                data = notification.get("data_raw", b"")
                if b"\x00\x04\x01\x03" in data:  # DP 0 = 03 (cleaning)
                    is_cleaning = True
                    _LOGGER.info("✓ Robot confirmed CLEANING (DP 0 = 03)")

            _LOGGER.info("\n" + "=" * 60)
            if is_cleaning:
                _LOGGER.info("SUCCESS: Full clean sequence completed - robot is cleaning!")
            else:
                _LOGGER.info("RESULT: Commands sent. Check robot behavior.")
                _LOGGER.info("        (Response verification may not detect all states)")
            _LOGGER.info("=" * 60)

            return True

    except Exception as e:
        _LOGGER.error("Full clean sequence failed: %s", e)
        return False


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="WyBot BLE Test Script")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Scan command
    subparsers.add_parser("scan", help="Scan for WyBot devices")

    # Discover command
    discover_parser = subparsers.add_parser("discover", help="Discover services on a device")
    discover_parser.add_argument("address", help="Device MAC address or BLE name")

    # Wake command
    wake_parser = subparsers.add_parser("wake", help="Wake a device via BLE")
    wake_parser.add_argument("address", help="Device MAC address or BLE name")

    # Query command
    query_parser = subparsers.add_parser("query", help="Query device status")
    query_parser.add_argument("address", help="Device MAC address or BLE name")

    # Monitor command
    monitor_parser = subparsers.add_parser("monitor", help="Monitor device notifications")
    monitor_parser.add_argument("address", help="Device MAC address or BLE name")
    monitor_parser.add_argument("duration", nargs="?", type=int, default=30, help="Duration in seconds (default: 30)")

    # Start cleaning command
    start_parser = subparsers.add_parser("start", help="Start cleaning")
    start_parser.add_argument("address", help="Device MAC address or BLE name")
    start_parser.add_argument(
        "--mode",
        type=int,
        default=0,
        help="Cleaning mode: 0=Floor, 1=Wall, 2=Wall Then Floor, 3=Advanced Full Pool, 4=Water Line, 5=Turbo Floor, 6=Eco Floor (default: 0)",
    )

    # Stop cleaning command
    stop_parser = subparsers.add_parser("stop", help="Stop cleaning")
    stop_parser.add_argument("address", help="Device MAC address or BLE name")

    # Undock command
    undock_parser = subparsers.add_parser("undock", help="Undock robot from dock")
    undock_parser.add_argument("address", help="Device MAC address or BLE name")

    # Full clean command
    full_clean_parser = subparsers.add_parser("full-clean", help="Full sequence: undock, wait, start cleaning")
    full_clean_parser.add_argument("address", help="Device MAC address or BLE name")
    full_clean_parser.add_argument(
        "--mode",
        type=int,
        default=0,
        help="Cleaning mode: 0=Floor, 1=Wall, etc. (default: 0)",
    )
    full_clean_parser.add_argument(
        "--wait",
        type=int,
        default=10,
        help="Seconds to wait after undock before starting (default: 10)",
    )

    # Start cleaning (binary format) - VERIFIED WORKING
    start_binary_parser = subparsers.add_parser(
        "start-binary",
        help="Start cleaning using binary format to EE01 (VERIFIED WORKING - use this!)",
    )
    start_binary_parser.add_argument("address", help="Device MAC address or BLE name")

    # Stop cleaning (binary format)
    stop_binary_parser = subparsers.add_parser(
        "stop-binary",
        help="Stop cleaning using binary format to EE01",
    )
    stop_binary_parser.add_argument("address", help="Device MAC address or BLE name")

    # Return to dock (binary format)
    return_binary_parser = subparsers.add_parser(
        "return-binary",
        help="Return to dock using binary format to EE01",
    )
    return_binary_parser.add_argument("address", help="Device MAC address or BLE name")

    # Set cleaning mode (binary format)
    mode_parser = subparsers.add_parser(
        "set-mode",
        help="Set cleaning mode (0=Floor, 1=Wall, 2=Wall Then Floor, 3=Advanced, 4=Water Line, 5=Turbo, 6=Eco)",
    )
    mode_parser.add_argument("address", help="Device MAC address or BLE name")
    mode_parser.add_argument("mode", type=int, choices=range(7), help="Mode 0-6")

    # Query (binary format)
    query_binary_parser = subparsers.add_parser(
        "query-binary",
        help="Query device status using binary format (shows cleaning mode)",
    )
    query_binary_parser.add_argument("address", help="Device MAC address or BLE name")

    args = parser.parse_args()

    if args.command == "scan":
        await scan_devices()
    elif args.command == "discover":
        await discover_services(args.address)
    elif args.command == "wake":
        await wake_device(args.address)
    elif args.command == "query":
        await query_device(args.address)
    elif args.command == "monitor":
        await monitor_device(args.address, args.duration)
    elif args.command == "start":
        await start_cleaning(args.address, args.mode)
    elif args.command == "stop":
        await stop_cleaning(args.address)
    elif args.command == "undock":
        await undock_device(args.address)
    elif args.command == "full-clean":
        await full_clean(args.address, args.mode, args.wait)
    elif args.command == "start-binary":
        await start_cleaning_binary(args.address)
    elif args.command == "stop-binary":
        await stop_cleaning_binary(args.address)
    elif args.command == "return-binary":
        await return_to_dock_binary(args.address)
    elif args.command == "set-mode":
        await set_mode_binary(args.address, args.mode)
    elif args.command == "query-binary":
        await query_device_binary(args.address)
    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _LOGGER.info("\nInterrupted by user")

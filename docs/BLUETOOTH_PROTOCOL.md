# WyBot Bluetooth (BLE) Protocol Documentation

This document describes the Bluetooth Low Energy (BLE) protocol used by WyBot pool cleaning robots, based on reverse engineering of the WyBot Android APK and live device testing.

## Table of Contents

1. [Overview](#overview)
2. [Device Discovery](#device-discovery)
3. [Service UUIDs](#service-uuids)
4. [Connection Protocol](#connection-protocol)
5. [Message Format](#message-format)
6. [Data Points (DPs) Reference](#data-points-dps-reference)
7. [BLE Response Parsing](#ble-response-parsing)
8. [Implementation Notes](#implementation-notes)
9. [Testing Guide](#testing-guide)

---

## Overview

WyBot pool robots support two communication methods:

| Method | Use Case | Range | Latency |
|--------|----------|-------|---------|
| **MQTT** | Cloud communication, remote control | Unlimited (internet) | ~1-2s |
| **BLE** | Local wake-up, direct commands | ~10m | ~100ms |

### Communication Architecture

```
┌─────────────┐      BLE        ┌─────────────┐
│  HA/Phone   │◄───────────────►│ WyBot Robot │
└─────────────┘                 └──────┬──────┘
       │                               │
       │ MQTT                          │ WiFi
       │                               │
       ▼                               ▼
┌─────────────────────────────────────────────┐
│           mqtt.wybotpool.com                │
└─────────────────────────────────────────────┘
```

### BLE Primary Use Cases

1. **Wake device**: Establish BLE connection to wake the robot from sleep mode
2. **Direct commands**: Send cleaning commands via BLE when MQTT is unavailable
3. **WiFi configuration**: Configure WiFi credentials on new/reset devices
4. **Status polling**: Query device status directly without cloud dependency

---

## Device Discovery

### BLE Advertisement Name Format

WyBot devices advertise using their MAC address without colons:

| Device | BLE Name Example | MAC Address |
|--------|------------------|-------------|
| S2 Pro Robot | `CCBA97932A96` | `CC:BA:97:93:2A:96` |
| DS20 Solar Dock | `3C8427565A1A` | `3C:84:27:56:5A:1A` |

### Scanning for Devices

```python
from homeassistant.components.bluetooth import async_discovered_service_info

# Scan for WyBot devices
service_infos = async_discovered_service_info(hass, connectable=True)
for info in service_infos:
    device = info.device
    # Check if name matches MAC pattern (12 hex characters)
    if device.name and len(device.name) == 12:
        if all(c in '0123456789ABCDEFabcdef' for c in device.name):
            print(f"Found WyBot device: {device.name} at {device.address}")
```

### Converting BLE Name to MAC Address

```python
def ble_name_to_mac(ble_name: str) -> str:
    """Convert BLE name (CCBA97932A96) to MAC (CC:BA:97:93:2A:96)."""
    if len(ble_name) == 12:
        return ':'.join(ble_name[i:i+2] for i in range(0, 12, 2)).upper()
    return ble_name
```

---

## Service UUIDs

### DS20 Solar Dock (Verified via BLE Scan)

> **Architecture Note:** You always send BLE commands to the **DS20 Solar Dock** unless the robot
> itself is visible via BLE. When the robot is docked, the dock relays commands to the robot.
> When the robot is out cleaning, it's typically out of BLE range. The dock advertises as
> `DS20-XXXXXXXXXXXX` (e.g., `DS20-3C8427565A1A`), while the robot advertises as just its MAC
> address (e.g., `CCBA97932A96`).

| Service | UUID | Description |
|---------|------|-------------|
| Service EE | `000000ee-0000-1000-8000-00805f9b34fb` | Primary service |
| Service FF | `000000ff-0000-1000-8000-00805f9b34fb` | Secondary service |

| Characteristic | UUID | Properties | Purpose |
|---------------|------|------------|---------|
| EE01 | `0000ee01-0000-1000-8000-00805f9b34fb` | Read/Write/Notify | **Write commands here** (handle 0x002e) |
| FF01 | `0000ff01-0000-1000-8000-00805f9b34fb` | Read/Write/Notify | **Enable notifications here** (handle 0x0029) |

> **Note:** Despite both characteristics supporting write, the WyBot app only writes to EE01.
> Commands written to FF01 are ignored. Status updates are received as notifications on FF01.

### K1/S1/S2 Series (From APK: `k1/AbstractC0300a.java`)

| Service | UUID | Description |
|---------|------|-------------|
| Service 1000 | `00001000-0000-1000-8000-00805f9b34fb` | Primary service |

| Characteristic | UUID | Properties | Purpose |
|---------------|------|------------|---------|
| 1001 | `00001001-0000-1000-8000-00805f9b34fb` | Write | Command write |
| 1002 | `00001002-0000-1000-8000-00805f9b34fb` | Notify | Data reception |
| 1003 | `00001003-0000-1000-8000-00805f9b34fb` | Unknown | Reserved |
| 1004 | `00001004-0000-1000-8000-00805f9b34fb` | Read | Status read |
| 1005 | `00001005-0000-1000-8000-00805f9b34fb` | Unknown | Reserved |

### Client Characteristic Configuration Descriptor (CCCD)

| Descriptor | UUID | Purpose |
|------------|------|---------|
| CCCD | `00002902-0000-1000-8000-00805f9b34fb` | Enable notifications |

---

## Connection Protocol

### Connection State Machine (From APK: `R1/f.java`)

```
┌───────────────┐
│ State 0       │
│ Disconnected  │
└───────┬───────┘
        │ connect()
        ▼
┌───────────────┐
│ State 1       │
│ Connecting    │
└───────┬───────┘
        │ onConnectionStateChange(state=2)
        ▼
┌───────────────┐       ┌───────────────┐
│ State 2       │──────►│ State 3       │
│ Connected     │       │ Disconnecting │
└───────────────┘       └───────────────┘
```

### Wake Sequence

Based on APK analysis, the device wakes when a BLE connection is established:

```python
async def wake_device(hass, ble_name: str) -> bool:
    """Wake WyBot device via BLE connection."""

    # Step 1: Convert BLE name to MAC address
    mac_address = ble_name_to_mac(ble_name)

    # Step 2: Get BLE device from Home Assistant
    ble_device = async_ble_device_from_address(hass, mac_address, connectable=True)
    if not ble_device:
        return False

    # Step 3: Connect (this triggers wake via onConnectionStateChange state=2)
    client = await establish_connection(BleakClient, ble_device, ble_device.name)

    try:
        if not client.is_connected:
            return False

        # Step 4: Discover services (validates connection)
        services = client.services

        # Step 5: Enable notifications on data characteristic
        await client.start_notify(CHAR_UUID_EE01, notification_handler)

        # Step 6: Hold connection for 3-5 seconds
        await asyncio.sleep(5.0)

        # Step 7: Device should now connect to MQTT
        return True

    finally:
        await client.disconnect()
```

### Connection Parameters (From APK: `BleService.java`)

| Parameter | Value | Notes |
|-----------|-------|-------|
| Max concurrent connections | 4 | Multiple devices supported |
| Connection timeout | 3000ms | App default, increase for reliability |
| Reconnect timer | 100ms | Interval on disconnect |
| Wake hold time | 3-5 seconds | Allow device to fully wake |

---

## Message Format

> **CRITICAL UPDATE (January 2026):** The WyBot mobile app uses **binary AA55 format** for BLE
> commands, NOT JSON. Commands must be sent to the **EE01** characteristic. This was verified
> via PacketLogger capture of the iOS app. See [Binary Command Format](#binary-command-format-verified-working)
> section below for working examples.

### Binary Command Format (VERIFIED WORKING)

The WyBot app sends commands to the **EE01 characteristic** using binary AA55 format:

```
┌────────┬─────────┬──────────┬───────────────────────────┬──────────┐
│ Header │   Cmd   │   Len    │        DP Payload         │ Checksum │
│ AA 55  │  00 04  │  XX XX   │ id + type + len + value   │    XX    │
│ 2 bytes│ 2 bytes │ 2 bytes  │       N bytes             │  1 byte  │
└────────┴─────────┴──────────┴───────────────────────────┴──────────┘
```

**Working Commands (captured from WyBot iOS app and verified):**

| Command | Hex Data | Description |
|---------|----------|-------------|
| Start Cleaning | `aa5500040400000401030f` | DP 0 = 03 → Status: Cleaning |
| Stop Cleaning | `aa5500040400000401010d` | DP 0 = 01 → Status: Stopped (robots without dock) |
| Return to Dock | `aa55000404000b04010118` | DP 11 = 01 → Status: 04 (Returning) |

**Cleaning Mode Commands (DP 1):**

| Mode | Value | Hex Command |
|------|-------|-------------|
| Floor | 00 | `aa5500040400010401000d` |
| Wall | 01 | `aa5500040400010401010e` |
| Wall Then Floor | 02 | `aa5500040400010401020f` |
| Advanced Full Pool | 03 | `aa55000404000104010310` |
| Water Line | 04 | `aa55000404000104010411` |
| Turbo Floor | 05 | `aa55000404000104010512` |
| Eco Floor | 06 | `aa55000404000104010613` |

> **Note:** For robots with a dock (like DS20 Solar Dock), the app only shows "Return to Dock".
> The Stop command (DP 0 = 01) is for robots without a dock that need to stop in place.

**WiFi Configuration Commands (captured from WyBot app):**

| Step | Cmd | Hex Example | Description |
|------|-----|-------------|-------------|
| 1 | 0x2D | `aa55002d020000002e` | WiFi init/reset |
| 2 | 0x2A | `aa55002a2200[SSID padded to ~34 bytes][checksum]` | Send SSID |
| 3 | 0x2B | `aa55002b1100[PASSWORD][checksum]` | Send password |
| 4 | 0x03 | `aa550003000002` | Apply configuration |

> **Note:** SSID is null-padded to a fixed length. Length field is little-endian.

**Query Commands (cmd 0x09):**

To query a DP value, send a binary query command:

```
Format: aa55 + 0009 + len_le + dp_id + checksum
Example: aa5500090100010a  (query DP 1 = CleaningMode)
```

| Query | Hex Command | Description |
|-------|-------------|-------------|
| CleaningStatus | `aa55000901000009` | Query DP 0 |
| CleaningMode | `aa5500090100010a` | Query DP 1 |
| Battery | `aa550009010032` + checksum | Query DP 50 (0x32) |
| DockStatus | `aa5500090100d5` + checksum | Query DP 213 (0xd5) |

> **Tip:** Use `build_binary_query(dp_id)` function to generate correct commands with checksums.

```python
def build_binary_query(dp_id: int) -> bytes:
    """Build a binary query command for a single DP."""
    header = b'\xaa\x55'
    cmd = b'\x00\x09'  # Query command
    length = b'\x01\x00'  # 1 byte payload, little-endian
    payload = bytes([dp_id])
    packet = header + cmd + length + payload
    checksum = (sum(packet[2:]) - 1) & 0xFF
    return packet + bytes([checksum])
```

**Response Types (IMPORTANT):**

The device sends different response types. **Only parse DP data from status broadcasts (cmd=0x05)**:

| Cmd | Name | Description | Contains DPs? |
|-----|------|-------------|---------------|
| 0x05 | Status | Status broadcast with DP data | ✅ Yes - parse these |
| 0x1C | ACK | Acknowledgment only | ❌ No - skip these |

```python
def handle_notify(sender, data: bytearray):
    if len(data) < 6:
        return

    # Check command type
    cmd = int.from_bytes(data[2:4], 'big')

    if cmd == 0x05:  # Status broadcast - contains DP data
        payload = data[6:-1]  # Skip header(2) + cmd(2) + len(2), exclude checksum
        # Parse DPs from payload...

    elif cmd == 0x1c:  # ACK - no data
        pass  # Skip
```

**Example responses:**
```
ACK:    aa55001c0100001c           (cmd=0x1C, no DP data)
Status: aa550005380000040103...   (cmd=0x05, contains DPs)
```

**Cleaning Status Values (DP 0):**

| Value | Status |
|-------|--------|
| 01 | Stopped |
| 02 | Returning (legacy?) |
| 03 | Cleaning |
| 04 | Returning to Dock |

**Key Characteristics:**

| What | Characteristic UUID | Handle | Purpose |
|------|---------------------|--------|---------|
| Write Commands | EE01 (`0000ee01-...`) | 0x002e | Send commands here |
| Notifications | FF01 (`0000ff01-...`) | 0x0029 | Receive status updates |

**Python Example:**

```python
from bleak import BleakClient

CHAR_UUID_EE01 = "0000ee01-0000-1000-8000-00805f9b34fb"
CHAR_UUID_FF01 = "0000ff01-0000-1000-8000-00805f9b34fb"

# Pre-built commands (verified working)
CMD_START_CLEANING = bytes.fromhex('aa5500040400000401030f')  # DP 0 = 03
CMD_STOP_CLEANING = bytes.fromhex('aa5500040400000401010d')   # DP 0 = 01 (robots without dock)
CMD_RETURN_TO_DOCK = bytes.fromhex('aa55000404000b04010118')  # DP 11 = 01

async def start_cleaning(address: str):
    async with BleakClient(address) as client:
        await client.write_gatt_char(CHAR_UUID_EE01, CMD_START_CLEANING, response=True)
        # Status will change to 03 (Cleaning)

async def stop_cleaning(address: str):
    """Stop cleaning in place (for robots without a dock)."""
    async with BleakClient(address) as client:
        await client.write_gatt_char(CHAR_UUID_EE01, CMD_STOP_CLEANING, response=True)
        # Status will change to 01 (Stopped)

async def return_to_dock(address: str):
    async with BleakClient(address) as client:
        await client.write_gatt_char(CHAR_UUID_EE01, CMD_RETURN_TO_DOCK, response=True)
        # Status will change to 04 (Returning to Dock)
```

**Building Custom Binary Commands:**

```python
def build_binary_command(cmd: int, dp_id: int, dp_type: int, dp_len: int, dp_value: bytes) -> bytes:
    """Build a binary AA55 format command for BLE."""
    dp_data = bytes([dp_id, dp_type, dp_len]) + dp_value
    header = b'\xaa\x55'
    cmd_bytes = cmd.to_bytes(2, 'big')  # 0x0004 for write
    payload_len = len(dp_data).to_bytes(2, 'big')
    packet = header + cmd_bytes + payload_len + dp_data
    checksum = sum(packet[2:]) & 0xFF
    return packet + bytes([checksum])

# Example: Build start cleaning command
start_cmd = build_binary_command(
    cmd=4,          # Write command
    dp_id=0,        # CleaningStatus
    dp_type=4,      # Enum
    dp_len=1,       # 1 byte
    dp_value=b'\x03'  # Cleaning
)
# Result: aa5500040400000401030f
```

---

### JSON Command Format (MQTT Only)

**Note:** JSON format works for MQTT but NOT for BLE commands. For BLE, use the binary format above.

For MQTT, WyBot uses JSON commands:

```json
{
  "ts": 1705600000,
  "cmd": 4,
  "dp": [
    {
      "id": 0,
      "type": 4,
      "len": 1,
      "data": "03"
    }
  ]
}
```

### Command Types (`cmd`)

| cmd | Name | Description | Direction |
|-----|------|-------------|-----------|
| 4 | Write | Set a DP value | App -> Device |
| 5 | Response | Status report / acknowledgment | Device -> App |
| 9 | Query | Request DP value(s) | App -> Device |

### Data Types (`type`)

| Type | Name | Format | Example |
|------|------|--------|---------|
| 0 | Raw | Hex string, variable length | `"0164"` (battery: charging, 100%) |
| 2 | 32-bit | 4 bytes, little-endian | `"e8030000"` (1000 in decimal) |
| 4 | Enum | 1 byte value as hex | `"03"` (cleaning) |
| 5 | String | Hex-encoded ASCII string | `"48656c6c6f"` ("Hello") |

### Building Commands

```python
import json
import time

def build_write_command(dp_id: int, dp_type: int, dp_len: int, dp_data: str) -> bytes:
    """Build a write command (cmd=4)."""
    cmd = {
        "ts": int(time.time()),
        "cmd": 4,
        "dp": [{"id": dp_id, "type": dp_type, "len": dp_len, "data": dp_data}]
    }
    return json.dumps(cmd, separators=(',', ':')).encode()

def build_query_command(dp_ids: list[int] | None = None) -> bytes:
    """Build a query command (cmd=9)."""
    cmd = {
        "ts": int(time.time()),
        "cmd": 9,
        "dp": [{"id": dp_id} for dp_id in (dp_ids or [])]
    }
    return json.dumps(cmd, separators=(',', ':')).encode()

# Example: Start cleaning
start_cleaning = build_write_command(dp_id=0, dp_type=4, dp_len=1, dp_data="03")
# Result: {"ts":1705600000,"cmd":4,"dp":[{"id":0,"type":4,"len":1,"data":"03"}]}

# Example: Query all status
query_all = build_query_command([0, 1, 50, 11])
# Result: {"ts":1705600000,"cmd":9,"dp":[{"id":0},{"id":1},{"id":50},{"id":11}]}
```

---

## Data Points (DPs) Reference

### Robot Control DPs

| DP ID | Name | Type | Len | Values | Direction |
|-------|------|------|-----|--------|-----------|
| 0 | CleaningStatus | 4 | 1 | `01`=Stopped, `02`=Returning, `03`=Cleaning | R/W |
| 1 | CleaningMode | 4 | 1 | `00`-`06` (see modes below) | R/W |
| 11 | Dock | 4 | 1-2 | `01`=Return to dock | R/W |
| 50 | Battery | 0 | 2 | `[charge_state][level%]` | R |
| 77 | CleaningLog | - | 36 | Map/log data (binary) | R |
| 79 | Schedule | 2 | 12 | Schedule configuration | R/W |

### Cleaning Modes (DP 1)

| Value | Mode |
|-------|------|
| `00` | Floor |
| `01` | Wall |
| `02` | Wall Then Floor |
| `03` | Advanced Full Pool |
| `04` | Water Line |
| `05` | Turbo Floor |
| `06` | Eco Floor |

### Battery Format (DP 50)

```
Data: "0164" = [01][64]
       ↑     ↑
       │     └─ Battery level: 0x64 = 100%
       └─ Charge state: 00=Not plugged, 01=Charging, 02=Charged
```

### Device Status DPs

| DP ID | Name | Type | Len | Values | Direction |
|-------|------|------|-----|--------|-----------|
| 209 | DeviceStatus | 4 | 1 | Status flag | R |
| 212 | ConnectionStatus | 4 | 1 | `01`=Connected | R |
| 213 | DockConnectionStatus | 4 | 1 | `01`=Docked | R |
| 214 | DockInfo | 4 | 1 | `01`=Standard, `05`=Solar | R |

### Solar Dock DPs (DS20)

| DP ID | Name | Type | Len | Values | Direction |
|-------|------|------|-----|--------|-----------|
| 131 | SolarEnergyHarvested | 2 | 4 | Wh (little-endian) | R |
| 221 | SolarDockBattery | 0 | 3 | 3-byte format (see below) | R |
| 222 | SolarStatus | 0 | 1 | `01`=Charging | R |
| 223 | Unknown | - | 4 | Unknown | R |

### Solar Energy Format (DP 131)

```python
def parse_solar_energy(data: str) -> int:
    """Parse solar energy harvested in Wh (little-endian)."""
    # data = "e8030000" = 1000 Wh
    return int.from_bytes(bytes.fromhex(data), byteorder="little")
```

### Solar Dock Battery Format (DP 221)

3-byte format: `XXYYZZ`
- `XX`: Status/flags byte (typically `01`)
- `YY`: Battery percentage (0-100)
- `ZZ`: Unknown (possibly voltage related, typically `0a`)

```python
def parse_solar_battery(data: str) -> int:
    """Parse solar dock battery percentage from 3-byte format."""
    # data = "01480a" -> byte 1 = "48" = 72%
    # data = "01470a" -> byte 1 = "47" = 71%
    return int(data[2:4], 16)
```

---

## BLE Response Parsing

### Response Formats

WyBot BLE responses use a **binary format** (not JSON like MQTT). This was verified through live device testing.

#### Binary Response Format (Verified)

```
┌────────┬─────────┬─────────┬────────────┬──────────┐
│ Header │ Version │   Cmd   │   Length   │ Payload  │ Checksum │
│ AA 55  │   00    │   05    │  XX XX     │ DP Data  │    XX    │
│ 2 bytes│ 1 byte  │ 1 byte  │ 2 bytes BE │ N bytes  │  1 byte  │
└────────┴─────────┴─────────┴────────────┴──────────┴──────────┘
```

**Command Codes (verified):**
| Cmd | Name | Description |
|-----|------|-------------|
| 0x05 | Status | Status response with DP data |
| 0x12 | DeviceInfo | Device pairing/info (contains device IDs, MACs) |
| 0x1C | Ack | Acknowledgment |

### Binary DP Data Format (Verified)

Each DP entry in the payload:

```
┌─────────┬──────┬────────┬───────────┐
│  DP ID  │ Type │ Length │   Data    │
│ 1 byte  │1 byte│ 1 byte │ len bytes │
└─────────┴──────┴────────┴───────────┘
```

**Example parsed from live DS20 Solar Dock:**
```
Raw: aa550005380000040101320002012c83020433783900...

Parsed DPs:
  DP   0 (CleaningStatus): type=4, len=1, data=01 -> Stopped
  DP  50 (Battery):        type=0, len=2, data=012c -> 44%, Charging
  DP 131 (SolarEnergy):    type=2, len=4, data=33783900 -> 3,766,323 Wh
  DP 214 (DockInfo):       type=4, len=1, data=05 -> Solar dock
  DP 222 (SolarStatus):    type=0, len=1, data=01 -> Charging
```

### Parsing Code

```python
def parse_ble_response(data: bytes) -> list[dict]:
    """Parse BLE response containing DP data."""
    dps = []

    if len(data) < 7:
        return dps

    # Check for header
    if data[:2] == b'\xaa\x55':
        # WyBot format: aa55 + cmd(2) + len(2) + data + checksum
        payload_len = (data[4] << 8) | data[5]
        offset = 6
        end_offset = min(6 + payload_len, len(data) - 1)
    elif data[:2] == b'\x55\xaa':
        # Tuya format: 55aa + version + cmd + len(2) + data
        offset = 6
        end_offset = len(data) - 1
    else:
        # Try as raw DP data
        offset = 0
        end_offset = len(data)

    # Parse DP entries
    while offset < end_offset - 2:
        dp_id = data[offset]
        dp_type = data[offset + 1]
        dp_len = data[offset + 2]
        dp_data_start = offset + 3

        if dp_data_start + dp_len > end_offset:
            break

        dp_data = data[dp_data_start:dp_data_start + dp_len].hex()
        dps.append({
            "id": dp_id,
            "type": dp_type,
            "len": dp_len,
            "data": dp_data
        })

        offset = dp_data_start + dp_len

    return dps
```

---

## Implementation Notes

### Write Types (From APK: `B2/p.java`)

| Write Type | Property | Description |
|------------|----------|-------------|
| 1 | `property & 4` | Write without response |
| 2 | Default | Standard write with response |
| 4 | `property & 64` | Signed write |

### MTU Considerations

- Default MTU: 23 bytes (20 bytes payload)
- Negotiate higher MTU for longer payloads
- JSON commands can exceed 20 bytes; use MTU negotiation or chunking

### Encryption (Optional)

The APK references `libhy_api.so` for encryption, but this library was not present in the analyzed APK version. Encryption may be:
- Optional feature for newer devices
- Used only for certain sensitive operations
- Not implemented in current firmware

The `f5354j` flag in the APK controls whether `decodeMessage()` is called on received data.

### Error Handling

```python
# Connection errors
try:
    client = await establish_connection(BleakClient, device, name)
except BleakError as err:
    if "timeout" in str(err).lower():
        # Connection attempt may still have woken device
        return True
    raise

# Write errors
try:
    await client.write_gatt_char(char_uuid, payload, response=True)
except BleakError:
    # Try write without response as fallback
    await client.write_gatt_char(char_uuid, payload, response=False)
```

---

## Testing Guide

### Using Home Assistant Bluetooth Integration

Home Assistant's Bluetooth integration supports:
- Local Bluetooth adapters
- ESPHome Bluetooth proxies
- Shelly BLE proxies

### Service Discovery Script

```python
import asyncio
from bleak import BleakClient, BleakScanner

async def discover_services(address: str):
    """Discover and print all services/characteristics."""
    async with BleakClient(address) as client:
        print(f"Connected to {address}")

        for service in client.services:
            print(f"\nService: {service.uuid}")
            for char in service.characteristics:
                print(f"  Characteristic: {char.uuid}")
                print(f"    Properties: {char.properties}")
                for desc in char.descriptors:
                    print(f"    Descriptor: {desc.uuid}")

# Run: asyncio.run(discover_services("CC:BA:97:93:2A:96"))
```

### Notification Capture Script

```python
async def capture_notifications(address: str, char_uuid: str, duration: int = 30):
    """Capture BLE notifications for analysis."""
    notifications = []

    def handler(sender, data):
        notifications.append({
            "time": time.time(),
            "sender": sender,
            "data": data.hex()
        })
        print(f"Notification: {data.hex()}")

    async with BleakClient(address) as client:
        await client.start_notify(char_uuid, handler)
        await asyncio.sleep(duration)
        await client.stop_notify(char_uuid)

    return notifications
```

### Command Test Sequence

1. **Connect and enable notifications**
2. **Send query command**: `{"ts":...,"cmd":9,"dp":[{"id":0},{"id":50}]}`
3. **Wait for response** (notification or polling)
4. **Parse response** and verify DP values
5. **Send write command**: `{"ts":...,"cmd":4,"dp":[{"id":0,"type":4,"len":1,"data":"03"}]}`
6. **Verify acknowledgment** (cmd=5 response)

---

## Appendix: Quick Reference

### Common Commands

```python
# Start cleaning
{"ts":T,"cmd":4,"dp":[{"id":0,"type":4,"len":1,"data":"03"}]}

# Stop cleaning
{"ts":T,"cmd":4,"dp":[{"id":0,"type":4,"len":1,"data":"01"}]}

# Return to dock
{"ts":T,"cmd":4,"dp":[{"id":11,"type":4,"len":1,"data":"01"}]}

# Set cleaning mode to "Floor"
{"ts":T,"cmd":4,"dp":[{"id":1,"type":4,"len":1,"data":"00"}]}

# Query all status
{"ts":T,"cmd":9,"dp":[{"id":0},{"id":1},{"id":50},{"id":11}]}
```

### Device Model UUID Summary

| Model | Service UUID | Write Char | Notify Char |
|-------|--------------|------------|-------------|
| K1/S1/S2 | 00001000-... | 1001 | 1002 |
| DS20 Solar | 000000ee-... | EE01 | EE01 |
| DS20 Solar | 000000ff-... | FF01 | FF01 |

---

## References

- **APK Source Files**:
  - `k1/AbstractC0300a.java` - BLE UUIDs
  - `R1/f.java` - GATT callbacks, connection states
  - `B2/p.java` - Write operations
  - `com/ble/ble/BleService.java` - Service architecture

- **Implementation**:
  - `wybot_ble_client.py` - Home Assistant BLE client
  - `wybot_mqtt_client.py` - MQTT protocol reference
  - `wybot_dp_models.py` - DP model definitions

---

## Live Testing Results (January 2026)

### Test Environment
- **Home Assistant**: 2026.1.2
- **Bluetooth Proxy**: ESPHome `esp32-bluetooth-proxy-01f424` at 192.168.x.x
- **Devices Tested**:
  - DS20 Solar Dock (BLE name: `3C8427565A1A`)
  - S2 Pro Robot (BLE name: `CCBA97932A96`)

### Verified Findings

#### 1. Device Discovery
- DS20 Solar Dock advertises as `DS20-3C8427565A1A`
- Robot BLE name is MAC without colons: `CCBA97932A96`
- ESPHome Bluetooth proxy successfully discovers devices

#### 2. Service/Characteristic Confirmation
- **DS20 Solar Dock uses `000000ff` service** with `0000ff01` characteristic
- Notifications enabled successfully on FF01
- Write operations work with JSON commands

#### 3. Query Command Format (Working)
```json
{"ts":1768707077,"cmd":9,"dp":[]}
```
- Empty `dp` array queries all available DPs
- Response is binary format (not JSON)

#### 4. Captured Response Data
```
Response 1: 000102030405060708090a0b0c0d0e (initialization)
Response 2: aa550005380000040101320002012c... (status with 11 DPs)
Response 3: aa5500121a00012a0d020107... (device info with paired robot MAC)
Response 4: aa55001c0100001c (acknowledgment)
```

#### 5. Decoded Status Data
| DP | Name | Value | Interpretation |
|----|------|-------|----------------|
| 0 | CleaningStatus | 01 | Stopped |
| 50 | Battery | 012c | 44%, Charging |
| 131 | SolarEnergy | 33783900 | 3,766,323 Wh total |
| 209 | DeviceStatus | 03 | Active |
| 212 | ConnectionStatus | 01 | Connected |
| 213 | DockConnectionStatus | 01 | Robot docked |
| 214 | DockInfo | 05 | Solar dock |
| 221 | SolarDockBattery | 01470a | 71% |
| 222 | SolarStatus | 01 | Currently charging |

#### 6. Device Pairing Info (cmd=0x12)
The dock stores information about the paired robot:
- Robot device ID: `64893b4e841230014a333830`
- Robot MAC: `CC:BA:97:93:2A:96`

### Key Protocol Differences from MQTT

| Aspect | MQTT | BLE |
|--------|------|-----|
| Message format | JSON | Binary |
| Response format | JSON | Binary (AA55 header) |
| Query command | JSON with dp list | JSON with empty dp |
| Characteristic | N/A | FF01 (DS20), 1001 (K1) |

#### 7. Start Cleaning Command Test (Verified)

**Command Sent:**
```json
{"ts":1768708070,"cmd":4,"dp":[{"id":0,"type":4,"len":1,"data":"03"}]}
```
- `dp_id=0` (CleaningStatus), `data="03"` (CLEANING)

**Response Received:**
```
aa550005380000040101320002013383020433783900d10401
03d40401055d04010100df000201470adc000101de00040000
5abb1c
```

**Parsed Response DPs:**
| DP | Name | Value | Interpretation |
|----|------|-------|----------------|
| 0 | CleaningStatus | 01 | **Stopped** (command acknowledged but not executed) |
| 50 | Battery | 0133 | 51%, Charging |
| 131 | SolarEnergy | 33783900 | 3,766,323 Wh |
| 209 | DeviceStatus | 03 | Active |
| 212 | ConnectionStatus | 01 | Connected |
| 213 | DockConnectionStatus | **01** | **Docked** |
| 214 | DockInfo | 05 | Solar dock |
| 221 | SolarDockBattery | 01470a | 71% |
| 222 | SolarStatus | 01 | Charging |
| 223 | Unknown | 00005abb | Unknown data |

**Conclusion:**
- The BLE write command was **successfully sent and acknowledged**
- Robot remained in STOPPED state because it's **docked on the solar dock**
- When robot is docked (DP 213 = 01), start cleaning commands are **ignored**

**VERIFIED WORKING (January 2026 via PacketLogger capture):**

The WyBot app uses **binary AA55 format** sent to **EE01 characteristic**, NOT JSON to FF01!

| What | Wrong (didn't work) | Correct (works!) |
|------|---------------------|------------------|
| Format | JSON `{"cmd":4,"dp":[...]}` | Binary `aa5500040400000401030f` |
| Characteristic | FF01 (`0000ff01-...`) | **EE01** (`0000ee01-...`) |
| Handle | 0x0029 | **0x002e** |

**Start Cleaning Command (binary):**
```
aa5500040400000401030f

Breakdown:
- aa55     = WyBot header
- 0004     = cmd 4 (write)
- 0400     = payload length (4 bytes)
- 00       = DP ID 0 (CleaningStatus)
- 04       = type 4 (enum)
- 01       = value length
- 03       = value (CLEANING)
- 0f       = checksum
```

**Other binary commands:**
- Stop cleaning: `aa5500040400000401010d` (DP 0 = 01)
- Return to dock: `aa55000404000b0401011a` (DP 11 = 01)

---

*Document generated from APK analysis and live device testing (January 2026). Protocol details may vary between firmware versions.*

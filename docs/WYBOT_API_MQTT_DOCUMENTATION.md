# WYBOT Pool Cleaner API & MQTT Communication Documentation

**Source:** WYBOT Android App v2.1.14 (package: com.wy.winnygo)
**Extraction Method:** APK decompilation, libapp.so string extraction, and network packet capture analysis
**Date:** 2025-12-21

This documentation is based ONLY on data extracted from the official WYBOT mobile application and captured network traffic.

---

## Table of Contents
1. [MQTT Communication](#mqtt-communication)
2. [HTTP REST API](#http-rest-api)
3. [Data Point (DP) System](#data-point-dp-system)
4. [Message Examples from Packet Captures](#message-examples-from-packet-captures)

---

## MQTT Communication

### MQTT Broker Configuration

**Broker Address:** `mqtt.wybotpool.com`
**Protocol:** MQTT v3.1.1
**Port:** 1883 (default MQTT)

### Authentication Credentials

**From Packet Capture:**
```
Username: wyindustry
Password: nwe_GTG4faf2qyx8ugx
```

**Important Note:** These credentials are hardcoded in the application and shared across all users. Device isolation is achieved through topic naming with unique device IDs.

### MQTT Connection Parameters

From captured CONNECT packet:
- **Protocol:** MQTT v3.1.1 (Version 4)
- **Clean Session:** Enabled
- **Keep Alive:** 60 seconds
- **Client ID Format:** `iWY-<randomString>` (e.g., `iWY-MdTXibGW96`)
- **Will Topic:** `/will`
- **Will QoS:** 1 (At least once delivery)
- **Will Retain:** Enabled

### MQTT Topics

All topics use the device ID as a suffix for device-specific communication.

#### Device Status Topics

**Will Topic (Last Will and Testament):**
```
/will/{deviceId}
```
Payload when online:
```json
{
	"online":	"1"
}
```
Payload when offline:
```json
{
	"online":	"0"
}
```

#### Data Communication Topics

**Query Topic (App → Device):**
```
/device/DATA/recv_transparent_query_data/{deviceId}
```
Used to request current values of specific data points.

**Command Topic (App → Device):**
```
/device/DATA/recv_transparent_cmd_data/{deviceId}
```
Used to send control commands to change device state.

**Response Topic (Device → App):**
```
/device/DATA/send_transparent_data/{deviceId}
```
Device publishes responses and status updates here.

#### OTA/Firmware Update Topics

**OTA Ready Notification:**
```
/device/OTA/notify_ready_to_update/{deviceId}
```

**OTA Update Progress:**
```
/device/OTA/post_update_progress/{deviceId}
```

**OTA Error Codes:**
```
/device/OTA/post_errcode/{deviceId}
```

**OTA Start Update:**
```
/device/OTA/recv_start_update/{deviceId}
```

**OTA Firmware Info Request:**
```
/device/OTA/reply_request_all_firmware_info/{deviceId}
```

**OTA Firmware Info Response:**
```
/device/OTA/reply_all_firmware_info/{deviceId}
```

#### Additional Device-Specific Topics

From string extraction:
```
/device/hayward/cus_privacy
/device/m2/vision_clean_mode_privacy
```

### Message Structure

All MQTT data messages use JSON format with the following structure:

#### Query Message (cmd = 9)
```json
{
  "cmd": 9,
  "ts": 1885119988,
  "dp": [
    {
      "id": 1
    }
  ]
}
```

#### Write Command (cmd = 4)
```json
{
  "cmd": 4,
  "ts": 1885119988,
  "dp": [
    {
      "id": 0,
      "type": 4,
      "len": 1,
      "data": "01"
    }
  ]
}
```

#### Response Message (cmd = 5)
```json
{
  "cmd": 5,
  "ts": 1885119994,
  "dp": [
    {
      "id": 0,
      "type": 4,
      "len": 1,
      "data": "01"
    },
    {
      "id": 50,
      "type": 0,
      "len": 2,
      "data": "0124"
    }
  ]
}
```

### Command Types

| cmd | Direction | Description |
|-----|-----------|-------------|
| 4 | App → Device | Write command (set values) |
| 5 | Device → App | Response with current values |
| 9 | App → Device | Query request (read values) |

### Connection Sequence (From Packet Capture)

1. **Connect to MQTT broker** with shared credentials
2. **Subscribe to topics** (initial subscription):
   - `/device/OTA/notify_ready_to_update/{deviceId}`
   - `/will/{deviceId}`
3. **Receive will message** - Device online status
4. **Send initial query** - Query DP 1 (cleaning mode)
5. **Subscribe to data topics**:
   - `/device/DATA/send_transparent_data/{deviceId}`
   - `/device/OTA/post_update_progress/{deviceId}`
6. **Send additional queries** (in rapid succession, <1ms apart):
   - Query DP 79
   - Query DP 1
   - Query DP 0
   - Query DP 77

---

## HTTP REST API

### Base URL
```
https://api.wybotpool.com
```

### Discovered Endpoints

From libapp.so string extraction:

#### User Management
- `POST /api/user/login` - User authentication
- `POST /api/user/register` - New user registration
- `POST /api/user/reset` - Password reset initiation
- `POST /api/user/reset/` - Password reset confirmation
- `GET /api/user` - Get user information
- `POST /api/user/cancellation` - Account deletion
- `GET /api/user/notification` - Get notifications
- `GET /api/user/message` - Get user messages

#### Device Management
- `GET /api/group/{userId}` - Get all device groups for user
- `POST /api/device/bind` - Bind new device to account
- `POST /api/device/release` - Release/unbind device
- `POST /api/device/ao` - Send command (alternative endpoint)

#### Group Management
- `GET /api/group` - Get device groups
- `POST /api/group/remove` - Remove a group

#### Environment/Pool
- `GET /api/env/pool` - Get pool information
- `POST /api/env/pool` - Create/update pool

#### Firmware
- `GET /api/firmware/release` - Get firmware release info

#### Vision/Camera
- `POST /api/vision` - Vision camera operations

#### Customer Support
- `POST /api/cs/feedback` - Submit user feedback
- `POST /api/cs/uploadvideo` - Upload support video

### Data Models

From package references in libapp.so:

**User Models:**
- `UserInfoModel`
- `UserInfoEditModel`
- `UserRestPasswordModel`
- `UserDeviceBindModel`
- `UserGroupBindModel`
- `UserGroupItemModel`
- `UserNotificationDataModel`
- `UserMessageOptModel`
- `UserFeedbackModel`

**Device Models:**
- `DeviceInfoEditModel`
- `DeviceListModel`
- `DeviceReleaseInfoModel`
- `DeviceOnlineStateModel`

**MQTT Models:**
- `SendPublishDataModel`
- `ReceiveSubscribeDataModel`
- `OtaNoticeDataModel`
- `OtaUpgradeProgressDataModel`
- `OtaUpgradeErrorCodeDataModel`
- `OtaUpgradVersionDataModel`

**Other Models:**
- `PoolEnvModel`
- `PoolAddModel`
- `PoolDeleteModel`
- `WeatherDataModel`
- `UploadImageModel`
- `DeleteDeviceModel`
- `DeleteAccountModel`

---

## Data Point (DP) System

The WYBOT system uses Data Points (DP) to represent device attributes and controls.

### DP Structure

Each DP consists of:
- **id**: Integer identifier (0-255)
- **type**: Data type (0-5)
- **len**: Length of data in bytes
- **data**: Hex-encoded value

### Known Data Points

| DP ID | Name/Purpose | Type | Observed Values | Description |
|-------|--------------|------|-----------------|-------------|
| 0 | Running/Cleaning Status | 4 | `01`, `03` | Device operational state |
| 1 | Cleaning Mode | 4 | `00`-`06` | Selected cleaning program |
| 13 | Unknown | 5 | `80840000` | Purpose unknown |
| 50 (0x32) | Battery Status | 0 | `0124`, `0164` | Battery state + level |
| 77 (0x4D) | Unknown Schedule/Config | 0 | 36 bytes | Possibly schedule data |
| 79 (0x4F) | Unknown Device Info | 2 | 12 bytes | Possibly firmware/device info |
| 131 (0x83) | Working Time | 2 | 4 bytes | Total working time |
| 209 (0xD1) | Sonar State | 4 | `03` | Sonar sensor status |
| 213 (0xD5) | Docker State | 4 | `01` | Docking station state |
| 214 (0xD6) | Delay Value | 4 | `00` | Timer delay setting |

### Additional DP IDs from String Extraction

From BLE debug messages (may not all be MQTT):
- **0xCE (206)**: Cleaning depth range
- **0xCF (207)**: Auto run mode
- **0xD1 (209)**: Sonar state
- **0xD4 (212)**: In water state
- **0xD5 (213)**: Docker state
- **0xD6 (214)**: Delay value
- **0xDD (221)**: Charging state
- **0xDE (222)**: Low power mode
- **0x91 (145)**: Heavy dirt mode
- **0x46 (70)**: System time

### DP Data Types

| Type | Format | Description |
|------|--------|-------------|
| 0 | Raw | Raw bytes, hex-encoded |
| 2 | Raw | Raw bytes (multi-byte values) |
| 4 | Integer | Simple integer value, hex-encoded |
| 5 | String | String value, hex-encoded |

### Battery Status (DP 50)

DP 50 encodes two values in 4 hex characters (2 bytes):

**Format:** `XXYY`
- **XX**: Charging state
  - `00` = Not charging
  - `01` = Charging
  - `02` = Fully charged
- **YY**: Battery percentage (hex 00-64 = 0-100%)

**Examples:**
- `0124` = Charging, 36% battery (0x24 = 36 decimal)
- `0164` = Charging, 100% battery (0x64 = 100 decimal)
- `0250` = Fully charged, 80% battery (0x50 = 80 decimal)

### Cleaning Status (DP 0)

From packet captures:
- `01` = Cleaning/Running
- `03` = Stopped (or another mode)

**Note:** The actual status codes need further verification. The mapping may vary by device model.

### Cleaning Mode (DP 1)

From string extraction, available modes:
- Floor cleaning
- Wall cleaning
- Wall then Floor
- Standard Full-Pool
- Water Line
- Strong Floor
- Eco Floor

Observed value in captures: `03` (likely Standard Full-Pool mode)

---

## Message Examples from Packet Captures

### Example 1: Initial Connection and Query Sequence

**Time:** 2025-12-21 15:06:38

**Step 1: Subscribe to initial topics**
```
SUBSCRIBE: /device/OTA/notify_ready_to_update/535032302d2d34b7dae70b66
SUBSCRIBE: /will/535032302d2d34b7dae70b66
```

**Step 2: Device publishes online status**
```
TOPIC: /will/535032302d2d34b7dae70b66
PAYLOAD: {"online": "1"}
```

**Step 3: App sends initial query (DP 1 - cleaning mode)**
```
TOPIC: /device/DATA/recv_transparent_query_data/535032302d2d34b7dae70b66
PAYLOAD:
{
  "ts": 1885215978,
  "dp": [{"id": 1}],
  "cmd": 9
}
```

**Step 4: Subscribe to data topics**
```
SUBSCRIBE: /device/DATA/send_transparent_data/535032302d2d34b7dae70b66
SUBSCRIBE: /device/OTA/post_update_progress/535032302d2d34b7dae70b66
```

**Step 5: App sends rapid queries (<1ms apart)**

Query DP 79:
```json
{
  "cmd": 9,
  "dp": [{"id": 79}],
  "ts": 1885215979
}
```

Query DP 1:
```json
{
  "cmd": 9,
  "dp": [{"id": 1}],
  "ts": 1885215980
}
```

Query DP 0:
```json
{
  "cmd": 9,
  "dp": [{"id": 0}],
  "ts": 1885215981
}
```

Query DP 77:
```json
{
  "cmd": 9,
  "dp": [{"id": 77}],
  "ts": 1885215984
}
```

### Example 2: Device Response with Multiple Data Points

```
TOPIC: /device/DATA/send_transparent_data/535032302d2d34b7dae70b66
PAYLOAD:
{
  "cmd": 5,
  "ts": 1885119994,
  "dp": [
    {
      "id": 0,
      "type": 4,
      "len": 1,
      "data": "01"
    },
    {
      "id": 50,
      "type": 0,
      "len": 2,
      "data": "0124"
    },
    {
      "id": 131,
      "type": 2,
      "len": 4,
      "data": "4a000000"
    },
    {
      "id": 209,
      "type": 4,
      "len": 1,
      "data": "03"
    },
    {
      "id": 213,
      "type": 4,
      "len": 1,
      "data": "01"
    },
    {
      "id": 214,
      "type": 4,
      "len": 1,
      "data": "00"
    },
    {
      "id": 13,
      "type": 5,
      "len": 4,
      "data": "80840000"
    }
  ]
}
```

**Interpretation:**
- DP 0 = `01` → Device is cleaning
- DP 50 = `0124` → Charging, 36% battery
- DP 131 = `4a000000` → Working time (74 units, possibly minutes)
- DP 209 = `03` → Sonar state
- DP 213 = `01` → Docker state (connected/docked)
- DP 214 = `00` → No delay set
- DP 13 = `80840000` → Unknown system value

### Example 3: Cleaning Mode Response

```
TOPIC: /device/DATA/send_transparent_data/535032302d2d34b7dae70b66
PAYLOAD:
{
  "cmd": 5,
  "ts": 1885215979,
  "dp": [
    {
      "id": 1,
      "type": 4,
      "len": 1,
      "data": "03"
    }
  ]
}
```

**Interpretation:**
- DP 1 = `03` → Cleaning mode #3 (likely "Standard Full-Pool")

### Example 4: DP 77 Extended Data

```json
{
  "cmd": 5,
  "ts": 1885215984,
  "dp": [
    {
      "id": 77,
      "type": 0,
      "len": 36,
      "data": "7f0900000003090000000309000000030900000003090000000309000000030"
    }
  ]
}
```

**Interpretation:**
DP 77 contains 36 bytes of data. Pattern suggests it might be a schedule or configuration with repeated blocks (`09000000 03` appears 7 times).

### Example 5: DP 79 Device Info

```json
{
  "cmd": 5,
  "ts": 1885215979,
  "dp": [
    {
      "id": 79,
      "type": 2,
      "len": 12,
      "data": "01072f010707020201070700"
    }
  ]
}
```

**Interpretation:**
12 bytes that might encode firmware version, hardware revision, or device capabilities.

---

## Device Models

From string extraction, the following device types are referenced in the app:

### Cleaner Models
- S1
- S2 (including Solar variants)
- S3
- WY200BW
- WY3052
- HJ4042
- 3092
- 3312
- 350sp
- SP20
- M2
- OS600
- A1
- C2

### Docking Station Models
- DS20

---

## Additional Information

### Weather Integration
The app integrates with OpenWeatherMap:
```
https://api.openweathermap.org/data/2.5/weather
```

### Support Resources
- Website: `https://wybotpool.com`
- EU Website: `https://www.eu.wybotpool.com`
- Email: `support@wybotpool.com`

### User Agent
When making HTTP API calls, the app uses:
```
User-Agent: WYBOT/13 CFNetwork/1498.700.2 Darwin/23.6.0
```
(Note: This was found in the Python code, not verified in packet captures)

---

## Implementation Notes

1. **Query Timing:** The app sends DP queries in very rapid succession (<1ms between queries), not with delays.

2. **Subscription Pattern:** The app subscribes to will/OTA topics first, waits for device online status, then queries and subscribes to data topics.

3. **Client ID:** Each app instance generates a random client ID with the prefix `iWY-`.

4. **Will Message:** The MQTT connection includes a Last Will and Testament on the `/will` topic to detect app disconnection.

5. **Device IDs:** Device IDs appear to be 24-character hex strings (e.g., `535032302d2d34b7dae70b66`).

6. **Data Encoding:** All DP data values are hex-encoded strings, regardless of the data type.

7. **Timestamps:** Unix epoch timestamps in milliseconds (integer, not float).

---

**Document Version:** 2.0
**Last Updated:** 2025-12-21
**Data Sources:**
- WYBOT Android App (com.wy.winnygo) v2.1.14
- libapp_arm64.so string extraction
- Network packet captures (app open.pcapng, appopen_online.pcapng, appopen_cleaning.pcapng)

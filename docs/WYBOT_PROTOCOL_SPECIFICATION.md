# WYBOT Protocol Complete Specification

**Source:** WYBOT Android App v2.1.14 (com.wy.winnygo) - Extracted from Flutter App ONLY
**Date:** 2025-12-21
**Methods:** APK decompilation, libapp_arm64.so string extraction, MQTT packet capture analysis

---

## Command Types

Based on packet capture analysis, there are exactly **3 command types**:

| cmd | Name | Direction | Purpose | Observed Count |
|-----|------|-----------|---------|----------------|
| 4 | WRITE | App → Device | Send control commands to change device state | 0 (not in captures, inferred) |
| 5 | RESPONSE | Device → App | Device sends current values or acknowledges commands | 19 |
| 9 | QUERY | App → Device | Request current values of specific DPs | 5 |

### Command Structure

All commands use identical JSON structure:

```json
{
  "cmd": <number>,
  "ts": <unix_timestamp_ms>,
  "dp": [<dp_array>]
}
```

**Fields:**
- `cmd`: Command type (4, 5, or 9)
- `ts`: Unix timestamp in milliseconds (integer)
- `dp`: Array of Data Point objects

### DP Object Structure

#### Query (cmd=9) - Minimal Structure
```json
{
  "id": <dp_id>
}
```

#### Write (cmd=4) & Response (cmd=5) - Full Structure
```json
{
  "id": <dp_id>,
  "type": <data_type>,
  "len": <data_length>,
  "data": "<hex_string>"
}
```

---

## Data Types

Based on observed packets and libapp.so model references:

| Type | Name | Encoding | Description | Example |
|------|------|----------|-------------|---------|
| 0 | RAW | Hex string | Raw bytes, no special interpretation | `"0124"` |
| 2 | RAW_MULTI | Hex string | Multi-byte raw values | `"4a000000"` |
| 4 | INTEGER | Hex string | Simple integer, convert hex to decimal | `"03"` |
| 5 | STRING | Hex string | String data, hex-encoded | `"80840000"` |

**Important:** All data values are hex-encoded strings, even for type 4 (integers).

---

## Complete Data Point (DP) Map

### DPs from Packet Captures (MQTT - Verified)

| DP ID | Hex | Type | Len | Name | Decoding | Example |
|-------|-----|------|-----|------|----------|---------|
| 0 | 0x00 | 4 | 1 | Running/Cleaning Status | `"01"` = 1 (cleaning)<br/>`"03"` = 3 (stopped) | `"01"` |
| 1 | 0x01 | 4 | 1 | Cleaning Mode | `"00"`-`"06"` = mode 0-6 | `"03"` |
| 13 | 0x0D | 5 | 4 | System Value | Unknown purpose | `"80840000"` |
| 50 | 0x32 | 0 | 2 | Battery Status | Byte 1: charge state<br/>Byte 2: percentage (0-100) | `"0124"` |
| 77 | 0x4D | 0 | 36 | Schedule/Config | 36 bytes, repeating pattern | `"7f09..."` |
| 79 | 0x4F | 2 | 12 | Device Info | 12 bytes, firmware/hardware info | `"01072f..."` |
| 131 | 0x83 | 2 | 4 | Working Time | 32-bit little-endian integer (minutes) | `"4a000000"` |
| 209 | 0xD1 | 4 | 1 | Sonar State | State value | `"03"` |
| 213 | 0xD5 | 4 | 1 | Docker/Dock State | Docking station state | `"01"` |
| 214 | 0xD6 | 4 | 1 | Delay Value | Timer delay | `"00"` |

### Additional DPs from Debug Strings (BLE/Unverified)

These are referenced in the app but not confirmed in MQTT captures:

| DP ID | Hex | Source | Description |
|-------|-----|--------|-------------|
| 70 | 0x46 | parseBLEData | System Time |
| 72 | 0x48 | parseDataById | Unknown pool parameter |
| 73 | 0x49 | parseDataById | Unknown pool parameter |
| 74 | 0x4A | parseDataById | Unknown pool parameter |
| 75 | 0x4B | parseDataById | Unknown pool parameter |
| 142 | 0x8E | parseBLEData | pH Data + Temperature Data |
| 145 | 0x91 | Debug string | Heavy Dirt Mode |
| 206 | 0xCE | parseBLEData | Cleaning Depth Range |
| 207 | 0xCF | parseBLEData | Auto Run Mode |
| 212 | 0xD4 | Debug string | In Water State |
| 221 | 0xDD | Debug string | Charging State (MQTT delay check) |
| 222 | 0xDE | Debug string | Low Power Value (MQTT delay check) |
| ? | ? | parseBLEData | Vision Mode Enable |
| ? | ? | parseBLEData | Manual Control Auto Return Back |

---

## DP Decoding Details

### DP 0 - Running/Cleaning Status (Type 4, Len 1)

**Encoding:** Single byte hex

**Values:**
- `"01"` = 0x01 = 1 decimal → **Cleaning/Running**
- `"03"` = 0x03 = 3 decimal → **Stopped**

**Usage:** Indicates whether device is actively cleaning

---

### DP 1 - Cleaning Mode (Type 4, Len 1)

**Encoding:** Single byte hex

**Known Modes (from string extraction):**
- `"00"` = 0 → Floor
- `"01"` = 1 → Wall
- `"02"` = 2 → Wall then Floor
- `"03"` = 3 → Standard Full-Pool
- `"04"` = 4 → Water Line
- `"05"` = 5 → Strong Floor
- `"06"` = 6 → Eco Floor

**Observed Value:** `"03"` (Standard Full-Pool)

---

### DP 13 - System Value (Type 5, Len 4)

**Encoding:** 4-byte hex string

**Observed Value:** `"80840000"` = [0x80, 0x84, 0x00, 0x00]

**Purpose:** Unknown. Possibly system flags or configuration.

---

### DP 50 - Battery Status (Type 0, Len 2)

**Encoding:** 2-byte hex string `XXYY`

**Byte 1 (XX) - Charging State:**
- `00` = Not charging
- `01` = Charging
- `02` = Fully charged

**Byte 2 (YY) - Battery Percentage:**
- Hex value 00-64 = 0-100 decimal percentage

**Examples:**
- `"0124"` → Charging, 36% (0x24 = 36 decimal)
- `"0164"` → Charging, 100% (0x64 = 100 decimal)
- `"0250"` → Fully charged, 80% (0x50 = 80 decimal)

---

### DP 77 - Schedule/Config (Type 0, Len 36)

**Encoding:** 36-byte hex string

**Observed Pattern:**
```
7f 09 00 00 00 03
09 00 00 00 03 09
00 00 00 03 09 00
00 00 03 09 00 00
00 03 09 00 00 00
03 09 00 00 00 03
```

**Analysis:** Repeating pattern `09 00 00 00 03` appears 7 times (for 7 days?). Likely weekly schedule configuration.

---

### DP 79 - Device Info (Type 2, Len 12)

**Encoding:** 12-byte hex string

**Observed Value:** `"01072f010707020201070700"`

**Bytes:** `[01, 07, 2f, 01, 07, 07, 02, 02, 01, 07, 07, 00]`
**Decimal:** `[1, 7, 47, 1, 7, 7, 2, 2, 1, 7, 7, 0]`

**Hypothesis:** Firmware version, hardware revision, or device capabilities encoding.

---

### DP 131 - Working Time (Type 2, Len 4)

**Encoding:** 32-bit little-endian integer (minutes)

**Observed Value:** `"4a000000"`

**Decoding:**
```
Hex: 4a 00 00 00
Little-endian 32-bit: 0x0000004a = 74 decimal
= 74 minutes = 1 hour 14 minutes
```

**Purpose:** Total working time counter

---

### DP 209 - Sonar State (Type 4, Len 1)

**Encoding:** Single byte hex

**Observed Value:** `"03"` = 3 decimal

**Purpose:** Sonar sensor state/status

---

### DP 213 - Docker/Dock State (Type 4, Len 1)

**Encoding:** Single byte hex

**Observed Value:** `"01"` = 1 decimal

**Purpose:** Docking station connection/state
- `01` = Connected/Docked (inferred)

---

### DP 214 - Delay Value (Type 4, Len 1)

**Encoding:** Single byte hex

**Observed Value:** `"00"` = 0 decimal

**Purpose:** Timer delay setting (no delay set)

---

## Message Sequence Patterns

### Initial Connection Sequence

From packet captures at 2025-12-21 15:06:38:

**1. Subscribe Phase (0ms)**
```
SUBSCRIBE: /will/{deviceId}
SUBSCRIBE: /device/OTA/notify_ready_to_update/{deviceId}
```

**2. Device Online (0.2ms later)**
```
PUBLISH: /will/{deviceId}
{"online": "1"}
```

**3. Initial Query (~6 seconds later)**
```
PUBLISH: /device/DATA/recv_transparent_query_data/{deviceId}
{
  "cmd": 9,
  "ts": 1885215978,
  "dp": [{"id": 1}]
}
```

**4. Subscribe to Data Topics (1.3ms later)**
```
SUBSCRIBE: /device/DATA/send_transparent_data/{deviceId}
SUBSCRIBE: /device/OTA/post_update_progress/{deviceId}
```

**5. Rapid Query Burst (<1ms between each)**
```
QUERY DP 79  (ts: 1885215979)
QUERY DP 1   (ts: 1885215980)
QUERY DP 0   (ts: 1885215981)
QUERY DP 77  (ts: 1885215984)
```

**Query Timing Analysis:**
- DP 1 to DP 79: 1ms
- DP 79 to DP 1 (2nd): 1ms
- DP 1 to DP 0: 1ms
- DP 0 to DP 77: 3ms

---

## Response Patterns

### Single DP Response

When querying a single DP, device responds with just that DP:

**Query:**
```json
{
  "cmd": 9,
  "dp": [{"id": 1}],
  "ts": 1885119988
}
```

**Response:**
```json
{
  "cmd": 5,
  "ts": 1885119989,
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

### Batch Response

Device can respond with multiple DPs in one message:

```json
{
  "cmd": 5,
  "ts": 1885119994,
  "dp": [
    {"id": 0, "type": 4, "len": 1, "data": "01"},
    {"id": 50, "type": 0, "len": 2, "data": "0124"},
    {"id": 131, "type": 2, "len": 4, "data": "4a000000"},
    {"id": 209, "type": 4, "len": 1, "data": "03"},
    {"id": 213, "type": 4, "len": 1, "data": "01"},
    {"id": 214, "type": 4, "len": 1, "data": "00"},
    {"id": 13, "type": 5, "len": 4, "data": "80840000"}
  ]
}
```

### Duplicate Responses

Device may send the same DP multiple times (observed: DP 1 sent 5+ times consecutively).

---

## Write Commands (cmd=4)

**Not observed in packet captures**, but structure can be inferred:

```json
{
  "cmd": 4,
  "ts": <timestamp>,
  "dp": [
    {
      "id": <dp_id>,
      "type": <type>,
      "len": <length>,
      "data": "<hex_value>"
    }
  ]
}
```

**Example - Start Cleaning:**
```json
{
  "cmd": 4,
  "ts": 1885120000,
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

**Example - Change to Floor Mode:**
```json
{
  "cmd": 4,
  "ts": 1885120000,
  "dp": [
    {
      "id": 1,
      "type": 4,
      "len": 1,
      "data": "00"
    }
  ]
}
```

---

## BLE vs MQTT Data Points

The app uses **both** BLE (Bluetooth Low Energy) and MQTT for communication:

### MQTT Data Points (Cloud/Remote)
DPs observed in MQTT packet captures:
- 0, 1, 13, 50, 77, 79, 131, 209, 213, 214

### BLE Data Points (Local/Bluetooth)
DPs referenced only in BLE parsing code:
- 70 (0x46) - System Time
- 72-75 (0x48-0x4B) - Pool parameters
- 142 (0x8E) - pH & Temperature
- 145 (0x91) - Heavy Dirt Mode
- 206 (0xCE) - Cleaning Depth
- 207 (0xCF) - Auto Run Mode
- 212 (0xD4) - In Water State
- Vision Mode Enable
- Manual Control Auto Return

**Note:** BLE DPs may have different encoding or may not be available via MQTT.

---

## Data Models from Flutter App

### MQTT Message Models

From `package:WYBOT/mqt/` references:

**SendPublishDataModel** (App → Device)
- Fields: `_cmd`, `_ts`, `_dp`
- Used for cmd=4 (write) and cmd=9 (query)

**ReceiveSubscribeDataModel** (Device → App)
- Fields: `_cmd`, `_ts`, `_dp`
- Used for cmd=5 (response)

**DP Model**
- Fields: `_id`, `_type`, `_len`, `_data`

### OTA Models

- `OtaNoticeDataModel`
- `OtaUpgradeProgressDataModel`
- `OtaUpgradeErrorCodeDataModel`
- `OtaUpgradVersionDataModel`

### Device Models

- `DeviceOnlineStateModel` - Contains `_online` field
- `DeviceInfoEditModel` - Contains `_code` field
- `DeviceListModel`
- `DeviceReleaseInfoModel`

---

## Encoding Rules Summary

### Hex String Encoding

**All DP data is hex-encoded strings:**

1. **Type 4 (Integer):** Single byte or multi-byte hex
   - `"01"` = 1 decimal
   - `"03"` = 3 decimal
   - `"64"` = 100 decimal

2. **Type 0 (Raw Bytes):** Direct hex representation
   - `"0124"` = two bytes [0x01, 0x24]
   - Read byte-by-byte for interpretation

3. **Type 2 (Multi-byte Raw):** Usually integers with specific byte order
   - `"4a000000"` = little-endian 32-bit = 74 decimal

4. **Type 5 (String):** Hex-encoded string data
   - May need additional decoding based on context

### Timestamp Encoding

- Unix epoch time in **milliseconds**
- Integer format (not float)
- Example: `1885119988` = 1885119988 ms since epoch

### Device ID Encoding

- 24-character hex string
- Example: `535032302d2d34b7dae70b66`

---

## Implementation Notes

### Query Strategy

The app queries DPs individually, not in batch:
```
Query DP 1
Query DP 79
Query DP 1  (again!)
Query DP 0
Query DP 77
```

**Important:** Query timing is <1ms between queries, essentially simultaneous.

### Response Handling

- Device may respond to each query individually
- Device may batch multiple responses
- Device may send duplicate responses
- App must handle all patterns

### Data Type Selection

When writing (cmd=4), match the type to what the device uses in responses (cmd=5):
- If device sends type 4, use type 4
- If device sends type 0, use type 0
- Type and len must match device's format

### Error Handling

No error codes observed in packet captures. Failure modes unknown.

---

## Complete Example: Device Status Update Flow

**Scenario:** Device cleaning, 36% battery

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

**Decoded:**
- DP 0 = `01` → **Cleaning**
- DP 50 = `0124` → **Charging, 36% battery**
- DP 131 = `4a000000` → **74 minutes working time**
- DP 209 = `03` → **Sonar state 3**
- DP 213 = `01` → **Docked**
- DP 214 = `00` → **No delay**
- DP 13 = `80840000` → **Unknown system value**

---

**Document Version:** 1.0
**Last Updated:** 2025-12-21
**Data Sources:**
- WYBOT Android App (com.wy.winnygo) v2.1.14
- libapp_arm64.so string extraction
- MQTT packet captures (appopen.pcapng, appopen_online.pcapng, appopen_cleaning.pcapng)
- No Python code referenced - Flutter app data only

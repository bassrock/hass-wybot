# WyBot for Home Assistant

[![GitHub release](https://img.shields.io/github/v/release/bassrock/hass-wybot?style=for-the-badge)](http://github.com/bassrock/hass-wybot/releases/latest)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue?style=for-the-badge)](https://www.python.org)

A Home Assistant custom integration for [WyBot pool vacuums](https://www.wybotpool.com/). Control your pool robot as a Vacuum entity with real-time status, battery monitoring, and solar dock tracking.

## Features

- **Vacuum entity** — Start, stop, return to dock, and select cleaning mode
- **7 cleaning modes** — Floor, Wall, Wall Then Floor, Advanced Full Pool, Water Line, Turbo Floor, Eco Floor
- **Battery monitoring** — Robot battery level and charging status
- **Solar dock support** — Dock battery level, solar charging status, and total energy harvested
- **BLE-first architecture** — Uses local Bluetooth when available, falls back to cloud MQTT
- **Bluetooth discovery** — Automatically detects DS20 docks via BLE
- **Diagnostic sensors** — Data source (BLE/MQTT), last communication timestamps

## Tested Devices

| Device | Status |
|--------|--------|
| S2 Pro + DS20 Solar Dock | ✅ Tested |
| C1 | 🔄 Testing in progress |
| Other WyBot models | Should work — please report issues |

## Entities

The integration creates the following entities per device:

### Vacuum
| Entity | Description |
|--------|-------------|
| Vacuum | Start/stop/return to dock, cleaning mode selection |

### Sensors
| Entity | Description |
|--------|-------------|
| Robot battery | Battery level (%) |
| Dock battery | Solar dock battery level (%) |
| Energy harvested | Total solar energy (Wh) |
| Dock type | Standard or Solar |
| Data source | Current connection method (Bluetooth / Cloud) |
| Last BLE communication | Timestamp of last Bluetooth poll |
| Last MQTT communication | Timestamp of last cloud message |

### Binary Sensors
| Entity | Description |
|--------|-------------|
| Robot charging | Whether the robot is charging |
| Dock charging | Whether the solar panel is actively charging |

### Buttons
| Entity | Description |
|--------|-------------|
| Send WiFi credentials | Send WiFi config to dock via BLE (diagnostic, disabled by default) |

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bassrock&repository=hass-wybot&category=integration)

### Manual

Download the [latest release](https://github.com/bassrock/hass-wybot/releases/latest/download/hass-wybot.zip) and extract to your `config/custom_components` directory.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **WyBot**
3. Enter your WyBot account email and password
4. (Optional) Enter WiFi SSID and password for manual dock provisioning via BLE

> **Bluetooth discovery:** If you have a DS20 dock in Bluetooth range, Home Assistant will automatically detect it and prompt you to set up the integration.

## How It Works

The integration uses a **BLE-first with MQTT fallback** architecture:

1. **Bluetooth (primary)** — Polls the dock via BLE every 30 seconds for instant local status updates
2. **Cloud MQTT (fallback)** — If BLE fails or the device is out of range, connects to WyBot's MQTT broker
3. **HTTP API** — Used for initial device discovery and session keepalive

Commands (start, stop, return to dock) are also sent via BLE first, falling back to MQTT if needed.

## Setting Up a WyBot S2 Pro with a Dock

> **⚠️ Important:** The dock must be set up **before** the robot. You must first set up the dock and connect it to WiFi, then pair the robot with it. There is no way to configure WiFi on the dock after the robot has been paired.

WyBot robots disconnect from WiFi when submerged. The dock stays connected to WiFi and relays commands to the robot underwater via Bluetooth.

## Contributing

See [DEVELOPER.md](DEVELOPER.md) for instructions on running tests and an overview of the codebase architecture.

## Roadmap

- Better MQTT connection handling
- Testing with more robot models
- Bluetooth-only operation (no cloud dependency)

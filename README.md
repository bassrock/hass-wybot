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
| C1 | ✅ Tested |
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

### Configuration parameters

Provided during setup (**Settings → Devices & Services → Add Integration → WyBot**):

| Parameter | Required | Description |
|-----------|----------|-------------|
| Email | Yes | The email address for your WyBot account. |
| Password | Yes | The password for your WyBot account. |
| WiFi network name | No | SSID your dock should join. Only used for manual WiFi provisioning via the diagnostic button. If set, WiFi password is required. |
| WiFi password | No | Password for the WiFi network above. |

The WiFi network name and password can also be changed later without removing the
integration via **Settings → Devices & Services → WyBot → Configure** (the options flow).

Only one config entry per WyBot account is allowed; a single account manages all of
its docks and robots.

### Reauthentication

If your WyBot password changes, the integration detects the invalid credentials and
raises a **Reauthenticate** notification in Home Assistant. Open it and enter the new
password — the entry reloads automatically without needing to be removed and re-added.

## Removing the integration

This integration follows standard Home Assistant removal — no extra steps are needed.

1. Go to **Settings → Devices & Services**.
2. Select the **WyBot** integration.
3. Click the **⋮** menu on the config entry and choose **Delete**.

This removes all WyBot devices and entities. To fully uninstall the code, remove the
integration from HACS (or delete `config/custom_components/wybot/`) and restart Home Assistant.

## How It Works

The integration uses a **BLE-first with MQTT fallback** architecture:

1. **Bluetooth (primary)** — Polls the dock via BLE every 30 seconds for instant local status updates
2. **Cloud MQTT (fallback)** — If BLE fails or the device is out of range, connects to WyBot's MQTT broker
3. **HTTP API** — Used for initial device discovery and session keepalive

Commands (start, stop, return to dock) are also sent via BLE first, falling back to MQTT if needed.

## Supported functionality

Each WyBot robot (and its dock, if present) is exposed as one or more Home Assistant devices with the following platforms. See the [Entities](#entities) tables above for the full per-entity breakdown.

- **`vacuum`** — Start, stop, and return-to-dock control, plus cleaning-mode selection (7 modes).
- **`sensor`** — Robot and dock battery levels, solar energy harvested, dock type, data source (Bluetooth/Cloud), and last-communication timestamps.
- **`binary_sensor`** — Robot charging and solar-dock charging status.
- **`button`** — Send WiFi credentials to the dock via Bluetooth (diagnostic, disabled by default).

## Use cases

- **Scheduled nightly cleaning** — Automatically start the robot on a floor-cleaning cycle each night (or on your preferred days) so the pool is ready in the morning.
- **Notify when cleaning completes** — Send a phone notification when the robot finishes and returns to the dock, so you know it's done without checking the pool.
- **Return to dock on low battery** — Trigger a return-to-dock (or a reminder) when the robot battery drops below a threshold to avoid it stranding mid-pool.
- **Dashboard status at a glance** — Show battery level, charging status, solar energy harvested, and the current data source (Bluetooth vs. Cloud) on a Lovelace dashboard.

## Example automations

These are copy-pasteable starting points. Replace the `entity_id` values with the ones created for your device (find them under **Settings → Devices & Services → WyBot**, or in **Developer Tools → States**).

**Start cleaning every day at 8am**

```yaml
alias: WyBot - Start cleaning at 8am
triggers:
  - trigger: time
    at: "08:00:00"
actions:
  - action: vacuum.start
    target:
      entity_id: vacuum.wybot_s2_pro
mode: single
```

**Notify when the robot battery is fully charged**

```yaml
alias: WyBot - Notify when battery full
triggers:
  - trigger: numeric_state
    entity_id: sensor.wybot_s2_pro_robot_battery
    above: 99
actions:
  - action: notify.notify
    data:
      title: WyBot
      message: The pool robot is fully charged and ready to clean.
mode: single
```

> A ready-made blueprint that bundles scheduling, completion notifications, and battery alerts is described under [Blueprints](#blueprints) below.

## Known limitations

- **The robot goes offline underwater** — WyBot robots disconnect from WiFi when submerged. The dock stays online and relays commands to the robot via Bluetooth, so control while the robot is in the water depends on the dock being powered and in BLE range.
- **Cloud dependency for remote control** — When the dock is out of Bluetooth range of your Home Assistant host, the integration falls back to WyBot's cloud MQTT broker. Remote control and status updates then depend on WyBot's cloud service being reachable.
- **Dock must be set up before the robot** — WiFi provisioning happens on the dock before the robot is paired. See [Setting Up a WyBot S2 Pro with a Dock](#setting-up-a-wybot-s2-pro-with-a-dock) — there is no way to configure the dock's WiFi after the robot has been paired.
- **Bluetooth range** — BLE-first operation requires the dock to be within Bluetooth range of your Home Assistant host or a Bluetooth proxy. Outside that range the integration relies on the cloud fallback.

## Troubleshooting

**A device shows as `unavailable`**
- Check that the dock is powered on and within Bluetooth range of your Home Assistant host (or a Bluetooth proxy).
- If relying on the cloud fallback, confirm your Home Assistant host has internet access and WyBot's cloud service is reachable.
- Check the **Data source** and **Last BLE/MQTT communication** diagnostic sensors to see how (and when) the device was last reached.

**Commands (start/stop/return to dock) don't work**
- Commands are sent over Bluetooth first, then the cloud. If the dock is at the edge of BLE range, try again — a transient BLE write can fail and the next attempt often succeeds.
- Confirm the dock is online (see above) and that the robot is docked or in the water within range of the dock.

**A reauthentication prompt appears**
- If your WyBot password changes, Home Assistant raises a **Reauthenticate** notification. Open it and enter the new password; the entry reloads automatically. See [Reauthentication](#reauthentication).

**Enabling debug logging**

Add the following to your `configuration.yaml` and restart Home Assistant to capture detailed logs for bug reports:

```yaml
logger:
  default: info
  logs:
    custom_components.wybot: debug
    wybot: debug
```

## Setting Up a WyBot S2 Pro with a Dock

> **⚠️ Important:** The dock must be set up **before** the robot. You must first set up the dock and connect it to WiFi, then pair the robot with it. There is no way to configure WiFi on the dock after the robot has been paired.

WyBot robots disconnect from WiFi when submerged. The dock stays connected to WiFi and relays commands to the robot underwater via Bluetooth.

## Blueprints

A comprehensive "All-in-One" automation blueprint is included in the `blueprints/` directory:

| Blueprint | Description |
|-----------|-------------|
| **WyBot: Pool Robot Notifications & Schedule** | A powerful multi-function blueprint that handles cleaning completion notifications, full charge alerts, low battery warnings, and daily cleaning schedules with mode selection and battery checks. |

To import the blueprint, copy `blueprints/wybot_all_in_one.yaml` to your Home Assistant `config/blueprints/automation/wybot/` directory, or use the **Import Blueprint** feature in the HA UI.

## Contributing

See [DEVELOPER.md](DEVELOPER.md) for instructions on running tests and an overview of the codebase architecture.

## Roadmap

- Better MQTT connection handling
- Testing with more robot models
- Bluetooth-only operation (no cloud dependency)

# WyBot For HomeAssistant

[![GitHub release](https://img.shields.io/github/v/release/bassrock/hass-wybot?style=for-the-badge)](http://github.com/bassrock/hass-wybot/releases/latest)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

A Home Assistant custom integration to interact with [WyBot's Pool vacumms](https://www.wybotpool.com/) which allows controlling of WyBot pool vaccums as Vacumm entities in HomeAssistant.

Note: This has only been tested with 1 S2 Pro with the docking station and requires the use of the Cloud API which connects to WyBot's MQTT broker for communication with the Robot.

## Installation

The easiest way to install this integration is by using [HACS](https://hacs.xyz).

If you have HACS installed, you can add the Vantage integration by using this My button:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bassrock&repository=hass-wybot&category=integration)

<details>
<summary>
<h4>Manual installation</h4>
</summary>

If you aren't using HACS, you can download the [latest release](https://github.com/bassrock/hass-wybot/releases/latest/download/hass-wybot.zip) and extract the contents to your Home Assistant `config/custom_components` directory.
</details>

## Setting up a WyBot S2 Pro with a Dock

WyBots disconnect from WiFI when they are in the water. However if you have a dock, the dock will stay connected to WiFi and relay commands underwater to the WyBot. HOWEVER, the order you setup the Dock & Wybot matter. YOU MUST first setup the dock, and connect it to WiFi then pair your robot with it. There is no way to set the wifi on the Dock after you setup the WyBot.

## Future

* Better connection handling with MQTT
* Enabling Bluetooth communication
* Testing with more robots.

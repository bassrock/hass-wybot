"""The WyBot integration."""

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import CONF_WIFI_PASSWORD, CONF_WIFI_SSID, DOMAIN
from .wybot_ble_client import WyBotBLEClient
from .wybot_coordinator import WyBotCoordinator
from .wybot_http_client import WyBotHTTPClient

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR, Platform.VACUUM]
_LOGGER = logging.getLogger(__name__)

# Service names
SERVICE_BLE_SCAN = "ble_scan"
SERVICE_BLE_DISCOVER = "ble_discover"
SERVICE_BLE_WAKE = "ble_wake"
SERVICE_BLE_QUERY = "ble_query"

# Service schema
SERVICE_BLE_ADDRESS_SCHEMA = vol.Schema({
    vol.Required("ble_address"): cv.string,
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up WyBot from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    wybot_http_client = WyBotHTTPClient(
        entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )

    authed = await hass.async_add_executor_job(wybot_http_client.authenticate)
    if not authed:
        return False

    # Create coordinator with config entry for WiFi credentials
    coordinator = WyBotCoordinator(
        hass, wybot_http_client=wybot_http_client, config_entry=entry
    )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register cleanup using entry.async_on_unload pattern
    async def _async_stop_coordinator() -> None:
        """Stop coordinator resources on unload."""
        await coordinator.async_stop()

    entry.async_on_unload(_async_stop_coordinator)

    # Listen for options updates to refresh WiFi credentials
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update.

    Called when config entry options are updated via the UI.
    Updates stored WiFi credentials for manual provisioning via diagnostic button.
    """
    coordinator: WyBotCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Update WiFi credentials directly (for manual provisioning only)
    coordinator._wifi_ssid = entry.data.get(CONF_WIFI_SSID)
    coordinator._wifi_password = entry.data.get(CONF_WIFI_PASSWORD)
    if coordinator._wifi_ssid:
        _LOGGER.debug(
            "WiFi credentials updated for SSID: %s", coordinator._wifi_ssid
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Note: coordinator.async_stop() is called via entry.async_on_unload
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the WyBot component and register services."""
    hass.data.setdefault(DOMAIN, {})

    async def handle_ble_scan(call: ServiceCall) -> ServiceResponse:
        """Handle BLE scan service call."""
        from homeassistant.components.bluetooth import async_discovered_service_info

        _LOGGER.info("=== WyBot BLE Scan ===")

        try:
            service_infos = async_discovered_service_info(hass, connectable=True)
            devices_found = []
            wybot_devices = []

            for info in service_infos:
                device = info.device
                device_info = {
                    "name": device.name or "Unknown",
                    "address": device.address,
                    "rssi": info.rssi if hasattr(info, "rssi") else None,
                }
                devices_found.append(device_info)

                # Check if it's a WyBot device
                if device.name and len(device.name) == 12:
                    if all(c in "0123456789ABCDEFabcdef" for c in device.name):
                        wybot_devices.append(device_info)
                        _LOGGER.info(
                            "  [WYBOT] %s at %s (RSSI: %s)",
                            device.name,
                            device.address,
                            device_info["rssi"],
                        )

            _LOGGER.info("Total BLE devices: %d, WyBot devices: %d", len(devices_found), len(wybot_devices))

            return {
                "total_devices": len(devices_found),
                "wybot_devices": wybot_devices,
                "all_devices": devices_found[:20],  # Limit to avoid large response
            }

        except Exception as err:
            _LOGGER.error("BLE scan failed: %s", err)
            return {"error": str(err)}

    async def handle_ble_discover(call: ServiceCall) -> ServiceResponse:
        """Handle BLE discover services call."""
        ble_address = call.data["ble_address"]
        ble_client = WyBotBLEClient(hass)

        _LOGGER.info("=== WyBot BLE Service Discovery for %s ===", ble_address)

        # Scan to find the device first
        device = await ble_client.scan_for_device(ble_address)

        if not device:
            _LOGGER.warning("Device %s not found in scan", ble_address)
            return {"error": f"Device {ble_address} not found"}

        # Use the internal method to discover services
        from homeassistant.components.bluetooth import async_ble_device_from_address
        from bleak import BleakClient
        from bleak_retry_connector import establish_connection

        try:
            ble_device = async_ble_device_from_address(hass, device.address, connectable=True)
            if not ble_device:
                return {"error": "Could not get BLE device handle"}

            client = await establish_connection(
                BleakClient,
                ble_device,
                ble_device.name or ble_device.address,
                max_attempts=2,
            )

            try:
                discovery = await ble_client._log_device_services(client)
                return {
                    "address": device.address,
                    "name": getattr(device, "name", "Unknown"),
                    "services": discovery["services"],
                    "found_wybot_service": discovery["found_primary_service"],
                }
            finally:
                if client.is_connected:
                    await client.disconnect()

        except Exception as err:
            _LOGGER.error("Service discovery failed: %s", err)
            return {"error": str(err)}

    async def handle_ble_wake(call: ServiceCall) -> ServiceResponse:
        """Handle BLE wake service call."""
        ble_address = call.data["ble_address"]
        ble_client = WyBotBLEClient(hass)

        _LOGGER.info("=== WyBot BLE Wake for %s ===", ble_address)

        success = await ble_client.wake_device(ble_address)

        return {
            "address": ble_address,
            "success": success,
            "message": "Wake sequence complete" if success else "Wake failed",
        }

    async def handle_ble_query(call: ServiceCall) -> ServiceResponse:
        """Handle BLE query status call."""
        ble_address = call.data["ble_address"]
        ble_client = WyBotBLEClient(hass)

        _LOGGER.info("=== WyBot BLE Query for %s ===", ble_address)

        dps = await ble_client.query_status(ble_address)

        if dps:
            return {
                "address": ble_address,
                "success": True,
                "data_points": dps,
            }
        return {
            "address": ble_address,
            "success": False,
            "error": "No response received",
        }

    # Register services
    hass.services.async_register(
        DOMAIN,
        SERVICE_BLE_SCAN,
        handle_ble_scan,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_BLE_DISCOVER,
        handle_ble_discover,
        schema=SERVICE_BLE_ADDRESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_BLE_WAKE,
        handle_ble_wake,
        schema=SERVICE_BLE_ADDRESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_BLE_QUERY,
        handle_ble_query,
        schema=SERVICE_BLE_ADDRESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    _LOGGER.info("WyBot BLE diagnostic services registered")

    # Run automatic BLE scan on startup for diagnostics
    async def run_startup_ble_scan():
        """Run BLE scan on startup for diagnostics."""
        await asyncio.sleep(10)  # Wait for Bluetooth to initialize
        _LOGGER.info("=== Running startup BLE scan ===")
        try:
            from homeassistant.components.bluetooth import async_discovered_service_info, async_scanner_count

            scanner_count = async_scanner_count(hass, connectable=True)
            _LOGGER.info("Bluetooth scanner count: %d", scanner_count)

            service_infos = async_discovered_service_info(hass, connectable=True)
            _LOGGER.info("Total discovered BLE devices: %d", len(service_infos))

            for info in service_infos:
                device = info.device
                is_wybot = False
                if device.name and len(device.name) == 12:
                    if all(c in "0123456789ABCDEFabcdef" for c in device.name):
                        is_wybot = True

                marker = " [WYBOT]" if is_wybot else ""
                rssi = getattr(info, "rssi", "N/A")
                _LOGGER.info(
                    "  BLE Device%s: name=%s, address=%s, rssi=%s",
                    marker,
                    device.name or "Unknown",
                    device.address,
                    rssi,
                )

        except Exception as err:
            _LOGGER.error("Startup BLE scan error: %s", err)

    # Schedule the startup scan
    hass.async_create_task(run_startup_ble_scan())

    # Add a service to trigger start cleaning via BLE
    async def handle_ble_start_cleaning(call: ServiceCall) -> ServiceResponse:
        """Handle BLE start cleaning service call."""
        ble_address = call.data.get("ble_address", "3C8427565A1A")  # Default to dock

        _LOGGER.info("=== BLE Start Cleaning Command ===")
        _LOGGER.info("Target device: %s", ble_address)

        try:
            from .wybot_dp_models import CleaningStatus, CleaningStatusMode

            ble_client = WyBotBLEClient(hass)

            # Create start cleaning command
            start_cmd = CleaningStatus()
            start_cmd.status = CleaningStatusMode.CLEANING

            _LOGGER.info(
                "Sending: DP id=%d, type=%d, len=%d, data=%s",
                start_cmd.id, start_cmd.type, start_cmd.len, start_cmd.data
            )

            success, response_dps = await ble_client.send_command(ble_address, start_cmd)

            return {
                "address": ble_address,
                "success": success,
                "command": "start_cleaning",
                "dp_sent": {"id": 0, "type": 4, "len": 1, "data": "03"},
                "response_dps": response_dps if response_dps else [],
            }

        except Exception as err:
            _LOGGER.error("BLE start cleaning error: %s", err)
            return {"error": str(err), "success": False}

    async def handle_ble_stop_cleaning(call: ServiceCall) -> ServiceResponse:
        """Handle BLE stop cleaning service call."""
        ble_address = call.data.get("ble_address", "3C8427565A1A")

        _LOGGER.info("=== BLE Stop Cleaning Command ===")

        try:
            from .wybot_dp_models import CleaningStatus, CleaningStatusMode

            ble_client = WyBotBLEClient(hass)

            stop_cmd = CleaningStatus()
            stop_cmd.status = CleaningStatusMode.STOPPED

            success, response_dps = await ble_client.send_command(ble_address, stop_cmd)

            return {
                "address": ble_address,
                "success": success,
                "command": "stop_cleaning",
                "dp_sent": {"id": 0, "type": 4, "len": 1, "data": "01"},
                "response_dps": response_dps if response_dps else [],
            }

        except Exception as err:
            _LOGGER.error("BLE stop cleaning error: %s", err)
            return {"error": str(err), "success": False}

    # Register cleaning control services
    hass.services.async_register(
        DOMAIN,
        "ble_start_cleaning",
        handle_ble_start_cleaning,
        schema=vol.Schema({
            vol.Optional("ble_address", default="3C8427565A1A"): cv.string,
        }),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "ble_stop_cleaning",
        handle_ble_stop_cleaning,
        schema=vol.Schema({
            vol.Optional("ble_address", default="3C8427565A1A"): cv.string,
        }),
        supports_response=SupportsResponse.ONLY,
    )

    _LOGGER.info("WyBot BLE cleaning control services registered")

    return True

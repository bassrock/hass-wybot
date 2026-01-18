"""Config flow for WyBot integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import format_mac

from .const import CONF_WIFI_PASSWORD, CONF_WIFI_SSID, DOMAIN
from .wybot_http_client import WyBotHTTPClient

_LOGGER = logging.getLogger(__name__)

# Key for storing discovered device info in config entry data
CONF_DISCOVERED_DEVICE_ADDRESS = "discovered_device_address"
CONF_DISCOVERED_DEVICE_NAME = "discovered_device_name"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_WIFI_SSID): str,
        vol.Optional(CONF_WIFI_PASSWORD): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    client = WyBotHTTPClient(data[CONF_USERNAME], data[CONF_PASSWORD])

    authed = await hass.async_add_executor_job(client.authenticate)

    if not authed:
        raise InvalidAuth

    # Return info that you want to store in the config entry.
    return {"title": data[CONF_USERNAME]}


class WyBotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for WyBot."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle Bluetooth discovery.

        This is triggered when a DS20 dock is discovered via Bluetooth.
        Only DS20 docks (with prefix "DS20-") are auto-discovered because robots
        advertise as raw MAC addresses which could match other BLE devices.
        """
        _LOGGER.info(
            "Bluetooth discovery: name=%s, address=%s",
            discovery_info.name,
            discovery_info.address,
        )

        # Use the MAC address as unique ID
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()

        # Store discovery info for later use
        self._discovery_info = discovery_info

        # Determine device type from name
        device_type = "DS20 Dock" if discovery_info.name.startswith("DS20-") else "Robot"
        self.context["title_placeholders"] = {"name": f"WyBot {device_type}"}

        # Proceed to user step to collect credentials
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Validate WiFi fields: if SSID is provided, password is required
            wifi_ssid = user_input.get(CONF_WIFI_SSID)
            wifi_password = user_input.get(CONF_WIFI_PASSWORD)
            if wifi_ssid and not wifi_password:
                errors["base"] = "wifi_password_required"
            else:
                try:
                    info = await validate_input(self.hass, user_input)
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected exception")
                    errors["base"] = "unknown"
                else:
                    # Include discovered device info if available
                    entry_data = dict(user_input)
                    if self._discovery_info:
                        entry_data[CONF_DISCOVERED_DEVICE_ADDRESS] = (
                            self._discovery_info.address
                        )
                        entry_data[CONF_DISCOVERED_DEVICE_NAME] = (
                            self._discovery_info.name
                        )
                    return self.async_create_entry(title=info["title"], data=entry_data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class OptionsFlowHandler(OptionsFlow):
    """Handle options flow for WyBot."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the WiFi options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate WiFi fields: if SSID is provided, password is required
            wifi_ssid = user_input.get(CONF_WIFI_SSID)
            wifi_password = user_input.get(CONF_WIFI_PASSWORD)
            if wifi_ssid and not wifi_password:
                errors["base"] = "wifi_password_required"
            else:
                # Update config entry data with new WiFi credentials
                new_data = {**self.config_entry.data}
                if wifi_ssid:
                    new_data[CONF_WIFI_SSID] = wifi_ssid
                    new_data[CONF_WIFI_PASSWORD] = wifi_password
                else:
                    # Remove WiFi credentials if SSID is cleared
                    new_data.pop(CONF_WIFI_SSID, None)
                    new_data.pop(CONF_WIFI_PASSWORD, None)

                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                return self.async_create_entry(title="", data={})

        # Get current values for the form
        current_ssid = self.config_entry.data.get(CONF_WIFI_SSID, "")
        current_password = self.config_entry.data.get(CONF_WIFI_PASSWORD, "")

        options_schema = vol.Schema(
            {
                vol.Optional(CONF_WIFI_SSID, default=current_ssid): str,
                vol.Optional(CONF_WIFI_PASSWORD, default=current_password): str,
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=options_schema, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

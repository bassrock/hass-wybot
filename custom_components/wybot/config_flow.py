"""Config flow for WyBot integration."""

from __future__ import annotations

from collections.abc import Mapping
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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, format_mac

from wybot import WyBotHTTPClient, WybotAuthError, WybotConnectionError

from .const import CONF_WIFI_PASSWORD, CONF_WIFI_SSID, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Key for storing discovered device info in config entry data
CONF_DISCOVERED_DEVICE_ADDRESS = "discovered_device_address"
CONF_DISCOVERED_DEVICE_NAME = "discovered_device_name"

DOCK_NAME_PREFIX = "DS20-"


def _dock_mac_from_local_name(name: str) -> str | None:
    """Return the dock MAC encoded in a ``DS20-<MAC>`` BLE local name.

    Returns ``None`` when the name is not a dock advertisement or does not
    carry a well-formed MAC.
    """
    if not name.startswith(DOCK_NAME_PREFIX):
        return None
    raw = name.removeprefix(DOCK_NAME_PREFIX)
    if len(raw) != 12:
        return None
    try:
        int(raw, 16)
    except ValueError:
        return None
    return format_mac(raw)


def _normalize_mac(mac: str) -> str:
    """Return a MAC stripped of separators and case for comparison."""
    return mac.replace(":", "").replace("-", "").replace(".", "").lower()

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
    client = WyBotHTTPClient(
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        session=async_get_clientsession(hass),
    )

    try:
        await client.authenticate()
    except WybotAuthError as err:
        raise InvalidAuth from err
    except WybotConnectionError as err:
        raise CannotConnect from err

    # Return info that you want to store in the config entry. The user_id
    # uniquely identifies the WyBot account and is used as the entry unique_id.
    return {"title": data[CONF_USERNAME], "user_id": client.user_id}


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

        # The advertising address is not a stable identity for a dock. BLE
        # relays (smart-home panels, some hubs) rebroadcast advertisements they
        # overhear under their own address, and a dock that switches to a
        # resolvable private address changes address on its own. Both make the
        # same physical dock look like new hardware every time. The dock's
        # local name is ``DS20-<factory MAC>``, so prefer that as the identity
        # and fall back to the address only when the name carries no MAC.
        dock_mac = _dock_mac_from_local_name(discovery_info.name)

        # A configured entry is keyed on the WyBot account, not on any one
        # dock, so unique_id alone cannot tell us whether this dock is already
        # covered. Ask the device registry instead.
        if dock_mac is not None and self._async_dock_is_registered(dock_mac):
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(dock_mac or format_mac(discovery_info.address))
        # Older entries were keyed on the dock MAC; keep honoring that, and
        # refresh the stored discovered address/name (they can change) instead
        # of just aborting.
        self._abort_if_unique_id_configured(
            updates={
                CONF_DISCOVERED_DEVICE_ADDRESS: discovery_info.address,
                CONF_DISCOVERED_DEVICE_NAME: discovery_info.name,
            }
        )

        # Store discovery info for later use
        self._discovery_info = discovery_info

        # Determine device type from name
        device_type = (
            "DS20 Dock"
            if discovery_info.name.startswith(DOCK_NAME_PREFIX)
            else "Robot"
        )
        self.context["title_placeholders"] = {"name": f"WyBot {device_type}"}

        # Proceed to user step to collect credentials
        return await self.async_step_user()

    @callback
    def _async_dock_is_registered(self, dock_mac: str) -> bool:
        """Return True when a configured entry already owns this dock.

        Matching is done on the normalized MAC because dock devices created by
        older releases stored their Bluetooth connection uppercased.
        """
        wanted = _normalize_mac(dock_mac)
        device_registry = dr.async_get(self.hass)
        for entry in self._async_current_entries(include_ignore=False):
            for device in dr.async_entries_for_config_entry(
                device_registry, entry.entry_id
            ):
                for conn_type, conn_value in device.connections:
                    if conn_type == CONNECTION_BLUETOOTH and (
                        _normalize_mac(conn_value) == wanted
                    ):
                        return True
        return False

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
                    # Key the entry on the WyBot account so the same account
                    # cannot be configured twice (unique-config-entry).
                    await self.async_set_unique_id(info["user_id"])
                    self._abort_if_unique_id_configured()

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

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when stored credentials become invalid."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication by collecting a new password."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            data = {
                CONF_USERNAME: reauth_entry.data[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                info = await validate_input(self.hass, data)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Guard against re-authenticating with a different account, but
                # only when the existing entry already carries a unique_id.
                if reauth_entry.unique_id is not None:
                    await self.async_set_unique_id(info["user_id"])
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    unique_id=info["user_id"],
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": reauth_entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change the WyBot account credentials."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
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
                # Guard against reconfiguring to a different account, but only
                # when the existing entry already carries a unique_id.
                if reconfigure_entry.unique_id is not None:
                    await self.async_set_unique_id(info["user_id"])
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    unique_id=info["user_id"],
                    data_updates={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=reconfigure_entry.data[CONF_USERNAME],
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
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

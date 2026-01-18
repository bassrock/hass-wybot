"""Button platform for WyBot integration."""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_WIFI_PASSWORD, CONF_WIFI_SSID, DOMAIN
from .wybot_coordinator import WyBotCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WyBot buttons from a config entry."""
    coordinator: WyBotCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[ButtonEntity] = []

    # Get WiFi credentials from config entry
    wifi_ssid = config_entry.data.get(CONF_WIFI_SSID)
    wifi_password = config_entry.data.get(CONF_WIFI_PASSWORD)

    # Add WiFi reconfigure button for each dock device (if credentials configured)
    for idx in coordinator.vacuums:
        group = coordinator.data.get(idx)
        # Only create button if there's a dock with a BLE name and WiFi credentials
        if (
            group
            and group.docker
            and group.docker.ble_name
            and wifi_ssid
            and wifi_password
        ):
            entities.append(
                WyBotWifiReconfigureButton(
                    coordinator,
                    idx,
                    group.docker.ble_name,
                    wifi_ssid,
                    wifi_password,
                )
            )

    async_add_entities(entities)


class WyBotWifiReconfigureButton(CoordinatorEntity[WyBotCoordinator], ButtonEntity):
    """Diagnostic button to manually send WiFi credentials to a WyBot device via BLE."""

    _attr_has_entity_name = True
    _attr_translation_key = "wifi_reconfigure"
    _attr_name = "Send WiFi credentials"  # Fallback name if translations not loaded
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False  # Disabled by default
    coordinator: WyBotCoordinator

    def __init__(
        self,
        coordinator: WyBotCoordinator,
        idx: str,
        ble_name: str,
        wifi_ssid: str,
        wifi_password: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._idx = idx
        self._ble_name = ble_name
        self._wifi_ssid = wifi_ssid
        self._wifi_password = wifi_password
        self._attr_unique_id = f"{idx}_dock_wifi_reconfigure_button"
        self._attr_icon = "mdi:wifi-cog"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information - associates with the dock device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._idx}_dock")},
        )

    async def async_press(self) -> None:
        """Handle the button press - sends WiFi credentials to the dock via BLE."""
        _LOGGER.info(
            "WiFi reconfigure button pressed, sending credentials to dock %s (SSID: %s)",
            self._ble_name,
            self._wifi_ssid,
        )
        success = await self.coordinator.wybot_ble_client.configure_wifi(
            self._ble_name, self._wifi_ssid, self._wifi_password
        )
        if success:
            _LOGGER.info(
                "Successfully sent WiFi credentials to dock %s", self._ble_name
            )
        else:
            _LOGGER.warning(
                "Failed to send WiFi credentials to dock %s via BLE", self._ble_name
            )

"""Button platform for WyBot integration."""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import WyBotConfigEntry
from .const import CONF_WIFI_PASSWORD, CONF_WIFI_SSID, DOMAIN
from .wybot_coordinator import WyBotCoordinator

_LOGGER = logging.getLogger(__name__)

# Commands are issued over BLE; serialize to avoid concurrent writes to a device.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WyBotConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WyBot buttons from a config entry."""
    coordinator = config_entry.runtime_data

    # Get WiFi credentials from config entry
    wifi_ssid = config_entry.data.get(CONF_WIFI_SSID)
    wifi_password = config_entry.data.get(CONF_WIFI_PASSWORD)
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        """Add WiFi buttons for qualifying devices discovered after setup."""
        entities: list[ButtonEntity] = []
        # Only create a button if the device has a dock with a BLE name and
        # WiFi credentials are configured.
        for idx in coordinator.vacuums:
            if idx in known:
                continue
            group = coordinator.data.get(idx)
            if (
                group
                and group.docker
                and group.docker.ble_name
                and wifi_ssid
                and wifi_password
            ):
                known.add(idx)
                entities.append(
                    WyBotWifiReconfigureButton(
                        coordinator,
                        idx,
                        group.docker.ble_name,
                        wifi_ssid,
                        wifi_password,
                    )
                )
        if entities:
            async_add_entities(entities)

    _add_new_devices()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


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
    def available(self) -> bool:
        """Return True when the coordinator is available and the dock exists."""
        group = self.coordinator.data.get(self._idx)
        return (
            self.coordinator.available
            and group is not None
            and group.docker is not None
        )

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
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="wifi_send_failed",
                translation_placeholders={"device": self._ble_name},
            )
        _LOGGER.info("Successfully sent WiFi credentials to dock %s", self._ble_name)

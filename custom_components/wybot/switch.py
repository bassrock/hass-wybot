"""Switch platform for WyBot integration — auto-run toggle (DP 207 / 0xCF).

APK: "parseBLEData: getAutoRunMode DP_ID_AUTO_RUN 0XCF mode: "
When enabled, the robot starts skimming automatically when battery is sufficient.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import WyBotConfigEntry
from .const import DOMAIN, MANUFACTURER
from .wybot_coordinator import WyBotCoordinator

from wybot.dp_models import DP, AutoRunMode, GenericDP
from wybot.models import Group

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


def format_mac(mac: str) -> str:
    """Format a MAC address string with colons."""
    mac = mac.upper().replace(":", "").replace("-", "")
    return ":".join(mac[i : i + 2] for i in range(0, 12, 2))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WyBotConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the WyBot switch platform."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        """Add switch entities for devices discovered after setup."""
        entities: list[SwitchEntity] = []
        for device_id in coordinator.vacuums:
            if device_id in known:
                continue
            known.add(device_id)
            # Auto-run is an F1 feature; DS20 robots ignore DP 207 entirely.
            if coordinator.is_f1(device_id):
                entities.append(
                    WyBotF1AutoRunSwitch(idx=device_id, coordinator=coordinator)
                )
        if entities:
            async_add_entities(entities)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class WyBotSwitchBase(SwitchEntity, CoordinatorEntity[WyBotCoordinator]):
    """Base class for WyBot switches."""

    _data: Group | None
    _idx: str
    _coordinator: WyBotCoordinator
    _attr_has_entity_name = True

    def __init__(self, idx: str, coordinator: WyBotCoordinator) -> None:
        """Initialize the WyBot switch."""
        super().__init__(coordinator=coordinator, context=idx)
        self._idx = idx
        self._coordinator = coordinator
        self._data = coordinator.data.get(self._idx) if coordinator.data else None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if str(self._idx) in self.coordinator.data:
            self._data = self.coordinator.data[str(self._idx)]
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.available:
            return False
        if not self._data:
            return False
        if str(self._idx) not in self.coordinator.data:
            return False
        return True

    def _get_robot_name(self) -> str:
        """Get the robot device name."""
        if self._data:
            if self._data.name:
                return self._data.name
            elif self._data.device and self._data.device.device_name:
                return self._data.device.device_name
            elif self._data.device and self._data.device.device_type:
                return self._data.device.device_type
        return "Unknown"

    def _get_robot_model(self) -> str:
        """Get the robot device model."""
        if self._data and self._data.device:
            return self._data.device.device_type
        return "Unknown"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the robot."""
        connections: set[tuple[str, str]] = set()
        if self._data and self._data.device and self._data.device.ble_name:
            connections.add(
                (CONNECTION_BLUETOOTH, format_mac(self._data.device.ble_name))
            )
        via_device = None
        if self._data and self._data.docker:
            via_device = (DOMAIN, f"{self._idx}_dock")
        info_kwargs: dict[str, Any] = {
            "identifiers": {(DOMAIN, str(self._idx))},
            "name": self._get_robot_name(),
            "manufacturer": MANUFACTURER,
            "model": self._get_robot_model(),
            "connections": connections if connections else None,
            "via_device": via_device,
        }
        return cast(DeviceInfo, info_kwargs)


class WyBotF1AutoRunSwitch(WyBotSwitchBase):
    """Switch for F1 auto-run mode (DP 207 / 0xCF).

    APK: "parseBLEData: getAutoRunMode DP_ID_AUTO_RUN 0XCF mode: "
    When enabled, the robot starts skimming when battery is sufficient.
    """

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "auto_run"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_f1_auto_run_switch"

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return "Auto run"

    @property
    def is_on(self) -> bool | None:
        """Return whether auto-run is enabled."""
        if not self._data:
            return None
        auto_run = self._data.get_dp(AutoRunMode)
        if auto_run is None or auto_run.data is None:
            return None
        return auto_run.is_enabled

    async def _async_set_auto_run(self, enabled: bool) -> None:
        """Write the auto-run flag to the device."""
        if not self._data:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_unavailable"
            )
        dp = GenericDP(data=DP(id=207, type=4, len=1, data="01" if enabled else "00"))
        if not await self.coordinator.async_send_command(self._data, dp):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cannot_send_command"
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable auto-run mode."""
        await self._async_set_auto_run(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable auto-run mode."""
        await self._async_set_auto_run(False)


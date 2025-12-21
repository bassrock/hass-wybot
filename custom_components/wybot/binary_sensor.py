"""Platform for binary sensor integration."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .wybot_coordinator import WyBotCoordinator
from .wybot_dp_models import Battery, BatteryState
from .wybot_models import Group

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator: WyBotCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        WyBotChargingSensor(idx=deviceId, coordinator=coordinator)
        for deviceId in coordinator.vacuums
    )


class WyBotChargingSensor(CoordinatorEntity, BinarySensorEntity):
    """A WyBot charging binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_has_entity_name = True

    _data: Group
    _idx: str
    _coordinator: WyBotCoordinator

    def __init__(self, idx: str, coordinator: WyBotCoordinator) -> None:
        """Initialize the WyBot charging sensor."""
        super().__init__(coordinator=coordinator, context=idx)
        self._idx = idx
        self._coordinator = coordinator
        # Initialize data safely
        self._data = coordinator.data.get(self._idx) if coordinator.data else None
        self._attr_unique_id = f"wybot_charging_{self._idx}"
        self._attr_translation_key = "charging"

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
        # Check coordinator data directly if local data not set
        if not self._data and str(self._idx) in self.coordinator.data:
            self._data = self.coordinator.data[str(self._idx)]
        if not self._data:
            return False
        if str(self._idx) not in self.coordinator.data:
            return False
        return True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        name = self._data.name if self._data else "Unknown"
        model = (
            self._data.device.device_type
            if self._data and self._data.device
            else "Unknown"
        )
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._idx))},
            name=name,
            manufacturer=MANUFACTURER,
            model=model,
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if the vacuum is charging."""
        # Get data from coordinator if not set locally
        if not self._data and str(self._idx) in self.coordinator.data:
            self._data = self.coordinator.data[str(self._idx)]

        if not self._data:
            return None
        battery = self._data.get_dp(Battery)
        if battery is None:
            return None
        return battery.charge_state in (BatteryState.CHARGING, BatteryState.CHARGED)


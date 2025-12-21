"""Platform for sensor integration."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.icon import icon_for_battery_level
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER
from .wybot_coordinator import WyBotCoordinator
from .wybot_dp_models import Battery, BatteryState
from .wybot_models import Group

_LOGGER = logging.getLogger(__name__)

# Force state write every 5 minutes to ensure history is recorded
FORCE_STATE_WRITE_INTERVAL = timedelta(minutes=5)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator: WyBotCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        WyBotBatterySensor(idx=deviceId, coordinator=coordinator)
        for deviceId in coordinator.vacuums
    )


class WyBotBatterySensor(CoordinatorEntity, SensorEntity):
    """A WyBot battery sensor."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    _data: Group
    _idx: str
    _coordinator: WyBotCoordinator
    _last_state_write: datetime | None = None
    _last_charging_state: BatteryState | None = None
    _last_availability: bool | None = None

    def __init__(self, idx: str, coordinator: WyBotCoordinator) -> None:
        """Initialize the WyBot battery sensor."""
        super().__init__(coordinator=coordinator, context=idx)
        self._idx = idx
        self._coordinator = coordinator
        # Initialize data safely
        self._data = coordinator.data.get(self._idx) if coordinator.data else None
        self._attr_unique_id = f"wybot_battery_{self._idx}"
        self._attr_translation_key = "battery"
        self._last_state_write = None
        self._last_charging_state = None
        self._last_availability = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if str(self._idx) in self.coordinator.data:
            self._data = self.coordinator.data[str(self._idx)]

        # Track availability changes
        current_availability = self.available
        previous_availability = self._last_availability
        availability_changed = (
            previous_availability is not None
            and previous_availability != current_availability
        )
        self._last_availability = current_availability

        # Track charging state changes
        current_charging_state = None
        if self._data:
            battery = self._data.get_dp(Battery)
            if battery is not None:
                current_charging_state = battery.charge_state
        previous_charging_state = self._last_charging_state
        charging_state_changed = (
            previous_charging_state is not None
            and previous_charging_state != current_charging_state
        )
        self._last_charging_state = current_charging_state

        # Always write state if:
        # 1. Availability changed (to record unavailable/available transitions)
        # 2. Charging state changed (to update icon and attributes)
        # 3. It's been more than FORCE_STATE_WRITE_INTERVAL since last write (to ensure history)
        # 4. Normal coordinator update (base class handles this)
        now = dt_util.utcnow()
        should_force_write = (
            availability_changed
            or charging_state_changed
            or self._last_state_write is None
            or (now - self._last_state_write) >= FORCE_STATE_WRITE_INTERVAL
        )

        # Call base class update handler
        super()._handle_coordinator_update()

        # Force state write if needed to ensure icon and attributes update
        if should_force_write:
            self.async_write_ha_state()
            self._last_state_write = now
            if availability_changed:
                _LOGGER.debug(
                    "Availability changed for %s: %s -> %s, forcing state write",
                    self.entity_id,
                    previous_availability,
                    current_availability,
                )
            if charging_state_changed:
                _LOGGER.debug(
                    "Charging state changed for %s: %s -> %s, forcing state write",
                    self.entity_id,
                    previous_charging_state,
                    current_charging_state,
                )

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
    def native_value(self) -> int | None:
        """Return the battery level of the vacuum cleaner."""
        # Get data from coordinator if not set locally
        if not self._data and str(self._idx) in self.coordinator.data:
            self._data = self.coordinator.data[str(self._idx)]

        if not self._data:
            return None
        battery = self._data.get_dp(Battery)
        if battery is None:
            return None
        return battery.battery_level

    @property
    def icon(self) -> str:
        """Return the icon for the battery sensor."""
        battery = self._data.get_dp(Battery) if self._data else None
        if battery is None:
            return "mdi:battery-unknown"
        battery_level = battery.battery_level
        is_charging = battery.charge_state in (BatteryState.CHARGING, BatteryState.CHARGED)
        return icon_for_battery_level(battery_level=battery_level, charging=is_charging)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return extra state attributes."""
        battery = self._data.get_dp(Battery) if self._data else None
        if battery is None:
            return {}
        return {
            "charging": battery.charge_state in (BatteryState.CHARGING, BatteryState.CHARGED),
            "charge_state": battery.charge_state.name,
        }


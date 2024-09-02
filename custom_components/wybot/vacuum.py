"""Platform for vacuum integration."""

from __future__ import annotations

import logging

from homeassistant.components.vacuum import (
    STATE_CLEANING,
    STATE_DOCKED,
    STATE_PAUSED,
    STATE_RETURNING,
    StateVacuumEntity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .wybot_coordinator import WyBotCoordinator
from .wybot_dp_models import (
    Battery,
    BatteryState,
    CleaningMode,
    CleaningStatus,
    CleaningStatusMode,
    Dock,
    DockStatus,
)
from .wybot_models import Group

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the vacuum platform."""

    coordinator: WyBotCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        WyBotVacuum(idx=deviceId, coordinator=coordinator)
        for deviceId in coordinator.vacuums
    )


class WyBotVacuum(StateVacuumEntity, CoordinatorEntity):
    """A wybot vacuum."""

    _data: Group
    _idx = str
    _coordinator: WyBotCoordinator

    def __init__(self, idx: str, coordinator: WyBotCoordinator) -> None:
        super().__init__(coordinator=coordinator, context=idx)
        self._idx = idx
        self._data = coordinator.data[self._idx]
        self._coordinator = coordinator

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._data = self.coordinator.data[self._idx]
        super()._handle_coordinator_update()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, self._idx),
            },
            name=self._data.name,
            manufacturer=MANUFACTURER,
            model=self._data.device.device_type,
        )

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return f"wybot_vacuum_{self._idx}"

    @property
    def name(self) -> str | None:
        """Return the display name of this device."""
        return self._data.name

    @property
    def state(self) -> str | None:
        """Return the state of thee device."""
        battery = self._data.get_dp(Battery)
        cleaning_status = self._data.get_dp(CleaningStatus)
        dock_status = self._data.get_dp(Dock)

        if battery is None or cleaning_status is None or dock_status is None:
            return None
        if battery.charge_state in (BatteryState.CHARGING, BatteryState.CHARGED):
            # Docked and charging
            return STATE_DOCKED
        if dock_status.status == DockStatus.RETURNING:
            return STATE_RETURNING
        if cleaning_status.status == CleaningStatusMode.STOPPED:
            return STATE_PAUSED
        if cleaning_status.status in (
            CleaningStatusMode.CLEANING,
            CleaningStatusMode.STARTING,
        ):
            return STATE_CLEANING

    @property
    def fan_speed_list(self) -> list[str]:
        """Flag vacuum cleaner robot features that are supported."""
        return CleaningMode.CLEANING_MODES

    @property
    def fan_speed(self) -> str | None:
        """Return the fan speed of the vacuum cleaner."""
        fan_speed = self._data.get_dp(CleaningMode)
        if fan_speed is not None:
            return fan_speed.cleaning_mode
        return None

    @property
    def supported_features(self) -> VacuumEntityFeature:
        """Flag vacuum cleaner robot features that are supported."""
        return (
            VacuumEntityFeature.BATTERY
            | VacuumEntityFeature.FAN_SPEED
            | VacuumEntityFeature.RETURN_HOME
            | VacuumEntityFeature.START
            | VacuumEntityFeature.STOP
        )

    async def async_set_fan_speed(self, fan_speed: str) -> None:
        cleaning_mode = CleaningMode(mode=fan_speed)
        self.coordinator.send_write_command(self._data, cleaning_mode)

    async def async_stop(self) -> None:
        cleaning_mode = CleaningStatus(status=CleaningStatusMode.STOPPED)
        self.coordinator.send_write_command(self._data, cleaning_mode)

    async def async_start(self) -> None:
        cleaning_mode = CleaningStatus(status=CleaningStatusMode.CLEANING)
        self.coordinator.send_write_command(self._data, cleaning_mode)

    async def async_return_to_base(self) -> None:
        self.coordinator.send_write_command(self._data, Dock(status=DockStatus.RETURN))

    @property
    def battery_level(self) -> int | None:
        battery = self._data.get_dp(Battery)
        if battery is None:
            return None
        # get the last 2 characters of the data of battery_level and convert from hex to decimal
        return battery.battery_level

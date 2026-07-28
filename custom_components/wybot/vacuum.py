"""Platform for vacuum integration."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, cast

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import WyBotConfigEntry
from .const import DOMAIN, MANUFACTURER
from .wybot_coordinator import WyBotCoordinator
from wybot.dp_models import (
    Battery,
    BatteryState,
    CleaningMode,
    GenericDP,
    CleaningStatus,
    CleaningStatusMode,
    Dock,
    DockConnectionStatus,
    DockStatus,
)
from wybot.models import Group

_LOGGER = logging.getLogger(__name__)

# Commands are issued over BLE/MQTT; serialize to avoid concurrent device writes.
PARALLEL_UPDATES = 1

# Force state write every 5 minutes to ensure history is recorded
FORCE_STATE_WRITE_INTERVAL = timedelta(minutes=5)


def format_mac(mac: str) -> str:
    """Format a MAC address string with colons.

    Converts "CCBA97932A96" to "CC:BA:97:93:2A:96".
    """
    mac = mac.upper().replace(":", "").replace("-", "")
    return ":".join(mac[i : i + 2] for i in range(0, 12, 2))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WyBotConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the vacuum platform."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        """Add vacuum entities for devices discovered after setup."""
        new_ids = [d for d in coordinator.vacuums if d not in known]
        known.update(new_ids)
        if new_ids:
            async_add_entities(
                WyBotVacuum(idx=device_id, coordinator=coordinator)
                for device_id in new_ids
            )

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class WyBotVacuum(StateVacuumEntity, CoordinatorEntity[WyBotCoordinator]):
    """A wybot vacuum."""

    # Primary entity of the device: take the device's name as the entity name.
    _attr_has_entity_name = True
    _attr_name = None

    _data: Group | None
    _idx: str
    _coordinator: WyBotCoordinator
    _last_state_write: datetime | None = None
    _last_availability: bool | None = None
    _last_charging_state: BatteryState | None = None

    def __init__(self, idx: str, coordinator: WyBotCoordinator) -> None:
        """Initialize the WyBot vacuum entity."""
        super().__init__(coordinator=coordinator, context=idx)
        self._idx = idx
        self._coordinator = coordinator
        # Initialize data safely
        self._data = coordinator.data.get(self._idx) if coordinator.data else None
        self._last_state_write = None
        self._last_availability = None
        self._last_charging_state = None

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
        # 2. Charging state changed (to record charging/docked transitions)
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

        # Force state write if needed to ensure history is recorded
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
        if not self._data:
            return False
        if str(self._idx) not in self.coordinator.data:
            return False
        # Check if device is online (received online: "1" from /will/ topic)
        # Note: Device may show data even when offline if we have cached data
        # We'll show available if we have data, but the device might be asleep
        return True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        # Use group name, fall back to device name, then device type
        name = "Unknown"
        model = "Unknown"
        connections: set[tuple[str, str]] = set()
        via_device = None
        if self._data:
            if self._data.name:
                name = self._data.name
            elif self._data.device and self._data.device.device_name:
                name = self._data.device.device_name
            elif self._data.device and self._data.device.device_type:
                name = self._data.device.device_type
            if self._data.device:
                model = self._data.device.device_type
                # Add Bluetooth MAC connection if available
                if self._data.device.ble_name:
                    connections.add(
                        (CONNECTION_BLUETOOTH, format_mac(self._data.device.ble_name))
                    )
            # If dock exists, robot connects via dock
            if self._data.docker:
                via_device = (DOMAIN, f"{self._idx}_dock")
        info_kwargs: dict[str, Any] = {
            "identifiers": {(DOMAIN, str(self._idx))},
            "name": name,
            "manufacturer": MANUFACTURER,
            "model": model,
            "connections": connections if connections else None,
            "via_device": via_device,
        }
        # HA's DeviceInfo TypedDict types connections/via_device as
        # non-optional, but this integration stores None to mean "unset"
        # (preserved for compatibility); cast the loosely-typed kwargs.
        return cast(DeviceInfo, info_kwargs)

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return f"wybot_vacuum_{self._idx}"

    @property
    def activity(self) -> VacuumActivity | None:
        """Return the state of the device."""
        if not self._data:
            return None
        battery = self._data.get_dp(Battery)
        cleaning_status = self._data.get_dp(CleaningStatus)
        dock_status = self._data.get_dp(Dock)
        dock_connection = self._data.get_dp(DockConnectionStatus)

        # Check if returning to dock via CleaningStatus (DP 0) - primary indicator
        if cleaning_status is not None and cleaning_status.status in (
            CleaningStatusMode.RETURNING_TO_DOCK,
            CleaningStatusMode.RETURNING,
        ):
            return VacuumActivity.RETURNING

        # Check if returning via Dock DP (fallback for legacy behavior)
        if dock_status is not None and dock_status.status == DockStatus.RETURNING:
            return VacuumActivity.RETURNING

        # Check cleaning status BEFORE dock status
        # The F1 reports DP 11 = DOCKED even while cleaning (no real dock),
        # so we must check DP 0 first to avoid showing "docked" during cleaning
        if cleaning_status is not None:
            if cleaning_status.status in (
                CleaningStatusMode.CLEANING,
                CleaningStatusMode.STARTING,
            ):
                return VacuumActivity.CLEANING
            if cleaning_status.status == CleaningStatusMode.STOPPED:
                return VacuumActivity.PAUSED

        # Check if docked - use dock status, dock connection status, or battery charging state
        is_docked = False
        if dock_status is not None and dock_status.status == DockStatus.DOCKED:
            is_docked = True
        elif dock_connection is not None:
            is_docked = dock_connection.is_docked
        elif battery is not None:
            is_docked = battery.charge_state in (BatteryState.CHARGING, BatteryState.CHARGED)

        if is_docked:
            return VacuumActivity.DOCKED

        # F1-specific: 0x0f (15) = IDLE/STANDBY
        if cleaning_status is not None and cleaning_status.status == CleaningStatusMode.UNKNOWN:
            return VacuumActivity.IDLE if hasattr(VacuumActivity, 'IDLE') else None

        return None

    # F1-specific cleaning modes (values 14-15, outside DS20's 0-6 range)
    F1_CLEANING_MODES = {14: "Smart", 15: "Standard"}

    @property
    def fan_speed_list(self) -> list[str]:
        """Return supported cleaning modes."""
        if self._data and self._data.device and self._data.device.device_type == "WYF1":
            return ["Standard", "Smart"]
        return CleaningMode.CLEANING_MODES

    @property
    def fan_speed(self) -> str | None:
        """Return the fan speed of the vacuum cleaner."""
        if not self._data:
            return None
        fan_speed = self._data.get_dp(CleaningMode)
        if fan_speed is None or fan_speed.data is None:
            return None
        mode_val = int(fan_speed.data, 16)
        # F1 modes
        if mode_val in self.F1_CLEANING_MODES:
            return self.F1_CLEANING_MODES[mode_val]
        # DS20 modes
        if mode_val < len(CleaningMode.CLEANING_MODES):
            return fan_speed.cleaning_mode
        return None

    @property
    def supported_features(self) -> VacuumEntityFeature:
        """Flag vacuum cleaner robot features that are supported."""
        return (
            VacuumEntityFeature.FAN_SPEED
            | VacuumEntityFeature.RETURN_HOME
            | VacuumEntityFeature.START
            | VacuumEntityFeature.STOP
            | VacuumEntityFeature.TURN_ON
            | VacuumEntityFeature.TURN_OFF
        )

    async def _async_send_command(self, dp: GenericDP) -> None:
        """Send a command to the device, raising on failure."""
        if not self._data:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_unavailable"
            )
        if not await self.coordinator.async_send_command(self._data, dp):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cannot_send_command"
            )

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set the fan speed of the vacuum cleaner."""
        # F1-specific modes
        f1_mode_map = {"Standard": 15, "Smart": 14}
        if fan_speed in f1_mode_map:
            from wybot.dp_models import DP
            dp = CleaningMode(data=DP(id=1, type=4, len=1, data=f"{f1_mode_map[fan_speed]:02x}"))
            await self._async_send_command(dp)
        else:
            await self._async_send_command(CleaningMode(mode=fan_speed))

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop the vacuum cleaner."""
        await self._async_send_command(
            CleaningStatus(status=CleaningStatusMode.STOPPED)
        )

    async def async_start(self) -> None:
        """Start the vacuum cleaner."""
        await self._async_send_command(
            CleaningStatus(status=CleaningStatusMode.CLEANING)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the vacuum cleaner (alias for start)."""
        await self._async_send_command(
            CleaningStatus(status=CleaningStatusMode.CLEANING)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the vacuum cleaner (alias for stop)."""
        await self._async_send_command(
            CleaningStatus(status=CleaningStatusMode.STOPPED)
        )

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return the vacuum cleaner to the dock."""
        await self._async_send_command(Dock(status=DockStatus.RETURNING))


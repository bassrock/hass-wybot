"""Platform for vacuum integration."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

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

# Force state write every 5 minutes to ensure history is recorded
FORCE_STATE_WRITE_INTERVAL = timedelta(minutes=5)


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
    _last_state_write: datetime | None = None
    _last_availability: bool | None = None
    _last_charging_state: BatteryState | None = None
    _pending_command: VacuumActivity | None = None
    _command_sent_time: datetime | None = None

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
        self._pending_command = None
        self._command_sent_time = None

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

        # Clear pending command if we got a confirmed status that matches our expectation
        if self._data and self._pending_command is not None:
            cleaning_status = self._data.get_dp(CleaningStatus)
            dock_status = self._data.get_dp(Dock)

            # Check if we got a status that confirms our pending command
            should_clear = False
            if (
                cleaning_status is not None
                and cleaning_status.status != CleaningStatusMode.UNKNOWN
            ):
                # For CLEANING command, clear when we get CLEANING or STARTING status
                if self._pending_command == VacuumActivity.CLEANING:
                    if cleaning_status.status in (
                        CleaningStatusMode.CLEANING,
                        CleaningStatusMode.STARTING,
                    ):
                        should_clear = True
                # For PAUSED command, clear when we get STOPPED status
                elif self._pending_command == VacuumActivity.PAUSED:
                    if cleaning_status.status == CleaningStatusMode.STOPPED:
                        should_clear = True

            # For RETURNING command, check dock status
            if (
                self._pending_command == VacuumActivity.RETURNING
                and dock_status is not None
            ):
                # Clear if dock status shows RETURNING, or if we're now docked (charging)
                battery = self._data.get_dp(Battery)
                if dock_status.status == DockStatus.RETURNING or (
                    battery is not None
                    and battery.charge_state
                    in (BatteryState.CHARGING, BatteryState.CHARGED)
                ):
                    should_clear = True

            if should_clear:
                _LOGGER.debug(
                    "Clearing pending command %s for %s, got confirming status",
                    self._pending_command,
                    self.entity_id,
                )
                self._pending_command = None
                self._command_sent_time = None
            # Also clear if command is too old (more than 30 seconds)
            elif (
                self._command_sent_time is not None
                and (dt_util.utcnow() - self._command_sent_time).total_seconds() > 30
            ):
                _LOGGER.debug("Clearing stale pending command for %s", self.entity_id)
                self._pending_command = None
                self._command_sent_time = None

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
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return f"wybot_vacuum_{self._idx}"

    @property
    def name(self) -> str | None:
        """Return the display name of this device."""
        if not self._data:
            return None
        return self._data.name

    @property
    def activity(self) -> VacuumActivity | None:
        """Return the state of the device."""
        # Get data from coordinator if not set locally
        if not self._data and str(self._idx) in self.coordinator.data:
            self._data = self.coordinator.data[str(self._idx)]

        if not self._data:
            return None
        battery = self._data.get_dp(Battery)
        cleaning_status = self._data.get_dp(CleaningStatus)
        dock_status = self._data.get_dp(Dock)

        # Require at least Battery and CleaningStatus to determine state
        # Dock is optional (only needed for RETURNING detection)
        if battery is None or cleaning_status is None:
            return None

        # If we have a pending command (optimistic state), prioritize it
        # Use it if status is UNKNOWN, or if status doesn't match what we expect
        if self._pending_command is not None:
            # Always use pending command if status is UNKNOWN
            if cleaning_status.status == CleaningStatusMode.UNKNOWN:
                _LOGGER.debug(
                    "Using pending command state %s for %s (status is UNKNOWN)",
                    self._pending_command,
                    self.entity_id,
                )
                return self._pending_command

            # For RETURNING command, use it even if cleaning status is STOPPED
            # (device might be stopped while returning to dock)
            if self._pending_command == VacuumActivity.RETURNING:
                _LOGGER.debug(
                    "Using pending RETURNING state for %s (command in progress)",
                    self.entity_id,
                )
                return self._pending_command

            # For CLEANING command, only use if status is not CLEANING yet
            if (
                self._pending_command == VacuumActivity.CLEANING
                and cleaning_status.status
                not in (
                    CleaningStatusMode.CLEANING,
                    CleaningStatusMode.STARTING,
                )
            ):
                _LOGGER.debug(
                    "Using pending CLEANING state for %s (status: %s)",
                    self.entity_id,
                    cleaning_status.status,
                )
                return self._pending_command

        # If device status is UNKNOWN, try to get status from docker as fallback
        if (
            cleaning_status.status == CleaningStatusMode.UNKNOWN
            and self._data.docker is not None
        ):
            docker_cleaning_status = self._data.docker.get_dp(CleaningStatus)
            if (
                docker_cleaning_status is not None
                and docker_cleaning_status.status != CleaningStatusMode.UNKNOWN
            ):
                _LOGGER.debug(
                    "Using docker cleaning status %s for %s (device status is UNKNOWN)",
                    docker_cleaning_status.status,
                    self.entity_id,
                )
                cleaning_status = docker_cleaning_status

        # Prioritize cleaning status over charging status
        # If device is cleaning, show CLEANING even if charging
        if cleaning_status.status in (
            CleaningStatusMode.CLEANING,
            CleaningStatusMode.STARTING,
        ):
            return VacuumActivity.CLEANING

        # Check if returning to dock (only if dock status is available)
        if dock_status is not None and dock_status.status == DockStatus.RETURNING:
            return VacuumActivity.RETURNING

        # Check if charging/charged - device is docked
        # This must come BEFORE the STOPPED check, because when docked the device
        # reports STOPPED status but should show as DOCKED if charging
        if battery.charge_state in (BatteryState.CHARGING, BatteryState.CHARGED):
            return VacuumActivity.DOCKED

        # Check if stopped/paused (only if not charging - handled above)
        if cleaning_status.status == CleaningStatusMode.STOPPED:
            return VacuumActivity.PAUSED

        # Handle UNKNOWN status - assume idle/paused (charging already handled above)
        if cleaning_status.status == CleaningStatusMode.UNKNOWN:
            return VacuumActivity.PAUSED

        return None

    @property
    def fan_speed_list(self) -> list[str]:
        """Flag vacuum cleaner robot features that are supported."""
        return CleaningMode.CLEANING_MODES

    @property
    def fan_speed(self) -> str | None:
        """Return the fan speed of the vacuum cleaner."""
        # Get data from coordinator if not set locally
        if not self._data and str(self._idx) in self.coordinator.data:
            self._data = self.coordinator.data[str(self._idx)]

        if not self._data:
            return None
        fan_speed = self._data.get_dp(CleaningMode)
        if fan_speed is not None:
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
        )

    async def async_set_fan_speed(self, fan_speed: str) -> None:
        """Set the fan speed of the vacuum cleaner."""
        if not self._data:
            return
        cleaning_mode = CleaningMode(mode=fan_speed)
        self.coordinator.send_write_command(self._data, cleaning_mode)

    async def async_stop(self) -> None:
        """Stop the vacuum cleaner."""
        if not self._data:
            return
        cleaning_mode = CleaningStatus(status=CleaningStatusMode.STOPPED)
        self.coordinator.send_write_command(self._data, cleaning_mode)
        # Set optimistic state - show as paused until we get confirmation
        self._pending_command = VacuumActivity.PAUSED
        self._command_sent_time = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_start(self) -> None:
        """Start the vacuum cleaner."""
        if not self._data:
            return
        cleaning_mode = CleaningStatus(status=CleaningStatusMode.CLEANING)
        self.coordinator.send_write_command(self._data, cleaning_mode)
        # Set optimistic state - show as cleaning until we get confirmation
        self._pending_command = VacuumActivity.CLEANING
        self._command_sent_time = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_return_to_base(self) -> None:
        """Return the vacuum cleaner to the dock."""
        if not self._data:
            return
        self.coordinator.send_write_command(
            self._data, Dock(status=DockStatus.RETURNING)
        )
        # Set optimistic state - show as returning until we get confirmation
        self._pending_command = VacuumActivity.RETURNING
        self._command_sent_time = dt_util.utcnow()
        self.async_write_ha_state()

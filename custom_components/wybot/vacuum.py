"""Platform for vacuum integration."""

from __future__ import annotations

import logging

from homeassistant.components.vacuum import (
    STATE_CLEANING,
    STATE_DOCKED,
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
from .wybot_responses import Group

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
        if (
            self._data.docker.device_status == "0"
            and self._data.docker.docker_status == "0"
        ):
            return STATE_CLEANING
        if (
            # if we are 1, we are returning to dock
            self._data.docker.docker_status == "1"
        ):
            return STATE_RETURNING
        # if we are 2 or 3, we are docked
        if self._data.docker.docker_status != "0":
            return STATE_DOCKED
        # 2 & 3 = docked and charged

    @property
    def suppored_features(self) -> list[VacuumEntityFeature]:
        """Flag vacuum cleaner robot features that are supported."""
        return [
            VacuumEntityFeature.SUPPORT_BATTERY,
            # VacuumEntityFeature.SUPPORT_RETURN_HOME,
        ]

    @property
    def battery_level(self) -> int | None:
        # 2: docked and charged
        if (
            self._data.docker.docker_status == "2"
            and self._data.docker.device_status == "2"
        ):
            return 100
        # 3: docked and charging
        # 1: returning to dock
        # 0: not docked
        return 50

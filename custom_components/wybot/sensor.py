"""Sensor platform for WyBot integration."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import WyBotConfigEntry
from .const import DOMAIN, MANUFACTURER
from .wybot_coordinator import WyBotCoordinator
from wybot.dp_models import (
    Battery,
    DockInfo,
    SolarDockBattery,
    SolarEnergyHarvested,
)
from wybot.models import Group

_LOGGER = logging.getLogger(__name__)

# Read-only entities driven by the DataUpdateCoordinator.
PARALLEL_UPDATES = 0


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
    """Set up the WyBot sensor platform."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        """Add sensor entities for devices discovered after setup."""
        entities: list[SensorEntity] = []
        for device_id in coordinator.vacuums:
            if device_id in known:
                continue
            known.add(device_id)
            entities.extend(
                [
                    WyBotRobotBatterySensor(idx=device_id, coordinator=coordinator),
                    WyBotSolarDockBatterySensor(idx=device_id, coordinator=coordinator),
                    WyBotSolarEnergySensor(idx=device_id, coordinator=coordinator),
                    WyBotDockTypeSensor(idx=device_id, coordinator=coordinator),
                    # Diagnostic sensors for communication tracking
                    WyBotLastBLECommunicationSensor(idx=device_id, coordinator=coordinator),
                    WyBotLastMQTTCommunicationSensor(idx=device_id, coordinator=coordinator),
                    WyBotDataSourceSensor(idx=device_id, coordinator=coordinator),
                ]
            )
        if entities:
            async_add_entities(entities)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class WyBotSensorBase(SensorEntity, CoordinatorEntity):
    """Base class for WyBot sensors."""

    _data: Group | None
    _idx: str
    _coordinator: WyBotCoordinator
    _attr_has_entity_name = True

    def __init__(self, idx: str, coordinator: WyBotCoordinator) -> None:
        """Initialize the WyBot sensor."""
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
        # If dock exists, robot connects via dock; otherwise standalone
        via_device = None
        if self._data and self._data.docker:
            via_device = (DOMAIN, f"{self._idx}_dock")
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._idx))},
            name=self._get_robot_name(),
            manufacturer=MANUFACTURER,
            model=self._get_robot_model(),
            connections=connections if connections else None,
            via_device=via_device,
        )


class WyBotDockSensorBase(WyBotSensorBase):
    """Base class for WyBot dock sensors - creates a separate dock device."""

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the solar dock."""
        dock_name = "Solar Dock"
        dock_model = "Unknown"
        connections: set[tuple[str, str]] = set()
        if self._data and self._data.docker:
            dock_model = self._data.docker.docker_type
            # Use docker type as name if available
            if self._data.docker.docker_type:
                dock_name = f"{self._data.docker.docker_type} Solar Dock"
            # Add Bluetooth MAC connection if available
            if self._data.docker.ble_name:
                connections.add(
                    (CONNECTION_BLUETOOTH, format_mac(self._data.docker.ble_name))
                )
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._idx}_dock")},
            name=dock_name,
            manufacturer=MANUFACTURER,
            model=dock_model,
            connections=connections if connections else None,
        )


class WyBotRobotBatterySensor(WyBotSensorBase):
    """Sensor for robot battery level."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_translation_key = "robot_battery"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_robot_battery"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Robot battery"

    @property
    def native_value(self) -> int | None:
        """Return the robot battery level as percentage."""
        if not self._data:
            return None
        battery = self._data.get_dp(Battery)
        if battery is None:
            return None
        return battery.battery_level


class WyBotSolarDockBatterySensor(WyBotDockSensorBase):
    """Sensor for solar dock battery level."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_translation_key = "dock_battery"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_dock_battery"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Battery"

    @property
    def native_value(self) -> int | None:
        """Return the solar dock battery level as percentage."""
        if not self._data:
            return None
        dock_battery = self._data.get_dp(SolarDockBattery)
        if dock_battery is None:
            return None
        return dock_battery.battery_level


class WyBotSolarEnergySensor(WyBotDockSensorBase):
    """Sensor for total solar energy harvested."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_translation_key = "solar_energy"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_solar_energy"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Energy harvested"

    @property
    def native_value(self) -> int | None:
        """Return the total solar energy harvested in Wh."""
        if not self._data:
            return None
        solar_energy = self._data.get_dp(SolarEnergyHarvested)
        if solar_energy is None:
            return None
        return solar_energy.energy_wh


class WyBotDockTypeSensor(WyBotDockSensorBase):
    """Sensor for dock type information."""

    _attr_device_class = None
    _attr_entity_registry_enabled_default = False  # Disabled by default (diagnostic)
    _attr_translation_key = "dock_type"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_dock_type"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Type"

    @property
    def native_value(self) -> str | None:
        """Return the dock type."""
        if not self._data:
            return None
        dock_info = self._data.get_dp(DockInfo)
        if dock_info is None:
            return None
        if dock_info.is_solar_dock:
            return "Solar"
        return dock_info.dock_type.name.title()

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        attrs = {}
        if self._data:
            dock_info = self._data.get_dp(DockInfo)
            if dock_info is not None:
                attrs["raw_value"] = dock_info.data
                attrs["is_solar_dock"] = dock_info.is_solar_dock
        return attrs


class WyBotLastBLECommunicationSensor(WyBotDockSensorBase):
    """Diagnostic sensor for last BLE communication time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True
    _attr_translation_key = "last_ble_communication"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_last_ble_communication"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Last BLE communication"

    @property
    def native_value(self) -> datetime | None:
        """Return the last BLE communication time."""
        # Get the dock device ID for BLE tracking (dock relays to robot)
        if self._data and self._data.docker:
            device_id = self._data.docker.docker_id
        elif self._data and self._data.device:
            device_id = self._data.device.device_id
        else:
            return None
        return self._coordinator.get_last_ble_communication(device_id)

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        attrs = {}
        if self._data and self._data.docker:
            device_id = self._data.docker.docker_id
            attrs["ble_available"] = self._coordinator.is_ble_available(device_id)
            if self._data.docker.ble_name:
                attrs["ble_name"] = self._data.docker.ble_name
        return attrs


class WyBotLastMQTTCommunicationSensor(WyBotDockSensorBase):
    """Diagnostic sensor for last MQTT communication time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True
    _attr_translation_key = "last_mqtt_communication"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_last_mqtt_communication"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Last MQTT communication"

    @property
    def native_value(self) -> datetime | None:
        """Return the last MQTT communication time."""
        # Get the dock device ID for MQTT tracking
        if self._data and self._data.docker:
            device_id = self._data.docker.docker_id
        elif self._data and self._data.device:
            device_id = self._data.device.device_id
        else:
            return None
        return self._coordinator.get_last_mqtt_communication(device_id)

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        attrs = {}
        if self._data and self._data.docker:
            attrs["mqtt_connected"] = self._coordinator._mqtt_connected
        return attrs


class WyBotDataSourceSensor(WyBotDockSensorBase):
    """Diagnostic sensor showing current data source (BLE or MQTT)."""

    _attr_device_class = None
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True
    _attr_translation_key = "data_source"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_data_source"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Data source"

    @property
    def native_value(self) -> str | None:
        """Return the current data source."""
        if self._data and self._data.docker:
            device_id = self._data.docker.docker_id
        elif self._data and self._data.device:
            device_id = self._data.device.device_id
        else:
            return None
        source = self._coordinator.get_data_source(device_id)
        if source == "ble":
            return "Bluetooth"
        elif source == "mqtt":
            return "Cloud (MQTT)"
        return "Unknown"

    @property
    def icon(self) -> str:
        """Return the icon based on data source."""
        if self._data and self._data.docker:
            device_id = self._data.docker.docker_id
        elif self._data and self._data.device:
            device_id = self._data.device.device_id
        else:
            return "mdi:help-circle"
        source = self._coordinator.get_data_source(device_id)
        if source == "ble":
            return "mdi:bluetooth"
        elif source == "mqtt":
            return "mdi:cloud"
        return "mdi:help-circle"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        attrs = {}
        if self._data and self._data.docker:
            device_id = self._data.docker.docker_id
            attrs["ble_available"] = self._coordinator.is_ble_available(device_id)
            attrs["mqtt_connected"] = self._coordinator._mqtt_connected
            last_ble = self._coordinator.get_last_ble_communication(device_id)
            last_mqtt = self._coordinator.get_last_mqtt_communication(device_id)
            if last_ble:
                attrs["last_ble"] = last_ble.isoformat()
            if last_mqtt:
                attrs["last_mqtt"] = last_mqtt.isoformat()
            mqtt_connected_at = self._coordinator._mqtt_last_connected_at
            if mqtt_connected_at:
                attrs["mqtt_last_connected_at"] = mqtt_connected_at.isoformat()
        return attrs

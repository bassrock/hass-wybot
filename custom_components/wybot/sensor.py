"""Sensor platform for WyBot integration."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, cast

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
import json as json_module

from wybot.dp_models import (
    Battery,
    CleaningMode,
    DockInfo,
    GenericDP,
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
                    # F1-specific: integrated solar battery (reads DP 50 middle byte)
                    WyBotF1SolarBatterySensor(idx=device_id, coordinator=coordinator),
                    # F1-specific: runtime, temperature, energy sensors
                    WyBotF1RuntimeSensor(idx=device_id, coordinator=coordinator),
                    WyBotF1CycleRuntimeSensor(idx=device_id, coordinator=coordinator),
                    WyBotF1WaterTempSensor(idx=device_id, coordinator=coordinator),
                    WyBotF1TotalEnergySensor(idx=device_id, coordinator=coordinator),
                    WyBotF1MotorPWMSensor(idx=device_id, coordinator=coordinator),
                    # F1 cleaning mode sensor
                    WyBotF1CleaningModeSensor(idx=device_id, coordinator=coordinator),
                    # APK-confirmed DP sensors
                    WyBotF1HeavyDirtSensor(idx=device_id, coordinator=coordinator),
                    WyBotF1InWaterSensor(idx=device_id, coordinator=coordinator),
                    WyBotF1SonarSensor(idx=device_id, coordinator=coordinator),
                    WyBotF1PhSensor(idx=device_id, coordinator=coordinator),
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


class WyBotSensorBase(SensorEntity, CoordinatorEntity[WyBotCoordinator]):
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
        info_kwargs: dict[str, Any] = {
            "identifiers": {(DOMAIN, str(self._idx))},
            "name": self._get_robot_name(),
            "manufacturer": MANUFACTURER,
            "model": self._get_robot_model(),
            "connections": connections if connections else None,
            "via_device": via_device,
        }
        # HA's DeviceInfo TypedDict types connections/via_device as
        # non-optional, but this integration stores None to mean "unset"
        # (preserved for compatibility); cast the loosely-typed kwargs.
        return cast(DeviceInfo, info_kwargs)


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
        info_kwargs: dict[str, Any] = {
            "identifiers": {(DOMAIN, f"{self._idx}_dock")},
            "name": dock_name,
            "manufacturer": MANUFACTURER,
            "model": dock_model,
            "connections": connections if connections else None,
        }
        # HA's DeviceInfo TypedDict types connections as non-optional, but this
        # integration stores None to mean "unset" (preserved for
        # compatibility); cast the loosely-typed kwargs.
        return cast(DeviceInfo, info_kwargs)


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
        """Return the robot battery level as percentage.

        On the F1, the robot battery byte only updates when physically plugged in.
        When charge_state is NOT_PLUGGED_IN on the F1 (3-byte DP 50), the value is
        stale — return None so HA shows "unknown" instead of a misleading percentage.
        """
        if not self._data:
            return None
        battery = self._data.get_dp(Battery)
        if battery is None:
            return None
        return battery.robot_battery_level


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
        # DS20 dock: uses DP 221
        dock_battery = self._data.get_dp(SolarDockBattery)
        if dock_battery is not None:
            return dock_battery.battery_level
        # F1 fallback: DP 50 has 3 bytes [charge_state][solar_battery%][robot_battery%]
        battery = self._data.get_dp(Battery)
        if battery is not None and battery.data and len(battery.data) >= 6:
            # Middle byte (hex chars 2-3) is the solar/integrated battery %
            try:
                return int(battery.data[2:4], 16)
            except (ValueError, IndexError):
                pass
        return None


class WyBotSolarEnergySensor(WyBotDockSensorBase):
    """Sensor for total working time (DP 131).

    Previously labeled 'Energy harvested' — APK analysis revealed DP 131 (0x83)
    is actually 'working time' in seconds, not energy in Wh.
    Kept the class name for entity ID stability but changed unit to seconds.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "s"
    _attr_translation_key = "solar_energy"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_solar_energy"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Working time"

    @property
    def native_value(self) -> int | None:
        """Return the total working time in seconds."""
        if not self._data:
            return None
        # DP 131 (0x83) = working time (confirmed by APK, was SolarEnergyHarvested)
        working_time = self._data.get_dp(SolarEnergyHarvested)
        if working_time is not None:
            # Use the old class's energy_wh property which actually reads LE 4-byte value
            return working_time.energy_wh  # This is actually seconds, not Wh
        # F1 fallback: DP 18 (type=2, 4-byte LE) from dock MQTT
        if self._data and self._data.docker:
            dp18 = self._data.docker.dps.get("18")
            if dp18 and dp18.data:
                try:
                    raw = bytes.fromhex(dp18.data)
                    if len(raw) >= 4:
                        return int.from_bytes(raw[:4], byteorder="little")
                except (ValueError, IndexError):
                    pass
        return None


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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs: dict[str, Any] = {}
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
        # Get the device ID for BLE tracking
        # Prefer device ID since coordinator stores BLE status under device, not docker
        if self._data and self._data.device and self._data.device.device_id:
            device_id = self._data.device.device_id
        elif self._data and self._data.docker and self._data.docker.docker_id:
            device_id = self._data.docker.docker_id
        else:
            return None
        return self._coordinator.get_last_ble_communication(device_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs: dict[str, Any] = {}
        if self._data and self._data.device and self._data.device.device_id:
            device_id = self._data.device.device_id
            attrs["ble_available"] = self._coordinator.is_ble_available(device_id)
        if self._data and self._data.docker and self._data.docker.ble_name:
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
        # Get the device ID for MQTT tracking
        if self._data and self._data.device and self._data.device.device_id:
            device_id = self._data.device.device_id
        elif self._data and self._data.docker and self._data.docker.docker_id:
            device_id = self._data.docker.docker_id
        else:
            return None
        return self._coordinator.get_last_mqtt_communication(device_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs: dict[str, Any] = {}
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
        if self._data and self._data.device and self._data.device.device_id:
            device_id = self._data.device.device_id
        elif self._data and self._data.docker and self._data.docker.docker_id:
            device_id = self._data.docker.docker_id
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
        if self._data and self._data.device and self._data.device.device_id:
            device_id = self._data.device.device_id
        elif self._data and self._data.docker and self._data.docker.docker_id:
            device_id = self._data.docker.docker_id
        else:
            return "mdi:help-circle"
        source = self._coordinator.get_data_source(device_id)
        if source == "ble":
            return "mdi:bluetooth"
        elif source == "mqtt":
            return "mdi:cloud"
        return "mdi:help-circle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs: dict[str, Any] = {}
        if self._data and self._data.device and self._data.device.device_id:
            device_id = self._data.device.device_id
        elif self._data and self._data.docker and self._data.docker.docker_id:
            device_id = self._data.docker.docker_id
        else:
            return attrs
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


class WyBotF1SolarBatterySensor(WyBotSensorBase):
    """Sensor for F1 integrated solar battery level.

    The F1 skimmer has an integrated solar panel and battery, unlike the
    S2 Pro which uses a separate DS20 dock. The solar battery percentage
    is encoded in the middle byte of DP 50 (Battery):
      - Byte 0: charge_state (0=not_plugged, 1=charging, 2=charged)
      - Byte 1: solar battery % (F1-specific, not present on DS20)
      - Byte 2: robot battery %
    """

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_translation_key = "dock_battery"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_f1_solar_battery"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Solar battery"

    @property
    def native_value(self) -> int | None:
        """Return the F1 integrated solar battery level as percentage."""
        if not self._data:
            return None
        battery = self._data.get_dp(Battery)
        if battery is None or not battery.data:
            return None
        # F1 sends 3 bytes for DP 50: [charge_state][solar_battery%][robot_battery%]
        # Standard DS20 sends 2 bytes, so only parse middle byte if 3+ bytes present
        if len(battery.data) >= 6:  # 3 bytes = 6 hex chars
            try:
                return int(battery.data[2:4], 16)
            except (ValueError, IndexError):
                pass
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs: dict[str, Any] = {}
        if self._data:
            battery = self._data.get_dp(Battery)
            if battery is not None:
                attrs["raw_data"] = battery.data
                attrs["charge_state"] = battery.charge_state.name
                attrs["robot_battery"] = battery.robot_battery_level
        return attrs


# =============================================================================
# F1-Specific Sensors
# =============================================================================


def _get_dp_le_value(group, dp_id: str, default: int = 0) -> int:
    """Get a DP value as little-endian integer from either device or docker."""
    if not group:
        return default
    # Check device DPs first
    dp = group.device.dps.get(str(dp_id)) if group.device else None
    if dp is None and group.docker:
        dp = group.docker.dps.get(str(dp_id))
    if dp is None or not dp.data:
        return default
    try:
        raw = bytes.fromhex(dp.data)
        if len(raw) >= 4:
            return int.from_bytes(raw[:4], byteorder="little")
        return int.from_bytes(raw, byteorder="little")
    except (ValueError, IndexError):
        return default


def _get_dp_raw(group, dp_id: str) -> str | None:
    """Get raw DP data hex string from device or docker."""
    if not group:
        return None
    dp = group.device.dps.get(str(dp_id)) if group.device else None
    if dp is None and group.docker:
        dp = group.docker.dps.get(str(dp_id))
    if dp is None or not dp.data:
        return None
    return dp.data


class WyBotF1RuntimeSensor(WyBotSensorBase):
    """Sensor for F1 total runtime in seconds (DP 25 / DP 111)."""

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "s"
    _attr_translation_key = "total_runtime"

    @property
    def unique_id(self) -> str:
        return f"wybot_{self._idx}_f1_total_runtime"

    @property
    def name(self) -> str:
        return "Total runtime"

    @property
    def native_value(self) -> int | None:
        if not self._data:
            return None
        val = _get_dp_le_value(self._data, "25")
        if val == 0:
            val = _get_dp_le_value(self._data, "111")
        return val if val > 0 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._data:
            attrs["dp_25"] = _get_dp_le_value(self._data, "25")
            attrs["dp_111"] = _get_dp_le_value(self._data, "111")
            attrs["dp_56"] = _get_dp_le_value(self._data, "56")
            attrs["dp_54"] = _get_dp_le_value(self._data, "54")
        return attrs


class WyBotF1CycleRuntimeSensor(WyBotSensorBase):
    """Sensor for F1 current/last cycle runtime in seconds (DP 55 / DP 129)."""

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "s"
    _attr_translation_key = "cycle_runtime"

    @property
    def unique_id(self) -> str:
        return f"wybot_{self._idx}_f1_cycle_runtime"

    @property
    def name(self) -> str:
        return "Cycle runtime"

    @property
    def native_value(self) -> int | None:
        if not self._data:
            return None
        val = _get_dp_le_value(self._data, "55")
        if val == 0:
            val = _get_dp_le_value(self._data, "129")
        return val if val > 0 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._data:
            attrs["dp_55"] = _get_dp_le_value(self._data, "55")
            attrs["dp_129"] = _get_dp_le_value(self._data, "129")
            attrs["dp_110"] = _get_dp_le_value(self._data, "110")
        return attrs


class WyBotF1WaterTempSensor(WyBotSensorBase):
    """Sensor for F1 water temperature (DP 30, guessed °C)."""

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°C"
    _attr_translation_key = "water_temperature"

    @property
    def unique_id(self) -> str:
        return f"wybot_{self._idx}_f1_water_temp"

    @property
    def name(self) -> str:
        return "Water temperature"

    @property
    def native_value(self) -> int | None:
        if not self._data:
            return None
        val = _get_dp_le_value(self._data, "30")
        return val if val > 0 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._data:
            attrs["dp_30_raw"] = _get_dp_raw(self._data, "30")
            attrs["dp_30_f"] = round(_get_dp_le_value(self._data, "30") * 9/5 + 32, 1) if _get_dp_le_value(self._data, "30") > 0 else None
        return attrs


class WyBotF1TotalEnergySensor(WyBotSensorBase):
    """Sensor for F1 total energy (DP 34, guessed Wh)."""

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_translation_key = "total_energy"

    @property
    def unique_id(self) -> str:
        return f"wybot_{self._idx}_f1_total_energy"

    @property
    def name(self) -> str:
        return "Total energy"

    @property
    def native_value(self) -> int | None:
        if not self._data:
            return None
        val = _get_dp_le_value(self._data, "34")
        return val if val > 0 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._data:
            attrs["dp_34"] = _get_dp_le_value(self._data, "34")
            attrs["dp_133"] = _get_dp_le_value(self._data, "133")
            attrs["dp_53"] = _get_dp_le_value(self._data, "53")
            attrs["dp_54"] = _get_dp_le_value(self._data, "54")
            attrs["dp_56"] = _get_dp_le_value(self._data, "56")
        return attrs


class WyBotF1MotorPWMSensor(WyBotSensorBase):
    """Sensor for F1 motor PWM values (DP 81-85).

    Shows the primary motor speed as state, all values as attributes.
    """

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_translation_key = "motor_pwm"

    @property
    def unique_id(self) -> str:
        return f"wybot_{self._idx}_f1_motor_pwm"

    @property
    def name(self) -> str:
        return "Motor PWM"

    @property
    def native_value(self) -> int | None:
        if not self._data:
            return None
        val = _get_dp_le_value(self._data, "81")
        return val if val > 0 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._data:
            attrs["dp_81_pump"] = _get_dp_le_value(self._data, "81")
            attrs["dp_82_drive"] = _get_dp_le_value(self._data, "82")
            attrs["dp_83_brush"] = _get_dp_le_value(self._data, "83")
            attrs["dp_84_setting"] = _get_dp_le_value(self._data, "84")
            attrs["dp_85_setting2"] = _get_dp_le_value(self._data, "85")
            attrs["dp_61_voltage_mv"] = _get_dp_le_value(self._data, "61")
        return attrs



class WyBotF1CleaningModeSensor(WyBotSensorBase):
    """Sensor for F1 cleaning mode (DP 1).

    Shows the current cleaning mode as a string:
    - DS20: Floor, Wall, Wall Then Floor, Advanced Full Pool, Water Line, Turbo Floor, Eco Floor
    - F1: Standard (0x0f=15), Smart (0x0e=14)
    """

    _attr_translation_key = "cleaning_mode"

    @property
    def unique_id(self) -> str:
        return f"wybot_{self._idx}_f1_cleaning_mode"

    @property
    def name(self) -> str:
        return "Cleaning mode"

    @property
    def native_value(self) -> str | None:
        if not self._data:
            return None
        mode_dp = self._data.get_dp(CleaningMode)
        if mode_dp is None or mode_dp.data is None:
            return None
        mode_val = int(mode_dp.data, 16)
        # F1 modes
        f1_modes = {14: "Smart", 15: "Standard"}
        if mode_val in f1_modes:
            return f1_modes[mode_val]
        # DS20 modes
        if mode_val < len(CleaningMode.CLEANING_MODES):
            return mode_dp.cleaning_mode
        return f"Unknown ({mode_val})"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._data:
            mode_dp = self._data.get_dp(CleaningMode)
            if mode_dp is not None and mode_dp.data is not None:
                attrs["raw_value"] = int(mode_dp.data, 16)
                attrs["raw_hex"] = mode_dp.data
        return attrs


# =============================================================================
# APK-Confirmed DP Sensors
# =============================================================================



class WyBotF1HeavyDirtSensor(WyBotSensorBase):
    """Sensor for F1 heavy dirt mode (DP 145 / 0x91).

    APK: "check mqtt delay DP_ID:OX91 heavyDirt mode: "
    """

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_translation_key = "heavy_dirt"
    _attr_translation_key = "heavy_dirt"

    @property
    def unique_id(self) -> str:
        return f"wybot_{self._idx}_f1_heavy_dirt"

    @property
    def name(self) -> str:
        return "Heavy dirt mode"

    @property
    def native_value(self) -> str | None:
        if not self._data:
            return None
        dp = _get_dp_raw(self._data, "145")
        if dp is None:
            return None
        val = int(dp, 16)
        return "Enabled" if val > 0 else "Disabled"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._data:
            attrs["raw_value"] = _get_dp_le_value(self._data, "145")
        return attrs


class WyBotF1InWaterSensor(WyBotSensorBase):
    """Sensor for F1 in-water state (DP 212 / 0xD4).

    APK: "DP_ID: 0XD4 inWaterState"
    """

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_translation_key = "in_water"
    _attr_translation_key = "in_water"

    @property
    def unique_id(self) -> str:
        return f"wybot_{self._idx}_f1_in_water"

    @property
    def name(self) -> str:
        return "In water"

    @property
    def native_value(self) -> str | None:
        if not self._data:
            return None
        dp = _get_dp_raw(self._data, "212")
        if dp is None:
            return None
        val = int(dp, 16)
        return "Yes" if val > 0 else "No"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._data:
            attrs["raw_value"] = _get_dp_le_value(self._data, "212")
        return attrs


class WyBotF1SonarSensor(WyBotSensorBase):
    """Sensor for F1 sonar state (DP 209 / 0xD1).

    APK: "DP_ID: 0XD1 sona state"
    """

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_translation_key = "sonar"
    _attr_translation_key = "sonar"

    @property
    def unique_id(self) -> str:
        return f"wybot_{self._idx}_f1_sonar"

    @property
    def name(self) -> str:
        return "Sonar"

    @property
    def native_value(self) -> str | None:
        if not self._data:
            return None
        dp = _get_dp_raw(self._data, "209")
        if dp is None:
            return None
        val = int(dp, 16)
        return "Active" if val > 0 else "Inactive"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._data:
            attrs["raw_value"] = _get_dp_le_value(self._data, "209")
        return attrs


class WyBotF1PhSensor(WyBotSensorBase):
    """Sensor for F1 pH value (DP 142 / 0x8E).

    APK: "parseBLEData: PH_DATA:0X8E, pHvalue: "
    """

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_device_class = None
    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "pH"
    _attr_translation_key = "ph_value"

    @property
    def unique_id(self) -> str:
        return f"wybot_{self._idx}_f1_ph"

    @property
    def name(self) -> str:
        return "pH"

    @property
    def native_value(self) -> float | None:
        if not self._data:
            return None
        dp = _get_dp_raw(self._data, "142")
        if dp is None or len(dp) < 4:
            return None
        try:
            return int(dp[:4], 16) / 100.0
        except (ValueError, IndexError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._data:
            dp = _get_dp_raw(self._data, "142")
            if dp and len(dp) >= 8:
                attrs["raw_hex"] = dp
                try:
                    attrs["temp_from_ph"] = int(dp[4:8], 16) / 10.0
                except (ValueError, IndexError):
                    pass
        return attrs


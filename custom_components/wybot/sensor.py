"""Sensor platform for WyBot integration."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, cast

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import WyBotConfigEntry
from .const import DOMAIN, MANUFACTURER
from .wybot_coordinator import WyBotCoordinator

from wybot.dp_models import (
    Battery,
    CleaningMode,
    DockInfo,
    HeavyDirtMode,
    PhData,
    SolarDockBattery,
    WorkingTime,
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

    # DP 131 was previously exposed as an energy sensor in Wh. It is really
    # working time in seconds, so the old entity is retired rather than
    # silently changing units under existing statistics.
    entity_registry = er.async_get(hass)
    for device_id in coordinator.vacuums:
        old_entity_id = entity_registry.async_get_entity_id(
            SENSOR_DOMAIN, DOMAIN, f"wybot_{device_id}_solar_energy"
        )
        if old_entity_id is not None:
            entity_registry.async_remove(old_entity_id)

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
                    WyBotWorkingTimeSensor(idx=device_id, coordinator=coordinator),
                    WyBotDockTypeSensor(idx=device_id, coordinator=coordinator),
                    # Diagnostic sensors for communication tracking
                    WyBotLastBLECommunicationSensor(idx=device_id, coordinator=coordinator),
                    WyBotLastMQTTCommunicationSensor(idx=device_id, coordinator=coordinator),
                    WyBotDataSourceSensor(idx=device_id, coordinator=coordinator),
                ]
            )
            if coordinator.is_f1(device_id):
                entities.extend(
                    [
                        WyBotF1SolarBatterySensor(idx=device_id, coordinator=coordinator),
                        WyBotF1CleaningModeSensor(idx=device_id, coordinator=coordinator),
                        WyBotF1RuntimeSensor(idx=device_id, coordinator=coordinator),
                        WyBotF1CycleRuntimeSensor(idx=device_id, coordinator=coordinator),
                        WyBotF1TotalEnergySensor(idx=device_id, coordinator=coordinator),
                        WyBotF1MotorPWMSensor(idx=device_id, coordinator=coordinator),
                        WyBotF1HeavyDirtSensor(idx=device_id, coordinator=coordinator),
                        WyBotF1InWaterSensor(idx=device_id, coordinator=coordinator),
                        WyBotF1SonarSensor(idx=device_id, coordinator=coordinator),
                        WyBotF1PhSensor(idx=device_id, coordinator=coordinator),
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

    def _comm_device_id(self) -> str | None:
        """Return the id the coordinator tracks BLE/MQTT traffic under.

        On a DS20 the dock is the transport endpoint, so traffic is recorded
        under the dock id. The F1 has no dock but still reports a placeholder
        docker entry with an empty id, so an empty id falls through to the
        robot rather than being used as-is.
        """
        if not self._data:
            return None
        if self._data.docker and self._data.docker.docker_id:
            return self._data.docker.docker_id
        if self._data.device and self._data.device.device_id:
            return self._data.device.device_id
        return None


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


class WyBotWorkingTimeSensor(WyBotDockSensorBase):
    """Sensor for total working time (DP 131).

    This DP was previously exposed as "Energy harvested" in Wh. Decompiling the
    vendor app showed DP 131 (0x83) is working time in seconds and was never an
    energy reading, so this is a distinct entity rather than a unit change on
    the old one — the recorder cannot migrate Wh statistics to seconds, and the
    old series was meaningless anyway. The obsolete entity is removed from the
    registry in async_setup_entry.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_translation_key = "working_time"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_working_time"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Working time"

    @property
    def native_value(self) -> int | None:
        """Return the total working time in seconds."""
        if not self._data:
            return None
        working_time = self._data.get_dp(WorkingTime)
        if working_time is None:
            return None
        return working_time.seconds


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
        device_id = self._comm_device_id()
        if device_id is None:
            return None
        return self._coordinator.get_last_ble_communication(device_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs: dict[str, Any] = {}
        device_id = self._comm_device_id()
        if device_id is not None:
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
        device_id = self._comm_device_id()
        if device_id is None:
            return None
        return self._coordinator.get_last_mqtt_communication(device_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {"mqtt_connected": self._coordinator._mqtt_connected}


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
        device_id = self._comm_device_id()
        if device_id is None:
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
        device_id = self._comm_device_id()
        if device_id is None:
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
        device_id = self._comm_device_id()
        if device_id is None:
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
        if battery is None:
            return None
        return battery.solar_battery_level

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if not self._data:
            return {}
        battery = self._data.get_dp(Battery)
        if battery is None:
            return {}
        return {"charge_state": battery.charge_state.name}


# =============================================================================
# F1-Specific Sensors
# =============================================================================


def _get_dp_raw(group: Group | None, dp_id: str) -> str | None:
    """Return a DP's raw hex payload, preferring the robot over the dock."""
    if group is None:
        return None
    dp = group.device.dps.get(dp_id) if group.device else None
    if dp is None and group.docker:
        dp = group.docker.dps.get(dp_id)
    if dp is None or not dp.data:
        return None
    return dp.data


def _get_dp_le_value(group: Group | None, dp_id: str, default: int = 0) -> int:
    """Return a DP payload decoded as a little-endian integer.

    Several F1 DPs are not modelled in pywybot yet, so they are read by raw id
    here. Payloads longer than four bytes are truncated to the first word.
    """
    data = _get_dp_raw(group, dp_id)
    if data is None:
        return default
    try:
        raw = bytes.fromhex(data)
    except ValueError:
        return default
    return int.from_bytes(raw[:4], byteorder="little")


class WyBotF1RuntimeSensor(WyBotSensorBase):
    """Sensor for F1 total runtime in seconds (DP 25, falling back to DP 111).

    These DP ids were identified from a BLE sweep rather than the vendor app,
    so the sensor is diagnostic and disabled by default. It is also BLE-only —
    neither DP is delivered over the MQTT cloud stream.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_translation_key = "total_runtime"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_f1_total_runtime"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Total runtime"

    @property
    def native_value(self) -> int | None:
        """Return the total runtime in seconds."""
        if not self._data:
            return None
        val = _get_dp_le_value(self._data, "25") or _get_dp_le_value(self._data, "111")
        return val or None


class WyBotF1CycleRuntimeSensor(WyBotSensorBase):
    """Sensor for F1 current/last cycle runtime (DP 55, falling back to DP 129).

    Identified from a BLE sweep rather than the vendor app, so this is
    diagnostic, disabled by default, and BLE-only.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_translation_key = "cycle_runtime"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_f1_cycle_runtime"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Cycle runtime"

    @property
    def native_value(self) -> int | None:
        """Return the cycle runtime in seconds."""
        if not self._data:
            return None
        val = _get_dp_le_value(self._data, "55") or _get_dp_le_value(self._data, "129")
        return val or None


class WyBotF1TotalEnergySensor(WyBotSensorBase):
    """Sensor for F1 total energy (DP 34).

    The unit is inferred, not confirmed by the vendor app, so this is
    diagnostic, disabled by default, and BLE-only.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_translation_key = "total_energy"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_f1_total_energy"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Total energy"

    @property
    def native_value(self) -> int | None:
        """Return the total energy."""
        if not self._data:
            return None
        return _get_dp_le_value(self._data, "34") or None


class WyBotF1MotorPWMSensor(WyBotSensorBase):
    """Sensor for the F1 pump motor PWM duty cycle (DP 81).

    DPs 82-85 carry the remaining motors but their mapping is unconfirmed, so
    only the pump is exposed. Diagnostic, disabled by default, and BLE-only.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_translation_key = "motor_pwm"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_f1_motor_pwm"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Motor PWM"

    @property
    def native_value(self) -> int | None:
        """Return the pump motor PWM duty cycle."""
        if not self._data:
            return None
        return _get_dp_le_value(self._data, "81") or None



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
        """Return the current cleaning mode."""
        if not self._data:
            return None
        mode_dp = self._data.get_dp(CleaningMode)
        if mode_dp is None or mode_dp.data is None:
            return None
        return mode_dp.cleaning_mode


# =============================================================================
# APK-Confirmed DP Sensors
# =============================================================================



class WyBotF1HeavyDirtSensor(WyBotSensorBase):
    """Sensor for F1 heavy dirt mode (DP 145 / 0x91).

    APK: "check mqtt delay DP_ID:OX91 heavyDirt mode: "
    """

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_translation_key = "heavy_dirt"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_f1_heavy_dirt"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Heavy dirt mode"

    @property
    def native_value(self) -> str | None:
        """Return whether heavy dirt mode is engaged."""
        if not self._data:
            return None
        dp = self._data.get_dp(HeavyDirtMode)
        if dp is None or dp.data is None:
            return None
        return "Enabled" if dp.is_enabled else "Disabled"


class WyBotF1InWaterSensor(WyBotSensorBase):
    """Sensor for F1 in-water state (DP 212 / 0xD4).

    APK: "DP_ID: 0XD4 inWaterState"
    """

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_translation_key = "in_water"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_f1_in_water"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "In water"

    @property
    def native_value(self) -> str | None:
        """Return whether the skimmer reports itself in the water."""
        if not self._data:
            return None
        dp = _get_dp_raw(self._data, "212")
        if dp is None:
            return None
        return "Yes" if int(dp, 16) > 0 else "No"


class WyBotF1SonarSensor(WyBotSensorBase):
    """Sensor for F1 sonar state (DP 209 / 0xD1).

    APK: "DP_ID: 0XD1 sona state"
    """

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_translation_key = "sonar"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_f1_sonar"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Sonar"

    @property
    def native_value(self) -> str | None:
        """Return whether the sonar is active."""
        if not self._data:
            return None
        dp = _get_dp_raw(self._data, "209")
        if dp is None:
            return None
        return "Active" if int(dp, 16) > 0 else "Inactive"


class WyBotF1PhSensor(WyBotSensorBase):
    """Sensor for F1 pH value (DP 142 / 0x8E).

    APK: "parseBLEData: PH_DATA:0X8E, pHvalue: "
    """

    _attr_entity_registry_enabled_default = False  # BLE-only, not available via MQTT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "pH"
    _attr_translation_key = "ph_value"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"wybot_{self._idx}_f1_ph"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "pH"

    @property
    def native_value(self) -> float | None:
        """Return the pH reading from the electrode."""
        if not self._data:
            return None
        ph_data = self._data.get_dp(PhData)
        if ph_data is None:
            return None
        return ph_data.ph_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the electrode temperature alongside the pH reading."""
        if not self._data:
            return {}
        ph_data = self._data.get_dp(PhData)
        if ph_data is None or ph_data.temperature is None:
            return {}
        return {"electrode_temperature": ph_data.temperature}


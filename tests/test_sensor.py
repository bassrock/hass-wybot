"""Tests for the WyBot sensor platform."""

from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.core import HomeAssistant

from custom_components.wybot import sensor as sensor_mod
from custom_components.wybot.sensor import (
    WyBotDataSourceSensor,
    WyBotDockTypeSensor,
    WyBotLastBLECommunicationSensor,
    WyBotLastMQTTCommunicationSensor,
    WyBotRobotBatterySensor,
    WyBotSolarDockBatterySensor,
    WyBotWorkingTimeSensor,
    async_setup_entry,
    format_mac,
)
from wybot.dp_models import (
    Battery,
    DockInfo,
    SolarDockBattery,
    WorkingTime,
)

from wybot_platform_helpers import add_entity, dp, make_coordinator, make_group

IDX = "grp1"


def _robot_dps():
    return {"50": dp(Battery, id=50, type=0, len=2, data="0132")}


def _dock_dps():
    return {
        "221": dp(SolarDockBattery, id=221, type=0, len=3, data="01480a"),
        "131": dp(WorkingTime, id=131, type=2, len=4, data="e8030000"),
        "214": dp(DockInfo, id=214, type=4, len=1, data="05"),
    }


def _coord(hass, **kwargs):
    group = make_group(device_dps=_robot_dps(), docker_dps=_dock_dps(), **kwargs)
    coord, entry = make_coordinator(hass, {IDX: group})
    return coord, entry, group


def test_format_mac() -> None:
    assert format_mac("CCBA97932A96") == "CC:BA:97:93:2A:96"
    assert format_mac("cc:ba:97:93:2a:96") == "CC:BA:97:93:2A:96"
    assert format_mac("cc-ba-97-93-2a-96") == "CC:BA:97:93:2A:96"


async def test_async_setup_entry_adds_entities(hass: HomeAssistant) -> None:
    coord, entry, _ = _coord(hass)
    entry.runtime_data = coord
    added: list = []
    await async_setup_entry(hass, entry, lambda e: added.extend(e))
    # 7 sensors per device.
    assert len(added) == 7


async def test_robot_battery_sensor(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    assert ent.native_value == 50
    assert ent.unique_id == "wybot_grp1_robot_battery"
    assert ent.name == "Robot battery"
    assert ent.available is True
    info = ent.device_info
    assert info["identifiers"] == {("wybot", "grp1")}
    assert info["via_device"] == ("wybot", "grp1_dock")
    assert ("bluetooth", "CC:BA:97:93:2A:96") in info["connections"]


async def test_robot_battery_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.native_value is None
    assert ent.available is False


async def test_robot_battery_dp_missing(hass: HomeAssistant) -> None:
    group = make_group(device_dps={}, docker_dps={})
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    assert ent.native_value is None


async def test_available_when_coordinator_unavailable(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    coord._connection_available = False
    assert ent.available is False


async def test_available_when_idx_missing(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    # Keep data truthy (coordinator.available) but drop this idx.
    coord.data = {"other": coord.data[IDX]}
    assert ent.available is False


async def test_solar_dock_battery_sensor(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotSolarDockBatterySensor(idx=IDX, coordinator=coord)
    assert ent.native_value == 72
    assert ent.unique_id == "wybot_grp1_dock_battery"
    assert ent.name == "Battery"
    info = ent.device_info
    assert info["identifiers"] == {("wybot", "grp1_dock")}
    assert info["name"] == "DS20 Solar Dock"
    assert info["model"] == "DS20"
    assert ("bluetooth", "3C:84:27:56:5A:1A") in info["connections"]


async def test_solar_dock_battery_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotSolarDockBatterySensor(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.native_value is None
    # device_info default (no data) branch.
    info = ent.device_info
    assert info["name"] == "Solar Dock"
    assert info["model"] == "Unknown"
    assert info["connections"] is None


async def test_solar_dock_battery_dp_missing(hass: HomeAssistant) -> None:
    group = make_group(device_dps={}, docker_dps={})
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotSolarDockBatterySensor(idx=IDX, coordinator=coord)
    assert ent.native_value is None


async def test_working_time_sensor(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotWorkingTimeSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == 1000
    assert ent.unique_id == "wybot_grp1_working_time"
    assert ent.name == "Working time"


async def test_working_time_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotWorkingTimeSensor(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.native_value is None


async def test_working_time_dp_missing(hass: HomeAssistant) -> None:
    group = make_group(device_dps={}, docker_dps={})
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotWorkingTimeSensor(idx=IDX, coordinator=coord)
    assert ent.native_value is None


async def test_dock_type_sensor_solar(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotDockTypeSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == "Solar"
    assert ent.unique_id == "wybot_grp1_dock_type"
    assert ent.name == "Type"
    attrs = ent.extra_state_attributes
    assert attrs["raw_value"] == "05"
    assert attrs["is_solar_dock"] is True


async def test_dock_type_sensor_standard(hass: HomeAssistant) -> None:
    group = make_group(
        device_dps={}, docker_dps={"214": dp(DockInfo, id=214, type=4, len=1, data="01")}
    )
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotDockTypeSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == "Standard"
    assert ent.extra_state_attributes["is_solar_dock"] is False


async def test_dock_type_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotDockTypeSensor(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.native_value is None
    assert ent.extra_state_attributes == {}


async def test_dock_type_dp_missing(hass: HomeAssistant) -> None:
    group = make_group(device_dps={}, docker_dps={})
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotDockTypeSensor(idx=IDX, coordinator=coord)
    assert ent.native_value is None
    assert ent.extra_state_attributes == {}


async def test_last_ble_communication_sensor(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    now = datetime.now(timezone.utc)
    coord._last_ble_poll["dock1"] = now
    coord._ble_available["dock1"] = True
    ent = WyBotLastBLECommunicationSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == now
    assert ent.unique_id == "wybot_grp1_last_ble_communication"
    assert ent.name == "Last BLE communication"
    attrs = ent.extra_state_attributes
    assert attrs["ble_available"] is True
    assert attrs["ble_name"] == "3C8427565A1A"


async def test_last_ble_communication_no_docker(hass: HomeAssistant) -> None:
    group = make_group(device_dps=_robot_dps(), with_docker=False)
    coord, _ = make_coordinator(hass, {IDX: group})
    now = datetime.now(timezone.utc)
    coord._last_ble_poll["dev1"] = now
    ent = WyBotLastBLECommunicationSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == now
    # A dockless robot (the F1) is tracked under its own device id.
    assert ent.extra_state_attributes == {"ble_available": None}


async def test_last_ble_communication_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotLastBLECommunicationSensor(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.native_value is None
    assert ent.extra_state_attributes == {}


async def test_last_mqtt_communication_sensor(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    now = datetime.now(timezone.utc)
    coord._last_mqtt_data["dock1"] = now
    coord._mqtt_connected = True
    ent = WyBotLastMQTTCommunicationSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == now
    assert ent.unique_id == "wybot_grp1_last_mqtt_communication"
    assert ent.name == "Last MQTT communication"
    assert ent.extra_state_attributes["mqtt_connected"] is True


async def test_last_mqtt_communication_no_docker(hass: HomeAssistant) -> None:
    group = make_group(device_dps=_robot_dps(), with_docker=False)
    coord, _ = make_coordinator(hass, {IDX: group})
    now = datetime.now(timezone.utc)
    coord._last_mqtt_data["dev1"] = now
    ent = WyBotLastMQTTCommunicationSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == now
    assert ent.extra_state_attributes == {"mqtt_connected": False}


async def test_last_mqtt_communication_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotLastMQTTCommunicationSensor(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.native_value is None


async def test_data_source_sensor_ble(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    coord._data_source["dock1"] = "ble"
    coord._ble_available["dock1"] = True
    coord._mqtt_connected = False
    now = datetime.now(timezone.utc)
    coord._last_ble_poll["dock1"] = now
    coord._last_mqtt_data["dock1"] = now
    coord._mqtt_last_connected_at = now
    ent = WyBotDataSourceSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == "Bluetooth"
    assert ent.icon == "mdi:bluetooth"
    assert ent.unique_id == "wybot_grp1_data_source"
    assert ent.name == "Data source"
    attrs = ent.extra_state_attributes
    assert attrs["ble_available"] is True
    assert attrs["last_ble"] == now.isoformat()
    assert attrs["last_mqtt"] == now.isoformat()
    assert attrs["mqtt_last_connected_at"] == now.isoformat()


async def test_data_source_sensor_mqtt(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    coord._data_source["dock1"] = "mqtt"
    ent = WyBotDataSourceSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == "Cloud (MQTT)"
    assert ent.icon == "mdi:cloud"


async def test_data_source_sensor_unknown(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotDataSourceSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == "Unknown"
    assert ent.icon == "mdi:help-circle"


async def test_data_source_sensor_no_docker_uses_device(hass: HomeAssistant) -> None:
    group = make_group(device_dps=_robot_dps(), with_docker=False)
    coord, _ = make_coordinator(hass, {IDX: group})
    coord._data_source["dev1"] = "ble"
    ent = WyBotDataSourceSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == "Bluetooth"
    assert ent.icon == "mdi:bluetooth"
    # A dockless robot (the F1) is tracked under its own device id.
    attrs = ent.extra_state_attributes
    assert attrs["ble_available"] is None
    assert attrs["mqtt_connected"] is False


async def test_data_source_sensor_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotDataSourceSensor(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.native_value is None
    assert ent.icon == "mdi:help-circle"
    assert ent.extra_state_attributes == {}


async def test_robot_name_fallbacks(hass: HomeAssistant) -> None:
    # name falsy -> device_name.
    group = make_group(device_dps=_robot_dps(), name="")
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    assert ent.device_info["name"] == "Robot"

    # name and device_name falsy -> device_type.
    group.device.device_name = ""
    assert ent._get_robot_name() == "S2 Pro"

    # No data -> Unknown.
    ent._data = None
    assert ent._get_robot_name() == "Unknown"
    assert ent._get_robot_model() == "Unknown"


async def test_device_info_no_ble_no_docker(hass: HomeAssistant) -> None:
    group = make_group(device_dps=_robot_dps(), with_docker=False)
    group.device.ble_name = ""
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    info = ent.device_info
    assert info["connections"] is None
    assert info["via_device"] is None


async def test_handle_coordinator_update(hass: HomeAssistant) -> None:
    coord, _, group = _coord(hass)
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    await add_entity(hass, ent)
    # idx present -> refresh _data and write state.
    ent._handle_coordinator_update()
    assert ent._data is group
    state = hass.states.get(ent.entity_id)
    assert state is not None
    assert state.state == "50"


async def test_handle_coordinator_update_idx_missing(hass: HomeAssistant) -> None:
    coord, _, group = _coord(hass)
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    await add_entity(hass, ent)
    coord.data = {}
    ent._handle_coordinator_update()
    # _data retained (idx not in coordinator.data).
    assert ent._data is group

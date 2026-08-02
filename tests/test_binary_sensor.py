"""Tests for the WyBot binary sensor platform."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.wybot.binary_sensor import (
    WyBotDockChargingBinarySensor,
    WyBotRobotChargingBinarySensor,
    async_setup_entry,
    format_mac,
)
from wybot.dp_models import Battery, DockConnectionStatus, SolarStatus

from wybot_platform_helpers import add_entity, dp, make_coordinator, make_group

IDX = "grp1"


def _robot_dps(battery="0132"):
    return {"50": dp(Battery, id=50, type=0, len=2, data=battery)}


def _dock_dps(solar="01"):
    return {
        "222": dp(SolarStatus, id=222, type=0, len=1, data=solar),
        "213": dp(DockConnectionStatus, id=213, type=4, len=1, data="01"),
    }


def _coord(hass, battery="0132", solar="01", **kwargs):
    group = make_group(
        device_dps=_robot_dps(battery), docker_dps=_dock_dps(solar), **kwargs
    )
    coord, entry = make_coordinator(hass, {IDX: group})
    return coord, entry, group


def test_format_mac() -> None:
    assert format_mac("CCBA97932A96") == "CC:BA:97:93:2A:96"


async def test_async_setup_entry(hass: HomeAssistant) -> None:
    coord, entry, _ = _coord(hass)
    entry.runtime_data = coord
    added: list = []
    await async_setup_entry(hass, entry, lambda e: added.extend(e))
    assert len(added) == 3


async def test_robot_charging_on(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass, battery="0132")  # 01 -> CHARGING
    ent = WyBotRobotChargingBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is True
    assert ent.unique_id == "wybot_grp1_robot_charging"
    assert ent.name == "Charging"
    assert ent.available is True
    attrs = ent.extra_state_attributes
    assert attrs["charge_state"] == "CHARGING"
    assert attrs["is_fully_charged"] is False
    info = ent.device_info
    assert info["identifiers"] == {("wybot", "grp1")}
    assert info["via_device"] == ("wybot", "grp1_dock")


async def test_robot_charging_off_and_fully_charged(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass, battery="0264")  # 02 -> CHARGED
    ent = WyBotRobotChargingBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is False
    attrs = ent.extra_state_attributes
    assert attrs["charge_state"] == "CHARGED"
    assert attrs["is_fully_charged"] is True


async def test_robot_charging_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotRobotChargingBinarySensor(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.is_on is None
    assert ent.extra_state_attributes == {}
    assert ent.available is False


async def test_robot_charging_dp_missing(hass: HomeAssistant) -> None:
    group = make_group(device_dps={}, docker_dps={})
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotRobotChargingBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is None
    assert ent.extra_state_attributes == {}


async def test_robot_charging_unavailable_coordinator(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotRobotChargingBinarySensor(idx=IDX, coordinator=coord)
    coord._connection_available = False
    assert ent.available is False


async def test_robot_charging_idx_missing(hass: HomeAssistant) -> None:
    coord, _, group = _coord(hass)
    ent = WyBotRobotChargingBinarySensor(idx=IDX, coordinator=coord)
    coord.data = {"other": group}
    assert ent.available is False


async def test_dock_charging_on(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass, solar="01")
    ent = WyBotDockChargingBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is True
    assert ent.unique_id == "wybot_grp1_dock_charging"
    assert ent.name == "Charging"
    attrs = ent.extra_state_attributes
    assert attrs["is_docked"] is True
    assert attrs["raw_value"] == "01"
    info = ent.device_info
    assert info["identifiers"] == {("wybot", "grp1_dock")}
    assert info["name"] == "DS20 Solar Dock"
    assert ("bluetooth", "3C:84:27:56:5A:1A") in info["connections"]


async def test_dock_charging_off(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass, solar="00")
    ent = WyBotDockChargingBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is False


async def test_dock_charging_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotDockChargingBinarySensor(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.is_on is None
    assert ent.extra_state_attributes == {}
    # device_info default branch.
    info = ent.device_info
    assert info["name"] == "Solar Dock"
    assert info["model"] == "Unknown"
    assert info["connections"] is None


async def test_dock_charging_dp_missing(hass: HomeAssistant) -> None:
    group = make_group(device_dps={}, docker_dps={})
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotDockChargingBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is None
    assert ent.extra_state_attributes == {}


async def test_robot_name_fallbacks(hass: HomeAssistant) -> None:
    group = make_group(device_dps=_robot_dps(), name="")
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotRobotChargingBinarySensor(idx=IDX, coordinator=coord)
    assert ent._get_robot_name() == "Robot"
    group.device.device_name = ""
    assert ent._get_robot_name() == "S2 Pro"
    ent._data = None
    assert ent._get_robot_name() == "Unknown"
    assert ent._get_robot_model() == "Unknown"


async def test_device_info_no_ble_no_docker(hass: HomeAssistant) -> None:
    group = make_group(device_dps=_robot_dps(), with_docker=False)
    group.device.ble_name = ""
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotRobotChargingBinarySensor(idx=IDX, coordinator=coord)
    info = ent.device_info
    assert info["connections"] is None
    assert info["via_device"] is None


async def test_handle_coordinator_update(hass: HomeAssistant) -> None:
    coord, _, group = _coord(hass)
    ent = WyBotRobotChargingBinarySensor(idx=IDX, coordinator=coord)
    await add_entity(hass, ent, domain="binary_sensor")
    ent._handle_coordinator_update()
    assert ent._data is group
    state = hass.states.get(ent.entity_id)
    assert state is not None
    assert state.state == "on"


async def test_handle_coordinator_update_idx_missing(hass: HomeAssistant) -> None:
    coord, _, group = _coord(hass)
    ent = WyBotRobotChargingBinarySensor(idx=IDX, coordinator=coord)
    await add_entity(hass, ent, domain="binary_sensor")
    coord.data = {}
    ent._handle_coordinator_update()
    assert ent._data is group

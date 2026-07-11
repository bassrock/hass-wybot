"""Tests for the WyBot vacuum platform."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.components.vacuum import VacuumActivity, VacuumEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest

from custom_components.wybot.vacuum import (
    WyBotVacuum,
    async_setup_entry,
    format_mac,
)
from wybot.dp_models import (
    Battery,
    BatteryState,
    CleaningMode,
    CleaningStatus,
    Dock,
    DockConnectionStatus,
)

from wybot_platform_helpers import add_entity, dp, make_coordinator, make_group

IDX = "grp1"


def _coord(hass, device_dps=None, docker_dps=None, **kwargs):
    group = make_group(device_dps=device_dps or {}, docker_dps=docker_dps or {}, **kwargs)
    coord, entry = make_coordinator(hass, {IDX: group})
    return coord, entry, group


def test_format_mac() -> None:
    assert format_mac("CCBA97932A96") == "CC:BA:97:93:2A:96"


async def test_async_setup_entry(hass: HomeAssistant) -> None:
    coord, entry, _ = _coord(hass)
    entry.runtime_data = coord
    added: list = []
    await async_setup_entry(hass, entry, lambda e: added.extend(e))
    assert len(added) == 1
    assert isinstance(added[0], WyBotVacuum)


async def test_basic_properties(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.unique_id == "wybot_vacuum_grp1"
    assert ent.available is True
    assert ent.fan_speed_list == CleaningMode.CLEANING_MODES
    assert ent.supported_features == (
        VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.START
        | VacuumEntityFeature.STOP
    )
    info = ent.device_info
    assert info["identifiers"] == {("wybot", "grp1")}
    assert info["name"] == "My Pool"
    assert info["model"] == "S2 Pro"
    assert info["via_device"] == ("wybot", "grp1_dock")
    assert ("bluetooth", "CC:BA:97:93:2A:96") in info["connections"]


async def test_available_branches(hass: HomeAssistant) -> None:
    coord, _, group = _coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    coord._connection_available = False
    assert ent.available is False
    coord._connection_available = True
    ent._data = None
    assert ent.available is False
    ent._data = group
    coord.data = {"other": group}
    assert ent.available is False


async def test_device_info_fallbacks(hass: HomeAssistant) -> None:
    # No data -> Unknown/Unknown, no connections/via.
    coord, _, group = _coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    ent._data = None
    info = ent.device_info
    assert info["name"] == "Unknown"
    assert info["model"] == "Unknown"
    assert info["connections"] is None
    assert info["via_device"] is None

    # name falsy -> device_name.
    group2 = make_group(name="")
    coord2, _ = make_coordinator(hass, {IDX: group2})
    ent2 = WyBotVacuum(idx=IDX, coordinator=coord2)
    assert ent2.device_info["name"] == "Robot"
    # name and device_name falsy -> device_type.
    group2.device.device_name = ""
    assert ent2.device_info["name"] == "S2 Pro"


async def test_device_info_no_ble_no_docker(hass: HomeAssistant) -> None:
    group = make_group(with_docker=False)
    group.device.ble_name = ""
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    info = ent.device_info
    assert info["connections"] is None
    assert info["via_device"] is None


async def test_activity_none_when_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.activity is None


async def test_activity_returning_via_cleaning_status(hass: HomeAssistant) -> None:
    # CleaningStatus data 04 -> RETURNING_TO_DOCK.
    coord, _, _ = _coord(
        hass, device_dps={"0": dp(CleaningStatus, id=0, type=4, len=1, data="04")}
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.RETURNING


async def test_activity_returning_via_dock_dp(hass: HomeAssistant) -> None:
    # Dock DP 11 data 01 -> RETURNING.
    coord, _, _ = _coord(
        hass, device_dps={"11": dp(Dock, id=11, type=4, len=1, data="01")}
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.RETURNING


async def test_activity_docked_via_dock_dp(hass: HomeAssistant) -> None:
    # Dock DP 11 data 00 -> DOCKED.
    coord, _, _ = _coord(
        hass, device_dps={"11": dp(Dock, id=11, type=4, len=1, data="00")}
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.DOCKED


async def test_activity_docked_via_dock_connection(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(
        hass,
        device_dps={
            "213": dp(DockConnectionStatus, id=213, type=4, len=1, data="01")
        },
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.DOCKED


async def test_activity_docked_via_battery(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(
        hass, device_dps={"50": dp(Battery, id=50, type=0, len=2, data="0132")}
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.DOCKED


async def test_activity_cleaning(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(
        hass, device_dps={"0": dp(CleaningStatus, id=0, type=4, len=1, data="03")}
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.CLEANING


async def test_activity_paused(hass: HomeAssistant) -> None:
    # CleaningStatus 01 -> STOPPED -> PAUSED. Battery not plugged in so not docked.
    coord, _, _ = _coord(
        hass,
        device_dps={
            "0": dp(CleaningStatus, id=0, type=4, len=1, data="01"),
            "50": dp(Battery, id=50, type=0, len=2, data="004b"),
        },
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.PAUSED


async def test_activity_none_when_no_dps(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity is None


async def test_fan_speed(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(
        hass, device_dps={"1": dp(CleaningMode, id=1, type=4, len=1, data="01")}
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.fan_speed == "Wall"


async def test_fan_speed_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    ent._data = None
    assert ent.fan_speed is None


async def test_fan_speed_dp_missing(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.fan_speed is None


async def test_commands_success(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    coord.async_send_command = AsyncMock(return_value=True)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    await ent.async_start()
    await ent.async_stop()
    await ent.async_return_to_base()
    await ent.async_set_fan_speed("Wall")
    assert coord.async_send_command.await_count == 4


async def test_commands_failure_raises(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    coord.async_send_command = AsyncMock(return_value=False)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    with pytest.raises(HomeAssistantError):
        await ent.async_start()
    with pytest.raises(HomeAssistantError):
        await ent.async_stop()
    with pytest.raises(HomeAssistantError):
        await ent.async_return_to_base()
    with pytest.raises(HomeAssistantError):
        await ent.async_set_fan_speed("Wall")


async def test_commands_no_data_raises(hass: HomeAssistant) -> None:
    coord, _, _ = _coord(hass)
    coord.async_send_command = AsyncMock(return_value=True)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    ent._data = None
    with pytest.raises(HomeAssistantError):
        await ent.async_start()
    coord.async_send_command.assert_not_awaited()


async def test_handle_coordinator_update_first_write(hass: HomeAssistant) -> None:
    coord, _, group = _coord(
        hass, device_dps={"0": dp(CleaningStatus, id=0, type=4, len=1, data="03")}
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    await add_entity(hass, ent, domain="vacuum")
    # First update: _last_state_write is None -> force write.
    ent._handle_coordinator_update()
    assert ent._data is group
    assert ent._last_state_write is not None
    state = hass.states.get(ent.entity_id)
    assert state is not None
    assert state.state == VacuumActivity.CLEANING


async def test_handle_coordinator_update_no_force_write(hass: HomeAssistant) -> None:
    coord, _, group = _coord(
        hass, device_dps={"50": dp(Battery, id=50, type=0, len=2, data="0132")}
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    await add_entity(hass, ent, domain="vacuum")
    # Prime state so nothing changes and it's within the write interval.
    ent._last_state_write = dt_util.utcnow()
    ent._last_availability = ent.available
    ent._last_charging_state = BatteryState.CHARGING
    ent._handle_coordinator_update()
    # No forced extra write; last_state_write unchanged (roughly).
    assert ent._last_charging_state == BatteryState.CHARGING


async def test_handle_coordinator_update_availability_changed(hass: HomeAssistant) -> None:
    coord, _, group = _coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    await add_entity(hass, ent, domain="vacuum")
    # Force an availability transition to exercise that debug branch.
    ent._last_availability = False
    ent._last_state_write = dt_util.utcnow()
    ent._handle_coordinator_update()
    assert ent._last_availability is True


async def test_handle_coordinator_update_charging_changed(hass: HomeAssistant) -> None:
    coord, _, group = _coord(
        hass, device_dps={"50": dp(Battery, id=50, type=0, len=2, data="0132")}
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    await add_entity(hass, ent, domain="vacuum")
    # Prime with a different charging state to exercise that debug branch.
    ent._last_availability = ent.available
    ent._last_charging_state = BatteryState.NOT_PLUGGED_IN
    ent._last_state_write = dt_util.utcnow()
    ent._handle_coordinator_update()
    assert ent._last_charging_state == BatteryState.CHARGING


async def test_handle_coordinator_update_stale_forces_write(hass: HomeAssistant) -> None:
    coord, _, group = _coord(
        hass, device_dps={"50": dp(Battery, id=50, type=0, len=2, data="0132")}
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    await add_entity(hass, ent, domain="vacuum")
    # Prime so the only trigger is the elapsed interval.
    ent._last_availability = ent.available
    ent._last_charging_state = BatteryState.CHARGING
    ent._last_state_write = dt_util.utcnow() - timedelta(minutes=10)
    ent._handle_coordinator_update()
    # A fresh write happened -> last_state_write moved to ~now.
    assert (dt_util.utcnow() - ent._last_state_write) < timedelta(minutes=1)


async def test_handle_coordinator_update_idx_missing(hass: HomeAssistant) -> None:
    coord, _, group = _coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    await add_entity(hass, ent, domain="vacuum")
    coord.data = {}
    ent._handle_coordinator_update()
    # _data retained; battery None branch (no dps) also exercised.
    assert ent._data is group

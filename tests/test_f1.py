"""Tests for F1 skimmer support across the WyBot platforms.

The F1 differs from the DS20 robots in ways that are easy to regress: it has no
dock, encodes DP 50 with an extra byte, uses cleaning modes outside the DS20
range, and reports itself docked while it is actually out skimming. These tests
pin that behaviour down and, just as importantly, assert that DS20 setups are
left alone.
"""

from __future__ import annotations

from homeassistant.components.vacuum import VacuumActivity, VacuumEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.wybot.binary_sensor import (
    WyBotDockChargingBinarySensor,
    WyBotFullyChargedBinarySensor,
)
from custom_components.wybot.const import F1_DEVICE_TYPE
from custom_components.wybot.sensor import (
    WyBotF1CleaningModeSensor,
    WyBotF1CycleRuntimeSensor,
    WyBotF1HeavyDirtSensor,
    WyBotF1InWaterSensor,
    WyBotF1MotorPWMSensor,
    WyBotF1PhSensor,
    WyBotF1RuntimeSensor,
    WyBotF1SolarBatterySensor,
    WyBotF1SonarSensor,
    WyBotF1TotalEnergySensor,
    WyBotRobotBatterySensor,
    WyBotSolarDockBatterySensor,
)
from custom_components.wybot.sensor import async_setup_entry as sensor_setup_entry
from custom_components.wybot.switch import WyBotF1AutoRunSwitch
from custom_components.wybot.switch import format_mac as switch_format_mac
from custom_components.wybot.switch import async_setup_entry as switch_setup_entry
from custom_components.wybot.vacuum import WyBotVacuum
from wybot.dp_models import (
    DP,
    AutoRunMode,
    Battery,
    CleaningMode,
    CleaningStatus,
    CleaningStatusMode,
    Dock,
    DockStatus,
    GenericDP,
    HeavyDirtMode,
    PhData,
)

from wybot_platform_helpers import add_entity, dp, make_coordinator, make_group

IDX = "grp1"

# DP 50 on the F1 is [charge_state][solar %][robot %]; "016302" is charging,
# 99% solar, 2% robot. The DS20 sends two bytes and no solar byte at all.
F1_BATTERY = "016302"
DS20_BATTERY = "0132"


def _f1_dps(battery=F1_BATTERY, **extra):
    dps = {"50": dp(Battery, id=50, type=0, len=3, data=battery)}
    dps.update(extra)
    return dps


def _f1_coord(hass, device_dps=None, with_docker=False):
    """Build a coordinator holding a single F1 group."""
    group = make_group(
        device_dps=device_dps if device_dps is not None else _f1_dps(),
        with_docker=with_docker,
        device_type=F1_DEVICE_TYPE,
    )
    coord, entry = make_coordinator(hass, {IDX: group})
    return coord, entry, group


def _ds20_coord(hass, device_dps=None):
    group = make_group(
        device_dps=device_dps
        if device_dps is not None
        else {"50": dp(Battery, id=50, type=0, len=2, data=DS20_BATTERY)},
    )
    coord, entry = make_coordinator(hass, {IDX: group})
    return coord, entry, group


async def test_coordinator_identifies_f1(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    assert coord.is_f1(IDX) is True


async def test_coordinator_does_not_identify_ds20_as_f1(hass: HomeAssistant) -> None:
    coord, _, _ = _ds20_coord(hass)
    assert coord.is_f1(IDX) is False


async def test_coordinator_is_f1_unknown_group(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    assert coord.is_f1("nope") is False


# ---------------------------------------------------------------------------
# Entity creation is gated on the device type
# ---------------------------------------------------------------------------


async def test_f1_sensors_created_for_f1(hass: HomeAssistant) -> None:
    coord, entry, _ = _f1_coord(hass)
    entry.runtime_data = coord
    added: list = []
    await sensor_setup_entry(hass, entry, lambda e: added.extend(e))
    names = {type(e).__name__ for e in added}
    assert "WyBotF1PhSensor" in names
    assert "WyBotF1SolarBatterySensor" in names


async def test_f1_sensors_not_created_for_ds20(hass: HomeAssistant) -> None:
    """A DS20 owner must not gain a screenful of permanently-unknown entities."""
    coord, entry, _ = _ds20_coord(hass)
    entry.runtime_data = coord
    added: list = []
    await sensor_setup_entry(hass, entry, lambda e: added.extend(e))
    assert not [e for e in added if type(e).__name__.startswith("WyBotF1")]


async def test_auto_run_switch_created_for_f1(hass: HomeAssistant) -> None:
    coord, entry, _ = _f1_coord(hass)
    entry.runtime_data = coord
    added: list = []
    await switch_setup_entry(hass, entry, lambda e: added.extend(e))
    assert len(added) == 1
    assert isinstance(added[0], WyBotF1AutoRunSwitch)


async def test_auto_run_switch_not_created_for_ds20(hass: HomeAssistant) -> None:
    coord, entry, _ = _ds20_coord(hass)
    entry.runtime_data = coord
    added: list = []
    await switch_setup_entry(hass, entry, lambda e: added.extend(e))
    assert added == []


# ---------------------------------------------------------------------------
# Battery handling
# ---------------------------------------------------------------------------


async def test_f1_solar_battery_reads_middle_byte(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotF1SolarBatterySensor(idx=IDX, coordinator=coord)
    assert ent.native_value == 99
    assert ent.unique_id == "wybot_grp1_f1_solar_battery"
    assert ent.extra_state_attributes == {"charge_state": "CHARGING"}


async def test_f1_solar_battery_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass, device_dps={})
    ent = WyBotF1SolarBatterySensor(idx=IDX, coordinator=coord)
    assert ent.native_value is None
    assert ent.extra_state_attributes == {}
    ent._data = None
    assert ent.native_value is None
    assert ent.extra_state_attributes == {}


async def test_robot_battery_unknown_when_f1_unplugged(hass: HomeAssistant) -> None:
    """The F1 freezes its robot byte on solar, so it must read as unknown."""
    coord, _, _ = _f1_coord(hass, device_dps=_f1_dps(battery="000e01"))
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    assert ent.native_value is None


async def test_robot_battery_reported_when_f1_plugged_in(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    assert ent.native_value == 2


async def test_robot_battery_unaffected_for_ds20(hass: HomeAssistant) -> None:
    """The DS20's two-byte payload has no staleness problem."""
    coord, _, _ = _ds20_coord(hass)
    ent = WyBotRobotBatterySensor(idx=IDX, coordinator=coord)
    assert ent.native_value == 50


async def test_solar_dock_battery_falls_back_to_f1_battery(
    hass: HomeAssistant,
) -> None:
    """With no DS20 dock DP, the dock battery sensor uses the F1 solar byte."""
    coord, _, _ = _f1_coord(hass)
    ent = WyBotSolarDockBatterySensor(idx=IDX, coordinator=coord)
    assert ent.native_value == 99


async def test_fully_charged_binary_sensor(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass, device_dps=_f1_dps(battery="026402"))
    ent = WyBotFullyChargedBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is True
    assert ent.unique_id == "wybot_grp1_fully_charged"
    assert ent.name == "Fully charged"
    attrs = ent.extra_state_attributes
    assert attrs["charge_state"] == "CHARGED"
    assert attrs["solar_battery"] == 100


async def test_fully_charged_off_while_charging(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotFullyChargedBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is False


async def test_fully_charged_no_data(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass, device_dps={})
    ent = WyBotFullyChargedBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is None
    assert ent.extra_state_attributes == {}
    ent._data = None
    assert ent.is_on is None
    assert ent.extra_state_attributes == {}


async def test_fully_charged_ds20_two_byte_omits_solar(hass: HomeAssistant) -> None:
    coord, _, _ = _ds20_coord(
        hass, device_dps={"50": dp(Battery, id=50, type=0, len=2, data="0264")}
    )
    ent = WyBotFullyChargedBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is True
    assert "solar_battery" not in ent.extra_state_attributes


async def test_dock_charging_falls_back_to_f1_charge_state(
    hass: HomeAssistant,
) -> None:
    """The F1 has no DP 222, so charging comes from the battery charge state."""
    coord, _, _ = _f1_coord(hass)
    ent = WyBotDockChargingBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is True


async def test_dock_charging_f1_not_charging(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass, device_dps=_f1_dps(battery="000e01"))
    ent = WyBotDockChargingBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is False


async def test_dock_charging_none_without_any_dp(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass, device_dps={})
    ent = WyBotDockChargingBinarySensor(idx=IDX, coordinator=coord)
    assert ent.is_on is None


# ---------------------------------------------------------------------------
# Cleaning mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [("0e", "Smart"), ("0f", "Standard")],
)
async def test_f1_cleaning_mode_sensor(
    hass: HomeAssistant, data: str, expected: str
) -> None:
    coord, _, _ = _f1_coord(
        hass, device_dps=_f1_dps(**{"1": dp(CleaningMode, id=1, type=4, len=1, data=data)})
    )
    ent = WyBotF1CleaningModeSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == expected


async def test_f1_cleaning_mode_sensor_no_dp(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotF1CleaningModeSensor(idx=IDX, coordinator=coord)
    assert ent.native_value is None
    ent._data = None
    assert ent.native_value is None


async def test_f1_fan_speed_list(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.fan_speed_list == ["Smart", "Standard"]


async def test_ds20_fan_speed_list_unchanged(hass: HomeAssistant) -> None:
    coord, _, _ = _ds20_coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.fan_speed_list == CleaningMode.CLEANING_MODES


async def test_f1_fan_speed(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(
        hass, device_dps=_f1_dps(**{"1": dp(CleaningMode, id=1, type=4, len=1, data="0e")})
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.fan_speed == "Smart"


async def test_fan_speed_none_for_mode_outside_list(hass: HomeAssistant) -> None:
    """A DS20 mode reported by an F1 is not a valid F1 fan speed."""
    coord, _, _ = _f1_coord(
        hass, device_dps=_f1_dps(**{"1": dp(CleaningMode, id=1, type=4, len=1, data="03")})
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.fan_speed is None


async def test_set_fan_speed_rejects_unsupported_mode(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    with pytest.raises(ServiceValidationError):
        await ent.async_set_fan_speed("Turbo Floor")


# ---------------------------------------------------------------------------
# Vacuum activity ordering
# ---------------------------------------------------------------------------


async def test_f1_cleaning_wins_over_phantom_dock(hass: HomeAssistant) -> None:
    """The F1 claims DP 11 = DOCKED while skimming; cleaning must win."""
    coord, _, _ = _f1_coord(
        hass,
        device_dps=_f1_dps(
            **{
                "0": dp(CleaningStatus, id=0, type=4, len=1, data="03"),
                "11": dp(Dock, id=11, type=4, len=1, data="00"),
            }
        ),
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.CLEANING


async def test_ds20_docked_wins_over_stopped(hass: HomeAssistant) -> None:
    """A docked DS20 reporting STOPPED must read DOCKED, not PAUSED."""
    coord, _, _ = _ds20_coord(
        hass,
        device_dps={
            "50": dp(Battery, id=50, type=0, len=2, data=DS20_BATTERY),
            "0": dp(CleaningStatus, id=0, type=4, len=1, data="01"),
            "11": dp(Dock, id=11, type=4, len=1, data="00"),
        },
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.DOCKED


async def test_f1_standby_reads_idle(hass: HomeAssistant) -> None:
    """DP 0 = 0x0f is the F1's standby; pywybot surfaces it as UNKNOWN."""
    coord, _, _ = _f1_coord(
        hass,
        device_dps=_f1_dps(**{"0": dp(CleaningStatus, id=0, type=4, len=1, data="0f")}),
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.IDLE


async def test_ds20_unknown_status_is_not_idle(hass: HomeAssistant) -> None:
    """Only the F1 maps an unknown cleaning status to idle."""
    coord, _, _ = _ds20_coord(
        hass,
        device_dps={"0": dp(CleaningStatus, id=0, type=4, len=1, data="0f")},
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity is None


async def test_f1_returning_still_wins(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(
        hass,
        device_dps=_f1_dps(**{"0": dp(CleaningStatus, id=0, type=4, len=1, data="04")}),
    )
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.RETURNING


async def test_f1_docked_when_charging_and_idle(hass: HomeAssistant) -> None:
    """A charging F1 with no cleaning DP still falls through to docked."""
    coord, _, _ = _f1_coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert ent.activity == VacuumActivity.DOCKED


async def test_vacuum_does_not_advertise_turn_on_off(hass: HomeAssistant) -> None:
    """StateVacuumEntity does not support TURN_ON/TURN_OFF."""
    coord, _, _ = _f1_coord(hass)
    ent = WyBotVacuum(idx=IDX, coordinator=coord)
    assert not ent.supported_features & VacuumEntityFeature.TURN_ON
    assert not ent.supported_features & VacuumEntityFeature.TURN_OFF


# ---------------------------------------------------------------------------
# Auto-run switch
# ---------------------------------------------------------------------------


async def test_auto_run_switch_state(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(
        hass, device_dps=_f1_dps(**{"207": dp(AutoRunMode, id=207, type=4, len=1, data="01")})
    )
    ent = WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord)
    assert ent.is_on is True
    assert ent.unique_id == "wybot_grp1_f1_auto_run_switch"
    assert ent.name == "Auto run"


async def test_auto_run_switch_off(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(
        hass, device_dps=_f1_dps(**{"207": dp(AutoRunMode, id=207, type=4, len=1, data="00")})
    )
    ent = WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord)
    assert ent.is_on is False


async def test_auto_run_switch_unknown_without_dp(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord)
    assert ent.is_on is None
    ent._data = None
    assert ent.is_on is None


@pytest.mark.parametrize(("turn_on", "expected"), [(True, "01"), (False, "00")])
async def test_auto_run_switch_sends_dp_207(
    hass: HomeAssistant, turn_on: bool, expected: str
) -> None:
    coord, _, group = _f1_coord(hass)
    sent: list[GenericDP] = []

    async def _send(target, command):
        sent.append(command)
        return True

    coord.async_send_command = _send
    ent = WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord)
    if turn_on:
        await ent.async_turn_on()
    else:
        await ent.async_turn_off()
    assert len(sent) == 1
    assert sent[0].id == 207
    assert sent[0].data == expected


async def test_auto_run_switch_raises_when_send_fails(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)

    async def _send(target, command):
        return False

    coord.async_send_command = _send
    ent = WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord)
    with pytest.raises(HomeAssistantError):
        await ent.async_turn_on()


async def test_auto_run_switch_raises_without_data(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord)
    ent._data = None
    with pytest.raises(HomeAssistantError):
        await ent.async_turn_on()


# ---------------------------------------------------------------------------
# Raw-DP sensors
# ---------------------------------------------------------------------------


async def test_ph_sensor(hass: HomeAssistant) -> None:
    """pH and electrode temperature are little-endian, scaled by 100 and 10."""
    coord, _, _ = _f1_coord(
        hass,
        device_dps=_f1_dps(
            **{"142": dp(PhData, id=142, type=2, len=4, data="d0021901")}
        ),
    )
    ent = WyBotF1PhSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == 7.2
    assert ent.extra_state_attributes == {"electrode_temperature": 28.1}


async def test_ph_sensor_no_dp(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotF1PhSensor(idx=IDX, coordinator=coord)
    assert ent.native_value is None
    assert ent.extra_state_attributes == {}
    ent._data = None
    assert ent.native_value is None
    assert ent.extra_state_attributes == {}


async def test_heavy_dirt_sensor(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(
        hass,
        device_dps=_f1_dps(
            **{"145": dp(HeavyDirtMode, id=145, type=4, len=1, data="01")}
        ),
    )
    ent = WyBotF1HeavyDirtSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == "Enabled"


async def test_heavy_dirt_sensor_disabled(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(
        hass,
        device_dps=_f1_dps(
            **{"145": dp(HeavyDirtMode, id=145, type=4, len=1, data="00")}
        ),
    )
    ent = WyBotF1HeavyDirtSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == "Disabled"


async def test_heavy_dirt_sensor_no_dp(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotF1HeavyDirtSensor(idx=IDX, coordinator=coord)
    assert ent.native_value is None
    ent._data = None
    assert ent.native_value is None


@pytest.mark.parametrize(
    ("entity_cls", "dp_id", "data", "expected"),
    [
        (WyBotF1InWaterSensor, "212", "01", "Yes"),
        (WyBotF1InWaterSensor, "212", "00", "No"),
        (WyBotF1SonarSensor, "209", "01", "Active"),
        (WyBotF1SonarSensor, "209", "00", "Inactive"),
    ],
)
async def test_boolean_raw_dp_sensors(
    hass: HomeAssistant, entity_cls, dp_id: str, data: str, expected: str
) -> None:
    coord, _, _ = _f1_coord(
        hass,
        device_dps=_f1_dps(**{dp_id: DP(id=int(dp_id), type=4, len=1, data=data)}),
    )
    ent = entity_cls(idx=IDX, coordinator=coord)
    assert ent.native_value == expected


@pytest.mark.parametrize(
    "entity_cls", [WyBotF1InWaterSensor, WyBotF1SonarSensor]
)
async def test_boolean_raw_dp_sensors_no_dp(hass: HomeAssistant, entity_cls) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = entity_cls(idx=IDX, coordinator=coord)
    assert ent.native_value is None
    ent._data = None
    assert ent.native_value is None


@pytest.mark.parametrize(
    ("entity_cls", "dp_id", "expected"),
    [
        (WyBotF1RuntimeSensor, "25", 1000),
        (WyBotF1CycleRuntimeSensor, "55", 1000),
        (WyBotF1TotalEnergySensor, "34", 1000),
        (WyBotF1MotorPWMSensor, "81", 1000),
    ],
)
async def test_le_raw_dp_sensors(
    hass: HomeAssistant, entity_cls, dp_id: str, expected: int
) -> None:
    coord, _, _ = _f1_coord(
        hass,
        device_dps=_f1_dps(
            **{dp_id: DP(id=int(dp_id), type=2, len=4, data="e8030000")}
        ),
    )
    ent = entity_cls(idx=IDX, coordinator=coord)
    assert ent.native_value == expected


@pytest.mark.parametrize(
    "entity_cls",
    [
        WyBotF1RuntimeSensor,
        WyBotF1CycleRuntimeSensor,
        WyBotF1TotalEnergySensor,
        WyBotF1MotorPWMSensor,
    ],
)
async def test_le_raw_dp_sensors_absent(hass: HomeAssistant, entity_cls) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = entity_cls(idx=IDX, coordinator=coord)
    assert ent.native_value is None
    ent._data = None
    assert ent.native_value is None


async def test_runtime_sensors_fall_back_to_alternate_dp(
    hass: HomeAssistant,
) -> None:
    coord, _, _ = _f1_coord(
        hass,
        device_dps=_f1_dps(
            **{"111": DP(id=111, type=2, len=4, data="e8030000")}
        ),
    )
    assert WyBotF1RuntimeSensor(idx=IDX, coordinator=coord).native_value == 1000

    coord, _, _ = _f1_coord(
        hass,
        device_dps=_f1_dps(
            **{"129": DP(id=129, type=2, len=4, data="e8030000")}
        ),
    )
    assert WyBotF1CycleRuntimeSensor(idx=IDX, coordinator=coord).native_value == 1000


async def test_le_raw_dp_sensor_ignores_malformed_payload(
    hass: HomeAssistant,
) -> None:
    coord, _, _ = _f1_coord(
        hass, device_dps=_f1_dps(**{"25": DP(id=25, type=2, len=4, data="zzzz")})
    )
    ent = WyBotF1RuntimeSensor(idx=IDX, coordinator=coord)
    assert ent.native_value is None


async def test_raw_dp_read_from_dock_when_absent_on_device(
    hass: HomeAssistant,
) -> None:
    """DS20-style setups keep working: DPs may live on the dock instead."""
    group = make_group(
        device_dps={},
        docker_dps={"25": DP(id=25, type=2, len=4, data="e8030000")},
        device_type=F1_DEVICE_TYPE,
    )
    coord, _ = make_coordinator(hass, {IDX: group})
    ent = WyBotF1RuntimeSensor(idx=IDX, coordinator=coord)
    assert ent.native_value == 1000


# ---------------------------------------------------------------------------
# Switch platform plumbing
# ---------------------------------------------------------------------------


def test_switch_format_mac() -> None:
    assert switch_format_mac("CCBA97932A96") == "CC:BA:97:93:2A:96"
    assert switch_format_mac("cc-ba-97-93-2a-96") == "CC:BA:97:93:2A:96"


async def test_switch_setup_skips_already_known_devices(hass: HomeAssistant) -> None:
    """The listener re-runs on every refresh but must not duplicate entities."""
    coord, entry, _ = _f1_coord(hass)
    entry.runtime_data = coord
    added: list = []
    await switch_setup_entry(hass, entry, lambda e: added.extend(e))
    assert len(added) == 1
    coord.async_update_listeners()
    assert len(added) == 1


async def test_switch_device_info(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord)
    info = ent.device_info
    assert info["identifiers"] == {("wybot", "grp1")}
    assert info["name"] == "My Pool"
    assert info["model"] == F1_DEVICE_TYPE
    assert ("bluetooth", "CC:BA:97:93:2A:96") in info["connections"]
    # The F1 has no dock, so nothing to hang the device off.
    assert info["via_device"] is None


async def test_switch_device_info_via_dock(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass, with_docker=True)
    ent = WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord)
    assert ent.device_info["via_device"] == ("wybot", "grp1_dock")


async def test_switch_device_info_falls_back_through_names(
    hass: HomeAssistant,
) -> None:
    coord, _, group = _f1_coord(hass)
    ent = WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord)

    group.name = None
    assert ent.device_info["name"] == "Robot"

    group.device.device_name = None
    assert ent.device_info["name"] == F1_DEVICE_TYPE

    ent._data = None
    assert ent.device_info["name"] == "Unknown"
    assert ent.device_info["model"] == "Unknown"


async def test_switch_availability(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord)
    assert ent.available is True

    ent._data = None
    assert ent.available is False

    ent._data = coord.data[IDX]
    coord.data = {}
    assert ent.available is False

    coord._connection_available = False
    assert ent.available is False


async def test_switch_handles_coordinator_update(hass: HomeAssistant) -> None:
    coord, _, _ = _f1_coord(hass)
    ent = await add_entity(hass, WyBotF1AutoRunSwitch(idx=IDX, coordinator=coord), "switch")
    updated = make_group(
        device_dps=_f1_dps(
            **{"207": dp(AutoRunMode, id=207, type=4, len=1, data="01")}
        ),
        with_docker=False,
        device_type=F1_DEVICE_TYPE,
    )
    coord.data = {IDX: updated}
    ent._handle_coordinator_update()
    assert ent.is_on is True

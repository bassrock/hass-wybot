"""Tests for wybot_dp_models.py — Data Point models."""

import pytest

from custom_components.wybot.wybot_dp_models import (
    DP,
    Battery,
    BatteryState,
    CleaningMode,
    CleaningStatus,
    CleaningStatusMode,
    ConnectionStatus,
    Dock,
    DockConnectionStatus,
    DockInfo,
    DockStatus,
    DockType,
    GenericDP,
    Schedule,
    SolarDockBattery,
    SolarEnergyHarvested,
    SolarStatus,
    SolarStatusMode,
    DeviceStatus,
    wybot_dp_id,
)


# =============================================================================
# DP BaseModel
# =============================================================================


class TestDP:
    """Tests for the DP pydantic model."""

    def test_create_full(self):
        dp = DP(id=0, type=4, len=1, data="03")
        assert dp.id == 0
        assert dp.type == 4
        assert dp.len == 1
        assert dp.data == "03"

    def test_create_query_only(self):
        """Query DPs only have id, other fields should default to None."""
        dp = DP(id=0)
        assert dp.id == 0
        assert dp.type is None
        assert dp.len is None
        assert dp.data is None

    def test_create_from_dict(self):
        dp = DP.model_validate({"id": 50, "type": 0, "len": 2, "data": "0132"})
        assert dp.id == 50
        assert dp.data == "0132"

    def test_serialization(self):
        dp = DP(id=1, type=4, len=1, data="00")
        dumped = dp.model_dump()
        assert dumped == {"id": 1, "type": 4, "len": 1, "data": "00"}


# =============================================================================
# GenericDP
# =============================================================================


class TestGenericDP:
    """Tests for the GenericDP plain Python class."""

    def test_init_from_dp(self):
        dp = DP(id=99, type=5, len=2, data="ab")
        generic = GenericDP(dp)
        assert generic.id == 99
        assert generic.type == 5
        assert generic.len == 2
        assert generic.data == "ab"

    def test_dict_method(self):
        dp = DP(id=0, type=4, len=1, data="03")
        generic = GenericDP(dp)
        result = generic.dict()
        assert result == {"id": 0, "type": 4, "len": 1, "data": "03"}

    def test_str_repr(self):
        dp = DP(id=0, type=4, len=1, data="03")
        generic = GenericDP(dp)
        s = str(generic)
        assert "GenericDP" in s
        assert "03" in s


# =============================================================================
# CleaningStatus
# =============================================================================


class TestCleaningStatus:
    """Tests for CleaningStatus DP."""

    def test_cleaning(self, sample_dp_data):
        dp = DP(**sample_dp_data["cleaning_status_cleaning"])
        cs = CleaningStatus(dp)
        assert cs.status == CleaningStatusMode.CLEANING

    def test_stopped(self, sample_dp_data):
        dp = DP(**sample_dp_data["cleaning_status_stopped"])
        cs = CleaningStatus(dp)
        assert cs.status == CleaningStatusMode.STOPPED

    def test_returning(self, sample_dp_data):
        dp = DP(**sample_dp_data["cleaning_status_returning"])
        cs = CleaningStatus(dp)
        assert cs.status == CleaningStatusMode.RETURNING

    def test_returning_to_dock(self, sample_dp_data):
        dp = DP(**sample_dp_data["cleaning_status_returning_dock"])
        cs = CleaningStatus(dp)
        assert cs.status == CleaningStatusMode.RETURNING_TO_DOCK

    def test_setter(self):
        cs = CleaningStatus()
        cs.status = CleaningStatusMode.CLEANING
        assert cs.data == "03"
        assert cs.status == CleaningStatusMode.CLEANING

    def test_setter_stopped(self):
        cs = CleaningStatus()
        cs.status = CleaningStatusMode.STOPPED
        assert cs.data == "01"

    def test_no_data_returns_unknown(self):
        cs = CleaningStatus()
        assert cs.status == CleaningStatusMode.UNKNOWN

    def test_init_with_status_kwarg(self):
        cs = CleaningStatus(status=CleaningStatusMode.CLEANING)
        assert cs.status == CleaningStatusMode.CLEANING
        assert cs.data == "03"

    def test_str_repr(self):
        cs = CleaningStatus(status=CleaningStatusMode.CLEANING)
        assert "CleaningStatus" in str(cs)
        assert "CLEANING" in str(cs)


# =============================================================================
# CleaningMode
# =============================================================================


class TestCleaningMode:
    """Tests for CleaningMode DP."""

    def test_floor_mode(self, sample_dp_data):
        dp = DP(**sample_dp_data["cleaning_mode_floor"])
        cm = CleaningMode(dp)
        assert cm.cleaning_mode == "Floor"

    def test_wall_mode(self, sample_dp_data):
        dp = DP(**sample_dp_data["cleaning_mode_wall"])
        cm = CleaningMode(dp)
        assert cm.cleaning_mode == "Wall"

    def test_all_modes_roundtrip(self):
        for mode_name in CleaningMode.CLEANING_MODES:
            cm = CleaningMode(mode=mode_name)
            assert cm.cleaning_mode == mode_name

    def test_setter(self):
        cm = CleaningMode()
        cm.cleaning_mode = "Turbo Floor"
        assert cm.cleaning_mode == "Turbo Floor"
        assert cm.data == "05"

    def test_no_data_defaults_to_floor(self):
        cm = CleaningMode()
        assert cm.cleaning_mode == "Floor"


# =============================================================================
# Battery
# =============================================================================


class TestBattery:
    """Tests for Battery DP."""

    def test_charging_50(self, sample_dp_data):
        dp = DP(**sample_dp_data["battery_charging_50"])
        bat = Battery(dp)
        assert bat.charge_state == BatteryState.CHARGING
        assert bat.battery_level == 50  # 0x32 = 50

    def test_charged_100(self, sample_dp_data):
        dp = DP(**sample_dp_data["battery_charged_100"])
        bat = Battery(dp)
        assert bat.charge_state == BatteryState.CHARGED
        assert bat.battery_level == 100  # 0x64 = 100

    def test_unplugged_75(self, sample_dp_data):
        dp = DP(**sample_dp_data["battery_unplugged_75"])
        bat = Battery(dp)
        assert bat.charge_state == BatteryState.NOT_PLUGGED_IN
        assert bat.battery_level == 75  # 0x4b = 75

    def test_no_data(self):
        dp = DP(id=50, type=0, len=2, data=None)
        bat = Battery(dp)
        assert bat.battery_level == 0
        assert bat.charge_state == BatteryState.NOT_PLUGGED_IN


# =============================================================================
# Dock
# =============================================================================


class TestDock:
    """Tests for Dock DP."""

    def test_docked(self, sample_dp_data):
        dp = DP(**sample_dp_data["dock_docked"])
        dock = Dock(dp)
        assert dock.status == DockStatus.DOCKED

    def test_returning(self, sample_dp_data):
        dp = DP(**sample_dp_data["dock_returning"])
        dock = Dock(dp)
        assert dock.status == DockStatus.RETURNING

    def test_setter(self):
        dock = Dock(status=DockStatus.RETURNING)
        assert dock.data == "01"

    def test_no_data(self):
        dock = Dock()
        assert dock.status == DockStatus.GENERAL


# =============================================================================
# Solar DP classes
# =============================================================================


class TestSolarEnergyHarvested:
    """Tests for SolarEnergyHarvested DP."""

    def test_energy_wh(self, sample_dp_data):
        dp = DP(**sample_dp_data["solar_energy"])
        solar = SolarEnergyHarvested(dp)
        # "e8030000" little-endian = 0x000003e8 = 1000 Wh
        assert solar.energy_wh == 1000

    def test_energy_kwh(self, sample_dp_data):
        dp = DP(**sample_dp_data["solar_energy"])
        solar = SolarEnergyHarvested(dp)
        assert solar.energy_kwh == 1.0

    def test_no_data(self):
        dp = DP(id=131, type=2, len=4, data=None)
        solar = SolarEnergyHarvested(dp)
        assert solar.energy_wh == 0


class TestSolarDockBattery:
    """Tests for SolarDockBattery DP."""

    def test_battery_level(self, sample_dp_data):
        dp = DP(**sample_dp_data["solar_dock_battery"])
        bat = SolarDockBattery(dp)
        # "01480a" -> byte 1 (chars 2-3) = 0x48 = 72%
        assert bat.battery_level == 72

    def test_no_data(self):
        dp = DP(id=221, type=0, len=3, data=None)
        bat = SolarDockBattery(dp)
        assert bat.battery_level == 0


class TestSolarStatus:
    """Tests for SolarStatus DP."""

    def test_charging(self, sample_dp_data):
        dp = DP(**sample_dp_data["solar_status_charging"])
        ss = SolarStatus(dp)
        assert ss.is_charging is True
        assert ss.status == SolarStatusMode.CHARGING

    def test_not_charging(self, sample_dp_data):
        dp = DP(**sample_dp_data["solar_status_not_charging"])
        ss = SolarStatus(dp)
        assert ss.is_charging is False
        assert ss.status == SolarStatusMode.NOT_CHARGING


class TestDockInfo:
    """Tests for DockInfo DP."""

    def test_solar_dock(self, sample_dp_data):
        dp = DP(**sample_dp_data["dock_info_solar"])
        di = DockInfo(dp)
        assert di.dock_type == DockType.SOLAR
        assert di.is_solar_dock is True

    def test_unknown_type(self):
        dp = DP(id=214, type=4, len=1, data="ff")
        di = DockInfo(dp)
        assert di.dock_type == DockType.UNKNOWN
        assert di.is_solar_dock is False


class TestDockConnectionStatus:
    """Tests for DockConnectionStatus DP."""

    def test_docked(self, sample_dp_data):
        dp = DP(**sample_dp_data["dock_connection_docked"])
        dcs = DockConnectionStatus(dp)
        assert dcs.is_docked is True

    def test_undocked(self, sample_dp_data):
        dp = DP(**sample_dp_data["dock_connection_undocked"])
        dcs = DockConnectionStatus(dp)
        assert dcs.is_docked is False


class TestConnectionStatus:
    """Tests for ConnectionStatus DP."""

    def test_connected(self):
        dp = DP(id=212, type=4, len=1, data="01")
        cs = ConnectionStatus(dp)
        assert cs.is_connected is True

    def test_disconnected(self):
        dp = DP(id=212, type=4, len=1, data="00")
        cs = ConnectionStatus(dp)
        assert cs.is_connected is False


class TestDeviceStatus:
    """Tests for DeviceStatus DP."""

    def test_status_value(self):
        dp = DP(id=209, type=4, len=1, data="03")
        ds = DeviceStatus(dp)
        assert ds.status_value == 3


class TestSchedule:
    """Tests for Schedule DP."""

    def test_raw_schedule(self):
        dp = DP(id=79, type=2, len=12, data="abcdef123456abcdef123456")
        sc = Schedule(dp)
        assert sc.raw_schedule == "abcdef123456abcdef123456"


# =============================================================================
# wybot_dp_id mapping
# =============================================================================


class TestDPMapping:
    """Tests for the wybot_dp_id mapping dict."""

    def test_known_ids_return_correct_class(self):
        assert wybot_dp_id[0] is CleaningStatus
        assert wybot_dp_id[1] is CleaningMode
        assert wybot_dp_id[11] is Dock
        assert wybot_dp_id[50] is Battery
        assert wybot_dp_id[79] is Schedule
        assert wybot_dp_id[131] is SolarEnergyHarvested
        assert wybot_dp_id[214] is DockInfo
        assert wybot_dp_id[221] is SolarDockBattery
        assert wybot_dp_id[222] is SolarStatus

    def test_unknown_id_falls_back_to_generic(self):
        """wybot_dp_id.get(unknown_id, GenericDP) should return GenericDP."""
        result = wybot_dp_id.get(9999, GenericDP)
        assert result is GenericDP

    def test_all_mapped_classes_can_instantiate(self, sample_dp_data):
        """Verify every mapped DP class can be instantiated from a DP."""
        for dp_id, dp_class in wybot_dp_id.items():
            dp = DP(id=dp_id, type=4, len=1, data="01")
            instance = dp_class(dp)
            assert instance.id == dp_id

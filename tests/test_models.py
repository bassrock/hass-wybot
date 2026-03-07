"""Tests for wybot_models.py — HTTP/MQTT response models."""

import pytest

from custom_components.wybot.wybot_dp_models import (
    DP,
    Battery,
    CleaningMode,
    CleaningStatus,
    GenericDP,
)
from custom_components.wybot.wybot_models import (
    Command,
    Device,
    DeviceMetadata,
    DevicesResponse,
    Docker,
    Group,
    LoginMetadata,
    LoginResponse,
    Version,
    Vision,
    to_snake_case,
)


# =============================================================================
# to_snake_case helper
# =============================================================================


class TestToSnakeCase:
    """Tests for the to_snake_case helper function."""

    def test_camel_case(self):
        assert to_snake_case("deviceId") == "device_id"

    def test_pascal_case(self):
        assert to_snake_case("DeviceName") == "device_name"

    def test_already_snake(self):
        assert to_snake_case("device_id") == "device_id"

    def test_single_word(self):
        assert to_snake_case("token") == "token"

    def test_consecutive_caps(self):
        assert to_snake_case("deviceID") == "device_i_d"

    def test_empty_string(self):
        assert to_snake_case("") == ""


# =============================================================================
# Command
# =============================================================================


class TestCommand:
    """Tests for the Command model."""

    def test_create_from_dict(self, sample_command_data):
        cmd = Command(**sample_command_data)
        assert cmd.cmd == 5
        assert cmd.ts == 1700000000
        assert len(cmd.dp) == 3

    def test_dp_list_types(self, sample_command_data):
        cmd = Command(**sample_command_data)
        for dp in cmd.dp:
            assert isinstance(dp, DP)

    def test_get_dps_as_keyed_dict(self, sample_command_data):
        cmd = Command(**sample_command_data)
        dp_dict = cmd.get_dps_as_keyed_dict()
        assert "0" in dp_dict
        assert "1" in dp_dict
        assert "50" in dp_dict
        assert isinstance(dp_dict["0"], CleaningStatus)
        assert isinstance(dp_dict["1"], CleaningMode)
        assert isinstance(dp_dict["50"], Battery)

    def test_unknown_dp_id_defaults_to_generic(self):
        cmd = Command(
            cmd=5,
            ts=0,
            dp=[DP(id=9999, type=4, len=1, data="01")],
        )
        dp_dict = cmd.get_dps_as_keyed_dict()
        assert isinstance(dp_dict["9999"], GenericDP)

    def test_populate_by_name(self):
        """Test that snake_case field names work directly."""
        cmd = Command(cmd=4, dp=[], ts=100)
        assert cmd.cmd == 4


# =============================================================================
# LoginMetadata / LoginResponse
# =============================================================================


class TestLoginModels:
    """Tests for login-related models."""

    def test_login_metadata_from_api(self):
        data = {
            "userId": "user1",
            "token": "abc123",
            "username": "test@test.com",
            "name": "Test User",
            "avatar": "https://example.com/avatar.png",
            "groupid": 1,
            "regTime": 1600000000,
            "lastLoginTime": 1700000000,
        }
        meta = LoginMetadata(**data)
        assert meta.user_id == "user1"
        assert meta.token == "abc123"
        assert meta.reg_time == 1600000000
        assert meta.last_login_time == 1700000000

    def test_login_response_success(self):
        data = {
            "code": 200,
            "reason": "OK",
            "message": "Success",
            "metadata": {
                "userId": "user1",
                "token": "abc123",
                "username": "test@test.com",
                "name": "Test",
                "avatar": "",
                "groupid": 1,
                "regTime": 0,
                "lastLoginTime": 0,
            },
        }
        resp = LoginResponse(**data)
        assert resp.code == 200
        assert resp.metadata is not None
        assert resp.metadata.token == "abc123"

    def test_login_response_no_metadata(self):
        data = {
            "code": 401,
            "reason": "Unauthorized",
            "message": "Invalid credentials",
        }
        resp = LoginResponse(**data)
        assert resp.code == 401
        assert resp.metadata is None


# =============================================================================
# Version
# =============================================================================


class TestVersion:
    """Tests for the Version model."""

    def test_from_api_alias(self):
        v = Version(**{"Firmware": "1.2.3"})
        assert v.firmware == "1.2.3"

    def test_none_firmware(self):
        v = Version(**{"Firmware": None})
        assert v.firmware is None


# =============================================================================
# Device
# =============================================================================


class TestDevice:
    """Tests for the Device model."""

    def test_from_api(self, sample_api_device):
        dev = Device(**sample_api_device)
        assert dev.device_id == "dev123"
        assert dev.device_name == "Pool Robot"
        assert dev.device_type == "S2 Pro"
        assert dev.ble_name == "CCBA97932A96"
        assert dev.pool_id == "pool1"
        assert dev.auto_update == "1"
        assert dev.version is not None
        assert dev.version.firmware == "1.2.3"

    def test_defaults(self, sample_api_device):
        dev = Device(**sample_api_device)
        assert dev.online is False
        assert dev.dps == {}

    def test_get_dp_returns_none_when_empty(self, sample_api_device):
        dev = Device(**sample_api_device)
        assert dev.get_dp(CleaningStatus) is None

    def test_get_dp_raises_for_non_generic(self, sample_api_device):
        dev = Device(**sample_api_device)
        with pytest.raises(TypeError):
            dev.get_dp(str)  # type: ignore


# =============================================================================
# Docker
# =============================================================================


class TestDocker:
    """Tests for the Docker model."""

    def test_from_api(self, sample_api_docker):
        dock = Docker(**sample_api_docker)
        assert dock.docker_id == "dock456"
        assert dock.docker_type == "DS20"
        assert dock.ble_name == "3C8427565A1A"
        assert dock.device_status == "online"
        assert dock.docker_status == "active"

    def test_defaults(self, sample_api_docker):
        dock = Docker(**sample_api_docker)
        assert dock.online is False
        assert dock.dps == {}


# =============================================================================
# Vision
# =============================================================================


class TestVision:
    """Tests for the Vision model."""

    def test_from_api(self, sample_api_vision):
        vis = Vision(**sample_api_vision)
        assert vis.vision_id == "vis789"
        assert vis.privacy is False
        assert vis.policy is True
        assert vis.log is None


# =============================================================================
# Group
# =============================================================================


class TestGroup:
    """Tests for the Group model."""

    def test_from_api(self, sample_api_group):
        group = Group(**sample_api_group)
        assert group.name == "My Pool"
        assert group.id == "group1"
        assert group.device is not None
        assert group.docker is not None
        assert group.vision is not None

    def test_without_docker(self, sample_api_device, sample_api_vision):
        data = {
            "device": sample_api_device,
            "docker": None,
            "vision": sample_api_vision,
            "name": "Solo Pool",
            "id": "group2",
            "autoUpdate": "0",
        }
        group = Group(**data)
        assert group.docker is None
        assert group.device.device_id == "dev123"

    def test_get_dp_searches_device_first(self, sample_api_group):
        group = Group(**sample_api_group)
        # No DPs loaded, should return None
        assert group.get_dp(CleaningStatus) is None

    def test_get_dp_searches_docker_fallback(self, sample_api_group):
        group = Group(**sample_api_group)
        # Add a DP to docker
        dp = DP(id=0, type=4, len=1, data="03")
        cs = CleaningStatus(dp)
        group.docker.dps = {"0": cs}
        # Should find it via docker fallback
        found = group.get_dp(CleaningStatus)
        assert found is not None
        assert isinstance(found, CleaningStatus)


# =============================================================================
# DevicesResponse (full API response)
# =============================================================================


class TestDevicesResponse:
    """Tests for the full API response model."""

    def test_full_response(self, sample_api_group):
        data = {
            "code": 200,
            "reason": "OK",
            "message": "Success",
            "metadata": {"groups": [sample_api_group]},
        }
        resp = DevicesResponse(**data)
        assert resp.code == 200
        assert len(resp.metadata.groups) == 1
        assert resp.metadata.groups[0].name == "My Pool"
        assert resp.metadata.groups[0].device.device_id == "dev123"

    def test_empty_groups(self):
        data = {
            "code": 200,
            "reason": "OK",
            "message": "No devices",
            "metadata": {"groups": []},
        }
        resp = DevicesResponse(**data)
        assert len(resp.metadata.groups) == 0

"""Fixtures and helpers for hass-wybot tests.

Tests run against the real Home Assistant test harness
(``pytest-homeassistant-custom-component``); the ``wybot`` custom integration is
loaded via ``enable_custom_integrations``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the repo root is importable so ``custom_components.wybot`` resolves.
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the wybot custom integration in every test."""
    yield


@pytest.fixture
def sample_dp_data():
    """Return sample DP data dicts for testing."""
    return {
        "cleaning_status_cleaning": {"id": 0, "type": 4, "len": 1, "data": "03"},
        "cleaning_status_stopped": {"id": 0, "type": 4, "len": 1, "data": "01"},
        "cleaning_status_returning": {"id": 0, "type": 4, "len": 1, "data": "02"},
        "cleaning_status_returning_dock": {"id": 0, "type": 4, "len": 1, "data": "04"},
        "cleaning_mode_floor": {"id": 1, "type": 4, "len": 1, "data": "00"},
        "cleaning_mode_wall": {"id": 1, "type": 4, "len": 1, "data": "01"},
        "battery_charging_50": {"id": 50, "type": 0, "len": 2, "data": "0132"},
        "battery_charged_100": {"id": 50, "type": 0, "len": 2, "data": "0264"},
        "battery_unplugged_75": {"id": 50, "type": 0, "len": 2, "data": "004b"},
        "dock_docked": {"id": 11, "type": 4, "len": 1, "data": "00"},
        "dock_returning": {"id": 11, "type": 4, "len": 1, "data": "01"},
        "solar_energy": {"id": 131, "type": 2, "len": 4, "data": "e8030000"},
        "solar_dock_battery": {"id": 221, "type": 0, "len": 3, "data": "01480a"},
        "solar_status_charging": {"id": 222, "type": 0, "len": 1, "data": "01"},
        "solar_status_not_charging": {"id": 222, "type": 0, "len": 1, "data": "00"},
        "dock_info_solar": {"id": 214, "type": 4, "len": 1, "data": "05"},
        "dock_connection_docked": {"id": 213, "type": 4, "len": 1, "data": "01"},
        "dock_connection_undocked": {"id": 213, "type": 4, "len": 1, "data": "00"},
        "query_only": {"id": 0},
    }


@pytest.fixture
def sample_api_device():
    """Return sample API response for a device."""
    return {
        "deviceId": "dev123",
        "deviceName": "Pool Robot",
        "deviceType": "S2 Pro",
        "bleName": "CCBA97932A96",
        "poolId": "pool1",
        "autoUpdate": "1",
        "version": {"Firmware": "1.2.3"},
    }


@pytest.fixture
def sample_api_docker():
    """Return sample API response for a docker/dock."""
    return {
        "dockerId": "dock456",
        "dockerType": "DS20",
        "bleName": "3C8427565A1A",
        "deviceStatus": "online",
        "dockerStatus": "active",
        "schedule": None,
        "version": {"Firmware": "2.0.0"},
    }


@pytest.fixture
def sample_api_vision():
    """Return sample API response for vision data."""
    return {
        "visionId": "vis789",
        "privacy": False,
        "log": None,
        "video": None,
        "picture": None,
        "policy": True,
    }


@pytest.fixture
def sample_api_group(sample_api_device, sample_api_docker, sample_api_vision):
    """Return sample API response for a group."""
    return {
        "device": sample_api_device,
        "docker": sample_api_docker,
        "vision": sample_api_vision,
        "name": "My Pool",
        "id": "group1",
        "autoUpdate": "1",
    }


@pytest.fixture
def sample_command_data():
    """Return sample MQTT command data."""
    return {
        "cmd": 5,
        "ts": 1700000000,
        "dp": [
            {"id": 0, "type": 4, "len": 1, "data": "03"},
            {"id": 1, "type": 4, "len": 1, "data": "00"},
            {"id": 50, "type": 0, "len": 2, "data": "0132"},
        ],
    }

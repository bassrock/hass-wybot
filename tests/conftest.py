"""Fixtures and helpers for hass-wybot tests.

Uses sys.path manipulation to import model files directly without
triggering the HA-dependent __init__.py.
"""

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add the custom_components/wybot directory to sys.path so we can import
# the model modules DIRECTLY (not via the packages that need HA)
_WYBOT_DIR = Path(__file__).parent.parent / "custom_components" / "wybot"

# We need to prevent the __init__.py from being loaded when we import
# submodules. We do this by registering mock parent packages.
# HA core
sys.modules.setdefault("homeassistant", MagicMock())
sys.modules.setdefault("homeassistant.core", MagicMock())
sys.modules.setdefault("homeassistant.config_entries", MagicMock())
sys.modules.setdefault("homeassistant.const", MagicMock())
sys.modules.setdefault("homeassistant.helpers", MagicMock())
sys.modules.setdefault("homeassistant.helpers.update_coordinator", MagicMock())
sys.modules.setdefault("homeassistant.helpers.config_validation", MagicMock())
sys.modules.setdefault("homeassistant.helpers.device_registry", MagicMock())
sys.modules.setdefault("homeassistant.helpers.entity_platform", MagicMock())
sys.modules.setdefault("homeassistant.exceptions", MagicMock())
sys.modules.setdefault("homeassistant.components.bluetooth", MagicMock())
sys.modules.setdefault("homeassistant.components.vacuum", MagicMock())
sys.modules.setdefault("homeassistant.components.sensor", MagicMock())
sys.modules.setdefault("homeassistant.components.binary_sensor", MagicMock())
sys.modules.setdefault("homeassistant.components.button", MagicMock())
sys.modules.setdefault("homeassistant.util", MagicMock())
sys.modules.setdefault("homeassistant.util.dt", MagicMock())

# voluptuous
sys.modules.setdefault("voluptuous", MagicMock())

# BLE libraries
sys.modules.setdefault("bleak", MagicMock())
sys.modules.setdefault("bleak.backends", MagicMock())
sys.modules.setdefault("bleak.backends.device", MagicMock())
sys.modules.setdefault("bleak_retry_connector", MagicMock())

# MQTT library
sys.modules.setdefault("paho", MagicMock())
sys.modules.setdefault("paho.mqtt", MagicMock())
sys.modules.setdefault("paho.mqtt.client", MagicMock())

# HTTP library
sys.modules.setdefault("requests", MagicMock())
sys.modules.setdefault("requests.adapters", MagicMock())
sys.modules.setdefault("urllib3", MagicMock())
sys.modules.setdefault("urllib3.util", MagicMock())
sys.modules.setdefault("urllib3.util.retry", MagicMock())

# Now ensure the wybot package directory is importable
sys.path.insert(0, str(_WYBOT_DIR.parent.parent))


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

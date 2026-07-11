"""Tests for the WyBot diagnostics module."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wybot.const import (
    CONF_WIFI_PASSWORD,
    CONF_WIFI_SSID,
    DOMAIN,
)
from custom_components.wybot.diagnostics import (
    CONF_DISCOVERED_DEVICE_ADDRESS,
    _serialize_coordinator,
    _serialize_dp,
    async_get_config_entry_diagnostics,
)
from custom_components.wybot.wybot_coordinator import WyBotCoordinator
from wybot.dp_models import DP, CleaningStatus
from wybot.models import Group

DEVICE_ID = "dev123"
DOCKER_ID = "dock456"
GROUP_ID = "group1"
DEVICE_BLE = "CCBA97932A96"
DOCKER_BLE = "3C8427565A1A"

PASSWORD = "supersecret"
WIFI_PASSWORD = "wifisecret"
DISCOVERED_ADDRESS = "AA:BB:CC:DD:EE:FF"


def _group_data(with_docker: bool = True) -> dict:
    data = {
        "device": {
            "deviceId": DEVICE_ID,
            "deviceName": "Pool Robot",
            "deviceType": "S2 Pro",
            "bleName": DEVICE_BLE,
            "autoUpdate": "1",
            "version": {"Firmware": "1.2.3"},
        },
        "docker": {
            "dockerId": DOCKER_ID,
            "dockerType": "DS20",
            "bleName": DOCKER_BLE,
            "deviceStatus": "online",
            "dockerStatus": "active",
            "schedule": None,
            "version": {"Firmware": "2.0.0"},
        },
        "vision": {
            "visionId": "vis789",
            "privacy": False,
            "log": None,
            "video": None,
            "picture": None,
            "policy": True,
        },
        "name": "My Pool",
        "id": GROUP_ID,
        "autoUpdate": "1",
    }
    if not with_docker:
        data["docker"] = None
    return data


def make_group(with_docker: bool = True) -> Group:
    """Build a Group with a cleaning-status DP populated on the device."""
    group = Group(**_group_data(with_docker=with_docker))
    group.device.dps = {"0": CleaningStatus(DP(id=0, type=4, len=1, data="03"))}
    return group


def make_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> WyBotCoordinator:
    """Build a real coordinator with external clients mocked out."""
    coord = WyBotCoordinator(hass, MagicMock(), entry)
    coord.wybot_http_client = MagicMock()
    coord.wybot_ble_client = MagicMock()
    coord.wybot_mqtt_client = MagicMock()
    coord._connection_available = True
    return coord


def make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: PASSWORD,
            CONF_WIFI_SSID: "HomeNet",
            CONF_WIFI_PASSWORD: WIFI_PASSWORD,
            CONF_DISCOVERED_DEVICE_ADDRESS: DISCOVERED_ADDRESS,
        },
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# async_get_config_entry_diagnostics
# ---------------------------------------------------------------------------


async def test_diagnostics_redacts_secrets(hass: HomeAssistant) -> None:
    entry = make_entry(hass)
    coord = make_coordinator(hass, entry)
    coord.data = {GROUP_ID: make_group()}
    entry.runtime_data = coord

    result = await async_get_config_entry_diagnostics(hass, entry)

    entry_data = result["entry_data"]
    # Secrets are redacted, not present verbatim.
    assert entry_data[CONF_PASSWORD] != PASSWORD
    assert entry_data[CONF_WIFI_PASSWORD] != WIFI_PASSWORD
    assert entry_data[CONF_USERNAME] != "user@example.com"
    assert entry_data[CONF_DISCOVERED_DEVICE_ADDRESS] != DISCOVERED_ADDRESS

    # No secret leaks anywhere in the serialized output.
    dumped = str(result)
    assert PASSWORD not in dumped
    assert WIFI_PASSWORD not in dumped
    assert DISCOVERED_ADDRESS not in dumped
    # BLE names/MACs are redacted too.
    assert DEVICE_BLE not in dumped
    assert DOCKER_BLE not in dumped


async def test_diagnostics_includes_device_and_dp_info(hass: HomeAssistant) -> None:
    entry = make_entry(hass)
    coord = make_coordinator(hass, entry)
    coord.data = {GROUP_ID: make_group()}
    entry.runtime_data = coord

    result = await async_get_config_entry_diagnostics(hass, entry)

    coordinator = result["coordinator"]
    assert coordinator["available"] is True
    assert coordinator["device_count"] == 1

    group = coordinator["groups"][GROUP_ID]
    assert group["group_id"] == GROUP_ID
    assert group["name"] == "My Pool"

    device = group["device"]
    assert device["id"] == DEVICE_ID
    assert device["type"] == "S2 Pro"
    assert device["ble_name"] == "**REDACTED**"
    # The cleaning-status DP is serialized to a plain dict, no raw objects.
    assert device["dps"]["0"] == {"id": 0, "type": 4, "len": 1, "data": "03"}

    docker = group["docker"]
    assert docker["id"] == DOCKER_ID
    assert docker["type"] == "DS20"
    assert docker["dps"] == {}


async def test_diagnostics_group_without_docker(hass: HomeAssistant) -> None:
    entry = make_entry(hass)
    coord = make_coordinator(hass, entry)
    coord.data = {GROUP_ID: make_group(with_docker=False)}
    entry.runtime_data = coord

    result = await async_get_config_entry_diagnostics(hass, entry)

    group = result["coordinator"]["groups"][GROUP_ID]
    assert group["docker"] is None
    assert group["device"]["id"] == DEVICE_ID


async def test_diagnostics_missing_runtime_data(hass: HomeAssistant) -> None:
    """Diagnostics must not raise when the coordinator is absent."""
    entry = make_entry(hass)
    # No runtime_data attached.

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["coordinator"] is None
    # Entry data is still redacted.
    assert result["entry_data"][CONF_PASSWORD] != PASSWORD


async def test_diagnostics_empty_coordinator_data(hass: HomeAssistant) -> None:
    entry = make_entry(hass)
    coord = make_coordinator(hass, entry)
    coord._connection_available = False
    coord.data = {}
    entry.runtime_data = coord

    result = await async_get_config_entry_diagnostics(hass, entry)

    coordinator = result["coordinator"]
    assert coordinator["available"] is False
    assert coordinator["device_count"] == 0
    assert coordinator["groups"] == {}


# ---------------------------------------------------------------------------
# helper unit tests (robust serialization branches)
# ---------------------------------------------------------------------------


def test_serialize_dp_plain_object() -> None:
    dp = CleaningStatus(DP(id=0, type=4, len=1, data="03"))
    assert _serialize_dp(dp) == {"id": 0, "type": 4, "len": 1, "data": "03"}


def test_serialize_dp_without_dict_method() -> None:
    # An object with no dict() falls back to str() and never raises.
    assert _serialize_dp("raw") == "raw"


def test_serialize_dp_dict_raises() -> None:
    class Boom:
        def dict(self):  # noqa: D401 - test double
            raise ValueError("boom")

        def __str__(self) -> str:
            return "boom-str"

    assert _serialize_dp(Boom()) == "boom-str"


def test_serialize_coordinator_none() -> None:
    assert _serialize_coordinator(None) is None

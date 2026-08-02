"""Tests for the WyBot data update coordinator."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wybot.const import (
    BLE_MAX_CONSECUTIVE_FAILURES,
    CONF_WIFI_PASSWORD,
    CONF_WIFI_SSID,
    DOMAIN,
)
from custom_components.wybot.wybot_coordinator import (
    BLE_RECOVERY_SECONDS,
    WyBotCoordinator,
)
from wybot import WybotAuthError
from wybot.dp_models import DP, CleaningStatus
from wybot.models import Group
from wybot.mqtt_client import WyBotMQTTClient

DEVICE_ID = "dev123"
DOCKER_ID = "dock456"
GROUP_ID = "group1"


def _group_data(with_docker: bool = True) -> dict:
    data = {
        "device": {
            "deviceId": DEVICE_ID,
            "deviceName": "Pool Robot",
            "deviceType": "S2 Pro",
            "bleName": "CCBA97932A96",
            "autoUpdate": "1",
            "version": {"Firmware": "1.2.3"},
        },
        "docker": {
            "dockerId": DOCKER_ID,
            "dockerType": "DS20",
            "bleName": "3C8427565A1A",
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
    """Build a Group with a cleaning-status DP populated."""
    group = Group(**_group_data(with_docker=with_docker))
    group.device.dps = {"0": CleaningStatus(DP(id=0, type=4, len=1, data="03"))}
    return group


def make_entry(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="acct", data=data)
    entry.add_to_hass(hass)
    return entry


def make_coordinator(hass: HomeAssistant, **entry_data) -> WyBotCoordinator:
    """Build a real coordinator with all external clients mocked out."""
    entry = make_entry(hass, **entry_data)
    coord = WyBotCoordinator(hass, MagicMock(), entry)

    http = MagicMock()
    http.get_indexed_current_grouped_devices = AsyncMock(return_value={})
    http.register_presence = AsyncMock(return_value=None)
    http.get_devices_and_status = AsyncMock(return_value=None)
    http.authenticate = AsyncMock(return_value=True)
    http.login = AsyncMock(return_value=None)
    http.close = AsyncMock(return_value=None)
    coord.wybot_http_client = http

    ble = MagicMock()
    ble.query_status = AsyncMock(return_value=None)
    ble.send_command = AsyncMock(return_value=(True, None))
    ble.wake_device = AsyncMock(return_value=True)
    ble.wake_devices = AsyncMock(return_value={})
    ble.configure_wifi = AsyncMock(return_value=True)
    ble.scan_for_device = AsyncMock(return_value=None)
    coord.wybot_ble_client = ble

    mqtt = MagicMock()
    mqtt.is_connected = MagicMock(return_value=True)
    mqtt.connect = AsyncMock(return_value=None)
    mqtt.disconnect = AsyncMock(return_value=None)
    mqtt.subscribe_for_device = AsyncMock(return_value=None)
    mqtt.ensure_device_sends_statuses = AsyncMock(return_value=None)
    mqtt.send_query_command_for_device = AsyncMock(return_value=None)
    mqtt.send_write_command_for_device = AsyncMock(return_value=None)
    coord.wybot_mqtt_client = mqtt

    return coord


# ---------------------------------------------------------------------------
# construction / simple accessors
# ---------------------------------------------------------------------------


async def test_init_loads_wifi_credentials(hass: HomeAssistant) -> None:
    coord = make_coordinator(
        hass, **{CONF_WIFI_SSID: "net", CONF_WIFI_PASSWORD: "pw"}
    )
    assert coord._wifi_ssid == "net"
    assert coord._wifi_password == "pw"


async def test_set_wifi_credentials(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.set_wifi_credentials("newssid", "newpw")
    assert coord._wifi_ssid == "newssid"
    assert coord._wifi_password == "newpw"
    coord.set_wifi_credentials(None, None)
    assert coord._wifi_ssid is None


async def test_available_and_vacuums(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._connection_available = True
    coord.data = {}
    assert coord.available is False  # no data
    coord.data = {GROUP_ID: make_group()}
    assert coord.available is True
    assert coord.vacuums == [GROUP_ID]
    coord._connection_available = False
    assert coord.available is False


async def test_getters(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    now = datetime.now(timezone.utc)
    coord._last_ble_poll[DEVICE_ID] = now
    coord._last_mqtt_data[DEVICE_ID] = now
    coord._data_source[DEVICE_ID] = "ble"
    coord._ble_available[DEVICE_ID] = True

    assert coord.get_last_ble_communication(DEVICE_ID) == now
    assert coord.get_last_mqtt_communication(DEVICE_ID) == now
    assert coord.get_data_source(DEVICE_ID) == "ble"
    assert coord.is_ble_available(DEVICE_ID) is True
    assert coord.get_last_ble_communication("unknown") is None

    group = coord.data[GROUP_ID]
    assert coord.get_group(DEVICE_ID) is group
    assert coord.get_group(DOCKER_ID) is group
    assert coord.get_group("nope") is None
    assert coord.get_device_or_docker(DEVICE_ID) is group.device
    assert coord.get_device_or_docker(DOCKER_ID) is group.docker
    assert coord.get_device_or_docker("nope") is None


async def test_get_device_ble_info(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    # docker preferred
    assert coord._get_device_ble_info(group) == (group.docker.ble_name, DOCKER_ID)
    # device-only
    group_no_dock = make_group(with_docker=False)
    assert coord._get_device_ble_info(group_no_dock) == (
        group_no_dock.device.ble_name,
        DEVICE_ID,
    )
    # neither BLE name
    group_no_dock.device.ble_name = ""
    assert coord._get_device_ble_info(group_no_dock) == (None, None)


# ---------------------------------------------------------------------------
# _maybe_recover_ble
# ---------------------------------------------------------------------------


async def test_maybe_recover_ble(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._ble_command_failures[DEVICE_ID] = 3
    coord._ble_disabled_at[DEVICE_ID] = time.time() - BLE_RECOVERY_SECONDS - 1
    coord._maybe_recover_ble(DEVICE_ID)
    assert DEVICE_ID not in coord._ble_command_failures
    assert DEVICE_ID not in coord._ble_disabled_at


async def test_maybe_recover_ble_not_yet(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._ble_command_failures[DEVICE_ID] = 3
    coord._ble_disabled_at[DEVICE_ID] = time.time()
    coord._maybe_recover_ble(DEVICE_ID)
    assert coord._ble_disabled_at[DEVICE_ID]  # still disabled


# ---------------------------------------------------------------------------
# _update_device_dps_from_ble / _update_from_ble_dps
# ---------------------------------------------------------------------------


async def test_update_device_dps_from_ble_docker(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    dps = [{"id": 11, "type": 4, "len": 1, "data": "00"}]
    coord._update_device_dps_from_ble(group, DOCKER_ID, dps)
    assert "11" in group.docker.dps


async def test_update_device_dps_from_ble_device(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group(with_docker=False)
    dps = [{"id": 1, "type": 4, "len": 1, "data": "00"}]
    coord._update_device_dps_from_ble(group, DEVICE_ID, dps)
    assert "1" in group.device.dps


async def test_update_device_dps_from_ble_empty(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    before = dict(group.docker.dps)
    coord._update_device_dps_from_ble(group, DOCKER_ID, [])
    assert group.docker.dps == before


async def test_update_from_ble_dps_docker(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    coord.data = {GROUP_ID: group}
    coord._update_from_ble_dps(group, DOCKER_ID, [{"id": 11, "type": 4, "len": 1, "data": "00"}])
    assert "11" in group.docker.dps
    assert DOCKER_ID in coord._last_mqtt_data


async def test_update_from_ble_dps_device(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group(with_docker=False)
    coord.data = {GROUP_ID: group}
    coord._update_from_ble_dps(group, DEVICE_ID, [{"id": 1, "type": 4, "len": 1, "data": "01"}])
    assert "1" in group.device.dps


async def test_update_from_ble_dps_empty(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    coord.data = {GROUP_ID: group}
    # empty returns early, no crash
    coord._update_from_ble_dps(group, DOCKER_ID, [])


async def test_update_from_ble_dps_error(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    coord.data = {GROUP_ID: group}
    # invalid dp id triggers Command parse failure -> caught, warning logged
    coord._update_from_ble_dps(group, DOCKER_ID, [{"id": "bad"}])


# ---------------------------------------------------------------------------
# BLE polling
# ---------------------------------------------------------------------------


async def test_poll_all_devices_via_ble_success(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord.wybot_ble_client.query_status.return_value = [
        {"id": 11, "type": 4, "len": 1, "data": "00"}
    ]
    needing = await coord._poll_all_devices_via_ble()
    assert needing == []
    assert coord._ble_available[DOCKER_ID] is True
    assert coord._data_source[DOCKER_ID] == "ble"


async def test_poll_all_devices_via_ble_no_data(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord.wybot_ble_client.query_status.return_value = None
    needing = await coord._poll_all_devices_via_ble()
    assert needing == [DOCKER_ID]
    assert coord._ble_available[DOCKER_ID] is False


async def test_poll_all_devices_via_ble_exception(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord.wybot_ble_client.query_status.side_effect = RuntimeError("boom")
    needing = await coord._poll_all_devices_via_ble()
    assert needing == [DOCKER_ID]
    assert coord._ble_available[DOCKER_ID] is False


async def test_poll_all_devices_via_ble_no_ble_name(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group(with_docker=False)
    group.device.ble_name = ""
    coord.data = {GROUP_ID: group}
    needing = await coord._poll_all_devices_via_ble()
    assert needing == [DEVICE_ID]


# ---------------------------------------------------------------------------
# MQTT polling / connection
# ---------------------------------------------------------------------------


async def test_poll_devices_via_mqtt_empty(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    await coord._poll_devices_via_mqtt([])
    coord.wybot_mqtt_client.ensure_device_sends_statuses.assert_not_called()


async def test_poll_devices_via_mqtt_success(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._mqtt_connected = True
    coord.wybot_mqtt_client.is_connected.return_value = True
    await coord._poll_devices_via_mqtt([DEVICE_ID])
    coord.wybot_mqtt_client.ensure_device_sends_statuses.assert_called_with(DEVICE_ID)


async def test_poll_devices_via_mqtt_connect_fails(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.wybot_mqtt_client.connect.side_effect = RuntimeError("no broker")
    await coord._poll_devices_via_mqtt([DEVICE_ID])
    coord.wybot_mqtt_client.ensure_device_sends_statuses.assert_not_called()


async def test_ensure_mqtt_connected_already(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._mqtt_connected = True
    coord.wybot_mqtt_client.is_connected.return_value = True
    assert await coord._ensure_mqtt_connected() is True


async def test_ensure_mqtt_connected_drift(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._mqtt_connected = True
    coord.data = {GROUP_ID: make_group()}
    # paho says disconnected first, then connect succeeds
    coord.wybot_mqtt_client.is_connected.return_value = False
    assert await coord._ensure_mqtt_connected() is True
    coord.wybot_mqtt_client.connect.assert_called()
    # subscribed for device + docker
    assert coord.wybot_mqtt_client.subscribe_for_device.call_count >= 2


async def test_ensure_mqtt_connected_fails(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.wybot_mqtt_client.connect.side_effect = RuntimeError("fail")
    assert await coord._ensure_mqtt_connected() is False
    assert coord._mqtt_connected is False


# ---------------------------------------------------------------------------
# subscribe_mqtt / query_all_device_status / send_write_command
# ---------------------------------------------------------------------------


async def test_subscribe_mqtt(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    data = {GROUP_ID: make_group(), "g2": make_group(with_docker=False)}
    data["g2"].device.dps = {}
    await coord.subscribe_mqtt(data)
    # 2 devices + 1 docker
    assert coord.wybot_mqtt_client.subscribe_for_device.call_count == 3


async def test_query_all_device_status(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord.wybot_mqtt_client.is_connected.return_value = True
    await coord.query_all_device_status()
    assert coord.wybot_mqtt_client.ensure_device_sends_statuses.call_count == 2


async def test_query_all_device_status_disconnected(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord.wybot_mqtt_client.is_connected.return_value = False
    await coord.query_all_device_status()
    coord.wybot_mqtt_client.ensure_device_sends_statuses.assert_not_called()


async def test_send_write_command(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    dp = group.device.dps["0"]
    await coord.send_write_command(group, dp)
    # once for device, once for docker
    assert coord.wybot_mqtt_client.send_write_command_for_device.call_count == 2


# ---------------------------------------------------------------------------
# async_send_command
# ---------------------------------------------------------------------------


async def test_async_send_command_ble_success(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    coord.data = {GROUP_ID: group}
    dp = group.device.dps["0"]
    coord.wybot_ble_client.send_command.return_value = (
        True,
        [{"id": 0, "type": 4, "len": 1, "data": "01"}],
    )
    assert await coord.async_send_command(group, dp) is True
    assert coord._ble_command_failures[DEVICE_ID] == 0
    coord.wybot_mqtt_client.send_write_command_for_device.assert_not_called()


async def test_async_send_command_ble_device_name(hass: HomeAssistant) -> None:
    # No docker -> uses the device ble_name branch
    coord = make_coordinator(hass)
    group = make_group(with_docker=False)
    dp = group.device.dps["0"]
    coord.wybot_ble_client.send_command.return_value = (True, None)
    assert await coord.async_send_command(group, dp) is True
    coord.wybot_ble_client.send_command.assert_awaited_once()


async def test_async_send_command_ble_success_no_dps(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    dp = group.device.dps["0"]
    coord.wybot_ble_client.send_command.return_value = (True, None)
    assert await coord.async_send_command(group, dp) is True


async def test_async_send_command_ble_failure_falls_back(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    dp = group.device.dps["0"]
    coord.wybot_ble_client.send_command.return_value = (False, None)
    assert await coord.async_send_command(group, dp) is True
    assert coord._ble_command_failures[DEVICE_ID] == 1
    assert coord.wybot_mqtt_client.send_write_command_for_device.called


async def test_async_send_command_ble_disables_after_max(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    dp = group.device.dps["0"]
    coord.wybot_ble_client.send_command.return_value = (False, None)
    coord._ble_command_failures[DEVICE_ID] = BLE_MAX_CONSECUTIVE_FAILURES - 1
    assert await coord.async_send_command(group, dp) is True
    assert coord._ble_command_failures[DEVICE_ID] == BLE_MAX_CONSECUTIVE_FAILURES
    assert DEVICE_ID in coord._ble_disabled_at


async def test_async_send_command_ble_exception(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    dp = group.device.dps["0"]
    coord.wybot_ble_client.send_command.side_effect = RuntimeError("ble err")
    assert await coord.async_send_command(group, dp) is True
    assert coord._ble_command_failures[DEVICE_ID] == 1


async def test_async_send_command_ble_exception_disables(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    dp = group.device.dps["0"]
    coord.wybot_ble_client.send_command.side_effect = RuntimeError("ble err")
    coord._ble_command_failures[DEVICE_ID] = BLE_MAX_CONSECUTIVE_FAILURES - 1
    assert await coord.async_send_command(group, dp) is True
    assert DEVICE_ID in coord._ble_disabled_at


async def test_async_send_command_no_ble_name(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group(with_docker=False)
    group.device.ble_name = ""
    dp = group.device.dps["0"]
    assert await coord.async_send_command(group, dp) is True
    coord.wybot_ble_client.send_command.assert_not_called()
    assert coord.wybot_mqtt_client.send_write_command_for_device.called


async def test_async_send_command_ble_globally_disabled(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._ble_command_enabled = False
    group = make_group()
    dp = group.device.dps["0"]
    assert await coord.async_send_command(group, dp) is True
    coord.wybot_ble_client.send_command.assert_not_called()


async def test_async_send_command_ble_too_many_failures(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    dp = group.device.dps["0"]
    coord._ble_command_failures[DEVICE_ID] = BLE_MAX_CONSECUTIVE_FAILURES
    assert await coord.async_send_command(group, dp) is True
    coord.wybot_ble_client.send_command.assert_not_called()


async def test_reset_ble_command_failures(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._ble_command_failures = {DEVICE_ID: 2, "other": 1}
    coord.reset_ble_command_failures(DEVICE_ID)
    assert DEVICE_ID not in coord._ble_command_failures
    coord.reset_ble_command_failures()
    assert coord._ble_command_failures == {}


# ---------------------------------------------------------------------------
# wake methods
# ---------------------------------------------------------------------------


async def test_async_wake_devices_ble_disabled(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._ble_wake_enabled = False
    assert await coord.async_wake_devices_ble() == {}


async def test_async_wake_devices_ble_none_offline(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    group.device.online = True
    group.docker.online = True
    coord.data = {GROUP_ID: group}
    assert await coord.async_wake_devices_ble() == {}


async def test_async_wake_devices_ble_wakes(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    group.device.online = False
    group.docker.online = False
    coord.data = {GROUP_ID: group}
    coord.wybot_ble_client.wake_devices.return_value = {
        group.device.ble_name: True,
        group.docker.ble_name: False,
    }
    results = await coord.async_wake_devices_ble()
    assert results[group.device.ble_name] is True
    assert results[group.docker.ble_name] is False


async def test_async_wake_device_ble_disabled(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._ble_wake_enabled = False
    assert await coord.async_wake_device_ble("ble") is False


async def test_async_wake_device_ble(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.wybot_ble_client.wake_device.return_value = True
    assert await coord.async_wake_device_ble("ble") is True


# ---------------------------------------------------------------------------
# async_stop
# ---------------------------------------------------------------------------


async def test_async_stop_connected(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._mqtt_connected = True
    await coord.async_stop()
    coord.wybot_mqtt_client.disconnect.assert_called_once()
    assert coord._mqtt_connected is False


async def test_async_stop_not_connected(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._mqtt_connected = False
    await coord.async_stop()
    coord.wybot_mqtt_client.disconnect.assert_not_called()


# ---------------------------------------------------------------------------
# on_message
# ---------------------------------------------------------------------------


def _cmd(dp_id: int = 0, data: str = "03") -> dict:
    return {"cmd": 5, "ts": 0, "dp": [{"id": dp_id, "type": 4, "len": 1, "data": data}]}


def _msg(topic: str, payload):
    """Parse a raw topic and payload the way the MQTT receive loop does.

    Going through pywybot's own parser rather than hand-building an
    ``MQTTMessage`` keeps these tests honest about the library contract: when
    the callback signature changed in pywybot 1.2.0, tests that called
    ``on_message(topic, data)`` directly kept passing while the real path was
    broken.
    """
    return WyBotMQTTClient(lambda _message: None)._parse_message(topic, payload)


async def test_on_message_will_online(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord.on_message(_msg(f"/will/{DEVICE_ID}", {"online": "1"}))
    assert DEVICE_ID in coord._online_devices
    assert coord.data[GROUP_ID].device.online is True
    coord.wybot_mqtt_client.ensure_device_sends_statuses.assert_called_with(DEVICE_ID)
    await hass.async_block_till_done()


async def test_on_message_will_offline_docker(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    coord.data = {GROUP_ID: group}
    coord._online_devices.add(DOCKER_ID)
    coord.on_message(_msg(f"/will/{DOCKER_ID}", {"online": "0"}))
    assert DOCKER_ID not in coord._online_devices
    assert coord.data[GROUP_ID].docker.online is False
    await hass.async_block_till_done()


async def test_on_message_will_unknown_device(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    # unknown device id -> group is None, but data_updated still True
    coord.on_message(_msg("/will/unknown", {"online": "1"}))
    await hass.async_block_till_done()


async def test_on_message_unrelated_topic_ignored(hass: HomeAssistant) -> None:
    """A topic pywybot cannot attribute to a device carries no device id."""
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord.on_message(_msg("/ota/progress", {"pct": 10}))
    # Nothing to attribute it to, so no device is marked online.
    assert coord._online_devices == set()
    await hass.async_block_till_done()


async def test_on_message_will_without_online_flag(hass: HomeAssistant) -> None:
    """A non-JSON will payload leaves online unset; treat it as no information."""
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord.on_message(_msg(f"/will/{DEVICE_ID}", b"not-json"))
    assert DEVICE_ID not in coord._online_devices
    await hass.async_block_till_done()


async def test_on_message_dps_for_unknown_device(hass: HomeAssistant) -> None:
    """DPs for a device absent from coordinator data are dropped, not crashed on."""
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord.on_message(_msg("/device/DATA/send_transparent_data/nope", _cmd(11, "00")))
    await hass.async_block_till_done()


async def test_on_message_send_transparent_data_docker(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    coord.data = {GROUP_ID: group}
    coord.on_message(_msg(f"/device/DATA/send_transparent_data/{DOCKER_ID}", _cmd(11, "00")))
    assert "11" in coord.data[GROUP_ID].docker.dps
    await hass.async_block_till_done()


async def test_on_message_send_transparent_data_device(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group(with_docker=False)
    coord.data = {GROUP_ID: group}
    coord.on_message(_msg(f"/device/DATA/send_transparent_data/{DEVICE_ID}", _cmd(1, "01")))
    assert "1" in coord.data[GROUP_ID].device.dps
    await hass.async_block_till_done()


async def test_on_message_send_transparent_data_bad(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    # invalid Command payload -> parse fails -> returns early
    coord.on_message(_msg(f"/device/DATA/send_transparent_data/{DOCKER_ID}", {"garbage": True}))
    await hass.async_block_till_done()


async def test_on_message_recv_query_data(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord.on_message(_msg(f"/device/DATA/recv_transparent_query_data/{DOCKER_ID}", _cmd()))
    await hass.async_block_till_done()


async def test_on_message_recv_cmd_data_docker(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group()
    coord.data = {GROUP_ID: group}
    coord.on_message(_msg(f"/device/DATA/recv_transparent_cmd_data/{DOCKER_ID}", _cmd(11, "01")))
    assert "11" in coord.data[GROUP_ID].docker.dps
    await hass.async_block_till_done()


async def test_on_message_recv_cmd_data_device(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    group = make_group(with_docker=False)
    coord.data = {GROUP_ID: group}
    coord.on_message(_msg(f"/device/DATA/recv_transparent_cmd_data/{DEVICE_ID}", _cmd(1, "01")))
    assert "1" in coord.data[GROUP_ID].device.dps
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# http_refresh_data
# ---------------------------------------------------------------------------


async def test_http_refresh_data_success(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    data = {GROUP_ID: make_group()}
    coord.wybot_http_client.get_indexed_current_grouped_devices.return_value = data
    await coord.http_refresh_data()
    assert coord.data is data
    assert coord._http_failure_count == 0


async def test_http_refresh_data_empty_retries(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.wybot_http_client.get_indexed_current_grouped_devices.return_value = {}
    with patch(
        "custom_components.wybot.wybot_coordinator.asyncio.sleep", AsyncMock()
    ):
        with pytest.raises(UpdateFailed):
            await coord.http_refresh_data()


async def test_http_refresh_data_auth_error(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.wybot_http_client.get_indexed_current_grouped_devices.side_effect = (
        WybotAuthError("bad")
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord.http_refresh_data()


async def test_http_refresh_data_connection_failures(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.wybot_http_client.get_indexed_current_grouped_devices.side_effect = (
        RuntimeError("net down")
    )
    with patch(
        "custom_components.wybot.wybot_coordinator.asyncio.sleep", AsyncMock()
    ):
        with pytest.raises(ConfigEntryNotReady):
            await coord.http_refresh_data()
    assert coord._http_failure_count >= 3


# ---------------------------------------------------------------------------
# _async_update_data
# ---------------------------------------------------------------------------


async def test_async_update_data_initial_load(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    data = {GROUP_ID: make_group()}
    coord.wybot_http_client.get_indexed_current_grouped_devices.return_value = data
    coord.wybot_ble_client.query_status.return_value = [
        {"id": 11, "type": 4, "len": 1, "data": "00"}
    ]
    result = await coord._async_update_data()
    assert result is data
    assert coord.initial_load is True
    assert coord._connection_available is True


async def test_async_update_data_subsequent(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.initial_load = True
    coord.data = {GROUP_ID: make_group()}
    coord.wybot_ble_client.query_status.return_value = [
        {"id": 11, "type": 4, "len": 1, "data": "00"}
    ]
    result = await coord._async_update_data()
    assert result == coord.data


async def test_async_update_data_no_data(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.initial_load = True
    coord.data = {}
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
    assert coord._connection_available is False


async def test_async_update_data_auth_error(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.wybot_http_client.register_presence.side_effect = WybotAuthError("bad")
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_async_update_data_config_entry_auth_failed_passthrough(
    hass: HomeAssistant,
) -> None:
    coord = make_coordinator(hass)
    coord.wybot_http_client.get_indexed_current_grouped_devices.side_effect = (
        WybotAuthError("bad")
    )
    # http_refresh_data converts to ConfigEntryAuthFailed, which passes through
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_async_update_data_config_entry_not_ready_passthrough(
    hass: HomeAssistant,
) -> None:
    coord = make_coordinator(hass)
    coord.wybot_http_client.get_indexed_current_grouped_devices.side_effect = (
        RuntimeError("net")
    )
    with patch(
        "custom_components.wybot.wybot_coordinator.asyncio.sleep", AsyncMock()
    ):
        with pytest.raises(ConfigEntryNotReady):
            await coord._async_update_data()


async def test_async_update_data_timeout(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.initial_load = True
    coord.data = {GROUP_ID: make_group()}
    with patch.object(
        coord, "_poll_all_devices_via_ble", side_effect=TimeoutError("slow")
    ):
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()


async def test_async_update_data_generic_error(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.initial_load = True
    coord.data = {GROUP_ID: make_group()}
    with patch.object(
        coord, "_poll_all_devices_via_ble", side_effect=ValueError("oops")
    ):
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()


async def test_async_update_data_mqtt_fallback(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.initial_load = True
    coord.data = {GROUP_ID: make_group()}
    # BLE returns nothing -> device needs MQTT
    coord.wybot_ble_client.query_status.return_value = None
    coord._mqtt_connected = True
    coord.wybot_mqtt_client.is_connected.return_value = True
    result = await coord._async_update_data()
    assert result == coord.data
    coord.wybot_mqtt_client.ensure_device_sends_statuses.assert_called()


# ---------------------------------------------------------------------------
# _maybe_refresh_http_session
# ---------------------------------------------------------------------------


async def test_maybe_refresh_http_session_throttled(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord._last_http_refresh_time = time.time()
    await coord._maybe_refresh_http_session()
    coord.wybot_http_client.register_presence.assert_not_called()


async def test_maybe_refresh_http_session_runs(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    coord._mqtt_connected = True
    coord.wybot_mqtt_client.is_connected.return_value = True
    await coord._maybe_refresh_http_session()
    coord.wybot_http_client.register_presence.assert_called()
    coord.wybot_http_client.get_devices_and_status.assert_called()


async def test_maybe_refresh_http_session_auth_error(hass: HomeAssistant) -> None:
    coord = make_coordinator(hass)
    coord.wybot_http_client.register_presence.side_effect = WybotAuthError("bad")
    with pytest.raises(WybotAuthError):
        await coord._maybe_refresh_http_session()


async def test_maybe_refresh_http_session_non_critical_error(
    hass: HomeAssistant,
) -> None:
    coord = make_coordinator(hass)
    coord.wybot_http_client.register_presence.side_effect = RuntimeError("meh")
    # swallowed, no raise
    await coord._maybe_refresh_http_session()


async def test_maybe_refresh_http_session_mqtt_keepalive_error(
    hass: HomeAssistant,
) -> None:
    coord = make_coordinator(hass)
    coord.data = {GROUP_ID: make_group()}
    # _ensure_mqtt_connected raising is swallowed by the keepalive try/except
    with patch.object(
        coord, "_ensure_mqtt_connected", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        await coord._maybe_refresh_http_session()

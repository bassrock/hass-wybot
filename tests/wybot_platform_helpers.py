"""Shared helpers for WyBot entity-platform tests.

Builds a real ``WyBotCoordinator`` (its constructor is side-effect free: it
creates MQTT/BLE client objects but does not connect) populated with real
``Group`` models so the entity platforms can be exercised end-to-end.
"""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.wybot.const import (
    CONF_WIFI_PASSWORD,
    CONF_WIFI_SSID,
    DOMAIN,
)
from custom_components.wybot.wybot_coordinator import WyBotCoordinator
from wybot.dp_models import DP
from wybot.models import Group

GROUP_DATA = {
    "device": {
        "deviceId": "dev1",
        "deviceName": "Robot",
        "deviceType": "S2 Pro",
        "bleName": "CCBA97932A96",
        "autoUpdate": "1",
        "version": {"Firmware": "1.0"},
    },
    "docker": {
        "dockerId": "dock1",
        "dockerType": "DS20",
        "bleName": "3C8427565A1A",
        "deviceStatus": "online",
        "dockerStatus": "active",
        "schedule": None,
        "version": {"Firmware": "2.0"},
    },
    "vision": {
        "visionId": "v1",
        "privacy": False,
        "log": None,
        "video": None,
        "picture": None,
        "policy": True,
    },
    "name": "My Pool",
    "id": "grp1",
    "autoUpdate": "1",
}


def dp(cls, **kwargs):
    """Wrap a raw DP into the given typed DP class."""
    return cls(DP(**kwargs))


def make_group(device_dps=None, docker_dps=None, with_docker=True, name="My Pool"):
    """Build a real ``Group`` with typed DP instances attached."""
    data = deepcopy(GROUP_DATA)
    data["name"] = name
    if not with_docker:
        data["docker"] = None
    group = Group(**data)
    group.device.dps = device_dps or {}
    if group.docker:
        group.docker.dps = docker_dps or {}
    return group


def make_coordinator(hass, data, wifi_ssid=None, wifi_password=None):
    """Build a real coordinator populated with the given ``data`` mapping."""
    entry_data = {CONF_USERNAME: "u", CONF_PASSWORD: "p"}
    if wifi_ssid is not None:
        entry_data[CONF_WIFI_SSID] = wifi_ssid
    if wifi_password is not None:
        entry_data[CONF_WIFI_PASSWORD] = wifi_password
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data)
    entry.add_to_hass(hass)
    coord = WyBotCoordinator(hass, MagicMock(), entry)
    coord.data = data
    coord._connection_available = True
    # Avoid scheduling a real refresh timer when entities register as listeners
    # (keeps the test harness free of lingering timers).
    coord.update_interval = None
    return coord, entry


async def add_entity(hass, entity, domain="sensor"):
    """Add an entity to a real (mock) entity platform.

    This gives the entity a platform, hass, and entity_id so
    ``async_write_ha_state`` works exactly as it would in production.
    """
    platform = MockEntityPlatform(hass, domain=domain, platform_name=DOMAIN)
    await platform.async_add_entities([entity])
    return entity

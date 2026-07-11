"""Tests for the WyBot button platform."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.wybot.button import (
    WyBotWifiReconfigureButton,
    async_setup_entry,
)

from wybot_platform_helpers import make_coordinator, make_group

IDX = "grp1"


def _button(hass, **coord_kwargs):
    group = make_group()
    coord, entry = make_coordinator(hass, {IDX: group}, **coord_kwargs)
    ent = WyBotWifiReconfigureButton(coord, IDX, "3C8427565A1A", "ssid", "pass")
    return coord, entry, ent, group


async def test_setup_creates_button_when_creds_present(hass: HomeAssistant) -> None:
    group = make_group()
    coord, entry = make_coordinator(
        hass, {IDX: group}, wifi_ssid="ssid", wifi_password="pass"
    )
    entry.runtime_data = coord
    added: list = []
    await async_setup_entry(hass, entry, lambda e: added.extend(e))
    assert len(added) == 1
    assert isinstance(added[0], WyBotWifiReconfigureButton)


async def test_setup_no_button_without_wifi_creds(hass: HomeAssistant) -> None:
    group = make_group()
    coord, entry = make_coordinator(hass, {IDX: group})  # no wifi creds
    entry.runtime_data = coord
    added: list = []
    await async_setup_entry(hass, entry, lambda e: added.extend(e))
    assert added == []


async def test_setup_no_button_without_docker(hass: HomeAssistant) -> None:
    group = make_group(with_docker=False)
    coord, entry = make_coordinator(
        hass, {IDX: group}, wifi_ssid="ssid", wifi_password="pass"
    )
    entry.runtime_data = coord
    added: list = []
    await async_setup_entry(hass, entry, lambda e: added.extend(e))
    assert added == []


async def test_setup_no_button_without_dock_ble_name(hass: HomeAssistant) -> None:
    group = make_group()
    group.docker.ble_name = ""
    coord, entry = make_coordinator(
        hass, {IDX: group}, wifi_ssid="ssid", wifi_password="pass"
    )
    entry.runtime_data = coord
    added: list = []
    await async_setup_entry(hass, entry, lambda e: added.extend(e))
    assert added == []


async def test_setup_no_button_when_group_missing(hass: HomeAssistant) -> None:
    # vacuums lists idx but data.get returns None (group absent).
    coord, entry = make_coordinator(
        hass, {}, wifi_ssid="ssid", wifi_password="pass"
    )
    # Force vacuums to include an idx that isn't in data.
    coord.data = {}
    entry.runtime_data = coord
    added: list = []
    await async_setup_entry(hass, entry, lambda e: added.extend(e))
    assert added == []


async def test_button_properties(hass: HomeAssistant) -> None:
    coord, _, ent, _ = _button(hass)
    assert ent.unique_id == "grp1_dock_wifi_reconfigure_button"
    assert ent.icon == "mdi:wifi-cog"
    assert ent.device_info["identifiers"] == {("wybot", "grp1_dock")}
    assert ent.available is True


async def test_button_unavailable_coordinator(hass: HomeAssistant) -> None:
    coord, _, ent, _ = _button(hass)
    coord._connection_available = False
    assert ent.available is False


async def test_button_unavailable_no_group(hass: HomeAssistant) -> None:
    coord, _, ent, _ = _button(hass)
    coord.data = {}
    assert ent.available is False


async def test_button_unavailable_no_docker(hass: HomeAssistant) -> None:
    group = make_group(with_docker=False)
    coord, entry = make_coordinator(hass, {IDX: group})
    ent = WyBotWifiReconfigureButton(coord, IDX, "x", "ssid", "pass")
    assert ent.available is False


async def test_async_press_success(hass: HomeAssistant) -> None:
    coord, _, ent, _ = _button(hass)
    coord.wybot_ble_client.configure_wifi = AsyncMock(return_value=True)
    await ent.async_press()
    coord.wybot_ble_client.configure_wifi.assert_awaited_once_with(
        "3C8427565A1A", "ssid", "pass"
    )


async def test_async_press_failure_raises(hass: HomeAssistant) -> None:
    coord, _, ent, _ = _button(hass)
    coord.wybot_ble_client.configure_wifi = AsyncMock(return_value=False)
    with pytest.raises(HomeAssistantError):
        await ent.async_press()

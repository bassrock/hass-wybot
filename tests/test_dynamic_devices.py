"""Tests for dynamic device addition (devices added after setup)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.wybot.binary_sensor import async_setup_entry as bs_setup
from custom_components.wybot.button import async_setup_entry as btn_setup
from custom_components.wybot.sensor import async_setup_entry as sensor_setup
from custom_components.wybot.vacuum import async_setup_entry as vacuum_setup
from wybot_platform_helpers import make_coordinator, make_group


def _capture():
    """Return (list, callback) that collects entities passed to async_add_entities."""
    added: list = []

    def cb(new_entities, update_before_add=False):
        added.extend(list(new_entities))

    return added, cb


async def _assert_dynamic(hass, setup, wifi=False):
    kwargs = {"wifi_ssid": "net", "wifi_password": "pw"} if wifi else {}
    coord, entry = make_coordinator(hass, {"g1": make_group()}, **kwargs)
    entry.runtime_data = coord
    added, cb = _capture()

    await setup(hass, entry, cb)
    first = len(added)
    assert first > 0

    # Re-firing with the same devices hits the "already known" branch — no dupes.
    coord.async_update_listeners()
    assert len(added) == first

    # A new device appearing creates entities dynamically.
    coord.data = {"g1": make_group(), "g2": make_group(name="Pool 2")}
    coord.async_update_listeners()
    assert len(added) > first


async def test_sensor_dynamic_devices(hass: HomeAssistant) -> None:
    await _assert_dynamic(hass, sensor_setup)


async def test_binary_sensor_dynamic_devices(hass: HomeAssistant) -> None:
    await _assert_dynamic(hass, bs_setup)


async def test_vacuum_dynamic_devices(hass: HomeAssistant) -> None:
    await _assert_dynamic(hass, vacuum_setup)


async def test_button_dynamic_devices(hass: HomeAssistant) -> None:
    await _assert_dynamic(hass, btn_setup, wifi=True)

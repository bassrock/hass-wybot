"""Device-registry behaviour of a full setup: dock linking and dockless units."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wybot.const import DOMAIN
from wybot.models import Group
from wybot_platform_helpers import make_group

IDX = "grp1"
# Prefix of HA's report_usage message for any deprecated call this integration makes.
DEPRECATION_REPORT = "Detected that custom integration 'wybot'"
# Entities that only make sense with a physical dock.
DOCK_ONLY_UNIQUE_IDS = {
    f"wybot_{IDX}_dock_battery",
    f"wybot_{IDX}_dock_type",
    f"wybot_{IDX}_dock_charging",
}


def _dockless_group() -> Group:
    """A C1/F1-style group: a placeholder docker entry with an empty id."""
    group = make_group()
    assert group.docker is not None
    group.docker.docker_id = ""
    group.docker.docker_type = ""
    group.docker.ble_name = ""
    return group


async def _setup(hass: HomeAssistant, groups: dict[str, Group]) -> MockConfigEntry:
    """Set up the integration for real, with the WyBot cloud mocked out."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account-123",
        data={CONF_USERNAME: "pool@example.com", CONF_PASSWORD: "hunter2"},
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.authenticate = AsyncMock(return_value=True)
    with (
        patch("custom_components.wybot.WyBotHTTPClient", return_value=client),
        patch(
            "custom_components.wybot.wybot_coordinator.WyBotCoordinator._async_update_data",
            AsyncMock(return_value=groups),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


def _device(
    hass: HomeAssistant, entry: MockConfigEntry, identifier: str
) -> dr.DeviceEntry | None:
    """Return this entry's device with ``identifier``, if any."""
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (DOMAIN, identifier) in device.identifiers:
            return device
    return None


async def _unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_robot_is_linked_to_its_dock_without_deprecated_via_device(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The robot hangs off the dock, without HA's via_device warning (#46)."""
    entry = await _setup(hass, {IDX: make_group()})

    robot = _device(hass, entry, IDX)
    dock = _device(hass, entry, f"{IDX}_dock")
    assert robot is not None and dock is not None
    assert robot.via_device_id == dock.id
    assert DEPRECATION_REPORT not in caplog.text

    await _unload(hass, entry)


async def test_dockless_unit_has_no_dock_device_or_dock_entities(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A C1/F1 gets no phantom solar dock (#39)."""
    entry = await _setup(hass, {IDX: _dockless_group()})

    registry = dr.async_get(hass)
    robot = _device(hass, entry, IDX)
    assert robot is not None
    assert robot.via_device_id is None
    assert dr.async_entries_for_config_entry(registry, entry.entry_id) == [robot]

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    unique_ids = {e.unique_id for e in entities}
    assert not unique_ids & DOCK_ONLY_UNIQUE_IDS
    # Generic diagnostics still exist, now on the robot itself.
    assert f"wybot_{IDX}_data_source" in unique_ids
    assert all(e.device_id == robot.id for e in entities)
    assert DEPRECATION_REPORT not in caplog.text

    await _unload(hass, entry)


async def test_existing_phantom_dock_is_removed_on_upgrade(
    hass: HomeAssistant,
) -> None:
    """A dock device created by an earlier version for a dockless unit goes away."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account-123",
        data={CONF_USERNAME: "pool@example.com", CONF_PASSWORD: "hunter2"},
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    phantom = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{IDX}_dock")},
        name="Solar Dock",
    )
    robot = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, IDX)}
    )
    registry.async_update_device(robot.id, via_device_id=phantom.id)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"wybot_{IDX}_dock_battery",
        config_entry=entry,
        device_id=phantom.id,
    )
    moved = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"wybot_{IDX}_data_source",
        config_entry=entry,
        device_id=phantom.id,
    )
    # MockConfigEntry.add_to_hass leaves the entry unloaded; set up for real.
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)

    client = MagicMock()
    client.authenticate = AsyncMock(return_value=True)
    with (
        patch("custom_components.wybot.WyBotHTTPClient", return_value=client),
        patch(
            "custom_components.wybot.wybot_coordinator.WyBotCoordinator._async_update_data",
            AsyncMock(return_value={IDX: _dockless_group()}),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert registry.async_get(phantom.id) is None
    robot_after = registry.async_get(robot.id)
    assert robot_after is not None and robot_after.via_device_id is None
    # The dock-only entity went with the phantom device; the generic one moved.
    assert (
        ent_reg.async_get_entity_id("sensor", DOMAIN, f"wybot_{IDX}_dock_battery")
        is None
    )
    moved_after = ent_reg.async_get(moved.entity_id)
    assert moved_after is not None and moved_after.device_id == robot.id

    await _unload(hass, entry)

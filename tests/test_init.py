"""Tests for WyBot integration setup and teardown."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wybot.const import DOMAIN
from wybot import (
    WybotAuthError,
    WybotConnectionError,
)

USER = "pool@example.com"
PASSWORD = "hunter2"
USER_ID = "account-123"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_ID,
        data={CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD},
    )


def _patch_http(authenticate=None):
    client = MagicMock()
    client.user_id = USER_ID
    if authenticate is not None:
        client.authenticate.side_effect = authenticate
    else:
        client.authenticate.return_value = True
    return patch("custom_components.wybot.WyBotHTTPClient", return_value=client)


def _fake_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = {}
    coordinator.vacuums = []
    coordinator.available = True
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_stop = AsyncMock()
    return coordinator


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    """A valid entry sets up, stores runtime_data, and unloads cleanly."""
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = _fake_coordinator()

    with (
        _patch_http(),
        patch("custom_components.wybot.WyBotCoordinator", return_value=coordinator),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is coordinator
    coordinator.async_config_entry_first_refresh.assert_awaited_once()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    coordinator.async_stop.assert_awaited()


async def test_setup_auth_failure_triggers_reauth(hass: HomeAssistant) -> None:
    """A rejected login raises ConfigEntryAuthFailed and starts reauth."""
    entry = _entry()
    entry.add_to_hass(hass)

    with _patch_http(authenticate=WybotAuthError):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_setup_connection_error_is_retried(hass: HomeAssistant) -> None:
    """A connection error raises ConfigEntryNotReady (setup retry)."""
    entry = _entry()
    entry.add_to_hass(hass)

    with _patch_http(authenticate=WybotConnectionError):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_update_listener_refreshes_wifi_credentials(hass: HomeAssistant) -> None:
    """Updating options pushes new WiFi credentials into the coordinator."""
    from custom_components.wybot import _async_update_listener
    from custom_components.wybot.const import CONF_WIFI_PASSWORD, CONF_WIFI_SSID

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_ID,
        data={
            CONF_USERNAME: USER,
            CONF_PASSWORD: PASSWORD,
            CONF_WIFI_SSID: "net",
            CONF_WIFI_PASSWORD: "pw",
        },
    )
    entry.add_to_hass(hass)
    coordinator = _fake_coordinator()
    entry.runtime_data = coordinator

    await _async_update_listener(hass, entry)

    coordinator.set_wifi_credentials.assert_called_once_with("net", "pw")

"""Tests for the WyBot config flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wybot.const import CONF_WIFI_PASSWORD, CONF_WIFI_SSID, DOMAIN
from wybot import (
    WybotAuthError,
    WybotConnectionError,
)

USER = "pool@example.com"
PASSWORD = "hunter2"
USER_ID = "account-123"


def _client(user_id: str = USER_ID, authenticate=None) -> MagicMock:
    """Build a mock WyBotHTTPClient."""
    client = MagicMock()
    client.user_id = user_id
    if authenticate is not None:
        client.authenticate.side_effect = authenticate
    else:
        client.authenticate.return_value = True
    return client


def _patch_client(client: MagicMock):
    return patch(
        "custom_components.wybot.config_flow.WyBotHTTPClient", return_value=client
    )


def _patch_setup():
    return patch("custom_components.wybot.async_setup_entry", return_value=True)


def _entry(**kwargs) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=kwargs.pop("unique_id", USER_ID),
        data={CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD, **kwargs},
    )


async def test_user_flow_success(hass: HomeAssistant) -> None:
    """A valid account creates an entry keyed on the account id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with _patch_client(_client()), _patch_setup() as mock_setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USER
    assert result["data"][CONF_USERNAME] == USER
    assert result["result"].unique_id == USER_ID
    assert len(mock_setup.mock_calls) >= 1


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (WybotAuthError, "invalid_auth"),
        (WybotConnectionError, "cannot_connect"),
        (Exception, "unknown"),
    ],
)
async def test_user_flow_errors_and_recovery(
    hass: HomeAssistant, side_effect, expected
) -> None:
    """Typed client errors map to form errors, then a retry succeeds."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with _patch_client(_client(authenticate=side_effect)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    with _patch_client(_client()), _patch_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_wifi_password_required(hass: HomeAssistant) -> None:
    """A WiFi SSID without a password is rejected before any network call."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD, CONF_WIFI_SSID: "MyWifi"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wifi_password_required"}


async def test_user_flow_duplicate_account_aborts(hass: HomeAssistant) -> None:
    """The same account cannot be configured twice."""
    _entry().add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with _patch_client(_client()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_success(hass: HomeAssistant) -> None:
    """Reauth updates the stored password and reloads the entry."""
    entry = _entry(**{CONF_PASSWORD: "old"})
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with _patch_client(_client(user_id=USER_ID)), _patch_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"


async def test_reauth_wrong_account(hass: HomeAssistant) -> None:
    """Reauthenticating with a different account is rejected."""
    entry = _entry(**{CONF_PASSWORD: "old"})
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    with _patch_client(_client(user_id="different-account")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_reauth_invalid_auth(hass: HomeAssistant) -> None:
    """A bad password during reauth shows an error and stays on the form."""
    entry = _entry(**{CONF_PASSWORD: "old"})
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    with _patch_client(_client(authenticate=WybotAuthError)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "bad"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (WybotConnectionError, "cannot_connect"),
        (Exception, "unknown"),
    ],
)
async def test_reauth_errors(hass: HomeAssistant, side_effect, expected) -> None:
    """Connection and unexpected errors during reauth show a form error."""
    entry = _entry(**{CONF_PASSWORD: "old"})
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    with _patch_client(_client(authenticate=side_effect)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "bad"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_options_flow_clears_wifi(hass: HomeAssistant) -> None:
    """Submitting an empty SSID removes stored WiFi credentials."""
    entry = _entry(**{CONF_WIFI_SSID: "old", CONF_WIFI_PASSWORD: "oldpw"})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WIFI_SSID: "", CONF_WIFI_PASSWORD: ""}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_WIFI_SSID not in entry.data
    assert CONF_WIFI_PASSWORD not in entry.data


async def test_options_flow_sets_wifi(hass: HomeAssistant) -> None:
    """The options flow stores WiFi credentials on the entry."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_WIFI_SSID: "MyWifi", CONF_WIFI_PASSWORD: "wifipass"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_WIFI_SSID] == "MyWifi"
    assert entry.data[CONF_WIFI_PASSWORD] == "wifipass"


async def test_options_flow_wifi_password_required(hass: HomeAssistant) -> None:
    """The options flow enforces the SSID/password pairing rule."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WIFI_SSID: "MyWifi"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wifi_password_required"}


def _service_info(name: str = "DS20-1234", address: str = "AA:BB:CC:DD:EE:FF"):
    """Build a BluetoothServiceInfoBleak for a discovered device."""
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

    device = BLEDevice(address=address, name=name, details={})
    advertisement = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        tx_power=-127,
        rssi=-60,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="local",
        device=device,
        advertisement=advertisement,
        connectable=True,
        time=0.0,
        tx_power=-127,
    )


async def test_bluetooth_discovery_flow(hass: HomeAssistant) -> None:
    """A DS20 dock discovered via Bluetooth advances to the user step."""
    info = _service_info(name="DS20-1234", address="AA:BB:CC:DD:EE:FF")

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=info,
    )
    # Bluetooth discovery advances straight to the user step to collect creds
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with _patch_client(_client()), _patch_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    # The MAC-derived unique id is set, and the discovered device is recorded.
    entry = result["result"]
    assert entry.unique_id == USER_ID
    assert result["data"]["discovered_device_address"] == "AA:BB:CC:DD:EE:FF"
    assert result["data"]["discovered_device_name"] == "DS20-1234"


async def test_bluetooth_discovery_robot_name(hass: HomeAssistant) -> None:
    """A non-DS20 advertisement is labelled as a Robot but still proceeds."""
    info = _service_info(name="CCBA97932A96", address="CC:BA:97:93:2A:96")

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=info,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def _start_reconfigure(hass: HomeAssistant, entry: MockConfigEntry):
    """Start a reconfigure flow, preferring the helper when available."""
    if hasattr(entry, "start_reconfigure_flow"):
        return await entry.start_reconfigure_flow(hass)
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )


async def test_reconfigure_success(hass: HomeAssistant) -> None:
    """Reconfigure updates the stored username/password and reloads the entry."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await _start_reconfigure(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    new_user = "new@example.com"
    with _patch_client(_client(user_id=USER_ID)), _patch_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: new_user, CONF_PASSWORD: "newpass"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_USERNAME] == new_user
    assert entry.data[CONF_PASSWORD] == "newpass"


async def test_reconfigure_wrong_account(hass: HomeAssistant) -> None:
    """Reconfiguring to a different account is rejected."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await _start_reconfigure(hass, entry)
    with _patch_client(_client(user_id="different-account")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "other@example.com", CONF_PASSWORD: "newpass"},
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_reconfigure_invalid_auth(hass: HomeAssistant) -> None:
    """Bad credentials during reconfigure show an error and stay on the form."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await _start_reconfigure(hass, entry)
    with _patch_client(_client(authenticate=WybotAuthError)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USER, CONF_PASSWORD: "bad"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (WybotConnectionError, "cannot_connect"),
        (Exception, "unknown"),
    ],
)
async def test_reconfigure_errors(
    hass: HomeAssistant, side_effect, expected
) -> None:
    """Connection and unexpected errors during reconfigure show a form error."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await _start_reconfigure(hass, entry)
    with _patch_client(_client(authenticate=side_effect)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: USER, CONF_PASSWORD: "bad"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_bluetooth_discovery_updates_configured_entry(
    hass: HomeAssistant,
) -> None:
    """A discovery for an already-configured dock updates its stored address."""
    address = "AA:BB:CC:DD:EE:FF"
    from homeassistant.helpers.device_registry import format_mac

    entry = _entry(
        unique_id=format_mac(address),
        **{
            "discovered_device_address": "00:00:00:00:00:00",
            "discovered_device_name": "DS20-OLD",
        },
    )
    entry.add_to_hass(hass)

    info = _service_info(name="DS20-NEW", address=address)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=info,
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # The stored discovered device info was refreshed from the new advertisement.
    assert entry.data["discovered_device_address"] == address
    assert entry.data["discovered_device_name"] == "DS20-NEW"

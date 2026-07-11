"""Diagnostics support for the WyBot integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import WyBotConfigEntry
from .const import CONF_WIFI_PASSWORD, CONF_WIFI_SSID

# Key used by the config flow to persist the Bluetooth address of a
# discovered device (see config_flow.CONF_DISCOVERED_DEVICE_ADDRESS).
CONF_DISCOVERED_DEVICE_ADDRESS = "discovered_device_address"

# Secrets to strip from the config entry data.
TO_REDACT_ENTRY = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_WIFI_PASSWORD,
    CONF_WIFI_SSID,
    CONF_DISCOVERED_DEVICE_ADDRESS,
}

# Identifying/BLE fields to strip from the coordinator summary. These match at
# any nesting depth because async_redact_data recurses into nested mappings.
TO_REDACT_COORDINATOR = {"ble_name", "mac"}


def _serialize_dp(dp: Any) -> Any:
    """Return a plain, JSON-safe representation of a DP object.

    DP values are ``GenericDP`` instances (with a ``dict()`` method returning
    ``id``/``type``/``len``/``data``). We never dump raw bytes: ``data`` is a
    hex string. Anything unexpected falls back to ``str()`` so serialization
    can never raise.
    """
    try:
        if hasattr(dp, "dict"):
            return dp.dict()
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        pass
    return str(dp)


def _serialize_device(obj: Any, id_attr: str, type_attr: str) -> dict[str, Any]:
    """Summarize a Device/Docker: id, type, ble name, online, and DP state."""
    dps = getattr(obj, "dps", None) or {}
    return {
        "id": getattr(obj, id_attr, None),
        "type": getattr(obj, type_attr, None),
        "ble_name": getattr(obj, "ble_name", None),
        "online": getattr(obj, "online", None),
        "dps": {key: _serialize_dp(value) for key, value in dps.items()},
    }


def _serialize_group(group: Any) -> dict[str, Any]:
    """Summarize a single Group (its device and optional docker)."""
    info: dict[str, Any] = {
        "group_id": getattr(group, "id", None),
        "name": getattr(group, "name", None),
        "device": None,
        "docker": None,
    }
    device = getattr(group, "device", None)
    if device is not None:
        info["device"] = _serialize_device(device, "device_id", "device_type")
    docker = getattr(group, "docker", None)
    if docker is not None:
        info["docker"] = _serialize_device(docker, "docker_id", "docker_type")
    return info


def _serialize_coordinator(coordinator: Any) -> dict[str, Any] | None:
    """Summarize the coordinator state, tolerating missing data."""
    if coordinator is None:
        return None
    data = getattr(coordinator, "data", None) or {}
    return {
        "available": bool(getattr(coordinator, "available", False)),
        "device_count": len(data),
        "groups": {
            group_id: _serialize_group(group) for group_id, group in data.items()
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WyBotConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = getattr(entry, "runtime_data", None)
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT_ENTRY),
        "coordinator": async_redact_data(
            _serialize_coordinator(coordinator), TO_REDACT_COORDINATOR
        ),
    }

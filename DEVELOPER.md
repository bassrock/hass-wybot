# Developer Guide

## Prerequisites

- Python 3.13+ (tested on 3.14)
- pip

## Setup

```bash
pip install pytest pydantic
```

> **Note:** You do _not_ need Home Assistant installed to run the model tests. The test suite mocks all HA dependencies.

## Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Specific test file
python -m pytest tests/test_dp_models.py -v
python -m pytest tests/test_models.py -v

# Single test class or method
python -m pytest tests/test_dp_models.py::TestCleaningStatus -v
python -m pytest tests/test_models.py::TestCommand::test_get_dps_as_keyed_dict -v
```

## Test Structure

```
tests/
├── conftest.py          # Fixtures + mocked HA/BLE/MQTT dependencies
├── test_dp_models.py    # Data Point models (DP, CleaningStatus, Battery, etc.)
└── test_models.py       # API/MQTT response models (Command, Device, Group, etc.)
```

`conftest.py` uses `unittest.mock.MagicMock` to stub out Home Assistant, bleak, paho-mqtt, and other runtime dependencies so the pydantic data models can be tested in isolation.

For a detailed list of all tests and what they verify, see [TESTS.md](TESTS.md).

## Architecture Overview

See [README.md](README.md) for user-facing docs. The WyBot protocol/transport
clients and data models live in the [pywybot](https://github.com/bassrock/pywybot)
package (`import wybot`). This repo contains the Home Assistant glue:

| Module | Role |
|--------|------|
| `wybot_coordinator.py` | DataUpdateCoordinator (BLE-first, MQTT fallback) |
| `bluetooth_adapter.py` | Adapts HA's Bluetooth stack to pywybot's `BluetoothAdapter` |
| `config_flow.py` | Setup, reauth, and options flows |
| `{sensor,binary_sensor,button,vacuum}.py` | Entity platforms |

The clients themselves — `WyBotHTTPClient`, `WyBotMQTTClient`, `WyBotBLEClient`,
the pydantic models, and the typed `Wybot*Error` exceptions — come from `pywybot`.

## Quality scale

Progress toward the [Home Assistant Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
is tracked in [`custom_components/wybot/quality_scale.yaml`](custom_components/wybot/quality_scale.yaml).
The code-side Bronze and Silver rules are implemented. The remaining items require
work outside this repo or a running HA test environment:

- **`test-coverage` / `config-flow-test-coverage`** — grow the `pytest-homeassistant-custom-component`
  suite (see below) to exceed 95% coverage of every module.
- **`dependency-transparency`** — **done.** The WyBot client is now the
  [pywybot](https://github.com/bassrock/pywybot) PyPI package (import name `wybot`;
  BLE decoupled from HA via `bluetooth_adapter.HomeAssistantBluetoothAdapter`), pinned
  as `pywybot==0.1.0` in `manifest.json`. The client's own model/protocol tests live in
  the pywybot repo.

Once `test-coverage` and `brands` are also met, flip `manifest.json` to
`"quality_scale": "silver"`.
- **`brands`** — submit a logo/icon PR to
  [home-assistant/brands](https://github.com/home-assistant/brands).

## Home Assistant test harness

Newer tests use the real HA test harness instead of mocking Home Assistant:

```bash
pip install pytest-homeassistant-custom-component
python -m pytest tests/ -v
```

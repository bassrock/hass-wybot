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

## Architecture Overview

See [README.md](README.md) for user-facing docs. Key modules:

| Module | Role |
|--------|------|
| `wybot_dp_models.py` | Data Point classes (protocol layer) |
| `wybot_models.py` | Pydantic models for HTTP/MQTT API responses |
| `wybot_coordinator.py` | DataUpdateCoordinator (BLE-first, MQTT fallback) |
| `wybot_ble_client.py` | BLE communication (AA55 binary protocol) |
| `wybot_mqtt_client.py` | MQTT pub/sub client |
| `wybot_http_client.py` | REST API client |

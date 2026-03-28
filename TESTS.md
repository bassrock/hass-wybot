# Test Reference

76 tests across 2 test files. Run with `python -m pytest tests/ -v`.

## test_dp_models.py — Data Point Models (47 tests)

### TestDP — Pydantic DP model
| Test | Description |
|------|-------------|
| `test_create_full` | Create DP with all fields |
| `test_create_query_only` | Create DP with only `id` (query mode) |
| `test_create_from_dict` | Create DP via `model_validate()` |
| `test_serialization` | `model_dump()` roundtrip |

### TestGenericDP — Base DP wrapper class
| Test | Description |
|------|-------------|
| `test_init_from_dp` | Initialize from DP pydantic model |
| `test_dict_method` | `.dict()` returns correct dict |
| `test_str_repr` | String representation includes class name and data |

### TestCleaningStatus — DP 0 (Start/Stop/Return)
| Test | Description |
|------|-------------|
| `test_cleaning` | Data `03` → `CLEANING` |
| `test_stopped` | Data `01` → `STOPPED` |
| `test_returning` | Data `02` → `RETURNING` |
| `test_returning_to_dock` | Data `04` → `RETURNING_TO_DOCK` |
| `test_setter` | Setting `CLEANING` → data becomes `03` |
| `test_setter_stopped` | Setting `STOPPED` → data becomes `01` |
| `test_no_data_returns_unknown` | No data → `UNKNOWN` |
| `test_init_with_status_kwarg` | Create with `status=` keyword |
| `test_str_repr` | String includes status name |

### TestCleaningMode — DP 1 (Cleaning mode)
| Test | Description |
|------|-------------|
| `test_floor_mode` | Data `00` → "Floor" |
| `test_wall_mode` | Data `01` → "Wall" |
| `test_all_modes_roundtrip` | All 7 modes survive set/get cycle |
| `test_setter` | Setting "Turbo Floor" → data `05` |
| `test_no_data_defaults_to_floor` | No data → defaults to "Floor" |

### TestBattery — DP 50 (Battery level + charge state)
| Test | Description |
|------|-------------|
| `test_charging_50` | `0132` → charging, 50% |
| `test_charged_100` | `0264` → charged, 100% |
| `test_unplugged_75` | `004b` → unplugged, 75% |
| `test_no_data` | None data → 0%, not plugged in |

### TestDock — DP 11 (Dock status)
| Test | Description |
|------|-------------|
| `test_docked` | Data `00` → `DOCKED` |
| `test_returning` | Data `01` → `RETURNING` |
| `test_setter` | Setting `RETURNING` → data `01` |
| `test_no_data` | No data → `GENERAL` |

### TestSolarEnergyHarvested — DP 131
| Test | Description |
|------|-------------|
| `test_energy_wh` | Little-endian `e8030000` → 1000 Wh |
| `test_energy_kwh` | 1000 Wh → 1.0 kWh |
| `test_no_data` | None → 0 Wh |

### TestSolarDockBattery — DP 221
| Test | Description |
|------|-------------|
| `test_battery_level` | `01480a` → 72% (byte 1) |
| `test_no_data` | None → 0% |

### TestSolarStatus — DP 222
| Test | Description |
|------|-------------|
| `test_charging` | Data `01` → charging |
| `test_not_charging` | Data `00` → not charging |

### TestDockInfo — DP 214
| Test | Description |
|------|-------------|
| `test_solar_dock` | Data `05` → Solar type, `is_solar_dock = True` |
| `test_unknown_type` | Data `ff` → Unknown type |

### TestDockConnectionStatus — DP 213
| Test | Description |
|------|-------------|
| `test_docked` | Data `01` → docked |
| `test_undocked` | Data `00` → undocked |

### TestConnectionStatus — DP 212
| Test | Description |
|------|-------------|
| `test_connected` | Data `01` → connected |
| `test_disconnected` | Data `00` → disconnected |

### TestDeviceStatus — DP 209
| Test | Description |
|------|-------------|
| `test_status_value` | Data `03` → status_value = 3 |

### TestSchedule — DP 79
| Test | Description |
|------|-------------|
| `test_raw_schedule` | Returns raw hex string |

### TestDPMapping — `wybot_dp_id` dict
| Test | Description |
|------|-------------|
| `test_known_ids_return_correct_class` | DP 0→CleaningStatus, 1→CleaningMode, etc. |
| `test_unknown_id_falls_back_to_generic` | Unknown ID → GenericDP |
| `test_all_mapped_classes_can_instantiate` | Every mapped class can be created from a DP |

---

## test_models.py — API/MQTT Response Models (29 tests)

### TestToSnakeCase — Helper function
| Test | Description |
|------|-------------|
| `test_camel_case` | "deviceId" → "device_id" |
| `test_pascal_case` | "DeviceName" → "device_name" |
| `test_already_snake` | "device_id" unchanged |
| `test_single_word` | "token" unchanged |
| `test_consecutive_caps` | "deviceID" → "device_i_d" |
| `test_empty_string` | "" → "" |

### TestCommand — MQTT command envelope
| Test | Description |
|------|-------------|
| `test_create_from_dict` | Parse JSON with cmd, ts, dp list |
| `test_dp_list_types` | All dp items are DP instances |
| `test_get_dps_as_keyed_dict` | Returns {id: TypedDP} using `wybot_dp_id` mapping |
| `test_unknown_dp_id_defaults_to_generic` | Unknown DP ID → GenericDP |
| `test_populate_by_name` | snake_case field names work directly |

### TestLoginModels — Authentication
| Test | Description |
|------|-------------|
| `test_login_metadata_from_api` | Parses camelCase (userId, regTime, etc.) |
| `test_login_response_success` | Full response with nested metadata |
| `test_login_response_no_metadata` | 401 response, metadata = None |

### TestVersion — Firmware info
| Test | Description |
|------|-------------|
| `test_from_api_alias` | `{"Firmware": "1.2.3"}` via alias |
| `test_none_firmware` | Firmware can be None |

### TestDevice — Robot device
| Test | Description |
|------|-------------|
| `test_from_api` | All camelCase aliases (deviceId, bleName, etc.) |
| `test_defaults` | online=False, dps={} |
| `test_get_dp_returns_none_when_empty` | No DPs → None |
| `test_get_dp_raises_for_non_generic` | Non-GenericDP class → TypeError |

### TestDocker — Dock device
| Test | Description |
|------|-------------|
| `test_from_api` | All aliases (dockerId, dockerType, etc.) |
| `test_defaults` | online=False, dps={} |

### TestVision — Vision module
| Test | Description |
|------|-------------|
| `test_from_api` | Parses visionId alias, optional fields |

### TestGroup — Device group (robot + dock + vision)
| Test | Description |
|------|-------------|
| `test_from_api` | Full group with device, docker, vision |
| `test_without_docker` | docker = None (standalone robot) |
| `test_get_dp_searches_device_first` | Searches device DPs first |
| `test_get_dp_searches_docker_fallback` | Falls back to docker DPs |

### TestDevicesResponse — Full API response
| Test | Description |
|------|-------------|
| `test_full_response` | Nested code/reason/message/metadata/groups |
| `test_empty_groups` | Empty groups list |

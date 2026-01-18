"""Constants for the WyBot integration."""

DOMAIN = "wybot"
TIMEOUT = 30
MANUFACTURER = "WyBot"

# WiFi configuration constants (for manual WiFi provisioning via diagnostic button)
CONF_WIFI_SSID = "wifi_ssid"
CONF_WIFI_PASSWORD = "wifi_password"

# BLE command configuration
BLE_COMMAND_TIMEOUT = 25.0  # seconds (includes connection + status wait + CleaningMode query)
BLE_COMMAND_HOLD_TIME = 2.0  # seconds to wait after write for acknowledgment
BLE_MAX_CONSECUTIVE_FAILURES = 3  # disable BLE commands after N consecutive failures

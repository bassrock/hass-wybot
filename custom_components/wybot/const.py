"""Constants for the WyBot integration."""

DOMAIN = "wybot"
MANUFACTURER = "WyBot"

# The F1 skimmer reports this device type. It has no dock (the solar panel is
# integrated), exposes sensors the DS20 robots do not, and encodes several DPs
# differently, so a number of entities are only created for it.
F1_DEVICE_TYPE = "WYF1"

# WiFi configuration constants (for manual WiFi provisioning via diagnostic button)
CONF_WIFI_SSID = "wifi_ssid"
CONF_WIFI_PASSWORD = "wifi_password"

# Disable BLE commands after N consecutive failures.
BLE_MAX_CONSECUTIVE_FAILURES = 3

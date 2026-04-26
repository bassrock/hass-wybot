"""Library for interacting with the WyBot MQTT API."""

from collections.abc import Callable
import json
import logging
import time
import uuid

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)

MQTT_URL = "mqtt.wybotpool.com"

# User/Password to authenticate from the iOS/Android app to the MQTT server
# These can be found in the app's network traffic, unsecured......... with a very basic wireshark packet capture.
# Wireshark even shows it as a "password" field in the packet capture...... Given this, and the fact its hardcoded to be the same
# FOR EVERY USER, I'm not too worried about sharing it here.
# Please don't abuse this, it's just for home automation purposes, It would suck for WyBot to disable this :(
USERNAME = "wyindustry"
PASWORD = "nwe_GTG4faf2qyx8ugx"

# Flag to disable MQTT command sending (useful for recording iOS app traffic)
# Set to True to prevent Home Assistant from sending any commands
DISABLE_MQTT_COMMANDS = False


class WyBotMQTTClient:
    """Client for interacting with the WyBot MQTT API."""

    def __init__(self, on_message: Callable) -> None:
        """Init the wybot mqtt api."""
        # Instance-level state (not class-level to avoid sharing across instances)
        self._subscriptions: set[str] = set()
        self._devices: set[str] = set()
        self._connected: bool = False
        self._connecting: bool = False
        self._loop_started: bool = False

        # Generate a UUID-based client ID like mobile apps use
        client_id = f"wybot-{uuid.uuid4()}"
        _LOGGER.debug("MQTT client ID: %s", client_id)
        self._mqtt = mqtt.Client(client_id=client_id, clean_session=True)
        self._mqtt.username_pw_set(USERNAME, PASWORD)
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message_handler
        self._mqtt.on_connect_fail = self._on_connect_fail
        self._mqtt.on_disconnect = self._on_disconnect
        # Enable paho's built-in auto-reconnect with exponential backoff
        self._mqtt.reconnect_delay_set(min_delay=1, max_delay=60)
        self._on_message = on_message

    def connect(self):
        """Connect to the MQTT server.

        Self-healing: if our flag says connected but paho's socket is dead
        (on_disconnect didn't fire, or paho's auto-retry is wedged), force
        a reconnect via paho's reconnect() — reuses the existing loop thread.
        """
        if self._connected and not self._mqtt.is_connected():
            _LOGGER.warning(
                "MQTT client flag drift — paho socket is dead; forcing reconnect"
            )
            self._connected = False
            self._connecting = True
            try:
                self._mqtt.reconnect()
            except Exception as err:
                _LOGGER.error("MQTT reconnect failed: %s", err)
                self._connecting = False
            return
        if self._connecting or self._connected:
            _LOGGER.debug("Already connected or connection in progress")
            return
        _LOGGER.debug("Connecting to wybot mqtt server %s", MQTT_URL)
        self._connecting = True
        self._connected = False
        try:
            self._mqtt.connect(MQTT_URL)
            if not self._loop_started:
                self._mqtt.loop_start()
                self._loop_started = True
        except Exception as err:
            _LOGGER.error("Failed to initiate MQTT connection: %s", err)
            self._connecting = False
            self._connected = False

    def is_connected(self) -> bool:
        """Check if connected to the MQTT server."""
        return self._connected and self._mqtt.is_connected()

    def disconnect(self):
        """Stop the MQTT client."""
        _LOGGER.info("Stopping MQTT client")
        self._connected = False
        self._connecting = False
        if self._loop_started:
            self._mqtt.loop_stop()
            self._loop_started = False
        try:
            self._mqtt.disconnect()
        except Exception as err:
            _LOGGER.debug("Error during disconnect: %s", err)

    def _on_connect(self, client: mqtt.Client, userdata, flags, reason_code):
        """Handle successful connection."""
        if reason_code == 0:
            _LOGGER.info("MQTT connected successfully")
            self._connected = True
            self._connecting = False
            # Re-subscribe to all topics on (re)connect
            for subscription in self._subscriptions:
                client.subscribe(subscription)
            # Request status updates for all devices
            for device in self._devices:
                self.ensure_device_sends_statuses(device)
        else:
            _LOGGER.warning("MQTT connection failed with result code %d", reason_code)
            self._connected = False
            self._connecting = False

    def _on_connect_fail(self, client, userdata):
        """Handle connection failure."""
        _LOGGER.warning("MQTT connect failed, paho will auto-retry")
        self._connected = False
        self._connecting = False

    def _on_disconnect(self, client, userdata, rc):
        """Handle disconnection. Paho auto-reconnects if rc != 0."""
        self._connected = False
        self._connecting = False
        if rc == 0:
            _LOGGER.debug("MQTT disconnected cleanly")
        else:
            _LOGGER.warning(
                "MQTT unexpected disconnect (rc=%d), paho will auto-reconnect", rc
            )

    def subscribe_for_device(self, device_id):
        """Subscribe to a device (idempotent — safe to call multiple times)."""
        if device_id in self._devices:
            _LOGGER.debug("Already subscribed to device %s", device_id)
            return

        _LOGGER.debug("Subscribing to wybot mqtt for device %s", device_id)
        topics = [
            f"/will/{device_id}",
            f"/device/DATA/send_transparent_data/{device_id}",
            f"/device/DATA/recv_transparent_query_data/{device_id}",
            f"/device/DATA/recv_transparent_cmd_data/{device_id}",
            f"/device/OTA/post_update_progress/{device_id}",
            f"/device/OTA/notify_ready_to_update/{device_id}",
        ]
        for topic in topics:
            self._subscriptions.add(topic)
            self._mqtt.subscribe(topic)
        self._devices.add(device_id)
        self.ensure_device_sends_statuses(device_id)

    def ensure_device_sends_statuses(self, deviceId: str):
        """Ensure that a device sends statuses."""
        _LOGGER.debug("Ensuring device sends statuses %s", deviceId)

        # Query DPs individually in the exact order the iOS app uses
        # iOS app queries: 1, 79, 1, 0, 77 (DP 1 is queried twice)
        # We also include: 50 (battery), 11 (dock), and solar dock DPs
        # Solar dock DPs: 131 (energy), 221 (dock battery), 222 (solar status), 214 (dock type)
        # S2 Pro additional DPs: 209, 212, 213
        query_dps = [0, 1, 79, 1, 0, 77, 50, 11, 131, 209, 212, 213, 214, 221, 222]
        for dp_id in query_dps:
            self.send_query_command_for_device(
                deviceId,
                {
                    "ts": int(time.time()),
                    "cmd": 9,
                    "dp": [{"id": dp_id}],
                },
            )

    def send_query_command_for_device(self, device_id: str, command: dict):
        """Send a query command to a device."""
        if DISABLE_MQTT_COMMANDS:
            _LOGGER.debug(
                "MQTT commands disabled, skipping query: %s - %s", device_id, command
            )
            return
        _LOGGER.debug("SENDING QUERY - %s - %s", device_id, command)
        if not self.is_connected():
            _LOGGER.debug(
                "Not connected, cannot send query command (will retry when connected)"
            )
            return
        try:
            topic = f"/device/DATA/recv_transparent_query_data/{device_id}"
            payload = json.dumps(command)
            _LOGGER.debug("Publishing to topic: %s, payload: %s", topic, payload)
            result = self._mqtt.publish(topic, payload)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                _LOGGER.error("Failed to publish query command: %d", result.rc)
            else:
                _LOGGER.debug(
                    "Query command published successfully (mid: %s)", result.mid
                )
        except Exception as err:
            _LOGGER.error("Error sending query command: %s", err)

    def send_write_command_for_device(self, device_id: str, command: dict):
        """Send a write command to a device."""
        if DISABLE_MQTT_COMMANDS:
            _LOGGER.debug(
                "MQTT commands disabled, skipping write: %s - %s", device_id, command
            )
            return
        _LOGGER.debug("SENDING CMD - %s - %s", device_id, command)
        if not self.is_connected():
            _LOGGER.debug(
                "Not connected, cannot send write command (will retry when connected)"
            )
            return
        try:
            result = self._mqtt.publish(
                f"/device/DATA/recv_transparent_cmd_data/{device_id}",
                json.dumps(command),
            )
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                _LOGGER.error("Failed to publish write command: %d", result.rc)
        except Exception as err:
            _LOGGER.error("Error sending write command: %s", err)

    def _on_message_handler(self, client, userdata, msg):
        """Handle the incoming message from the MQTT server."""
        try:
            payload = json.loads(msg.payload)
            _LOGGER.debug(
                "MQTT RECEIVED - Topic: %s, Payload: %s", msg.topic, json.dumps(payload)
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            _LOGGER.debug(
                "MQTT RECEIVED - Topic: %s, Payload (raw): %s", msg.topic, msg.payload
            )
            payload = msg.payload
        self._on_message(msg.topic, payload)

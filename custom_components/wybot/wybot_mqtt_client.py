"""Library for interacting with the WyBot MQTT API."""

import asyncio
from collections.abc import Callable
import json
import logging
import time

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

# Connection retry configuration
MAX_RECONNECT_RETRIES = 5
INITIAL_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 30.0
CONNECTION_TIMEOUT = 10.0

# Flag to disable MQTT command sending (useful for recording iOS app traffic)
# Set to True to prevent Home Assistant from sending any commands
DISABLE_MQTT_COMMANDS = False


class WyBotMQTTClient:
    """Client for interacting with the WyBot MQTT API."""

    _mqtt: mqtt.Client
    _subscriptions: list[str] = []
    _on_message: Callable

    _devices: list[str] = []
    _connected: bool = False
    _connecting: bool = False
    _reconnect_retries: int = 0
    _connection_event: asyncio.Event | None = None

    def __init__(self, on_message: Callable) -> None:
        """Init the wybot mqtt api."""
        self._mqtt = mqtt.Client()
        self._mqtt.username_pw_set(USERNAME, PASWORD)
        self._mqtt.on_connect = self.on_connect
        self._mqtt.on_message = self.on_message
        self._mqtt.on_connect_fail = self.on_connect_fail
        self._mqtt.on_disconnect = self.on_disconnect
        self._on_message = on_message
        self._connection_event = asyncio.Event()

    def connect(self):
        """Connect to the MQTT server."""
        if self._connecting:
            _LOGGER.debug("Connection already in progress")
            return
        _LOGGER.debug("Connecting to wybot mqtt server %s", MQTT_URL)
        self._connecting = True
        self._connected = False
        self._reconnect_retries = 0
        self._connection_event = asyncio.Event()
        self._mqtt.loop_start()
        try:
            self._mqtt.connect(MQTT_URL)
        except Exception as err:
            _LOGGER.error("Failed to initiate MQTT connection: %s", err)
            self._connecting = False
            self._connected = False

    async def async_reconnect(self) -> bool:
        """Re-connect to the MQTT server with exponential backoff."""
        if self._connecting:
            _LOGGER.debug("Reconnection already in progress")
            return False

        if self._reconnect_retries >= MAX_RECONNECT_RETRIES:
            _LOGGER.error("Max reconnection retries reached, giving up")
            self._connected = False
            return False

        self._connecting = True
        self._connected = False
        delay = min(
            INITIAL_RECONNECT_DELAY * (2**self._reconnect_retries), MAX_RECONNECT_DELAY
        )
        self._reconnect_retries += 1

        _LOGGER.debug(
            "Reconnecting to wybot mqtt server %s (attempt %d/%d, delay %.1fs)",
            MQTT_URL,
            self._reconnect_retries,
            MAX_RECONNECT_RETRIES,
            delay,
        )

        await asyncio.sleep(delay)

        self._connection_event = asyncio.Event()
        try:
            self._mqtt.reconnect()
            # Wait for connection to complete with timeout
            try:
                await asyncio.wait_for(
                    self._connection_event.wait(), timeout=CONNECTION_TIMEOUT
                )
                if self._connected:
                    _LOGGER.info("Successfully reconnected to MQTT server")
                    self._reconnect_retries = 0
                    return True
                _LOGGER.warning("Reconnection attempt failed")
                return False
            except TimeoutError:
                _LOGGER.warning("Reconnection timeout after %.1fs", CONNECTION_TIMEOUT)
                return False
        except Exception as err:
            _LOGGER.error("Failed to reconnect: %s", err)
            self._connecting = False
            self._connected = False
            return False

    def reconnect(self):
        """Synchronous reconnect (for backward compatibility)."""
        if self._connecting:
            return
        _LOGGER.debug("Synchronous reconnect called, initiating async reconnect")
        # Create a task for async reconnect if we're in an async context
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the async reconnect
                asyncio.create_task(self.async_reconnect())
            else:
                # Run in the event loop
                loop.run_until_complete(self.async_reconnect())
        except RuntimeError:
            # No event loop, just try direct reconnect
            _LOGGER.debug("No event loop available, using direct reconnect")
            self._connecting = True
            self._connected = False
            self._connection_event = asyncio.Event()
            try:
                self._mqtt.reconnect()
            except Exception as err:
                _LOGGER.error("Failed to reconnect: %s", err)
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
        self._mqtt.loop_stop()
        try:
            self._mqtt.disconnect()
        except Exception as err:
            _LOGGER.debug("Error during disconnect: %s", err)

    def on_connect(self, client: mqtt.Client, userdata, flags, reason_code):
        """Handle successful connection."""
        if reason_code == 0:
            _LOGGER.debug("Connected with result code %d", reason_code)
            self._connected = True
            self._connecting = False
            self._reconnect_retries = 0
            # Re-subscribe to all topics
            for subscription in self._subscriptions:
                client.subscribe(subscription)
            # Note: Don't query devices here - wait for will messages to arrive
            # The coordinator will trigger queries when devices come online
            # This matches iOS app behavior
            # Signal connection event
            if self._connection_event:
                self._connection_event.set()
        else:
            _LOGGER.warning("Connection failed with result code %d", reason_code)
            self._connected = False
            self._connecting = False
            if self._connection_event:
                self._connection_event.set()

    def on_connect_fail(self, client, userdata):
        """Handle connection failure."""
        _LOGGER.debug("Connect failed")
        self._connected = False
        self._connecting = False
        if self._connection_event:
            self._connection_event.set()

    def on_disconnect(self, client, userdata, rc):
        """Handle disconnection."""
        _LOGGER.debug("Disconnected with result code %d", rc)
        self._connected = False
        self._connecting = False
        # If disconnect was unexpected (rc != 0), we'll reconnect on next update
        if rc != 0:
            _LOGGER.info("Unexpected disconnection, will attempt to reconnect")

    def subscribe_for_device_initial(self, device_id):
        """Subscribe to initial topics for a device (will and OTA notifications).

        This matches the iOS app pattern: subscribe to will/OTA topics first,
        before sending any queries.
        """
        _LOGGER.debug(f"Subscribing to initial topics for device {device_id}")
        initial_topics = [
            f"/will/{device_id}",
            f"/device/OTA/notify_ready_to_update/{device_id}",
        ]
        for topic in initial_topics:
            if topic not in self._subscriptions:
                self._subscriptions.append(topic)
            self._mqtt.subscribe(topic)
        if device_id not in self._devices:
            self._devices.append(device_id)

    def subscribe_for_device(self, device_id):
        """Subscribe to response topics for a device.

        This is called after the initial query is sent, matching iOS app pattern.
        """
        _LOGGER.debug(f"Subscribing to response topics for device {device_id}")
        response_topics = [
            f"/device/DATA/send_transparent_data/{device_id}",
            f"/device/OTA/post_update_progress/{device_id}",
            f"/device/DATA/recv_transparent_query_data/{device_id}",
            f"/device/DATA/recv_transparent_cmd_data/{device_id}",
        ]
        for topic in response_topics:
            if topic not in self._subscriptions:
                self._subscriptions.append(topic)
            self._mqtt.subscribe(topic)

    def ensure_device_sends_statuses(self, deviceId: str):
        """Ensure that a device sends statuses (sync version).

        Sends all queries immediately. For async version with delays, use
        ensure_device_sends_statuses_async.
        """
        _LOGGER.debug(f"Ensuring device sends statuses {deviceId}")
        # Query DPs individually in the exact order the iOS app uses
        # iOS app queries: 1, 79, 1, 0, 77 (DP 1 is queried twice)
        # We also include 50 and 11 for our own needs
        query_dps = [1, 79, 1, 0, 77, 50, 11]
        for dp_id in query_dps:
            self.send_query_command_for_device(
                deviceId,
                {
                    "ts": time.time(),
                    "cmd": 9,
                    "dp": [{"id": dp_id}],
                },
            )

    def send_initial_query(self, deviceId: str):
        """Send the initial query (DP 1 only) matching iOS app pattern.

        The iOS app sends only DP 1 query first, before subscribing to
        response topics.
        """
        _LOGGER.debug(f"Sending initial query (DP 1) for device {deviceId}")
        self.send_query_command_for_device(
            deviceId,
            {
                "ts": time.time(),
                "cmd": 9,
                "dp": [{"id": 1}],
            },
        )

    async def ensure_device_sends_statuses_async(self, deviceId: str):
        """Ensure that a device sends statuses (async version with delays).

        Sends remaining queries one at a time with delays, matching iOS app behavior.
        iOS app sends queries with 20-120ms delays between them.
        This sends queries for DPs: 79, 1, 0, 77, 50, 11 (excluding the initial DP 1).
        """
        _LOGGER.debug(f"Ensuring device sends statuses {deviceId} (async with delays)")
        # Query DPs individually in the exact order the iOS app uses
        # iOS app queries: 79, 1, 0, 77 (after initial DP 1 query)
        # We also include 50 and 11 for our own needs
        # iOS app sends queries with 20-120ms delays between them
        query_dps = [79, 1, 0, 77, 50, 11]
        for dp_id in query_dps:
            self.send_query_command_for_device(
                deviceId,
                {
                    "ts": time.time(),
                    "cmd": 9,
                    "dp": [{"id": dp_id}],
                },
            )
            # Add delay between queries to match iOS app behavior (20-120ms)
            # Use 50ms as a middle ground
            await asyncio.sleep(0.05)  # 50ms delay

    async def query_device_ios_pattern(self, deviceId: str):
        """Query device using iOS app pattern.

        Since response topics are already subscribed upfront, this just sends queries:
        1. Send initial query (DP 1)
        2. Wait briefly
        3. Send remaining queries with delays
        """
        _LOGGER.debug(f"Querying device {deviceId} using iOS app pattern")
        if not self.is_connected():
            _LOGGER.debug("Not connected, cannot query device")
            return

        # Step 1: Send initial query (DP 1 only)
        self.send_initial_query(deviceId)

        # Step 2: Wait briefly (50-100ms) before sending remaining queries
        # This matches iOS app timing
        await asyncio.sleep(0.075)  # 75ms delay

        # Step 3: Send remaining queries with delays
        await self.ensure_device_sends_statuses_async(deviceId)

    def send_query_command_for_device(self, device_id: str, command: dict):
        """Send a query command to a device."""
        if DISABLE_MQTT_COMMANDS:
            _LOGGER.debug(
                "MQTT commands disabled, skipping query: %s - %s", device_id, command
            )
            return
        _LOGGER.info("SENDING QUERY - %s - %s", device_id, command)
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

    def on_message(self, client, userdata, msg):
        """Handle the incoming message from the MQTT server."""
        try:
            payload = json.loads(msg.payload)
            # Log all incoming MQTT messages for monitoring (especially useful when DISABLE_MQTT_COMMANDS is True)
            # This captures everything: iOS app commands, device responses, etc.
            _LOGGER.info(
                "MQTT RECEIVED - Topic: %s, Payload: %s", msg.topic, json.dumps(payload)
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            # If payload is not JSON, log as string
            _LOGGER.info(
                "MQTT RECEIVED - Topic: %s, Payload (raw): %s", msg.topic, msg.payload
            )
            payload = msg.payload
        self._on_message(msg.topic, payload)

"""Library for interacting with the WyBot MQTT API."""

import json
import logging

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


class WyBotMQTTClient:
    """Client for interacting with the WyBot MQTT API."""

    _mqtt: mqtt.Client
    _subscriptions: list[str] = []
    _on_message: callable

    def __init__(self, on_message) -> None:
        """Init the wybot mqtt api."""
        self._mqtt = mqtt.Client()
        self._mqtt.username_pw_set(USERNAME, PASWORD)
        self._mqtt.on_connect = self.on_connect
        self._mqtt.on_message = self.on_message
        self._mqtt.on_connect_fail = self.on_connect_fail
        self._on_message = on_message

    def connect(self):
        """Connect to the MQTT server."""
        _LOGGER.debug(f"Connecting to wybot mqtt server {MQTT_URL}")
        self._mqtt.loop_start()
        self._mqtt.connect(MQTT_URL)

    def disconnect(self):
        """Stop the MQTT client."""
        _LOGGER.info("Stopping MQTT client.")
        self._mqtt.loop_stop()
        self._mqtt.disconnect()

    def on_connect(self, client: mqtt.Client, userdata, flags, reasonCode):
        _LOGGER.debug(f"Connected with result code {reasonCode}")
        for subscription in self._subscriptions:
            client.subscribe(subscription)

    def on_connect_fail(self, client, userdata):
        _LOGGER.debug(f"Connect failed")

    def subscribe_for_device(self, device_id):
        """Subscribe to a device."""
        _LOGGER.debug(f"Subscribing to wybot mqtt for device {device_id}")
        self._subscriptions.append(f"/will/{device_id}")
        self._subscriptions.append(f"/device/DATA/send_transparent_data/{device_id}")
        self._subscriptions.append(
            f"/device/DATA/recv_transparent_query_data/{device_id}"
        )
        self._subscriptions.append(
            f"/device/DATA/recv_transparent_cmd_data/{device_id}"
        )
        self._subscriptions.append(f"/device/OTA/post_update_progress/{device_id}")
        self._subscriptions.append(f"/device/OTA/notify_ready_to_update/{device_id}")

    def send_query_command_for_device(self, device_id: str, command: dict):
        """Send a command to a device."""
        _LOGGER.debug(f"SENDING QUERY - {device_id} - {command}")
        self._mqtt.publish(
            f"/device/DATA/recv_transparent_query_data/{device_id}", json.dumps(command)
        )

    def send_write_command_for_device(self, device_id: str, command: dict):
        """Send a command to a device."""
        _LOGGER.debug(f"SENDING CMD - {device_id} - {command}")
        self._mqtt.publish(
            f"/device/DATA/recv_transparent_cmd_data/{device_id}", json.dumps(command)
        )

    def on_message(self, client, userdata, msg):
        """Handle the incoming message from the MQTT server."""
        self._on_message(msg.topic, json.loads(msg.payload))

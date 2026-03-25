# Copyright (c) 2025-2026, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
"""MQTT client for Philips FUSION devices (cloud relay via AWS IoT)."""

from __future__ import annotations

import json
import logging
import secrets
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt

from .local_api import LocalDeviceInfo, LocalDeviceState

_LOGGER = logging.getLogger(__name__)

# MQTT connection settings (from APK DaMqttClientImpl)
MQTT_KEEPALIVE = 30
MQTT_CONNECT_TIMEOUT = 10

# QoS levels
QOS_AT_MOST_ONCE = 0


@dataclass
class FusionDeviceInfo:
    """FUSION device information for MQTT communication."""

    thing_name: str
    device_id: str
    tenant: str
    mqtt_host: str
    platform_rest_url: str
    model_name: str = ""
    model_number: str = ""
    friendly_name: str = ""
    mac_address: str = ""


class PhilipsMQTTClient:
    """MQTT client for FUSION device communication via AWS IoT.

    Uses MQTT over WebSocket Secure with AWS IoT Custom Authorizer
    for authentication. Subscribes to device shadow topics and NCP
    control topics for state updates and command responses.
    """

    def __init__(
        self,
        device: FusionDeviceInfo,
        loop: Any = None,
    ) -> None:
        """Initialize the MQTT client."""
        self._device = device
        self._loop = loop
        self._client: mqtt.Client | None = None
        self._connected = False
        self._state: LocalDeviceState | None = None
        self._state_callback: Callable[[LocalDeviceState], None] | None = None
        self._lock = threading.Lock()

        # Build topic names
        tn = device.thing_name
        t = device.tenant
        self._topics = {
            "shadow_get_accepted": f"$aws/things/{tn}/shadow/get/accepted",
            "shadow_update_accepted": f"$aws/things/{tn}/shadow/update/accepted",
            "shadow_get_rejected": f"$aws/things/{tn}/shadow/get/rejected",
            "shadow_update_rejected": f"$aws/things/{tn}/shadow/update/rejected",
            "from_ncp": f"{t}_ctrl/{tn}/from_ncp",
            "shadow_get": f"$aws/things/{tn}/shadow/get",
            "to_ncp": f"{t}_ctrl/{tn}/to_ncp",
        }

    @property
    def connected(self) -> bool:
        """Return whether the MQTT client is connected."""
        return self._connected

    @property
    def device_state(self) -> LocalDeviceState | None:
        """Return the current device state."""
        return self._state

    def set_state_callback(self, callback: Callable[[LocalDeviceState], None]) -> None:
        """Set callback for state updates."""
        self._state_callback = callback

    def connect(
        self,
        access_token: str,
        mqtt_signature: str,
    ) -> None:
        """Connect to the AWS IoT MQTT broker over WSS.

        Uses Custom Authorizer headers for authentication.
        Must be called from an executor thread (blocking).
        """
        device = self._device

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ha-philips-{secrets.token_hex(8)}",
            transport="websockets",
        )

        # TLS for WSS
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)

        # AWS IoT Custom Authorizer headers via WebSocket
        client.ws_set_options(
            path="/mqtt",
            headers={
                "x-amz-customauthorizer-name": "CustomAuthorizer",
                "x-amz-customauthorizer-signature": mqtt_signature,
                "token-header": f"Bearer {access_token}",
                "tenant": device.tenant,
                "content-type": "application/json",
            },
        )

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        _LOGGER.info(
            "Connecting to MQTT broker %s for device %s",
            device.mqtt_host,
            device.thing_name,
        )

        client.connect(
            device.mqtt_host,
            port=443,
            keepalive=MQTT_KEEPALIVE,
        )
        client.loop_start()
        self._client = client

        # Wait for connection
        deadline = time.monotonic() + MQTT_CONNECT_TIMEOUT
        while not self._connected and time.monotonic() < deadline:
            time.sleep(0.1)

        if not self._connected:
            client.loop_stop()
            raise ConnectionError(f"MQTT connection to {device.mqtt_host} timed out")

    def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self._connected = False

    def request_state(self) -> None:
        """Request the device shadow state.

        Publishes an empty message to the shadow/get topic.
        The response arrives on shadow/get/accepted.
        """
        if not self._client or not self._connected:
            return
        self._client.publish(
            self._topics["shadow_get"],
            payload=b"{}",
            qos=QOS_AT_MOST_ONCE,
        )
        _LOGGER.debug("Requested shadow state for %s", self._device.thing_name)

    def send_port_command(
        self,
        port_name: str,
        command_name: str = "updatePort",
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Send a port command to the device via NCP.

        Args:
            port_name: The device port (e.g., "airfryer", "status")
            command_name: "updatePort" or "getPort"
            properties: Dict of properties to set (for updatePort)
        """
        if not self._client or not self._connected:
            _LOGGER.warning("Cannot send command: MQTT not connected")
            return

        data: dict[str, Any] = {"portName": port_name}
        if properties:
            data["properties"] = properties
        payload = {
            "cid": secrets.token_hex(16),
            "time": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "type": "command",
            "cn": command_name,
            "ct": "mobile",
            "data": data,
        }

        self._client.publish(
            self._topics["to_ncp"],
            payload=json.dumps(payload),
            qos=QOS_AT_MOST_ONCE,
        )
        _LOGGER.debug(
            "Sent %s to %s/%s: %s",
            command_name,
            self._device.thing_name,
            port_name,
            properties,
        )

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Handle MQTT connection established."""
        if reason_code == 0:
            self._connected = True
            _LOGGER.info(
                "MQTT connected to %s for %s",
                self._device.mqtt_host,
                self._device.thing_name,
            )
            # Subscribe to all device topics
            for name, topic in self._topics.items():
                if name in (
                    "shadow_get_accepted",
                    "shadow_update_accepted",
                    "shadow_get_rejected",
                    "shadow_update_rejected",
                    "from_ncp",
                ):
                    client.subscribe(topic, qos=QOS_AT_MOST_ONCE)
                    _LOGGER.debug("Subscribed to %s", topic)

            # Request initial state
            self.request_state()
        else:
            _LOGGER.error("MQTT connection failed: %s", reason_code)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Handle MQTT disconnection."""
        self._connected = False
        if reason_code != 0:
            _LOGGER.warning("MQTT disconnected unexpectedly: %s", reason_code)
        else:
            _LOGGER.info("MQTT disconnected")

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Handle incoming MQTT message."""
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            _LOGGER.debug("Non-JSON MQTT message on %s", msg.topic)
            return

        _LOGGER.debug("MQTT message on %s: %s", msg.topic, str(payload)[:500])

        if msg.topic == self._topics["shadow_get_accepted"]:
            self._handle_shadow(payload)
        elif msg.topic == self._topics["shadow_update_accepted"]:
            self._handle_shadow(payload)
        elif msg.topic == self._topics["from_ncp"]:
            self._handle_ncp_response(payload)
        elif "rejected" in msg.topic:
            _LOGGER.warning("Shadow request rejected: %s", payload)

    def _handle_shadow(self, payload: dict[str, Any]) -> None:
        """Parse shadow document and update device state."""
        state = payload.get("state", {})
        reported = state.get("reported", {})

        if not reported:
            return

        device_info = LocalDeviceInfo(
            ip_address="",
            cpp_id=self._device.device_id,
            model_name=self._device.model_name,
            model_number=self._device.model_number,
            friendly_name=self._device.friendly_name,
        )

        with self._lock:
            if self._state is None:
                self._state = LocalDeviceState(device_info=device_info)

            self._state.power_on = reported.get("powerOn", False)
            self._state.connection_state = "connected"

            # Merge reported properties into state
            for key, value in reported.items():
                if key != "powerOn":
                    self._state.properties[key] = value

        self._notify_state_update()

    def _handle_ncp_response(self, payload: dict[str, Any]) -> None:
        """Parse NCP response and update device state."""
        data = payload.get("data", {})
        port_name = data.get("portName", "")
        properties = data.get("properties", {})

        if not port_name or not properties:
            # Could be a status response or other NCP message
            _LOGGER.debug("NCP message without port/properties: %s", payload)
            return

        device_info = LocalDeviceInfo(
            ip_address="",
            cpp_id=self._device.device_id,
            model_name=self._device.model_name,
            model_number=self._device.model_number,
            friendly_name=self._device.friendly_name,
        )

        with self._lock:
            if self._state is None:
                self._state = LocalDeviceState(device_info=device_info)

            # Store port data the same way local_api does
            self._state.properties[port_name] = properties
            self._state.connection_state = "connected"

            # Update power state from airfryer status
            if port_name in ("airfryer", "venusaf", "venus1af", "nutrimax", "hermesac"):
                status = properties.get("status", "")
                self._state.power_on = status in (
                    "cooking",
                    "pause",
                    "setting",
                    "precook",
                    "parasetting",
                    "maintain",
                    "user_action",
                )

        self._notify_state_update()

    def _notify_state_update(self) -> None:
        """Notify the callback of a state update."""
        if self._state_callback and self._state:
            if self._loop:
                self._loop.call_soon_threadsafe(self._state_callback, self._state)
            else:
                self._state_callback(self._state)

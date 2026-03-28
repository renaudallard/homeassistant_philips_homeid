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
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


import paho.mqtt.client as mqtt

from .local_api import LocalDeviceInfo, LocalDeviceState

# NCP port name → local API port name (entities use local API names)
_NCP_PORT_MAP: dict[str, str] = {
    "Status": "airfryer",
    "Config": "config",
    "Control": "control",
}

# NCP property name → local API property name
# NCP (FUSION MQTT) uses different keys than the local HTTP API.
_NCP_PROPERTY_MAP: dict[str, str] = {
    "drw_opn": "drawer_open",
    "shk_rm_act": "shake",
    "prev_stat": "prev_status",
}

# Reverse maps for sending commands (local API names → NCP names)
_LOCAL_PORT_MAP: dict[str, str] = {v: k for k, v in _NCP_PORT_MAP.items()}
_LOCAL_PROPERTY_MAP: dict[str, str] = {v: k for k, v in _NCP_PROPERTY_MAP.items()}

_LOGGER = logging.getLogger(__name__)

# MQTT connection settings (from APK DaMqttClientImpl)
MQTT_KEEPALIVE = 30
MQTT_CONNECT_TIMEOUT = 30

# QoS levels (from APK fv/a enum)
QOS_AT_MOST_ONCE = 0  # subscriptions
QOS_AT_LEAST_ONCE = 1  # publishes (shadow + NCP commands)


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
    user_id: str = ""


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
        credential_refresh: Callable[[], tuple[str, str]] | None = None,
    ) -> None:
        """Initialize the MQTT client.

        Args:
            device: FUSION device information.
            loop: asyncio event loop for thread-safe callbacks.
            credential_refresh: Callable that returns (access_token, signature)
                for reconnection after token expiry. Called from background thread.
        """
        self._device = device
        self._loop = loop
        self._credential_refresh = credential_refresh
        self._client: mqtt.Client | None = None
        self._connected = False
        self._reconnecting = False
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
            "shadow_update": f"$aws/things/{tn}/shadow/update",
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
        """Connect to the AWS IoT MQTT broker via WSS on port 443.

        Uses Custom Authorizer headers for authentication.
        Port 8883 requires mutual TLS (client certificates) which we
        don't have, so only WSS is supported.
        Must be called from an executor thread (blocking).
        """
        device = self._device

        _LOGGER.info(
            "MQTT credentials: token=%s...%s (%d chars), signature=%s...%s (%d chars)",
            access_token[:8] if access_token else "EMPTY",
            access_token[-4:] if len(access_token) > 12 else "",
            len(access_token),
            mqtt_signature[:8] if mqtt_signature else "EMPTY",
            mqtt_signature[-4:] if len(mqtt_signature) > 12 else "",
            len(mqtt_signature),
        )

        # APK client ID format: {userId}_{UUID}
        # userId from POST /api/da/user/self/get-id (NOT the JWT sub claim).
        prefix = device.user_id or device.device_id
        client_id = f"{prefix}_{uuid.uuid4()}"
        _LOGGER.info("MQTT client_id: %s", client_id)

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            transport="websockets",
            # APK uses cleanSession=false (persistent session)
            clean_session=False,
        )

        # TLS for WSS
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)

        # AWS IoT Custom Authorizer headers via WebSocket upgrade.
        # Use a callable to remove the Origin header that Python paho adds
        # by default -- the APK's Java Paho does not send Origin and the
        # Custom Authorizer may reject connections that include it.
        auth_headers = {
            "x-amz-customauthorizer-name": "CustomAuthorizer",
            "x-amz-customauthorizer-signature": mqtt_signature,
            "token-header": f"Bearer {access_token}",
            "tenant": device.tenant,
            "content-type": "application/json",
        }

        def _apply_ws_headers(
            default_headers: dict[str, str],
        ) -> dict[str, str]:
            default_headers.pop("Origin", None)
            default_headers.update(auth_headers)
            return default_headers

        client.ws_set_options(path="/mqtt", headers=_apply_ws_headers)

        # APK enables Paho's built-in auto-reconnect
        client.reconnect_delay_set(min_delay=1, max_delay=60)

        self._setup_callbacks(client)

        _LOGGER.info(
            "WSS connecting to %s:443 for %s",
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

        self._wait_for_connection(client)

    def _setup_callbacks(self, client: mqtt.Client) -> None:
        """Wire up MQTT callbacks and debug logging."""
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        # Detailed protocol logging for debugging connection issues
        def _on_log(
            _client: mqtt.Client,
            _userdata: Any,
            level: int,
            buf: str,
        ) -> None:
            _LOGGER.debug("paho: [%s] %s", level, buf)

        client.on_log = _on_log

    def _wait_for_connection(self, client: mqtt.Client) -> None:
        """Wait for MQTT CONNACK or raise on timeout."""
        deadline = time.monotonic() + MQTT_CONNECT_TIMEOUT
        while not self._connected and time.monotonic() < deadline:
            time.sleep(0.1)

        if not self._connected:
            client.loop_stop()
            raise ConnectionError(
                f"MQTT connection to {self._device.mqtt_host} timed out"
            )

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
            qos=QOS_AT_LEAST_ONCE,
        )
        _LOGGER.debug("Requested shadow state for %s", self._device.thing_name)

    def _request_port_data(self) -> None:
        """Discover available ports via NCP getAllPorts.

        The shadow only has device-level state (powerOn, productState).
        Airfryer-specific properties (status, temperature, time) come
        from NCP port responses on the from_ncp topic.

        NCP port names may differ from local HTTP port names, so we
        use getAllPorts to discover what the device supports, then
        getPort for each discovered port.
        """
        self.send_port_command("", command_name="getAllPorts")
        _LOGGER.debug("Sent getAllPorts for %s", self._device.thing_name)

    def set_power(self, power_on: bool) -> None:
        """Set device power via shadow update (APK UpdatePowerState)."""
        if not self._client or not self._connected:
            return
        payload = json.dumps({"state": {"desired": {"powerOn": power_on}}})
        self._client.publish(
            self._topics["shadow_update"],
            payload=payload,
            qos=QOS_AT_LEAST_ONCE,
        )
        _LOGGER.debug("Shadow power update: %s", power_on)

    def send_port_command(
        self,
        port_name: str,
        command_name: str = "updatePort",
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Send a port command to the device via NCP.

        Args:
            port_name: The device port (e.g., "airfryer", "status").
                       Empty string for commands that don't need a port (getAllPorts).
            command_name: "updatePort", "getPort", "getAllPorts", etc.
            properties: Dict of properties to set (for updatePort)
        """
        if not self._client or not self._connected:
            _LOGGER.warning("Cannot send command: MQTT not connected")
            return

        data: dict[str, Any] | None = None
        if port_name:
            # Map local API port name to NCP port name
            ncp_port = _LOCAL_PORT_MAP.get(port_name, port_name)
            data = {"portName": ncp_port}
            if properties:
                # Map local API property names to NCP names
                ncp_props: dict[str, Any] = {}
                for k, v in properties.items():
                    ncp_props[_LOCAL_PROPERTY_MAP.get(k, k)] = v
                data["properties"] = ncp_props
        # CID: APK uses 8-char hex (32-bit random, byte-reversed)
        cid = secrets.token_bytes(4).hex()
        payload: dict[str, Any] = {
            "cid": cid,
            # APK NcpRequestTime: "yyyy-MM-dd'T'HH:mm:ss'Z'" (no fractional seconds)
            "time": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": "command",
            "cn": command_name,
            "ct": "mobile",
        }
        if data is not None:
            payload["data"] = data

        self._client.publish(
            self._topics["to_ncp"],
            payload=json.dumps(payload),
            qos=QOS_AT_LEAST_ONCE,
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
        _LOGGER.info("MQTT on_connect: reason_code=%s, flags=%s", reason_code, flags)
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

            # Request initial state (shadow + port data)
            self.request_state()
            self._request_port_data()
        else:
            _LOGGER.error("MQTT CONNACK rejected: reason_code=%s", reason_code)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Handle MQTT disconnection with reconnection."""
        self._connected = False
        if reason_code == 0:
            _LOGGER.info("MQTT disconnected gracefully")
            return

        _LOGGER.warning("MQTT disconnected unexpectedly: %s", reason_code)

        if self._reconnecting or not self._credential_refresh:
            return

        # Reconnect with fresh credentials in a background thread
        # (matching APK's reactive reconnect pattern)
        self._reconnecting = True
        thread = threading.Thread(target=self._reconnect_with_backoff, daemon=True)
        thread.start()

    def _reconnect_with_backoff(self) -> None:
        """Reconnect with exponential backoff and fresh credentials."""
        delay = 1.0
        max_retries = 5
        for attempt in range(max_retries):
            time.sleep(delay)
            _LOGGER.info("MQTT reconnect attempt %d/%d", attempt + 1, max_retries)
            try:
                assert self._credential_refresh is not None
                access_token, signature = self._credential_refresh()
                # Disconnect old client
                if self._client:
                    self._client.loop_stop()
                # Connect with fresh credentials
                self.connect(access_token, signature)
                _LOGGER.info("MQTT reconnected successfully")
                self._reconnecting = False
                return
            except Exception:
                _LOGGER.warning("MQTT reconnect attempt %d failed", attempt + 1)
                delay = min(delay * 1.5, 60.0)

        _LOGGER.error("MQTT reconnection failed after %d attempts", max_retries)
        self._reconnecting = False

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
        command = payload.get("cn", "")
        status = payload.get("status")

        # Handle getAllPorts response: send getPort for each discovered port
        if command == "getAllPorts" and status == 0:
            ports_data = payload.get("data", [])
            if isinstance(ports_data, list):
                for p in ports_data:
                    pname = p.get("portName", "") if isinstance(p, dict) else ""
                    if pname:
                        _LOGGER.info("Discovered NCP port: %s", pname)
                        self.send_port_command(pname, command_name="getPort")
            return

        if status is not None and status != 0:
            _LOGGER.debug("NCP error status %s for %s: %s", status, command, payload)
            return

        data = payload.get("data", {})
        ncp_port = data.get("portName", "") if isinstance(data, dict) else ""
        properties = data.get("properties", {}) if isinstance(data, dict) else {}

        if not ncp_port or not properties:
            _LOGGER.debug("NCP message without port/properties: %s", payload)
            return

        # Map NCP port name to local API port name
        port_name = _NCP_PORT_MAP.get(ncp_port, ncp_port)

        # Normalize NCP property names to local API names
        for ncp_key, local_key in _NCP_PROPERTY_MAP.items():
            if ncp_key in properties and local_key not in properties:
                properties[local_key] = properties[ncp_key]

        _LOGGER.debug(
            "NCP port %s -> %s: %s", ncp_port, port_name, list(properties.keys())
        )

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
            if port_name in (
                "airfryer",
                "venusaf",
                "venus1af",
                "nutrimax",
                "hermesac",
            ):
                port_status = properties.get("status", "")
                self._state.power_on = port_status in (
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

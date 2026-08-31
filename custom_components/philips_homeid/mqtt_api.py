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

import copy
import json
import logging
import secrets
import ssl
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .local_api import LocalDeviceInfo, LocalDeviceState

if TYPE_CHECKING:
    import paho.mqtt.client as mqtt

# NCP port name → local API port name (entities use local API names)
_NCP_PORT_MAP: dict[str, str] = {
    # Airfryer status ports (APK VenusStatusPortKt / SpectreStatusPortKt)
    "Status": "airfryer",  # SPECTRE (HD928x) + Venus 1 (HD987x)
    "venusaf_s": "airfryer",  # Venus 2 (HD9880)
    # Airfryer control ports (APK VenusControlPortKt / SpectreControlPortKt)
    "Config": "config",
    "Control": "control",  # SPECTRE + Venus 1
    "venusaf_c": "control",  # Venus 2 (HD9880)
    # SPECTRE recipe/user-preset control port (APK SpectreRecipeControlPortKt).
    # Distinct from Control: it carries recipe_id/step_id; the Control port has
    # no such fields and rejects them with NCP port_error.
    "recipe_c": "recipe_control",
    # Venus device state (APK VenusDeviceCurrentStatePortKt)
    # Merged into airfryer dict, same as local API does with devcurrstate
    "devcurst_s": "airfryer",
    # Venus firmware (APK VenusFirmwarePortKt)
    "firmware_s": "firmware",
    # Device settings (firmware-reported, not in APK port specs).
    # Contains fw_update (→upgrade via property map) for firmware entity.
    "devsett_s": "firmware",
    # Venus recipe (APK VenusRecipeStatusPortKt)
    "recipe_s": "recipe",
    # Venus auto cook (APK VenusAutoCookStatusPortKt)
    "acp_s": "autocook",
    # Espresso ports (from APK espresso/PortsKt.java)
    "machinestatus": "machinestatus",
    "command": "command",
    "command/BasicRecipe": "basicrecipe",
    "configuration": "configuration",
    "device": "device",
}

# NCP property name → local API property name
# NCP (FUSION MQTT) uses different keys than the local HTTP API.
# From APK VenusStatusPortProperties / VenusDeviceCurrentStatePortProperties
# @SerializedName annotations.
_NCP_PROPERTY_MAP: dict[str, str] = {
    "drw_opn": "drawer_open",
    "shk_rm_act": "shake",
    "prev_stat": "prev_status",
    "probe_unpl": "probe_unplugged",
    "probe_rqrd": "probe_required",
    # NCP uses curr_temp, Venus HTTP uses current_temp (both → cur_temp via Venus map)
    "curr_temp": "current_temp",
    "cur_tmp_pr": "current_temp_probe",
    # Firmware port (APK VenusFirmwarePortProperties): versions → version
    "versions": "version",
    # Device settings port: fw_update → upgrade
    "fw_update": "upgrade",
    # Recipe port (APK VenusRecipeStatusPortProperties): rec_cur_st → cur_stage
    "rec_cur_st": "cur_stage",
}

# Venus key normalization (Venus→SPECTRE), applied after _NCP_PROPERTY_MAP
# for airfryer-mapped ports only. Mirrors local_api._VENUS_KEY_MAP.
_VENUS_KEY_MAP: dict[str, str] = {
    "disp_time": "cur_time",
    "total_time": "time",
    "method": "preset",
    "current_temp": "cur_temp",
}

# NCP status codes (from APK NcpStatusCode.java)
_NCP_STATUS_NAMES: dict[int, str] = {
    0: "ok",
    1: "busy",
    2: "json_error",
    3: "correlation_id_error",
    4: "client_type_error",
    5: "time_error",
    6: "message_type_error",
    7: "command_name_error",
    8: "port_error",
    9: "write_read_only_port",
    10: "read_write_only_port",
    11: "serialization_error",
    12: "mqtt_error",
}

_NCP_STATUS_BUSY = 1

# getPort pacing. The MUJI purifier NCP (AC0651, likely AC0650/AC1715 too) is
# effectively single-threaded: concurrent getPorts are answered with busy(1),
# and the busy reply carries an empty data object, so it does not even name the
# port it refused. A sustained burst can wedge the NCP until the appliance is
# power-cycled, which also locks out the official app. So getPort reads are
# serialized: one in flight at a time, a gap between sends, and bounded retries
# for a port that answers busy.
_PORT_SEND_GAP = 1.0  # seconds between consecutive getPort sends
_PORT_BUSY_RETRY_DELAY = 3.0  # seconds before retrying a port that answered busy
_PORT_BUSY_RETRY_LIMIT = 3  # busy replies for a port before deferring to next round
# from_ncp is subscribed QoS0, so a reply can be lost. This bounds how long a
# lost reply stalls the queue before the next port is tried; kept short so a
# single loss cannot push the startup port-fetch past its budget.
_PORT_INFLIGHT_TIMEOUT = 5.0  # seconds to wait for a getPort reply

# Reverse maps for sending commands (local API names → NCP names)
# Use first-wins to prefer SPECTRE/Venus 1 names as default fallback;
# actual resolution uses discovered ports (see _resolve_ncp_port).
_LOCAL_PORT_MAP: dict[str, str] = {}
for _ncp, _local in _NCP_PORT_MAP.items():
    _LOCAL_PORT_MAP.setdefault(_local, _ncp)
_LOCAL_PROPERTY_MAP: dict[str, str] = {v: k for k, v in _NCP_PROPERTY_MAP.items()}

# Reverse Venus key map for sending commands to Venus control ports.
# Coordinator uses SPECTRE-normalized names; Venus NCP expects Venus names.
_VENUS_SEND_KEY_MAP: dict[str, str] = {v: k for k, v in _VENUS_KEY_MAP.items()}

# Venus NCP control port names (for send-path key mapping)
_VENUS_CONTROL_PORTS = {"venusaf_c"}

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


class MqttCredentialsRejected(Exception):
    """The cloud refused to mint MQTT credentials for this account.

    Raised by the credential_refresh callable when the refresh token is no
    longer accepted (password change, revoked session). Retrying cannot fix
    that, so the reconnect loop gives up instead of backing off forever.
    """


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
        self._refreshing = False  # True during proactive token refresh
        self._stop = threading.Event()  # Set by disconnect() to stop reconnect loop
        # Serializes the actual reconnect dance so a proactive refresh and a
        # reactive backoff-reconnect can never produce two live paho clients.
        self._reconnect_lock = threading.Lock()
        # Guards the _reconnecting claim itself. The flag is touched by the
        # paho network thread, the event loop and the reconnect thread, so
        # checking and setting it has to be one step. See claim_reconnect.
        self._reconnecting_lock = threading.Lock()
        self._connect_time: float = 0.0  # monotonic time of last connect
        self._state: LocalDeviceState | None = None
        self._state_callback: Callable[[LocalDeviceState], None] | None = None
        self._lock = threading.Lock()
        self._discovered_ports: list[str] = []  # NCP read port names from getAllPorts
        self._discovered_write_ports: list[str] = []  # NCP write port names
        # NCP read ports the device has replied about, and the subset that
        # actually reported. The answers arrive one message at a time, so
        # these are what tell a caller the state is whole rather than merely
        # started. See ports_replied and ports_complete.
        self._replied_ports: set[str] = set()
        self._reported_ports: set[str] = set()
        self._device_type: str | None = None  # Cached get_device_type() result
        # getPort serialization (see _pump_port_queue): one read in flight, the
        # rest queued. A busy reply carries no port name, so the in-flight port
        # is what a busy reply is attributed to; the in-flight cid is what
        # distinguishes our solicited reply from this chatty device's own
        # unsolicited status pushes.
        self._port_queue: deque[str] = deque()
        self._inflight_port: str | None = None
        self._inflight_cid: str | None = None
        self._inflight_since: float = 0.0
        self._port_busy_counts: dict[str, int] = {}
        self._pump_timer: threading.Timer | None = None

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
        return self._connected or self._refreshing

    @property
    def device_state(self) -> LocalDeviceState | None:
        """Return the current device state."""
        return self._state

    def claim_reconnect(self) -> bool:
        """Take ownership of the reconnect, or report that someone else has it.

        Only one reconnect may own the client. The check and the set are one
        step because the callers do not share a thread: a proactive refresh
        runs on the event loop while on_disconnect runs on the paho network
        thread, and both reading False meant two reconnect threads, whose
        teardowns then fought over each other's client.
        """
        with self._reconnecting_lock:
            if self._reconnecting:
                return False
            self._reconnecting = True
            return True

    def release_reconnect(self) -> None:
        """Give up ownership of the reconnect."""
        with self._reconnecting_lock:
            self._reconnecting = False

    @property
    def ports_discovered(self) -> bool:
        """Return whether getAllPorts has named the device's read ports.

        False means the appliance never answered the discovery command, which
        is how an NCP that is asleep behaves: the link is up and the publish
        is acknowledged, but nothing comes back.
        """
        return bool(self._discovered_ports)

    @property
    def ports_replied(self) -> bool:
        """Return whether every discovered read port has replied to its getPort.

        Includes ports that replied busy or with nothing to say, so a caller
        waiting on the appliance stops as soon as it has heard from all of
        them rather than sitting out its whole deadline.
        """
        if not self._discovered_ports:
            return False
        with self._lock:
            return all(port in self._replied_ports for port in self._discovered_ports)

    @property
    def ports_complete(self) -> bool:
        """Return whether every discovered read port has reported its state.

        getAllPorts is followed by one getPort per port and the appliance
        answers them one message at a time, so the arrival of the first port
        says nothing about the rest. A caller that judges a property missing
        before this is True is really just reading a half-filled state. A port
        that replied busy does not count: what it holds is still unknown.
        """
        if not self._discovered_ports:
            return False
        with self._lock:
            return all(port in self._reported_ports for port in self._discovered_ports)

    @property
    def is_venus(self) -> bool:
        """Return True if this is a Venus-style FUSION device."""
        venus_ports = {"venusaf_s", "venusaf_c", "devcurst_s"}
        all_ports = set(self._discovered_ports) | set(self._discovered_write_ports)
        return bool(venus_ports & all_ports)

    def has_cooking_control_port(self) -> bool:
        """Return True if the device advertises a cooking control port.

        Venus 2 (HD9880) does not advertise venusaf_c while in standby;
        the device must be woken via shadow powerOn first.
        """
        control_ports = {"venusaf_c", "Control"}
        return bool(control_ports & set(self._discovered_write_ports))

    def wake_airfryer(self) -> None:
        """Wake a FUSION airfryer from standby by setting shadow powerOn.

        Needed for Venus 2 devices (HD9880) which hide their cooking
        control port (venusaf_c) while in standby. After wake, the
        device re-advertises its ports so a fresh getAllPorts is issued.
        """
        if not self._client or not self._connected:
            return
        self.set_power(True)
        self._request_port_data()

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

        _LOGGER.debug(
            "MQTT credentials: token=%d chars, signature=%d chars",
            len(access_token),
            len(mqtt_signature),
        )

        # APK client ID format: {userId}_{UUID}
        # userId from POST /api/da/user/self/get-id (NOT the JWT sub claim).
        prefix = device.user_id or device.device_id
        client_id = f"{prefix}_{uuid.uuid4()}"
        # The prefix is the Philips account id, and logs get attached to
        # issues, so report the shape rather than the value.
        _LOGGER.debug("MQTT client_id built (prefix %d chars)", len(prefix))

        # paho ships with core but is not in our requirements, and a local
        # only device never reaches this code, so importing it here keeps
        # those setups working on an install that does not have it.
        import paho.mqtt.client as mqtt

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            transport="websockets",
            # The client_id rotates every connect (fresh UUID), so the
            # broker cannot resume a prior session anyway. Setting
            # clean_session=True tells the broker the truth and avoids
            # leaving orphaned session state behind. The APK reuses one
            # client_id and sets clean_session=false; we don't, so we
            # shouldn't pretend to.
            clean_session=True,
            # Paho's own auto-reconnect replays the WS upgrade headers it
            # captured at connect time, i.e. the expired access token, and it
            # races _reconnect_with_backoff for the same client. The backoff
            # loop is the only path that mints fresh credentials, so it owns
            # reconnection and Paho's engine stays off.
            reconnect_on_failure=False,
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
        """Wait for MQTT CONNACK, stop event, or raise on timeout."""
        deadline = time.monotonic() + MQTT_CONNECT_TIMEOUT
        while not self._connected and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if self._stop.wait(min(0.1, remaining)):
                # disconnect() requested while waiting; abandon the connect
                # so the reconnect lock can be released quickly.
                client.loop_stop()
                if self._client is client:
                    self._client = None
                raise ConnectionError("MQTT connect aborted by disconnect")

        if not self._connected:
            client.loop_stop()
            if self._client is client:
                self._client = None
            raise ConnectionError(
                f"MQTT connection to {self._device.mqtt_host} timed out"
            )

    def disconnect(self) -> None:
        """Disconnect from the MQTT broker and stop any pending reconnects."""
        self._stop.set()
        self._cancel_port_queue()
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self._connected = False

    def needs_token_refresh(self) -> bool:
        """Check if the MQTT token is about to expire (>50 minutes old)."""
        if not self._connected or self._connect_time == 0.0:
            return False
        age = time.monotonic() - self._connect_time
        if age > 2700:  # Log when approaching refresh threshold (45+ min)
            _LOGGER.debug("MQTT token age: %.0f seconds", age)
        return age > 3000  # 50 minutes

    def _teardown_client(self) -> None:
        """Stop the current client and mark the link down.

        disconnect() does dispatch on_disconnect, synchronously on this
        thread: loop_stop() joins the network thread and clears paho's
        _thread, so the DISCONNECT packet is written by loop_write() right
        here, which fires the callback itself. It is harmless because a
        client-initiated disconnect carries reason code 0 and on_disconnect
        returns early on that, without claiming a reconnect. Anything that
        relaxes that early return has to account for being called from here.

        It does not clear _connected, so that is done by hand. Leaving it set
        makes _wait_for_connection() return before the new CONNACK lands and
        hides a failed reconnect behind a connected-looking client.
        """
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
        self._connected = False

    def _do_reconnect(self, access_token: str, signature: str) -> None:
        """Disconnect old client and connect with new credentials (blocking)."""
        with self._reconnect_lock:
            if self._stop.is_set():
                return
            self._teardown_client()
            # Queued reads belong to the old session; the fresh getAllPorts
            # after CONNACK rebuilds the queue.
            self._cancel_port_queue()
            # Clear discovered ports so they're re-fetched by getAllPorts.
            # Keep the cached state in place so consumers don't see a brief
            # unavailability gap while the new shadow + NCP messages arrive;
            # the device's first push after CONNACK overwrites whatever
            # fields it owns.
            self._discovered_ports = []
            self._discovered_write_ports = []
            self.connect(access_token, signature)

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

    def refresh_port_data(self) -> None:
        """Re-request data from previously discovered ports.

        Uses cached port names from the last getAllPorts response.
        If no ports discovered yet, sends getAllPorts to discover them.
        """
        if not self._client or not self._connected:
            return
        # Copy to avoid race with MQTT thread writing _discovered_ports
        ports = list(self._discovered_ports)
        if ports:
            self._enqueue_port_reads(ports, new_round=True)
        else:
            self._request_port_data()

    def _enqueue_port_reads(self, ports: list[str], new_round: bool = False) -> None:
        """Queue getPort reads, sent one at a time by _pump_port_queue.

        new_round resets the per-port busy counters so a port that gave up
        after _PORT_BUSY_RETRY_LIMIT busy replies gets a fresh chance on the
        next refresh cycle.
        """
        with self._lock:
            if new_round:
                self._port_busy_counts.clear()
            for pname in ports:
                if pname != self._inflight_port and pname not in self._port_queue:
                    self._port_queue.append(pname)
        self._pump_port_queue()

    def _pump_port_queue(self) -> None:
        """Send the next queued getPort if none is in flight.

        Serialization is what keeps the MUJI NCP alive: it answers concurrent
        reads with busy(1) and a sustained burst can wedge it until the
        appliance is power-cycled (observed on the AC0651), which also locks
        out the official app.
        """
        with self._lock:
            if not self._client or not self._connected:
                self._port_queue.clear()
                self._inflight_port = None
                self._inflight_cid = None
                return
            if self._inflight_port is not None:
                if time.monotonic() - self._inflight_since < _PORT_INFLIGHT_TIMEOUT:
                    return
                _LOGGER.debug(
                    "getPort for %s got no reply in %.0fs, moving on",
                    self._inflight_port,
                    _PORT_INFLIGHT_TIMEOUT,
                )
                self._inflight_port = None
                self._inflight_cid = None
            if not self._port_queue:
                return
            port = self._port_queue.popleft()
            self._inflight_port = port
            self._inflight_cid = None
            self._inflight_since = time.monotonic()
        # Publish outside the lock: send_port_command does network I/O, and the
        # reply can land on the paho thread before it returns.
        cid = self.send_port_command(port, command_name="getPort")
        with self._lock:
            if self._inflight_port != port:
                # The reply already landed (matched by type) and armed its own
                # pump, so do not touch the timer here.
                return
            if cid is None:
                # The link dropped mid-send; the reconnect getAllPorts rebuilds
                # the queue, so drop what is left rather than orphan it.
                self._inflight_port = None
                self._port_queue.clear()
                return
            self._inflight_cid = cid
            # Safety net: advance if the reply never arrives (from_ncp is QoS0).
            # Armed under the lock so a reply that raced in cannot be clobbered.
            self._arm_pump_timer_locked(_PORT_INFLIGHT_TIMEOUT + 0.5)

    def _arm_pump_timer_locked(self, delay: float) -> None:
        """(Re)arm the timer that runs _pump_port_queue after delay seconds.

        Caller must hold self._lock. Every arm and cancel of _pump_timer runs
        under the lock, so the in-flight reply's short pump always wins over
        the safety timer instead of racing it.
        """
        if self._pump_timer is not None:
            self._pump_timer.cancel()
        timer = threading.Timer(delay, self._pump_port_queue)
        timer.daemon = True
        self._pump_timer = timer
        timer.start()

    def _cancel_port_queue(self) -> None:
        """Drop queued reads and stop the pump timer (on disconnect)."""
        with self._lock:
            if self._pump_timer is not None:
                self._pump_timer.cancel()
                self._pump_timer = None
            self._port_queue.clear()
            self._inflight_port = None
            self._inflight_cid = None

    def _on_getport_reply(self, payload: dict[str, Any]) -> None:
        """Track the in-flight getPort and pace the next send.

        A busy reply carries an empty data object, so the in-flight port is
        the only way to know which port was refused; busy ports are retried a
        bounded number of times per round, with a breather, then deferred to
        the next refresh cycle. This device also emits unsolicited status
        pushes, which are told apart from our solicited reply by message type
        and correlation id so they do not advance the queue.
        """
        reply_type = payload.get("type")
        reply_cid = payload.get("cid")
        status = payload.get("status")
        delay = _PORT_SEND_GAP
        with self._lock:
            port = self._inflight_port
            if port is None:
                return  # nothing in flight: an unsolicited push
            # A status event is typed non-response; a stale reply carries a cid
            # that does not match the read we are waiting on. Either way it is
            # not our in-flight reply, so leave the queue untouched and let the
            # real reply (or the safety timeout) advance it.
            if reply_type is not None and reply_type != "response":
                return
            if (
                reply_cid is not None
                and self._inflight_cid is not None
                and reply_cid != self._inflight_cid
            ):
                return
            self._inflight_port = None
            self._inflight_cid = None
            if status == _NCP_STATUS_BUSY:
                tries = self._port_busy_counts.get(port, 0) + 1
                self._port_busy_counts[port] = tries
                if tries < _PORT_BUSY_RETRY_LIMIT:
                    if port not in self._port_queue:
                        self._port_queue.append(port)
                    delay = _PORT_BUSY_RETRY_DELAY
                else:
                    _LOGGER.debug(
                        "NCP port %s busy %d times, deferring to next refresh",
                        port,
                        tries,
                    )
            else:
                self._port_busy_counts.pop(port, None)
            self._arm_pump_timer_locked(delay)

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

    def _is_air_purifier(self) -> bool:
        """Return True for MUJI air purifiers (AC0650/AC0651/AC1715).

        Deferred import of get_device_type avoids a circular import with
        sensor_descriptions, matching the coordinator's convention.
        """
        if self._device_type is None:
            from .sensor_descriptions import get_device_type

            self._device_type = get_device_type(self._device.model_name or "")
        return self._device_type == "air_purifier"

    def _resolve_ncp_port(self, local_port: str) -> str:
        """Resolve local API port name to the NCP port name for this device.

        Prefers discovered ports (device-specific) over static reverse map.
        Venus 2 uses different NCP names (venusaf_c) than SPECTRE/Venus 1 (Control).
        Write ports (venusaf_c, Control) are also checked so control commands
        resolve correctly.
        """
        # Copy to avoid race with MQTT thread writing discovered port lists
        discovered_all = list(self._discovered_ports) + list(
            self._discovered_write_ports
        )
        for discovered in discovered_all:
            if _NCP_PORT_MAP.get(discovered) == local_port:
                return discovered
        return _LOCAL_PORT_MAP.get(local_port, local_port)

    def send_port_command(
        self,
        port_name: str,
        command_name: str = "setPort",
        properties: dict[str, Any] | None = None,
    ) -> str | None:
        """Send a port command to the device via NCP.

        Args:
            port_name: The device port (e.g., "airfryer", "status").
                       Empty string for commands that don't need a port (getAllPorts).
            command_name: "updatePort", "getPort", "getAllPorts", etc.
            properties: Dict of properties to set (for updatePort)

        Returns the correlation id (cid) stamped on the message, so a caller
        that needs to match the reply (the serialized getPort queue) can, or
        None when nothing was sent because the link is down.
        """
        if not self._client or not self._connected:
            _LOGGER.warning("Cannot send command: MQTT not connected")
            return None

        data: dict[str, Any] | None = None
        if port_name:
            # Map local API port name to NCP port name
            ncp_port = self._resolve_ncp_port(port_name)
            data = {"portName": ncp_port}
            if properties:
                # Map local API property names to NCP names
                ncp_props: dict[str, Any] = {}
                for k, v in properties.items():
                    ncp_props[_LOCAL_PROPERTY_MAP.get(k, k)] = v
                # Venus control ports use different property names
                if ncp_port in _VENUS_CONTROL_PORTS or (
                    self.is_venus and ncp_port == "Control"
                ):
                    ncp_props = {
                        _VENUS_SEND_KEY_MAP.get(k, k): v for k, v in ncp_props.items()
                    }
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
        return cid

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Handle MQTT connection established."""
        _LOGGER.debug("MQTT on_connect: reason_code=%s, flags=%s", reason_code, flags)
        if reason_code == 0:
            self._connected = True
            self._connect_time = time.monotonic()
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
        # Ignore disconnect from a stale client (replaced by proactive_reconnect)
        if client is not self._client:
            _LOGGER.debug("Ignoring disconnect from stale MQTT client")
            return

        self._connected = False
        if reason_code == 0:
            _LOGGER.info("MQTT disconnected gracefully")
            return

        _LOGGER.warning("MQTT disconnected unexpectedly: %s", reason_code)

        if not self._credential_refresh or not self.claim_reconnect():
            return

        # Reconnect with fresh credentials in a background thread
        # (matching APK's reactive reconnect pattern)
        thread = threading.Thread(target=self._reconnect_with_backoff, daemon=True)
        thread.start()

    def _reconnect_with_backoff(self) -> None:
        """Reconnect indefinitely with exponential backoff until disconnect().

        A bounded retry count left the integration silently dead after a long
        outage: _reconnecting was cleared, _connected stayed False, and the
        proactive refresh path requires _connected=True to fire. Loop until
        disconnect() sets _stop or the reconnect succeeds.
        """
        delay = 1.0
        attempt = 0
        try:
            while not self._stop.is_set():
                attempt += 1
                if self._stop.wait(delay):
                    return
                _LOGGER.log(
                    logging.INFO if attempt == 1 else logging.DEBUG,
                    "MQTT reconnect attempt %d",
                    attempt,
                )
                try:
                    assert self._credential_refresh is not None
                    access_token, signature = self._credential_refresh()
                    with self._reconnect_lock:
                        # _connected means another reconnect already restored
                        # the link; tearing it down here would drop a working
                        # client just to rebuild it.
                        if self._stop.is_set() or self._connected:
                            return
                        self._teardown_client()
                        # Queued reads belong to the dropped session; the fresh
                        # getAllPorts after CONNACK rebuilds the queue.
                        self._cancel_port_queue()
                        self.connect(access_token, signature)
                    _LOGGER.info("MQTT reconnected successfully")
                    return
                except MqttCredentialsRejected as err:
                    # The account itself is the problem; the caller has asked
                    # the user to re-authenticate. Backing off would only hide
                    # that behind an endless retry.
                    _LOGGER.error("MQTT reconnect abandoned: %s", err)
                    return
                except Exception as err:
                    # The loop has no attempt cap, so only the first failure
                    # is worth a warning. After that it is a trace of a
                    # condition the user cannot act on.
                    _LOGGER.log(
                        logging.WARNING if attempt == 1 else logging.DEBUG,
                        "MQTT reconnect attempt %d failed: %s",
                        attempt,
                        err,
                    )
                    delay = min(delay * 1.5, 300.0)
        finally:
            self.release_reconnect()

    def start_reconnect(self) -> None:
        """Start the reactive reconnect loop if disconnected and idle.

        Used to recover after a failed proactive token-refresh reconnect: at
        that point no client is alive to emit on_disconnect, so nothing else
        would restart the connection.
        """
        if self._stop.is_set() or self._connected or not self._credential_refresh:
            return
        if not self.claim_reconnect():
            return
        thread = threading.Thread(target=self._reconnect_with_backoff, daemon=True)
        thread.start()

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

        try:
            if msg.topic == self._topics["shadow_get_accepted"]:
                self._handle_shadow(payload)
            elif msg.topic == self._topics["shadow_update_accepted"]:
                self._handle_shadow(payload)
            elif msg.topic == self._topics["from_ncp"]:
                self._handle_ncp_response(payload)
            elif "rejected" in msg.topic:
                _LOGGER.warning("Shadow request rejected: %s", payload)
        except Exception:
            _LOGGER.exception("Error handling MQTT message on %s", msg.topic)

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

            # shadow/update/accepted only carries the fields that changed in
            # that update, so a partial device report that omits powerOn must
            # not be read as power off. Only update when the key is present.
            if "powerOn" in reported:
                self._state.power_on = reported["powerOn"]
            self._state.connection_state = "connected"

            # Merge reported properties into state
            for key, value in reported.items():
                if key != "powerOn":
                    self._state.properties[key] = value

            snapshot = copy.deepcopy(self._state)

        self._notify_state_update(snapshot)

    def _handle_ncp_response(self, payload: dict[str, Any]) -> None:
        """Parse NCP response and update device state."""
        command = payload.get("cn", "")
        status = payload.get("status")

        # Handle getAllPorts response: send getPort for each discovered port
        if command == "getAllPorts" and status == 0:
            ports_data = payload.get("data", [])
            if isinstance(ports_data, list):
                read_ports = []
                write_ports = []
                for p in ports_data:
                    if not isinstance(p, dict):
                        continue
                    pname = p.get("portName", "")
                    direction = p.get("direction", "")
                    if not pname:
                        continue
                    if direction == "read":
                        read_ports.append(pname)
                        _LOGGER.debug("Discovered NCP read port: %s", pname)
                    elif direction == "write":
                        write_ports.append(pname)
                        _LOGGER.debug("Discovered NCP write port: %s", pname)
                self._discovered_ports = read_ports
                self._discovered_write_ports = write_ports
                # A fresh discovery re-asks every port, so nothing has
                # replied this round yet.
                with self._lock:
                    self._replied_ports = set()
                    self._reported_ports = set()
                self._enqueue_port_reads(read_ports, new_round=True)
            return

        # Pace the serialized getPort queue off each reply. Done before the
        # busy/normal handling below so a busy reply (which carries no port
        # name) still advances the queue.
        if command == "getPort":
            self._on_getport_reply(payload)

        data = payload.get("data", {})
        ncp_port = data.get("portName", "") if isinstance(data, dict) else ""

        if status is not None and status != 0:
            status_name = (
                _NCP_STATUS_NAMES.get(status, "unknown")
                if isinstance(status, int)
                else status
            )
            _LOGGER.debug("NCP %s (%s) for %s", status_name, status, command)
            # The device replied but declined to report, so a caller waiting
            # on the port set is not left waiting for a message that already
            # came. It is not recorded as reported: a busy port's properties
            # are still unknown, and pruning on that would be a guess.
            if ncp_port:
                with self._lock:
                    self._replied_ports.add(ncp_port)
            return

        properties = data.get("properties", {}) if isinstance(data, dict) else {}

        if not ncp_port:
            _LOGGER.debug("NCP message without a port name: %s", payload)
            return

        # A port with nothing to say has still reported: acp_s and recipe_s
        # answer empty whenever no program or recipe is running. Recording it
        # only when properties arrive left the port set never complete.
        with self._lock:
            self._replied_ports.add(ncp_port)
            self._reported_ports.add(ncp_port)

        if not properties:
            _LOGGER.debug("NCP port %s reported no properties", ncp_port)
            return

        # Map NCP port name to local API port name
        port_name = _NCP_PORT_MAP.get(ncp_port, ncp_port)

        # Normalize NCP property names to local API names
        for ncp_key, local_key in _NCP_PROPERTY_MAP.items():
            if ncp_key in properties and local_key not in properties:
                properties[local_key] = properties[ncp_key]

        # Apply Venus→SPECTRE normalization for Venus airfryer ports only
        # (matches local_api._normalize_venus_response behavior)
        if port_name == "airfryer" and self.is_venus:
            for venus_key, spectre_key in _VENUS_KEY_MAP.items():
                if venus_key in properties and spectre_key not in properties:
                    properties[spectre_key] = properties[venus_key]

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
            self._state.connection_state = "connected"

            # MUJI air purifiers report flat D-code properties on the Status and
            # filter-read (filtRd) ports. Store those at top level so the air
            # purifier entities (keyed on the raw D-code) resolve, and sidestep
            # the Status->airfryer remap collision in _NCP_PORT_MAP. Other ports
            # (Config, firmware) keep their nested layout for the diagnostic
            # sensors that read nested_key="config"/"firmware".
            if self._is_air_purifier() and ncp_port in ("Status", "filtRd"):
                # Air purifier power state comes from the AWS-IoT shadow
                # (powerOn) in _handle_shadow, not from a D-code. The Status
                # port only carries D-code readings (fan speed, pm2.5, filters,
                # ...) that idle to 0 while the device is still on, so merge them
                # at top level and leave power_on to the shadow.
                self._state.properties.update(properties)
            else:
                # Merge port properties (NCP push updates only send changed fields)
                existing = self._state.properties.get(port_name)
                if existing and isinstance(existing, dict):
                    existing.update(properties)
                else:
                    self._state.properties[port_name] = properties

                # Update power state from device port data
                # Only update when status is actually present (devcurst_s merges
                # into airfryer but has no status field).
                if port_name == "airfryer" and "status" in properties:
                    port_status = properties["status"]
                    self._state.power_on = port_status in (
                        "cooking",
                        "pause",
                        "setting",
                        "precook",
                        "parasetting",
                        "maintain",
                        "user_action",
                        "idle",
                        "finish",
                    )
                elif port_name == "machinestatus":
                    # Espresso: mainstate != 0 means active
                    mainstate = properties.get("mainstate")
                    if mainstate is not None:
                        self._state.power_on = mainstate != 0

            snapshot = copy.deepcopy(self._state)

        self._notify_state_update(snapshot)

    def _notify_state_update(self, state_snapshot: LocalDeviceState) -> None:
        """Notify the callback of a state update.

        Accepts a deep-copied snapshot to avoid cross-thread mutation.
        """
        if self._state_callback and state_snapshot:
            if self._loop:
                self._loop.call_soon_threadsafe(self._state_callback, state_snapshot)
            else:
                self._state_callback(state_snapshot)

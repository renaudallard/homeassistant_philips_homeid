"""Tests for when connect() publishes the client the callbacks reach for."""

import sys
import threading
import types
from collections import deque
from unittest.mock import MagicMock

from custom_components.philips_homeid.mqtt_api import (
    FusionDeviceInfo,
    PhilipsMQTTClient,
)


class _EagerClient:
    """A paho stand-in that answers CONNACK the moment the loop starts.

    That is the interleaving the real thing is allowed to produce: the socket
    can already hold the CONNACK when loop_start() hands it to a thread.
    """

    def __init__(self, *args, **kwargs):
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.on_log = None
        self.published = []

    def tls_set(self, **kwargs):
        pass

    def ws_set_options(self, **kwargs):
        pass

    def connect(self, host, port=None, keepalive=None):
        pass

    def loop_start(self):
        self.on_connect(self, None, {}, 0)

    def loop_stop(self):
        pass

    def subscribe(self, topic, qos=0):
        pass

    def publish(self, topic, payload=None, qos=0):
        self.published.append((topic, payload))


def _install_fake_paho(monkeypatch):
    """Stand in for paho.mqtt.client, which connect() imports on the fly.

    paho is not a requirement of this integration, for the reason connect()
    gives, so it is absent wherever the mqtt integration has not pulled it in.
    That means the whole package chain has to be stood up and not just the
    leaf: `import paho.mqtt.client` returns the top level paho, so the import
    reaches for that name even when the leaf is already in sys.modules.
    """
    client_module = types.ModuleType("paho.mqtt.client")
    client_module.Client = _EagerClient
    client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2="v2")
    mqtt_module = types.ModuleType("paho.mqtt")
    mqtt_module.client = client_module
    paho_module = types.ModuleType("paho")
    paho_module.mqtt = mqtt_module

    monkeypatch.setitem(sys.modules, "paho", paho_module)
    monkeypatch.setitem(sys.modules, "paho.mqtt", mqtt_module)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", client_module)


def _mqtt_client():
    device = FusionDeviceInfo(
        thing_name="thing",
        device_id="dev",
        tenant="da",
        mqtt_host="broker.example",
        platform_rest_url="rest.example",
        user_id="user",
    )
    client = PhilipsMQTTClient(device)
    client._lock = threading.Lock()
    client._port_queue = deque()
    return client


def test_the_first_commands_reach_the_broker(monkeypatch):
    """on_connect sends the shadow get and getAllPorts.

    Both drop the command when the client handle is not published yet, so a
    CONNACK that lands during loop_start() used to cost the initial state and
    the port list, and on a reconnect sent them down the dead link instead.
    """
    _install_fake_paho(monkeypatch)

    client = _mqtt_client()
    client._wait_for_connection = MagicMock()
    client.connect("token", "signature")

    topics = [topic for topic, _ in client._client.published]
    assert client._topics["shadow_get"] in topics
    assert client._topics["to_ncp"] in topics


def test_a_stale_handle_is_replaced_before_the_loop_runs(monkeypatch):
    """A reconnect keeps the old client until connect() swaps it in.

    _teardown_client() stops it but leaves it in place, so publishing before
    the swap put the commands on a link that was already gone.
    """
    _install_fake_paho(monkeypatch)

    client = _mqtt_client()
    stale = _EagerClient()
    client._client = stale
    client._wait_for_connection = MagicMock()
    client.connect("token", "signature")

    assert stale.published == []
    assert client._client is not stale
    assert client._client.published

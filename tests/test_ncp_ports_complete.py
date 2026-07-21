"""Tests for knowing when a FUSION device has reported all of its ports.

Two questions, deliberately separate:
  ports_replied  - has the appliance said anything about every port? Ends the
                   startup wait.
  ports_complete - has every port actually reported its state? Gates pruning.
"""

import threading
from collections import deque
from unittest.mock import MagicMock

from custom_components.philips_homeid.mqtt_api import PhilipsMQTTClient


def _client(discovered):
    client = PhilipsMQTTClient.__new__(PhilipsMQTTClient)
    client._lock = threading.Lock()
    client._discovered_ports = discovered
    client._replied_ports = set()
    client._reported_ports = set()
    client._state = None
    client._device_type = None
    client._notify_state_update = lambda snapshot: None
    client._device = _Device()
    client._connected = True
    client._client = MagicMock()
    # Serialized getPort queue state. send_port_command returns None here (no
    # broker), so the pump never arms a real timer; these tests only exercise
    # the ports_replied / ports_complete accounting, not the queue.
    client._port_queue = deque()
    client._inflight_port = None
    client._inflight_cid = None
    client._inflight_since = 0.0
    client._port_busy_counts = {}
    client._pump_timer = None
    # The handler re-asks each discovered port; there is no broker here.
    client.send_port_command = lambda *a, **kw: None
    return client


class _Device:
    device_id = "aabb"
    model_name = "HD9280"
    model_number = ""
    friendly_name = "Fryer"


def _get_ports(client, ports):
    client._handle_ncp_response(
        {
            "cn": "getAllPorts",
            "status": 0,
            "data": [{"portName": p, "direction": "read"} for p in ports],
        }
    )


def _port_reply(client, port, status=0, properties=None):
    client._handle_ncp_response(
        {
            "cn": "getPort",
            "status": status,
            "data": {"portName": port, "properties": properties or {}},
        }
    )


def test_no_discovery_yet_is_neither_replied_nor_complete():
    client = _client([])
    assert client.ports_replied is False
    assert client.ports_complete is False


def test_one_port_of_several_is_not_complete():
    """The first port to answer used to be read as the whole state.

    Config replying says nothing about Status, and entity cleanup ran on that
    and pruned entities whose port was still in flight.
    """
    client = _client([])
    _get_ports(client, ["Config", "Status"])
    _port_reply(client, "Config", properties={"x": 1})

    assert client.ports_complete is False
    assert client.ports_replied is False


def test_every_port_reporting_is_complete():
    client = _client([])
    _get_ports(client, ["Config", "Status"])
    _port_reply(client, "Config", properties={"x": 1})
    _port_reply(client, "Status", properties={"temp": 180})

    assert client.ports_replied is True
    assert client.ports_complete is True


def test_a_port_with_nothing_to_report_still_counts():
    """acp_s and recipe_s answer empty when no program or recipe is running.

    Counting only ports that carried properties left the set never complete,
    so the startup wait always timed out and pruning never ran at all.
    """
    client = _client([])
    _get_ports(client, ["Status", "acp_s"])
    _port_reply(client, "Status", properties={"temp": 180})
    _port_reply(client, "acp_s", properties={})

    assert client.ports_replied is True
    assert client.ports_complete is True


def test_a_busy_port_ends_the_wait_but_not_the_pruning_question():
    """Busy means the appliance declined to say, so what it holds is unknown.

    It has replied, so the startup wait can stop; it has not reported, so
    pruning must not run against it.
    """
    client = _client([])
    _get_ports(client, ["Status", "acp_s"])
    _port_reply(client, "Status", properties={"temp": 180})
    _port_reply(client, "acp_s", status=1)  # NCP status 1 = busy

    assert client.ports_replied is True
    assert client.ports_complete is False


def test_a_rediscovery_starts_the_question_over():
    client = _client([])
    _get_ports(client, ["Status"])
    _port_reply(client, "Status", properties={"temp": 180})
    assert client.ports_complete is True

    _get_ports(client, ["Status", "Config"])
    assert client.ports_complete is False
    assert client.ports_replied is False

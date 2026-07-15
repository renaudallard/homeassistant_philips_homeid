"""Tests for knowing when a FUSION device has reported all of its ports."""

import threading

from custom_components.philips_homeid.mqtt_api import PhilipsMQTTClient


def _client(discovered, answered):
    client = PhilipsMQTTClient.__new__(PhilipsMQTTClient)
    client._lock = threading.Lock()
    client._discovered_ports = discovered
    client._answered_ports = set(answered)
    return client


def test_no_discovery_yet_is_not_complete():
    assert _client([], []).ports_complete is False


def test_one_port_of_several_is_not_complete():
    """The first port to answer used to be read as the whole state.

    getAllPorts is followed by one getPort per port and the answers arrive
    separately, so Config replying says nothing about Status. Entity cleanup
    ran on that and pruned entities whose port was still in flight.
    """
    assert _client(["Config", "Status"], ["Config"]).ports_complete is False


def test_every_port_answered_is_complete():
    assert _client(["Config", "Status"], ["Config", "Status"]).ports_complete is True


def test_unexpected_extra_answers_do_not_break_completeness():
    client = _client(["Config"], ["Config", "firmware_s"])
    assert client.ports_complete is True

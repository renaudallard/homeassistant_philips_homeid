"""Tests for the serialized NCP getPort queue.

The MUJI purifier NCP is single-threaded: a burst of getPort reads makes every
request past the first answer busy(1) with an empty data object (so it does not
even name the refused port), and a sustained burst can wedge the NCP until the
appliance is power-cycled. The queue sends one getPort at a time, retries a
busy port a bounded number of times, and tells the device's own unsolicited
status pushes apart from our solicited reply by message type and correlation id.
"""

import threading
from collections import deque
from unittest.mock import MagicMock

from custom_components.philips_homeid.mqtt_api import PhilipsMQTTClient


def _client():
    c = PhilipsMQTTClient.__new__(PhilipsMQTTClient)
    c._lock = threading.Lock()
    c._client = MagicMock()
    c._connected = True
    c._port_queue = deque()
    c._inflight_port = None
    c._inflight_cid = None
    c._inflight_since = 0.0
    c._port_busy_counts = {}
    c._pump_timer = None
    c.sent = []
    c._cid_seq = 0

    def _send(port, command_name="setPort", properties=None):
        c._cid_seq += 1
        cid = f"cid{c._cid_seq}"
        c.sent.append(port)
        return cid

    # Override the bound methods so the queue logic runs without real network
    # I/O or real timers; the timer is driven by calling _pump_port_queue.
    c.send_port_command = _send
    c._arm_pump_timer_locked = MagicMock()
    return c


def _reply(c, status=0):
    """Build the reply the device would send for the in-flight getPort."""
    return {"type": "response", "cid": c._inflight_cid, "status": status}


def test_only_one_getport_in_flight():
    c = _client()
    c._enqueue_port_reads(["A", "B", "C"], new_round=True)
    assert c.sent == ["A"]
    assert c._inflight_port == "A"
    assert list(c._port_queue) == ["B", "C"]


def test_success_advances_to_next_port():
    c = _client()
    c._enqueue_port_reads(["A", "B", "C"], new_round=True)
    c._on_getport_reply(_reply(c, status=0))
    assert c._inflight_port is None
    c._pump_port_queue()  # timer fires
    assert c.sent == ["A", "B"]
    assert c._inflight_port == "B"


def test_busy_port_is_retried():
    c = _client()
    c._enqueue_port_reads(["A"], new_round=True)
    c._on_getport_reply(_reply(c, status=1))  # busy
    assert c._inflight_port is None
    assert list(c._port_queue) == ["A"]  # re-queued
    c._pump_port_queue()
    assert c.sent == ["A", "A"]


def test_busy_gives_up_after_the_limit():
    c = _client()
    c._enqueue_port_reads(["A"], new_round=True)
    # Three busy replies: retry, retry, then defer to the next round.
    for _ in range(3):
        c._on_getport_reply(_reply(c, status=1))
        c._pump_port_queue()
    assert c.sent == ["A", "A", "A"]
    assert list(c._port_queue) == []
    assert c._inflight_port is None


def test_unsolicited_event_does_not_advance_the_queue():
    c = _client()
    c._enqueue_port_reads(["A", "B"], new_round=True)
    # A status EVENT push (typed non-response) must not be taken for our reply.
    c._on_getport_reply({"type": "event", "cid": "other", "status": 0})
    assert c._inflight_port == "A"
    assert list(c._port_queue) == ["B"]
    # A stale reply carrying a mismatched cid is ignored just the same.
    c._on_getport_reply({"type": "response", "cid": "stale", "status": 0})
    assert c._inflight_port == "A"
    assert list(c._port_queue) == ["B"]


def test_cancel_clears_queue_and_inflight():
    c = _client()
    c._enqueue_port_reads(["A", "B", "C"], new_round=True)
    c._cancel_port_queue()
    assert list(c._port_queue) == []
    assert c._inflight_port is None
    assert c._inflight_cid is None


def test_pump_on_empty_queue_is_a_noop():
    c = _client()
    c._pump_port_queue()
    assert c.sent == []


def test_pump_while_disconnected_clears_the_queue():
    c = _client()
    c._port_queue = deque(["A", "B"])
    c._connected = False
    c._pump_port_queue()
    assert c.sent == []
    assert list(c._port_queue) == []
    assert c._inflight_port is None


def test_send_failure_midpump_clears_the_queue():
    # A send that returns None (link dropped mid-pump) must not orphan the
    # remaining queued ports with no timer; the reconnect getAllPorts rebuilds.
    c = _client()
    c._port_queue = deque(["A", "B", "C"])
    c.send_port_command = lambda *a, **kw: None
    c._pump_port_queue()
    assert c._inflight_port is None
    assert list(c._port_queue) == []

"""Tests for the MQTT reconnect loop's handling of a rejected token."""

import threading
from collections import deque
from unittest.mock import MagicMock

from custom_components.philips_homeid.mqtt_api import (
    MqttCredentialsRejected,
    PhilipsMQTTClient,
)


class _NoWaitEvent(threading.Event):
    """A stop event that never actually sleeps, so the backoff runs instantly."""

    def wait(self, timeout=None):
        return super().wait(0)


def _client(credential_refresh):
    client = PhilipsMQTTClient.__new__(PhilipsMQTTClient)
    client._credential_refresh = credential_refresh
    client._stop = _NoWaitEvent()
    client._lock = threading.Lock()
    client._reconnect_lock = threading.Lock()
    client._reconnecting_lock = threading.Lock()
    client._reconnecting = True
    client._connected = False
    client._teardown_client = MagicMock()
    client.connect = MagicMock()
    # Reconnect cancels the getPort queue for the dropped session.
    client._port_queue = deque()
    client._inflight_port = None
    client._inflight_cid = None
    client._pump_timer = None
    return client


def test_rejected_credentials_stop_the_loop():
    """A dead refresh token must not be retried forever.

    Reauth is started by the credential_refresh callable; the loop's job is
    to stop rather than back off against an account that will keep saying no.
    """
    calls = []

    def refresh():
        calls.append(1)
        raise MqttCredentialsRejected("Token refresh rejected: invalid_grant")

    client = _client(refresh)
    client._reconnect_with_backoff()

    assert len(calls) == 1
    client.connect.assert_not_called()
    assert client._reconnecting is False


def test_transient_failure_retries_then_succeeds():
    """An ordinary failure still backs off and retries."""
    calls = []

    def refresh():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("cloud unreachable")
        return "token", "sig"

    client = _client(refresh)
    client._reconnect_with_backoff()

    assert len(calls) == 3
    client.connect.assert_called_once_with("token", "sig")
    assert client._reconnecting is False


def test_only_one_caller_can_claim_the_reconnect():
    """The claim decides who owns the client, so it must be atomic.

    on_disconnect runs on the paho network thread while the proactive
    refresh runs on the event loop. Both reading the flag as False meant two
    reconnect threads, and the loser's finally then cleared a flag the
    winner still held.
    """
    client = _client(lambda: ("t", "s"))
    client._reconnecting = False

    assert client.claim_reconnect() is True
    assert client.claim_reconnect() is False

    client.release_reconnect()
    assert client.claim_reconnect() is True


def test_a_reconnect_thread_leaves_a_restored_link_alone():
    """Another reconnect winning the race must not have its client torn down."""
    client = _client(lambda: ("token", "sig"))
    client._connected = True

    client._reconnect_with_backoff()

    client._teardown_client.assert_not_called()
    client.connect.assert_not_called()

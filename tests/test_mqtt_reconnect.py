"""Tests for the MQTT reconnect loop's handling of a rejected token."""

import threading
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
    client._reconnect_lock = threading.Lock()
    client._reconnecting = True
    client._connected = False
    client._teardown_client = MagicMock()
    client.connect = MagicMock()
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

"""Tests for matching an NCP write to the appliance's reply.

The reply arrives on paho's network thread while the write waits in an
executor thread, so these run the wait on a real thread and answer it from
another once the write is registered.
"""

import asyncio
import json
import logging
import threading
import time

import pytest

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.mqtt_api import (
    FusionDeviceInfo,
    PhilipsMQTTClient,
)


class _FakePaho:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos):
        self.published.append(json.loads(payload))


def _client():
    client = PhilipsMQTTClient(
        FusionDeviceInfo(
            thing_name="da-test",
            device_id="test",
            tenant="da",
            mqtt_host="host",
            platform_rest_url="url",
            model_name="HD9875",
        )
    )
    client._client = _FakePaho()
    client._connected = True
    client._discovered_write_ports = ["Control"]
    return client


def _pending(client, exclude=(), timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with client._lock:
            waiting = [cid for cid in client._pending_writes if cid not in exclude]
        if waiting:
            return waiting[0]
        time.sleep(0.005)
    pytest.fail("no write was registered")


def _reply(client, cid, status, reply_type="response"):
    payload = {"cid": cid, "cn": "setPort", "status": status, "data": {}}
    if reply_type is not None:
        payload["type"] = reply_type
    client._handle_ncp_response(payload)


def _wait_in_thread(client, props, timeout=2.0):
    holder = {}

    def run():
        holder["result"] = client.send_port_command_and_wait(
            "control", "setPort", props, timeout
        )

    thread = threading.Thread(target=run)
    thread.start()
    return thread, holder


def test_an_accepted_write_reports_ok():
    client = _client()
    thread, holder = _wait_in_thread(client, {"status": "cooking"})

    cid = _pending(client)
    _reply(client, cid, 0)
    thread.join(2)

    assert holder["result"] == (True, 0, "ok")
    assert client._client.published[-1]["cid"] == cid
    assert not client._pending_writes


@pytest.mark.parametrize(
    ("status", "name"),
    [(8, "port_error"), (7, "command_name_error")],
)
def test_a_refused_write_reports_the_refusal(status, name):
    client = _client()
    thread, holder = _wait_in_thread(client, {"temp": 200})

    _reply(client, _pending(client), status)
    thread.join(2)

    assert holder["result"] == (False, status, name)
    assert len(client._client.published) == 1


@pytest.fixture
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(
        "custom_components.philips_homeid.mqtt_api._WRITE_BUSY_RETRY_DELAY",
        (0.0, 0.0),
    )


@pytest.mark.usefixtures("no_retry_delay")
def test_a_busy_write_is_sent_again_under_a_new_cid():
    """busy means not now, and the app sends the command again."""
    client = _client()
    thread, holder = _wait_in_thread(client, {"status": "finish"})

    first = _pending(client)
    _reply(client, first, 1, reply_type=None)
    second = _pending(client, exclude=(first,))
    _reply(client, second, 0)
    thread.join(2)

    assert holder["result"] == (True, 0, "ok")
    assert [msg["cid"] for msg in client._client.published] == [first, second]
    assert client._client.published[0]["data"] == client._client.published[1]["data"]


@pytest.mark.usefixtures("no_retry_delay")
def test_a_write_still_busy_after_three_retries_reports_busy():
    client = _client()
    thread, holder = _wait_in_thread(client, {"status": "finish"})

    sent: list[str] = []
    for _ in range(4):
        sent.append(_pending(client, exclude=sent))
        _reply(client, sent[-1], 1)
    thread.join(2)

    assert holder["result"] == (False, 1, "busy")
    assert [msg["cid"] for msg in client._client.published] == sent


@pytest.mark.usefixtures("no_retry_delay")
def test_a_busy_write_is_not_sent_again_once_disconnected():
    client = _client()
    client._stop.set()
    thread, holder = _wait_in_thread(client, {"status": "finish"})

    _reply(client, _pending(client), 1)
    thread.join(2)

    assert holder["result"] == (False, 1, "busy")
    assert len(client._client.published) == 1


def test_no_reply_times_out_and_cleans_up():
    client = _client()

    result = client.send_port_command_and_wait("control", "setPort", {}, 0.05)

    assert result == (False, None, "timeout")
    assert not client._pending_writes


def test_nothing_is_awaited_while_disconnected():
    client = _client()
    client._connected = False

    result = client.send_port_command_and_wait("control", "setPort", {}, 1.0)

    assert result == (False, None, "not_connected")
    assert not client._pending_writes


def test_a_status_push_does_not_release_a_write():
    """The appliance's own pushes carry cid "0" and type event."""
    client = _client()
    thread, holder = _wait_in_thread(client, {"status": "cooking"}, timeout=0.2)
    _pending(client)

    _reply(client, "0", 0, reply_type="event")
    thread.join(2)

    assert holder["result"] == (False, None, "timeout")


def test_each_write_is_released_by_its_own_reply():
    client = _client()
    first, first_result = _wait_in_thread(client, {"status": "pause"})
    first_cid = _pending(client)
    second, second_result = _wait_in_thread(client, {"status": "mainmenu"})
    second_cid = _pending(client, exclude=(first_cid,))

    _reply(client, second_cid, 0)
    second.join(2)
    assert second_result["result"] == (True, 0, "ok")
    assert "result" not in first_result

    _reply(client, first_cid, 8)
    first.join(2)
    assert first_result["result"] == (False, 8, "port_error")


class _Hass:
    async def async_add_executor_job(self, func, *args):
        return await asyncio.get_running_loop().run_in_executor(None, func, *args)


def _coordinator(client):
    coordinator = PhilipsHomeIDCoordinator.__new__(PhilipsHomeIDCoordinator)
    coordinator.hass = _Hass()
    coordinator.mqtt_client = client
    return coordinator


def _answer_when_sent(client, status):
    threading.Thread(target=lambda: _reply(client, _pending(client), status)).start()


@pytest.mark.asyncio
async def test_the_coordinator_reports_an_accepted_write():
    client = _client()
    _answer_when_sent(client, 0)

    assert await _coordinator(client)._mqtt_command("control", {"temp": 200})


@pytest.mark.asyncio
async def test_the_coordinator_reports_and_logs_a_refused_write(caplog):
    """A refusal used to come back as success, and was nowhere in the log."""
    client = _client()
    _answer_when_sent(client, 8)

    with caplog.at_level(logging.WARNING):
        accepted = await _coordinator(client)._mqtt_command(
            "control", {"status": "cooking", "preheat": True}
        )

    assert accepted is False
    assert (
        "NCP port_error (8) for setPort to control: "
        "{'status': 'cooking', 'preheat': True}" in caplog.text
    )


@pytest.mark.asyncio
async def test_the_coordinator_does_not_count_a_lost_reply_as_a_refusal(
    caplog, monkeypatch
):
    """from_ncp is subscribed at QoS 0, so a write can go through and its
    reply still be lost. Only an actual refusal, or nothing sent, is False.
    """
    monkeypatch.setattr(
        "custom_components.philips_homeid.coordinator._WRITE_REPLY_TIMEOUT", 0.05
    )

    with caplog.at_level(logging.DEBUG):
        sent = await _coordinator(_client())._mqtt_command("control", {"temp": 1})

    assert sent is True
    assert "No reply to setPort to control" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_the_coordinator_reports_a_write_it_could_not_send():
    client = _client()
    client._connected = False

    assert await _coordinator(client)._mqtt_command("control", {"temp": 1}) is False

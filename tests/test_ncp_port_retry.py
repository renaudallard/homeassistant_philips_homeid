"""Tests for re-asking a FUSION appliance for its NCP ports.

An appliance that parks its NCP answers no getAllPorts: the link is up and the
publish is acknowledged, but nothing comes back, and every entity is built
from that answer. The heartbeat carries the retry, but a device that pushes
more often than the heartbeat interval keeps the heartbeat from ever running,
so the push path has to carry it too.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.philips_homeid.const import FUSION_HEARTBEAT_INTERVAL
from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.mqtt_api import PhilipsMQTTClient


def _coordinator(discovered=False, connected=True):
    coordinator = PhilipsHomeIDCoordinator.__new__(PhilipsHomeIDCoordinator)
    coordinator._is_fusion = True
    coordinator._last_port_request = time.monotonic() - FUSION_HEARTBEAT_INTERVAL - 1
    coordinator.hass = MagicMock()
    # The send itself is a tracked task; closing the coroutine keeps the test
    # synchronous without leaving it un-awaited.
    coordinator._create_tracked_task = MagicMock(side_effect=lambda coro: coro.close())
    mqtt = MagicMock()
    mqtt.ports_discovered = discovered
    mqtt.connected = connected
    coordinator.mqtt_client = mqtt
    return coordinator


def _asked(coordinator):
    return coordinator._create_tracked_task.call_count


def test_a_silent_appliance_is_asked_again():
    coordinator = _coordinator()
    coordinator._maybe_request_ncp_ports()
    assert _asked(coordinator) == 1


def test_the_retry_is_paced():
    """A chatty appliance pushes many times a minute.

    Asking on every push would be a getAllPorts storm at exactly the moment
    the appliance is already refusing to answer.
    """
    coordinator = _coordinator()
    coordinator._maybe_request_ncp_ports()
    coordinator._maybe_request_ncp_ports()
    assert _asked(coordinator) == 1


def test_an_appliance_that_named_its_ports_is_left_alone():
    coordinator = _coordinator(discovered=True)
    coordinator._maybe_request_ncp_ports()
    assert _asked(coordinator) == 0


def test_a_dropped_link_is_not_asked():
    coordinator = _coordinator(connected=False)
    coordinator._maybe_request_ncp_ports()
    assert _asked(coordinator) == 0


@pytest.mark.asyncio
async def test_the_request_reaches_the_client():
    coordinator = _coordinator()
    coordinator.hass.async_add_executor_job = AsyncMock()

    await coordinator._request_ncp_ports()

    coordinator.hass.async_add_executor_job.assert_awaited_once_with(
        coordinator.mqtt_client.refresh_port_data
    )


def test_ports_discovered_follows_the_getallports_answer():
    client = PhilipsMQTTClient.__new__(PhilipsMQTTClient)
    client._discovered_ports = []
    assert client.ports_discovered is False

    client._discovered_ports = ["Status", "Config"]
    assert client.ports_discovered is True

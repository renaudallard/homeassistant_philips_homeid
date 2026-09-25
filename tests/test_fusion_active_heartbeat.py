"""Tests for the FUSION heartbeat while an airfryer is cooking.

An HD9875 on FUSION does not always push the end of a cook. On 2026-09-24 the
countdown reached zero at 05:24:15, the device then pushed nothing, and
'finish' only arrived with the next heartbeat at 05:29:05. Every push restarts
the heartbeat timer, so that lag is up to a full FUSION_HEARTBEAT_INTERVAL.
The heartbeat therefore runs faster while the airfryer is in an active status.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.philips_homeid.const import (
    FUSION_ACTIVE_HEARTBEAT_INTERVAL,
    FUSION_HEARTBEAT_INTERVAL,
)
from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator


def _coordinator():
    coordinator = PhilipsHomeIDCoordinator.__new__(PhilipsHomeIDCoordinator)
    coordinator._is_fusion = True
    coordinator.config_entry = MagicMock(options={})
    coordinator.update_interval = timedelta(seconds=FUSION_HEARTBEAT_INTERVAL)
    return coordinator


def _state(status):
    state = MagicMock()
    state.properties = {"airfryer": {"status": status}}
    return state


def test_a_cooking_airfryer_gets_the_fast_heartbeat():
    coordinator = _coordinator()
    coordinator._update_polling_interval(_state("cooking"))
    assert coordinator.update_interval == timedelta(
        seconds=FUSION_ACTIVE_HEARTBEAT_INTERVAL
    )


def test_the_fast_heartbeat_is_shorter_than_the_idle_one():
    assert FUSION_ACTIVE_HEARTBEAT_INTERVAL < FUSION_HEARTBEAT_INTERVAL


def test_finish_drops_back_to_the_idle_heartbeat():
    coordinator = _coordinator()
    coordinator._update_polling_interval(_state("cooking"))
    coordinator._update_polling_interval(_state("finish"))
    assert coordinator.update_interval == timedelta(seconds=FUSION_HEARTBEAT_INTERVAL)


def test_a_device_without_an_airfryer_keeps_the_idle_heartbeat():
    coordinator = _coordinator()
    state = MagicMock()
    state.properties = {}
    coordinator._update_polling_interval(state)
    assert coordinator.update_interval == timedelta(seconds=FUSION_HEARTBEAT_INTERVAL)


def test_the_local_scan_options_do_not_apply_to_fusion():
    """The local-API active scan is user-tunable down to seconds; FUSION's is not."""
    coordinator = _coordinator()
    coordinator.config_entry = MagicMock(options={"active_scan_interval": 3})
    coordinator._update_polling_interval(_state("cooking"))
    assert coordinator.update_interval == timedelta(
        seconds=FUSION_ACTIVE_HEARTBEAT_INTERVAL
    )


def _heartbeat_ready(coordinator, connected):
    coordinator.hass = MagicMock(async_add_executor_job=AsyncMock())
    coordinator.mqtt_client = MagicMock(connected=connected, _connect_time=0.0)
    coordinator.mqtt_client.needs_token_refresh.return_value = False
    coordinator._maybe_fetch_ota_jobs = MagicMock()
    coordinator._state = _state("cooking")
    coordinator._update_polling_interval(coordinator._state)
    return coordinator


@pytest.mark.asyncio
async def test_a_lost_link_drops_back_to_the_idle_heartbeat():
    """With the link down no push can end the cook, so 20 s only repeats a warning."""
    coordinator = _heartbeat_ready(_coordinator(), connected=False)
    await coordinator._async_update_data_fusion()
    assert coordinator.update_interval == timedelta(seconds=FUSION_HEARTBEAT_INTERVAL)


@pytest.mark.asyncio
async def test_a_connected_heartbeat_keeps_the_fast_interval():
    coordinator = _heartbeat_ready(_coordinator(), connected=True)
    await coordinator._async_update_data_fusion()
    assert coordinator.update_interval == timedelta(
        seconds=FUSION_ACTIVE_HEARTBEAT_INTERVAL
    )

"""Tests for polling a device that has stopped answering.

A poll reads the ports one at a time, so an appliance that is off or whose
web server has wedged used to cost a full request timeout on every one of
them: seven timeouts a cycle for an airfryer, and eleven while the airfryer
port is still unknown. They all sit behind the same socket, so the first
unanswered read is the answer for the rest of the cycle.
"""

from unittest.mock import AsyncMock

import pytest

from custom_components.philips_homeid.local_api import PhilipsLocalAPI
from custom_components.philips_homeid.local_models import LocalDeviceInfo


def _api(answers):
    """Build a client whose reads answer on the named ports and nowhere else.

    A port that is not named goes unanswered, which is what a timeout or a
    dropped connection leaves behind.
    """
    api = PhilipsLocalAPI()
    ports = []

    async def request(device, port_name, *args, **kwargs):
        ports.append(port_name)
        api._no_response = port_name not in answers
        return answers.get(port_name)

    api._request = AsyncMock(side_effect=request)
    return api, ports


def _device(**kwargs):
    return LocalDeviceInfo(
        ip_address="192.0.2.10",
        cpp_id="",
        client_id="id",
        client_secret="secret",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_an_unanswered_read_ends_the_cycle():
    """One silent port is enough to know the rest are silent too."""
    api, ports = _api({})

    state = await api.get_full_state(_device(airfryer_port="airfryer"))

    assert state is None
    assert ports == ["airfryer"]


@pytest.mark.asyncio
async def test_the_port_probe_stops_at_the_first_silence():
    """An unknown airfryer port used to be probed five times over."""
    api, ports = _api({})

    state = await api.get_full_state(_device())

    assert state is None
    assert ports == ["airfryer"]


@pytest.mark.asyncio
async def test_a_refused_port_is_not_a_silent_device():
    """A device that answers on one port has the others read as before."""
    api, ports = _api({"status": {"pwr": "1"}, "firmware": {"version": "1.6.0"}})

    state = await api.get_full_state(_device(airfryer_port=False))

    assert state is not None
    assert state.properties["pwr"] == "1"
    assert ports == [
        "status",
        "air",
        "fltsts",
        "machinestatus",
        "configuration",
        "firmware",
    ]


@pytest.mark.asyncio
async def test_a_skipped_block_does_not_inherit_the_last_failure():
    """The first read of a cycle must happen whatever the last one did.

    An air purifier skips the airfryer block outright, so a flag left set by
    the previous cycle would end this one before it asked anything, and the
    device would never be polled again.
    """
    api, ports = _api({"status": {"pwr": "1"}})
    api._no_response = True

    state = await api.get_full_state(_device(airfryer_port=False))

    assert state is not None
    assert ports[0] == "status"

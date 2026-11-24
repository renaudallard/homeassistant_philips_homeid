"""Tests for which protocol probe_device picks."""

import asyncio

import pytest

from custom_components.philips_homeid.local_api import PhilipsLocalAPI


def _api(spec):
    """Fake each protocol's probe reply: {use_https: (delay, status)}.

    A status of None means no HTTP reply at all.
    """
    api = PhilipsLocalAPI()

    async def probe_request(device, port_name, product_id=None):
        delay, status = spec[device.use_https]
        await asyncio.sleep(delay)
        return None, status

    api._probe_request = probe_request
    return api


@pytest.mark.asyncio
async def test_https_wins_over_a_faster_http_404():
    """A UPnP server on port 80 answers 404 before HTTPS finishes its
    handshake. Taking the first answer saved the device as plain HTTP."""
    api = _api({False: (0, 404), True: (0.05, 401)})

    device = await api.probe_device("192.0.2.10")

    assert device is not None
    assert device.use_https is True


@pytest.mark.asyncio
async def test_http_is_used_when_https_does_not_answer():
    api = _api({False: (0, 401), True: (0.05, None)})

    device = await api.probe_device("192.0.2.10")

    assert device is not None
    assert device.use_https is False


@pytest.mark.asyncio
async def test_nothing_found_when_neither_answers():
    api = _api({False: (0, None), True: (0, None)})

    assert await api.probe_device("192.0.2.10") is None

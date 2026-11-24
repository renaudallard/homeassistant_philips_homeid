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


@pytest.mark.asyncio
async def test_https_answer_does_not_wait_for_a_silent_http():
    """A port 80 that accepts and never replies must not hold up a device
    already found on HTTPS. Waiting for both cost about 20 s here."""
    api = PhilipsLocalAPI()
    http_started = asyncio.Event()
    http_cancelled = asyncio.Event()

    async def probe_request(device, port_name, product_id=None):
        if device.use_https:
            await http_started.wait()
            return None, 401
        http_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            http_cancelled.set()
            raise
        return None, None

    api._probe_request = probe_request

    device = await asyncio.wait_for(api.probe_device("192.0.2.10"), 5)

    assert device is not None
    assert device.use_https is True
    assert http_cancelled.is_set()

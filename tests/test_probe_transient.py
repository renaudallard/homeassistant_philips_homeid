"""Tests for what counts as a transient probe failure."""

import pytest

from custom_components.philips_homeid.local_api import PhilipsLocalAPI
from custom_components.philips_homeid.local_models import LocalDeviceInfo


class _Response:
    def __init__(self, status, headers=None, body=""):
        self.status = status
        self.headers = headers or {}
        self._body = body

    async def text(self):
        return self._body


def _api():
    api = PhilipsLocalAPI()
    api._probe_transient = False
    return api


def _device():
    return LocalDeviceInfo(ip_address="192.0.2.10", cpp_id="aabb")


@pytest.mark.asyncio
async def test_unauthorized_is_transient():
    """A 401 must not let the caller conclude the port is absent.

    get_full_state caches airfryer_port/purifier_port/espresso_port as False
    when a probe comes back empty and nothing transient happened, and that
    sentinel is never cleared. A refused request says nothing about which
    ports the device has.
    """
    api = _api()
    result, should_retry = await api._handle_response(_device(), _Response(401))

    assert result is None
    assert should_retry is False
    assert api._probe_transient is True


@pytest.mark.asyncio
async def test_unauthorized_with_a_challenge_is_transient_too():
    """The retry path leaves the flag set, since the retry may fail as well."""
    api = _api()
    device = _device()
    device.client_id = "cid"
    device.client_secret = "secret"
    resp = _Response(401, {"WWW-Authenticate": 'PhilipsCondor nonce="abc"'})

    await api._handle_response(device, resp)

    assert api._probe_transient is True


@pytest.mark.asyncio
async def test_not_implemented_is_not_transient():
    """A 501 is the device saying the port does not exist, which is cacheable."""
    api = _api()
    result, should_retry = await api._handle_response(_device(), _Response(501))

    assert result is None
    assert should_retry is False
    assert api._probe_transient is False


@pytest.mark.asyncio
async def test_busy_is_transient():
    """The pre-existing 429 behaviour is unchanged."""
    api = _api()
    await api._handle_response(_device(), _Response(429))

    assert api._probe_transient is True

"""Tests for reporting the result of a Venus mid-cook settings write."""

import pytest

from custom_components.philips_homeid.local_api import PhilipsLocalAPI
from custom_components.philips_homeid.local_models import PORT_VENUSAF, LocalDeviceInfo


def _api(responses):
    """Build an API whose _request replays a canned answer per PUT payload."""
    api = PhilipsLocalAPI()
    sent = []

    async def _request(device, port, method="GET", data=None, **kwargs):
        sent.append(data)
        return responses(data)

    api._request = _request
    api._sent = sent
    return api


def _device():
    device = LocalDeviceInfo(ip_address="192.0.2.10", cpp_id="aabb")
    device.airfryer_port = PORT_VENUSAF
    return device


def _is_status(data, status):
    return data.get("status") == status


@pytest.mark.asyncio
async def test_rejected_settings_write_is_reported_as_failure():
    """A settings write the appliance refuses must not look like success.

    The result was bound in a finally block, so it described the resume and
    the caller saw True. The number entity then reported success and snapped
    back to the old value with nothing logged.
    """
    api = _api(lambda data: None if "temp" in data else {"ok": True})

    result = await api.airfryer_update_settings(_device(), temp=180, is_cooking=True)

    assert result is False


@pytest.mark.asyncio
async def test_successful_settings_write_is_reported_as_success():
    api = _api(lambda data: {"ok": True})

    result = await api.airfryer_update_settings(_device(), temp=180, is_cooking=True)

    assert result is True


@pytest.mark.asyncio
async def test_the_cook_is_resumed_even_when_the_settings_write_fails():
    """Leaving the appliance paused would be worse than the failed write."""
    api = _api(lambda data: None if "temp" in data else {"ok": True})

    await api.airfryer_update_settings(_device(), temp=180, is_cooking=True)

    assert _is_status(api._sent[-1], "cooking")


@pytest.mark.asyncio
async def test_a_failed_resume_is_reported_as_failure():
    api = _api(lambda data: None if _is_status(data, "cooking") else {"ok": True})

    result = await api.airfryer_update_settings(_device(), temp=180, is_cooking=True)

    assert result is False

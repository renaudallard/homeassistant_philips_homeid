"""Tests for how the IoT device-list call classifies HTTP error codes.

A 403 from the IoT API is the AWS gateway refusing the OIDC token, which is a
permanent authorization denial. Reporting it as a retryable connection error
made the config flow tell the user to "wait a moment and try again", which
never helps. It has to be an auth error instead.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.philips_homeid.cloud_api import PhilipsCloudAPI
from custom_components.philips_homeid.cloud_auth import (
    CloudAuthError,
    CloudConnectionError,
)


def _api_returning(status, body="{}"):
    """Build an API whose next request yields the given status and body."""
    api = PhilipsCloudAPI()
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=body)

    @asynccontextmanager
    async def _get(*_args, **_kwargs):
        yield resp

    session = MagicMock()
    session.get = _get
    api._get_session = AsyncMock(return_value=session)
    return api


@pytest.mark.asyncio
async def test_device_list_403_is_auth_not_transient():
    """A 403 must be an auth error, never a retryable connection error."""
    api = _api_returning(403, '{"Message": "not authorized"}')
    with pytest.raises(CloudAuthError) as exc:
        await api.get_devices("token")
    assert not isinstance(exc.value, CloudConnectionError)


@pytest.mark.asyncio
async def test_device_list_401_is_auth():
    api = _api_returning(401)
    with pytest.raises(CloudAuthError) as exc:
        await api.get_devices("token")
    assert not isinstance(exc.value, CloudConnectionError)


@pytest.mark.asyncio
async def test_device_list_500_stays_a_connection_error():
    """A 5xx is a genuine bad moment and stays retryable."""
    api = _api_returning(500)
    with pytest.raises(CloudConnectionError):
        await api.get_devices("token")

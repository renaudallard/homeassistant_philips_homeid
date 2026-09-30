"""Tests for listing Air+ purifiers next to HomeID appliances at cloud login.

The Air+ registry only shows a device to the OAuth client it was paired with,
so an account holding a HomeID appliance and an Air+ purifier needs both
lookups. The Air+ one used to run only when HomeID found nothing, which left
the purifier out of reach on such accounts.
"""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.philips_homeid.config_flow import PhilipsHomeIDConfigFlow
from custom_components.philips_homeid.const import (
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_OAUTH_CLIENT,
    OAUTH_CLIENT_AIRPLUS,
)

HOMEID_TOKENS = {"access_token": "homeid-at", "refresh_token": "homeid-rt"}
AIRPLUS_TOKENS = {"access_token": "airplus-at", "refresh_token": "airplus-rt"}
APPLIANCE = {
    "name": "Airfryer",
    "macAddress": "aa:bb:cc:dd:ee:ff",
    "clientId": "cid",
    "clientSecret": "secret",
}
PURIFIER = {"name": "Purifier", "ctn": "AC0651/10", "uuid": "1234"}


def _flow(airplus_devices):
    flow = PhilipsHomeIDConfigFlow()
    api = MagicMock()
    api.verify_otp = AsyncMock(return_value="sess-token")

    async def _tokens(_session, client="homeid"):
        return AIRPLUS_TOKENS if client == OAUTH_CLIENT_AIRPLUS else HOMEID_TOKENS

    api.get_oidc_tokens = AsyncMock(side_effect=_tokens)
    api.get_appliances_via_homeid = AsyncMock(return_value=[APPLIANCE])
    api.get_devices = AsyncMock(return_value=airplus_devices)
    api.close = AsyncMock()
    flow._cloud_api = api
    flow._cloud_email = "user@example.com"
    return flow, api


@pytest.mark.asyncio
async def test_both_lists_are_offered():
    flow, _api = _flow([PURIFIER])

    result = await flow.async_step_cloud_otp({"code": "123456"})

    assert result["step_id"] == "cloud_devices"
    assert flow._cloud_devices == [
        ("homeid", APPLIANCE),
        (OAUTH_CLIENT_AIRPLUS, PURIFIER),
    ]
    labels = result["data_schema"].schema["device"].container
    assert labels == {
        "0": "Airfryer (aa:bb:cc:dd:ee:ff)",
        "1": "Purifier (AC0651/10) [Air+]",
    }
    # The HomeID tokens stay in place for the HomeID appliance.
    assert flow._cloud_tokens == HOMEID_TOKENS
    assert flow._airplus_tokens == AIRPLUS_TOKENS


@pytest.mark.asyncio
async def test_air_plus_entry_stores_the_air_plus_token():
    flow, _api = _flow([PURIFIER])
    flow._set_unique_id_or_abort = AsyncMock()
    flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)

    await flow.async_step_cloud_otp({"code": "123456"})
    result = await flow.async_step_cloud_devices({"device": "1"})

    assert result["data"][CONF_CLOUD_REFRESH_TOKEN] == "airplus-rt"
    assert result["data"][CONF_OAUTH_CLIENT] == OAUTH_CLIENT_AIRPLUS


@pytest.mark.asyncio
async def test_air_plus_failure_keeps_the_homeid_devices():
    flow, api = _flow([])
    api.get_devices = AsyncMock(side_effect=aiohttp.ClientError("reset"))

    result = await flow.async_step_cloud_otp({"code": "123456"})

    assert result["step_id"] == "cloud_devices"
    assert flow._cloud_devices == [("homeid", APPLIANCE)]


@pytest.mark.asyncio
async def test_air_plus_only_account_still_works():
    flow, api = _flow([PURIFIER])
    api.get_appliances_via_homeid = AsyncMock(return_value=[])
    api.get_user_profile = AsyncMock(return_value={"id": "user"})
    api.get_homes = AsyncMock(return_value=[])
    # The IoT registry with the HomeID token finds nothing, Air+ finds the
    # purifier.
    api.get_devices = AsyncMock(side_effect=[[], [PURIFIER]])

    result = await flow.async_step_cloud_otp({"code": "123456"})

    assert result["step_id"] == "cloud_devices"
    assert flow._cloud_devices == [(OAUTH_CLIENT_AIRPLUS, PURIFIER)]


@pytest.mark.asyncio
async def test_local_discovery_skips_air_plus():
    flow, api = _flow([PURIFIER])
    flow._discovered_device = MagicMock()

    await flow.async_step_cloud_otp({"code": "123456"})

    assert flow._cloud_devices == [("homeid", APPLIANCE)]
    api.get_devices.assert_not_called()

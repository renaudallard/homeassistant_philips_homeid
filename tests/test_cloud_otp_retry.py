"""Tests for retrying the cloud OTP step after a post-verify failure."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.philips_homeid.cloud_auth import CloudConnectionError
from custom_components.philips_homeid.config_flow import PhilipsHomeIDConfigFlow


def _flow(get_oidc_tokens):
    flow = PhilipsHomeIDConfigFlow()
    api = MagicMock()
    api.verify_otp = AsyncMock(return_value="sess-token")
    api.get_oidc_tokens = get_oidc_tokens
    flow._cloud_api = api
    flow._cloud_email = "user@example.com"
    return flow, api


@pytest.mark.asyncio
async def test_transient_failure_after_verify_keeps_the_step_usable():
    """A hiccup after the code verifies must not strand the step.

    Closing the cloud API left _cloud_api None, so the next submission fell
    through to "missing_code" for a code the user did type, with no way
    forward but to restart the whole flow.
    """
    flow, api = _flow(AsyncMock(side_effect=CloudConnectionError("boom")))

    result = await flow.async_step_cloud_otp({"code": "123456"})

    assert result["type"] == "form"
    assert result["errors"] == {"base": "cloud_unreachable"}
    assert flow._cloud_api is not None


@pytest.mark.asyncio
async def test_retry_does_not_reverify_the_spent_code():
    """The retry resumes from the session token, not from the used-up OTP."""
    flow, api = _flow(AsyncMock(side_effect=CloudConnectionError("boom")))

    await flow.async_step_cloud_otp({"code": "123456"})
    result = await flow.async_step_cloud_otp({"code": "123456"})

    assert api.verify_otp.call_count == 1
    assert api.get_oidc_tokens.call_count == 2
    assert result["errors"] == {"base": "cloud_unreachable"}


@pytest.mark.asyncio
async def test_retry_without_a_code_still_resumes():
    """Once the code is spent the form no longer needs it to carry on."""
    flow, api = _flow(AsyncMock(side_effect=CloudConnectionError("boom")))

    await flow.async_step_cloud_otp({"code": "123456"})
    result = await flow.async_step_cloud_otp({"code": ""})

    assert result["errors"] == {"base": "cloud_unreachable"}
    assert api.get_oidc_tokens.call_count == 2


@pytest.mark.asyncio
async def test_missing_code_is_still_reported_before_any_verify():
    """An empty first submission is still a missing code."""
    flow, _api = _flow(AsyncMock())

    result = await flow.async_step_cloud_otp({"code": ""})

    assert result["errors"] == {"base": "missing_code"}


def test_abandoned_flow_closes_the_cloud_session():
    """Dropping the dialog mid-login must not leak the aiohttp session."""
    flow, api = _flow(AsyncMock())
    api.close = AsyncMock()
    created = []
    flow.hass = MagicMock()
    flow.hass.async_create_task = lambda coro: created.append(coro)

    flow.async_remove()

    assert flow._cloud_api is None
    assert len(created) == 1
    # Close the coroutine we intercepted so it isn't reported as un-awaited.
    created[0].close()


def test_remove_without_a_cloud_session_is_a_no_op():
    """A local-only flow has nothing to close."""
    flow, _api = _flow(AsyncMock())
    flow._cloud_api = None
    flow.hass = MagicMock()

    flow.async_remove()

    flow.hass.async_create_task.assert_not_called()

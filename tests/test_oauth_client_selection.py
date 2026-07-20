"""The OAuth client selection must send the right client id, redirect and scope.

A purifier paired in the Philips Air+ app is registered against the Air+ OAuth
client, so tokens for it must be minted and refreshed with that client id;
picking the HomeID client would miss the device or have the refresh refused.
Both clients are public and use the same pure-HTTP prompt=none PKCE flow, so
neither sends a client secret. See issue #33.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.philips_homeid.cloud_auth import PhilipsCloudAuth
from custom_components.philips_homeid.const import (
    AIRPLUS_CLIENT_ID,
    AIRPLUS_REDIRECT_URI,
    MOBILE_APP_REDIRECT_URI,
    OAUTH_CLIENT_AIRPLUS,
    OAUTH_CLIENT_ID,
)


def _api_capturing_post(captured):
    """Build an auth client whose next POST is captured and answered 200."""
    api = PhilipsCloudAuth()
    tokens = {"access_token": "at", "refresh_token": "rt"}
    resp = MagicMock()
    resp.status = 200
    resp.text = AsyncMock(return_value='{"access_token": "at", "refresh_token": "rt"}')
    resp.json = AsyncMock(return_value=tokens)

    @asynccontextmanager
    async def _post(url, data=None, **_kwargs):
        captured["url"] = url
        captured["data"] = data
        yield resp

    session = MagicMock()
    session.post = _post
    api._get_session = AsyncMock(return_value=session)
    return api


@pytest.mark.asyncio
async def test_refresh_defaults_to_homeid_client():
    """The default client is the HomeID one, sent without a secret."""
    captured = {}
    api = _api_capturing_post(captured)
    await api.refresh_tokens("old-rt")
    assert captured["data"]["client_id"] == OAUTH_CLIENT_ID
    assert "client_secret" not in captured["data"]


@pytest.mark.asyncio
async def test_refresh_uses_airplus_client():
    """The Air+ client id is used when selected, still without a secret."""
    captured = {}
    api = _api_capturing_post(captured)
    await api.refresh_tokens("old-rt", OAUTH_CLIENT_AIRPLUS)
    assert captured["data"]["client_id"] == AIRPLUS_CLIENT_ID
    assert "client_secret" not in captured["data"]


@pytest.mark.asyncio
async def test_code_exchange_uses_airplus_client_and_redirect():
    """The Air+ code exchange uses the Air+ client id and redirect, no secret."""
    captured = {}
    api = _api_capturing_post(captured)
    await api._exchange_code("code", "verifier", OAUTH_CLIENT_AIRPLUS)
    assert captured["data"]["client_id"] == AIRPLUS_CLIENT_ID
    assert captured["data"]["redirect_uri"] == AIRPLUS_REDIRECT_URI
    assert captured["data"]["code_verifier"] == "verifier"
    assert "client_secret" not in captured["data"]


@pytest.mark.asyncio
async def test_code_exchange_defaults_to_homeid_client():
    """The HomeID code exchange uses the HomeID client id and redirect."""
    captured = {}
    api = _api_capturing_post(captured)
    await api._exchange_code("code", "verifier")
    assert captured["data"]["client_id"] == OAUTH_CLIENT_ID
    assert captured["data"]["redirect_uri"] == MOBILE_APP_REDIRECT_URI
    assert "client_secret" not in captured["data"]

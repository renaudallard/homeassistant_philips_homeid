"""Tests that a rotated cloud refresh token survives an unload."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import custom_components.philips_homeid.cloud_api as cloud_api_mod
from custom_components.philips_homeid.const import CONF_CLOUD_REFRESH_TOKEN
from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator


@pytest.fixture
def rotating_cloud(monkeypatch):
    """A cloud that rotates the refresh token, slowly enough to be cancelled."""

    class FakeAPI:
        async def refresh_tokens(self, refresh_token, client="homeid"):
            await asyncio.sleep(0.05)
            return {"access_token": "at", "refresh_token": "NEW"}

        async def close(self):
            return None

    monkeypatch.setattr(cloud_api_mod, "PhilipsCloudAPI", FakeAPI)


def _coordinator(persisted):
    coordinator = PhilipsHomeIDCoordinator.__new__(PhilipsHomeIDCoordinator)
    coordinator._token_lock = asyncio.Lock()
    coordinator.config_entry = SimpleNamespace(data={CONF_CLOUD_REFRESH_TOKEN: "OLD"})
    coordinator._background_tasks = set()
    coordinator.hass = MagicMock()
    coordinator.hass.config_entries.async_update_entry = lambda entry, data: (
        persisted.update(data)
    )
    coordinator.hass.async_create_task = lambda coro: (
        asyncio.get_running_loop().create_task(coro)
    )
    return coordinator


@pytest.mark.asyncio
async def test_a_rotated_token_is_kept_when_unload_cancels_the_refresh(rotating_cloud):
    """Unload cancels the cloud fetches, and the cloud retires the token it
    replaces. A cancel landing between the response and the write would leave
    the entry holding a dead token, and the user would have to log in again.
    """
    persisted = {}
    coordinator = _coordinator(persisted)

    task = coordinator._create_tracked_task(coordinator._get_access_token())
    await asyncio.sleep(0.01)
    coordinator.cancel_background_tasks()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.15)

    assert persisted.get(CONF_CLOUD_REFRESH_TOKEN) == "NEW"


@pytest.mark.asyncio
async def test_an_uninterrupted_refresh_still_persists_and_returns(rotating_cloud):
    persisted = {}
    coordinator = _coordinator(persisted)

    access_token = await coordinator._get_access_token()

    assert access_token == "at"
    assert persisted.get(CONF_CLOUD_REFRESH_TOKEN) == "NEW"


@pytest.mark.asyncio
async def test_no_refresh_token_returns_none(rotating_cloud):
    coordinator = _coordinator({})
    coordinator.config_entry = SimpleNamespace(data={})

    assert await coordinator._get_access_token() is None

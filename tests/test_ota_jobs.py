"""Tests for the cloud firmware job check.

An empty job list is the cloud saying "up to date". That makes every failure
mode dangerous: a refusal, a 404 or a body we cannot parse must raise, because
returning an empty list would quietly report a pending update as done.
"""

import time
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.philips_homeid.cloud_api import PhilipsCloudAPI
from custom_components.philips_homeid.cloud_auth import (
    CloudAuthError,
    CloudConnectionError,
)
from custom_components.philips_homeid.const import (
    CLOUD_FETCH_RETRY_DELAY,
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_DEVICE_ID,
    OTA_JOBS_POLL_INTERVAL,
)
from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator


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
async def test_empty_job_list_is_a_real_answer():
    """An empty list means up to date and must not raise."""
    api = _api_returning(200, "[]")
    assert await api.get_device_jobs("token", "dev1") == []


@pytest.mark.asyncio
async def test_job_list_is_returned():
    """A queued job comes back as given."""
    api = _api_returning(200, '[{"jobId": "j1", "queuedAt": "2026-08-01"}]')
    jobs = await api.get_device_jobs("token", "dev1")
    assert len(jobs) == 1
    assert jobs[0]["jobId"] == "j1"


@pytest.mark.asyncio
async def test_wrapped_job_list_is_unwrapped():
    """A dict envelope around the list is accepted."""
    api = _api_returning(200, '{"jobs": [{"jobId": "j1"}]}')
    assert await api.get_device_jobs("token", "dev1") == [{"jobId": "j1"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_refusal_is_auth_not_transient(status):
    """A refused token is an auth error, never an empty job list."""
    api = _api_returning(status, '{"message": "denied"}')
    with pytest.raises(CloudAuthError) as exc:
        await api.get_device_jobs("token", "dev1")
    assert not isinstance(exc.value, CloudConnectionError)


@pytest.mark.asyncio
async def test_404_is_never_an_empty_list():
    """A wrong device id must not read as "no updates pending"."""
    api = _api_returning(404, '{"message": "Not Found"}')
    with pytest.raises(CloudConnectionError):
        await api.get_device_jobs("token", "dev1")


@pytest.mark.asyncio
async def test_500_is_a_connection_error():
    """A backend fault is retryable, not an answer."""
    api = _api_returning(500, '{"message": "Internal Server Error"}')
    with pytest.raises(CloudConnectionError):
        await api.get_device_jobs("token", "dev1")


@pytest.mark.asyncio
async def test_non_json_body_raises():
    """An HTML error page is not an empty job list."""
    api = _api_returning(200, "<html>gateway</html>")
    with pytest.raises(CloudConnectionError):
        await api.get_device_jobs("token", "dev1")


@pytest.mark.asyncio
async def test_unexpected_shape_raises():
    """A dict with no list in it is not an answer either."""
    api = _api_returning(200, '{"message": "ok"}')
    with pytest.raises(CloudConnectionError):
        await api.get_device_jobs("token", "dev1")


class _Coordinator:
    """Minimal stand-in exercising the real gate and fetch logic."""

    _maybe_fetch_ota_jobs = PhilipsHomeIDCoordinator._maybe_fetch_ota_jobs
    _poll_ota_jobs = PhilipsHomeIDCoordinator._poll_ota_jobs
    ota_update_available = PhilipsHomeIDCoordinator.ota_update_available

    def __init__(self, is_fusion=True, device_id="dev1", refresh_token="rt"):
        self._is_fusion = is_fusion
        self._state = SimpleNamespace(properties={})
        self._ota_jobs_pending = None
        self._ota_jobs_fetch_running = False
        self._ota_jobs_next_fetch = 0.0
        self.config_entry = SimpleNamespace(
            data={CONF_DEVICE_ID: device_id, CONF_CLOUD_REFRESH_TOKEN: refresh_token},
            async_start_reauth=MagicMock(),
        )
        self.spawned = []
        self.notified = []
        self.published = 0

    def _create_tracked_task(self, coro):
        self.spawned.append(coro)
        return coro

    def _notify_new_properties(self, props):
        self.notified.append(props)

    def async_set_updated_data(self, _state):
        self.published += 1

    async def _get_access_token(self):
        return "token"


def _close_spawned(coordinator):
    """Close coroutines the gate created but the test never awaited."""
    for coro in coordinator.spawned:
        coro.close()
    coordinator.spawned.clear()


@contextmanager
def _cloud_returning(jobs=None, error=None):
    """Patch the cloud client the fetch imports so it yields jobs or raises."""
    api = MagicMock()
    api.close = AsyncMock()
    api.get_device_jobs = AsyncMock(
        return_value=jobs if jobs is not None else [], side_effect=error
    )
    with patch(
        "custom_components.philips_homeid.cloud_api.PhilipsCloudAPI",
        return_value=api,
    ):
        yield api


def test_local_device_never_polls_jobs():
    """A local device has no cloud job list and must not reach for one."""
    coordinator = _Coordinator(is_fusion=False)
    coordinator._maybe_fetch_ota_jobs()
    assert coordinator.spawned == []
    assert coordinator._ota_jobs_fetch_running is False


def test_missing_device_id_never_polls():
    """Without a DA device id the URL would be wrong, so do not ask."""
    coordinator = _Coordinator(device_id="")
    coordinator._maybe_fetch_ota_jobs()
    assert coordinator.spawned == []
    assert coordinator._ota_jobs_fetch_running is False


def test_missing_refresh_token_never_polls():
    """Without cloud credentials there is nothing to authenticate with."""
    coordinator = _Coordinator(refresh_token="")
    coordinator._maybe_fetch_ota_jobs()
    assert coordinator.spawned == []
    assert coordinator._ota_jobs_fetch_running is False


def test_interval_gate_holds():
    """Pushes arriving before the interval elapses spawn nothing."""
    coordinator = _Coordinator()
    coordinator._ota_jobs_next_fetch = time.monotonic() + 3600
    coordinator._maybe_fetch_ota_jobs()
    coordinator._maybe_fetch_ota_jobs()
    assert coordinator.spawned == []

    coordinator._ota_jobs_next_fetch = 0.0
    coordinator._maybe_fetch_ota_jobs()
    assert len(coordinator.spawned) == 1
    _close_spawned(coordinator)


def test_in_flight_guard_holds():
    """A second push while a check is running spawns nothing."""
    coordinator = _Coordinator()
    coordinator._ota_jobs_fetch_running = True
    coordinator._maybe_fetch_ota_jobs()
    assert coordinator.spawned == []


@pytest.mark.asyncio
async def test_first_answer_announces_and_publishes():
    """The first answer creates the entity and pushes it to HA once."""
    coordinator = _Coordinator()
    with _cloud_returning(jobs=[{"jobId": "j1"}]):
        await coordinator._poll_ota_jobs()
    assert coordinator.ota_update_available is True
    assert coordinator.notified == [[("firmware_update_available", None)]]
    assert coordinator.published == 1
    assert coordinator._ota_jobs_fetch_running is False


@pytest.mark.asyncio
async def test_unchanged_answer_does_not_publish():
    """A repeat answer must not restart the heartbeat timer."""
    coordinator = _Coordinator()
    with _cloud_returning(jobs=[]):
        await coordinator._poll_ota_jobs()
        coordinator._ota_jobs_next_fetch = 0.0
        await coordinator._poll_ota_jobs()
    assert coordinator.ota_update_available is False
    assert len(coordinator.notified) == 1
    assert coordinator.published == 1


@pytest.mark.asyncio
async def test_failure_leaves_state_unknown():
    """A failed check reads as unknown, never as up to date."""
    coordinator = _Coordinator()
    before = time.monotonic()
    with _cloud_returning(error=CloudConnectionError("boom")):
        await coordinator._poll_ota_jobs()
    assert coordinator.ota_update_available is None
    assert coordinator.notified == []
    assert coordinator.published == 0
    assert coordinator._ota_jobs_next_fetch >= before + CLOUD_FETCH_RETRY_DELAY
    assert coordinator._ota_jobs_fetch_running is False


@pytest.mark.asyncio
async def test_auth_failure_does_not_start_reauth():
    """The MQTT credential path owns reauth; a side fetch just waits."""
    coordinator = _Coordinator()
    before = time.monotonic()
    with _cloud_returning(error=CloudAuthError("denied")):
        await coordinator._poll_ota_jobs()
    assert coordinator.ota_update_available is None
    coordinator.config_entry.async_start_reauth.assert_not_called()
    assert coordinator._ota_jobs_next_fetch >= before + OTA_JOBS_POLL_INTERVAL
    assert coordinator._ota_jobs_fetch_running is False


@pytest.mark.asyncio
async def test_unexpected_error_is_swallowed():
    """Nothing from this fetch may reach the coordinator's update path."""
    coordinator = _Coordinator()
    with _cloud_returning(error=RuntimeError("unexpected")):
        await coordinator._poll_ota_jobs()
    assert coordinator.ota_update_available is None
    assert coordinator._ota_jobs_fetch_running is False

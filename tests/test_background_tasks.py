"""Tests for cancelling the coordinator's background tasks on unload."""

import asyncio

import pytest

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator


def _coordinator():
    coordinator = PhilipsHomeIDCoordinator.__new__(PhilipsHomeIDCoordinator)
    coordinator._background_tasks = set()

    class _Hass:
        @staticmethod
        def async_create_task(coro):
            return asyncio.get_running_loop().create_task(coro)

    coordinator.hass = _Hass()
    return coordinator


@pytest.mark.asyncio
async def test_unload_cancels_a_task_still_in_flight():
    """A cloud fetch left running would write to a half-unloaded entry.

    It would also refresh the cloud token under the old coordinator's lock
    while the reloaded one refreshes under its own, which the shared lock
    exists to prevent.
    """
    coordinator = _coordinator()
    started = asyncio.Event()

    async def _slow():
        started.set()
        await asyncio.sleep(30)

    task = coordinator._create_tracked_task(_slow())
    await started.wait()

    coordinator.cancel_background_tasks()
    await asyncio.sleep(0)

    assert task.cancelled() or task.cancelling()


@pytest.mark.asyncio
async def test_finished_tasks_are_forgotten():
    """The set must not grow for the life of the entry."""
    coordinator = _coordinator()

    async def _quick():
        return None

    task = coordinator._create_tracked_task(_quick())
    await task

    assert coordinator._background_tasks == set()


@pytest.mark.asyncio
async def test_cancelling_with_nothing_running_is_a_no_op():
    coordinator = _coordinator()
    coordinator.cancel_background_tasks()
    assert coordinator._background_tasks == set()

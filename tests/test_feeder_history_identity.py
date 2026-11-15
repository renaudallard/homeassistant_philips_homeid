"""Events belong to the feeder by deviceId, as in the HomeID app."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.philips_homeid.feeder_meals import MealManager

START = datetime(2026, 11, 13, tzinfo=UTC)
END = START + timedelta(days=1)


def manager(*rows):
    request = AsyncMock(return_value={"item": list(rows)})
    return MealManager(request, "home", "philips-id", "UTC"), request


def row(event_id="event", **changes):
    return {
        "id": event_id,
        "type": "meal_dispensed",
        "deviceId": "philips-id",
        "time": START.isoformat(),
        **changes,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other_ids",
    [
        {},
        {"deviceVendorId": "tuya-id"},
        {"deviceExternalId": "tuya-id"},
        {"deviceExternalId": None},
        {"deviceExternalId": "something-else"},
    ],
)
async def test_own_device_id_is_enough_whatever_the_other_ids(other_ids):
    api, _ = manager(row(**other_ids))
    assert [event.id for event in await api.history(START, END)] == ["event"]


@pytest.mark.asyncio
@pytest.mark.parametrize("device_id", ["other", None, "", 5])
async def test_other_or_missing_device_ids_are_skipped_not_fatal(device_id):
    api, _ = manager(row("theirs", deviceId=device_id), row("ours"))
    assert [event.id for event in await api.history(START, END)] == ["ours"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "time", [None, 5, "garbage", "2026-11-13T10:00:00", "2026-11-13"]
)
async def test_unusable_event_time_drops_only_that_event(time):
    api, _ = manager(row("bad", time=time), row("good"))
    assert [event.id for event in await api.history(START, END)] == ["good"]
    status, _ = manager(
        row("bad", type="device_online", time=time),
        row("good", type="device_offline"),
    )
    assert await status.latest_status(START, END) == {"device_offline": START}


@pytest.mark.asyncio
async def test_events_are_requested_for_this_device_only():
    api, request = manager()
    await api.history(START, END)
    assert ("deviceIds", "philips-id") in request.await_args.kwargs["params"]

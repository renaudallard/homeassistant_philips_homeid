"""Event-derived feeder status and the desiccant bag timer."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.philips_homeid.feeder_api import (
    FeederDevice,
    FeederError,
    FeederHTTPError,
)
from custom_components.philips_homeid.feeder_meals import (
    STATUS_EVENT_TYPES,
    DesiccantTimer,
    MealManager,
    MealsError,
)

START = datetime(2026, 11, 7, tzinfo=UTC)
END = START + timedelta(days=7)


def manager(request):
    return MealManager.for_device(
        request,
        FeederDevice("tuya-vendor", "internal", "home", "PAW5320", "Cat", None),
    )


def event(kind, time, **changes):
    return {
        "id": f"{kind}-{time}",
        "type": kind,
        "time": time,
        "deviceId": "internal",
        **changes,
    }


@pytest.mark.asyncio
async def test_latest_status_per_type_with_exact_query():
    rows = [
        event("food_level_low", "2026-11-08T08:00:00Z"),
        event("food_level_low", "2026-11-10T08:00:00+02:00"),
        event("device_offline", "2026-11-09T00:00:00Z"),
        event("device_online", "2026-11-09T00:05:00Z"),
        event("food_outlet_stuck", "2026-11-01T00:00:00Z"),  # outside window
        event("meal_dispensed", "2026-11-09T00:00:00Z"),  # not a status type
        event("motion_detected", "2026-11-09T00:00:00Z", deviceId=None),
        event("food_level_low", "2026-11-13T00:00:00Z", deviceId="other"),
        event("device_online", "2026-11-13T00:00:00Z", deviceId=None),
    ]
    request = AsyncMock(return_value={"item": rows})
    result = await manager(request).latest_status(START, END)
    assert result == {
        "food_level_low": datetime(2026, 11, 10, 6, tzinfo=UTC),
        "device_offline": datetime(2026, 11, 9, tzinfo=UTC),
        "device_online": datetime(2026, 11, 9, 0, 5, tzinfo=UTC),
    }
    request.assert_awaited_once_with(
        "GET",
        "/api/homes/home/events",
        params=[
            ("from", START.isoformat()),
            ("to", END.isoformat()),
            *(("types", kind) for kind in STATUS_EVENT_TYPES),
            ("deviceIds", "internal"),
            ("clustered", "false"),
        ],
    )


@pytest.mark.asyncio
async def test_no_status_events_is_empty_not_healthy():
    request = AsyncMock(return_value={"item": []})
    assert await manager(request).latest_status(START, END) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [{"item": None}, {"item": [1]}, "x"],
)
async def test_bad_status_responses_are_errors(bad):
    request = AsyncMock(return_value=bad if isinstance(bad, dict) else {"item": bad})
    with pytest.raises(FeederError):
        await manager(request).latest_status(START, END)


@pytest.mark.asyncio
async def test_status_window_is_bounded_before_requesting():
    request = AsyncMock()
    with pytest.raises(MealsError):
        await manager(request).latest_status(START, START + timedelta(days=32))
    request.assert_not_called()


@pytest.mark.asyncio
async def test_desiccant_reads_the_timer_of_the_internal_device_id():
    request = AsyncMock(
        return_value={"expiryDate": "2026-12-01", "lastReplacedDate": "2026-11-01"}
    )
    assert await manager(request).desiccant() == DesiccantTimer(
        date(2026, 12, 1), date(2026, 11, 1)
    )
    request.assert_awaited_once_with(
        "GET", "/api/homes/home/devices/internal/desiccant-timer"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timer", "expected"),
    [
        ({}, DesiccantTimer(None, None)),
        (
            {"expiryDate": None, "lastReplacedDate": "2026-11-01"},
            DesiccantTimer(None, None),
        ),
        ({"expiryDate": "2026-12-01T10:00:00Z"}, DesiccantTimer(None, None)),
        ({"expiryDate": "2026-13-01"}, DesiccantTimer(None, None)),
        ({"expiryDate": 1764583200}, DesiccantTimer(None, None)),
        ({"expiryDate": "2026-12-01"}, DesiccantTimer(date(2026, 12, 1), None)),
        (
            {"expiryDate": "2026-12-01", "lastReplacedDate": "yesterday"},
            DesiccantTimer(date(2026, 12, 1), None),
        ),
    ],
)
async def test_unusable_desiccant_dates_are_unknown_like_the_app(timer, expected):
    assert await manager(AsyncMock(return_value=timer)).desiccant() == expected


@pytest.mark.asyncio
async def test_desiccant_404_means_no_timer():
    request = AsyncMock(side_effect=FeederHTTPError(404))
    assert await manager(request).desiccant() == DesiccantTimer(None, None)


@pytest.mark.asyncio
async def test_desiccant_other_http_errors_propagate():
    request = AsyncMock(side_effect=FeederHTTPError(500))
    with pytest.raises(FeederHTTPError):
        await manager(request).desiccant()


@pytest.mark.asyncio
@pytest.mark.parametrize("timer", [None, [], "2026-12-01"])
async def test_unsupported_desiccant_responses_are_errors(timer):
    with pytest.raises(MealsError):
        await manager(AsyncMock(return_value=timer)).desiccant()

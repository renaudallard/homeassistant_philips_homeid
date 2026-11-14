"""Event-derived feeder status and the device settings' replacement timer."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.philips_homeid.feeder_api import FeederDevice
from custom_components.philips_homeid.feeder_meals import (
    STATUS_EVENT_TYPES,
    DesiccantTimer,
    EventOwnershipError,
    MealManager,
    MealsError,
)

START = datetime(2026, 11, 7, tzinfo=UTC)
END = START + timedelta(days=7)


def manager(request, external_id=None):
    return MealManager.for_device(
        request,
        FeederDevice(
            "tuya-vendor", "internal", "home", "PAW5320", "Cat", external_id, None
        ),
    )


def event(kind, time, **changes):
    return {
        "id": f"{kind}-{time}",
        "type": kind,
        "time": time,
        "deviceId": "internal",
        "deviceExternalId": "tuya-vendor",
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
        event(
            "food_level_low",
            "2026-11-13T00:00:00Z",
            deviceId="other",
            deviceExternalId="other-vendor",
        ),
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
        params={
            "from": START.isoformat(),
            "to": END.isoformat(),
            "types": ",".join(STATUS_EVENT_TYPES),
            "clustered": "false",
        },
    )


@pytest.mark.asyncio
async def test_internal_id_without_the_vendor_pair_is_unavailable():
    row = event("device_online", "2026-11-09T00:00:00Z", deviceExternalId=None)
    request = AsyncMock(return_value={"item": [row]})
    with pytest.raises(EventOwnershipError):
        await manager(request).latest_status(START, END)


@pytest.mark.asyncio
async def test_no_status_events_is_empty_not_healthy():
    request = AsyncMock(return_value={"item": []})
    assert await manager(request).latest_status(START, END) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        event("device_online", "2026-11-09T00:00:00Z", deviceId=None),
        event("device_online", "2026-11-09T00:00:00Z", deviceExternalId="other"),
    ],
)
async def test_unverifiable_own_status_event_makes_status_unavailable(row):
    request = AsyncMock(return_value={"item": [row]})
    with pytest.raises(EventOwnershipError):
        await manager(request).latest_status(START, END)


@pytest.mark.asyncio
async def test_status_vendor_pair_never_overrides_known_external_id():
    row = event("device_online", "2026-11-09T00:00:00Z")
    request = AsyncMock(return_value={"item": [row]})
    with pytest.raises(EventOwnershipError):
        await manager(request, external_id="registry-ext").latest_status(START, END)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"item": None},
        [event("device_online", "2026-11-09T00:00:00")],  # naive timestamp
        [event("device_online", 5)],
    ],
)
async def test_bad_status_responses_are_errors(bad):
    request = AsyncMock(return_value=bad if isinstance(bad, dict) else {"item": bad})
    with pytest.raises(MealsError):
        await manager(request).latest_status(START, END)


@pytest.mark.asyncio
async def test_status_window_is_bounded_before_requesting():
    request = AsyncMock()
    with pytest.raises(MealsError):
        await manager(request).latest_status(START, START + timedelta(days=32))
    request.assert_not_called()


@pytest.mark.asyncio
async def test_desiccant_reads_settings_of_the_internal_device_id():
    request = AsyncMock(
        return_value={
            "filter_replacement_time": {
                "type": "feeder",
                "value": "2026-12-01T10:00:00Z",
                "format": "datetime",
            },
            "filter_application_time": {"value": "2026-11-01T10:00:00+01:00"},
            "feeder_voice_audio_id": {"audioId": "ignored", "url": "ignored"},
        }
    )
    assert await manager(request).desiccant() == DesiccantTimer(
        datetime(2026, 12, 1, 10, tzinfo=UTC), datetime(2026, 11, 1, 9, tzinfo=UTC)
    )
    request.assert_awaited_once_with("GET", "/api/homes/home/devices/internal/settings")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [{}, {"filter_replacement_time": None}, {"filter_replacement_time": {}}],
)
async def test_missing_desiccant_timer_is_unknown(settings):
    timer = await manager(AsyncMock(return_value=settings)).desiccant()
    assert timer == DesiccantTimer(None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        None,
        [],
        {"filter_replacement_time": "2026-12-01T10:00:00Z"},
        {"filter_replacement_time": {"value": "2026-12-01T10:00:00"}},
        {"filter_replacement_time": {"value": 1764583200}},
    ],
)
async def test_unsupported_settings_are_errors_not_unknown(settings):
    with pytest.raises(MealsError):
        await manager(AsyncMock(return_value=settings)).desiccant()

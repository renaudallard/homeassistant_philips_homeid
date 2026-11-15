"""Offline protocol, ownership and timezone tests for HomeID feeder meals."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.philips_homeid.feeder_api import FeederError
from custom_components.philips_homeid.feeder_meals import (
    FeedingEvent,
    Meal,
    MealManager,
    MealsError,
    occurrences,
)


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def meal_row(**changes):
    return {
        "id": "meal-1",
        "deviceId": "internal",
        "name": "Breakfast",
        "portionAmount": 2,
        "feedTime": "08:30",
        "repeatDays": [1, 3, 7],
        "enabled": True,
        "url": "https://invalid.example/never-follow",
        **changes,
    }


def event_row(**changes):
    return {
        "id": "event-1",
        "deviceId": "internal",
        "deviceExternalId": "external",
        "type": "meal_dispensed",
        "time": "2026-01-05T08:30:00Z",
        "mealName": "Breakfast",
        "mealAmount": 2.5,
        "source": "schedule",
        "url": "https://invalid.example/never-follow",
        "metadata": {"ignored": "not returned"},
        **changes,
    }


def manager(request):
    return MealManager(
        request,
        "home /?#",
        "internal",
        "tuya-vendor",
        "Europe/Riga",
        external_device_id="external",
    )


def schedule(**changes):
    return replace(Meal("meal-1", "Breakfast", 2, "08:30", (1, 3, 7), True), **changes)


@pytest.mark.asyncio
async def test_list_exact_device_preserves_disabled_empty_and_aliases():
    request = AsyncMock(
        return_value={
            "item": [
                meal_row(enabled=False, repeatDays=[]),
                meal_row(
                    id="alias",
                    portionAmount=None,
                    amount=3,
                    feedTime=None,
                    time="06:45Z",
                ),
                meal_row(
                    id="foreign-1", deviceId="internal-extra", feedTime="unsupported"
                ),
                meal_row(id="foreign-2", deviceId="external"),
            ]
        }
    )
    meals = await manager(request).list_meals()
    assert meals == [
        schedule(enabled=False, repeat_days=()),
        schedule(id="alias", portions=3, time="06:45Z"),
    ]
    request.assert_awaited_once_with("GET", "/api/homes/home%20%2F%3F%23/meals")
    assert set(asdict(meals[0])) == {
        "id",
        "name",
        "portions",
        "time",
        "repeat_days",
        "enabled",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, None, {"item": {}}, {"item": [None]}])
async def test_bad_list_envelope_is_not_empty_success(response):
    with pytest.raises(FeederError):
        await manager(AsyncMock(return_value=response)).list_meals()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"feedTime": {"hour": 8}},
        {"feedTime": "8:30"},
        {"feedTime": "24:00"},
        {"feedTime": "12:00:00"},
        {"feedTime": "12:00+03:00"},
        {"repeatDays": None},
        {"repeatDays": [1, 1]},
        {"repeatDays": [True]},
        {"repeatDays": [0]},
        {"repeatDays": [8]},
        {"portionAmount": True},
        {"portionAmount": 2.0},
        {"portionAmount": 0},
        {"portionAmount": 21},
        {"name": ""},
        {"name": "x" * 81},
        {"enabled": 1},
        {"id": None},
        {"deviceId": None},
    ],
)
async def test_unsupported_selected_meal_fails_entire_read(changes):
    request = AsyncMock(return_value={"item": [meal_row(), meal_row(**changes)]})
    with pytest.raises(FeederError):
        await manager(request).list_meals()
    assert request.await_count == 1


@pytest.mark.asyncio
async def test_duplicate_meal_identity_rejected():
    with pytest.raises(MealsError, match="Duplicate"):
        await manager(AsyncMock(return_value=[meal_row(), meal_row()])).list_meals()


@pytest.mark.asyncio
async def test_conflicting_meal_owners_fail_the_read():
    request = AsyncMock(return_value=[meal_row(), meal_row(deviceId="foreign")])
    with pytest.raises(MealsError, match="Conflicting meal ownership"):
        await manager(request).list_meals()
    assert request.await_count == 1


@pytest.mark.asyncio
async def test_history_scope_dedup_sort_fields_and_neutral_amount():
    first = event_row(id="first", time="2026-01-05T09:00:00+03:00", mealAmount=None)
    request = AsyncMock(
        return_value={
            "item": [
                event_row(),
                first,
                event_row(),
                event_row(id="external-only", deviceId=None, mealAmount=0),
                event_row(id="internal-only", deviceExternalId=None),
                event_row(id="foreign", deviceId="other", deviceExternalId="other"),
                event_row(id="other-type", type="meal_upcoming"),
                event_row(id="at-end", time="2026-01-06T00:00:00Z"),
                event_row(id="before", time="2026-01-04T23:59:59Z"),
            ]
        }
    )
    start, end = utc("2026-01-05"), utc("2026-01-06")
    events = await manager(request).history(start, end)
    assert [event.id for event in events] == [
        "first",
        "event-1",
        "external-only",
        "internal-only",
    ]
    assert events[0] == FeedingEvent(
        "first", utc("2026-01-05T06:00"), "Breakfast", None, "schedule"
    )
    assert events[1].amount == 2.5
    assert events[2].amount == 0
    assert all(event.time.tzinfo is UTC for event in events)
    assert set(asdict(events[0])) == {"id", "time", "name", "amount", "source"}
    request.assert_awaited_once_with(
        "GET",
        "/api/homes/home%20%2F%3F%23/events",
        params={
            "from": start.isoformat(),
            "to": end.isoformat(),
            "types": "meal_dispensed",
            "clustered": "false",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registry_external,internal,external,accepted",
    [
        ("external", "internal", "external", True),
        ("external", "internal", None, True),
        ("external", None, "external", True),
        ("external", "foreign", "external", "unverifiable"),
        ("external", "internal", "tuya-vendor", "unverifiable"),
        ("external", "tuya-vendor", "external", "unverifiable"),
        ("external", "external", "external", "unverifiable"),
        ("external", None, "internal", False),
        ("external", None, "tuya-vendor", False),
        ("external", None, None, "unverifiable"),
        # The Philips ID alone is not the verified pair.
        (None, "internal", None, "unverifiable"),
        (None, "internal", "external", "unverifiable"),
        (None, "internal", "tuya-vendor", True),
        (None, None, "external", "unverifiable"),
        (None, None, "tuya-vendor", "unverifiable"),
        (None, None, None, "unverifiable"),
    ],
)
async def test_history_registry_external_id_is_distinct_from_tuya_vendor_id(
    registry_external, internal, external, accepted
):
    request = AsyncMock(
        return_value=[event_row(deviceId=internal, deviceExternalId=external)]
    )
    api = MealManager(
        request,
        "home",
        "internal",
        "tuya-vendor",
        None,
        external_device_id=registry_external,
    )
    if accepted == "unverifiable":
        with pytest.raises(MealsError, match="ownership"):
            await api.history(utc("2026-01-05"), utc("2026-01-06"))
    else:
        events = await api.history(utc("2026-01-05"), utc("2026-01-06"))
        assert bool(events) is accepted


@pytest.mark.asyncio
async def test_unknown_registry_external_id_does_not_return_partial_history():
    request = AsyncMock(
        return_value=[
            event_row(id="internal-only", deviceExternalId=None),
            event_row(id="unverified-external"),
            event_row(id="vendor-is-not-external", deviceExternalId="tuya-vendor"),
        ]
    )
    api = MealManager(request, "home", "internal", "tuya-vendor", None)
    with pytest.raises(MealsError, match="ownership"):
        await api.history(utc("2026-01-05"), utc("2026-01-06"))


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", [None, "", "invalid/zone", "localtime"])
async def test_history_and_meal_reads_work_without_a_resolvable_home_zone(zone):
    request = AsyncMock(
        side_effect=[
            [event_row(deviceExternalId="tuya-vendor")],
            [meal_row(), meal_row(id="meal-2", feedTime="08:30Z")],
        ]
    )
    api = MealManager(request, "home", "internal", "tuya-vendor", zone)
    assert len(await api.history(utc("2026-01-05"), utc("2026-01-06"))) == 1
    assert [meal.time for meal in await api.list_meals()] == ["08:30", "08:30Z"]
    assert all(call.args[0] == "GET" for call in request.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"time": None},
        {"time": "bad"},
        {"time": "2026-01-05T08:30:00"},
        {"id": ""},
        {"mealAmount": "2"},
        {"mealAmount": True},
        {"mealAmount": -1},
        {"mealAmount": float("nan")},
        {"mealAmount": float("inf")},
        {"source": {}},
        {"mealName": []},
    ],
)
async def test_bad_selected_history_data_never_manufactured(changes):
    with pytest.raises(FeederError):
        await manager(AsyncMock(return_value=[event_row(**changes)])).history(
            utc("2026-01-05"), utc("2026-01-06")
        )


@pytest.mark.asyncio
async def test_history_conflicting_duplicate_id_is_error():
    request = AsyncMock(return_value=[event_row(), event_row(mealAmount=4)])
    with pytest.raises(MealsError, match="Conflicting"):
        await manager(request).history(utc("2026-01-05"), utc("2026-01-06"))


@pytest.mark.asyncio
async def test_legitimate_empty_history_and_missing_optional_values():
    request = AsyncMock(
        side_effect=[
            {"item": []},
            [event_row(mealName=None, mealAmount=None, source=None)],
        ]
    )
    api = manager(request)
    start, end = utc("2026-01-05"), utc("2026-01-06")
    assert await api.history(start, end) == []
    events = await api.history(start, end)
    assert (events[0].name, events[0].amount, events[0].source) == ("", None, "")
    assert await api.history(start, start) == []
    assert request.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start,end",
    [
        (utc("2026-01-01").replace(tzinfo=None), utc("2026-01-02")),
        (utc("2026-01-02"), utc("2026-01-01")),
        (utc("2026-01-01"), utc("2026-02-01") + timedelta(microseconds=1)),
    ],
)
async def test_history_invalid_bounds_do_not_request(start, end):
    request = AsyncMock()
    with pytest.raises(MealsError):
        await manager(request).history(start, end)
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_max_range_uses_elapsed_utc_time():
    request = AsyncMock(return_value=[])
    start = utc("2026-01-01").astimezone(ZoneInfo("Pacific/Auckland"))
    end = utc("2026-02-01").astimezone(ZoneInfo("America/New_York"))
    assert await manager(request).history(start, end) == []
    assert request.await_count == 1


def test_occurrences_local_date_and_utc_weekday_across_query_dates():
    start = datetime.fromisoformat("2026-01-04T17:00:00-10:00")
    end = start + timedelta(days=1)
    result = occurrences(
        [
            schedule(id="utc", time="23:30Z", repeat_days=(1,)),
            schedule(id="local", time="00:30", repeat_days=(2,)),
            schedule(id="wrong-weekday", time="23:30Z", repeat_days=(7,)),
        ],
        start,
        end,
        "Europe/Riga",
    )
    assert [(item.meal_id, item.start) for item in result] == [
        ("local", utc("2026-01-05T22:30")),
        ("utc", utc("2026-01-05T23:30")),
    ]
    assert all(item.end - item.start == timedelta(minutes=1) for item in result)


def test_occurrences_dst_gap_skipped_fold_first_instant_once():
    meal = schedule(time="03:30", repeat_days=(7,))
    assert (
        occurrences([meal], utc("2026-03-29"), utc("2026-03-30"), "Europe/Riga") == []
    )
    result = occurrences([meal], utc("2026-10-25"), utc("2026-10-26"), "Europe/Riga")
    assert [item.start for item in result] == [utc("2026-10-25T00:30")]
    # UTC schedules are unaffected by the home zone's skipped/repeated hour.
    result = occurrences(
        [replace(meal, time="03:30Z")],
        utc("2026-03-29"),
        utc("2026-03-30"),
        "Europe/Riga",
    )
    assert result[0].start == utc("2026-03-29T03:30")


def test_occurrences_bounds_weekdays_disabled_empty_and_sorting():
    meals = [
        schedule(id="z", time="08:30Z", repeat_days=(1,)),
        schedule(id="a", time="08:30Z", repeat_days=(1,)),
        schedule(id="off", enabled=False),
        schedule(id="none", repeat_days=()),
    ]
    start = utc("2026-01-05T08:30")
    result = occurrences(meals, start, start + timedelta(days=8), "UTC")
    assert [item.meal_id for item in result] == ["a", "z", "a", "z"]
    assert result[0].start == start
    assert result[2].start == start + timedelta(days=7)
    assert occurrences(meals, start - timedelta(minutes=1), start, "UTC") == []
    # A meal in progress overlaps the range and is returned; one that ended
    # exactly at the range start is not.
    in_progress = occurrences(
        meals, start + timedelta(seconds=1), start + timedelta(minutes=1), "UTC"
    )
    assert [item.meal_id for item in in_progress] == ["a", "z"]
    assert (
        occurrences(
            meals, start + timedelta(minutes=1), start + timedelta(minutes=2), "UTC"
        )
        == []
    )
    assert occurrences(meals, start, start, "UTC") == []


@pytest.mark.parametrize(
    "zone", [None, "", "invalid/zone", "/etc/localtime", "localtime"]
)
def test_home_zone_required_for_local_occurrences_only(zone):
    with pytest.raises(MealsError):
        occurrences([schedule()], utc("2026-01-05"), utc("2026-01-06"), zone)
    result = occurrences(
        [schedule(time="08:30Z")], utc("2026-01-05"), utc("2026-01-06"), zone
    )
    assert result[0].start == utc("2026-01-05T08:30")


def test_occurrences_limit_is_366_days_and_requires_aware_ordered_bounds():
    start = utc("2026-01-01")
    assert occurrences([], start, start + timedelta(days=366), "UTC") == []
    for first, last in [
        (start, start + timedelta(days=366, microseconds=1)),
        (start.replace(tzinfo=None), start),
        (start, start - timedelta(seconds=1)),
    ]:
        with pytest.raises(MealsError):
            occurrences([], first, last, "UTC")


@pytest.mark.asyncio
async def test_validation_errors_sanitized_and_get_failure_not_retried():
    marker = "synthetic-sensitive-response-marker"
    request = AsyncMock(return_value=[meal_row(feedTime=marker)])
    with pytest.raises(MealsError) as caught:
        await manager(request).list_meals()
    assert marker not in str(caught.value)
    assert caught.value.__cause__ is None
    request = AsyncMock(side_effect=TimeoutError("synthetic timeout"))
    with pytest.raises(TimeoutError):
        await manager(request).list_meals()
    assert request.await_count == 1

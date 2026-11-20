"""Events are read one UTC day per request, and ended days are kept a while."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.philips_homeid import feeder_meals
from custom_components.philips_homeid.feeder_api import FeederError
from custom_components.philips_homeid.feeder_meals import (
    STATUS_EVENT_TYPES,
    MealManager,
    latest_status,
)

DAY = datetime(2026, 11, 12, tzinfo=UTC)


def row(event_id, time, kind="meal_dispensed"):
    return {"id": event_id, "type": kind, "time": time.isoformat(), "deviceId": "dev"}


def serving(rows):
    """A request answering each day with the rows that fall in it."""

    async def request(method, path, *, params):
        start = datetime.fromisoformat(dict(params)["from"])
        end = datetime.fromisoformat(dict(params)["to"])
        return {"item": [r for r in rows if start <= _time(r) <= end]}

    return AsyncMock(side_effect=request)


def _time(event):
    return datetime.fromisoformat(event["time"])


def days(request):
    return [dict(call.kwargs["params"])["from"] for call in request.await_args_list]


@pytest.mark.asyncio
async def test_a_range_is_read_one_utc_day_at_a_time():
    midnight = DAY + timedelta(days=1)
    rows = [
        row("before", DAY + timedelta(hours=10)),
        row("midnight", midnight),  # in both days' answers here
        row("inside", DAY + timedelta(days=1, hours=12)),
        row("after", DAY + timedelta(days=2, hours=7)),
    ]
    request = serving(rows)
    api = MealManager(request, "home", "dev", None)
    start, end = DAY + timedelta(hours=15), DAY + timedelta(days=2, hours=6)
    events = await api.history(start, end)
    assert [event.id for event in events] == ["midnight", "inside"]
    assert days(request) == [(DAY + timedelta(days=n)).isoformat() for n in range(3)]
    for call in request.await_args_list:
        params = dict(call.kwargs["params"])
        start_of_day = datetime.fromisoformat(params["from"])
        assert params["to"] == (start_of_day + timedelta(days=1)).isoformat()


@pytest.mark.asyncio
async def test_ended_days_are_kept():
    request = serving(
        [
            row("a", DAY + timedelta(hours=1)),
            row("b", DAY + timedelta(hours=2)),
            row("c", DAY + timedelta(days=1, hours=3)),
            row("d", DAY + timedelta(days=1, hours=4)),
        ]
    )
    api = MealManager(request, "home", "dev", None)
    first = await api.history(DAY, DAY + timedelta(days=3))
    assert [event.id for event in first] == ["a", "b", "c", "d"]
    assert request.await_count == 3
    assert await api.history(DAY, DAY + timedelta(days=3)) == first
    assert request.await_count == 3


@pytest.mark.asyncio
async def test_a_day_not_yet_ended_is_read_every_time(monkeypatch):
    today = DAY + timedelta(days=1)
    monkeypatch.setattr(feeder_meals, "_utcnow", lambda: today + timedelta(hours=12))
    request = serving([])
    api = MealManager(request, "home", "dev", None)
    await api.history(DAY, today + timedelta(hours=1))
    await api.history(DAY, today + timedelta(hours=1))
    assert days(request) == [DAY.isoformat(), today.isoformat(), today.isoformat()]


@pytest.mark.asyncio
async def test_a_day_is_kept_only_once_it_ended_a_while_ago(monkeypatch):
    ended = DAY + timedelta(days=1)
    now = [ended + feeder_meals._DAY_GRACE - timedelta(seconds=1)]
    monkeypatch.setattr(feeder_meals, "_utcnow", lambda: now[0])
    request = serving([])
    api = MealManager(request, "home", "dev", None)
    await api.history(DAY, ended)
    await api.history(DAY, ended)
    assert request.await_count == 2
    now[0] += timedelta(seconds=1)
    await api.history(DAY, ended)
    await api.history(DAY, ended)
    assert request.await_count == 3


@pytest.mark.asyncio
async def test_days_not_yet_begun_are_not_asked_for(monkeypatch):
    now = DAY + timedelta(hours=12)
    monkeypatch.setattr(feeder_meals, "_utcnow", lambda: now)
    request = serving([row("a", DAY + timedelta(hours=1))])
    api = MealManager(request, "home", "dev", None)
    events = await api.history(DAY - timedelta(days=1), DAY + timedelta(days=20))
    assert [event.id for event in events] == ["a"]
    assert days(request) == [(DAY - timedelta(days=1)).isoformat(), DAY.isoformat()]
    # Midnight that begins a day is part of it.
    monkeypatch.setattr(feeder_meals, "_utcnow", lambda: DAY + timedelta(days=1))
    await api.history(DAY, DAY + timedelta(days=3))
    assert days(request)[2:] == [DAY.isoformat(), (DAY + timedelta(days=1)).isoformat()]


@pytest.mark.asyncio
async def test_kept_days_are_read_again_after_a_while(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(
        feeder_meals, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    request = serving([])
    api = MealManager(request, "home", "dev", None)
    await api.history(DAY, DAY + timedelta(days=1))
    clock[0] += feeder_meals._DAY_TTL - 1
    await api.history(DAY, DAY + timedelta(days=1))
    assert request.await_count == 1
    clock[0] += 1
    await api.history(DAY, DAY + timedelta(days=1))
    assert request.await_count == 2


@pytest.mark.asyncio
async def test_expired_days_are_dropped_even_when_not_asked_again(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(
        feeder_meals, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    api = MealManager(serving([]), "home", "dev", None)
    other = DAY + timedelta(days=5)
    await api.history(DAY, DAY + timedelta(days=1))
    clock[0] += feeder_meals._DAY_TTL - 1
    await api.history(other, other + timedelta(days=1))
    assert {day for _, day in api._days} == {DAY, other}
    clock[0] += 1
    await api.history(other, other + timedelta(days=1))
    assert {day for _, day in api._days} == {other}


@pytest.mark.asyncio
async def test_history_and_status_keep_their_days_apart():
    request = serving([row("low", DAY + timedelta(hours=1), "food_level_low")])
    api = MealManager(request, "home", "dev", None)
    assert await api.history(DAY, DAY + timedelta(days=1)) == []
    assert latest_status(await api.status_events(DAY, DAY + timedelta(days=1))) == {
        "food_level_low": DAY + timedelta(hours=1)
    }
    types = [
        tuple(v for k, v in call.kwargs["params"] if k == "types")
        for call in request.await_args_list
    ]
    assert types == [("meal_dispensed",), STATUS_EVENT_TYPES]


@pytest.mark.asyncio
async def test_a_day_that_failed_is_not_kept():
    answers = [{"item": []}, FeederError("down"), {"item": []}, {"item": []}]
    request = AsyncMock(side_effect=answers)
    api = MealManager(request, "home", "dev", None)
    with pytest.raises(FeederError):
        await api.history(DAY, DAY + timedelta(days=2))
    await api.history(DAY, DAY + timedelta(days=2))
    assert days(request) == [
        DAY.isoformat(),
        (DAY + timedelta(days=1)).isoformat(),
        (DAY + timedelta(days=1)).isoformat(),
    ]

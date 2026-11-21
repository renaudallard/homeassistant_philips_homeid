# Copyright (c) 2025-2026, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
"""Device-scoped HomeID meals, events and settings, without HA or HTTP.

The only writes are the app's own: meal changes, the camera switch and the
desiccant reset. The request and write callbacks own authentication, HTTP
status handling and redirects. URLs found in responses are never used.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .feeder_api import (
    FeederDevice,
    FeederError,
    FeederHTTPError,
    identifier,
    list_items,
)

# Event types whose latest occurrence is exposed as feeder status. These come
# from the pet backend's event model; none has a matching "resolved" event.
STATUS_EVENT_TYPES = (
    "food_level_low",
    "food_outlet_stuck",
    "device_online",
    "device_offline",
)
_TEXT_LIMIT = 80
# The app's meal editor keeps the amount within 1 to 20 portions.
MAX_MEAL_PORTIONS = 20
# A day's events are kept once it ended this long ago, leaving time for an
# event the cloud records late, and read again after _DAY_TTL seconds.
_DAY_GRACE = timedelta(minutes=15)
_DAY_TTL = 6 * 3600.0
# How long a planned meal lasts on the calendar; display only.
_MEAL_DURATION = timedelta(minutes=1)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MealsError(FeederError, ValueError):
    """Invalid input or unsupported response; messages contain no response data."""


class Request(Protocol):
    """Authenticated JSON read supplied by the caller."""

    async def __call__(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]] | None = None,
    ) -> Any: ...


class Write(Protocol):
    """Authenticated JSON write supplied by the caller."""

    async def __call__(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> None: ...


@dataclass(frozen=True)
class Meal:
    """Weekly schedule; time preserves HH:MM home-local or HH:MMZ UTC."""

    id: str
    name: str
    portions: int
    time: str
    repeat_days: tuple[int, ...]
    enabled: bool


@dataclass(frozen=True)
class FeedingEvent:
    """Cloud-reported dispensing; amount has no inferred unit."""

    id: str
    time: datetime
    name: str
    amount: int | float | None
    source: str


@dataclass(frozen=True)
class StatusEvent:
    """A one-shot status report; none says that a condition ended.

    id is None when HomeID gave none that is usable: such a report still
    counts for the latest time of its type, but is never fired as an event.
    """

    id: str | None
    type: str
    time: datetime


def latest_status(events: list[StatusEvent]) -> dict[str, datetime]:
    """Latest time of each status event type; types without one are absent."""
    latest: dict[str, datetime] = {}
    for event in events:
        if event.type not in latest or event.time > latest[event.type]:
            latest[event.type] = event.time
    return latest


@dataclass(frozen=True)
class Occurrence:
    """Planned feeding in UTC, not a guarantee of physical dispensing."""

    start: datetime
    end: datetime
    name: str
    portions: int
    meal_id: str


def _name(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or not 1 <= len(value) <= 80:
        raise MealsError("Meal name must contain 1 to 80 characters")
    return value


def _portions(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_MEAL_PORTIONS:
        raise MealsError("Portions must be an integer from 1 to 20")
    return value


def _time(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]Z?", value
    ):
        raise MealsError("Meal time must be HH:MM or HH:MMZ")
    return value


def _write_time(value: object) -> str:
    """The app sends a local time as HH:mm, never a UTC HH:MMZ."""
    if _time(value).endswith("Z"):
        raise MealsError("Meal time must be HH:MM")
    return str(value)


def _days(value: object, *, allow_empty: bool = False) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or any(
        type(day) is not int or not 1 <= day <= 7 for day in value
    ):
        raise MealsError("Repeat days must be unique weekdays from 1 to 7")
    if len(set(value)) != len(value) or (not value and not allow_empty):
        raise MealsError("Repeat days must be unique and nonempty")
    return tuple(value)


def _enabled(value: object) -> bool:
    if type(value) is not bool:
        raise MealsError("Enabled must be a boolean")
    return value


def _zone(value: str | None) -> ZoneInfo:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or value in ("localtime", "posixrules")
    ):
        raise MealsError("An explicit home IANA time zone is required")
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise MealsError("An explicit home IANA time zone is required") from None


def _bounds(start: datetime, end: datetime, max_days: int) -> tuple[datetime, datetime]:
    if any(
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        for value in (start, end)
    ):
        raise MealsError("Time bounds must be timezone-aware")
    try:
        start, end = start.astimezone(UTC), end.astimezone(UTC)
    except (ValueError, OverflowError):
        raise MealsError("Time bounds are outside the supported date range") from None
    if end < start or end - start > timedelta(days=max_days):
        raise MealsError(f"Time range must be ordered and at most {max_days} days")
    # Leave room for timezone conversion and the display duration at date limits.
    if start.year <= 1 or end.year >= 9999:
        raise MealsError("Time bounds are outside the supported date range")
    return start, end


def _meal(row: dict[str, Any]) -> Meal:
    return Meal(
        id=identifier(row.get("id")),
        name=_name(row.get("name")),
        portions=_portions(
            row.get("portionAmount")
            if row.get("portionAmount") is not None
            else row.get("amount")
        ),
        time=_time(
            row.get("feedTime") if row.get("feedTime") is not None else row.get("time")
        ),
        repeat_days=_days(row.get("repeatDays"), allow_empty=True),
        enabled=_enabled(row.get("enabled")),
    )


def _event_id(value: object) -> str | None:
    try:
        return identifier(value)
    except FeederError:
        return None


def _event_time(value: object) -> datetime | None:
    """An event time with an offset, in UTC; None for anything else.

    The app parses it with Instant.parse and drops the event when that fails.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        pass
    return None


def _event_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MealsError("Invalid feeding event text")
    return value[:_TEXT_LIMIT]


@dataclass(frozen=True)
class DesiccantTimer:
    """The desiccant bag timer the HomeID app shows, as plain dates."""

    replace_at: date | None
    applied_at: date | None


def _timer_date(value: object) -> date | None:
    """An ISO date as the app parses it; anything else counts as absent."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _moshi_text(value: object) -> bool:
    """Whether the app's JSON reader takes value as text: a string or a number."""
    return isinstance(value, str | int | float) and not isinstance(value, bool)


def _camera_row(row: dict[str, Any]) -> tuple[str, bool]:
    """A row's id and camera state, held to the shape the app's model reads.

    The app needs an id and a settings map in every row, each entry null or
    a {category, value} object, value a boolean or null, and fails on
    anything else. It reads a missing or null device_active as off.
    """
    settings = row.get("settings")
    name = row.get("name")
    if (
        not _moshi_text(row.get("id"))
        or not (name is None or _moshi_text(name))
        or not isinstance(settings, dict)
        or not all(
            item is None
            or (
                isinstance(item, dict)
                and (item.get("category") is None or _moshi_text(item.get("category")))
                and isinstance(item.get("value"), bool | None)
            )
            for item in settings.values()
        )
    ):
        raise MealsError("Unsupported camera setting response")
    active = settings.get("device_active")
    return str(row["id"]), bool(active and active.get("value"))


def today_in(zone: str | None) -> date:
    """Today in a home's IANA zone, which is refused as for the meals."""
    return _utcnow().astimezone(_zone(zone)).date()


class MealManager:
    """One feeder's meals, events and settings via home-rooted paths only."""

    def __init__(
        self,
        request: Request,
        home_id: str,
        philips_device_id: str,
        time_zone: str | None,
        write: Write | None = None,
    ) -> None:
        self._request = request
        self._write = write
        self._root = f"/api/homes/{quote(identifier(home_id), safe='')}"
        self._philips_id = identifier(philips_device_id)
        # History has absolute timestamps and must remain usable without a home
        # timezone. Resolve the zone only when a local-time schedule needs it.
        self.time_zone = time_zone or None
        # Events of ended UTC days by (types, day): (expiry, rows).
        self._days: dict[
            tuple[tuple[str, ...], datetime],
            tuple[float, list[dict[str, Any]]],
        ] = {}

    @classmethod
    def for_device(
        cls, request: Request, device: FeederDevice, write: Write | None = None
    ) -> MealManager:
        return cls(request, device.home_id, device.philips_id, device.time_zone, write)

    async def list_meals(self) -> list[Meal]:
        """Keep disabled/empty-repeat meals; reject unsupported selected schedules."""
        rows = list_items(await self._request("GET", f"{self._root}/meals"))
        meals: list[Meal] = []
        seen: set[str] = set()
        foreign_ids: set[str] = set()
        for row in rows:
            device_id = identifier(row.get("deviceId"))
            if device_id != self._philips_id:
                if isinstance(row.get("id"), str):
                    foreign_ids.add(row["id"])
                continue
            meal = _meal(row)
            if meal.id in seen:
                raise MealsError("Duplicate meal identity")
            seen.add(meal.id)
            meals.append(meal)
        if seen & foreign_ids:
            raise MealsError("Conflicting meal ownership")
        return meals

    def _meal_body(
        self, name: object, feed_time: object, portions: object, days: object
    ) -> dict[str, Any]:
        """The app's body for a new or edited meal, validated before sending."""
        return {
            "name": _name(name),
            "time": _write_time(feed_time),
            "amount": _portions(portions),
            "deviceId": self._philips_id,
            "repeatDays": sorted(_days(days)),
        }

    async def _send(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> None:
        if self._write is None:
            raise MealsError("HomeID changes are not available")
        await self._write(method, path, body)

    def _meal_path(self, meal_id: str) -> str:
        return f"{self._root}/meals/{quote(identifier(meal_id), safe='')}"

    async def add_meal(
        self, name: str, feed_time: str, portions: int, days: Iterable[int]
    ) -> None:
        """Plan a weekly meal; the new meal shows on the next read."""
        body = self._meal_body(name, feed_time, portions, tuple(days))
        await self._send("POST", f"{self._root}/meals", body)

    async def edit_meal(
        self,
        meal_id: str,
        name: str,
        feed_time: str,
        portions: int,
        days: Iterable[int],
    ) -> None:
        """Replace a meal's name, time, portions and days, as the app does."""
        path = self._meal_path(meal_id)
        body = self._meal_body(name, feed_time, portions, tuple(days))
        await self._send("PATCH", path, body)

    async def set_meal_enabled(self, meal_id: str, enabled: bool) -> None:
        path = self._meal_path(meal_id)
        await self._send("PATCH", path, {"enabled": _enabled(enabled)})

    async def delete_meal(self, meal_id: str) -> None:
        await self._send("DELETE", self._meal_path(meal_id))

    def _event_owned(self, row: dict[str, Any]) -> bool:
        """An event is this feeder's when its deviceId is, as in the app.

        The app asks the backend for this device's events and reads no other
        identifier from them.
        """
        return row.get("deviceId") == self._philips_id

    async def history(self, start: datetime, end: datetime) -> list[FeedingEvent]:
        """Cloud meal_dispensed events in [start, end), at most 31 days.

        No commands issued locally are converted into historical feeding events.
        """
        start, end = _bounds(start, end, 31)
        if start == end:
            return []
        rows = await self._events(start, end, ("meal_dispensed",))
        events: dict[str, FeedingEvent] = {}
        for row in rows:
            if row.get("type") != "meal_dispensed" or not self._event_owned(row):
                continue
            when = _event_time(row.get("time"))
            if when is None or not start <= when < end:
                continue
            amount = row.get("mealAmount")
            if amount is not None and (
                type(amount) not in (int, float)
                or amount < 0
                or (isinstance(amount, float) and not math.isfinite(amount))
            ):
                raise MealsError("Invalid feeding event amount")
            event = FeedingEvent(
                id=identifier(row.get("id")),
                time=when,
                name=_event_text(row.get("mealName")),
                amount=amount,
                source=_event_text(row.get("source")),
            )
            if event.id in events and events[event.id] != event:
                raise MealsError("Conflicting event identity")
            events[event.id] = event
        return sorted(events.values(), key=lambda event: (event.time, event.id))

    async def _events(
        self, start: datetime, end: datetime, types: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        """Events of every UTC day that [start, end) touches, a day at a time.

        The cloud answers a longer range with only its newest events, and the
        app asks for one day per request. A day that has not begun holds no
        events and is not asked for. A day that ended _DAY_GRACE ago is kept
        for _DAY_TTL, so that an update reads only today again.
        """
        now = _utcnow()
        clock = time.monotonic()
        self._days = {key: kept for key, kept in self._days.items() if clock < kept[0]}
        rows: list[dict[str, Any]] = []
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while day < end and day <= now:
            next_day = day + timedelta(days=1)
            kept = self._days.get((types, day))
            if kept is None:
                found = await self._day_events(day, next_day, types)
                if next_day + _DAY_GRACE <= now:
                    self._days[(types, day)] = (time.monotonic() + _DAY_TTL, found)
            else:
                found = kept[1]
            rows += found
            day = next_day
        return rows

    async def _day_events(
        self, start: datetime, end: datetime, types: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        # One pair per list element, the way the app's Retrofit client sends
        # a list query parameter, and only this device's events, as the app.
        params = [("from", start.isoformat()), ("to", end.isoformat())]
        params += [("types", kind) for kind in types]
        params += [("deviceIds", self._philips_id)]
        return list_items(
            await self._request("GET", f"{self._root}/events", params=params)
        )

    async def status_events(self, start: datetime, end: datetime) -> list[StatusEvent]:
        """Status events in [start, end), at most 31 days, oldest first.

        Each event is a one-shot report: none of them says that a condition
        ended, so no "resolved" or healthy state is derived from their absence.
        """
        start, end = _bounds(start, end, 31)
        if start == end:
            return []
        events: dict[str, StatusEvent] = {}
        unnamed: list[StatusEvent] = []
        for row in await self._events(start, end, STATUS_EVENT_TYPES):
            kind = row.get("type")
            if kind not in STATUS_EVENT_TYPES or not self._event_owned(row):
                continue
            when = _event_time(row.get("time"))
            if when is None or not start <= when < end:
                continue
            event = StatusEvent(_event_id(row.get("id")), kind, when)
            if event.id is None:
                unnamed.append(event)
                continue
            if events.get(event.id, event) != event:
                raise MealsError("Conflicting event identity")
            events[event.id] = event
        return sorted(
            [*events.values(), *unnamed],
            key=lambda event: (event.time, event.id or ""),
        )

    async def desiccant(self) -> DesiccantTimer:
        """Read the desiccant bag timer like the HomeID app does.

        A 404 or an unusable expiryDate means no timer, as in the app, and
        both dates then stay unknown.
        """
        path = (
            f"{self._root}/devices/{quote(self._philips_id, safe='')}/desiccant-timer"
        )
        try:
            data = await self._request("GET", path)
        except FeederHTTPError as err:
            if err.status == 404:
                return DesiccantTimer(None, None)
            raise
        if not isinstance(data, dict):
            raise MealsError("Unsupported desiccant timer response")
        expiry = _timer_date(data.get("expiryDate"))
        if expiry is None:
            return DesiccantTimer(None, None)
        return DesiccantTimer(expiry, _timer_date(data.get("lastReplacedDate")))

    async def reset_desiccant(self, day: date) -> None:
        """Record the day the desiccant bag was replaced, as the app does."""
        if type(day) is not date:
            raise MealsError("A replacement day is required")
        path = (
            f"{self._root}/devices/{quote(self._philips_id, safe='')}/desiccant-timer"
        )
        await self._send("POST", path, {"lastReplacedDate": day.isoformat()})

    async def camera(self) -> bool | None:
        """Whether the camera is on, as the app reads it.

        The app takes the first row of this device. None means HomeID has
        no row for it, and the app then shows no camera switch.
        """
        rows = list_items(
            await self._request("GET", f"{self._root}/modes/home/devices")
        )
        found = [_camera_row(row) for row in rows]
        return next((on for key, on in found if key == self._philips_id), None)

    async def set_camera(self, enabled: bool) -> None:
        """Turn the camera on or off, the only setting the app writes here."""
        path = f"{self._root}/modes/home/devices/{quote(self._philips_id, safe='')}"
        body = {"settings": {"device_active": {"value": _enabled(enabled)}}}
        await self._send("PATCH", path, body)


def occurrences(
    meals: Iterable[Meal], start: datetime, end: datetime, time_zone: str | None
) -> list[Occurrence]:
    """Expand planned weekly meals overlapping [start, end), at most 366 days.

    Overlapping, not only starting, as HA's calendar triggers expect: an end
    trigger needs a meal that started just before its scan window. Weekdays
    belong to the schedule's timezone: UTC for HH:MMZ, otherwise the explicit
    home IANA zone (required only for local schedules). Skip nonexistent wall
    times; choose fold=0 (the first instant) during a repeated hour. One minute
    is display duration only.
    """
    start, end = _bounds(start, end, 366)
    result: list[Occurrence] = []
    for meal in meals:
        identifier(meal.id)
        _name(meal.name)
        _portions(meal.portions)
        feed_time = _time(meal.time)
        days = _days(meal.repeat_days, allow_empty=True)
        if not _enabled(meal.enabled) or not days or start == end:
            continue
        zone = UTC if feed_time.endswith("Z") else _zone(time_zone)
        hour, minute = map(int, feed_time.removesuffix("Z").split(":"))
        day = (start - _MEAL_DURATION).astimezone(zone).date()
        last_day = end.astimezone(zone).date()
        while day <= last_day:
            if day.isoweekday() in days:
                local = datetime(
                    day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=0
                )
                when = local.astimezone(UTC)
                # A UTC round trip detects a wall time skipped by a DST jump.
                if (
                    when.astimezone(zone).replace(tzinfo=None)
                    == local.replace(tzinfo=None)
                    and when < end
                    and when + _MEAL_DURATION > start
                ):
                    result.append(
                        Occurrence(
                            when,
                            when + _MEAL_DURATION,
                            meal.name,
                            meal.portions,
                            meal.id,
                        )
                    )
            day += timedelta(days=1)
    return sorted(result, key=lambda item: (item.start, item.meal_id))

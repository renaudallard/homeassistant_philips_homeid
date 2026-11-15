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
"""Device-scoped HomeID meals, events and desiccant timer, without HA or HTTP.

Read only. The request callback owns authentication, HTTP status handling
and redirects. URLs found in responses are never used.
"""

from __future__ import annotations

import math
import re
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
# How long a planned meal lasts on the calendar; display only.
_MEAL_DURATION = timedelta(minutes=1)


class MealsError(FeederError, ValueError):
    """Invalid input or unsupported response; messages contain no response data."""


class EventOwnershipError(MealsError):
    """Only boolean identity comparisons, never the identifiers themselves."""

    def __init__(self, flags: tuple[bool, ...]) -> None:
        super().__init__("Feeding event ownership could not be verified")
        # present internal/external, known registry external, matches internal/
        # external, internal/external equal this device's confirmed Tuya vendor ID.
        self.flags = flags


class Request(Protocol):
    """Authenticated JSON read supplied by the caller."""

    async def __call__(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any: ...


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
    if type(value) is not int or not 1 <= value <= 20:
        raise MealsError("Portions must be an integer from 1 to 20")
    return value


def _time(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]Z?", value
    ):
        raise MealsError("Meal time must be HH:MM or HH:MMZ")
    return value


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


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise MealsError("Invalid feeding event timestamp")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        pass
    raise MealsError("Invalid feeding event timestamp")


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


class MealManager:
    """Read one feeder's meals, events and settings via home-rooted paths only."""

    def __init__(
        self,
        request: Request,
        home_id: str,
        philips_device_id: str,
        tuya_device_id: str,
        time_zone: str | None,
        *,
        external_device_id: str | None = None,
    ) -> None:
        self._request = request
        self._root = f"/api/homes/{quote(identifier(home_id), safe='')}"
        self._philips_id = identifier(philips_device_id)
        # vendorId (Tuya) and externalId are distinct registry fields. Only an
        # explicitly supplied externalId can verify an event's deviceExternalId.
        self._tuya_id = identifier(tuya_device_id)
        self._external_id = (
            identifier(external_device_id) if external_device_id is not None else None
        )
        # History has absolute timestamps and must remain usable without a home
        # timezone. Resolve the zone only when a local-time schedule needs it.
        self.time_zone = time_zone or None

    @classmethod
    def for_device(cls, request: Request, device: FeederDevice) -> MealManager:
        return cls(
            request,
            device.home_id,
            device.philips_id,
            device.vendor_id,
            device.time_zone,
            external_device_id=device.external_id,
        )

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

    def _ownership_error(self, internal: Any, external: Any) -> EventOwnershipError:
        return EventOwnershipError(
            (
                internal is not None,
                external is not None,
                self._external_id is not None,
                internal == self._philips_id,
                self._external_id is not None and external == self._external_id,
                internal == self._tuya_id,
                external == self._tuya_id,
            )
        )

    def _event_owned(self, row: dict[str, Any]) -> bool:
        internal, external = row.get("deviceId"), row.get("deviceExternalId")
        internal_matches = internal == self._philips_id
        if self._external_id is None:
            # Observed HomeID shape: registry externalId is absent, while the
            # event pairs the exact Philips internal ID with the exact Tuya
            # vendor ID. Only that pair is accepted. One of the two alone, or
            # one contradicted by the other, is unverifiable, not foreign.
            vendor_matches = external == self._tuya_id
            if internal_matches and vendor_matches:
                return True
            if internal_matches or vendor_matches:
                raise self._ownership_error(internal, external)
            if internal is not None:
                identifier(internal)
                return False  # positively identified as another Philips device
            raise self._ownership_error(internal, external)
        # A known registry externalId is never overridden by the vendor ID.
        external_matches = external == self._external_id
        if internal_matches or external_matches:
            # A partially matching own event with an unknown/conflicting second
            # ID is an unverifiable read, not evidence of an empty history.
            if (internal is not None and not internal_matches) or (
                external is not None and not external_matches
            ):
                raise self._ownership_error(internal, external)
            return True
        if internal is not None:
            identifier(internal)
            return False  # positively identified as another Philips device
        if external is not None:
            identifier(external)
            return False  # positively identified as another external device
        raise self._ownership_error(internal, external)

    async def history(self, start: datetime, end: datetime) -> list[FeedingEvent]:
        """Cloud meal_dispensed events in [start, end), at most 31 days.

        clustered=false is intentional but unverified live (reference uses true).
        No commands issued locally are converted into historical feeding events.
        """
        start, end = _bounds(start, end, 31)
        if start == end:
            return []
        rows = await self._events(start, end, "meal_dispensed")
        events: dict[str, FeedingEvent] = {}
        for row in rows:
            if row.get("type") != "meal_dispensed" or not self._event_owned(row):
                continue
            when = _timestamp(row.get("time"))
            if not start <= when < end:
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
                raise MealsError("Conflicting feeding event identity")
            events[event.id] = event
        return sorted(events.values(), key=lambda event: (event.time, event.id))

    async def _events(
        self, start: datetime, end: datetime, types: str
    ) -> list[dict[str, Any]]:
        return list_items(
            await self._request(
                "GET",
                f"{self._root}/events",
                params={
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "types": types,
                    "clustered": "false",
                },
            )
        )

    async def latest_status(
        self, start: datetime, end: datetime
    ) -> dict[str, datetime]:
        """Latest time of each status event type in [start, end), at most 31 days.

        Only types with an event in the window appear in the result. Each
        event is a one-shot report: none of them says that a condition ended,
        so no "resolved" or healthy state is derived from their absence.
        """
        start, end = _bounds(start, end, 31)
        if start == end:
            return {}
        latest: dict[str, datetime] = {}
        for row in await self._events(start, end, ",".join(STATUS_EVENT_TYPES)):
            kind = row.get("type")
            if kind not in STATUS_EVENT_TYPES or not self._event_owned(row):
                continue
            when = _timestamp(row.get("time"))
            if start <= when < end and (kind not in latest or when > latest[kind]):
                latest[kind] = when
        return latest

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

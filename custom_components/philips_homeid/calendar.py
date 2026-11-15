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
"""Read-only calendars for planned meals and cloud-reported feedings."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .cloud_auth import CloudAuthError
from .const import CONF_IS_FEEDER, DOMAIN
from .feeder import FEEDER_ERRORS, FeederCoordinator, FeederEntity
from .feeder_meals import FeedingEvent, occurrences


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Calendars exist only for feeder entries."""
    if entry.data.get(CONF_IS_FEEDER):
        coordinator = hass.data[DOMAIN][entry.entry_id]
        async_add_entities(
            [FeederScheduleCalendar(coordinator), FeederHistoryCalendar(coordinator)]
        )


class FeederScheduleCalendar(FeederEntity, CalendarEntity):
    """Weekly cloud schedule, read only; meals are edited in the HomeID app."""

    _attr_translation_key = "feeder_schedule"

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator, "feeder_schedule")

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        if not super().available or data is None or not data.meals_ok:
            return False
        try:
            self._events(dt_util.utcnow(), dt_util.utcnow() + timedelta(days=1))
        except FEEDER_ERRORS:
            return False
        return True

    def _events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return [
            CalendarEvent(
                start=item.start,
                end=item.end,
                summary=item.name,
                description=f"{item.portions} portions. Planned meal; not confirmation of dispensing.",
                uid=f"{item.meal_id}:{item.start.isoformat()}",
            )
            for item in occurrences(
                self.coordinator.data.meals, start, end, self.coordinator.data.time_zone
            )
        ]

    @property
    def event(self) -> CalendarEvent | None:
        if not self.available:
            return None
        now = dt_util.utcnow()
        events = self._events(now - timedelta(minutes=1), now + timedelta(days=8))
        return next((event for event in events if event.end > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        if not self.available:
            raise HomeAssistantError("HomeID schedule is unavailable")
        try:
            return self._events(start_date, end_date)
        except FEEDER_ERRORS as err:
            raise HomeAssistantError(
                "Unable to display HomeID schedule for this range"
            ) from err


class FeederHistoryCalendar(FeederEntity, CalendarEntity):
    """Actual meal_dispensed reports; no synthetic events from commands."""

    _attr_translation_key = "feeder_history"

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator, "feeder_history")

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and data.history_ok

    @staticmethod
    def _event(item: FeedingEvent) -> CalendarEvent:
        # The API does not establish mealAmount units. Do not label it grams.
        return CalendarEvent(
            start=item.time,
            end=item.time + timedelta(seconds=1),
            summary=item.name or "Feeding",
            description=f"Cloud-reported feeding. Amount: {item.amount}; source: {item.source}",
            uid=item.id,
        )

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.utcnow()
        return (
            next(
                (
                    self._event(item)
                    for item in self.coordinator.data.history
                    if item.time <= now < item.time + timedelta(seconds=1)
                ),
                None,
            )
            if self.available
            else None
        )

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        if (
            start_date.tzinfo is None
            or end_date.tzinfo is None
            or not timedelta(0) < end_date - start_date <= timedelta(days=93)
        ):
            raise HomeAssistantError("Choose a history range from 1 second to 93 days")
        try:
            manager = await self.coordinator.meal_manager()
            events: dict[str, FeedingEvent] = {}
            cursor = start_date
            while cursor < end_date:
                stop = min(cursor + timedelta(days=31), end_date)
                for item in await manager.history(cursor, stop):
                    events[item.id] = item
                cursor = stop
            return [
                self._event(item)
                for item in sorted(events.values(), key=lambda item: item.time)
            ]
        except FEEDER_ERRORS as err:  # before CloudAuthError, its base class
            raise HomeAssistantError("Unable to read HomeID feeding history") from err
        except CloudAuthError as err:
            raise HomeAssistantError("HomeID login expired") from err

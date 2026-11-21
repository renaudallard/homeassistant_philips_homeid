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
"""Events for the feeder's activity log, as HomeID reports them.

An entity fires once for each event that appears after its first successful
read. Events already there at that read, including those from while Home
Assistant was stopped, do not fire; the sensors still show the latest.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import CONF_IS_FEEDER, DOMAIN
from .feeder import LOOKBACK, FeederCoordinator, FeederData, FeederEntity

# Events from one read fire this many seconds apart, each with a state of its
# own: before 2026.8 HA gave two events in one millisecond the same state,
# and a state trigger then saw only the first.
_SPACING = 0.01


@dataclass(frozen=True)
class _Report:
    id: str
    type: str
    time: datetime
    attributes: dict[str, Any]


def _feedings(data: FeederData) -> list[_Report]:
    # mealAmount's unit is not established, so it is not called grams.
    return [
        _Report(
            item.id,
            "meal_dispensed",
            item.time,
            {
                "time": item.time.isoformat(),
                "name": item.name,
                "amount": item.amount,
                "source": item.source,
            },
        )
        for item in data.history
    ]


_FOOD_ALERTS = ("food_level_low", "food_outlet_stuck")


def _food_alerts(data: FeederData) -> list[_Report]:
    # Without an id an alert cannot be told from one already fired.
    return [
        _Report(item.id, item.type, item.time, {"time": item.time.isoformat()})
        for item in data.status_events
        if item.type in _FOOD_ALERTS and item.id is not None
    ]


@dataclass(frozen=True)
class _EventSpec:
    key: str
    types: tuple[str, ...]
    available: Callable[[FeederData], bool]
    reports: Callable[[FeederData], list[_Report]]
    icon: str


# Only the types a PAW5320 has been seen to send.
_EVENTS = (
    _EventSpec(
        "feeder_feeding",
        ("meal_dispensed",),
        lambda data: data.history_ok,
        _feedings,
        "mdi:food-drumstick",
    ),
    _EventSpec(
        "feeder_food_alert",
        _FOOD_ALERTS,
        lambda data: data.status_ok,
        _food_alerts,
        "mdi:bowl-mix-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Events exist only for feeder entries."""
    if entry.data.get(CONF_IS_FEEDER):
        coordinator = hass.data[DOMAIN][entry.entry_id]
        async_add_entities(FeederEvent(coordinator, spec) for spec in _EVENTS)


class FeederEvent(FeederEntity, EventEntity):
    """Fires each new event of its types, oldest first."""

    def __init__(self, coordinator: FeederCoordinator, spec: _EventSpec) -> None:
        super().__init__(coordinator, spec.key)
        self._spec = spec
        self._attr_event_types = list(spec.types)
        self._attr_icon = spec.icon
        # Events already seen by id, with their time; None until the first
        # successful read.
        self._seen: dict[str, datetime] | None = None
        self._queue: list[_Report] = []
        self._next: CALLBACK_TYPE | None = None

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and self._spec.available(data)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._take()

    async def async_will_remove_from_hass(self) -> None:
        if self._next is not None:
            self._next()
            self._next = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        # Availability is written first, so that an event after a failed read
        # changes the state from a time, not from unavailable.
        super()._handle_coordinator_update()
        self._take()

    @callback
    def _take(self) -> None:
        data = self.coordinator.data
        if data is None or not self._spec.available(data):
            return
        reports = self._spec.reports(data)
        seen = self._seen
        self._seen = {report.id: report.time for report in reports}
        if seen is None:
            return
        self._queue += sorted(
            (report for report in reports if report.id not in seen),
            key=lambda report: (report.time, report.id),
        )
        # An event older than the window is never read again.
        oldest = dt_util.utcnow() - LOOKBACK
        for key, when in seen.items():
            if key not in self._seen and when >= oldest:
                self._seen[key] = when
        if self._queue and self._next is None:
            self._fire()

    @callback
    def _fire(self, _now: datetime | None = None) -> None:
        self._next = None
        report = self._queue.pop(0)
        self._trigger_event(report.type, report.attributes)
        self.async_write_ha_state()
        if self._queue:
            self._next = async_call_later(self.hass, _SPACING, self._fire)

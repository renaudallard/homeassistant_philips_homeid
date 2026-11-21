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
"""Feeder meal changes: the add, edit and delete actions and a switch per meal.

Each change is what the HomeID app sends, through the coordinator's meal
manager, and the meals are read again afterwards, also when the change was
not confirmed, since a write that timed out may still have been applied.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import time
from typing import Any

import voluptuous as vol
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import WEEKDAYS
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.service import async_register_admin_service

from .cloud_auth import CloudAuthError
from .const import CONF_CPP_ID, DOMAIN
from .feeder import FEEDER_ERRORS, FeederCoordinator, FeederEntity
from .feeder_api import FeederHTTPError
from .feeder_meals import MAX_MEAL_PORTIONS, Meal, MealManager, MealsError

SERVICE_ADD_MEAL = "add_feeder_meal"
SERVICE_EDIT_MEAL = "edit_feeder_meal"
SERVICE_DELETE_MEAL = "delete_feeder_meal"
ATTR_DEVICE = "device"
ATTR_MEAL = "meal"
ATTR_NAME = "name"
ATTR_TIME = "time"
ATTR_PORTIONS = "portions"
ATTR_DAYS = "days"


def _whole(value: Any) -> int:
    """A whole number of portions; a fraction or a boolean is refused."""
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            raise vol.Invalid("Portions must be a whole number") from None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if type(value) is not int:
        raise vol.Invalid("Portions must be a whole number")
    return value


_PORTIONS = vol.All(_whole, vol.Range(min=1, max=MAX_MEAL_PORTIONS))
_DAYS = vol.All(cv.weekdays, vol.Length(min=1))

ADD_MEAL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE): cv.string,
        vol.Required(ATTR_NAME): cv.string,
        vol.Required(ATTR_TIME): cv.time,
        vol.Required(ATTR_PORTIONS): _PORTIONS,
        vol.Required(ATTR_DAYS): _DAYS,
    }
)
EDIT_MEAL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MEAL): cv.entity_id,
        vol.Optional(ATTR_NAME): cv.string,
        vol.Optional(ATTR_TIME): cv.time,
        vol.Optional(ATTR_PORTIONS): _PORTIONS,
        vol.Optional(ATTR_DAYS): _DAYS,
    }
)
DELETE_MEAL_SCHEMA = vol.Schema({vol.Required(ATTR_MEAL): cv.entity_id})


def _meal_prefix(coordinator: FeederCoordinator) -> str:
    return f"{coordinator.config_entry.data[CONF_CPP_ID]}_meal_"


def _iso_days(days: list[str]) -> list[int]:
    return sorted({WEEKDAYS.index(day) + 1 for day in days})


def _day_names(days: tuple[int, ...]) -> list[str]:
    return [WEEKDAYS[day - 1] for day in sorted(days)]


def _clock(value: time) -> str:
    if value.second or value.microsecond:
        raise ServiceValidationError("Meal times are whole minutes")
    return f"{value.hour:02d}:{value.minute:02d}"


async def _change(
    coordinator: FeederCoordinator, change: Callable[[MealManager], Awaitable[None]]
) -> None:
    """Send one schedule change, then read the meals again whatever happened.

    Changes go one at a time, so each starts from what the one before it
    read back.
    """
    async with coordinator.changes:
        try:
            manager = await coordinator.meal_manager()
        except FEEDER_ERRORS as err:  # before CloudAuthError, its base class
            raise HomeAssistantError("HomeID cloud unavailable") from err
        except CloudAuthError as err:
            raise HomeAssistantError("HomeID login expired") from err
        try:
            await change(manager)
        except MealsError as err:
            # Raised before anything is sent; its messages are fixed text.
            raise ServiceValidationError(str(err)) from err
        except FeederHTTPError as err:
            await coordinator.refresh_meals()
            if err.status == 409:
                # The app's MealTimeConflictException.
                raise HomeAssistantError(
                    "Another meal is already planned at that time"
                ) from err
            raise HomeAssistantError("HomeID did not confirm the meal change") from err
        except FEEDER_ERRORS as err:
            await coordinator.refresh_meals()
            raise HomeAssistantError("HomeID did not confirm the meal change") from err
        except CloudAuthError as err:
            raise HomeAssistantError("HomeID login expired") from err
        await coordinator.refresh_meals()


def _loaded_feeders(hass: HomeAssistant) -> list[FeederCoordinator]:
    return [
        item
        for item in hass.data.get(DOMAIN, {}).values()
        if isinstance(item, FeederCoordinator)
    ]


def _feeder_of_device(hass: HomeAssistant, device_id: str) -> FeederCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        for coordinator in _loaded_feeders(hass):
            identity = (DOMAIN, coordinator.config_entry.data[CONF_CPP_ID])
            if identity in device.identifiers:
                return coordinator
    raise ServiceValidationError("Choose a loaded HomeID pet feeder")


_NO_MEAL = "Choose a meal switch of a loaded HomeID pet feeder"


def _stored_meal(coordinator: FeederCoordinator, meal_id: str) -> Meal:
    # After a failed poll nothing read back is stored, so the meals held
    # may predate a change: an edit built on them could undo it.
    data = coordinator.data
    if data is None:
        raise ServiceValidationError(_NO_MEAL)
    if not (data.meals_ok and coordinator.last_update_success):
        raise ServiceValidationError(
            "The feeder's meals could not be read: try again after the next poll"
        )
    for meal in data.meals:
        if meal.id == meal_id:
            return meal
    raise ServiceValidationError(_NO_MEAL)


def _meal_of_entity(
    hass: HomeAssistant, entity_id: str
) -> tuple[FeederCoordinator, Meal]:
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None and entry.platform == DOMAIN and entry.domain == "switch":
        coordinator = hass.data.get(DOMAIN, {}).get(entry.config_entry_id)
        if isinstance(coordinator, FeederCoordinator):
            prefix = _meal_prefix(coordinator)
            if entry.unique_id.startswith(prefix):
                meal_id = entry.unique_id.removeprefix(prefix)
                return coordinator, _stored_meal(coordinator, meal_id)
    raise ServiceValidationError(_NO_MEAL)


async def _async_add_meal(call: ServiceCall) -> None:
    coordinator = _feeder_of_device(call.hass, call.data[ATTR_DEVICE])
    name = call.data[ATTR_NAME]
    feed_time = _clock(call.data[ATTR_TIME])
    portions = call.data[ATTR_PORTIONS]
    days = _iso_days(call.data[ATTR_DAYS])
    await _change(
        coordinator, lambda manager: manager.add_meal(name, feed_time, portions, days)
    )


async def _async_edit_meal(call: ServiceCall) -> None:
    coordinator, found = _meal_of_entity(call.hass, call.data[ATTR_MEAL])
    feed_time = _clock(call.data[ATTR_TIME]) if ATTR_TIME in call.data else None

    async def edit(manager: MealManager) -> None:
        # The meal as the change before this one read it back: what was left
        # out is sent as it is now, not as it was when the action came in.
        meal = _stored_meal(coordinator, found.id)
        # The app never sends these, so they are not sent back unasked.
        if ATTR_DAYS not in call.data and not meal.repeat_days:
            raise ServiceValidationError("This meal repeats on no day: give its days")
        if feed_time is None and meal.time.endswith("Z"):
            raise ServiceValidationError("This meal's time is in UTC: give its time")
        days = (
            _iso_days(call.data[ATTR_DAYS])
            if ATTR_DAYS in call.data
            else list(meal.repeat_days)
        )
        await manager.edit_meal(
            meal.id,
            call.data.get(ATTR_NAME, meal.name),
            feed_time or meal.time,
            call.data.get(ATTR_PORTIONS, meal.portions),
            days,
        )

    await _change(coordinator, edit)


async def _async_delete_meal(call: ServiceCall) -> None:
    coordinator, meal = _meal_of_entity(call.hass, call.data[ATTR_MEAL])
    await _change(coordinator, lambda manager: manager.delete_meal(meal.id))


@callback
def async_setup_schedule_services(hass: HomeAssistant) -> None:
    """Admin only, like other actions that change a device's configuration."""
    for service, handler, schema in (
        (SERVICE_ADD_MEAL, _async_add_meal, ADD_MEAL_SCHEMA),
        (SERVICE_EDIT_MEAL, _async_edit_meal, EDIT_MEAL_SCHEMA),
        (SERVICE_DELETE_MEAL, _async_delete_meal, DELETE_MEAL_SCHEMA),
    ):
        async_register_admin_service(hass, DOMAIN, service, handler, schema)


class FeederMealSwitch(FeederEntity, SwitchEntity):
    """Whether one planned meal is enabled, named after the meal."""

    _attr_icon = "mdi:food-drumstick-outline"

    def __init__(self, coordinator: FeederCoordinator, meal_id: str) -> None:
        super().__init__(coordinator, "feeder_meal")
        # Named after the meal, which has no translation.
        self._attr_translation_key = None
        self._meal_id = meal_id
        self._attr_unique_id = f"{_meal_prefix(coordinator)}{meal_id}"

    def _meal(self) -> Meal | None:
        data = self.coordinator.data
        if data is None or not data.meals_ok:
            return None
        return next((meal for meal in data.meals if meal.id == self._meal_id), None)

    @property
    def name(self) -> str | None:
        meal = self._meal()
        return meal.name if meal else None

    @property
    def available(self) -> bool:
        return super().available and self._meal() is not None

    @property
    def is_on(self) -> bool | None:
        meal = self._meal()
        return meal.enabled if meal else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        meal = self._meal()
        if meal is None:
            return None
        return {
            ATTR_TIME: meal.time,
            ATTR_PORTIONS: meal.portions,
            ATTR_DAYS: _day_names(meal.repeat_days),
        }

    async def _set(self, enabled: bool) -> None:
        await _change(
            self.coordinator,
            lambda manager: manager.set_meal_enabled(self._meal_id, enabled),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


async def async_setup_feeder_switches(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """A switch per meal, added and removed as the meals change."""
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    prefix = _meal_prefix(coordinator)
    known: set[str] = set()

    @callback
    def sync() -> None:
        data = coordinator.data
        if data is None or not data.meals_ok:
            return
        ids = {meal.id for meal in data.meals}
        if new := ids - known:
            known.update(new)
            async_add_entities(FeederMealSwitch(coordinator, i) for i in sorted(new))
        registry = er.async_get(hass)
        for item in er.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                item.domain == "switch"
                and item.unique_id.startswith(prefix)
                and item.unique_id.removeprefix(prefix) not in ids
            ):
                known.discard(item.unique_id.removeprefix(prefix))
                registry.async_remove(item.entity_id)

    sync()
    entry.async_on_unload(coordinator.async_add_listener(sync))

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
"""Feeder settings kept by the pet REST backend: the camera and the desiccant.

Each change is the app's own request, and the feeder is read again
afterwards, also when the change was not confirmed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .feeder import FeederCoordinator, FeederEntity, apply_change
from .feeder_meals import today_in


class FeederCameraSwitch(FeederEntity, SwitchEntity):
    """The app's camera switch, device_active in HomeID."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:cctv"

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator, "feeder_camera")

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return (
            super().available
            and data is not None
            and data.camera_ok
            and data.camera is not None
        )

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.camera if data is not None else None

    async def _set(self, enabled: bool) -> None:
        await apply_change(
            self.coordinator,
            lambda manager: manager.set_camera(enabled),
            self.coordinator.async_request_refresh,
            "HomeID did not confirm the camera change",
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class FeederDesiccantButton(FeederEntity, ButtonEntity):
    """Record today as the day the desiccant bag was replaced."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restore"

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator, "feeder_desiccant_reset")

    async def async_press(self) -> None:
        # The app sends a day picked on the phone; today in the home's zone,
        # or HA's when HomeID gives none, is the nearest. The zone is the one
        # of the manager that sends it, never one from an older poll. A zone
        # that is not an IANA name is refused before anything is sent, as for
        # the meals.
        coordinator = self.coordinator
        await apply_change(
            coordinator,
            lambda manager: manager.reset_desiccant(
                today_in(coordinator.home_zone(manager))
            ),
            coordinator.async_request_refresh,
            "HomeID did not confirm the desiccant reset",
        )


async def async_setup_settings_switches(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.has_camera:
        async_add_entities([FeederCameraSwitch(coordinator)])


async def async_setup_settings_buttons(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FeederDesiccantButton(coordinator)])

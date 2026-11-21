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
"""Feeder settings: the camera and the desiccant, kept by the pet REST
backend, and the motion sensitivity, high contrast mode and volume data
points, kept by Tuya.

Each change is the app's own request. After a REST change the feeder is read
again, also when the change was not confirmed. A data point Tuya confirmed is
shown as sent until the next poll, as the app does.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .feeder import (
    FeederCoordinator,
    FeederEntity,
    apply_change,
    pet_call,
    tuya_change,
)
from .feeder_feed import (
    CONTRAST_DP,
    MAX_VOLUME,
    MIN_VOLUME,
    MOTION_DP,
    MOTION_LEVELS,
    VOLUME_DP,
    volume_dp,
)
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


class FeederSettingEntity(FeederEntity):
    """A settings data point, sent through the feed's Tuya session."""

    _attr_entity_category = EntityCategory.CONFIG
    # The DpSettings field shown.
    _field = ""
    # The app only lets it change while the camera is on.
    _needs_camera = False

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return (
            super().available
            and self._current is not None
            and (not self._needs_camera or (data is not None and data.camera is True))
        )

    @property
    def _current(self) -> Any:
        data = self.coordinator.data
        if data is None or data.settings is None:
            return None
        return getattr(data.settings, self._field)

    async def _send(self, dps: dict[str, int | str], **changes: Any) -> None:
        coordinator = self.coordinator
        feed = coordinator.feed
        if feed is None:
            raise ServiceValidationError("This feeder has no Tuya settings")
        # Waits for a camera change in progress and its read back.
        async with coordinator.changes:
            if self._needs_camera:
                await self._camera_on()
            await tuya_change(
                feed.publish(dps), "setting", "Tuya did not confirm the setting change"
            )
            coordinator.settings_written(**changes)

    async def _camera_on(self) -> None:
        # The camera may have been turned off since the last poll, so it is
        # read again, as HomeID holds it now.
        manager = await pet_call(self.coordinator.meal_manager())
        if await pet_call(manager.camera()) is not True:
            raise ServiceValidationError("Turn the feeder's camera on first")


class FeederMotionSelect(FeederSettingEntity, SelectEntity):
    """Motion detection sensitivity, data point 106."""

    _attr_icon = "mdi:motion-sensor"
    _field = "motion"
    _needs_camera = True

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator, "feeder_motion_sensitivity")
        self._attr_options = list(MOTION_LEVELS.values())

    @property
    def current_option(self) -> str | None:
        return self._current

    async def async_select_option(self, option: str) -> None:
        for dp, level in MOTION_LEVELS.items():
            if level == option:
                await self._send({MOTION_DP: dp}, motion=option)
                return
        raise ServiceValidationError("Unknown motion sensitivity")


class FeederHighContrastSwitch(FeederSettingEntity, SwitchEntity):
    """High contrast mode, data point 108: on is the app's AUTO."""

    _attr_icon = "mdi:contrast-box"
    _field = "high_contrast"
    _needs_camera = True

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator, "feeder_high_contrast")

    @property
    def is_on(self) -> bool | None:
        return self._current

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send({CONTRAST_DP: "0"}, high_contrast=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send({CONTRAST_DP: "1"}, high_contrast=False)


class FeederVolumeNumber(FeederSettingEntity, NumberEntity):
    """Speaker volume, data point 231, on the app's 1 to 10 scale."""

    _attr_icon = "mdi:volume-high"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = MIN_VOLUME
    _attr_native_max_value = MAX_VOLUME
    _attr_native_step = 1
    _field = "volume"

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator, "feeder_volume")

    @property
    def native_value(self) -> float | None:
        return self._current

    async def async_set_native_value(self, value: float) -> None:
        if not (value.is_integer() and MIN_VOLUME <= value <= MAX_VOLUME):
            raise ServiceValidationError(
                f"Choose a volume from {MIN_VOLUME} to {MAX_VOLUME}"
            )
        volume = int(value)
        await self._send({VOLUME_DP: volume_dp(volume)}, volume=volume)


async def async_setup_settings_switches(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.has_camera:
        async_add_entities([FeederCameraSwitch(coordinator)])
    if coordinator.has_settings:
        async_add_entities([FeederHighContrastSwitch(coordinator)])


async def async_setup_settings_selects(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.has_settings:
        async_add_entities([FeederMotionSelect(coordinator)])


async def async_setup_settings_numbers(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.has_settings:
        async_add_entities([FeederVolumeNumber(coordinator)])


async def async_setup_settings_buttons(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FeederDesiccantButton(coordinator)])

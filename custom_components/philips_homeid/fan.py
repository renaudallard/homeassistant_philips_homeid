# Copyright (c) 2025, Renaud Allard <renaud@allard.it>
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
"""Fan platform for Philips HomeID air purifiers."""
from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.fan import (
    FanEntity,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    int_states_in_range,
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from .const import DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .entity import PhilipsHomeIDEntity

_LOGGER = logging.getLogger(__name__)

# Speed range for fan (typically 1-3 or 1-4 for Philips air purifiers)
SPEED_RANGE = (1, 3)

# Preset modes for air purifiers
# A=Auto, M=Manual, S=Sleep, T=Turbo, AG=Allergen
PRESET_MODE_MAP = {
    "A": "auto",
    "M": "manual",
    "S": "sleep",
    "T": "turbo",
    "AG": "allergen",
    "B": "bacteria",
    "N": "night",
}
PRESET_MODE_REVERSE = {v: k for k, v in PRESET_MODE_MAP.items()}
PRESET_MODES = list(PRESET_MODE_MAP.values())


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up fans from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [PhilipsAirPurifierFan(coordinator, coordinator.device_id)]
    async_add_entities(entities)


class PhilipsAirPurifierFan(PhilipsHomeIDEntity, FanEntity):
    """Representation of a Philips Air Purifier as a fan."""

    _attr_translation_key = "air_purifier"
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_speed_count = int_states_in_range(SPEED_RANGE)
    _attr_preset_modes = PRESET_MODES

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize the fan."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_fan"

    @property
    def is_on(self) -> bool | None:
        """Return true if the fan is on."""
        state = self.device_state
        if state:
            return state.power_on
        return None

    @property
    def percentage(self) -> int | None:
        """Return the current speed percentage."""
        state = self.device_state
        if not state or not state.power_on:
            return 0

        # Get fan speed from properties (om = operation mode)
        speed = state.properties.get("om")
        if speed is None:
            return None

        # Handle preset modes (non-numeric values)
        if isinstance(speed, str):
            if speed in PRESET_MODE_MAP:
                return None  # Preset mode active
            try:
                speed = int(speed)
            except ValueError:
                return None

        return ranged_value_to_percentage(SPEED_RANGE, speed)

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        state = self.device_state
        if not state:
            return None

        mode = state.properties.get("mode")
        if mode and mode in PRESET_MODE_MAP:
            return PRESET_MODE_MAP[mode]

        return None

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        await self.coordinator.async_set_power(True)

        if percentage is not None:
            await self.async_set_percentage(percentage)
        elif preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        await self.coordinator.async_set_power(False)

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan."""
        if percentage == 0:
            await self.async_turn_off()
            return

        speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
        await self.coordinator.async_set_fan_speed(str(speed))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        if preset_mode in PRESET_MODE_REVERSE:
            mode = PRESET_MODE_REVERSE[preset_mode]
            await self.coordinator.async_set_mode(mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        state = self.device_state
        if not state:
            return {}

        attrs = {}
        props = state.properties

        # Air quality metrics
        if "pm25" in props:
            attrs["pm25"] = props["pm25"]
        if "iaql" in props:
            attrs["air_quality_index"] = props["iaql"]
        if "rh" in props:
            attrs["humidity"] = props["rh"]
        if "temp" in props:
            attrs["temperature"] = props["temp"]

        # Mode info
        if "mode" in props:
            attrs["mode"] = props["mode"]
        if "om" in props:
            attrs["fan_speed"] = props["om"]

        return attrs

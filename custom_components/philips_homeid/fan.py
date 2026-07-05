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
from .sensor import get_device_type

_LOGGER = logging.getLogger(__name__)

# Speed range for legacy (py-air-control style) air purifiers.
SPEED_RANGE = (1, 3)

# Preset modes for legacy air purifiers.
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

# MUJI (FUSION) air purifier D-codes (APK MUJI AirStatusPort / AirControlPort).
# D0310C = operationMode (read + write), D0310D = fanSpeed (read; 0 = off).
MUJI_MODE_KEY = "D0310C"
MUJI_POWER_KEY = "D0310D"

# operationMode (D0310C) integer values per model family. Verified against the
# decompiled APK enums MujiOperationMode (AC0650) and MujiPlusOperationMode
# (AC0651); AC1715 adds a "fast" step.
MUJI_MODE_MAPS: dict[str, dict[str, int]] = {
    "AC0650": {"gentle": 1, "sleep": 17, "turbo": 18},
    "AC0651": {"auto": 0, "medium": 1, "sleep": 17, "turbo": 18},
    "AC1715": {"auto": 0, "medium": 1, "fast": 2, "sleep": 17, "turbo": 18},
}


def _muji_mode_map(model_name: str | None) -> dict[str, int] | None:
    """Return the MUJI operationMode map for a model, or None if not MUJI."""
    model_upper = (model_name or "").upper()
    for prefix, mode_map in MUJI_MODE_MAPS.items():
        if model_upper.startswith(prefix):
            return mode_map
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up fans from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Only create fan entity for air purifiers
    model_name = coordinator.device_info.model_name or ""
    if get_device_type(model_name) != "air_purifier":
        _LOGGER.debug("Skipping fan entity for non-air-purifier device: %s", model_name)
        return

    added = False

    def _create_if_ready() -> bool:
        """Create the fan once the device reports a mode/power property."""
        nonlocal added
        if added:
            return True
        # MUJI (FUSION) devices report D0310C/D0310D; legacy devices report
        # om/mode.
        if (
            coordinator.has_property(MUJI_MODE_KEY)
            or coordinator.has_property(MUJI_POWER_KEY)
            or coordinator.has_property("om")
            or coordinator.has_property("mode")
        ):
            added = True
            async_add_entities(
                [PhilipsAirPurifierFan(coordinator, coordinator.device_id)]
            )
            return True
        return False

    if not _create_if_ready():
        # FUSION NCP D-codes can arrive after setup; create the fan lazily.
        def handle_new_properties(new_properties: list[tuple[str, str | None]]) -> None:
            _create_if_ready()

        unregister = coordinator.register_new_property_callback(handle_new_properties)
        entry.async_on_unload(unregister)


class PhilipsAirPurifierFan(PhilipsHomeIDEntity, FanEntity):
    """Representation of a Philips Air Purifier as a fan."""

    _attr_translation_key = "air_purifier"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize the fan."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_fan"

        self._mode_map = _muji_mode_map(coordinator.device_info.model_name)
        self._mode_reverse = (
            {v: k for k, v in self._mode_map.items()} if self._mode_map else {}
        )
        # Treat any air purifier that reports the MUJI power D-code as MUJI, even
        # if its model is not in the mode table (control power, no preset modes).
        self._is_muji = self._mode_map is not None or coordinator.has_property(
            MUJI_POWER_KEY
        )

        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if self._is_muji:
            if self._mode_map:
                features |= FanEntityFeature.PRESET_MODE
                self._attr_preset_modes = list(self._mode_map)
        else:
            features |= FanEntityFeature.SET_SPEED | FanEntityFeature.PRESET_MODE
            self._attr_preset_modes = PRESET_MODES
            self._attr_speed_count = int_states_in_range(SPEED_RANGE)
        self._attr_supported_features = features

    @property
    def is_on(self) -> bool | None:
        """Return true if the fan is on."""
        state = self.device_state
        if not state:
            return None
        if self._is_muji:
            power = state.properties.get(MUJI_POWER_KEY)
            if power is None:
                return state.power_on
            try:
                return int(power) != 0
            except (TypeError, ValueError):
                return None
        return state.power_on

    @property
    def percentage(self) -> int | None:
        """Return the current speed percentage (legacy air purifiers only)."""
        if self._is_muji:
            return None

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

        if self._is_muji:
            raw = state.properties.get(MUJI_MODE_KEY)
            if raw is None:
                return None
            try:
                return self._mode_reverse.get(int(raw))
            except (TypeError, ValueError):
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
        """Set the speed percentage of the fan (legacy air purifiers only)."""
        if self._is_muji:
            return
        if percentage == 0:
            await self.async_turn_off()
            return

        speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
        await self.coordinator.async_set_fan_speed(str(speed))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        if self._is_muji:
            if self._mode_map and preset_mode in self._mode_map:
                # MUJI operationMode is written to the Control port (D0310C).
                await self.coordinator.async_set_control_property(
                    MUJI_MODE_KEY, self._mode_map[preset_mode]
                )
            return

        if preset_mode in PRESET_MODE_REVERSE:
            mode = PRESET_MODE_REVERSE[preset_mode]
            await self.coordinator.async_set_mode(mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        state = self.device_state
        if not state:
            return {}

        attrs: dict[str, Any] = {}
        props = state.properties

        if self._is_muji:
            if MUJI_MODE_KEY in props:
                attrs["operation_mode"] = props[MUJI_MODE_KEY]
            if MUJI_POWER_KEY in props:
                attrs["fan_speed"] = props[MUJI_POWER_KEY]
            return attrs

        # Legacy air quality metrics
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

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
"""Switch platform for Philips HomeID."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .entity import PhilipsHomeIDEntity
from .sensor import get_device_type

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    model_name = coordinator.device_info.model_name or ""
    device_type = get_device_type(model_name)

    entities: list[SwitchEntity] = []

    # Child lock only for air purifiers
    # Note: Airfryers don't have a power switch - they use start/stop buttons
    # Air purifiers use the fan entity for on/off control
    if device_type == "air_purifier":
        entities.append(
            PhilipsHomeIDChildLockSwitch(coordinator, coordinator.device_id)
        )

    # Preheat toggle for airfryers
    if device_type in ("airfryer", "airfryer_dual"):
        entities.append(PhilipsHomeIDPreheatSwitch(coordinator, coordinator.device_id))

    if entities:
        async_add_entities(entities)


class PhilipsHomeIDPowerSwitch(PhilipsHomeIDEntity, SwitchEntity):
    """Power switch for Philips HomeID devices."""

    _attr_translation_key = "power"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_power"

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        state = self.device_state
        if state:
            return state.power_on
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        await self.coordinator.async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        await self.coordinator.async_set_power(False)


class PhilipsHomeIDChildLockSwitch(PhilipsHomeIDEntity, SwitchEntity):
    """Child lock switch for Philips HomeID devices."""

    _attr_translation_key = "child_lock"
    _attr_icon = "mdi:lock-outline"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_child_lock"

    @property
    def is_on(self) -> bool | None:
        """Return true if child lock is enabled."""
        state = self.device_state
        if state:
            # Child lock property key is 'cl'
            return state.properties.get("cl", False)
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False
        state = self.device_state
        return state is not None and "cl" in state.properties

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable child lock."""
        await self.coordinator.async_set_child_lock(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable child lock."""
        await self.coordinator.async_set_child_lock(False)


class PhilipsHomeIDPreheatSwitch(PhilipsHomeIDEntity, SwitchEntity):
    """Preheat toggle for Philips airfryers.

    Controls whether preheat is enabled when the Start button is pressed.
    """

    _attr_translation_key = "preheat"
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_preheat"

    @property
    def is_on(self) -> bool:
        """Return true if preheat is enabled."""
        return self.coordinator.preheat_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable preheat."""
        self.coordinator.set_preheat_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable preheat."""
        self.coordinator.set_preheat_enabled(False)
        self.async_write_ha_state()

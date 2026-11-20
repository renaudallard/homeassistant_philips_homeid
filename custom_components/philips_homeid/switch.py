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
from collections.abc import Callable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_IS_FEEDER, CONF_IS_FUSION, DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .entity import PhilipsHomeIDEntity
from .fan import MUJI_AC17_MODELS, MUJI_PICO_MODELS
from .sensor import get_device_type

_LOGGER = logging.getLogger(__name__)

_MujiSwitchFactory = Callable[[PhilipsHomeIDCoordinator, str], SwitchEntity]

# AC07 child lock (Air+ app): D03103 reads 1 when locked, written as 1 or 0.
MUJI_CHILD_LOCK_KEY = "D03103"
# AC17 display light (Air+ app): on writes D03104 and D03105 both 100, off
# writes both 0, and it reads as on while D03105 is above 0.
MUJI_DISPLAY_LIGHT_KEY = "D03105"
MUJI_DISPLAY_LIGHT_LEVEL_KEY = "D03104"
MUJI_DISPLAY_LIGHT_ON = 100


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches from config entry."""
    if entry.data.get(CONF_IS_FEEDER):
        from .feeder_schedule import async_setup_feeder_switches

        await async_setup_feeder_switches(hass, entry, async_add_entities)
        return

    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    model_name = coordinator.device_info.model_name or ""
    device_type = get_device_type(model_name)
    is_fusion = bool(entry.data.get(CONF_IS_FUSION))
    is_airfryer = device_type in ("airfryer", "multicooker")

    entities: list[SwitchEntity] = []

    # Child lock only for air purifiers that report it
    if device_type == "air_purifier" and coordinator.has_property("cl"):
        entities.append(
            PhilipsHomeIDChildLockSwitch(coordinator, coordinator.device_id)
        )

    # Power switch for FUSION airfryers only (local HTTP devices don't need it).
    # Not gated on the Status port: an appliance that parked its NCP answers
    # no port read at all, and turning this switch on is what wakes it, so
    # gating it on port data left such a device with no entity to wake it
    # from (issue #40).
    if is_airfryer and is_fusion:
        entities.append(PhilipsHomeIDPowerSwitch(coordinator, coordinator.device_id))

    # Power switch for Rita espresso machines (FUSION shadow powerOn)
    if device_type == "espresso" and is_fusion:
        entities.append(PhilipsHomeIDPowerSwitch(coordinator, coordinator.device_id))

    # Power switch for local EP/SM espresso machines (command port power enum)
    if device_type == "espresso" and not is_fusion:
        entities.append(PhilipsHomeIDPowerSwitch(coordinator, coordinator.device_id))

    # Preheat toggle for airfryers
    preheat_created = is_airfryer and coordinator.has_property("status", "airfryer")
    if preheat_created:
        entities.append(PhilipsHomeIDPreheatSwitch(coordinator, coordinator.device_id))

    # MUJI switches, each created once the D-code it reads is known: the
    # Status port can answer after platform setup.
    muji_pending: list[tuple[str, _MujiSwitchFactory]] = []
    if device_type == "air_purifier":
        muji_pending.append(("D03134", PhilipsHomeIDSensorMonitorSwitch))
        if model_name.upper().startswith(MUJI_PICO_MODELS):
            muji_pending.append((MUJI_CHILD_LOCK_KEY, PhilipsHomeIDMujiChildLockSwitch))
        if model_name.upper().startswith(MUJI_AC17_MODELS):
            muji_pending.append(
                (MUJI_DISPLAY_LIGHT_KEY, PhilipsHomeIDMujiDisplayLightSwitch)
            )

    def _make_muji_switches() -> list[SwitchEntity]:
        ready = [p for p in muji_pending if coordinator.has_property(p[0])]
        for p in ready:
            muji_pending.remove(p)
        return [factory(coordinator, coordinator.device_id) for _, factory in ready]

    entities.extend(_make_muji_switches())

    _LOGGER.debug(
        "Setting up switches for device type: %s (model: %s), count: %d",
        device_type,
        model_name,
        len(entities),
    )
    if entities:
        async_add_entities(entities)

    # Dynamic creation for the preheat toggle when the Status port answers late
    if is_airfryer and not preheat_created:
        created = False

        def handle_new_properties(
            new_properties: list[tuple[str, str | None]],
        ) -> None:
            nonlocal created
            if created:
                return
            for prop_key, nested_key in new_properties:
                if prop_key == "status" and nested_key == "airfryer":
                    created = True
                    _LOGGER.debug(
                        "Creating preheat switch for newly discovered airfryer"
                    )
                    async_add_entities(
                        [
                            PhilipsHomeIDPreheatSwitch(
                                coordinator, coordinator.device_id
                            )
                        ],
                        update_before_add=True,
                    )
                    return

        unregister = coordinator.register_new_property_callback(handle_new_properties)
        entry.async_on_unload(unregister)

    if muji_pending:

        def handle_new_muji_switch(
            new_properties: list[tuple[str, str | None]],
        ) -> None:
            switches = _make_muji_switches()
            if switches:
                async_add_entities(switches)

        unregister = coordinator.register_new_property_callback(handle_new_muji_switch)
        entry.async_on_unload(unregister)


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


class PhilipsHomeIDPowerSwitch(PhilipsHomeIDEntity, SwitchEntity):
    """Power switch for Philips airfryers."""

    _attr_translation_key = "power"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_power"

    @property
    def is_on(self) -> bool | None:
        """Return true if the device is powered on."""
        state = self.device_state
        if state:
            return state.power_on
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the device."""
        await self.coordinator.async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the device."""
        await self.coordinator.async_set_power(False)


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


class PhilipsHomeIDSensorMonitorSwitch(PhilipsHomeIDEntity, SwitchEntity):
    """Sensor monitor in standby switch for MUJI air purifiers."""

    _attr_translation_key = "sensor_monitor_standby"
    _attr_icon = "mdi:eye"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_sensor_monitor_standby"

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor monitor in standby is enabled."""
        state = self.device_state
        if state:
            return bool(state.properties.get("D03134", 0))
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False
        state = self.device_state
        return state is not None and "D03134" in state.properties

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable sensor monitor in standby."""
        await self.coordinator.async_set_control_property("D03134", 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable sensor monitor in standby."""
        await self.coordinator.async_set_control_property("D03134", 0)


class PhilipsHomeIDMujiChildLockSwitch(PhilipsHomeIDEntity, SwitchEntity):
    """Child lock switch for AC07 air purifiers (D03103)."""

    _attr_translation_key = "child_lock"
    _attr_icon = "mdi:lock-outline"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_child_lock"

    @property
    def is_on(self) -> bool | None:
        """Return true if the child lock is on."""
        state = self.device_state
        if not state or MUJI_CHILD_LOCK_KEY not in state.properties:
            return None
        return state.properties[MUJI_CHILD_LOCK_KEY] == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the controls."""
        await self.coordinator.async_set_control_property(MUJI_CHILD_LOCK_KEY, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the controls."""
        await self.coordinator.async_set_control_property(MUJI_CHILD_LOCK_KEY, 0)


class PhilipsHomeIDMujiDisplayLightSwitch(PhilipsHomeIDEntity, SwitchEntity):
    """Display light switch for AC17 air purifiers."""

    _attr_translation_key = "display_light"
    _attr_icon = "mdi:lightbulb-outline"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_display_light"

    @property
    def is_on(self) -> bool | None:
        """Return true if the display light is on."""
        state = self.device_state
        if not state:
            return None
        value = state.properties.get(MUJI_DISPLAY_LIGHT_KEY)
        if not isinstance(value, int | float):
            return None
        return value > 0

    async def _write(self, value: int) -> None:
        await self.coordinator.async_set_control_properties(
            {MUJI_DISPLAY_LIGHT_LEVEL_KEY: value, MUJI_DISPLAY_LIGHT_KEY: value}
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the display light on."""
        await self._write(MUJI_DISPLAY_LIGHT_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the display light off."""
        await self._write(0)

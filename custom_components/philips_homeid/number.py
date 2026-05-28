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
"""Number platform for Philips HomeID."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .entity import PhilipsHomeIDEntity
from .sensor import get_device_type

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhilipsHomeIDNumberEntityDescription(NumberEntityDescription):
    """Describes Philips HomeID number entity."""

    property_key: str | None = None
    nested_key: str | None = None  # For nested properties like airfryer.temp
    set_fn: (
        Callable[[PhilipsHomeIDCoordinator, float], Coroutine[Any, Any, bool]] | None
    ) = None
    available_key: str | None = None  # Key to check for availability


# Airfryer number entities
AIRFRYER_NUMBERS: tuple[PhilipsHomeIDNumberEntityDescription, ...] = (
    PhilipsHomeIDNumberEntityDescription(
        key="airfryer_set_temperature",
        translation_key="airfryer_set_temperature",
        property_key="temp",
        nested_key="airfryer",
        native_min_value=40,
        native_max_value=200,
        native_step=5,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        icon="mdi:thermometer",
        mode=NumberMode.SLIDER,
        set_fn=lambda c, v: c.async_airfryer_update_settings(temp=int(v)),
        available_key="airfryer",
    ),
    PhilipsHomeIDNumberEntityDescription(
        key="airfryer_set_time",
        translation_key="airfryer_set_time",
        property_key="time",
        nested_key="airfryer",
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:timer",
        mode=NumberMode.BOX,
        set_fn=lambda c, v: c.async_airfryer_update_settings(time_seconds=int(v * 60)),
        available_key="airfryer",
    ),
    PhilipsHomeIDNumberEntityDescription(
        key="airfryer_set_airspeed",
        translation_key="airfryer_set_airspeed",
        property_key="airspeed",
        nested_key="airfryer",
        native_min_value=1,
        native_max_value=2,
        native_step=1,
        icon="mdi:fan",
        mode=NumberMode.BOX,
        set_fn=lambda c, v: c.async_airfryer_set_settings(airspeed=int(v)),
        available_key="airfryer",
    ),
    PhilipsHomeIDNumberEntityDescription(
        key="airfryer_set_probe_temperature",
        translation_key="airfryer_set_probe_temperature",
        property_key="temp_probe",
        nested_key="airfryer",
        native_min_value=40,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        icon="mdi:thermometer-probe",
        mode=NumberMode.BOX,
        set_fn=lambda c, v: c.async_airfryer_set_settings(probe_temp=int(v)),
        available_key="airfryer",
    ),
)

# MUJI air purifier number entities (hex-key properties)
MUJI_NUMBERS: tuple[PhilipsHomeIDNumberEntityDescription, ...] = (
    PhilipsHomeIDNumberEntityDescription(
        key="set_beep_volume",
        translation_key="set_beep_volume",
        property_key="D03130",
        native_min_value=0,
        native_max_value=3,
        native_step=1,
        icon="mdi:volume-medium",
        mode=NumberMode.BOX,
        set_fn=lambda c, v: c.async_set_status_property("D03130", int(v)),
    ),
    PhilipsHomeIDNumberEntityDescription(
        key="set_air_quality_threshold",
        translation_key="set_air_quality_threshold",
        property_key="D0312C",
        native_min_value=1,
        native_max_value=4,
        native_step=1,
        icon="mdi:air-filter",
        mode=NumberMode.BOX,
        set_fn=lambda c, v: c.async_set_status_property("D0312C", int(v)),
    ),
)

# Espresso machine number entities (recipe settings)
# Property names from APK BasicRecipePortProperties
ESPRESSO_NUMBERS: tuple[PhilipsHomeIDNumberEntityDescription, ...] = (
    PhilipsHomeIDNumberEntityDescription(
        key="espresso_grinder_dose",
        translation_key="espresso_grinder_dose",
        property_key="GrDose",
        nested_key="basicrecipe",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        icon="mdi:coffee-maker-outline",
        mode=NumberMode.BOX,
        set_fn=lambda c, v: c.async_set_port_property("basicrecipe", "GrDose", int(v)),
        available_key="basicrecipe",
    ),
    PhilipsHomeIDNumberEntityDescription(
        key="espresso_brew_temperature",
        translation_key="espresso_brew_temperature",
        property_key="Temperature",
        nested_key="basicrecipe",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        icon="mdi:thermometer",
        mode=NumberMode.BOX,
        set_fn=lambda c, v: c.async_set_port_property(
            "basicrecipe", "Temperature", int(v)
        ),
        available_key="basicrecipe",
    ),
    PhilipsHomeIDNumberEntityDescription(
        key="espresso_brew_count",
        translation_key="espresso_brew_count",
        property_key="NrOfBrews",
        nested_key="basicrecipe",
        native_min_value=1,
        native_max_value=4,
        native_step=1,
        icon="mdi:counter",
        mode=NumberMode.BOX,
        set_fn=lambda c, v: c.async_set_port_property(
            "basicrecipe", "NrOfBrews", int(v)
        ),
        available_key="basicrecipe",
    ),
    PhilipsHomeIDNumberEntityDescription(
        key="espresso_primary_dose",
        translation_key="espresso_primary_dose",
        property_key="PrimDose",
        nested_key="basicrecipe",
        native_min_value=0,
        native_max_value=500,
        native_step=1,
        icon="mdi:cup-water",
        mode=NumberMode.BOX,
        set_fn=lambda c, v: c.async_set_port_property(
            "basicrecipe", "PrimDose", int(v)
        ),
        available_key="basicrecipe",
    ),
    PhilipsHomeIDNumberEntityDescription(
        key="espresso_secondary_dose",
        translation_key="espresso_secondary_dose",
        property_key="SecDose",
        nested_key="basicrecipe",
        native_min_value=0,
        native_max_value=500,
        native_step=1,
        icon="mdi:cup",
        mode=NumberMode.BOX,
        set_fn=lambda c, v: c.async_set_port_property("basicrecipe", "SecDose", int(v)),
        available_key="basicrecipe",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    model_name = coordinator.device_info.model_name or ""
    device_type = get_device_type(model_name)

    entities: list[NumberEntity] = []

    def _create_airfryer_numbers() -> list[NumberEntity]:
        nums: list[NumberEntity] = []
        for description in AIRFRYER_NUMBERS:
            if coordinator.has_property(
                description.property_key, description.nested_key
            ):
                nums.append(
                    PhilipsHomeIDNumber(coordinator, description, coordinator.device_id)
                )
        nums.append(PhilipsHomeIDKeepWarmTimeNumber(coordinator, coordinator.device_id))
        nums.append(PhilipsHomeIDKeepWarmTempNumber(coordinator, coordinator.device_id))
        return nums

    if device_type in ("airfryer", "airfryer_dual", "multicooker"):
        if coordinator.has_property("status", "airfryer"):
            entities.extend(_create_airfryer_numbers())
        else:
            # Dynamic creation when airfryer properties arrive late
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
                        _LOGGER.info("Creating numbers for newly discovered airfryer")
                        async_add_entities(
                            _create_airfryer_numbers(), update_before_add=True
                        )
                        return

            unregister = coordinator.register_new_property_callback(
                handle_new_properties
            )
            entry.async_on_unload(unregister)

    elif device_type == "espresso":
        seen_keys: set[str] = set()
        for description in ESPRESSO_NUMBERS:
            if coordinator.has_property(
                description.property_key, description.nested_key
            ):
                entities.append(
                    PhilipsHomeIDNumber(coordinator, description, coordinator.device_id)
                )
                seen_keys.add(description.key)

        # FUSION espresso machines receive basicrecipe via NCP push after
        # MQTT connect, often arriving after entity setup finishes. Without
        # a dynamic-creation hook the recipe numbers are never created.
        if any(d.key not in seen_keys for d in ESPRESSO_NUMBERS):
            wanted = {
                (d.property_key, d.nested_key): d
                for d in ESPRESSO_NUMBERS
                if d.key not in seen_keys
            }

            def handle_new_espresso_properties(
                new_properties: list[tuple[str, str | None]],
            ) -> None:
                new_entities: list[NumberEntity] = []
                for prop_key, nested_key in new_properties:
                    description = wanted.pop((prop_key, nested_key), None)
                    if description is not None:
                        new_entities.append(
                            PhilipsHomeIDNumber(
                                coordinator, description, coordinator.device_id
                            )
                        )
                if new_entities:
                    async_add_entities(new_entities, update_before_add=True)

            unregister = coordinator.register_new_property_callback(
                handle_new_espresso_properties
            )
            entry.async_on_unload(unregister)
    elif device_type == "air_purifier":
        seen_muji: set[str] = set()
        for description in MUJI_NUMBERS:
            if coordinator.has_property(description.property_key):
                entities.append(
                    PhilipsHomeIDNumber(coordinator, description, coordinator.device_id)
                )
                seen_muji.add(description.key)

        # MUJI hex properties (D03130/D0312C) can arrive after entity
        # setup on first connect; register a callback so they appear
        # dynamically instead of being missed forever.
        if any(d.key not in seen_muji for d in MUJI_NUMBERS):
            muji_wanted: dict[
                tuple[str, str | None], PhilipsHomeIDNumberEntityDescription
            ] = {
                (d.property_key, None): d
                for d in MUJI_NUMBERS
                if d.key not in seen_muji and d.property_key is not None
            }

            def handle_new_muji_properties(
                new_properties: list[tuple[str, str | None]],
            ) -> None:
                new_entities: list[NumberEntity] = []
                for prop_key, nested_key in new_properties:
                    description = muji_wanted.pop((prop_key, nested_key), None)
                    if description is not None:
                        new_entities.append(
                            PhilipsHomeIDNumber(
                                coordinator, description, coordinator.device_id
                            )
                        )
                if new_entities:
                    async_add_entities(new_entities, update_before_add=True)

            unregister = coordinator.register_new_property_callback(
                handle_new_muji_properties
            )
            entry.async_on_unload(unregister)

    if entities:
        async_add_entities(entities)


class PhilipsHomeIDNumber(PhilipsHomeIDEntity, NumberEntity):
    """Number entity for Philips HomeID."""

    entity_description: PhilipsHomeIDNumberEntityDescription

    def __init__(
        self,
        coordinator: PhilipsHomeIDCoordinator,
        description: PhilipsHomeIDNumberEntityDescription,
        device_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> int | float | None:
        """Return the current value."""
        desc = self.entity_description
        value = self._get_property_value(desc.property_key, desc.nested_key)
        if value is None:
            return None
        # Convert time from seconds to minutes for display (int since min is 1 minute)
        if desc.key == "airfryer_set_time":
            return int(value) // 60
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        if self.entity_description.set_fn:
            await self.entity_description.set_fn(self.coordinator, value)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False
        if self.entity_description.available_key:
            return self._has_property(self.entity_description.available_key)
        return True


class PhilipsHomeIDKeepWarmTimeNumber(PhilipsHomeIDEntity, NumberEntity):
    """Keep warm duration setting."""

    _attr_translation_key = "set_keep_warm_time"
    _attr_icon = "mdi:timer"
    _attr_native_min_value = 1
    _attr_native_max_value = 180
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_set_keep_warm_time"

    @property
    def native_value(self) -> float:
        """Return the current value in minutes."""
        return self.coordinator.keep_warm_time // 60

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        self.coordinator.set_keep_warm_time(int(value) * 60)
        self.async_write_ha_state()


class PhilipsHomeIDKeepWarmTempNumber(PhilipsHomeIDEntity, NumberEntity):
    """Keep warm temperature setting."""

    _attr_translation_key = "set_keep_warm_temp"
    _attr_icon = "mdi:thermometer"
    _attr_native_min_value = 40
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_set_keep_warm_temp"

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return float(self.coordinator.keep_warm_temp)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        self.coordinator.set_keep_warm_temp(int(value))
        self.async_write_ha_state()

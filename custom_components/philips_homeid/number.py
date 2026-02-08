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
from typing import Callable, Coroutine, Any

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
        set_fn=lambda c, v: c.async_airfryer_set_settings(temp=int(v)),
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
        set_fn=lambda c, v: c.async_airfryer_set_settings(time_seconds=int(v * 60)),
        available_key="airfryer",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Only create number entities for airfryers
    model_name = coordinator.device_info.model_name or ""
    device_type = get_device_type(model_name)

    if device_type not in ("airfryer", "airfryer_dual"):
        _LOGGER.debug(
            "Skipping number entities for non-airfryer device: %s", model_name
        )
        return

    entities: list[PhilipsHomeIDNumber] = []

    # Add airfryer number entities
    for description in AIRFRYER_NUMBERS:
        entities.append(
            PhilipsHomeIDNumber(coordinator, description, coordinator.device_id)
        )

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

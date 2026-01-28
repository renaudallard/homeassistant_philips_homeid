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
"""Binary sensor platform for Philips HomeID."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .entity import PhilipsHomeIDEntity
from .sensor import get_device_type

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhilipsHomeIDBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes Philips HomeID binary sensor entity."""

    property_key: str | None = None
    nested_key: str | None = None  # For nested properties like airfryer.drawer_open
    # Device types this sensor applies to: air_purifier, airfryer, airfryer_dual
    device_types: tuple[str, ...] | None = None


# Air purifier binary sensors
AIR_PURIFIER_BINARY_SENSORS: tuple[PhilipsHomeIDBinarySensorEntityDescription, ...] = (
    PhilipsHomeIDBinarySensorEntityDescription(
        key="filter_replace_required",
        translation_key="filter_replace_required",
        property_key="fltt1",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:air-filter",
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="water_tank_empty",
        translation_key="water_tank_empty",
        property_key="wl",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:water-off",
        device_types=("air_purifier",),
    ),
)

# Airfryer binary sensors
AIRFRYER_BINARY_SENSORS: tuple[PhilipsHomeIDBinarySensorEntityDescription, ...] = (
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_drawer_open",
        translation_key="airfryer_drawer_open",
        property_key="drawer_open",
        nested_key="airfryer",
        device_class=BinarySensorDeviceClass.DOOR,
        icon="mdi:tray",
        device_types=("airfryer",),  # Single basket only
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_shake_reminder",
        translation_key="airfryer_shake_reminder",
        property_key="shake",
        nested_key="airfryer",
        icon="mdi:hand-wave",
        device_types=("airfryer", "airfryer_dual"),
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_flip_reminder",
        translation_key="airfryer_flip_reminder",
        property_key="flip",
        nested_key="airfryer",
        icon="mdi:rotate-3d-variant",
        device_types=("airfryer", "airfryer_dual"),
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_preheat_active",
        translation_key="airfryer_preheat_active",
        property_key="preheat_active",
        nested_key="airfryer",
        icon="mdi:fire",
        device_types=("airfryer", "airfryer_dual"),
    ),
    # Dual basket sensors
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_left_drawer_open",
        translation_key="airfryer_left_drawer_open",
        property_key="drawer_open_l",
        nested_key="airfryer",
        device_class=BinarySensorDeviceClass.DOOR,
        icon="mdi:tray-arrow-up",
        device_types=("airfryer_dual",),
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_right_drawer_open",
        translation_key="airfryer_right_drawer_open",
        property_key="drawer_open_r",
        nested_key="airfryer",
        device_class=BinarySensorDeviceClass.DOOR,
        icon="mdi:tray-arrow-down",
        device_types=("airfryer_dual",),
    ),
)

# All binary sensors combined
BINARY_SENSORS = AIR_PURIFIER_BINARY_SENSORS + AIRFRYER_BINARY_SENSORS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Determine device type from model name
    model_name = coordinator.device_info.model_name or ""
    device_type = get_device_type(model_name)

    _LOGGER.debug("Setting up binary sensors for device type: %s (model: %s)", device_type, model_name)

    entities: list[PhilipsHomeIDBinarySensor] = []

    # Only add binary sensors that match the device type AND have data
    for description in BINARY_SENSORS:
        # If sensor has device_types defined, check if current device matches
        if description.device_types is not None:
            if device_type not in description.device_types:
                continue

        # Only create sensor if the property exists in device state
        if not coordinator.has_property(description.property_key, description.nested_key):
            _LOGGER.debug(
                "Skipping binary sensor %s - property %s not found in device state",
                description.key,
                description.property_key,
            )
            continue

        entities.append(PhilipsHomeIDBinarySensor(coordinator, description, coordinator.device_id))

    _LOGGER.info("Created %d binary sensors for %s", len(entities), model_name)
    async_add_entities(entities)


class PhilipsHomeIDBinarySensor(PhilipsHomeIDEntity, BinarySensorEntity):
    """Binary sensor entity for Philips HomeID."""

    entity_description: PhilipsHomeIDBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: PhilipsHomeIDCoordinator,
        description: PhilipsHomeIDBinarySensorEntityDescription,
        device_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        desc = self.entity_description
        value = self._get_property_value(desc.property_key, desc.nested_key)
        return bool(value) if value is not None else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False
        desc = self.entity_description
        return self._has_property(desc.property_key, desc.nested_key)

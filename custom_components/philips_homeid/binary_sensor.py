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
from homeassistant.const import EntityCategory
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
    # Device types this sensor applies to: air_purifier, airfryer, multicooker
    device_types: tuple[str, ...] | None = None
    # If True, invert the boolean value (problem when value is 0/False)
    invert: bool = False
    # Optional property to read the on/off value from when it differs from
    # property_key (which still drives creation and availability). Lets one
    # field keep the entity alive while another supplies its state, without
    # colliding with a different platform that reads that state field.
    state_key: str | None = None


# Air purifier binary sensors
AIR_PURIFIER_BINARY_SENSORS: tuple[PhilipsHomeIDBinarySensorEntityDescription, ...] = (
    PhilipsHomeIDBinarySensorEntityDescription(
        key="filter_replace_required",
        translation_key="filter_replace_required",
        # Created and kept available by fltt1 (the HEPA filter type label,
        # always present on the filter port), but the replace-required state
        # comes from fltsts1, the HEPA filter life remaining: 0 = replace
        # required. Keying creation on fltt1 rather than fltsts1 avoids
        # colliding with the filter_hepa sensor, which also reads fltsts1, in
        # the shared seen-property tracker. fltt1 on its own is always truthy
        # and could never signal a replacement.
        property_key="fltt1",
        state_key="fltsts1",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:air-filter",
        device_types=("air_purifier",),
        invert=True,
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="water_tank_empty",
        translation_key="water_tank_empty",
        property_key="wl",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:water-off",
        device_types=("air_purifier",),
        # wl is water level percentage; 0 = empty = problem
        invert=True,
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
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_flip_reminder",
        translation_key="airfryer_flip_reminder",
        property_key="flip",
        nested_key="airfryer",
        icon="mdi:rotate-3d-variant",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_preheat_active",
        translation_key="airfryer_preheat_active",
        property_key="preheat_active",
        nested_key="airfryer",
        icon="mdi:fire",
        device_types=("airfryer", "multicooker"),
    ),
    # Venus-only sensors (HD9875, HD9876, HD9880)
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_probe_unplugged",
        translation_key="airfryer_probe_unplugged",
        property_key="probe_unplugged",
        nested_key="airfryer",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:thermometer-probe-off",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_probe_required",
        translation_key="airfryer_probe_required",
        property_key="probe_required",
        nested_key="airfryer",
        icon="mdi:thermometer-probe",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_resting",
        translation_key="airfryer_resting",
        property_key="resting",
        nested_key="airfryer",
        icon="mdi:sleep",
        device_types=("airfryer", "multicooker"),
    ),
    # Multicooker-specific sensors (Nutrimax NX0960, Hermes NX0950)
    PhilipsHomeIDBinarySensorEntityDescription(
        key="multicooker_lid_open",
        translation_key="multicooker_lid_open",
        property_key="lid_open",
        nested_key="airfryer",
        device_class=BinarySensorDeviceClass.DOOR,
        icon="mdi:pot-steam-outline",
        device_types=("multicooker",),
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="multicooker_no_water",
        translation_key="multicooker_no_water",
        property_key="no_water",
        nested_key="airfryer",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:water-off",
        device_types=("multicooker",),
    ),
    # Dual basket sensors
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_left_drawer_open",
        translation_key="airfryer_left_drawer_open",
        property_key="drawer_open_l",
        nested_key="airfryer",
        device_class=BinarySensorDeviceClass.DOOR,
        icon="mdi:tray-arrow-up",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDBinarySensorEntityDescription(
        key="airfryer_right_drawer_open",
        translation_key="airfryer_right_drawer_open",
        property_key="drawer_open_r",
        nested_key="airfryer",
        device_class=BinarySensorDeviceClass.DOOR,
        icon="mdi:tray-arrow-down",
        device_types=("airfryer", "multicooker"),
    ),
)

# Temperature unit sensor (shared across device types)
COMMON_BINARY_SENSORS: tuple[PhilipsHomeIDBinarySensorEntityDescription, ...] = (
    PhilipsHomeIDBinarySensorEntityDescription(
        key="temp_unit_fahrenheit",
        translation_key="temp_unit_fahrenheit",
        property_key="temp_unit",
        nested_key="airfryer",
        icon="mdi:temperature-fahrenheit",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "multicooker"),
    ),
)

# All binary sensors combined
BINARY_SENSORS = (
    AIR_PURIFIER_BINARY_SENSORS + AIRFRYER_BINARY_SENSORS + COMMON_BINARY_SENSORS
)


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

    _LOGGER.debug(
        "Setting up binary sensors for device type: %s (model: %s)",
        device_type,
        model_name,
    )

    # Build a mapping of property keys to sensor descriptions for this device type
    property_to_description: dict[str, PhilipsHomeIDBinarySensorEntityDescription] = {}
    for description in BINARY_SENSORS:
        if description.device_types is not None:
            if device_type not in description.device_types:
                continue
        if description.property_key:
            key = coordinator.get_property_key(
                description.property_key, description.nested_key
            )
            property_to_description[key] = description

    entities: list[PhilipsHomeIDBinarySensor] = []
    # Track property keys this platform has created, so dynamic creation dedups
    # per-platform. The coordinator's shared seen-set would let another platform
    # that reads the same property (binary_sensor water_tank_empty and sensor
    # water_level both use "wl") suppress this platform's entity.
    created_keys: set[str] = set()

    # Only add binary sensors that match the device type AND have data
    for description in BINARY_SENSORS:
        # If sensor has device_types defined, check if current device matches
        if description.device_types is not None:
            if device_type not in description.device_types:
                continue

        # Only create sensor if the property exists in device state
        if not coordinator.has_property(
            description.property_key, description.nested_key
        ):
            _LOGGER.debug(
                "Skipping binary sensor %s - property %s not found in device state",
                description.key,
                description.property_key,
            )
            continue

        # Mark seen (coordinator dedup) and track locally for dynamic creation
        if description.property_key:
            coordinator.mark_property_seen(
                description.property_key, description.nested_key
            )
            created_keys.add(
                coordinator.get_property_key(
                    description.property_key, description.nested_key
                )
            )

        entities.append(
            PhilipsHomeIDBinarySensor(coordinator, description, coordinator.device_id)
        )

    _LOGGER.info("Created %d binary sensors for %s", len(entities), model_name)
    async_add_entities(entities)

    # Register callback for dynamic entity creation when new properties appear
    def handle_new_properties(new_properties: list[tuple[str, str | None]]) -> None:
        """Handle newly discovered properties by creating binary sensors."""
        new_entities: list[PhilipsHomeIDBinarySensor] = []

        for property_key, nested_key in new_properties:
            key = coordinator.get_property_key(property_key, nested_key)
            description = property_to_description.get(key)

            if description and key not in created_keys:
                _LOGGER.info(
                    "Creating binary sensor %s for newly discovered property %s",
                    description.key,
                    property_key,
                )
                coordinator.mark_property_seen(property_key, nested_key)
                created_keys.add(key)
                new_entities.append(
                    PhilipsHomeIDBinarySensor(
                        coordinator, description, coordinator.device_id
                    )
                )

        if new_entities:
            async_add_entities(new_entities)

    # Register the callback
    unregister = coordinator.register_new_property_callback(handle_new_properties)

    # Store unregister function for cleanup
    entry.async_on_unload(unregister)


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
        value = self._get_property_value(
            desc.state_key or desc.property_key, desc.nested_key
        )
        if value is None:
            return None
        result = bool(value)
        return not result if desc.invert else result

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False
        desc = self.entity_description
        return self._has_property(desc.property_key, desc.nested_key)

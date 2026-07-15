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
"""Sensor platform for Philips HomeID."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .entity import PhilipsHomeIDEntity

# Re-export for other modules that import from sensor
from .sensor_descriptions import (  # noqa: F401
    SENSORS,
    PhilipsHomeIDSensorEntityDescription,
    get_device_type,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Determine device type from model name
    model_name = coordinator.device_info.model_name or ""
    device_type = get_device_type(model_name)

    _LOGGER.debug(
        "Setting up sensors for device type: %s (model: %s)", device_type, model_name
    )

    # Build a mapping of property keys to sensor descriptions for this device type
    property_to_description: dict[str, PhilipsHomeIDSensorEntityDescription] = {}
    for description in SENSORS:
        if description.device_types is not None:
            if device_type not in description.device_types:
                continue
        if description.property_key:
            key = coordinator.get_property_key(
                description.property_key, description.nested_key
            )
            property_to_description[key] = description

    entities: list[PhilipsHomeIDSensor] = []
    # Track property keys this platform has created, so dynamic creation dedups
    # per-platform. The coordinator's shared seen-set would let another platform
    # that reads the same property (sensor water_level and binary_sensor
    # water_tank_empty both use "wl") suppress this platform's entity.
    created_keys: set[str] = set()

    # Only add sensors that match the device type AND have data
    for description in SENSORS:
        # If sensor has device_types defined, check if current device matches
        if description.device_types is not None:
            if device_type not in description.device_types:
                continue

        # Only create sensor if the property exists in device state
        if not coordinator.has_property(
            description.property_key, description.nested_key
        ):
            _LOGGER.debug(
                "Skipping sensor %s - property %s not found in device state",
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
            PhilipsHomeIDSensor(coordinator, description, coordinator.device_id)
        )

    _LOGGER.info("Created %d sensors for %s", len(entities), model_name)
    async_add_entities(entities)

    # Register callback for dynamic entity creation when new properties appear
    def handle_new_properties(new_properties: list[tuple[str, str | None]]) -> None:
        """Handle newly discovered properties by creating sensors."""
        new_entities: list[PhilipsHomeIDSensor] = []

        for property_key, nested_key in new_properties:
            key = coordinator.get_property_key(property_key, nested_key)
            description = property_to_description.get(key)

            if description and key not in created_keys:
                _LOGGER.info(
                    "Creating sensor %s for newly discovered property %s",
                    description.key,
                    property_key,
                )
                coordinator.mark_property_seen(property_key, nested_key)
                created_keys.add(key)
                new_entities.append(
                    PhilipsHomeIDSensor(coordinator, description, coordinator.device_id)
                )

        if new_entities:
            async_add_entities(new_entities)

    # Register the callback
    unregister = coordinator.register_new_property_callback(handle_new_properties)

    # Store unregister function for cleanup
    entry.async_on_unload(unregister)


class PhilipsHomeIDSensor(PhilipsHomeIDEntity, SensorEntity):
    """Sensor entity for Philips HomeID."""

    entity_description: PhilipsHomeIDSensorEntityDescription

    def __init__(
        self,
        coordinator: PhilipsHomeIDCoordinator,
        description: PhilipsHomeIDSensorEntityDescription,
        device_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"
        self._countdown_unsub: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        await super().async_added_to_hass()
        if self.entity_description.extrapolate_countdown:
            self._update_countdown_timer()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is being removed from hass."""
        self._stop_countdown_timer()
        await super().async_will_remove_from_hass()

    def _start_countdown_timer(self) -> None:
        """Start the countdown timer to update every second."""
        if self._countdown_unsub is not None:
            return

        @callback
        def _update_countdown(_: Any) -> None:
            """Update the countdown value."""
            self.async_write_ha_state()

        self._countdown_unsub = async_track_time_interval(
            self.hass, _update_countdown, timedelta(seconds=1)
        )

    def _stop_countdown_timer(self) -> None:
        """Stop the countdown timer."""
        if self._countdown_unsub is not None:
            self._countdown_unsub()
            self._countdown_unsub = None

    def _update_countdown_timer(self) -> None:
        """Start or stop countdown timer based on cooking state."""
        if self.coordinator.is_airfryer_cooking():
            self._start_countdown_timer()
        else:
            self._stop_countdown_timer()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.entity_description.extrapolate_countdown:
            self._update_countdown_timer()
        super()._handle_coordinator_update()

    def _get_extrapolated_value(self, value: int) -> int:
        """Calculate extrapolated countdown value based on time since last poll."""
        if not self.coordinator.is_airfryer_cooking():
            return value

        last_update = self.coordinator.countdown_baseline
        if last_update == 0:
            return value

        elapsed = time.monotonic() - last_update
        extrapolated = value - int(elapsed)
        return max(0, extrapolated)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit the value is actually in."""
        if self.entity_description.device_temp_unit:
            return self.coordinator.airfryer_temperature_unit()
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        desc = self.entity_description
        value = self._get_property_value(desc.property_key, desc.nested_key)

        if value is not None and desc.extrapolate_countdown:
            try:
                value = self._get_extrapolated_value(int(value))
            except (ValueError, TypeError):
                pass

        if value is not None and desc.value_fn:
            return desc.value_fn(value)
        return value

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False
        desc = self.entity_description
        return self._has_property(desc.property_key, desc.nested_key)

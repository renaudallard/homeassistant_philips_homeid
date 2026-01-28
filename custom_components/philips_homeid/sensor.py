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
"""Sensor platform for Philips HomeID."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_BILLION,
    PERCENTAGE,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .entity import PhilipsHomeIDEntity

_LOGGER = logging.getLogger(__name__)


# Device type detection based on model name patterns
def get_device_type(model_name: str) -> str:
    """Determine device type from model name."""
    model_lower = (model_name or "").lower()

    # Air purifiers - AC series
    if model_lower.startswith("ac"):
        return "air_purifier"

    # Dual basket air fryers - HD9880 series
    if "hd988" in model_lower or "dual" in model_lower:
        return "airfryer_dual"

    # Air fryers - HD series (single basket)
    if model_lower.startswith("hd9") or "airfryer" in model_lower:
        return "airfryer"

    # Default: try to detect from data
    return "unknown"


@dataclass(frozen=True)
class PhilipsHomeIDSensorEntityDescription(SensorEntityDescription):
    """Describes Philips HomeID sensor entity."""

    property_key: str | None = None
    nested_key: str | None = None  # For nested properties like airfryer.status
    value_fn: Callable[[Any], Any] | None = None
    # Device types this sensor applies to: air_purifier, airfryer, airfryer_dual
    device_types: tuple[str, ...] | None = None
    # If True, extrapolate value based on time elapsed since last poll (for countdown timers)
    extrapolate_countdown: bool = False


def _seconds_to_minutes(value: Any) -> int | None:
    """Convert seconds to minutes."""
    if value is None:
        return None
    try:
        return int(value) // 60
    except (ValueError, TypeError):
        return None


# Air purifier sensors - based on local API response keys
AIR_PURIFIER_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="pm25",
        translation_key="pm25",
        property_key="pm25",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="pm1",
        translation_key="pm1",
        property_key="pm1",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM1,
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="pm10",
        translation_key="pm10",
        property_key="pm10",
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        device_class=SensorDeviceClass.PM10,
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="air_quality_index",
        translation_key="air_quality_index",
        property_key="iaql",
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="tvoc",
        translation_key="tvoc",
        property_key="tvoc",
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_BILLION,
        device_class=SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS,
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="gas",
        translation_key="gas",
        property_key="gas",
        icon="mdi:gas-cylinder",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="allergen_index",
        translation_key="allergen_index",
        property_key="aqit",
        icon="mdi:flower-pollen",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="humidity",
        translation_key="humidity",
        property_key="rh",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        property_key="temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="filter_pre",
        translation_key="filter_pre",
        property_key="fltsts0",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="filter_hepa",
        translation_key="filter_hepa",
        property_key="fltsts1",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="filter_carbon",
        translation_key="filter_carbon",
        property_key="fltsts2",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="filter_wick",
        translation_key="filter_wick",
        property_key="wicksts",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:water",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="water_level",
        translation_key="water_level",
        property_key="wl",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:water-percent",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="display_brightness",
        translation_key="display_brightness",
        property_key="uil",
        icon="mdi:brightness-6",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="runtime",
        translation_key="runtime",
        property_key="runtime",
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:clock-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda x: x // 3600 if x else None,  # Convert seconds to hours
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="mode",
        translation_key="mode",
        property_key="mode",
        icon="mdi:cog",
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="fan_speed",
        translation_key="fan_speed",
        property_key="om",
        icon="mdi:fan",
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="error_code",
        translation_key="error_code",
        property_key="err",
        icon="mdi:alert-circle",
        device_types=("air_purifier",),
    ),
)

# Airfryer sensors - common to single and dual basket
AIRFRYER_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_status",
        translation_key="airfryer_status",
        property_key="status",
        nested_key="airfryer",
        icon="mdi:stove",
        device_types=("airfryer", "airfryer_dual"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_temperature",
        translation_key="airfryer_temperature",
        property_key="temp",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        device_types=("airfryer",),  # Single basket only - dual uses temp_l/temp_r
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_current_temperature",
        translation_key="airfryer_current_temperature",
        property_key="cur_temp",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-check",
        device_types=("airfryer",),  # Only some models have current temp sensor
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_time_total",
        translation_key="airfryer_time_total",
        property_key="time",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer",
        value_fn=_seconds_to_minutes,
        device_types=("airfryer",),  # Single basket only - dual uses time_l/time_r
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_time_remaining",
        translation_key="airfryer_time_remaining",
        property_key="cur_time",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
        device_types=("airfryer", "airfryer_dual"),
        extrapolate_countdown=True,  # Extrapolate between polls when cooking
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_preset",
        translation_key="airfryer_preset",
        property_key="preset",
        nested_key="airfryer",
        icon="mdi:format-list-numbered",
        device_types=("airfryer", "airfryer_dual"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_recipe_name",
        translation_key="airfryer_recipe_name",
        property_key="recipeName",
        nested_key="airfryer",
        icon="mdi:food",
        device_types=("airfryer", "airfryer_dual"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_error",
        translation_key="airfryer_error",
        property_key="error",
        nested_key="airfryer",
        icon="mdi:alert-circle",
        device_types=("airfryer", "airfryer_dual"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_preheat_status",
        translation_key="airfryer_preheat_status",
        property_key="preheat",
        nested_key="airfryer",
        icon="mdi:fire",
        device_types=("airfryer", "airfryer_dual"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_keep_warm",
        translation_key="airfryer_keep_warm",
        property_key="keep_warm",
        nested_key="airfryer",
        icon="mdi:pot-steam",
        device_types=("airfryer", "airfryer_dual"),
    ),
    # Dual basket airfryer sensors (left basket)
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_left_status",
        translation_key="airfryer_left_status",
        property_key="status_l",
        nested_key="airfryer",
        icon="mdi:tray-arrow-up",
        device_types=("airfryer_dual",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_left_temperature",
        translation_key="airfryer_left_temperature",
        property_key="temp_l",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        device_types=("airfryer_dual",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_left_time",
        translation_key="airfryer_left_time",
        property_key="time_l",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer",
        value_fn=_seconds_to_minutes,
        device_types=("airfryer_dual",),
    ),
    # Dual basket airfryer sensors (right basket)
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_right_status",
        translation_key="airfryer_right_status",
        property_key="status_r",
        nested_key="airfryer",
        icon="mdi:tray-arrow-down",
        device_types=("airfryer_dual",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_right_temperature",
        translation_key="airfryer_right_temperature",
        property_key="temp_r",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        device_types=("airfryer_dual",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_right_time",
        translation_key="airfryer_right_time",
        property_key="time_r",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer",
        value_fn=_seconds_to_minutes,
        device_types=("airfryer_dual",),
    ),
)

# All sensors combined
SENSORS = AIR_PURIFIER_SENSORS + AIRFRYER_SENSORS


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

    _LOGGER.debug("Setting up sensors for device type: %s (model: %s)", device_type, model_name)

    entities: list[PhilipsHomeIDSensor] = []

    # Only add sensors that match the device type
    for description in SENSORS:
        # If sensor has device_types defined, check if current device matches
        if description.device_types is not None:
            if device_type not in description.device_types:
                continue

        entities.append(PhilipsHomeIDSensor(coordinator, description, coordinator.device_id))

    _LOGGER.info("Created %d sensors for %s", len(entities), model_name)
    async_add_entities(entities)


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

    def _get_extrapolated_value(self, value: int) -> int:
        """Calculate extrapolated countdown value based on time since last poll.

        Only extrapolates when airfryer is actively cooking (not paused).
        Returns the value capped at 0 (won't go negative).
        """
        if not self.coordinator.is_airfryer_cooking():
            # Not actively cooking, return raw value
            return value

        last_update = self.coordinator.last_update_time
        if last_update == 0:
            return value

        elapsed = time.monotonic() - last_update
        extrapolated = value - int(elapsed)
        return max(0, extrapolated)

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        desc = self.entity_description
        value = self._get_property_value(desc.property_key, desc.nested_key)

        # Apply extrapolation for countdown timers
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

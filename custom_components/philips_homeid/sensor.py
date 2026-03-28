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
from datetime import timedelta
from collections.abc import Callable
from typing import Any

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
    EntityCategory,
    PERCENTAGE,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

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

    # Air fryers - HD9 series and codenames (SPECTRE, VENUS 1, VENUS 2)
    if (
        model_lower.startswith("hd9")
        or "airfryer" in model_lower
        or "venus" in model_lower
        or "spectre" in model_lower
    ):
        return "airfryer"

    # Multicookers - NX series and codenames (Nutrimax, Hermes)
    if (
        model_lower.startswith("nx")
        or "nutrimax" in model_lower
        or "hermes" in model_lower
    ):
        return "multicooker"

    # Espresso machines - EP series and SM series
    if (
        model_lower.startswith("ep")
        or model_lower.startswith("sm")
        or "espresso" in model_lower
        or "coffee" in model_lower
    ):
        return "espresso"

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
        value_fn=lambda x: x // 3600 if x is not None else None,
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
    # MUJI air purifier sensors (hex-key properties, AC0650/AC0651)
    PhilipsHomeIDSensorEntityDescription(
        key="muji_filter0_lifetime",
        translation_key="muji_filter0_lifetime",
        property_key="D05207",
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="muji_filter1_lifetime",
        translation_key="muji_filter1_lifetime",
        property_key="D05408",
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="muji_filter0_remaining",
        translation_key="muji_filter0_remaining",
        property_key="D0520D",
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("air_purifier",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="muji_filter1_remaining",
        translation_key="muji_filter1_remaining",
        property_key="D0540E",
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
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
        device_types=("airfryer", "airfryer_dual", "multicooker"),
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
        device_types=(
            "airfryer",
            "multicooker",
        ),  # Single basket - dual uses temp_l/temp_r
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
        device_types=("airfryer", "multicooker"),  # Models with current temp sensor
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
        device_types=(
            "airfryer",
            "multicooker",
        ),  # Single basket - dual uses time_l/time_r
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
        device_types=("airfryer", "airfryer_dual", "multicooker"),
        extrapolate_countdown=True,  # Extrapolate between polls when cooking
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_preset",
        translation_key="airfryer_preset",
        property_key="preset",
        nested_key="airfryer",
        icon="mdi:format-list-numbered",
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_recipe_name",
        translation_key="airfryer_recipe_name",
        property_key="recipeName",
        nested_key="airfryer",
        icon="mdi:food",
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_error",
        translation_key="airfryer_error",
        property_key="error",
        nested_key="airfryer",
        icon="mdi:alert-circle",
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_preheat_status",
        translation_key="airfryer_preheat_status",
        property_key="preheat",
        nested_key="airfryer",
        icon="mdi:fire",
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_keep_warm",
        translation_key="airfryer_keep_warm",
        property_key="keep_warm",
        nested_key="airfryer",
        icon="mdi:pot-steam",
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_recipe_id",
        translation_key="airfryer_recipe_id",
        property_key="recipe_id",
        nested_key="airfryer",
        icon="mdi:book-open-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_step_id",
        translation_key="airfryer_step_id",
        property_key="step_id",
        nested_key="airfryer",
        icon="mdi:format-list-numbered",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    # Venus-only sensors (HD9875, HD9876, HD9880)
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_airspeed",
        translation_key="airfryer_airspeed",
        property_key="airspeed",
        nested_key="airfryer",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_probe_temperature",
        translation_key="airfryer_probe_temperature",
        property_key="temp_probe",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-probe",
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_current_probe_temperature",
        translation_key="airfryer_current_probe_temperature",
        property_key="current_temp_probe",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-probe-off",
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_dialog",
        translation_key="airfryer_dialog",
        property_key="dialog",
        nested_key="airfryer",
        icon="mdi:message-alert",
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_previous_status",
        translation_key="airfryer_previous_status",
        property_key="prev_status",
        nested_key="airfryer",
        icon="mdi:history",
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_cooking_id",
        translation_key="airfryer_cooking_id",
        property_key="cooking_id",
        nested_key="airfryer",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_current_stage",
        translation_key="airfryer_current_stage",
        property_key="cur_stage",
        nested_key="airfryer",
        icon="mdi:stairs",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_voltage",
        translation_key="airfryer_voltage",
        property_key="voltage",
        nested_key="airfryer",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    # Multicooker-specific sensors (Nutrimax NX0960, Hermes NX0950)
    PhilipsHomeIDSensorEntityDescription(
        key="multicooker_humidity",
        translation_key="multicooker_humidity",
        property_key="humidity",
        nested_key="airfryer",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
        device_types=("multicooker",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="multicooker_ingredient",
        translation_key="multicooker_ingredient",
        property_key="ingredient",
        nested_key="airfryer",
        icon="mdi:food-variant",
        device_types=("multicooker",),
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

# Venus-only endpoint sensors (autocook + recipe)
VENUS_ENDPOINT_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="autocook_uuid",
        translation_key="autocook_uuid",
        property_key="UUID",
        nested_key="autocook",
        icon="mdi:script-text-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="autocook_doneness",
        translation_key="autocook_doneness",
        property_key="doneness",
        nested_key="autocook",
        icon="mdi:gauge",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="autocook_amount",
        translation_key="autocook_amount",
        property_key="u1",
        nested_key="autocook",
        icon="mdi:numeric",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="autocook_weight",
        translation_key="autocook_weight",
        property_key="u2",
        nested_key="autocook",
        icon="mdi:weight",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="autocook_thickness",
        translation_key="autocook_thickness",
        property_key="u3",
        nested_key="autocook",
        icon="mdi:arrow-expand-vertical",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="recipe_current_stage",
        translation_key="recipe_current_stage",
        property_key="cur_stage",
        nested_key="recipe",
        icon="mdi:stairs",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("airfryer", "airfryer_dual", "multicooker"),
    ),
)

# Sensors common to all device types
COMMON_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        property_key="version",
        nested_key="firmware",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("air_purifier", "airfryer", "airfryer_dual", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="firmware_available",
        translation_key="firmware_available",
        property_key="upgrade",
        nested_key="firmware",
        icon="mdi:update",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("air_purifier", "airfryer", "airfryer_dual", "multicooker"),
    ),
)

# Espresso machine sensors (EP/SM series)
# Property names from APK: MachineStatusPortProperties, ConfigurationPortProperties
ESPRESSO_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_mainstate",
        translation_key="espresso_mainstate",
        property_key="mainstate",
        nested_key="machinestatus",
        icon="mdi:coffee-maker",
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_brewing_progress",
        translation_key="espresso_brewing_progress",
        property_key="Progress",
        nested_key="machinestatus",
        icon="mdi:progress-clock",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_water_level",
        translation_key="espresso_water_level",
        property_key="waterlevel",
        nested_key="machinestatus",
        icon="mdi:water",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_descale_status",
        translation_key="espresso_descale_status",
        property_key="Descalestat",
        nested_key="machinestatus",
        icon="mdi:water-opacity",
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_filter_status",
        translation_key="espresso_filter_status",
        property_key="Filterstat",
        nested_key="machinestatus",
        icon="mdi:filter",
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_filter_number",
        translation_key="espresso_filter_number",
        property_key="Filternr",
        nested_key="machinestatus",
        icon="mdi:filter-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_waste_bean",
        translation_key="espresso_waste_bean",
        property_key="wastebean",
        nested_key="machinestatus",
        icon="mdi:delete-variant",
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_switch_state",
        translation_key="espresso_switch_state",
        property_key="switchstat",
        nested_key="machinestatus",
        icon="mdi:power",
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_last_error",
        translation_key="espresso_last_error",
        property_key="lasterror",
        nested_key="machinestatus",
        icon="mdi:alert-circle",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_active_user",
        translation_key="espresso_active_user",
        property_key="activeuser",
        nested_key="machinestatus",
        icon="mdi:account",
        device_types=("espresso",),
    ),
    # Configuration port
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_water_hardness",
        translation_key="espresso_water_hardness",
        property_key="waterhard",
        nested_key="configuration",
        icon="mdi:water-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_auto_standby_time",
        translation_key="espresso_auto_standby_time",
        property_key="asotime",
        nested_key="configuration",
        icon="mdi:timer-off-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("espresso",),
    ),
)

# All sensors combined
SENSORS = (
    AIR_PURIFIER_SENSORS
    + AIRFRYER_SENSORS
    + VENUS_ENDPOINT_SENSORS
    + ESPRESSO_SENSORS
    + COMMON_SENSORS
)


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

        # Mark property as seen
        if description.property_key:
            coordinator.mark_property_seen(
                description.property_key, description.nested_key
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

            if description and not coordinator.is_property_seen(
                property_key, nested_key
            ):
                _LOGGER.info(
                    "Creating sensor %s for newly discovered property %s",
                    description.key,
                    property_key,
                )
                coordinator.mark_property_seen(property_key, nested_key)
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
        # Start countdown timer if this is a countdown sensor and cooking is active
        if self.entity_description.extrapolate_countdown:
            self._update_countdown_timer()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is being removed from hass."""
        self._stop_countdown_timer()
        await super().async_will_remove_from_hass()

    def _start_countdown_timer(self) -> None:
        """Start the countdown timer to update every second."""
        if self._countdown_unsub is not None:
            return  # Already running

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

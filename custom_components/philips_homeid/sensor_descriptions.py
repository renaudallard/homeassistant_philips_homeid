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
"""Sensor entity descriptions and device type detection."""

from __future__ import annotations

import re

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_BILLION,
    EntityCategory,
    PERCENTAGE,
    UnitOfTemperature,
    UnitOfTime,
)


def is_spectre_model(model_name: str | None) -> bool:
    """Return True for SPECTRE-class airfryers (HD9200/HD9255/HD9280/HD9285).

    These use STANDARD temp_unit semantics (True=Fahrenheit, False=Celsius),
    same as Venus, APK-verified and confirmed on a real HD9280 in issue #27.
    Currently unused: kept for SPECTRE model detection if needed later.
    """
    if not model_name:
        return False
    model_upper = model_name.upper()
    if "SPECTRE" in model_upper:
        return True
    return model_upper.startswith(("HD9200", "HD9255", "HD9280", "HD9285"))


# Device type detection based on model name patterns
def get_device_type(model_name: str) -> str:
    """Determine device type from model name."""
    model_lower = (model_name or "").lower()

    if model_lower.startswith("ac"):
        return "air_purifier"

    if (
        model_lower.startswith("hd9")
        or "airfryer" in model_lower
        or "venus" in model_lower
        or "spectre" in model_lower
    ):
        return "airfryer"

    if (
        model_lower.startswith("nx")
        or "nutrimax" in model_lower
        or "hermes" in model_lower
    ):
        return "multicooker"

    # Espresso machines. Match on the EP<digit>/SM<digit> model token rather
    # than a bare "ep"/"sm" prefix so unrelated words ("epoch", "smart...")
    # don't classify as espresso. The token regex catches both
    # "EP2520" and the marketing prefix form "Flash_Entry_P EP2520".
    if (
        "espresso" in model_lower
        or "coffee" in model_lower
        or "flash_entry" in model_lower
        or re.search(r"\bep\d", model_lower) is not None
        or re.search(r"\bsm\d", model_lower) is not None
    ):
        return "espresso"

    return "unknown"


@dataclass(frozen=True)
class PhilipsHomeIDSensorEntityDescription(SensorEntityDescription):
    """Describes Philips HomeID sensor entity."""

    property_key: str | None = None
    nested_key: str | None = None
    value_fn: Callable[[Any], Any] | None = None
    device_types: tuple[str, ...] | None = None
    extrapolate_countdown: bool = False
    # The appliance reports this temperature in whatever unit temp_unit names
    # and never converts it, so the entity reads the unit off the device
    # instead of using native_unit_of_measurement.
    device_temp_unit: bool = False


# Espresso machine state codes (from APK EspressoStateProperty.java)
_ESPRESSO_MAINSTATE: dict[int, str] = {
    0: "undefined",
    1: "standby",
    2: "ready",
    3: "brewing",
    4: "processing",
    5: "action_required",
    6: "error",
    7: "suspended",
    8: "unknown",
    9: "out_of_order",
}


def _espresso_mainstate(value: Any) -> str | None:
    """Convert espresso mainstate integer to human-readable string."""
    if value is None:
        return None
    try:
        return _ESPRESSO_MAINSTATE.get(int(value), f"unknown ({value})")
    except (ValueError, TypeError):
        return str(value)


# Rita (EP8757 and similar) enum mappings from APK
# (fusion/bridge/device/rita/mappers/)
_RITA_MACHINE_STATE: dict[int, str] = {
    0: "not_used",
    1: "brewing",
    2: "action_required",
    3: "unrecoverable_error",
}

_RITA_MACHINE_STATUS: dict[int, str] = {
    0: "not_used",
    1: "running",
    2: "suspended_alarm",
    3: "suspended_resumable",
    4: "finishing_successfully",
    5: "finishing_unsuccessfully",
}

_RITA_MACHINE_EXTRA_INFO: dict[int, str] = {
    0: "not_used",
    1: "bean_lack",
    2: "water_lack",
    3: "ground_container_full",
    4: "brew_unit_missing",
    5: "water_container_should_be_removed",
    6: "drip_tray_removed",
    7: "door_open",
    8: "ground_container_removed",
    9: "masterpiece_alarm",
    20: "user_abort_or_suspend_timeout",
    21: "brew_abort_refill_required",
    22: "brew_abort_heater_fail",
    23: "brew_abort_bu_fail",
    24: "brew_abort_grinder_fail",
    40: "error_1",
    41: "error_2",
    42: "error_3",
    43: "error_4",
    44: "error_5",
    45: "error_10",
    46: "error_11",
    47: "error_14",
    48: "error_15",
    49: "error_19",
    50: "error_24",
}

_RITA_BEAN_TYPE: dict[int, str] = {
    0: "arabica",
    1: "mix",
    2: "other",
}

_RITA_ROAST_LEVEL: dict[int, str] = {
    0: "light",
    1: "medium",
    2: "dark",
}

_RITA_CONTROL_STATUS: dict[int, str] = {
    1: "machine_notification",
    2: "no_error",
    3: "invalid_command",
    50: "non_existent_or_invalid_recipe",
    51: "machine_busy",
    52: "machine_in_alarm_or_error_state",
    100: "profiles_full",
    101: "unavailable_color",
    102: "invalid_profile_name",
    103: "no_recipe_selected",
    104: "recipes_full",
    105: "invalid_recipe_name",
    106: "invalid_profile_id",
    107: "invalid_profile_order",
    150: "invalid_barista_assistant_settings",
}


def _make_enum_decoder(mapping: dict[int, str]) -> Callable[[Any], str | None]:
    """Build a value_fn that decodes an integer enum via the given mapping."""

    def decoder(value: Any) -> str | None:
        if value is None:
            return None
        try:
            return mapping.get(int(value), f"unknown ({value})")
        except (ValueError, TypeError):
            return str(value)

    return decoder


_rita_machine_state = _make_enum_decoder(_RITA_MACHINE_STATE)
_rita_machine_status = _make_enum_decoder(_RITA_MACHINE_STATUS)
_rita_machine_extra_info = _make_enum_decoder(_RITA_MACHINE_EXTRA_INFO)
_rita_bean_type = _make_enum_decoder(_RITA_BEAN_TYPE)
_rita_roast_level = _make_enum_decoder(_RITA_ROAST_LEVEL)
_rita_control_status = _make_enum_decoder(_RITA_CONTROL_STATUS)


def _seconds_to_minutes(value: Any) -> int | None:
    """Convert seconds to minutes."""
    if value is None:
        return None
    try:
        return int(value) // 60
    except (ValueError, TypeError):
        return None


def _seconds_to_hours(value: Any) -> int | None:
    """Convert seconds to hours."""
    if value is None:
        return None
    try:
        return int(value) // 3600
    except (ValueError, TypeError):
        return None


# Air purifier sensors
AIR_PURIFIER_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="pm25",
        translation_key="pm25",
        # FUSION MUJI air purifiers report PM2.5 on D03221 (APK AirStatusPort).
        property_key="D03221",
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
        # FUSION MUJI reports the indoor air-quality index on D03120
        # (APK AirStatusPortProperties.indoorAirIndex).
        property_key="D03120",
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
        value_fn=_seconds_to_hours,
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

# Airfryer sensors
AIRFRYER_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_status",
        translation_key="airfryer_status",
        property_key="status",
        nested_key="airfryer",
        icon="mdi:stove",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_temperature",
        translation_key="airfryer_temperature",
        property_key="temp",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_temp_unit=True,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_current_temperature",
        translation_key="airfryer_current_temperature",
        property_key="cur_temp",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_temp_unit=True,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-check",
        device_types=("airfryer", "multicooker"),
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
        device_types=("airfryer", "multicooker"),
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
        device_types=("airfryer", "multicooker"),
        extrapolate_countdown=True,
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_preset",
        translation_key="airfryer_preset",
        property_key="preset",
        nested_key="airfryer",
        icon="mdi:format-list-numbered",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_recipe_name",
        translation_key="airfryer_recipe_name",
        property_key="recipeName",
        nested_key="airfryer",
        icon="mdi:food",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_error",
        translation_key="airfryer_error",
        property_key="error",
        nested_key="airfryer",
        icon="mdi:alert-circle",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_preheat_status",
        translation_key="airfryer_preheat_status",
        property_key="preheat",
        nested_key="airfryer",
        icon="mdi:fire",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_keep_warm",
        translation_key="airfryer_keep_warm",
        property_key="keep_warm",
        nested_key="airfryer",
        icon="mdi:pot-steam",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_recipe_id",
        translation_key="airfryer_recipe_id",
        property_key="recipe_id",
        nested_key="airfryer",
        icon="mdi:book-open-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_step_id",
        translation_key="airfryer_step_id",
        property_key="step_id",
        nested_key="airfryer",
        icon="mdi:format-list-numbered",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_airspeed",
        translation_key="airfryer_airspeed",
        property_key="airspeed",
        nested_key="airfryer",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_probe_temperature",
        translation_key="airfryer_probe_temperature",
        property_key="temp_probe",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_temp_unit=True,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-probe",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_current_probe_temperature",
        translation_key="airfryer_current_probe_temperature",
        property_key="current_temp_probe",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_temp_unit=True,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-probe-off",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_dialog",
        translation_key="airfryer_dialog",
        property_key="dialog",
        nested_key="airfryer",
        icon="mdi:message-alert",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_previous_status",
        translation_key="airfryer_previous_status",
        property_key="prev_status",
        nested_key="airfryer",
        icon="mdi:history",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_cooking_id",
        translation_key="airfryer_cooking_id",
        property_key="cooking_id",
        nested_key="airfryer",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_current_stage",
        translation_key="airfryer_current_stage",
        property_key="cur_stage",
        nested_key="airfryer",
        icon="mdi:stairs",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("airfryer", "multicooker"),
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
        device_types=("airfryer", "multicooker"),
    ),
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
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_left_status",
        translation_key="airfryer_left_status",
        property_key="status_l",
        nested_key="airfryer",
        icon="mdi:tray-arrow-up",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_left_temperature",
        translation_key="airfryer_left_temperature",
        property_key="temp_l",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_temp_unit=True,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        device_types=("airfryer", "multicooker"),
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
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_right_status",
        translation_key="airfryer_right_status",
        property_key="status_r",
        nested_key="airfryer",
        icon="mdi:tray-arrow-down",
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="airfryer_right_temperature",
        translation_key="airfryer_right_temperature",
        property_key="temp_r",
        nested_key="airfryer",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_temp_unit=True,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        device_types=("airfryer", "multicooker"),
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
        device_types=("airfryer", "multicooker"),
    ),
)

# Venus-only endpoint sensors
VENUS_ENDPOINT_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="autocook_uuid",
        translation_key="autocook_uuid",
        property_key="UUID",
        nested_key="autocook",
        icon="mdi:script-text-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="autocook_doneness",
        translation_key="autocook_doneness",
        property_key="doneness",
        nested_key="autocook",
        icon="mdi:gauge",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="autocook_amount",
        translation_key="autocook_amount",
        property_key="u1",
        nested_key="autocook",
        icon="mdi:numeric",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="autocook_weight",
        translation_key="autocook_weight",
        property_key="u2",
        nested_key="autocook",
        icon="mdi:weight",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="autocook_thickness",
        translation_key="autocook_thickness",
        property_key="u3",
        nested_key="autocook",
        icon="mdi:arrow-expand-vertical",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="recipe_current_stage",
        translation_key="recipe_current_stage",
        property_key="cur_stage",
        nested_key="recipe",
        icon="mdi:stairs",
        state_class=SensorStateClass.MEASUREMENT,
        device_types=("airfryer", "multicooker"),
    ),
)

# Common sensors
COMMON_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        property_key="version",
        nested_key="firmware",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("air_purifier", "airfryer", "multicooker"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="firmware_available",
        translation_key="firmware_available",
        property_key="upgrade",
        nested_key="firmware",
        icon="mdi:update",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("air_purifier", "airfryer", "multicooker"),
    ),
)

# Espresso machine sensors
ESPRESSO_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="espresso_mainstate",
        translation_key="espresso_mainstate",
        property_key="mainstate",
        nested_key="machinestatus",
        icon="mdi:coffee-maker",
        value_fn=_espresso_mainstate,
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
        key="espresso_bean_level",
        translation_key="espresso_bean_level",
        property_key="beanlevel",
        nested_key="machinestatus",
        icon="mdi:seed-outline",
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

# Rita espresso machines (EP8757 and similar). Status port properties
# are stored under the "airfryer" key of device state (see _NCP_PORT_MAP
# in mqtt_api.py; "Status" -> "airfryer" is applied for all FUSION devices).
RITA_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="rita_machine_state",
        translation_key="rita_machine_state",
        property_key="McState",
        nested_key="airfryer",
        icon="mdi:coffee-maker",
        value_fn=_rita_machine_state,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="rita_machine_status",
        translation_key="rita_machine_status",
        property_key="McStatus",
        nested_key="airfryer",
        icon="mdi:coffee",
        value_fn=_rita_machine_status,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="rita_machine_extra_info",
        translation_key="rita_machine_extra_info",
        property_key="McExInfo",
        nested_key="airfryer",
        icon="mdi:information-outline",
        value_fn=_rita_machine_extra_info,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="rita_control_status",
        translation_key="rita_control_status",
        property_key="CtrlStatus",
        nested_key="airfryer",
        icon="mdi:check-circle-outline",
        value_fn=_rita_control_status,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="rita_bean_type",
        translation_key="rita_bean_type",
        property_key="BeanType",
        nested_key="airfryer",
        icon="mdi:coffee-outline",
        value_fn=_rita_bean_type,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="rita_roast_level",
        translation_key="rita_roast_level",
        property_key="RoastLevel",
        nested_key="airfryer",
        icon="mdi:fire",
        value_fn=_rita_roast_level,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="rita_aquaclean_filter_number",
        translation_key="rita_aquaclean_filter_number",
        property_key="AqFiltNum",
        nested_key="airfryer",
        icon="mdi:filter-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="rita_aquaclean_autonomy",
        translation_key="rita_aquaclean_autonomy",
        property_key="AqAutmy",
        nested_key="airfryer",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="rita_descale_autonomy",
        translation_key="rita_descale_autonomy",
        property_key="DescAutmy",
        nested_key="airfryer",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-opacity",
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="rita_coffee_autonomy",
        translation_key="rita_coffee_autonomy",
        property_key="CorAutmy",
        nested_key="airfryer",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:coffee-maker-outline",
        device_types=("espresso",),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="rita_brew_group_autonomy",
        translation_key="rita_brew_group_autonomy",
        property_key="MbcAutmy",
        nested_key="airfryer",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cog-outline",
        device_types=("espresso",),
    ),
)

# FUSION cloud-relay sensors (shadow + Config port fields).
# Populated from the AWS IoT shadow `reported` block and the NCP Config port
# for any FUSION-capable device (airfryer HD928x/HD988x, espresso EP*).
FUSION_SENSORS: tuple[PhilipsHomeIDSensorEntityDescription, ...] = (
    PhilipsHomeIDSensorEntityDescription(
        key="ncp_firmware_version",
        translation_key="ncp_firmware_version",
        property_key="ncpFirmwareVersion",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "espresso"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="host_firmware_version",
        translation_key="host_firmware_version",
        property_key="hostFirmwareVersion",
        icon="mdi:memory",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "espresso"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="product_error",
        translation_key="product_error",
        property_key="productError",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "espresso"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="device_name",
        translation_key="device_name",
        property_key="name",
        nested_key="config",
        icon="mdi:tag-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "espresso"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="device_serial",
        translation_key="device_serial",
        property_key="serial",
        nested_key="config",
        icon="mdi:barcode",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "espresso"),
    ),
    PhilipsHomeIDSensorEntityDescription(
        key="device_ctn",
        translation_key="device_ctn",
        property_key="ctn",
        nested_key="config",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=("airfryer", "espresso"),
    ),
)

# All sensors combined
SENSORS = (
    AIR_PURIFIER_SENSORS
    + AIRFRYER_SENSORS
    + VENUS_ENDPOINT_SENSORS
    + ESPRESSO_SENSORS
    + RITA_SENSORS
    + FUSION_SENSORS
    + COMMON_SENSORS
)

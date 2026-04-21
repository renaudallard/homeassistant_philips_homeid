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
"""Select platform for Philips HomeID."""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterator

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .entity import PhilipsHomeIDEntity
from .local_api import PORT_HERMESAC, PORT_NUTRIMAX, VENUS_STYLE_PORTS
from .sensor import get_device_type

_LOGGER = logging.getLogger(__name__)

# SPECTRE preset IDs (from APK CookingMethodCategoryKt)
SPECTRE_PRESETS: dict[int, str] = {
    0: "manual",
    1: "frozen_snacks",
    2: "fresh_fries",
    3: "chicken",
    4: "fish",
    5: "muffins_cake",
    6: "meat_chops",
    7: "vegetables",
    8: "keep_warm",
}

# Venus preset IDs (from APK CookingMethodCategoryKt)
VENUS_PRESETS: dict[int, str] = {
    0: "manual",
    1: "auto_cook",
    2: "keep_warm",
    3: "recipe",
    4: "no_selection",
}

# Nutrimax preset IDs (from APK nutrimax/CookingMethodCategoryKt)
NUTRIMAX_PRESETS: dict[int, str] = {
    0: "air_steam",
    1: "steaming",
    2: "roast",
    3: "bake",
    4: "slow_cook",
    5: "defrost",
    6: "reheat",
    7: "sous_vide",
    8: "manual",
    9: "keep_warm",
}

# Rita bean type / roast level (APK RitaBeanType / RitaRoastLevel)
RITA_BEAN_TYPES: dict[int, str] = {
    0: "arabica",
    1: "mix",
    2: "other",
}
RITA_ROAST_LEVELS: dict[int, str] = {
    0: "light",
    1: "medium",
    2: "dark",
}

# Hermes preset IDs (from APK hermes/CookingMethodCategoryKt)
HERMES_PRESETS: dict[int, str] = {
    0: "no_selection",
    1: "manual",
    2: "air_steam",
    3: "roast",
    4: "bake",
    11: "steaming",
    12: "air_steam_pro",
    13: "defrost",
    14: "reheat",
    15: "stew",
    30: "user_preset",
    40: "recipe",
    50: "keep_warm",
    60: "easy_clean",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    model_name = coordinator.device_info.model_name or ""
    device_type = get_device_type(model_name)

    if device_type in ("airfryer", "airfryer_dual", "multicooker"):
        created_keys: set[str] = set()

        def _make_airfryer_selects() -> list[PhilipsHomeIDEntity]:
            entities: list[PhilipsHomeIDEntity] = []
            if "cooking_method" not in created_keys and coordinator.has_property(
                "preset", "airfryer"
            ):
                entities.append(
                    PhilipsHomeIDCookingMethodSelect(coordinator, coordinator.device_id)
                )
                created_keys.add("cooking_method")
            if (
                "autocook_program" not in created_keys
                and coordinator.mqtt_client is None
                and coordinator.has_property("autocook")
            ):
                entities.append(
                    PhilipsHomeIDAutoCookProgramSelect(
                        coordinator, coordinator.device_id
                    )
                )
                created_keys.add("autocook_program")
            return entities

        initial = _make_airfryer_selects()
        if initial:
            async_add_entities(initial)

        def handle_new_properties(
            new_properties: list[tuple[str, str | None]],
        ) -> None:
            entities = _make_airfryer_selects()
            if entities:
                _LOGGER.info(
                    "Creating airfryer selects for newly discovered properties"
                )
                async_add_entities(entities, update_before_add=True)

        unregister = coordinator.register_new_property_callback(handle_new_properties)
        entry.async_on_unload(unregister)
        return

    if device_type == "espresso":

        def _rita_entities() -> list[PhilipsHomeIDEntity]:
            return [
                PhilipsHomeIDRitaRoastLevelSelect(coordinator, coordinator.device_id),
                PhilipsHomeIDRitaBeanTypeSelect(coordinator, coordinator.device_id),
                PhilipsHomeIDRitaBrewProfileSelect(coordinator, coordinator.device_id),
                PhilipsHomeIDRitaBrewRecipeSelect(coordinator, coordinator.device_id),
            ]

        if coordinator.has_property(
            "RoastLevel", "airfryer"
        ) or coordinator.has_property("BeanType", "airfryer"):
            async_add_entities(_rita_entities())
            return

        rita_created = False

        def handle_rita_properties(
            new_properties: list[tuple[str, str | None]],
        ) -> None:
            nonlocal rita_created
            if rita_created:
                return
            for prop_key, nested_key in new_properties:
                if prop_key in ("RoastLevel", "BeanType") and nested_key == "airfryer":
                    rita_created = True
                    _LOGGER.info("Creating Rita selects for newly discovered espresso")
                    async_add_entities(_rita_entities(), update_before_add=True)
                    return

        unregister = coordinator.register_new_property_callback(handle_rita_properties)
        entry.async_on_unload(unregister)
        return


class PhilipsHomeIDCookingMethodSelect(PhilipsHomeIDEntity, SelectEntity):
    """Cooking method select for Philips airfryers."""

    _attr_translation_key = "cooking_method"
    _attr_icon = "mdi:chef-hat"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_cooking_method"
        self._presets = self._get_presets()
        self._id_to_name = self._presets
        self._name_to_id = {v: k for k, v in self._presets.items()}
        self._attr_options = list(self._presets.values())

    def _get_presets(self) -> dict[int, str]:
        """Return the correct preset list for this device's architecture."""
        port = self.coordinator.device_info.airfryer_port
        if port == PORT_NUTRIMAX:
            return NUTRIMAX_PRESETS
        if port == PORT_HERMESAC:
            return HERMES_PRESETS
        if port in VENUS_STYLE_PORTS:
            return VENUS_PRESETS
        return SPECTRE_PRESETS

    @property
    def current_option(self) -> str | None:
        """Return the currently selected cooking method."""
        value = self._get_property_value("preset", "airfryer")
        if value is not None:
            try:
                return self._id_to_name.get(int(value))
            except (ValueError, TypeError):
                return None
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the cooking method."""
        preset_id = self._name_to_id.get(option)
        if preset_id is not None:
            await self.coordinator.async_airfryer_set_settings(preset=preset_id)


class PhilipsHomeIDRitaRoastLevelSelect(PhilipsHomeIDEntity, SelectEntity):
    """Roast level select for Rita espresso machines."""

    _attr_translation_key = "rita_roast_level_select"
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_rita_roast_level"
        self._name_to_id = {v: k for k, v in RITA_ROAST_LEVELS.items()}
        self._attr_options = list(RITA_ROAST_LEVELS.values())

    @property
    def current_option(self) -> str | None:
        value = self._get_property_value("RoastLevel", "airfryer")
        if value is None:
            return None
        try:
            return RITA_ROAST_LEVELS.get(int(value))
        except (ValueError, TypeError):
            return None

    async def async_select_option(self, option: str) -> None:
        enum_value = self._name_to_id.get(option)
        if enum_value is not None:
            await self.coordinator.async_rita_set_roast_level(enum_value)


class PhilipsHomeIDAutoCookProgramSelect(PhilipsHomeIDEntity, SelectEntity):
    """Built-in AutoCook program selector for Venus local-HTTP airfryers."""

    _attr_translation_key = "autocook_program"
    _attr_icon = "mdi:chef-hat"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_autocook_program"

    def _name_to_uuid(self) -> dict[str, str]:
        programs = self.coordinator.autocook_program_options()
        return {
            name: uuid
            for uuid, name in sorted(
                programs.items(), key=lambda item: item[1].casefold()
            )
        }

    @property
    def options(self) -> list[str]:
        return list(self._name_to_uuid().keys())

    @property
    def current_option(self) -> str | None:
        uuid = self.coordinator.autocook_selected_uuid
        if not uuid:
            return None
        return self.coordinator.autocook_program_options().get(uuid)

    async def async_select_option(self, option: str) -> None:
        uuid = self._name_to_uuid().get(option)
        if uuid:
            self.coordinator.set_autocook_selected_uuid(uuid)
            self.coordinator.async_update_listeners()


class PhilipsHomeIDRitaBeanTypeSelect(PhilipsHomeIDEntity, SelectEntity):
    """Bean type select for Rita espresso machines."""

    _attr_translation_key = "rita_bean_type_select"
    _attr_icon = "mdi:coffee-outline"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_rita_bean_type"
        self._name_to_id = {v: k for k, v in RITA_BEAN_TYPES.items()}
        self._attr_options = list(RITA_BEAN_TYPES.values())

    @property
    def current_option(self) -> str | None:
        value = self._get_property_value("BeanType", "airfryer")
        if value is None:
            return None
        try:
            return RITA_BEAN_TYPES.get(int(value))
        except (ValueError, TypeError):
            return None

    async def async_select_option(self, option: str) -> None:
        enum_value = self._name_to_id.get(option)
        if enum_value is not None:
            await self.coordinator.async_rita_set_bean_type(enum_value)


def _split_names(raw: object, expected: int) -> list[str]:
    """Split a comma-separated name list from an NCP port response.

    The machine reports the names as a single comma-separated string with
    a fixed number of slots (8 profiles, 40 recipes per recipe port).
    """
    if not isinstance(raw, str):
        return [""] * expected
    parts = raw.split(",")
    parts += [""] * max(0, expected - len(parts))
    return [p.strip() for p in parts[:expected]]


def _build_named_options(
    names: list[str], total: int, fallback_prefix: str
) -> dict[int, str]:
    """Return a slot->label map for a named slot dropdown.

    Empty slots are hidden when at least one slot carries a name, and the
    label is just the name for uniquely-named slots. Slots that share a
    name keep the "N: name" prefix so they stay distinguishable. When no
    slot is named the dropdown falls back to the full numbered list so
    the user can still pick a slot to brew its built-in drink id.
    """
    has_named = any(n for n in names)
    if not has_named:
        return {i: f"{fallback_prefix} {i}" for i in range(total)}

    name_counts: dict[str, int] = {}
    for n in names:
        if n:
            name_counts[n] = name_counts.get(n, 0) + 1

    slot_to_label: dict[int, str] = {}
    for i in range(total):
        name = names[i] if i < len(names) else ""
        if not name:
            continue
        if name_counts.get(name, 0) > 1:
            slot_to_label[i] = f"{i}: {name}"
        else:
            slot_to_label[i] = name
    return slot_to_label


def _label_to_slot(labels: dict[int, str], option: str) -> int | None:
    """Return the slot index for the given option label."""
    for slot, label in labels.items():
        if label == option:
            return slot
    return None


class PhilipsHomeIDRitaBrewProfileSelect(PhilipsHomeIDEntity, SelectEntity):
    """Profile selector for Rita espresso machines (APK RitaProfilesPort)."""

    _attr_translation_key = "rita_brew_profile_select"
    _attr_icon = "mdi:account"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_rita_brew_profile_select"

    def _slot_labels(self) -> dict[int, str]:
        state = self.device_state
        names = [""] * 8
        if state:
            profiles = state.properties.get("Profiles")
            if isinstance(profiles, dict):
                names = _split_names(profiles.get("Pr_Names"), 8)
        return _build_named_options(names, 8, "Profile")

    @property
    def options(self) -> list[str]:
        return list(self._slot_labels().values())

    @property
    def current_option(self) -> str | None:
        return self._slot_labels().get(self.coordinator.rita_brew_profile_id)

    async def async_select_option(self, option: str) -> None:
        slot = _label_to_slot(self._slot_labels(), option)
        if slot is None:
            return
        self.coordinator.set_rita_brew_profile_id(slot)
        self.coordinator.async_update_listeners()


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode one protobuf varint; return (value, new_offset)."""
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift >= 64:
            raise ValueError("varint overflow")
    raise ValueError("truncated varint")


def _pb_iter_fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield (field_number, wire_type, value) for top-level protobuf fields."""
    offset = 0
    while offset < len(data):
        tag, offset = _decode_varint(data, offset)
        field_number = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:  # varint
            value, offset = _decode_varint(data, offset)
            yield field_number, wire_type, value
        elif wire_type == 2:  # length-delimited
            length, offset = _decode_varint(data, offset)
            raw = data[offset : offset + length]
            offset += length
            yield field_number, wire_type, raw
        elif wire_type == 1:  # 64-bit fixed
            offset += 8
        elif wire_type == 5:  # 32-bit fixed
            offset += 4
        else:
            raise ValueError(f"unsupported wire type {wire_type}")


def _decode_profile_recipe_ids(blob_b64: str) -> set[int]:
    """Return the recipeIds from a Rita profile's recipeIdOrderList (tag 4)."""
    try:
        data = base64.b64decode(blob_b64)
    except (ValueError, TypeError):
        return set()
    result: set[int] = set()
    try:
        for field_number, wire_type, value in _pb_iter_fields(data):
            if field_number != 4 or wire_type != 2 or not isinstance(value, bytes):
                continue
            offset = 0
            while offset < len(value):
                recipe_id, offset = _decode_varint(value, offset)
                if recipe_id:
                    result.add(recipe_id)
    except ValueError:
        return set()
    return result


def _decode_recipe_id(blob_b64: str) -> int | None:
    """Return the recipeId (tag 1) from a RitaBrewCommand saved recipe blob."""
    try:
        data = base64.b64decode(blob_b64)
    except (ValueError, TypeError):
        return None
    try:
        for field_number, wire_type, value in _pb_iter_fields(data):
            if field_number == 1 and wire_type == 0 and isinstance(value, int):
                return value
    except ValueError:
        return None
    return None


class PhilipsHomeIDRitaBrewRecipeSelect(PhilipsHomeIDEntity, SelectEntity):
    """Recipe selector for Rita espresso machines (APK RitaRecipesPort).

    Shown slots are filtered to the active profile by matching each rcp
    blob's recipeId (field 1 of RitaBrewCommand) against the selected
    profile's recipeIdOrderList (field 4 of RitaProfileData), the same
    approach the APK uses in DefaultGetDrinksForActiveProfileUseCase.
    """

    _attr_translation_key = "rita_brew_recipe_select"
    _attr_icon = "mdi:book-open-variant"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_rita_brew_recipe_select"

    def _slot_labels(self) -> dict[int, str]:
        state = self.device_state
        names = [""] * 80
        blobs: list[str] = [""] * 80
        profile_ids: set[int] = set()
        profile_slot = self.coordinator.rita_brew_profile_id

        if state:
            p1 = state.properties.get("Recipes_p1")
            p2 = state.properties.get("Recipes_p2")
            if isinstance(p1, dict):
                names[0:40] = _split_names(p1.get("Rec_Names"), 40)
                for i in range(40):
                    raw = p1.get(f"rcp{i}")
                    if isinstance(raw, str):
                        blobs[i] = raw
            if isinstance(p2, dict):
                names[40:80] = _split_names(p2.get("Rec_Names"), 40)
                for i in range(40):
                    raw = p2.get(f"rcp{40 + i}")
                    if isinstance(raw, str):
                        blobs[40 + i] = raw

            profiles = state.properties.get("Profiles")
            if isinstance(profiles, dict):
                profile_blob = profiles.get(f"profile{profile_slot}")
                if isinstance(profile_blob, str) and profile_blob:
                    profile_ids = _decode_profile_recipe_ids(profile_blob)

        if not profile_ids:
            # Empty profile: keep the numeric fallback so users can still
            # trigger a built-in-drink brew via slot id (the brew path
            # falls through to REMOTE_BREW when the slot has no blob).
            return _build_named_options([""] * 80, 80, "Recipe")

        filtered = [""] * 80
        for i in range(80):
            if not names[i] or not blobs[i]:
                continue
            rid = _decode_recipe_id(blobs[i])
            if rid is not None and rid in profile_ids:
                filtered[i] = names[i]

        if not any(filtered):
            return {}
        return _build_named_options(filtered, 80, "Recipe")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Auto-select first recipe when current selection is not in the list."""
        labels = self._slot_labels()
        if labels and self.coordinator.rita_brew_recipe_id not in labels:
            self.coordinator.set_rita_brew_recipe_id(next(iter(labels)))
        super()._handle_coordinator_update()

    @property
    def options(self) -> list[str]:
        return list(self._slot_labels().values())

    @property
    def current_option(self) -> str | None:
        return self._slot_labels().get(self.coordinator.rita_brew_recipe_id)

    async def async_select_option(self, option: str) -> None:
        slot = _label_to_slot(self._slot_labels(), option)
        if slot is None:
            return
        self.coordinator.set_rita_brew_recipe_id(slot)
        self.async_write_ha_state()

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

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
        if coordinator.has_property("preset", "airfryer"):
            async_add_entities(
                [PhilipsHomeIDCookingMethodSelect(coordinator, coordinator.device_id)]
            )
            return

        created = False

        def handle_new_properties(
            new_properties: list[tuple[str, str | None]],
        ) -> None:
            nonlocal created
            if created:
                return
            for prop_key, nested_key in new_properties:
                if prop_key == "preset" and nested_key == "airfryer":
                    created = True
                    _LOGGER.info(
                        "Creating cooking method select for newly discovered airfryer"
                    )
                    async_add_entities(
                        [
                            PhilipsHomeIDCookingMethodSelect(
                                coordinator, coordinator.device_id
                            )
                        ],
                        update_before_add=True,
                    )
                    return

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


def _name_option(index: int, name: str, fallback_prefix: str) -> str:
    """Return the option label for the given slot index and name."""
    if name:
        return f"{index}: {name}"
    return f"{fallback_prefix} {index}"


def _parse_option_index(option: str) -> int | None:
    """Extract the integer slot index from an option label."""
    head = option.split(":", 1)[0].strip()
    token = head.split()[-1] if head else ""
    try:
        return int(token)
    except ValueError:
        return None


def _build_named_options(
    names: list[str], total: int, fallback_prefix: str
) -> tuple[list[str], dict[int, str]]:
    """Return (options list, slot->label map) for a named slot dropdown.

    Empty slots are hidden when at least one slot carries a name. When no
    slot is named the dropdown falls back to the full numbered list so the
    user can still pick a slot to brew its built-in drink id.
    """
    has_named = any(n for n in names)
    slot_to_label: dict[int, str] = {}
    for i in range(total):
        name = names[i] if i < len(names) else ""
        if has_named and not name:
            continue
        slot_to_label[i] = _name_option(i, name, fallback_prefix)
    return list(slot_to_label.values()), slot_to_label


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
        _, mapping = _build_named_options(names, 8, "Profile")
        return mapping

    @property
    def options(self) -> list[str]:
        return list(self._slot_labels().values())

    @property
    def current_option(self) -> str | None:
        return self._slot_labels().get(self.coordinator.rita_brew_profile_id)

    async def async_select_option(self, option: str) -> None:
        idx = _parse_option_index(option)
        if idx is None or not 0 <= idx < 8:
            return
        self.coordinator.set_rita_brew_profile_id(idx)
        self.async_write_ha_state()


class PhilipsHomeIDRitaBrewRecipeSelect(PhilipsHomeIDEntity, SelectEntity):
    """Recipe selector for Rita espresso machines (APK RitaRecipesPort)."""

    _attr_translation_key = "rita_brew_recipe_select"
    _attr_icon = "mdi:book-open-variant"

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_rita_brew_recipe_select"

    def _slot_labels(self) -> dict[int, str]:
        state = self.device_state
        names = [""] * 80
        if state:
            p1 = state.properties.get("Recipes_p1")
            p2 = state.properties.get("Recipes_p2")
            if isinstance(p1, dict):
                names[0:40] = _split_names(p1.get("Rec_Names"), 40)
            if isinstance(p2, dict):
                names[40:80] = _split_names(p2.get("Rec_Names"), 40)
        _, mapping = _build_named_options(names, 80, "Recipe")
        return mapping

    @property
    def options(self) -> list[str]:
        return list(self._slot_labels().values())

    @property
    def current_option(self) -> str | None:
        return self._slot_labels().get(self.coordinator.rita_brew_recipe_id)

    async def async_select_option(self, option: str) -> None:
        idx = _parse_option_index(option)
        if idx is None or not 0 <= idx < 80:
            return
        self.coordinator.set_rita_brew_recipe_id(idx)
        self.async_write_ha_state()

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
"""Button platform for Philips HomeID."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_CLOUD_REFRESH_TOKEN, DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .entity import PhilipsHomeIDEntity
from .sensor import get_device_type

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhilipsHomeIDButtonEntityDescription(ButtonEntityDescription):
    """Describes Philips HomeID button entity."""

    press_fn: Callable[[PhilipsHomeIDCoordinator], Coroutine[Any, Any, bool]] | None = (
        None
    )
    available_key: str | None = None  # Nested key to check for availability
    cloud_only: bool = False  # Only show for cloud-authenticated devices
    local_only: bool = False  # Only show for local-HTTP devices (not FUSION)
    venus_only: bool = False  # Only show for Venus-style ports (autocook-capable)


# Rita espresso machine buttons (APK RitaControlCommand)
RITA_BUTTONS: tuple[PhilipsHomeIDButtonEntityDescription, ...] = (
    PhilipsHomeIDButtonEntityDescription(
        key="rita_brew",
        translation_key="rita_brew",
        icon="mdi:coffee",
        press_fn=lambda c: c.async_rita_brew(
            c.rita_brew_profile_id, c.rita_brew_recipe_id
        ),
        available_key="airfryer",
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="rita_brew_builtin",
        translation_key="rita_brew_builtin",
        icon="mdi:coffee-outline",
        press_fn=lambda c: c.async_rita_brew_builtin(
            c.rita_brew_profile_id, c.rita_brew_drink_id
        ),
        available_key="airfryer",
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="rita_brew_hot_water",
        translation_key="rita_brew_hot_water",
        icon="mdi:kettle-steam",
        press_fn=lambda c: c.async_rita_brew_hot_water(),
        available_key="airfryer",
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="rita_abort_brew",
        translation_key="rita_abort_brew",
        icon="mdi:stop",
        press_fn=lambda c: c.async_rita_abort_brew(),
        available_key="airfryer",
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="rita_resume_brew",
        translation_key="rita_resume_brew",
        icon="mdi:play",
        press_fn=lambda c: c.async_rita_resume_brew(),
        available_key="airfryer",
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="rita_skip_step",
        translation_key="rita_skip_step",
        icon="mdi:skip-next",
        press_fn=lambda c: c.async_rita_skip_step(),
        available_key="airfryer",
    ),
)


# Airfryer buttons
AIRFRYER_BUTTONS: tuple[PhilipsHomeIDButtonEntityDescription, ...] = (
    PhilipsHomeIDButtonEntityDescription(
        key="airfryer_start",
        translation_key="airfryer_start",
        icon="mdi:play",
        press_fn=lambda c: c.async_airfryer_start(),
        available_key="airfryer",
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="airfryer_pause",
        translation_key="airfryer_pause",
        icon="mdi:pause",
        press_fn=lambda c: c.async_airfryer_pause(),
        available_key="airfryer",
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="airfryer_stop",
        translation_key="airfryer_stop",
        icon="mdi:stop",
        press_fn=lambda c: c.async_airfryer_stop(),
        available_key="airfryer",
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="airfryer_keep_warm",
        translation_key="airfryer_keep_warm",
        icon="mdi:pot-steam",
        press_fn=lambda c: c.async_airfryer_keep_warm(),
        available_key="airfryer",
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="airfryer_refresh_recipe",
        translation_key="airfryer_refresh_recipe",
        icon="mdi:book-refresh",
        press_fn=lambda c: c.async_refresh_recipe_cache(),
        available_key="airfryer",
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="airfryer_autocook_send",
        translation_key="airfryer_autocook_send",
        icon="mdi:send",
        press_fn=lambda c: c.async_autocook_send(),
        available_key="autocook",
        local_only=True,
        venus_only=True,
    ),
)


# Local EP/SM espresso machine brew buttons (command/BasicRecipe port).
# Built-in drinks confirmed on EP2520; brewing first powers the machine on
# and waits for it to be ready.
ESPRESSO_BREW_BUTTONS: tuple[PhilipsHomeIDButtonEntityDescription, ...] = (
    PhilipsHomeIDButtonEntityDescription(
        key="espresso_brew_espresso",
        translation_key="espresso_brew_espresso",
        icon="mdi:coffee",
        press_fn=lambda c: c.async_espresso_brew_espresso(),
        available_key="machinestatus",
        local_only=True,
    ),
    PhilipsHomeIDButtonEntityDescription(
        key="espresso_brew_coffee",
        translation_key="espresso_brew_coffee",
        icon="mdi:coffee-outline",
        press_fn=lambda c: c.async_espresso_brew_coffee(),
        available_key="machinestatus",
        local_only=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    model_name = coordinator.device_info.model_name or ""
    device_type = get_device_type(model_name)

    button_descriptions: tuple[PhilipsHomeIDButtonEntityDescription, ...]
    watch_prop: tuple[str, str | None]
    if device_type in ("airfryer", "multicooker"):
        button_descriptions = AIRFRYER_BUTTONS
        watch_prop = ("status", "airfryer")
    elif device_type == "espresso":
        if coordinator.mqtt_client is None:
            # Local EP/SM espresso machine (e.g. EP2520) via the command port.
            # machinestatus is a port dict, and a port is only ever announced
            # by its members, so watch for one of those rather than the port.
            button_descriptions = ESPRESSO_BREW_BUTTONS
            watch_prop = ("mainstate", "machinestatus")
        else:
            # Rita espresso machine (FUSION / cloud)
            button_descriptions = RITA_BUTTONS
            watch_prop = ("McState", "airfryer")
    else:
        _LOGGER.debug("Skipping button entities for device: %s", model_name)
        return

    def _create_buttons() -> list[PhilipsHomeIDButton]:
        from .local_models import VENUS_STYLE_PORTS

        entities: list[PhilipsHomeIDButton] = []
        airfryer_port = coordinator.device_info.airfryer_port
        for description in button_descriptions:
            if description.cloud_only and not entry.data.get(CONF_CLOUD_REFRESH_TOKEN):
                continue
            if description.local_only and coordinator.mqtt_client is not None:
                continue
            if description.venus_only and airfryer_port not in VENUS_STYLE_PORTS:
                continue
            entities.append(
                PhilipsHomeIDButton(coordinator, description, coordinator.device_id)
            )
        return entities

    # Create buttons if the watched property is already available
    if coordinator.has_property(watch_prop[0], watch_prop[1]):
        async_add_entities(_create_buttons())
        return

    # Dynamic creation when the watched property arrives late
    created = False

    def handle_new_properties(
        new_properties: list[tuple[str, str | None]],
    ) -> None:
        nonlocal created
        if created:
            return
        for prop_key, nested_key in new_properties:
            if (prop_key, nested_key) == watch_prop:
                created = True
                _LOGGER.info("Creating buttons for newly discovered %s", device_type)
                async_add_entities(_create_buttons(), update_before_add=True)
                return

    unregister = coordinator.register_new_property_callback(handle_new_properties)
    entry.async_on_unload(unregister)


class PhilipsHomeIDButton(PhilipsHomeIDEntity, ButtonEntity):
    """Button entity for Philips HomeID."""

    entity_description: PhilipsHomeIDButtonEntityDescription

    def __init__(
        self,
        coordinator: PhilipsHomeIDCoordinator,
        description: PhilipsHomeIDButtonEntityDescription,
        device_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    async def async_press(self) -> None:
        """Handle the button press."""
        if self.entity_description.press_fn:
            await self.entity_description.press_fn(self.coordinator)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False
        if self.entity_description.available_key:
            return self._has_property(self.entity_description.available_key)
        return True

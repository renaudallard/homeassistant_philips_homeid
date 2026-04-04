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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Only create button entities for airfryers
    model_name = coordinator.device_info.model_name or ""
    device_type = get_device_type(model_name)

    if device_type not in ("airfryer", "airfryer_dual", "multicooker"):
        _LOGGER.debug(
            "Skipping button entities for non-airfryer device: %s", model_name
        )
        return

    def _create_buttons() -> list[PhilipsHomeIDButton]:
        entities: list[PhilipsHomeIDButton] = []
        for description in AIRFRYER_BUTTONS:
            if description.cloud_only and not entry.data.get(CONF_CLOUD_REFRESH_TOKEN):
                continue
            entities.append(
                PhilipsHomeIDButton(coordinator, description, coordinator.device_id)
            )
        return entities

    # Create buttons if airfryer data is available
    if coordinator.has_property("status", "airfryer"):
        async_add_entities(_create_buttons())
        return

    # Dynamic creation when airfryer properties arrive late
    created = False

    def handle_new_properties(
        new_properties: list[tuple[str, str | None]],
    ) -> None:
        nonlocal created
        if created:
            return
        for prop_key, nested_key in new_properties:
            if prop_key == "status" and nested_key == "airfryer":
                created = True
                _LOGGER.info("Creating buttons for newly discovered airfryer")
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

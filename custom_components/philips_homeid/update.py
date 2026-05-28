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
"""Update platform for Philips HomeID."""

from __future__ import annotations

import logging

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .entity import PhilipsHomeIDEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up update entity from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Only create if firmware data is available
    if coordinator.has_property("version", "firmware"):
        async_add_entities([PhilipsHomeIDUpdate(coordinator, coordinator.device_id)])


class PhilipsHomeIDUpdate(PhilipsHomeIDEntity, UpdateEntity):
    """Firmware update entity for Philips HomeID devices."""

    _attr_translation_key = "firmware"
    _attr_supported_features = UpdateEntityFeature(0)

    def __init__(
        self,
        coordinator: PhilipsHomeIDCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the update entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_id}_firmware"

    @property
    def installed_version(self) -> str | None:
        """Return the installed firmware version."""
        return self._get_property_value("version", "firmware")

    @property
    def latest_version(self) -> str | None:
        """Return the latest available firmware version.

        Devices report the new version in different shapes: most send a
        plain version string, some send a dict containing the version
        under one of a few keys. Only accept actual version-like strings;
        anything else (booleans, ints, empty payloads) falls through to
        the installed version so HA shows "up to date" instead of
        rendering nonsense like "True" as the latest version.
        """
        upgrade = self._get_property_value("upgrade", "firmware")
        if isinstance(upgrade, str) and upgrade:
            return upgrade
        if isinstance(upgrade, dict):
            for key in ("version", "new_version", "fw_version"):
                value = upgrade.get(key)
                if isinstance(value, str) and value:
                    return value
        return self.installed_version

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
"""Base entity for Philips HomeID integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PhilipsHomeIDCoordinator
from .local_api import LocalDeviceState

_LOGGER = logging.getLogger(__name__)


class PhilipsHomeIDEntity(CoordinatorEntity[PhilipsHomeIDCoordinator]):
    """Base entity for Philips HomeID devices."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PhilipsHomeIDCoordinator,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)

        device_info = coordinator.device_info
        device_id = device_info.cpp_id or device_info.ip_address

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device_info.friendly_name
            or device_info.model_name
            or device_info.ip_address,
            manufacturer="Philips",
            model=device_info.model_name or device_info.model_number,
        )

        _LOGGER.debug(
            "Created entity for device %s (model=%s)",
            device_info.friendly_name or device_info.ip_address,
            device_info.model_name,
        )

    @property
    def device_state(self) -> LocalDeviceState | None:
        """Get current device state."""
        return self.coordinator.device_state

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.coordinator.available

    def _get_property_value(
        self,
        property_key: str | None,
        nested_key: str | None = None,
    ) -> Any:
        """Get a property value from device state, handling nested properties."""
        state = self.device_state
        if not state or not property_key:
            return None

        if nested_key:
            nested = state.properties.get(nested_key)
            if nested and isinstance(nested, dict):
                return nested.get(property_key)
            return None
        return state.properties.get(property_key)

    def _has_property(
        self,
        property_key: str | None,
        nested_key: str | None = None,
    ) -> bool:
        """Check if a property exists in device state."""
        state = self.device_state
        if not state or not property_key:
            return True  # No property to check = available

        if nested_key:
            nested = state.properties.get(nested_key)
            if nested and isinstance(nested, dict):
                return property_key in nested
            return False
        return property_key in state.properties

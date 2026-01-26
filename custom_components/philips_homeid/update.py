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
"""Update platform for Philips HomeID firmware updates."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
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
    """Set up update entities from config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device_id, device in coordinator.data.items():
        entities.append(PhilipsHomeIDUpdate(coordinator, device))

    async_add_entities(entities)


class PhilipsHomeIDUpdate(PhilipsHomeIDEntity, UpdateEntity):
    """Firmware update entity for Philips HomeID devices."""

    _attr_name = "Firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    def __init__(self, coordinator: PhilipsHomeIDCoordinator, device) -> None:
        """Initialize the update entity."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_firmware"
        self._ota_status: dict[str, Any] = {}
        self._installing = False
        self._install_progress: int | None = None

    @property
    def installed_version(self) -> str | None:
        """Return the installed firmware version."""
        device = self.device
        if device:
            return device.firmware_version
        return None

    @property
    def latest_version(self) -> str | None:
        """Return the latest available firmware version."""
        if self._ota_status:
            jobs = self._ota_status.get("jobs", [])
            if jobs:
                # Get the latest available update
                for job in jobs:
                    if job.get("status") in ["pending", "available"]:
                        return job.get("targetVersion")
        return self.installed_version  # No update available

    @property
    def in_progress(self) -> bool | int:
        """Return if update is in progress."""
        if self._installing:
            return self._install_progress if self._install_progress else True
        return False

    @property
    def release_url(self) -> str | None:
        """Return URL to release notes."""
        return None

    @property
    def release_summary(self) -> str | None:
        """Return release summary."""
        if self._ota_status:
            jobs = self._ota_status.get("jobs", [])
            if jobs:
                for job in jobs:
                    if job.get("status") in ["pending", "available"]:
                        return job.get("description", "Firmware update available")
        return None

    async def async_update(self) -> None:
        """Update the entity."""
        await super().async_update()

        # Fetch OTA status
        try:
            self._ota_status = await self.coordinator.async_get_ota_status(
                self._device_id
            )
        except Exception as err:
            _LOGGER.debug("Failed to fetch OTA status for %s: %s", self._device_id, err)

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install the firmware update."""
        self._installing = True
        self._install_progress = 0
        self.async_write_ha_state()

        try:
            # Find the job ID for the target version
            job_id = None
            if self._ota_status:
                jobs = self._ota_status.get("jobs", [])
                for job in jobs:
                    if version is None or job.get("targetVersion") == version:
                        job_id = job.get("id")
                        break

            # Start the OTA update
            success = await self.coordinator.async_start_ota_update(
                self._device_id, job_id
            )

            if not success:
                _LOGGER.error("Failed to start firmware update for %s", self._device_id)

        except Exception as err:
            _LOGGER.error("Error starting firmware update: %s", err)

        finally:
            # The actual progress will be tracked through device state updates
            self._installing = False
            self._install_progress = None
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {}

        if self._ota_status:
            jobs = self._ota_status.get("jobs", [])
            if jobs:
                attrs["available_updates"] = len(
                    [j for j in jobs if j.get("status") in ["pending", "available"]]
                )
                attrs["update_jobs"] = [
                    {
                        "version": j.get("targetVersion"),
                        "status": j.get("status"),
                        "description": j.get("description"),
                    }
                    for j in jobs
                ]

        return attrs

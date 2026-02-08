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
"""Data update coordinator for Philips HomeID."""

from __future__ import annotations

from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import ACTIVE_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .local_api import (
    AIRFRYER_STATUS_COOKING,
    AIRFRYER_STATUS_PAUSED,
    AIRFRYER_STATUS_SETTING,
    LocalDeviceInfo,
    LocalDeviceState,
    PhilipsLocalAPI,
)

_LOGGER = logging.getLogger(__name__)


class PhilipsHomeIDCoordinator(DataUpdateCoordinator[LocalDeviceState | None]):
    """Coordinator for Philips HomeID devices using local API."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: PhilipsLocalAPI,
        device_info: LocalDeviceInfo,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device_info.cpp_id or device_info.ip_address}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self.device_info = device_info
        self.config_entry = entry
        self._state: LocalDeviceState | None = None
        self._last_update_time: float = 0.0  # Timestamp of last successful poll
        self._seen_properties: set[str] = set()  # Track all properties ever seen
        self._new_properties_callbacks: list[
            callable
        ] = []  # Callbacks for new properties

    def _is_airfryer_active(self, state: LocalDeviceState) -> bool:
        """Check if airfryer is actively cooking."""
        airfryer = state.properties.get("airfryer")
        if not airfryer or not isinstance(airfryer, dict):
            return False
        status = airfryer.get("status", "")
        return status in (
            AIRFRYER_STATUS_COOKING,
            AIRFRYER_STATUS_PAUSED,
            AIRFRYER_STATUS_SETTING,
        )

    def _update_polling_interval(self, state: LocalDeviceState | None) -> None:
        """Adjust polling interval based on device state."""
        if state and self._is_airfryer_active(state):
            new_interval = timedelta(seconds=ACTIVE_SCAN_INTERVAL)
        else:
            new_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

        if self.update_interval != new_interval:
            _LOGGER.debug(
                "Changing polling interval from %s to %s",
                self.update_interval,
                new_interval,
            )
            self.update_interval = new_interval

    async def _async_update_data(self) -> LocalDeviceState | None:
        """Fetch data from device via local API."""
        try:
            _LOGGER.debug("Polling device at %s", self.device_info.ip_address)

            # Get full state from device
            state = await self.api.get_full_state(self.device_info)

            if state:
                # Check for new properties before updating state
                new_properties = self._check_for_new_properties(state)

                self._state = state
                self._last_update_time = time.monotonic()
                _LOGGER.debug(
                    "Device %s: power=%s, properties=%s",
                    self.device_info.ip_address,
                    state.power_on,
                    list(state.properties.keys()),
                )
                # Adjust polling interval based on cooking state
                self._update_polling_interval(state)

                # Notify about new properties (after state is updated)
                if new_properties:
                    self._notify_new_properties(new_properties)

                return state
            else:
                _LOGGER.warning(
                    "No response from device at %s", self.device_info.ip_address
                )
                # Return cached state if available
                return self._state

        except Exception as err:
            _LOGGER.exception("Error fetching data from device")
            raise UpdateFailed(f"Error communicating with device: {err}") from err

    async def async_set_power(self, power_on: bool) -> bool:
        """Set device power state."""
        result = await self.api.set_power(self.device_info, power_on)
        if result:
            # Update local state immediately
            if self._state:
                self._state.power_on = power_on
            # Request a refresh
            await self.async_request_refresh()
        return result

    async def async_set_mode(self, mode: str) -> bool:
        """Set device mode."""
        result = await self.api.set_mode(self.device_info, mode)
        if result:
            if self._state:
                self._state.properties["mode"] = mode
            await self.async_request_refresh()
        return result

    async def async_set_fan_speed(self, speed: str) -> bool:
        """Set fan speed."""
        result = await self.api.set_fan_speed(self.device_info, speed)
        if result:
            if self._state:
                self._state.properties["om"] = speed
            await self.async_request_refresh()
        return result

    async def async_set_child_lock(self, locked: bool) -> bool:
        """Set child lock state."""
        result = await self.api.set_child_lock(self.device_info, locked)
        if result:
            if self._state:
                self._state.properties["cl"] = locked
            await self.async_request_refresh()
        return result

    # Airfryer-specific methods
    async def async_airfryer_start(self) -> bool:
        """Start airfryer cooking."""
        result = await self.api.airfryer_start_cooking(self.device_info)
        if result:
            await self.async_request_refresh()
        return result

    async def async_airfryer_pause(self) -> bool:
        """Pause airfryer cooking."""
        result = await self.api.airfryer_pause(self.device_info)
        if result:
            await self.async_request_refresh()
        return result

    async def async_airfryer_stop(self) -> bool:
        """Stop airfryer cooking."""
        result = await self.api.airfryer_stop(self.device_info)
        if result:
            await self.async_request_refresh()
        return result

    async def async_airfryer_set_settings(
        self,
        temp: int | None = None,
        time_seconds: int | None = None,
        temp_unit_fahrenheit: bool = False,
        preset: int | None = None,
    ) -> bool:
        """Set airfryer cooking settings."""
        result = await self.api.airfryer_set_settings(
            self.device_info, temp, time_seconds, temp_unit_fahrenheit, preset
        )
        if result:
            await self.async_request_refresh()
        return result

    @property
    def device_state(self) -> LocalDeviceState | None:
        """Get current device state."""
        return self._state

    @property
    def device_id(self) -> str:
        """Return unique device identifier."""
        return self.device_info.cpp_id or self.device_info.ip_address

    @property
    def available(self) -> bool:
        """Return True if device is available."""
        return self._state is not None

    @property
    def last_update_time(self) -> float:
        """Return timestamp of last successful poll."""
        return self._last_update_time

    def is_airfryer_cooking(self) -> bool:
        """Check if airfryer is actively cooking (not paused)."""
        if not self._state:
            return False
        airfryer = self._state.properties.get("airfryer")
        if not airfryer or not isinstance(airfryer, dict):
            return False
        return airfryer.get("status") == AIRFRYER_STATUS_COOKING

    def has_property(
        self,
        property_key: str | None,
        nested_key: str | None = None,
    ) -> bool:
        """Check if a property exists in current device state.

        Used during entity setup to filter which entities to create.
        """
        if not self._state or not property_key:
            return False

        if nested_key:
            nested = self._state.properties.get(nested_key)
            if nested and isinstance(nested, dict):
                return property_key in nested
            return False
        return property_key in self._state.properties

    def _get_property_key(self, property_key: str, nested_key: str | None) -> str:
        """Generate a unique key for tracking properties."""
        if nested_key:
            return f"{nested_key}.{property_key}"
        return property_key

    def is_property_seen(
        self, property_key: str, nested_key: str | None = None
    ) -> bool:
        """Check if a property has ever been seen."""
        key = self._get_property_key(property_key, nested_key)
        return key in self._seen_properties

    def mark_property_seen(
        self, property_key: str, nested_key: str | None = None
    ) -> None:
        """Mark a property as seen (entity created for it)."""
        key = self._get_property_key(property_key, nested_key)
        self._seen_properties.add(key)

    def register_new_property_callback(self, callback: callable) -> callable:
        """Register a callback to be called when new properties are discovered.

        Returns a function to unregister the callback.
        """
        self._new_properties_callbacks.append(callback)

        def unregister() -> None:
            if callback in self._new_properties_callbacks:
                self._new_properties_callbacks.remove(callback)

        return unregister

    def _check_for_new_properties(
        self, state: LocalDeviceState
    ) -> list[tuple[str, str | None]]:
        """Check for new properties that haven't been seen before.

        Returns list of (property_key, nested_key) tuples for new properties.
        """
        new_properties: list[tuple[str, str | None]] = []

        # Check top-level properties
        for key in state.properties:
            if key not in self._seen_properties:
                # Check if it's a nested dict (like airfryer)
                value = state.properties[key]
                if isinstance(value, dict):
                    # Check nested properties
                    for nested_prop in value:
                        full_key = f"{key}.{nested_prop}"
                        if full_key not in self._seen_properties:
                            new_properties.append((nested_prop, key))
                else:
                    new_properties.append((key, None))

        return new_properties

    def _notify_new_properties(
        self, new_properties: list[tuple[str, str | None]]
    ) -> None:
        """Notify callbacks about new properties."""
        if not new_properties or not self._new_properties_callbacks:
            return

        _LOGGER.debug("New properties discovered: %s", new_properties)
        for callback in self._new_properties_callbacks:
            try:
                callback(new_properties)
            except Exception:
                _LOGGER.exception("Error in new property callback")

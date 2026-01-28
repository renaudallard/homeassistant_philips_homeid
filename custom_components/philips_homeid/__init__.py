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
"""The Philips HomeID integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_CPP_ID,
    CONF_MODEL,
    DOMAIN,
)
from .coordinator import PhilipsHomeIDCoordinator
from .local_api import LocalDeviceInfo, PhilipsLocalAPI

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.FAN,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Philips HomeID component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Philips HomeID from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Get device configuration
    host = entry.data.get(CONF_HOST)
    cpp_id = entry.data.get(CONF_CPP_ID, "")
    model = entry.data.get(CONF_MODEL, "")
    client_id = entry.data.get(CONF_CLIENT_ID)
    client_secret = entry.data.get(CONF_CLIENT_SECRET)

    if not host:
        _LOGGER.error("No host configured for device")
        return False

    # Create device info
    device_info = LocalDeviceInfo(
        ip_address=host,
        cpp_id=cpp_id,
        model_name=model,
        client_id=client_id,
        client_secret=client_secret,
    )

    # Create local API client
    api = PhilipsLocalAPI()

    # Probe device to verify connectivity
    try:
        probed = await api.probe_device(host)
        if probed:
            # Update device info with probed data
            if probed.cpp_id:
                device_info.cpp_id = probed.cpp_id
            if probed.model_name:
                device_info.model_name = probed.model_name
            if probed.friendly_name:
                device_info.friendly_name = probed.friendly_name
            _LOGGER.info(
                "Connected to device %s at %s (model: %s)",
                device_info.friendly_name or device_info.cpp_id,
                host,
                device_info.model_name,
            )
        else:
            _LOGGER.warning("Could not probe device at %s, will try polling anyway", host)
    except Exception as err:
        _LOGGER.error("Failed to connect to device at %s: %s", host, err)
        await api.close()
        raise ConfigEntryNotReady(f"Could not connect to device at {host}") from err

    # Create coordinator
    coordinator = PhilipsHomeIDCoordinator(hass, api, device_info, entry)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.api.close()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)

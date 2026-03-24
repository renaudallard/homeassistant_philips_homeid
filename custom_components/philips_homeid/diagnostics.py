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
"""Diagnostics support for Philips HomeID."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_ENCRYPTION_KEY, DOMAIN
from .coordinator import PhilipsHomeIDCoordinator

REDACT_KEYS = {CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_ENCRYPTION_KEY, CONF_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: PhilipsHomeIDCoordinator = hass.data[DOMAIN][entry.entry_id]
    device = coordinator.device_info

    # Redact sensitive config data
    config_data = {}
    for key, value in entry.data.items():
        if key in REDACT_KEYS:
            config_data[key] = "**REDACTED**"
        else:
            config_data[key] = value

    # Device info
    device_data = {
        "model_name": device.model_name,
        "model_number": device.model_number,
        "friendly_name": device.friendly_name,
        "cpp_id": device.cpp_id,
        "protocol_version": device.protocol_version,
        "product_id": device.product_id,
        "use_https": device.use_https,
        "airfryer_port": device.airfryer_port,
    }

    # Current state
    state_data: dict[str, Any] = {}
    state = coordinator.device_state
    if state:
        state_data = {
            "power_on": state.power_on,
            "connection_state": state.connection_state,
            "properties": state.properties,
        }

    # Coordinator status
    coordinator_data = {
        "update_interval": str(coordinator.update_interval),
        "last_update_time": coordinator.last_update_time,
        "consecutive_failures": coordinator._consecutive_failures,
        "available": coordinator.available,
        "preheat_enabled": coordinator.preheat_enabled,
        "keep_warm_time": coordinator.keep_warm_time,
        "keep_warm_temp": coordinator.keep_warm_temp,
    }

    return {
        "config": config_data,
        "device": device_data,
        "state": state_data,
        "coordinator": coordinator_data,
    }

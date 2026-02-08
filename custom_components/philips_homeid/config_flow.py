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
"""Config flow for Philips HomeID integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import ssdp, zeroconf
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_CPP_ID,
    CONF_DEVICE_ID,
    CONF_MODEL,
    CONF_USE_HTTPS,
    DOMAIN,
)
from .local_api import (
    LocalDeviceInfo,
    PhilipsLocalAPI,
    parse_ssdp_device,
    parse_zeroconf_device,
)

_LOGGER = logging.getLogger(__name__)

# Pairing instructions for different device types
PAIRING_INSTRUCTIONS = {
    "airfryer": (
        "Device must be in pairing mode (factory reset or unpaired from HomeID app).\n\n"
        "If pairing fails, check 'Enter credentials manually' below."
    ),
    "default": (
        "Device must be in pairing mode.\n\n"
        "If pairing fails, check 'Enter credentials manually' below."
    ),
}

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
    }
)


class PhilipsHomeIDConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Philips HomeID."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_device: LocalDeviceInfo | None = None
        self._local_api: PhilipsLocalAPI | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle initial step - go directly to manual host entry."""
        return await self.async_step_manual_host(user_input)

    async def async_step_manual_host(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual configuration - enter IP address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]

            try:
                # Probe the device
                self._local_api = PhilipsLocalAPI()
                device = await self._local_api.probe_device(host)

                if device:
                    self._discovered_device = device

                    # Set unique ID based on device ID
                    unique_id = device.cpp_id or host
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})

                    return await self.async_step_confirm()
                else:
                    errors["base"] = "cannot_connect"

            except Exception:
                _LOGGER.exception("Unexpected exception during device probe")
                errors["base"] = "unknown"
            finally:
                if self._local_api:
                    await self._local_api.close()

        return self.async_show_form(
            step_id="manual_host",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm the discovered device."""
        if user_input is not None:
            # Try to pair with the device
            return await self.async_step_pair()

        device = self._discovered_device
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": device.friendly_name or device.model_name or "Unknown Device",
                "host": device.ip_address,
                "model": device.model_name or "Unknown",
            },
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pair with the device to obtain credentials."""
        errors: dict[str, str] = {}
        device = self._discovered_device
        error_message = ""

        if user_input is not None:
            # Check if user wants to enter credentials manually
            if user_input.get("manual_entry"):
                return await self.async_step_manual_credentials()

            # Try pairing
            try:
                self._local_api = PhilipsLocalAPI()

                # First try to clear existing pairing
                clear_success, clear_msg = await self._local_api.try_clear_pairing(
                    device
                )
                if clear_success:
                    _LOGGER.info("Cleared existing pairing: %s", clear_msg)

                # Now try to pair
                success, message = await self._local_api.pair_device(device)

                if success:
                    return self.async_create_entry(
                        title=device.friendly_name
                        or device.model_name
                        or device.ip_address,
                        data={
                            CONF_HOST: device.ip_address,
                            CONF_CPP_ID: device.cpp_id,
                            CONF_MODEL: device.model_name,
                            CONF_DEVICE_ID: device.cpp_id,
                            CONF_CLIENT_ID: device.client_id,
                            CONF_CLIENT_SECRET: device.client_secret,
                            CONF_USE_HTTPS: device.use_https,
                        },
                    )
                else:
                    errors["base"] = "pairing_failed"
                    error_message = f"\n\n**Error:** {message}"
                    error_message += (
                        "\n\n**Options:**\n"
                        "- Remove device from HomeID app and try again\n"
                        "- Factory reset the device and try again\n"
                        "- Check 'Enter credentials manually' below if you have them"
                    )

            except Exception:
                _LOGGER.exception("Unexpected exception during pairing")
                errors["base"] = "unknown"
            finally:
                if self._local_api:
                    await self._local_api.close()
                    self._local_api = None

        # Determine pairing instructions based on device type
        model_lower = (device.model_name or "").lower()
        if "airfryer" in model_lower:
            instructions = PAIRING_INSTRUCTIONS["airfryer"]
        else:
            instructions = PAIRING_INSTRUCTIONS["default"]

        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema(
                {
                    vol.Optional("manual_entry", default=False): bool,
                }
            ),
            description_placeholders={
                "name": device.friendly_name or device.model_name or "Unknown Device",
                "instructions": instructions + error_message,
            },
            errors=errors,
        )

    async def async_step_manual_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Allow manual entry of credentials."""
        errors: dict[str, str] = {}
        device = self._discovered_device

        if user_input is not None:
            client_id = user_input.get(CONF_CLIENT_ID, "").strip()
            client_secret = user_input.get(CONF_CLIENT_SECRET, "").strip()

            if not client_id or not client_secret:
                errors["base"] = "missing_credentials"
            else:
                # Test the credentials
                try:
                    self._local_api = PhilipsLocalAPI()
                    device.client_id = client_id
                    device.client_secret = client_secret

                    # Try to make a request with these credentials
                    info = await self._local_api.get_device_info(device)
                    if info:
                        return self.async_create_entry(
                            title=device.friendly_name
                            or device.model_name
                            or device.ip_address,
                            data={
                                CONF_HOST: device.ip_address,
                                CONF_CPP_ID: device.cpp_id,
                                CONF_MODEL: device.model_name,
                                CONF_DEVICE_ID: device.cpp_id,
                                CONF_CLIENT_ID: client_id,
                                CONF_CLIENT_SECRET: client_secret,
                                CONF_USE_HTTPS: device.use_https,
                            },
                        )
                    else:
                        errors["base"] = "invalid_credentials"
                except Exception:
                    _LOGGER.exception("Error testing credentials")
                    errors["base"] = "invalid_credentials"
                finally:
                    if self._local_api:
                        await self._local_api.close()
                        self._local_api = None

        return self.async_show_form(
            step_id="manual_credentials",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): str,
                }
            ),
            description_placeholders={
                "name": device.friendly_name or device.model_name or "Unknown Device",
            },
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle zeroconf discovery."""
        _LOGGER.info("Zeroconf discovery: %s", discovery_info)

        # Parse discovery info
        device = parse_zeroconf_device(
            {
                "host": str(discovery_info.host),
                "name": discovery_info.name,
                "properties": discovery_info.properties,
                "type": discovery_info.type,
            }
        )

        if not device:
            return self.async_abort(reason="cannot_connect")

        self._discovered_device = device

        # Set unique ID
        unique_id = device.cpp_id or discovery_info.name
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: device.ip_address})

        # Set context for display
        self.context["title_placeholders"] = {
            "name": device.friendly_name or device.model_name or discovery_info.name,
        }

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm zeroconf discovered device."""
        if user_input is not None:
            # Try to pair with the device
            return await self.async_step_pair()

        device = self._discovered_device
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": device.friendly_name or device.model_name or "Unknown Device",
                "host": device.ip_address,
                "model": device.model_name or "Unknown",
            },
        )

    async def async_step_ssdp(self, discovery_info: ssdp.SsdpServiceInfo) -> FlowResult:
        """Handle SSDP discovery."""
        _LOGGER.info("SSDP discovery: %s", discovery_info)

        # Parse discovery info - use keys directly from upnp dict
        upnp = discovery_info.upnp
        device = parse_ssdp_device(
            {
                "location": discovery_info.ssdp_location or "",
                "udn": upnp.get("UDN", ""),
                "friendlyName": upnp.get("friendlyName", ""),
                "modelName": upnp.get("modelName", ""),
                "modelNumber": upnp.get("modelNumber", ""),
                "serialNumber": upnp.get("serialNumber", ""),
                "cppId": upnp.get("cppId", ""),
            }
        )

        if not device:
            return self.async_abort(reason="cannot_connect")

        self._discovered_device = device

        # Set unique ID - prefer cppId over UDN
        unique_id = device.cpp_id or upnp.get("UDN", "")
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: device.ip_address})

        # Set context for display
        self.context["title_placeholders"] = {
            "name": device.friendly_name or device.model_name or "Philips Device",
        }

        return await self.async_step_ssdp_confirm()

    async def async_step_ssdp_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm SSDP discovered device."""
        if user_input is not None:
            # Try to pair with the device
            return await self.async_step_pair()

        device = self._discovered_device
        return self.async_show_form(
            step_id="ssdp_confirm",
            description_placeholders={
                "name": device.friendly_name or device.model_name or "Unknown Device",
                "host": device.ip_address,
                "model": device.model_name or "Unknown",
            },
        )

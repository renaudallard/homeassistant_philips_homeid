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
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST

from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from homeassistant.core import callback

from .cloud_api import CloudAuthError, ConsentRequired, PhilipsCloudAPI
from .const import (
    ACTIVE_SCAN_INTERVAL,
    CONF_ACTIVE_SCAN_INTERVAL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_CPP_ID,
    CONF_DEVICE_ID,
    CONF_ENCRYPTION_KEY,
    CONF_MODEL,
    CONF_SCAN_INTERVAL,
    CONF_USE_HTTPS,
    DEFAULT_SCAN_INTERVAL,
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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> PhilipsHomeIDOptionsFlow:
        """Get the options flow handler."""
        return PhilipsHomeIDOptionsFlow()

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_device: LocalDeviceInfo | None = None
        self._local_api: PhilipsLocalAPI | None = None
        self._cloud_api: PhilipsCloudAPI | None = None
        self._cloud_email: str = ""
        self._cloud_vtoken: str = ""
        self._cloud_session_token: str = ""
        self._cloud_tokens: dict[str, Any] = {}
        self._cloud_devices: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial step - choose between local and cloud setup."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["manual_host", "cloud_email"],
        )

    async def async_step_manual_host(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
    ) -> ConfigFlowResult:
        """Confirm the discovered device."""
        if user_input is not None:
            # Try to pair with the device
            return await self.async_step_pair()

        device = self._discovered_device
        if not device:
            return self.async_abort(reason="cannot_connect")
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
    ) -> ConfigFlowResult:
        """Pair with the device to obtain credentials."""
        errors: dict[str, str] = {}
        device = self._discovered_device
        if not device:
            return self.async_abort(reason="cannot_connect")
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
                    # For HTTP devices, try to fetch the encryption key
                    if not device.use_https:
                        await self._local_api.exchange_encryption_key(device)
                        if not device.encryption_key:
                            _LOGGER.warning(
                                "Failed to obtain encryption key for HTTP device %s after pairing",
                                device.ip_address,
                            )

                    entry_data = {
                        CONF_HOST: device.ip_address,
                        CONF_CPP_ID: device.cpp_id,
                        CONF_MODEL: device.model_name,
                        CONF_DEVICE_ID: device.cpp_id,
                        CONF_CLIENT_ID: device.client_id,
                        CONF_CLIENT_SECRET: device.client_secret,
                        CONF_USE_HTTPS: device.use_https,
                    }
                    if device.encryption_key:
                        entry_data[CONF_ENCRYPTION_KEY] = device.encryption_key
                    return self.async_create_entry(
                        title=device.friendly_name
                        or device.model_name
                        or device.ip_address,
                        data=entry_data,
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
    ) -> ConfigFlowResult:
        """Allow manual entry of credentials."""
        errors: dict[str, str] = {}
        device = self._discovered_device
        if not device:
            return self.async_abort(reason="cannot_connect")

        if user_input is not None:
            client_id = user_input.get(CONF_CLIENT_ID, "").strip()
            client_secret = user_input.get(CONF_CLIENT_SECRET, "").strip()
            encryption_key = user_input.get(CONF_ENCRYPTION_KEY, "").strip()

            if not client_id or not client_secret:
                errors["base"] = "missing_credentials"
            else:
                # Test the credentials
                try:
                    self._local_api = PhilipsLocalAPI()
                    device.client_id = client_id
                    device.client_secret = client_secret
                    if encryption_key:
                        device.encryption_key = encryption_key

                    # For HTTP devices without encryption key, try key exchange
                    if not device.use_https and not device.encryption_key:
                        await self._local_api.exchange_encryption_key(device)

                    if not device.use_https and not device.encryption_key:
                        _LOGGER.warning(
                            "Failed to obtain encryption key from %s — "
                            "credentials may be invalid or device requires "
                            "manual encryption key",
                            device.ip_address,
                        )
                        errors["base"] = "encryption_key_failed"
                    else:
                        # Try to make a request with these credentials
                        info = await self._local_api.get_device_info(device)
                        if info:
                            entry_data = {
                                CONF_HOST: device.ip_address,
                                CONF_CPP_ID: device.cpp_id,
                                CONF_MODEL: device.model_name,
                                CONF_DEVICE_ID: device.cpp_id,
                                CONF_CLIENT_ID: client_id,
                                CONF_CLIENT_SECRET: client_secret,
                                CONF_USE_HTTPS: device.use_https,
                            }
                            if device.encryption_key:
                                entry_data[CONF_ENCRYPTION_KEY] = device.encryption_key
                            return self.async_create_entry(
                                title=device.friendly_name
                                or device.model_name
                                or device.ip_address,
                                data=entry_data,
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
                    vol.Optional(CONF_ENCRYPTION_KEY, default=""): str,
                }
            ),
            description_placeholders={
                "name": device.friendly_name or device.model_name or "Unknown Device",
            },
            errors=errors,
        )

    async def async_step_cloud_email(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle cloud login - email entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input.get("email", "").strip()
            if email:
                try:
                    self._cloud_api = PhilipsCloudAPI()
                    vtoken = await self._cloud_api.request_otp(email)
                    self._cloud_email = email
                    self._cloud_vtoken = vtoken
                    return await self.async_step_cloud_otp()
                except CloudAuthError as err:
                    _LOGGER.error("OTP request failed: %s", err)
                    errors["base"] = "otp_send_failed"
                except Exception:
                    _LOGGER.exception("Unexpected error sending OTP")
                    errors["base"] = "unknown"
            else:
                errors["base"] = "missing_email"

        return self.async_show_form(
            step_id="cloud_email",
            data_schema=vol.Schema({vol.Required("email"): str}),
            errors=errors,
        )

    async def async_step_cloud_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle cloud login - OTP code entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input.get("code", "").strip()
            if code and self._cloud_api:
                try:
                    session_token = await self._cloud_api.verify_otp(
                        self._cloud_email, code, self._cloud_vtoken
                    )
                    self._cloud_session_token = session_token

                    # Try auto-consent OAuth
                    tokens = await self._cloud_api.get_oidc_tokens(session_token)
                    self._cloud_tokens = tokens

                    # Get device list
                    devices = await self._cloud_api.get_devices(tokens["access_token"])
                    self._cloud_devices = devices

                    if not devices:
                        errors["base"] = "no_cloud_devices"
                    else:
                        return await self.async_step_cloud_devices()

                except ConsentRequired:
                    errors["base"] = "consent_required"
                except CloudAuthError as err:
                    _LOGGER.error("Cloud auth failed: %s", err)
                    errors["base"] = "otp_failed"
                except Exception:
                    _LOGGER.exception("Unexpected error during cloud auth")
                    errors["base"] = "unknown"
            else:
                errors["base"] = "missing_code"

        return self.async_show_form(
            step_id="cloud_otp",
            data_schema=vol.Schema({vol.Required("code"): str}),
            description_placeholders={"email": self._cloud_email},
            errors=errors,
        )

    async def async_step_cloud_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle cloud login - device selection."""
        errors: dict[str, str] = {}

        if user_input is not None and self._cloud_api:
            selected = user_input.get("device")
            if selected:
                # Find selected device
                device_data = None
                for dev in self._cloud_devices:
                    dev_id = dev.get("id", "")
                    if dev_id == selected:
                        device_data = dev
                        break

                if device_data:
                    # Try to get credentials
                    ctn = device_data.get("ctn", "")
                    try:
                        cred_list = await self._cloud_api.get_device_credentials(
                            self._cloud_tokens["access_token"],
                            [device_data["id"]],
                            [ctn] if ctn else [],
                        )
                    except Exception:
                        _LOGGER.exception("Error fetching credentials")
                        cred_list = []
                    finally:
                        await self._cloud_api.close()
                        self._cloud_api = None

                    # Extract credentials
                    creds = None
                    for cred_dev in cred_list:
                        parsed = cred_dev.get("parsed_credentials")
                        if parsed:
                            creds = parsed
                            break

                    if creds:
                        # Probe device to get IP and details
                        host = device_data.get("ipAddress", "")
                        if not host:
                            errors["base"] = "cloud_no_ip"
                        else:
                            entry_data = {
                                CONF_HOST: host,
                                CONF_CPP_ID: device_data.get("id", ""),
                                CONF_MODEL: ctn,
                                CONF_DEVICE_ID: device_data.get("id", ""),
                                CONF_CLIENT_ID: creds.get("client_id", ""),
                                CONF_CLIENT_SECRET: creds.get("client_secret", ""),
                                CONF_USE_HTTPS: True,
                                CONF_CLOUD_REFRESH_TOKEN: self._cloud_tokens.get(
                                    "refresh_token", ""
                                ),
                            }
                            enc_key = creds.get("encryption_key")
                            if enc_key:
                                entry_data[CONF_ENCRYPTION_KEY] = enc_key

                            await self.async_set_unique_id(device_data.get("id", host))
                            self._abort_if_unique_id_configured()

                            return self.async_create_entry(
                                title=device_data.get("friendlyName", ctn)
                                or ctn
                                or host,
                                data=entry_data,
                            )
                    else:
                        errors["base"] = "cloud_credentials_not_found"

        # Build device selection dropdown
        device_options = {}
        for dev in self._cloud_devices:
            dev_id = dev.get("id", "")
            name = dev.get("friendlyName", "") or dev.get("ctn", dev_id)
            ctn = dev.get("ctn", "")
            label = f"{name} ({ctn})" if ctn else name
            device_options[dev_id] = label

        return self.async_show_form(
            step_id="cloud_devices",
            data_schema=vol.Schema({vol.Required("device"): vol.In(device_options)}),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
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
    ) -> ConfigFlowResult:
        """Confirm zeroconf discovered device."""
        if user_input is not None:
            return await self.async_step_pair()

        device = self._discovered_device
        if not device:
            return self.async_abort(reason="cannot_connect")
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": device.friendly_name or device.model_name or "Unknown Device",
                "host": device.ip_address,
                "model": device.model_name or "Unknown",
            },
        )

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
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
    ) -> ConfigFlowResult:
        """Confirm SSDP discovered device."""
        if user_input is not None:
            return await self.async_step_pair()

        device = self._discovered_device
        if not device:
            return self.async_abort(reason="cannot_connect")
        return self.async_show_form(
            step_id="ssdp_confirm",
            description_placeholders={
                "name": device.friendly_name or device.model_name or "Unknown Device",
                "host": device.ip_address,
                "model": device.model_name or "Unknown",
            },
        )


class PhilipsHomeIDOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Philips HomeID."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
                    vol.Optional(
                        CONF_ACTIVE_SCAN_INTERVAL,
                        default=options.get(
                            CONF_ACTIVE_SCAN_INTERVAL, ACTIVE_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=60)),
                }
            ),
        )

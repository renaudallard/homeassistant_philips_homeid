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

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST

from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from homeassistant.core import callback

from .cloud_api import CloudAuthError, PhilipsCloudAPI
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
        self._cloud_source: str = ""  # "homeid" or "iot"
        self._install_task: asyncio.Task[bool] | None = None

    async def _close_cloud_api(self) -> None:
        """Close cloud API session if open."""
        if self._cloud_api:
            await self._cloud_api.close()
            self._cloud_api = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial step - go to manual host entry."""
        return await self.async_step_manual_host(user_input)

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
            return await self.async_step_cloud_email()

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
        """Handle cloud login - email entry (default auth method)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("manual_entry"):
                return await self.async_step_manual_credentials()

            email = user_input.get("email", "").strip()
            if email:
                self._cloud_email = email
                self._cloud_api = PhilipsCloudAPI()
                return await self.async_step_cloud_install()
            else:
                errors["base"] = "missing_email"

        return self.async_show_form(
            step_id="cloud_email",
            data_schema=vol.Schema(
                {
                    vol.Required("email"): str,
                    vol.Optional("manual_entry", default=False): bool,
                }
            ),
            errors=errors,
        )

    async def _async_install_and_send_otp(self) -> bool:
        """Install playwright and send OTP in one background task."""
        assert self._cloud_api is not None
        if not await self._cloud_api.async_install_playwright():
            raise CloudAuthError("Failed to install playwright")
        vtoken = await self._cloud_api.request_otp(self._cloud_email)
        self._cloud_vtoken = vtoken
        return True

    async def async_step_cloud_install(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Install playwright and send OTP with progress indicator."""
        if self._install_task is None:
            # Check platform before attempting install
            platform_error = PhilipsCloudAPI.check_playwright_platform()
            if platform_error:
                _LOGGER.error(platform_error)
                return self.async_abort(reason="playwright_unsupported")

            self._install_task = self.hass.async_create_task(
                self._async_install_and_send_otp()
            )

        if not self._install_task.done():
            return self.async_show_progress(
                step_id="cloud_install",
                progress_action="cloud_install",
                progress_task=self._install_task,
            )

        try:
            await self._install_task
        except CloudAuthError as err:
            _LOGGER.error("Cloud setup failed: %s", err)
            self._install_task = None
            await self._close_cloud_api()
            return self.async_show_progress_done(next_step_id="cloud_install_failed")
        except Exception:
            _LOGGER.exception("Unexpected error during cloud setup")
            self._install_task = None
            await self._close_cloud_api()
            return self.async_show_progress_done(next_step_id="cloud_install_failed")
        finally:
            self._install_task = None

        return self.async_show_progress_done(next_step_id="cloud_otp")

    async def async_step_cloud_install_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle cloud install/OTP failure."""
        return self.async_abort(reason="cloud_setup_failed")

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

                    # Headless browser OAuth (playwright already installed)
                    tokens = await self._cloud_api.get_oidc_tokens(session_token)
                    self._cloud_tokens = tokens

                    # Try Home ID HAL API first (the app's primary backend)
                    _LOGGER.debug("Trying Home ID API for appliances")
                    appliances = await self._cloud_api.get_appliances_via_homeid(
                        tokens, self._cloud_email
                    )

                    if appliances:
                        self._cloud_devices = appliances
                        self._cloud_source = "homeid"
                        return await self.async_step_cloud_devices()

                    # Fall back to IoT API
                    _LOGGER.debug("Home ID API returned no appliances, trying IoT API")

                    # Verify token with user profile (IoT)
                    try:
                        profile = await self._cloud_api.get_user_profile(
                            tokens["access_token"]
                        )
                        _LOGGER.debug("IoT user: id=%s", profile.get("id", "unknown"))
                    except CloudAuthError:
                        _LOGGER.warning("Could not fetch IoT user profile (non-fatal)")

                    devices = await self._cloud_api.get_devices(tokens["access_token"])

                    # Also query homes for debug context
                    try:
                        homes = await self._cloud_api.get_homes(tokens["access_token"])
                        if homes:
                            _LOGGER.debug("IoT homes: %d found", len(homes))
                    except Exception:
                        pass

                    if devices:
                        self._cloud_devices = devices
                        self._cloud_source = "iot"
                        return await self.async_step_cloud_devices()

                    errors["base"] = "no_cloud_devices"

                except CloudAuthError as err:
                    _LOGGER.error("Cloud auth failed: %s", err)
                    errors["base"] = "otp_failed"
                    await self._close_cloud_api()
                except Exception:
                    _LOGGER.exception("Unexpected error during cloud auth")
                    errors["base"] = "unknown"
                    await self._close_cloud_api()
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
                device_data = None
                for idx, dev in enumerate(self._cloud_devices):
                    if str(idx) == selected:
                        device_data = dev
                        break

                if device_data:
                    if self._cloud_source == "homeid":
                        result = await self._create_entry_from_homeid(
                            device_data, errors
                        )
                    else:
                        result = await self._create_entry_from_iot(device_data, errors)
                    if result:
                        return result

        # Build device selection dropdown (use index as key for both sources)
        device_options: dict[str, str] = {}
        for idx, dev in enumerate(self._cloud_devices):
            if self._cloud_source == "homeid":
                name = dev.get("name", "") or "Unknown"
                mac = dev.get("macAddress", "")
                label = f"{name} ({mac})" if mac else name
            else:
                name = dev.get("friendlyName", "") or dev.get("ctn", "")
                ctn = dev.get("ctn", "")
                label = f"{name} ({ctn})" if ctn else name
            device_options[str(idx)] = label

        return self.async_show_form(
            step_id="cloud_devices",
            data_schema=vol.Schema({vol.Required("device"): vol.In(device_options)}),
            errors=errors,
        )

    async def _create_entry_from_homeid(
        self, device_data: dict[str, Any], errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Create config entry from Home ID appliance data."""
        client_id = device_data.get("clientId", "")
        client_secret = device_data.get("clientSecret", "")

        if not client_id or not client_secret:
            _LOGGER.warning(
                "Home ID appliance has no credentials: clientId=%s, clientSecret=%s",
                bool(client_id),
                bool(client_secret),
            )
            errors["base"] = "cloud_credentials_not_found"
            return None

        # Resolve IP: use discovered device or MAC-based lookup
        host = ""
        mac = device_data.get("macAddress", "")
        if self._discovered_device:
            host = self._discovered_device.ip_address
        if not host:
            errors["base"] = "cloud_no_ip"
            return None

        # Use MAC as unique ID (matches cpp_id format)
        unique_id = mac or host
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        # Use discovered device model if available, fall back to cloud data
        model = ""
        if self._discovered_device and self._discovered_device.model_name:
            model = self._discovered_device.model_name
        name = device_data.get("name", "") or model or mac or host
        entry_data = {
            CONF_HOST: host,
            CONF_CPP_ID: mac,
            CONF_MODEL: model,
            CONF_DEVICE_ID: mac,
            CONF_CLIENT_ID: client_id,
            CONF_CLIENT_SECRET: client_secret,
            CONF_USE_HTTPS: True,
            CONF_CLOUD_REFRESH_TOKEN: self._cloud_tokens.get("refresh_token", ""),
        }

        await self._close_cloud_api()
        return self.async_create_entry(title=name, data=entry_data)

    async def _create_entry_from_iot(
        self, device_data: dict[str, Any], errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Create config entry from IoT API device data (migration flow)."""
        assert self._cloud_api is not None
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
            await self._close_cloud_api()

        creds = None
        for cred_dev in cred_list:
            parsed = cred_dev.get("parsed_credentials")
            if parsed:
                creds = parsed
                break

        if not creds:
            errors["base"] = "cloud_credentials_not_found"
            return None

        host = device_data.get("ipAddress", "")
        if not host and self._discovered_device:
            host = self._discovered_device.ip_address
        if not host:
            errors["base"] = "cloud_no_ip"
            return None

        unique_id = device_data.get("id", host)
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        entry_data = {
            CONF_HOST: host,
            CONF_CPP_ID: device_data.get("id", ""),
            CONF_MODEL: ctn,
            CONF_DEVICE_ID: device_data.get("id", ""),
            CONF_CLIENT_ID: creds.get("client_id", ""),
            CONF_CLIENT_SECRET: creds.get("client_secret", ""),
            CONF_USE_HTTPS: True,
            CONF_CLOUD_REFRESH_TOKEN: self._cloud_tokens.get("refresh_token", ""),
        }
        enc_key = creds.get("encryption_key")
        if enc_key:
            entry_data[CONF_ENCRYPTION_KEY] = enc_key

        name = device_data.get("friendlyName", ctn) or ctn or host
        return self.async_create_entry(title=name, data=entry_data)

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
            return await self.async_step_cloud_email()

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
            return await self.async_step_cloud_email()

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

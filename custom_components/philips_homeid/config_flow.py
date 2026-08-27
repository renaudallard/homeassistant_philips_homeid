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
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST

from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from homeassistant.core import callback

from .cloud_api import (
    CloudAuthError,
    CloudBackendError,
    CloudConnectionError,
    CloudNotRegisteredError,
    PhilipsCloudAPI,
)
from .const import (
    ACTIVE_SCAN_INTERVAL,
    CONF_ACTIVE_SCAN_INTERVAL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_CPP_ID,
    CONF_DEVICE_ID,
    CONF_ENCRYPTION_KEY,
    CONF_IS_FUSION,
    CONF_MODEL,
    CONF_MQTT_HOST,
    CONF_OAUTH_CLIENT,
    CONF_PLATFORM_REST_URL,
    CONF_SCAN_INTERVAL,
    CONF_TENANT,
    CONF_THING_NAME,
    CONF_USE_HTTPS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FUSION_MQTT_HOST,
    FUSION_PLATFORM_REST_URL,
    FUSION_TENANT,
    OAUTH_CLIENT_AIRPLUS,
    OAUTH_CLIENT_HOMEID,
)
from .local_api import (
    LocalDeviceInfo,
    PhilipsLocalAPI,
    bracket_ipv6,
    parse_ssdp_device,
    parse_zeroconf_device,
)
from .sensor_descriptions import get_device_type
from .util import normalize_unique_id

_LOGGER = logging.getLogger(__name__)


def _sanitize_host(raw: str) -> str | None:
    """Strip scheme, path, port and whitespace from user-entered host.

    Devices are reached as https://<host>/... so a user pasting
    "https://1.2.3.4/", "http://1.2.3.4:8080", or "1.2.3.4 " must not turn
    into a malformed URL like "https://https://1.2.3.4/...". Returns None
    for input that cannot be coerced into a plausible hostname/IP. IPv6
    addresses (including link-local with %zone) are re-bracketed so the
    downstream URL builder produces a valid URL.
    """
    if not raw:
        return None
    host = raw.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    host = host.split("?", 1)[0]
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    host = host.strip()
    if not host:
        return None
    # Allow letters, digits, dot, dash, colon (IPv6) and percent (IPv6 zone id).
    if not all(c.isalnum() or c in ".-:%" for c in host):
        return None
    # Re-bracket bare IPv6 (anything with a colon) so f-string URL builders
    # produce https://[...]/... instead of an unparseable URL.
    if ":" in host:
        return f"[{host}]"
    return host


DEVICE_MODELS = {
    "HD9200": "Air Fryer HD9200",
    "HD9255": "Air Fryer HD9255",
    "HD9280": "Air Fryer HD9280",
    "HD9285": "Air Fryer HD9285",
    "HD9875": "Air Fryer HD9875 (Venus 1)",
    "HD9876": "Air Fryer HD9876 (Venus 1)",
    "HD9880": "Air Fryer HD9880 (Venus 2)",
    "NX0950": "Multicooker NX0950 (Hermes)",
    "NX0960": "Multicooker NX0960 (Nutrimax)",
    "AC": "Air Purifier (AC series)",
    "auto": "Auto-detect (may crash device, use only if model not listed)",
}

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required("model"): vol.In(DEVICE_MODELS),
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

    async def _close_cloud_api(self) -> None:
        """Close cloud API session if open."""
        if self._cloud_api:
            await self._cloud_api.close()
            self._cloud_api = None

    @callback
    def async_remove(self) -> None:
        """Close the cloud session when the flow goes away.

        The cloud API owns an aiohttp session from the moment the OTP is
        requested, and a user who closes the dialog without finishing leaves
        no other chance to release it. This hook is synchronous, so the close
        is handed to the loop.
        """
        if self._cloud_api is not None:
            api = self._cloud_api
            self._cloud_api = None
            self.hass.async_create_task(api.close())

    async def _set_unique_id_or_abort(self, unique_id: str) -> None:
        """Set the flow unique id and abort if the device is already set up.

        Closes the cloud API before aborting so onboarding a device that is
        already configured, or whose discovery flow is already in progress,
        does not leak the open cloud session. Both async_set_unique_id
        (already_in_progress) and _abort_if_unique_id_configured
        (already_configured) can raise AbortFlow, so guard both.
        """
        from homeassistant.data_entry_flow import AbortFlow

        try:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
        except AbortFlow:
            await self._close_cloud_api()
            raise

    def _abort_if_device_already_configured(
        self, cpp_id: str, ip_address: str = ""
    ) -> None:
        """Abort if any existing entry matches this device."""
        from homeassistant.data_entry_flow import AbortFlow

        norm = normalize_unique_id(cpp_id)
        for entry in self._async_current_entries():
            entry_cpp = entry.data.get(CONF_CPP_ID, "")
            if norm and normalize_unique_id(entry_cpp) == norm:
                # Update host IP if discovered (helps future matching)
                if ip_address and entry.data.get(CONF_HOST) != ip_address:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_HOST: ip_address}
                    )
                raise AbortFlow("already_configured")
            # Match by IP only for FUSION entries (cpp_id is a cloud
            # external_id that won't match the discovered MAC). Both sides go
            # through bracket_ipv6: an entry written before discovery carried
            # an IPv6 address in URL form holds the bare address, and comparing
            # that to a bracketed one would rediscover a device already set up.
            if (
                ip_address
                and entry.data.get(CONF_IS_FUSION)
                and bracket_ipv6(entry.data.get(CONF_HOST, "")) == ip_address
            ):
                raise AbortFlow("already_configured")

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth triggered when cloud credentials are needed."""
        return await self.async_step_reauth_email()

    async def async_step_reauth_email(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth - email entry. Sends the OTP inline."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input.get("email", "").strip()
            if not email:
                errors["base"] = "missing_email"
            else:
                self._cloud_email = email
                self._cloud_api = PhilipsCloudAPI()
                try:
                    self._cloud_vtoken = await self._cloud_api.request_otp(email)
                    return await self.async_step_reauth_otp()
                except CloudConnectionError:
                    _LOGGER.warning("Reauth OTP send: cloud unreachable")
                    await self._close_cloud_api()
                    errors["base"] = "cloud_unreachable"
                except CloudAuthError:
                    _LOGGER.exception("Reauth OTP send failed")
                    await self._close_cloud_api()
                    errors["base"] = "otp_send_failed"

        return self.async_show_form(
            step_id="reauth_email",
            data_schema=vol.Schema({vol.Required("email"): str}),
            errors=errors,
        )

    async def async_step_reauth_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth OTP verification."""
        errors: dict[str, str] = {}
        if user_input is not None:
            code = user_input.get("code", "").strip()
            if code and self._cloud_api:
                try:
                    session_token = await self._cloud_api.verify_otp(
                        self._cloud_email, code, self._cloud_vtoken
                    )
                    # Look up the entry before the OAuth exchange. An Air+
                    # device's refresh token must be minted with the Air+
                    # client, so the entry's audience has to be known first.
                    # Use .get() so a missing context["entry_id"] surfaces as
                    # a clean abort rather than a KeyError after the user has
                    # already typed the OTP.
                    entry_id = self.context.get("entry_id")
                    reauth_entry = (
                        self.hass.config_entries.async_get_entry(entry_id)
                        if isinstance(entry_id, str) and entry_id
                        else None
                    )
                    if reauth_entry is None:
                        await self._close_cloud_api()
                        return self.async_abort(reason="reauth_entry_missing")
                    oauth_client = reauth_entry.data.get(
                        CONF_OAUTH_CLIENT, OAUTH_CLIENT_HOMEID
                    )
                    tokens = await self._cloud_api.get_oidc_tokens(
                        session_token, client=oauth_client
                    )
                    new_data = {
                        **reauth_entry.data,
                        CONF_CLOUD_REFRESH_TOKEN: tokens.get("refresh_token", ""),
                    }
                    await self._close_cloud_api()
                    # Reload, don't just store: an entry that failed setup with
                    # ConfigEntryAuthFailed gets no retry timer, so without a
                    # reload it stays in error with the fresh token unused.
                    return self.async_update_reload_and_abort(
                        reauth_entry, data=new_data
                    )
                except CloudConnectionError as err:
                    _LOGGER.warning("Reauth OTP verify: cloud unreachable (%s)", err)
                    errors["base"] = "cloud_unreachable"
                except CloudNotRegisteredError as err:
                    _LOGGER.error("Reauth OTP login: %s", err)
                    errors["base"] = "account_not_registered"
                except CloudAuthError as err:
                    _LOGGER.error("Reauth OTP failed: %s", err)
                    errors["base"] = "otp_failed"
            else:
                errors["base"] = "missing_code"

        return self.async_show_form(
            step_id="reauth_otp",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
            description_placeholders={"email": self._cloud_email},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial step - choose local IP entry or cloud login.

        Cloud-only (FUSION) devices have no local HTTP server, so a local
        probe can never succeed for them. Offering cloud login as a
        first-class choice lets those devices be onboarded manually instead
        of dead-ending on a failed probe.
        """
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
            host = _sanitize_host(user_input[CONF_HOST])
            model = user_input.get("model", "auto")

            if host is None:
                errors["base"] = "invalid_host"
            else:
                device = None
                try:
                    # Probe the device
                    self._local_api = PhilipsLocalAPI()
                    device = await self._local_api.probe_device(host)
                except Exception:
                    _LOGGER.exception("Unexpected exception during device probe")
                    errors["base"] = "unknown"
                finally:
                    if self._local_api:
                        await self._local_api.close()

                # async_set_unique_id / _abort_if_unique_id_configured raise
                # AbortFlow (already_configured / already_in_progress), which
                # must propagate to the flow manager. Keep them outside the
                # try above so the broad except does not swallow the abort into
                # a generic "unknown" error.
                if device:
                    if model != "auto":
                        device.model_name = model
                    self._discovered_device = device
                    unique_id = normalize_unique_id(device.cpp_id or host)
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                    return await self.async_step_confirm()
                elif not errors:
                    errors["base"] = "cannot_connect"

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
                    # Both are device credentials, so they are masked in the
                    # form the way a password field is.
                    vol.Required(CONF_CLIENT_SECRET): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_ENCRYPTION_KEY, default=""): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
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
        """Handle cloud login - email entry.

        OTP is sent inline, then we jump straight to cloud_otp.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("manual_entry") and self._discovered_device is not None:
                return await self.async_step_manual_credentials()

            email = user_input.get("email", "").strip()
            if not email:
                errors["base"] = "missing_email"
            else:
                self._cloud_email = email
                self._cloud_api = PhilipsCloudAPI()

                try:
                    self._cloud_vtoken = await self._cloud_api.request_otp(email)
                    return await self.async_step_cloud_otp()
                except CloudConnectionError:
                    _LOGGER.warning("Cloud OTP send: cloud unreachable")
                    await self._close_cloud_api()
                    errors["base"] = "cloud_unreachable"
                except CloudAuthError:
                    _LOGGER.exception("Cloud OTP send failed")
                    await self._close_cloud_api()
                    errors["base"] = "otp_send_failed"

        # "Enter credentials manually" tests credentials against the
        # device's local IP, so it only works when a device was discovered
        # or probed first. Hide it on the cloud-first path (no discovered
        # device) where it would dead-end at cannot_connect.
        schema_dict: dict[Any, Any] = {vol.Required("email"): str}
        if self._discovered_device is not None:
            schema_dict[vol.Optional("manual_entry", default=False)] = bool

        return self.async_show_form(
            step_id="cloud_email",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_cloud_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle cloud login - OTP code entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input.get("code", "").strip()
            # A verified code is spent, so once the session token is in hand a
            # retry resumes from it rather than asking the device's owner for
            # a code that would no longer verify.
            if self._cloud_api and (code or self._cloud_session_token):
                if not self._cloud_session_token:
                    try:
                        self._cloud_session_token = await self._cloud_api.verify_otp(
                            self._cloud_email, code, self._cloud_vtoken
                        )
                    # Keep the cloud API (and its vToken) alive on these
                    # errors so the user can re-enter the code without
                    # restarting the whole flow, matching the reauth step.
                    except CloudConnectionError as err:
                        _LOGGER.warning("OTP verify: cloud unreachable (%s)", err)
                        errors["base"] = "cloud_unreachable"
                    except CloudNotRegisteredError as err:
                        _LOGGER.error("OTP login: %s", err)
                        errors["base"] = "account_not_registered"
                    except CloudAuthError as err:
                        _LOGGER.error("OTP verification failed: %s", err)
                        errors["base"] = "otp_failed"

                if not errors:
                    try:
                        tokens = await self._cloud_api.get_oidc_tokens(
                            self._cloud_session_token
                        )
                        self._cloud_tokens = tokens

                        # Try Home ID HAL API first (the app's primary backend)
                        _LOGGER.debug("Trying Home ID API for appliances")
                        homeid_failed = False
                        try:
                            appliances = (
                                await self._cloud_api.get_appliances_via_homeid(tokens)
                            )
                        except CloudBackendError as err:
                            # A 5xx here is the HomeID backend failing to build
                            # the response for this account. That says nothing
                            # about the DA registry, so remember the failure and
                            # let the IoT and Air+ lookups still have a turn
                            # instead of ending the flow (issue #36).
                            _LOGGER.error(
                                "Cloud OAuth: HomeID backend error (%s), "
                                "falling back to the IoT device registry",
                                err,
                            )
                            homeid_failed = True
                            appliances = []

                        if appliances:
                            self._cloud_devices = appliances
                            self._cloud_source = "homeid"
                            # An appliance set up outside the HomeID app pairing
                            # flow (e.g. via the machine's own screen) can show up
                            # here without credentials and without an
                            # externalDeviceId, so it cannot route to the FUSION
                            # relay. When that happens, also log the IoT device
                            # registry so we can tell whether a matching thingName
                            # exists for it.
                            if any(
                                not a.get("clientId")
                                and not a.get("clientSecret")
                                and not a.get("externalDeviceId")
                                for a in appliances
                            ):
                                _LOGGER.debug(
                                    "Incomplete HomeID appliance found, querying "
                                    "IoT device registry for diagnostics"
                                )
                                try:
                                    await self._cloud_api.get_devices(
                                        tokens["access_token"]
                                    )
                                except Exception:
                                    _LOGGER.debug(
                                        "IoT device registry lookup failed (non-fatal)",
                                        exc_info=True,
                                    )
                            return await self.async_step_cloud_devices()

                        # Fall back to IoT API
                        _LOGGER.debug("No Home ID appliances, trying IoT API")

                        # Verify token with user profile (IoT)
                        try:
                            profile = await self._cloud_api.get_user_profile(
                                tokens["access_token"]
                            )
                            _LOGGER.debug(
                                "IoT user: id=%s", profile.get("id", "unknown")
                            )
                        except CloudAuthError:
                            _LOGGER.warning(
                                "Could not fetch IoT user profile (non-fatal)"
                            )

                        try:
                            devices = await self._cloud_api.get_devices(
                                tokens["access_token"]
                            )
                        except CloudAuthError:
                            # With the HomeID backend already broken for this
                            # account, a refusal here is just the second door
                            # shutting. Keep the HomeID diagnosis rather than
                            # reporting it as an auth failure.
                            if not homeid_failed:
                                raise
                            _LOGGER.debug(
                                "IoT device registry lookup failed after the "
                                "HomeID backend error",
                                exc_info=True,
                            )
                            devices = []

                        # Also query homes for debug context
                        try:
                            homes = await self._cloud_api.get_homes(
                                tokens["access_token"]
                            )
                            if homes:
                                _LOGGER.debug("IoT homes: %d found", len(homes))
                        except Exception:
                            pass

                        if devices:
                            self._cloud_devices = devices
                            self._cloud_source = "iot"
                            return await self.async_step_cloud_devices()

                        # Air+ app pairing: a purifier paired in the standalone
                        # Philips Air+ app is registered against the Air+ OAuth
                        # client, so it is invisible to the HomeID-audience
                        # token used above. Mint an Air+ token from the same
                        # OTP session and query the same DA registry (issue #33).
                        airplus_devices = await self._discover_airplus_devices()
                        if airplus_devices:
                            self._cloud_devices = airplus_devices
                            self._cloud_source = OAUTH_CLIENT_AIRPLUS
                            return await self.async_step_cloud_devices()

                        errors["base"] = (
                            "cloud_profile_error"
                            if homeid_failed
                            else "no_cloud_devices"
                        )

                    # The cloud API stays open here too: closing it stranded
                    # the step, because the next submission found no API and
                    # answered a typed-in code with "missing_code" forever.
                    # async_remove closes it when the flow goes away.
                    except CloudBackendError as err:
                        _LOGGER.error("Cloud OAuth: HomeID backend error (%s)", err)
                        errors["base"] = "cloud_profile_error"
                    except CloudConnectionError as err:
                        _LOGGER.warning("Cloud OAuth: cloud unreachable (%s)", err)
                        errors["base"] = "cloud_unreachable"
                    except CloudAuthError as err:
                        _LOGGER.error("Cloud auth failed: %s", err)
                        errors["base"] = "cloud_oauth_failed"
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

    async def _discover_airplus_devices(self) -> list[dict[str, Any]]:
        """Discover purifiers paired in the Philips Air+ app.

        Mints an Air+-client token from the existing Gigya OTP session and
        queries the DA IoT device registry, which lists a device only to the
        OAuth client it was paired with. On success the Air+ tokens replace
        self._cloud_tokens so the entry stores an Air+ refresh token. Returns
        an empty list on any failure so the caller falls through to
        no_cloud_devices.
        """
        if not self._cloud_api or not self._cloud_session_token:
            return []
        try:
            tokens = await self._cloud_api.get_oidc_tokens(
                self._cloud_session_token, client=OAUTH_CLIENT_AIRPLUS
            )
            devices = await self._cloud_api.get_devices(tokens["access_token"])
        except CloudAuthError as err:
            # CloudConnectionError subclasses CloudAuthError; either way the
            # Air+ path simply yields nothing and the flow reports no devices.
            _LOGGER.debug("Air+ discovery failed: %s", err)
            return []
        if devices:
            _LOGGER.info("Air+ discovery found %d device(s)", len(devices))
            self._cloud_tokens = tokens
        return devices

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
                    elif self._cloud_source == OAUTH_CLIENT_AIRPLUS:
                        result = await self._create_fusion_entry_from_device(
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
                name = (
                    dev.get("friendlyName", "")
                    or dev.get("name", "")
                    or dev.get("deviceName", "")
                    or dev.get("ctn", "")
                )
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
        external_id = device_data.get("externalDeviceId", "")

        if not client_id or not client_secret:
            # No local credentials: try FUSION (cloud MQTT relay)
            registered_in = device_data.get("registeredIn", "")
            if external_id:
                _LOGGER.info(
                    "No local credentials, attempting FUSION cloud relay "
                    "(registeredIn=%s, externalDeviceId=%s)",
                    registered_in,
                    external_id,
                )
                return await self._create_fusion_entry(device_data, errors)
            _LOGGER.warning(
                "Home ID appliance has no credentials and no externalDeviceId"
            )
            errors["base"] = "cloud_credentials_not_found"
            return None

        # Resolve IP: use discovered device or MAC-based lookup
        host = ""
        mac = device_data.get("macAddress", "")
        if self._discovered_device:
            host = self._discovered_device.ip_address
        if not host:
            if external_id:
                _LOGGER.info("No IP for local device, falling back to FUSION relay")
                return await self._create_fusion_entry(device_data, errors)
            errors["base"] = "cloud_no_ip"
            return None

        # If the device also has an externalDeviceId (FUSION-capable),
        # verify local connectivity before committing to a local entry.
        # Some FUSION devices have local credentials in the cloud backend
        # but no working local HTTP server.
        if external_id:
            api = PhilipsLocalAPI()
            try:
                probe_result = await api.probe_device(host)
            finally:
                await api.close()
            if not probe_result:
                _LOGGER.info(
                    "Local probe failed for FUSION-capable device at %s, "
                    "using cloud relay instead",
                    host,
                )
                return await self._create_fusion_entry(device_data, errors)

        # Use MAC as unique ID (normalized to match discovery format)
        unique_id = normalize_unique_id(mac or host)
        await self._set_unique_id_or_abort(unique_id)

        # Use discovered device model if available, fall back to cloud data
        # Include model_number (e.g., "HD9280/9x") for port lookup
        model = ""
        if self._discovered_device:
            parts = [
                self._discovered_device.model_name,
                self._discovered_device.model_number,
            ]
            model = " ".join(filter(None, parts))
        name = device_data.get("name", "") or model or mac or host
        use_https = True
        if self._discovered_device:
            use_https = self._discovered_device.use_https

        entry_data = {
            CONF_HOST: host,
            CONF_CPP_ID: mac,
            CONF_MODEL: model,
            CONF_DEVICE_ID: mac,
            CONF_CLIENT_ID: client_id,
            CONF_CLIENT_SECRET: client_secret,
            CONF_USE_HTTPS: use_https,
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

        unique_id = normalize_unique_id(device_data.get("id", host))
        await self._set_unique_id_or_abort(unique_id)

        use_https = True
        if self._discovered_device:
            use_https = self._discovered_device.use_https

        entry_data = {
            CONF_HOST: host,
            CONF_CPP_ID: device_data.get("id", ""),
            CONF_MODEL: ctn,
            CONF_DEVICE_ID: device_data.get("id", ""),
            CONF_CLIENT_ID: creds.get("client_id", ""),
            CONF_CLIENT_SECRET: creds.get("client_secret", ""),
            CONF_USE_HTTPS: use_https,
            CONF_CLOUD_REFRESH_TOKEN: self._cloud_tokens.get("refresh_token", ""),
        }
        enc_key = creds.get("encryption_key")
        if enc_key:
            entry_data[CONF_ENCRYPTION_KEY] = enc_key

        name = device_data.get("friendlyName", ctn) or ctn or host
        await self._close_cloud_api()
        return self.async_create_entry(title=name, data=entry_data)

    async def _create_fusion_entry(
        self, device_data: dict[str, Any], errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Create config entry for a FUSION device (cloud MQTT relay)."""
        external_id = device_data.get("externalDeviceId", "")
        mac = device_data.get("macAddress", "")
        fw = device_data.get("firmwareVersion", "")
        name = device_data.get("name", "") or mac or external_id

        # Prefer discovered device MAC so unique ID matches zeroconf/SSDP
        # discovery, preventing duplicate discovery notifications.
        if not mac and self._discovered_device:
            mac = self._discovered_device.cpp_id

        unique_id = normalize_unique_id(mac or external_id)
        await self._set_unique_id_or_abort(unique_id)

        # FUSION entries require a refresh token; the setup path bails
        # immediately without it. Surface the failure here so the user
        # sees an actionable error instead of a silently broken entry.
        if not self._cloud_tokens.get("refresh_token"):
            _LOGGER.error(
                "Cloud OAuth returned no refresh_token; cannot create FUSION entry"
            )
            errors["base"] = "cloud_oauth_failed"
            return None

        # thingName != externalDeviceId: they are separate fields in the
        # IoT API. thingName is the AWS IoT thing name for MQTT topics.
        thing_name = None
        access_token = self._cloud_tokens.get("access_token", "")
        if access_token and self._cloud_api:
            thing_name = await self._cloud_api.get_thing_name(
                access_token,
                device_id=external_id,
                mac_address=mac,
            )
        if not thing_name:
            _LOGGER.error(
                "Could not resolve thingName for device %s (mac=%s)",
                external_id,
                mac,
            )
            errors["base"] = "cloud_credentials_not_found"
            return None

        # Use discovered device model if available
        model = ""
        if self._discovered_device:
            parts = [
                self._discovered_device.model_name,
                self._discovered_device.model_number,
            ]
            model = " ".join(filter(None, parts))
        if not model:
            model = name

        host = ""
        if self._discovered_device:
            host = self._discovered_device.ip_address

        entry_data = self._fusion_entry_data(
            thing_name=thing_name,
            device_id=external_id,
            mac=mac or external_id,
            model=model,
            host=host,
            oauth_client=OAUTH_CLIENT_HOMEID,
        )

        _LOGGER.info(
            "Creating FUSION entry: name=%s, thing=%s, mac=%s, fw=%s",
            name,
            thing_name,
            mac,
            fw,
        )

        await self._close_cloud_api()
        return self.async_create_entry(title=f"{name} (Cloud)", data=entry_data)

    def _fusion_entry_data(
        self,
        *,
        thing_name: str,
        device_id: str,
        mac: str,
        model: str,
        host: str,
        oauth_client: str,
    ) -> dict[str, Any]:
        """Build the config-entry data for a FUSION (cloud MQTT relay) device.

        ``oauth_client`` records which OAuth client the stored refresh token
        belongs to, so the runtime relay refreshes it with the same client.
        """
        return {
            CONF_HOST: host,
            CONF_CPP_ID: mac or device_id,
            CONF_MODEL: model,
            CONF_DEVICE_ID: device_id,
            CONF_IS_FUSION: True,
            CONF_THING_NAME: thing_name,
            CONF_TENANT: FUSION_TENANT,
            CONF_MQTT_HOST: FUSION_MQTT_HOST,
            CONF_PLATFORM_REST_URL: FUSION_PLATFORM_REST_URL,
            CONF_CLOUD_REFRESH_TOKEN: self._cloud_tokens.get("refresh_token", ""),
            CONF_OAUTH_CLIENT: oauth_client,
        }

    async def _create_fusion_entry_from_device(
        self, device_data: dict[str, Any], errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Create a FUSION entry from an Air+ IoT device record.

        Unlike the HomeID appliance path, an Air+ device record from the DA
        registry already carries its AWS IoT thingName, so no separate lookup
        is needed. The device was discovered with an Air+ token, so the entry
        is stamped with the Air+ client for its runtime token refresh.
        """
        # The device UUID names the AWS IoT thing. Community Air+ integrations
        # read "uuid" first (falling back to "id"); when the record carries no
        # explicit thingName the thing is "da-<uuid>" on this platform.
        device_id = device_data.get("uuid", "") or device_data.get("id", "")
        thing_name = device_data.get("thingName", "")
        if not thing_name and device_id:
            thing_name = f"da-{device_id}"
        if not thing_name:
            # The record holds localCredentials, so name the fields it does
            # have rather than dumping it.
            _LOGGER.error(
                "Air+ device record has no thingName or id, fields: %s",
                sorted(device_data),
            )
            errors["base"] = "cloud_credentials_not_found"
            return None

        if not self._cloud_tokens.get("refresh_token"):
            _LOGGER.error(
                "Air+ OAuth returned no refresh_token; cannot create FUSION entry"
            )
            errors["base"] = "cloud_oauth_failed"
            return None

        mac = device_data.get("macAddress", "")
        ctn = device_data.get("ctn", "")
        name = (
            device_data.get("name", "")
            or device_data.get("deviceName", "")
            or device_data.get("friendlyName", "")
            or ctn
            or thing_name
        )

        unique_id = normalize_unique_id(mac or device_id or thing_name)
        await self._set_unique_id_or_abort(unique_id)

        entry_data = self._fusion_entry_data(
            thing_name=thing_name,
            device_id=device_id,
            mac=mac,
            model=ctn or name,
            host="",
            oauth_client=OAUTH_CLIENT_AIRPLUS,
        )

        _LOGGER.info(
            "Creating Air+ FUSION entry: name=%s, thing=%s, mac=%s, ctn=%s",
            name,
            thing_name,
            mac,
            ctn,
        )
        await self._close_cloud_api()
        return self.async_create_entry(title=f"{name} (Cloud)", data=entry_data)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle zeroconf discovery."""
        _LOGGER.debug(
            "Zeroconf discovery: %s at %s", discovery_info.name, discovery_info.host
        )

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

        if (
            get_device_type(device.model_name) == "unknown"
            and get_device_type(device.model_number) == "unknown"
        ):
            _LOGGER.debug(
                "Ignoring unsupported zeroconf device: name=%s model=%s mr=%s",
                device.friendly_name,
                device.model_name,
                device.model_number,
            )
            return self.async_abort(reason="unsupported_device")

        self._discovered_device = device

        # Set unique ID (normalized to match FUSION entries)
        unique_id = normalize_unique_id(device.cpp_id or discovery_info.name)
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: device.ip_address})

        # Also check existing entries by cpp_id or IP to catch FUSION
        # entries that used external_id instead of MAC as unique_id.
        self._abort_if_device_already_configured(device.cpp_id or "", device.ip_address)

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
        _LOGGER.debug(
            "SSDP discovery: %s at %s",
            discovery_info.upnp.get("friendlyName", ""),
            discovery_info.ssdp_location or "",
        )

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

        if (
            get_device_type(device.model_name) == "unknown"
            and get_device_type(device.model_number) == "unknown"
        ):
            _LOGGER.debug(
                "Ignoring unsupported SSDP device: name=%s model=%s number=%s",
                device.friendly_name,
                device.model_name,
                device.model_number,
            )
            return self.async_abort(reason="unsupported_device")

        self._discovered_device = device

        # Set unique ID - prefer cppId over UDN (normalized to match FUSION entries)
        unique_id = normalize_unique_id(device.cpp_id or upnp.get("UDN", ""))
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: device.ip_address})

        # Also check existing entries by cpp_id or IP to catch FUSION
        # entries that used external_id instead of MAC as unique_id.
        self._abort_if_device_already_configured(device.cpp_id or "", device.ip_address)

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

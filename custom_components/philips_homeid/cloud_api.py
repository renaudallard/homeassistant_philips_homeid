# Copyright (c) 2025-2026, Renaud Allard <renaud@allard.it>
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
"""Philips Cloud API: device queries, MQTT setup, credential retrieval."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .cloud_auth import (
    CloudAuthError,
    CloudBackendError,
    CloudConnectionError,
    CloudNotRegisteredError,
    PhilipsCloudAuth,
)
from .util import normalize_unique_id

__all__ = [
    "CloudAuthError",
    "CloudBackendError",
    "CloudConnectionError",
    "CloudNotRegisteredError",
    "PhilipsCloudAPI",
]

_LOGGER = logging.getLogger(__name__)

# IoT API (production, from APK DomainConfig)
IOT_BASE = "https://prod.eu-da.iot.versuni.com/api/da"

# Home ID backend (from APK BackendConfigKt)
BACKEND_BASE = "https://www.backend.vbs.versuni.com"
BACKEND_API_BASE = "https://www.backend.vbs.versuni.com/api"
HOMEID_ACCEPT = "application/vnd.oneka.v2.0+json"

# Headers matching the Android app (DefaultRequestInterceptor)
HOMEID_USER_AGENT = (
    "HomeID/8.16.0 (com.philips.ka.oneka.app; build:8160001; Android 14)"
)
HOMEID_X_USER_AGENT = "Android 14;8.16.0"

# HA gives short ISO 639-1 codes; the Philips backend expects BCP 47 tags
# (e.g. "de-DE"). APK LanguageUtilsImpl.l() always sends toLanguageTag() output.
_LANG_TAG_MAP = {
    "en": "en-GB",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-ES",
    "it": "it-IT",
    "nl": "nl-NL",
    "pt": "pt-PT",
    "pl": "pl-PL",
    "sv": "sv-SE",
    "zh": "zh-CN",
    "ko": "ko-KR",
    "ja": "ja-JP",
    "ru": "ru-RU",
    "cs": "cs-CZ",
    "da": "da-DK",
    "fi": "fi-FI",
    "no": "nb-NO",
    "nb": "nb-NO",
    "tr": "tr-TR",
    "ar": "ar-AE",
    "el": "el-GR",
    "hu": "hu-HU",
    "ro": "ro-RO",
    "sk": "sk-SK",
    "th": "th-TH",
    "uk": "uk-UA",
    "vi": "vi-VN",
}


def _expand_language_tag(lang: str) -> str:
    """Return a BCP 47 tag. Pass through if already has a region subtag."""
    if not lang:
        return "en-GB"
    if "-" in lang or "_" in lang:
        return lang.replace("_", "-")
    return _LANG_TAG_MAP.get(lang.lower(), f"{lang}-{lang.upper()}")


class PhilipsCloudAPI(PhilipsCloudAuth):
    """Philips cloud client for device queries and MQTT setup.

    Inherits OTP, OAuth, and token management from PhilipsCloudAuth.
    """

    # --- MQTT setup ---

    async def get_mqtt_signature(
        self,
        access_token: str,
        platform_rest_url: str = "prod.eu-da.iot.versuni.com",
        tenant: str = "da",
    ) -> dict[str, Any]:
        """Get MQTT signature for FUSION device cloud relay.

        Calls the DaConnect signature endpoint to obtain the
        mqttSignature needed for AWS IoT Custom Authorizer.

        Returns dict with 'signature' key.
        """
        session = await self._get_session()
        url = f"https://{platform_rest_url}/api/{tenant}/user/self/signature"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        _LOGGER.debug("MQTT signature: GET %s", url)
        async with session.get(url, headers=headers) as resp:
            text = await resp.text()
            # The body is the AWS IoT custom-authorizer signature, which is a
            # credential, so log its size and never its value.
            _LOGGER.debug(
                "MQTT signature response: HTTP %s, %d bytes",
                resp.status,
                len(text),
            )
            if resp.status == 401:
                raise CloudAuthError(f"MQTT signature rejected: HTTP {resp.status}")
            if resp.status != 200:
                raise CloudConnectionError(
                    f"MQTT signature request failed: HTTP {resp.status}"
                )
            return json.loads(text)

    async def get_mqtt_user_id(
        self,
        access_token: str,
        id_token: str,
        platform_rest_url: str = "prod.eu-da.iot.versuni.com",
        tenant: str = "da",
    ) -> str | None:
        """Get the MQTT userId from the IoT API.

        APK calls POST /user/self/get-id with the OIDC id_token.
        The returned userId is what the Custom Authorizer IoT policy
        expects as the MQTT client ID prefix.

        Raises CloudAuthError on 401/403 (the caller must reauthenticate)
        and CloudConnectionError on 5xx or non-JSON responses. Returns
        None only when the response is a 200 with no userId field.
        """
        session = await self._get_session()
        url = f"https://{platform_rest_url}/api/{tenant}/user/self/get-id"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = json.dumps({"idToken": id_token})

        _LOGGER.debug("MQTT get-id: POST %s", url)
        async with session.post(url, headers=headers, data=body) as resp:
            text = await resp.text()
            _LOGGER.debug(
                "MQTT get-id response: HTTP %s, body: %s",
                resp.status,
                text[:500],
            )
            if resp.status in (401, 403):
                raise CloudAuthError(f"MQTT get-id rejected: HTTP {resp.status}")
            if resp.status != 200:
                raise CloudConnectionError(f"MQTT get-id failed: HTTP {resp.status}")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as err:
                raise CloudConnectionError(
                    f"MQTT get-id response not JSON: {text[:200]}"
                ) from err
            return data.get("userId")

    # --- IoT API ---

    async def get_thing_name(
        self,
        access_token: str,
        device_id: str = "",
        mac_address: str = "",
    ) -> str | None:
        """Get the AWS IoT thingName for a device.

        The HomeID appliance record and the IoT device registry are separate
        backends that can format the same MAC or device id differently (colon
        vs dash vs bare hex, upper vs lower case, or wrapped in a UUID). Both
        sides are normalized before matching so a format mismatch does not hide
        an otherwise valid thingName.
        """
        devices = await self.get_devices(access_token)
        norm_id = normalize_unique_id(device_id)
        norm_mac = normalize_unique_id(mac_address)
        for dev in devices:
            if device_id and normalize_unique_id(dev.get("id", "")) == norm_id:
                thing = dev.get("thingName", "")
                if thing:
                    _LOGGER.debug(
                        "Found thingName=%s for device id=%s", thing, device_id
                    )
                    return thing
            if (
                mac_address
                and normalize_unique_id(dev.get("macAddress", "")) == norm_mac
            ):
                thing = dev.get("thingName", "")
                if thing:
                    _LOGGER.debug("Found thingName=%s for mac=%s", thing, mac_address)
                    return thing
        _LOGGER.warning(
            "No thingName found for device_id=%s, mac=%s", device_id, mac_address
        )
        return None

    async def get_user_profile(self, access_token: str) -> dict[str, Any]:
        """Get the cloud user profile to verify the token works."""
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        async with session.get(f"{IOT_BASE}/user/self", headers=headers) as resp:
            text = await resp.text()
            _LOGGER.debug(
                "User profile response: HTTP %s, body: %s", resp.status, text[:500]
            )
            if resp.status == 401:
                raise CloudAuthError(f"User profile rejected: HTTP {resp.status}")
            if resp.status != 200:
                raise CloudConnectionError(
                    f"User profile request failed: HTTP {resp.status}"
                )
            try:
                data = json.loads(text)
            except json.JSONDecodeError as err:
                raise CloudConnectionError(
                    f"User profile response not JSON: {text[:200]}"
                ) from err
            _LOGGER.debug("Cloud user ID: %s", data.get("id", "unknown"))
            return data

    async def get_devices(self, access_token: str) -> list[dict[str, Any]]:
        """List devices registered to the user's account."""
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        async with session.get(f"{IOT_BASE}/user/self/device", headers=headers) as resp:
            text = await resp.text()
            _LOGGER.debug(
                "Device list response: HTTP %s, body: %s", resp.status, text[:1000]
            )
            # A 403 is the IoT API refusing the token (its AWS gateway wants an
            # IAM-signed request, not a bare OIDC bearer), which is a permanent
            # authorization denial rather than a transient outage, so it is
            # reported like a 401 and not as a retryable connection error.
            if resp.status in (401, 403):
                raise CloudAuthError(
                    f"Device list rejected: HTTP {resp.status}, body: {text[:200]}"
                )
            if resp.status != 200:
                raise CloudConnectionError(
                    f"Device list request failed: HTTP {resp.status}, body: {text[:200]}"
                )
            try:
                data = json.loads(text)
            except json.JSONDecodeError as err:
                raise CloudConnectionError(
                    f"Device list response not JSON: {text[:200]}"
                ) from err

        if isinstance(data, list):
            devices = data
        elif isinstance(data, dict):
            devices = data.get("devices") or data.get("data") or data.get("items") or []
            if not devices:
                _LOGGER.debug(
                    "Device response is dict with keys: %s", list(data.keys())
                )
        else:
            _LOGGER.debug("Unexpected device response type: %s", type(data).__name__)
            devices = []

        _LOGGER.debug("Found %d device(s)", len(devices))
        for dev in devices:
            _LOGGER.debug(
                "  Device: id=%s, ctn=%s, name=%s, mac=%s, thingName=%s",
                dev.get("id", "?"),
                dev.get("ctn", "?"),
                dev.get("friendlyName", "?"),
                dev.get("macAddress", "?"),
                dev.get("thingName", "?"),
            )
        return devices

    async def get_rita_capabilities(
        self,
        access_token: str,
        device_id: str,
        ctn: str,
        firmware_version: str,
    ) -> list[dict[str, Any]]:
        """Fetch a Rita machine's supported drink catalog.

        Returns the raw drink list from the DaConnect device-capabilities
        endpoint. An empty list means the machine reported no drinks, so the
        caller may fall back to the built-in list and stop asking. A retryable
        failure raises CloudConnectionError instead, so the caller can tell
        the two apart and ask again later.
        """
        try:
            session = await self._get_session()
            url = (
                f"{IOT_BASE}/device/{device_id}/capabilities"
                f"?proposition={ctn}&version={firmware_version}"
            )
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
            async with session.get(url, headers=headers) as resp:
                text = await resp.text()
                _LOGGER.debug(
                    "Rita capabilities response: HTTP %s, body: %s",
                    resp.status,
                    text[:500],
                )
                if resp.status != 200:
                    self._raise_if_retryable(
                        resp.status, f"Rita capabilities for {device_id}"
                    )
                    return []
                data = json.loads(text)
        except CloudConnectionError:
            raise
        except Exception as err:
            raise CloudConnectionError(
                f"Rita capabilities fetch failed for {device_id}: {err}"
            ) from err
        return data if isinstance(data, list) else []

    async def get_homes(self, access_token: str) -> list[dict[str, Any]]:
        """List homes from IoT API (for debugging)."""
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        async with session.get(f"{IOT_BASE}/user/self/home", headers=headers) as resp:
            text = await resp.text()
            _LOGGER.debug("Homes response: HTTP %s, body: %s", resp.status, text[:500])
            if resp.status != 200:
                return []
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("homes") or data.get("data") or []
        return []

    # --- Home ID backend API ---

    async def _homeid_discovery(self) -> dict[str, Any]:
        """Fetch the HomeID tenant discovery document.

        Plain GET — must NOT send Content-Type (sending
        application/vnd.api+json triggers a 403).
        """
        session = await self._get_session()
        discovery_url = f"{BACKEND_BASE}/.well-known/tenant/oneka"
        _LOGGER.debug("HomeID discovery: GET %s", discovery_url)
        try:
            async with session.get(discovery_url) as resp:
                text = await resp.text()
                _LOGGER.debug(
                    "HomeID discovery response: HTTP %s, body: %s",
                    resp.status,
                    text[:500],
                )
                if resp.status != 200:
                    _LOGGER.error("HomeID discovery failed: HTTP %s", resp.status)
                    return {}
                return json.loads(text)
        except Exception:
            _LOGGER.exception("HomeID discovery request failed")
            return {}

    async def get_appliances_via_homeid(
        self, oidc_tokens: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Get appliances via the Home ID backend API.

        Chain:
        1. Discovery: GET /.well-known/tenant/oneka -> profileUrl
        2. Profile: GET {profileUrl} (Bearer OIDC access_token) -> userAppliances href
        3. Appliances: GET {appliancesUrl} -> _embedded.item[]
        """
        session = await self._get_session()
        access_token = oidc_tokens.get("access_token", "")
        if not access_token:
            _LOGGER.error("No OIDC access_token available")
            return []

        discovery = await self._homeid_discovery()
        if not discovery:
            return []

        ts = int(time.time() * 1000)
        hal_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": HOMEID_ACCEPT,
            "Accept-Language": "en-GB",
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
        }

        profile_url = discovery.get("profileUrl")
        if not profile_url:
            _LOGGER.error(
                "HomeID discovery has no profileUrl, keys: %s",
                list(discovery.keys()),
            )
            return []

        if profile_url.startswith("/"):
            profile_url = f"{BACKEND_API_BASE}{profile_url}"

        profile_req_url = f"{profile_url}?ts={ts}"
        _LOGGER.debug("HomeID profile: GET %s", profile_req_url)
        async with session.get(profile_req_url, headers=hal_headers) as resp:
            text = await resp.text()
            # The profile can embed the appliance list, which carries a
            # clientId and clientSecret per appliance, so log the size only.
            _LOGGER.debug(
                "HomeID profile response: HTTP %s, %d bytes",
                resp.status,
                len(text),
            )
            if resp.status != 200:
                _LOGGER.error(
                    "HomeID profile failed: HTTP %s, body: %s",
                    resp.status,
                    text[:500],
                )
                self._raise_for_homeid(resp.status, "HomeID profile")
                return []
            try:
                profile = json.loads(text)
            except json.JSONDecodeError:
                _LOGGER.error("HomeID profile not JSON, %d bytes", len(text))
                return []

        embedded = profile.get("_embedded", {})
        appliances_embedded = embedded.get("userAppliances", {})
        if isinstance(appliances_embedded, dict):
            items = appliances_embedded.get("_embedded", {}).get("item", [])
            if items:
                _LOGGER.debug(
                    "HomeID: found %d embedded appliance(s) in profile",
                    len(items),
                )
                self._log_appliances(items)
                return items

        links = profile.get("_links", {})
        appliances_link = links.get("userAppliances", {})
        appliances_href = (
            appliances_link.get("href", "") if isinstance(appliances_link, dict) else ""
        )

        if not appliances_href:
            _LOGGER.debug(
                "HomeID profile has no userAppliances link, _links keys: %s",
                list(links.keys()),
            )
            return []

        if appliances_href.startswith("/"):
            appliances_href = f"{BACKEND_API_BASE}{appliances_href}"

        appliances_href = re.sub(r"\{[^}]*\}", "", appliances_href)

        appliances_req_url = f"{appliances_href}?ts={ts}&includeSkippedPairing=true"
        _LOGGER.debug("HomeID appliances: GET %s", appliances_req_url)
        async with session.get(appliances_req_url, headers=hal_headers) as resp:
            text = await resp.text()
            # Every appliance in the body carries its clientId and
            # clientSecret, so log the size only. The per-appliance lines in
            # _log_appliances() report those two as booleans.
            _LOGGER.debug(
                "HomeID appliances response: HTTP %s, %d bytes",
                resp.status,
                len(text),
            )
            if resp.status != 200:
                _LOGGER.error(
                    "HomeID appliances failed: HTTP %s, body: %s",
                    resp.status,
                    text[:500],
                )
                self._raise_for_homeid(resp.status, "HomeID appliances")
                return []
            try:
                appliances_data = json.loads(text)
            except json.JSONDecodeError:
                _LOGGER.error("HomeID appliances not JSON, %d bytes", len(text))
                return []

        if isinstance(appliances_data, dict):
            items = appliances_data.get("_embedded", {}).get("item", [])
        elif isinstance(appliances_data, list):
            items = appliances_data
        else:
            items = []

        _LOGGER.debug("HomeID: found %d appliance(s)", len(items))
        self._log_appliances(items)
        return items

    def _log_appliances(self, items: list[dict[str, Any]]) -> None:
        """Log appliance details for debugging."""
        for item in items:
            _LOGGER.debug(
                "  Appliance: name=%s, mac=%s, fw=%s, "
                "has_clientId=%s, has_clientSecret=%s, "
                "registeredIn=%s, externalDeviceId=%s",
                item.get("name", "?"),
                item.get("macAddress", "?"),
                item.get("firmwareVersion", "?"),
                bool(item.get("clientId")),
                bool(item.get("clientSecret")),
                item.get("registeredIn", "?"),
                item.get("externalDeviceId", "?"),
            )

    # --- Recipe lookup ---

    @staticmethod
    def _raise_if_retryable(status: int, what: str) -> None:
        """Raise CloudConnectionError when a lookup failed for a retryable reason.

        A 5xx or a 429 is the cloud having a bad moment. The caller has to be
        able to tell that apart from a genuine "no such recipe" so it does not
        give up on the id for the rest of the session.
        """
        if status >= 500 or status == 429:
            raise CloudConnectionError(f"{what}: HTTP {status}")

    @staticmethod
    def _raise_for_homeid(status: int, what: str) -> None:
        """Raise the right error for a failed HomeID backend request.

        A 401 or 403 means the token was refused, so the caller has to
        reauthenticate. A 5xx or a 429 is the backend failing to build the
        response for the account, reported as CloudBackendError so the config
        flow can point the user at the official app. Any other non-200 is left
        for the caller to fall back to the IoT device list, matching the old
        behaviour where the HomeID path returned empty on such responses.
        """
        if status in (401, 403):
            raise CloudAuthError(f"{what}: HTTP {status}")
        if status >= 500 or status == 429:
            raise CloudBackendError(f"{what}: HTTP {status}")

    async def get_recipe_name(
        self, access_token: str, recipe_id: str, language: str = "en-GB"
    ) -> str | None:
        """Fetch a single recipe name from the backend API."""
        session = await self._get_session()
        url = (
            f"{BACKEND_API_BASE}/v1/mobile/recipes/{recipe_id}?incrementViewCount=false"
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept-Language": _expand_language_tag(language),
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
        }
        _LOGGER.debug("Recipe lookup: GET %s", url)
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    self._raise_if_retryable(
                        resp.status, f"Recipe lookup for {recipe_id}"
                    )
                    _LOGGER.warning(
                        "Recipe lookup failed: HTTP %s for %s", resp.status, recipe_id
                    )
                    return None
                data = await resp.json(content_type=None)
        except CloudConnectionError:
            raise
        except Exception as err:
            raise CloudConnectionError(
                f"Recipe lookup request failed for {recipe_id}: {err}"
            ) from err

        return self._extract_recipe_title(data)

    @staticmethod
    def _extract_recipe_title(data: Any) -> str | None:
        """Extract recipe title from a JSON:API response.

        A 200 carrying null, a list, or a null member is still a real answer
        and has to come back as "no title" rather than raise: the caller
        reads an exception here as a permanent miss.
        """
        if not isinstance(data, dict):
            return None
        attrs = (data.get("data") or {}).get("attributes") or {}
        title = attrs.get("title")
        if title:
            return str(title)
        for item in data.get("included") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") in ("recipeTranslations", "recipeTranslation"):
                t = (item.get("attributes") or {}).get("title")
                if t:
                    return str(t)
        return None

    async def get_autocook_program_name(
        self, access_token: str, reference_id: str, language: str = "en-GB"
    ) -> str | None:
        """Fetch an AutoCook program foodItem name by referenceId.

        AutoCook programs live on a different endpoint than community recipes.
        The URL template is discovered via discovery -> space.backendBaseUrl ->
        root API _links.autocookPrograms, then expanded with referenceId.
        """
        template = await self._get_autocook_template(access_token)
        if not template:
            return None
        url = re.sub(r"\{[^}]*\}", "", template)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}referenceId={reference_id}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": HOMEID_ACCEPT,
            "Accept-Language": _expand_language_tag(language),
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
        }
        _LOGGER.debug("AutoCook lookup: GET %s", url)
        session = await self._get_session()
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    self._raise_if_retryable(
                        resp.status, f"AutoCook lookup for referenceId={reference_id}"
                    )
                    _LOGGER.warning(
                        "AutoCook lookup failed: HTTP %s for referenceId=%s",
                        resp.status,
                        reference_id,
                    )
                    return None
                data = await resp.json(content_type=None)
        except CloudConnectionError:
            raise
        except Exception as err:
            raise CloudConnectionError(
                f"AutoCook lookup request failed for referenceId={reference_id}: {err}"
            ) from err
        return self._extract_autocook_food_item(data)

    async def get_autocook_programs(
        self, access_token: str, language: str = "en-GB"
    ) -> dict[str, str]:
        """Fetch the full AutoCook program catalog as referenceId -> foodItem.

        Hits the root AutoCook link with no referenceId filter. An empty dict
        means this account genuinely has no AutoCook catalog; a retryable
        failure raises CloudConnectionError.
        """
        template = await self._get_autocook_template(access_token)
        if not template:
            return {}
        url = re.sub(r"\{[^}]*\}", "", template)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": HOMEID_ACCEPT,
            "Accept-Language": _expand_language_tag(language),
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
        }
        _LOGGER.debug("AutoCook catalog: GET %s", url)
        session = await self._get_session()
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    self._raise_if_retryable(resp.status, "AutoCook catalog")
                    _LOGGER.warning("AutoCook catalog failed: HTTP %s", resp.status)
                    return {}
                data = await resp.json(content_type=None)
        except CloudConnectionError:
            raise
        except Exception as err:
            raise CloudConnectionError(
                f"AutoCook catalog request failed: {err}"
            ) from err
        return self._walk_autocook_catalog(data)

    @staticmethod
    def _walk_autocook_catalog(data: Any) -> dict[str, str]:
        """Collect referenceId -> foodItem pairs from a HAL response tree."""
        result: dict[str, str] = {}

        def visit(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    visit(item)
                return
            if not isinstance(node, dict):
                return
            ref = node.get("referenceId")
            food = node.get("foodItem")
            if ref is not None and food:
                result[str(ref)] = str(food)
            embedded = node.get("_embedded")
            if isinstance(embedded, dict):
                for v in embedded.values():
                    visit(v)

        visit(data)
        return result

    async def _get_root_links(self, access_token: str) -> dict[str, Any]:
        """Return the root API _links map.

        Chain: GET /.well-known/tenant/oneka -> spaces[].backendBaseUrl ->
        root document _links. The same root document holds every templated
        link (autocookPrograms, profileSelfApplianceCookingMethods, ...).

        The result is memoized, but callers build a client per fetch and
        close it, so today nothing calls this twice on one instance and the
        memo never pays off. It is kept for a caller that reuses a client.

        An empty map means the tenant genuinely exposes no links. A retryable
        failure raises CloudConnectionError, so a caller can tell a bad moment
        apart from a real answer instead of caching "nothing" for good.
        """
        cached = getattr(self, "_root_links_cache", None)
        if cached is not None:
            return cached

        session = await self._get_session()
        discovery_url = f"{BACKEND_BASE}/.well-known/tenant/oneka"
        try:
            async with session.get(discovery_url) as resp:
                if resp.status != 200:
                    self._raise_if_retryable(resp.status, "Root API discovery")
                    _LOGGER.debug("Root API discovery failed: HTTP %s", resp.status)
                    return {}
                discovery = await resp.json(content_type=None)
        except CloudConnectionError:
            raise
        except Exception as err:
            raise CloudConnectionError(
                f"Root API discovery request failed: {err}"
            ) from err

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": HOMEID_ACCEPT,
            "Accept-Language": "en-GB",
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
        }
        # A space that fails for a retryable reason must not be mistaken for a
        # space that has no links: keep trying the others, but if none of them
        # produced links, say so rather than caching an empty map.
        deferred: Exception | None = None
        for space in discovery.get("spaces") or []:
            base_url = space.get("backendBaseUrl", "")
            if not base_url:
                continue
            _LOGGER.debug("Root API: GET %s", base_url)
            try:
                async with session.get(base_url, headers=headers) as resp:
                    if resp.status != 200:
                        self._raise_if_retryable(resp.status, f"Root API {base_url}")
                        continue
                    root = await resp.json(content_type=None)
            except CloudConnectionError as err:
                deferred = err
                continue
            except Exception as err:
                deferred = err
                continue
            links = root.get("_links")
            if isinstance(links, dict) and links:
                self._root_links_cache = links
                return links
        if deferred is not None:
            raise CloudConnectionError(
                f"Root API links unreachable: {deferred}"
            ) from deferred
        _LOGGER.debug("Root API links not found in any space")
        return {}

    @staticmethod
    def _root_link_href(links: dict[str, Any], key: str) -> str | None:
        """Return the templated href for a named root API link."""
        link = links.get(key) or {}
        if isinstance(link, list):
            link = link[0] if link else {}
        return link.get("href") if isinstance(link, dict) else None

    async def _get_autocook_template(self, access_token: str) -> str | None:
        """Return the AutoCook programs URL template from the root API."""
        cached = getattr(self, "_autocook_template_cache", None)
        if cached:
            return cached
        links = await self._get_root_links(access_token)
        href = self._root_link_href(links, "autocookPrograms")
        if href:
            self._autocook_template_cache = href
            _LOGGER.debug("AutoCook template discovered: %s", href)
        return href

    async def get_my_presets(
        self, access_token: str, appliance_id: str, language: str = "en-GB"
    ) -> list[dict[str, Any]]:
        """Fetch the user's custom cooking presets for one appliance.

        Resolves the root API link profileSelfApplianceCookingMethods,
        expands {id} with the appliance id and GETs the collection. Each
        returned preset is a dict with name, short_id, temp, time and
        fahrenheit keys. An empty list means the user has no presets; a
        retryable failure raises CloudConnectionError.
        """
        links = await self._get_root_links(access_token)
        template = self._root_link_href(links, "profileSelfApplianceCookingMethods")
        if not template:
            _LOGGER.debug("My Presets link not in root API")
            return []
        url = template.replace("{id}", appliance_id)
        url = re.sub(r"\{[^}]*\}", "", url)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}ts={int(time.time() * 1000)}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.oneka.v2.0+json",
            "Accept-Language": _expand_language_tag(language),
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
        }
        _LOGGER.debug("My Presets: GET %s", url)
        session = await self._get_session()
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    self._raise_if_retryable(resp.status, "My Presets fetch")
                    _LOGGER.warning("My Presets fetch failed: HTTP %s", resp.status)
                    return []
                data = await resp.json(content_type=None)
        except CloudConnectionError:
            raise
        except Exception as err:
            raise CloudConnectionError(f"My Presets request failed: {err}") from err
        return self._parse_my_presets(data)

    @staticmethod
    def _parse_my_presets(data: Any) -> list[dict[str, Any]]:
        """Parse a CookingMethodsResponse into preset dicts.

        Mirrors the APK SpectreUserPresetSetConverter: a custom preset is a
        manual cook identified by its shortId, carrying a default
        temperature (with unit) and time.

        A 200 whose shape is not the expected object means no presets, not an
        exception: the caller treats a raise as a reason to stop asking.
        """
        out: list[dict[str, Any]] = []
        if not isinstance(data, dict):
            return out
        embedded = data.get("_embedded")
        if not isinstance(embedded, dict):
            embedded = {}
        items: Any = None
        for key in ("item", "cookingMethods", "cookingMethod"):
            if embedded.get(key) is not None:
                items = embedded.get(key)
                break
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return out
        for it in items:
            if not isinstance(it, dict):
                continue
            short_id = it.get("shortId")
            name = it.get("name")
            if not short_id or not name:
                continue
            temperature = it.get("temperature")
            time_obj = it.get("time")
            if not isinstance(temperature, dict):
                temperature = {}
            if not isinstance(time_obj, dict):
                time_obj = {}
            unit = str(temperature.get("unit") or "").upper()
            try:
                temp_default = temperature.get("default")
                time_default = time_obj.get("default")
                temp = int(temp_default) if temp_default is not None else None
                cook_time = int(time_default) if time_default is not None else None
            except (TypeError, ValueError):
                # A preset whose numbers are unreadable is not worth taking the
                # rest of the set down for.
                _LOGGER.debug("Skipping My Preset with unreadable values: %s", name)
                continue
            out.append(
                {
                    "name": str(name),
                    "short_id": str(short_id),
                    "temp": temp,
                    "time": cook_time,
                    "fahrenheit": unit.startswith("F"),
                }
            )
        return out

    @staticmethod
    def _extract_autocook_food_item(data: Any) -> str | None:
        """Extract foodItem from an AutoCook HAL collection response.

        A 200 carrying null, a list, or a null member is still a real answer
        and has to come back as "no food item" rather than raise: the caller
        reads an exception here as a permanent miss and stops asking for that
        recipe. Mirrors _extract_recipe_title.
        """
        if not isinstance(data, dict):
            return None
        embedded = data.get("_embedded")
        if not isinstance(embedded, dict):
            embedded = {}
        for key in ("item", "autocookProgram", "autocookPrograms"):
            items = embedded.get(key)
            if isinstance(items, list) and items and isinstance(items[0], dict):
                food = items[0].get("foodItem")
                if food:
                    return str(food)
            if isinstance(items, dict):
                food = items.get("foodItem")
                if food:
                    return str(food)
        food = data.get("foodItem")
        if food:
            return str(food)
        return None

    # --- Credential migration ---

    async def get_device_credentials(
        self, access_token: str, device_ids: list[str], ctns: list[str]
    ) -> list[dict[str, Any]]:
        """Retrieve local credentials for devices via cloud migration API."""
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        ctn_params = "&".join(f"ctn={c}" for c in ctns)
        url = f"{IOT_BASE}/user/self/device-migration?{ctn_params}"

        body = {
            "sourceAppId": "com.philips.ka.oneka.app",
            "deviceIds": device_ids,
        }

        _LOGGER.debug("Migration request: POST %s, body: %s", url, json.dumps(body))

        async with session.post(url, headers=headers, json=body) as resp:
            text = await resp.text()
            # The body carries localCredentials for every device, so log its
            # size only. The per-device lines below stay free of secrets.
            _LOGGER.debug(
                "Migration response: HTTP %s, %d bytes", resp.status, len(text)
            )
            if resp.status != 200:
                return []
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                _LOGGER.debug("Migration response not JSON, %d bytes", len(text))
                return []

        devices = data if isinstance(data, list) else data.get("devices", [])
        _LOGGER.debug("Migration returned %d device(s)", len(devices))
        result = []
        for device in devices:
            creds_str = device.get("localCredentials")
            _LOGGER.debug(
                "  Migration device id=%s, has localCredentials=%s",
                device.get("id", "?"),
                bool(creds_str),
            )
            if creds_str:
                try:
                    creds = json.loads(creds_str)
                    device["parsed_credentials"] = creds
                    _LOGGER.debug("  Parsed credential keys: %s", list(creds.keys()))
                except (json.JSONDecodeError, TypeError):
                    _LOGGER.debug("  Failed to parse localCredentials JSON")
            result.append(device)

        return result

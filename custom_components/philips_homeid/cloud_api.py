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

from .cloud_auth import CloudAuthError, PhilipsCloudAuth

__all__ = ["CloudAuthError", "PhilipsCloudAPI"]

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
            _LOGGER.debug(
                "MQTT signature response: HTTP %s, body: %s",
                resp.status,
                text[:500],
            )
            if resp.status != 200:
                raise CloudAuthError(
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
            if resp.status != 200:
                _LOGGER.warning("MQTT get-id failed: HTTP %s", resp.status)
                return None
            data = json.loads(text)
            return data.get("userId")

    # --- IoT API ---

    async def get_thing_name(
        self,
        access_token: str,
        device_id: str = "",
        mac_address: str = "",
    ) -> str | None:
        """Get the AWS IoT thingName for a device."""
        devices = await self.get_devices(access_token)
        for dev in devices:
            if device_id and dev.get("id") == device_id:
                thing = dev.get("thingName", "")
                if thing:
                    _LOGGER.debug(
                        "Found thingName=%s for device id=%s", thing, device_id
                    )
                    return thing
            if mac_address and dev.get("macAddress") == mac_address:
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
            if resp.status != 200:
                raise CloudAuthError(f"User profile request failed: {resp.status}")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                raise CloudAuthError(f"User profile response not JSON: {text[:200]}")
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
            if resp.status != 200:
                raise CloudAuthError(
                    f"Device list request failed: HTTP {resp.status}, body: {text[:200]}"
                )
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                raise CloudAuthError(f"Device list response not JSON: {text[:200]}")

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

    async def _backend_login(
        self, oidc_tokens: dict[str, Any], email: str
    ) -> tuple[str | None, dict[str, Any]]:
        """Login to the Home ID backend and return a backend session token.

        The backend at backend.vbs.versuni.com requires its own session token,
        obtained by POSTing the OIDC id_token via the loginConsumer endpoint.
        """
        session = await self._get_session()
        id_token = oidc_tokens.get("id_token", "")

        if not id_token:
            _LOGGER.debug("No id_token available, skipping backend login")
            return None, {}

        common_headers = {
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/vnd.api+json",
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
            "Accept-Language": "en-GB",
        }

        discovery_url = f"{BACKEND_BASE}/.well-known/tenant/oneka"
        _LOGGER.debug("Backend discovery: GET %s", discovery_url)
        try:
            async with session.get(discovery_url) as resp:
                disc_text = await resp.text()
                _LOGGER.debug(
                    "Backend discovery response: HTTP %s, body: %s",
                    resp.status,
                    disc_text[:500],
                )
                if resp.status != 200:
                    _LOGGER.error("Backend discovery failed: HTTP %s", resp.status)
                    return None, {}
                discovery = json.loads(disc_text)
        except Exception:
            _LOGGER.exception("Backend discovery request failed")
            return None, {}

        auth_url = discovery.get("authorizationUrl", "")
        if not auth_url:
            _LOGGER.error("Backend discovery has no authorizationUrl")
            return None, discovery

        spaces = discovery.get("spaces", [])
        space_id = spaces[0].get("spaceId", "") if spaces else ""
        _LOGGER.debug("Backend login URL: %s", auth_url)
        _LOGGER.debug("Backend spaceId: %s", space_id)

        if not space_id:
            _LOGGER.error("No spaceId in discovery response")
            return None, discovery

        login_body = {
            "data": {
                "type": "consumerLoginRequest",
                "attributes": {
                    "email": email,
                    "token": id_token,
                    "identityProvider": "DI",
                    "spaceId": space_id,
                },
            }
        }

        headers = dict(common_headers)
        _LOGGER.debug("Backend login: POST %s", auth_url)
        try:
            async with session.post(auth_url, headers=headers, json=login_body) as resp:
                text = await resp.text()
                _LOGGER.debug(
                    "Backend login response: HTTP %s, body: %s",
                    resp.status,
                    text[:500],
                )
                if resp.status not in (200, 201):
                    _LOGGER.error("Backend login failed: HTTP %s", resp.status)
                    return None, discovery
                data = json.loads(text)
        except Exception:
            _LOGGER.exception("Backend login request failed")
            return None, discovery

        token = data.get("data", {}).get("attributes", {}).get("token")
        if not token:
            token = data.get("token")
        if token:
            _LOGGER.info("Backend login succeeded")
            return token, discovery

        _LOGGER.debug(
            "Backend login response has no token, keys: %s",
            list(data.keys()),
        )
        return None, discovery

    async def get_appliances_via_homeid(
        self, oidc_tokens: dict[str, Any], email: str
    ) -> list[dict[str, Any]]:
        """Get appliances via the Home ID backend API.

        The full chain:
        1. Login to backend with OIDC id_token -> backend session token
        2. Profile: GET {profileUrl} -> _links.userAppliances.href
        3. Appliances: GET {appliancesUrl} -> _embedded.item[]
        """
        session = await self._get_session()
        access_token = oidc_tokens.get("access_token", "")
        ts = int(time.time() * 1000)

        backend_token, discovery = await self._backend_login(oidc_tokens, email)

        auth_token = backend_token or access_token
        token_source = "backend" if backend_token else "oidc"
        _LOGGER.debug("Using %s token for Home ID API", token_source)

        if not discovery:
            _LOGGER.error("No discovery data available")
            return []

        hal_headers = {
            "Authorization": f"Bearer {auth_token}",
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
            _LOGGER.debug(
                "HomeID profile response: HTTP %s, body (first 1000): %s",
                resp.status,
                text[:1000],
            )
            if resp.status != 200:
                _LOGGER.error("HomeID profile failed: HTTP %s", resp.status)
                return []
            try:
                profile = json.loads(text)
            except json.JSONDecodeError:
                _LOGGER.error("HomeID profile not JSON: %s", text[:200])
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
            _LOGGER.debug(
                "HomeID appliances response: HTTP %s, body (first 2000): %s",
                resp.status,
                text[:2000],
            )
            if resp.status != 200:
                _LOGGER.error("HomeID appliances failed: HTTP %s", resp.status)
                return []
            try:
                appliances_data = json.loads(text)
            except json.JSONDecodeError:
                _LOGGER.error("HomeID appliances not JSON: %s", text[:200])
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
                    _LOGGER.warning(
                        "Recipe lookup failed: HTTP %s for %s", resp.status, recipe_id
                    )
                    return None
                data = await resp.json(content_type=None)
        except Exception:
            _LOGGER.exception("Recipe lookup request failed for %s", recipe_id)
            return None

        return self._extract_recipe_title(data)

    @staticmethod
    def _extract_recipe_title(data: dict[str, Any]) -> str | None:
        """Extract recipe title from a JSON:API response."""
        attrs = data.get("data", {}).get("attributes", {})
        title = attrs.get("title")
        if title:
            return str(title)
        for item in data.get("included", []):
            if item.get("type") in ("recipeTranslations", "recipeTranslation"):
                t = item.get("attributes", {}).get("title")
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
                    _LOGGER.warning(
                        "AutoCook lookup failed: HTTP %s for referenceId=%s",
                        resp.status,
                        reference_id,
                    )
                    return None
                data = await resp.json(content_type=None)
        except Exception:
            _LOGGER.exception(
                "AutoCook lookup request failed for referenceId=%s", reference_id
            )
            return None
        return self._extract_autocook_food_item(data)

    async def _get_autocook_template(self, access_token: str) -> str | None:
        """Return the AutoCook programs URL template from the root API.

        Cached on the instance after first successful discovery.
        """
        cached = getattr(self, "_autocook_template_cache", None)
        if cached:
            return cached

        session = await self._get_session()
        discovery_url = f"{BACKEND_BASE}/.well-known/tenant/oneka"
        try:
            async with session.get(discovery_url) as resp:
                if resp.status != 200:
                    _LOGGER.debug("AutoCook discovery failed: HTTP %s", resp.status)
                    return None
                discovery = await resp.json(content_type=None)
        except Exception:
            _LOGGER.debug("AutoCook discovery request failed", exc_info=True)
            return None

        spaces = discovery.get("spaces") or []
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": HOMEID_ACCEPT,
            "Accept-Language": "en-GB",
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
        }
        for space in spaces:
            base_url = space.get("backendBaseUrl", "")
            if not base_url:
                continue
            _LOGGER.debug("AutoCook root API: GET %s", base_url)
            try:
                async with session.get(base_url, headers=headers) as resp:
                    if resp.status != 200:
                        continue
                    root = await resp.json(content_type=None)
            except Exception:
                continue
            links = root.get("_links") or {}
            link = links.get("autocookPrograms") or {}
            if isinstance(link, list):
                link = link[0] if link else {}
            href = link.get("href") if isinstance(link, dict) else None
            if href:
                self._autocook_template_cache = href
                _LOGGER.debug("AutoCook template discovered: %s", href)
                return href
        _LOGGER.debug("AutoCook template not found in any space")
        return None

    @staticmethod
    def _extract_autocook_food_item(data: dict[str, Any]) -> str | None:
        """Extract foodItem from an AutoCook HAL collection response."""
        embedded = data.get("_embedded") or {}
        for key in ("item", "autocookProgram", "autocookPrograms"):
            items = embedded.get(key)
            if isinstance(items, list) and items:
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
            _LOGGER.debug(
                "Migration response: HTTP %s, body: %s", resp.status, text[:1000]
            )
            if resp.status != 200:
                return []
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                _LOGGER.debug("Migration response not JSON: %s", text[:200])
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

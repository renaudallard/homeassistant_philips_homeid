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
"""Philips Cloud API for OTP authentication and device credential retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import shutil
import subprocess
import time
import urllib.parse
from base64 import urlsafe_b64encode
from typing import Any

import aiohttp

from .const import (
    GIGYA_API_KEY,
    GIGYA_API_URL,
    MOBILE_APP_REDIRECT_URI,
    OAUTH_CLIENT_ID,
    OIDC_AUTH_ENDPOINT,
    OIDC_TOKEN_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

# IoT API (production, from APK DomainConfig)
IOT_BASE = "https://prod.eu-da.iot.versuni.com/api/da"

# Home ID API (from APK BackendConfigKt)
BACKEND_API_BASE = "https://www.backend.vbs.versuni.com/api"
HOMEID_API_BASE = "https://www.home.id/api"
HOMEID_ACCEPT = "application/vnd.oneka.v2.0+json"

# Headers matching the Android app (DefaultRequestInterceptor)
HOMEID_USER_AGENT = (
    "HomeID/8.16.0 (com.philips.ka.oneka.app; build:8160001; Android 14)"
)
HOMEID_X_USER_AGENT = "Android 14;8.16.0"

# OAuth scopes (full set from APK)
OAUTH_SCOPES = (
    "openid profile email offline_access "
    "DI.Account.read DI.AccountProfile.read DI.AccountProfile.write "
    "DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write "
    "DI.GeneralConsent.read DI.GeneralConsent.write "
    "VoiceProvider.read VoiceProvider.write "
    "subscriptions consents profile_extended "
    "DI.AccountSubscription.write DI.AccountSubscription.read"
)


class CloudAuthError(Exception):
    """Raised on cloud authentication failures."""


class PhilipsCloudAPI:
    """Async client for Philips cloud authentication and device management."""

    def __init__(self) -> None:
        """Initialize the cloud API client."""
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def request_otp(self, email: str) -> str:
        """Request OTP code to be sent to the user's email.

        Returns the vToken needed for OTP verification.
        """
        session = await self._get_session()
        url = f"{GIGYA_API_URL}/accounts.auth.otp.email.sendCode"
        params = {
            "email": email,
            "apiKey": GIGYA_API_KEY,
            "format": "json",
        }

        async with session.post(url, data=params) as resp:
            data = await resp.json(content_type=None)

        error_code = data.get("errorCode", -1)
        if error_code != 0:
            msg = data.get("errorMessage", "Unknown error")
            raise CloudAuthError(f"OTP request failed: {msg}")

        vtoken = data.get("vToken")
        if not vtoken:
            raise CloudAuthError("No vToken in OTP response")

        _LOGGER.debug("OTP sent to %s", email)
        return vtoken

    async def verify_otp(self, email: str, code: str, vtoken: str) -> str:
        """Verify the OTP code entered by the user.

        Returns the Gigya session token.
        """
        session = await self._get_session()
        url = f"{GIGYA_API_URL}/accounts.auth.otp.email.login"
        params = {
            "email": email,
            "code": code,
            "vToken": vtoken,
            "apiKey": GIGYA_API_KEY,
            "format": "json",
        }

        async with session.post(url, data=params) as resp:
            data = await resp.json(content_type=None)

        error_code = data.get("errorCode", -1)
        if error_code != 0:
            msg = data.get("errorMessage", "Unknown error")
            raise CloudAuthError(f"OTP verification failed: {msg}")

        session_token = data.get("sessionInfo", {}).get("cookieValue")
        if not session_token:
            raise CloudAuthError("No session token in OTP response")

        _LOGGER.debug("OTP verified for %s", email)
        return session_token

    async def async_install_playwright(self) -> bool:
        """Install Playwright asynchronously (runs in executor)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.install_playwright)

    async def get_oidc_tokens(self, session_token: str) -> dict[str, Any]:
        """Exchange Gigya session for OIDC tokens using headless browser.

        Uses PKCE flow with Playwright to execute the Gigya OAuth authorize
        page JavaScript, exactly matching the mobile app's AppAuth flow.
        Playwright is auto-installed before use and uninstalled after.

        Returns dict with access_token, refresh_token, id_token, expires_in.
        Raises CloudAuthError on failure.
        """
        # Generate PKCE challenge
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(16)

        # Build authorize URL
        params = {
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": MOBILE_APP_REDIRECT_URI,
            "scope": OAUTH_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{OIDC_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

        # Run headless browser OAuth in executor (Playwright is sync)
        loop = asyncio.get_running_loop()
        auth_code = await loop.run_in_executor(
            None, self._browser_oauth, session_token, auth_url
        )

        if not auth_code:
            raise CloudAuthError(
                "Could not obtain authorization code. "
                "Please ensure you have used the Philips HomeID app "
                "with this account at least once."
            )

        return await self._exchange_code(auth_code, code_verifier)

    @staticmethod
    def _playwright_available() -> bool:
        """Check if Playwright is already installed."""
        try:
            import playwright  # noqa: F401

            return True
        except ImportError:
            return False

    @staticmethod
    def install_playwright() -> bool:
        """Install Playwright and Chromium. Returns True on success.

        Safe to call if already installed (no-op).
        """
        try:
            import playwright  # noqa: F401

            _LOGGER.debug("Playwright already installed")
            return True
        except ImportError:
            pass

        _LOGGER.info("Installing playwright for cloud authentication")
        try:
            subprocess.run(
                ["pip", "install", "playwright"],
                capture_output=True,
                check=True,
                timeout=120,
            )
            _LOGGER.debug("Playwright pip package installed, installing chromium")
            subprocess.run(
                ["playwright", "install", "chromium"],
                capture_output=True,
                check=True,
                timeout=300,
            )
            _LOGGER.info("Playwright and chromium installed successfully")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, TimeoutError):
            _LOGGER.exception("Failed to install playwright")
            return False

    @staticmethod
    def _uninstall_playwright() -> None:
        """Uninstall Playwright and its browsers."""
        _LOGGER.info("Uninstalling playwright")
        try:
            # Remove browser binaries first
            if shutil.which("playwright"):
                subprocess.run(
                    ["playwright", "uninstall"],
                    capture_output=True,
                    timeout=60,
                )
            subprocess.run(
                ["pip", "uninstall", "-y", "playwright"],
                capture_output=True,
                timeout=60,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, TimeoutError):
            _LOGGER.debug("Playwright uninstall failed (non-critical)")

    @staticmethod
    def _browser_oauth(session_token: str, auth_url: str) -> str | None:
        """Run headless browser OAuth flow (sync, runs in executor).

        Matches the exact flow from cloud_key_fetcher.py:
        1. Launch headless Chromium
        2. Set Gigya session cookies
        3. Navigate to authorize URL
        4. Intercept authorize/continue response for auth code
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _LOGGER.error("Playwright not available for browser OAuth")
            return None

        auth_code = None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()

                # Set Gigya session cookies (exact match with cloud_key_fetcher)
                gmid = secrets.token_hex(16)
                context.add_cookies(
                    [
                        {
                            "name": f"glt_{GIGYA_API_KEY}",
                            "value": session_token,
                            "domain": ".accounts.home.id",
                            "path": "/",
                        },
                        {
                            "name": f"gac_{GIGYA_API_KEY}",
                            "value": session_token,
                            "domain": ".accounts.home.id",
                            "path": "/",
                        },
                        {
                            "name": "gmid",
                            "value": gmid,
                            "domain": ".accounts.home.id",
                            "path": "/",
                        },
                        {
                            "name": "ucid",
                            "value": gmid,
                            "domain": ".accounts.home.id",
                            "path": "/",
                        },
                        {
                            "name": "hasGmid",
                            "value": "ver4",
                            "domain": ".accounts.home.id",
                            "path": "/",
                        },
                    ]
                )

                page = context.new_page()

                # Intercept authorize/continue response for the auth code
                def handle_response(response: Any) -> None:
                    nonlocal auth_code
                    if "authorize/continue" in response.url and not auth_code:
                        for header in response.headers_array():
                            if header["name"].lower() == "location":
                                location = header["value"]
                                if "code=" in location:
                                    parsed = urllib.parse.urlparse(location)
                                    qs = urllib.parse.parse_qs(parsed.query)
                                    codes = qs.get("code", [])
                                    if codes:
                                        auth_code = codes[0]

                page.on("response", handle_response)

                try:
                    page.goto(auth_url, timeout=30000, wait_until="networkidle")
                except Exception:
                    pass

                # Wait for JS to complete if code not captured yet
                if not auth_code:
                    for _ in range(10):
                        page.wait_for_timeout(1000)
                        if auth_code:
                            break

                browser.close()
        except Exception:
            _LOGGER.exception("Browser OAuth flow failed")
        finally:
            PhilipsCloudAPI._uninstall_playwright()

        return auth_code

    async def _exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange authorization code for OIDC tokens."""
        session = await self._get_session()
        data = {
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": MOBILE_APP_REDIRECT_URI,
            "code_verifier": code_verifier,
        }

        _LOGGER.debug("Exchanging auth code for tokens at %s", OIDC_TOKEN_ENDPOINT)
        async with session.post(OIDC_TOKEN_ENDPOINT, data=data) as resp:
            text = await resp.text()
            _LOGGER.debug("Token exchange response: HTTP %s", resp.status)
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                raise CloudAuthError(f"Token exchange response not JSON: {text[:200]}")

        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "Unknown"))
            _LOGGER.debug("Token exchange error response: %s", text[:500])
            raise CloudAuthError(f"Token exchange failed: {error}")

        _LOGGER.debug(
            "OIDC tokens obtained (scopes: %s, expires_in: %s)",
            result.get("scope", "?"),
            result.get("expires_in", "?"),
        )
        return result

    async def refresh_tokens(self, refresh_token: str) -> dict[str, Any]:
        """Refresh OIDC tokens using the refresh token."""
        session = await self._get_session()
        data = {
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        async with session.post(OIDC_TOKEN_ENDPOINT, data=data) as resp:
            result = await resp.json(content_type=None)

        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "Unknown"))
            raise CloudAuthError(f"Token refresh failed: {error}")

        return result

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

        # Handle both flat list and nested dict responses
        if isinstance(data, list):
            devices = data
        elif isinstance(data, dict):
            # Try common nesting patterns
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
                "  Device: id=%s, ctn=%s, name=%s, mac=%s",
                dev.get("id", "?"),
                dev.get("ctn", "?"),
                dev.get("friendlyName", "?"),
                dev.get("macAddress", "?"),
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

    async def _backend_login(
        self, oidc_tokens: dict[str, Any], email: str
    ) -> str | None:
        """Login to the Home ID backend and return a backend session token.

        The backend at backend.vbs.versuni.com requires its own session token,
        obtained by POSTing the OIDC id_token via the loginConsumer endpoint.
        Uses JSON:API format (LoginUserParams type=consumerLoginRequest).

        Tries multiple candidate paths since the exact URL normally comes
        from the discovery service which itself requires auth.
        """
        session = await self._get_session()
        id_token = oidc_tokens.get("id_token", "")
        access_token = oidc_tokens.get("access_token", "")

        if not id_token:
            _LOGGER.debug("No id_token available, skipping backend login")
            return None

        # JSON:API format matching LoginUserParams (type=consumerLoginRequest)
        login_body = {
            "data": {
                "type": "consumerLoginRequest",
                "attributes": {
                    "email": email,
                    "token": id_token,
                    "identityProvider": "DI",
                },
            }
        }

        common_headers = {
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/vnd.api+json",
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
            "Accept-Language": "en-GB",
        }

        # Try with and without Bearer token, multiple paths
        login_paths = [
            "v2/authorization",
            "v1/authorization",
            "v2/consumer/login",
            "v2/login",
        ]

        for path in login_paths:
            url = f"{BACKEND_API_BASE}/{path}"
            for token_type, token_val in [
                ("access_token", access_token),
                ("none", ""),
            ]:
                headers = dict(common_headers)
                if token_val:
                    headers["Authorization"] = f"Bearer {token_val}"

                _LOGGER.debug("Backend login: POST %s (auth=%s)", url, token_type)
                try:
                    async with session.post(
                        url, headers=headers, json=login_body
                    ) as resp:
                        text = await resp.text()
                        _LOGGER.debug(
                            "Backend login response: HTTP %s, body: %s",
                            resp.status,
                            text[:500],
                        )
                        if resp.status == 200 or resp.status == 201:
                            try:
                                data = json.loads(text)
                            except json.JSONDecodeError:
                                continue
                            # JSON:API response: data.attributes.token
                            token = (
                                data.get("data", {}).get("attributes", {}).get("token")
                            )
                            if not token:
                                # Try flat response
                                token = data.get("token")
                            if token:
                                _LOGGER.info("Backend login succeeded at %s", url)
                                return token
                            _LOGGER.debug(
                                "Login 200 but no token in response: %s",
                                list(data.keys()),
                            )
                except Exception:
                    _LOGGER.debug("Backend login request failed for %s", url)

        _LOGGER.warning("Backend login failed on all paths")
        return None

    async def get_appliances_via_homeid(
        self, oidc_tokens: dict[str, Any], email: str
    ) -> list[dict[str, Any]]:
        """Get appliances via the Home ID backend API.

        The full chain:
        1. Login to backend with OIDC id_token -> backend session token
        2. Discovery: GET /.well-known/tenant/oneka -> profileUrl
        3. Profile: GET {profileUrl} -> _links.userAppliances.href
        4. Appliances: GET {appliancesUrl} -> _embedded.item[]

        Falls back to trying discovery directly with the OIDC access_token
        if backend login fails.
        """
        session = await self._get_session()
        access_token = oidc_tokens.get("access_token", "")
        ts = int(time.time() * 1000)

        # Step 1: Try to get a backend session token
        backend_token = await self._backend_login(oidc_tokens, email)

        # Use backend token if available, otherwise try OIDC access_token
        auth_token = backend_token or access_token
        token_source = "backend" if backend_token else "oidc"
        _LOGGER.debug("Using %s token for Home ID API", token_source)

        hal_headers = {
            "Authorization": f"Bearer {auth_token}",
            "Accept": HOMEID_ACCEPT,
            "Accept-Language": "en-GB",
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
        }

        # Step 2: Discovery service (try both base URLs)
        discovery = None
        for base in [BACKEND_API_BASE, HOMEID_API_BASE]:
            discovery_url = f"{base}/.well-known/tenant/oneka"
            _LOGGER.debug("HomeID discovery: GET %s", discovery_url)
            try:
                async with session.get(discovery_url, headers=hal_headers) as resp:
                    text = await resp.text()
                    _LOGGER.debug(
                        "HomeID discovery response: HTTP %s, body: %s",
                        resp.status,
                        text[:500],
                    )
                    if resp.status == 200:
                        try:
                            discovery = json.loads(text)
                            _LOGGER.info("HomeID discovery succeeded at %s", base)
                            break
                        except json.JSONDecodeError:
                            _LOGGER.error("HomeID discovery not JSON: %s", text[:200])
            except Exception:
                _LOGGER.debug("HomeID discovery request failed for %s", base)

        if not discovery:
            _LOGGER.error("HomeID discovery failed on all base URLs")
            return []

        _LOGGER.debug("Discovery keys: %s", list(discovery.keys()))

        profile_url = discovery.get("profileUrl")
        if not profile_url:
            _LOGGER.error(
                "HomeID discovery has no profileUrl, keys: %s",
                list(discovery.keys()),
            )
            return []

        # Make profile URL absolute if relative
        if profile_url.startswith("/"):
            profile_url = f"{BACKEND_API_BASE}{profile_url}"

        # Step 3: Get user profile
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

        # Try embedded appliances first
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

        # Follow HAL link to appliances
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

        # Step 4: Get appliances
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

        # Extract items from HAL _embedded.item
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

    async def get_device_credentials(
        self, access_token: str, device_ids: list[str], ctns: list[str]
    ) -> list[dict[str, Any]]:
        """Retrieve local credentials for devices via cloud migration API.

        Args:
            access_token: OIDC access token
            device_ids: List of device IDs from get_devices()
            ctns: List of model numbers (e.g., ["HD9280"])

        Returns list of device dicts with localCredentials field.
        """
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

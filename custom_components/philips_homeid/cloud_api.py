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

# OAuth scopes (full set from APK)
OAUTH_SCOPES = (
    "openid profile email offline_access "
    "DI.Account.read DI.AccountProfile.read DI.AccountProfile.write "
    "DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write "
    "DI.GeneralConsent.read DI.GeneralConsent.write "
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
    def _ensure_playwright() -> bool:
        """Install Playwright and Chromium if not present. Returns True on success."""
        try:
            import playwright  # noqa: F401

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
            subprocess.run(
                ["playwright", "install", "chromium"],
                capture_output=True,
                check=True,
                timeout=300,
            )
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
        if not PhilipsCloudAPI._ensure_playwright():
            return None

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
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

        async with session.post(OIDC_TOKEN_ENDPOINT, data=data) as resp:
            result = await resp.json(content_type=None)

        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "Unknown"))
            raise CloudAuthError(f"Token exchange failed: {error}")

        _LOGGER.debug("OIDC tokens obtained")
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

    async def get_devices(self, access_token: str) -> list[dict[str, Any]]:
        """List devices registered to the user's account."""
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        async with session.get(f"{IOT_BASE}/user/self/device", headers=headers) as resp:
            if resp.status != 200:
                raise CloudAuthError(f"Device list request failed: {resp.status}")
            return await resp.json(content_type=None)

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

        async with session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                text = await resp.text()
                _LOGGER.debug("Migration API response: %s %s", resp.status, text[:200])
                return []
            data = await resp.json(content_type=None)

        devices = data if isinstance(data, list) else data.get("devices", [])
        result = []
        for device in devices:
            creds_str = device.get("localCredentials")
            if creds_str:
                try:
                    creds = json.loads(creds_str)
                    device["parsed_credentials"] = creds
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(device)

        return result

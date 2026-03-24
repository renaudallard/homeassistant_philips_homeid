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

import hashlib
import json
import logging
import secrets
import urllib.parse
from base64 import urlsafe_b64encode
from typing import Any

import aiohttp
from yarl import URL

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


class ConsentRequired(Exception):
    """Raised when OAuth consent has not been granted yet."""


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

        async with session.get(url, params=params) as resp:
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

        async with session.get(url, params=params) as resp:
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
        """Exchange Gigya session for OIDC tokens.

        Uses PKCE flow. Follows the full redirect chain through
        Gigya's authorize/continue endpoint to capture the auth code.

        The redirect chain is: /authorize -> /authorize/continue ->
        redirect_uri (custom scheme). aiohttp follows HTTP redirects
        and we scan the history for the auth code in Location headers.

        Returns dict with access_token, refresh_token, id_token, expires_in.
        Raises ConsentRequired if interactive consent is needed.
        """
        # Generate PKCE challenge
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(32)

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

        # Create session with Gigya cookies
        gmid = secrets.token_hex(16)
        jar = aiohttp.CookieJar(unsafe=True)
        gigya_url = URL(GIGYA_API_URL)
        cookies = {
            f"glt_{GIGYA_API_KEY}": session_token,
            f"gac_{GIGYA_API_KEY}": session_token,
            "gmid": gmid,
            "ucid": gmid,
            "hasGmid": "ver4",
        }
        jar.update_cookies(cookies, gigya_url)
        cookie_session = aiohttp.ClientSession(cookie_jar=jar)
        try:
            auth_code = await self._follow_authorize_flow(cookie_session, auth_url)
            if auth_code:
                return await self._exchange_code(auth_code, code_verifier)

            raise ConsentRequired(
                "Please open the Philips HomeID app on your phone, "
                "log in with this account, then try again here."
            )
        finally:
            await cookie_session.close()

    async def _follow_authorize_flow(
        self, session: aiohttp.ClientSession, auth_url: str
    ) -> str | None:
        """Follow the OAuth authorize redirect chain to extract the auth code.

        The Gigya authorize endpoint uses a multi-step flow:
        1. GET /authorize -> 302 to /authorize/continue (or 200 with JS)
        2. GET /authorize/continue -> 302 to redirect_uri with code=

        aiohttp will fail on the final redirect to the custom scheme
        (com.philips.ka.oneka.app.prod://), so we also scan redirect
        history and catch InvalidUrlClientError.
        """
        try:
            async with session.get(auth_url, max_redirects=10) as resp:
                # Check final URL
                final_url = str(resp.url)
                auth_code = self._extract_code(final_url)
                if auth_code:
                    return auth_code

                # Scan redirect history for the code
                for hist_resp in resp.history:
                    location = hist_resp.headers.get("Location", "")
                    auth_code = self._extract_code(location)
                    if auth_code:
                        return auth_code

        except aiohttp.InvalidUrlClientError as err:
            # aiohttp raises this when redirected to a non-HTTP scheme
            # (com.philips.ka.oneka.app.prod://oauthredirect?code=...)
            # The auth code is in the invalid URL
            err_url = str(err)
            auth_code = self._extract_code(err_url)
            if auth_code:
                return auth_code
        except Exception:
            _LOGGER.debug("Error during authorize flow", exc_info=True)

        return None

    @staticmethod
    def _extract_code(url: str) -> str | None:
        """Extract authorization code from a URL."""
        if "code=" not in url:
            return None
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        codes = qs.get("code")
        return codes[0] if codes else None

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

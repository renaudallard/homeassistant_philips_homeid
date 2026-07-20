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
"""Philips Cloud authentication: OTP, OAuth, token management.

OAuth uses a pure-HTTP flow: a Gigya passive login (``prompt=none``),
a gmidTicket from ``socialize.getIDs``, and ``/authorize/continue`` to
obtain the authorization code. No external binaries, no installs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import urllib.parse
from base64 import urlsafe_b64encode
from typing import Any, NamedTuple

import aiohttp

from .const import (
    AIRPLUS_CLIENT_ID,
    AIRPLUS_REDIRECT_URI,
    AIRPLUS_SCOPES,
    GIGYA_API_KEY,
    GIGYA_API_URL,
    MOBILE_APP_REDIRECT_URI,
    OAUTH_CLIENT_AIRPLUS,
    OAUTH_CLIENT_HOMEID,
    OAUTH_CLIENT_ID,
    OIDC_AUTH_ENDPOINT,
    OIDC_ISSUER,
    OIDC_TOKEN_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

OAUTH_SCOPES = (
    "openid profile email offline_access "
    "DI.Account.read DI.AccountProfile.read DI.AccountProfile.write "
    "DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write "
    "DI.GeneralConsent.read DI.GeneralConsent.write "
    "VoiceProvider.read VoiceProvider.write "
    "subscriptions consent profile_extended "
    "DI.AccountSubscription.write DI.AccountSubscription.read"
)


class _OAuthClient(NamedTuple):
    """OAuth parameters that differ between the HomeID and Air+ clients.

    Both are public clients driven by the same pure-HTTP prompt=none PKCE
    flow, so neither sends a client secret; only the id, redirect and scope
    differ.
    """

    client_id: str
    redirect_uri: str
    scope: str


_OAUTH_CLIENTS: dict[str, _OAuthClient] = {
    OAUTH_CLIENT_HOMEID: _OAuthClient(
        OAUTH_CLIENT_ID, MOBILE_APP_REDIRECT_URI, OAUTH_SCOPES
    ),
    OAUTH_CLIENT_AIRPLUS: _OAuthClient(
        AIRPLUS_CLIENT_ID, AIRPLUS_REDIRECT_URI, AIRPLUS_SCOPES
    ),
}


def _oauth_client(client: str) -> _OAuthClient:
    """Return the OAuth parameters for a client id, defaulting to HomeID."""
    return _OAUTH_CLIENTS.get(client, _OAUTH_CLIENTS[OAUTH_CLIENT_HOMEID])


_SOCIALIZE_GET_IDS = f"{GIGYA_API_URL}/socialize.getIDs"

# aiohttp defaults to a 5 minute total and no connect bound. Every cloud call
# runs under the coordinator's token lock, and the MQTT credential refresh
# gives up waiting after 10s without cancelling the request, so an endpoint
# that black-holes would hold the lock long after the caller stopped caring
# and stall every other cloud fetch behind it.
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)


class CloudAuthError(Exception):
    """Raised on permanent cloud authentication failures (reauth needed).

    Examples: refresh_token rejected with invalid_grant, OTP failed,
    HTTP 401 from a token-protected endpoint. The caller is expected
    to convert this into a reauth prompt.
    """


class CloudConnectionError(CloudAuthError):
    """Raised on transient cloud failures that the caller should retry.

    Examples: HTTP 5xx, network errors, malformed responses. Subclasses
    CloudAuthError so existing broad ``except CloudAuthError`` blocks
    keep handling it as before, but specific callers can catch this
    first to retry instead of triggering reauth.
    """


class CloudNotRegisteredError(CloudAuthError):
    """Raised when the OTP code is accepted but the account is not usable.

    Gigya returns errorCode 206001 ("Account Pending Registration") when
    the verification code was correct but the email is not a fully
    registered Philips HomeID account (required registration/consent
    fields are missing, e.g. the user signed up via a social provider or
    never finished registration). Email-OTP login cannot complete it, so
    the caller should tell the user to register in the app or fall back
    to manual credential entry rather than report a wrong code.
    """


class CloudBackendError(CloudConnectionError):
    """Raised when the HomeID backend returns a server error for the account.

    The profile and appliance endpoints return HTTP 5xx when the token is
    accepted but the backend cannot build the response, typically because the
    account has no completed HomeID profile (country and consents are set
    during app onboarding). Subclasses CloudConnectionError so it is still
    treated as retryable, but the config flow catches it first to show a
    message that points at the official app rather than "cloud unreachable".
    """


class PhilipsCloudAuth:
    """Handles OTP login, OAuth, and token management."""

    def __init__(self) -> None:
        """Initialize the auth client."""
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def request_otp(self, email: str) -> str:
        """Request OTP code to be sent to the user's email.

        Returns the vToken needed for OTP verification.
        Raises CloudConnectionError on transport / non-JSON failures and
        CloudAuthError on Gigya-side error codes.
        """
        session = await self._get_session()
        url = f"{GIGYA_API_URL}/accounts.auth.otp.email.sendCode"
        params = {
            "email": email,
            "apiKey": GIGYA_API_KEY,
            "format": "json",
        }

        try:
            async with session.post(url, data=params) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except (json.JSONDecodeError, ValueError) as err:
                    raise CloudConnectionError(
                        f"OTP send endpoint returned non-JSON (HTTP {status})"
                    ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CloudConnectionError(f"OTP send endpoint unreachable: {err}") from err

        if status >= 500:
            raise CloudConnectionError(f"OTP send failed: HTTP {status}")

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

        Returns the Gigya session token. Raises CloudConnectionError on
        transport / non-JSON failures and CloudAuthError on Gigya errors.
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

        try:
            async with session.post(url, data=params) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except (json.JSONDecodeError, ValueError) as err:
                    raise CloudConnectionError(
                        f"OTP verify endpoint returned non-JSON (HTTP {status})"
                    ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CloudConnectionError(
                f"OTP verify endpoint unreachable: {err}"
            ) from err

        if status >= 500:
            raise CloudConnectionError(f"OTP verify failed: HTTP {status}")

        error_code = data.get("errorCode", -1)
        if error_code == 206001:
            # The code was accepted, but this email is not a fully
            # registered Philips HomeID account (Gigya "Account Pending
            # Registration"). Completing it needs consent fields the
            # integration cannot grant, so steer the user accordingly.
            raise CloudNotRegisteredError(
                "Account pending registration: this email is not a fully "
                "registered Philips HomeID account"
            )
        if error_code != 0:
            msg = data.get("errorMessage", "Unknown error")
            raise CloudAuthError(msg)

        session_token = data.get("sessionInfo", {}).get("cookieValue")
        if not session_token:
            raise CloudAuthError("No session token in OTP response")

        _LOGGER.debug("OTP verified for %s", email)
        return session_token

    async def get_oidc_tokens(
        self, session_token: str, client: str = OAUTH_CLIENT_HOMEID
    ) -> dict[str, Any]:
        """Exchange a Gigya session for OIDC tokens.

        Pure HTTP: we ask Gigya for a passive login (``prompt=none``),
        fetch a gmidTicket via ``socialize.getIDs``, then call
        ``/authorize/continue`` to receive the authorization code in a
        redirect Location header. PKCE is completed against the ``/token``
        endpoint as usual.

        ``client`` selects the OAuth client (OAUTH_CLIENT_HOMEID or
        OAUTH_CLIENT_AIRPLUS); the same Gigya session works for either, so
        an Air+ token can be minted from the HomeID OTP login.
        """
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

        try:
            auth_code = await self._http_oauth(session_token, code_challenge, client)
            return await self._exchange_code(auth_code, code_verifier, client)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CloudConnectionError(f"OAuth flow unreachable: {err}") from err

    async def _http_oauth(
        self,
        session_token: str,
        code_challenge: str,
        client: str = OAUTH_CLIENT_HOMEID,
    ) -> str:
        """Pure-HTTP OAuth flow. Returns the authorization code."""
        session = await self._get_session()
        cfg = _oauth_client(client)

        auth_params = {
            "client_id": cfg.client_id,
            "response_type": "code",
            "redirect_uri": cfg.redirect_uri,
            "scope": cfg.scope,
            "state": secrets.token_urlsafe(16),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # passiveLogin: skips the consent dialog for users who already
            # consented via the Philips HomeID app. Without it, Gigya's
            # /authorize/continue rejects the request because it expects a
            # signed consent token only the WebSDK consent UI can produce.
            "prompt": "none",
        }
        auth_url = f"{OIDC_AUTH_ENDPOINT}?{urllib.parse.urlencode(auth_params)}"

        async with session.get(auth_url, allow_redirects=False) as resp:
            status = resp.status
            if status not in (301, 302, 303, 307, 308):
                body = (await resp.text())[:300]
                if status >= 500:
                    raise CloudConnectionError(
                        f"/authorize unreachable: HTTP {status}: {body}"
                    )
                raise CloudAuthError(
                    f"/authorize: expected redirect, got HTTP {status}: {body}"
                )
            location = resp.headers.get("Location", "")

        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        context_jwt = (query.get("context") or [""])[0]
        if not context_jwt:
            raise CloudAuthError(
                f"/authorize: no 'context' in Location header ({location[:200]})"
            )

        async with session.post(
            _SOCIALIZE_GET_IDS,
            data={
                "APIKey": GIGYA_API_KEY,
                "includeTicket": "true",
                "format": "json",
            },
        ) as resp:
            try:
                ids_data = await resp.json(content_type=None)
            except (json.JSONDecodeError, ValueError) as json_err:
                raise CloudConnectionError(
                    f"socialize.getIDs returned non-JSON (HTTP {resp.status})"
                ) from json_err
        gmid_ticket = ids_data.get("gmidTicket")
        if not gmid_ticket:
            err = ids_data.get("errorMessage") or ids_data.get("errorCode")
            raise CloudAuthError(f"socialize.getIDs returned no gmidTicket: {err}")

        cont_params = {
            "context": context_jwt,
            "login_token": session_token,
            "gmidTicket": gmid_ticket,
            "client_id": cfg.client_id,
        }
        cont_url = (
            f"{OIDC_ISSUER}/authorize/continue?{urllib.parse.urlencode(cont_params)}"
        )
        async with session.get(cont_url, allow_redirects=False) as resp:
            status = resp.status
            if status not in (301, 302, 303, 307, 308):
                body = (await resp.text())[:300]
                if status >= 500:
                    raise CloudConnectionError(
                        f"/authorize/continue unreachable: HTTP {status}: {body}"
                    )
                raise CloudAuthError(
                    f"/authorize/continue: expected redirect, got HTTP {status}: {body}"
                )
            location = resp.headers.get("Location", "")

        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        if query.get("errorMessage"):
            raise CloudAuthError(f"/authorize/continue: {query['errorMessage'][0]}")
        auth_code = (query.get("code") or [""])[0]
        if not auth_code:
            raise CloudAuthError(
                f"/authorize/continue: no 'code' in Location ({location[:200]})"
            )
        return auth_code

    # ------------------------------------------------------------------ #
    # Token exchange.
    # ------------------------------------------------------------------ #

    async def _exchange_code(
        self, code: str, code_verifier: str, client: str = OAUTH_CLIENT_HOMEID
    ) -> dict[str, Any]:
        """Exchange authorization code for OIDC tokens.

        Raises CloudAuthError only when the code is permanently rejected. Any
        other failure (5xx, malformed response, network) raises
        CloudConnectionError, so the user is told the cloud is unreachable
        rather than that their verification code was wrong.
        """
        session = await self._get_session()
        cfg = _oauth_client(client)
        data = {
            "client_id": cfg.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": cfg.redirect_uri,
            "code_verifier": code_verifier,
        }

        _LOGGER.debug("Exchanging auth code for tokens at %s", OIDC_TOKEN_ENDPOINT)
        try:
            async with session.post(OIDC_TOKEN_ENDPOINT, data=data) as resp:
                status = resp.status
                text = await resp.text()
                _LOGGER.debug("Token exchange response: HTTP %s", status)
                try:
                    result = json.loads(text)
                except json.JSONDecodeError as err:
                    raise CloudConnectionError(
                        f"Token exchange returned non-JSON (HTTP {status}): "
                        f"{text[:200]}"
                    ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CloudConnectionError(f"Token endpoint unreachable: {err}") from err

        if "access_token" not in result:
            error_code = result.get("error", "")
            error = result.get("error_description", error_code or f"HTTP {status}")
            _LOGGER.debug("Token exchange error response: %s", text[:500])
            if status == 401 or error_code in ("invalid_grant", "invalid_token"):
                raise CloudAuthError(f"Token exchange rejected: {error}")
            raise CloudConnectionError(
                f"Token exchange failed (HTTP {status}): {error}"
            )

        _LOGGER.debug(
            "OIDC tokens obtained (scopes: %s, expires_in: %s)",
            result.get("scope", "?"),
            result.get("expires_in", "?"),
        )
        return result

    async def refresh_tokens(
        self, refresh_token: str, client: str = OAUTH_CLIENT_HOMEID
    ) -> dict[str, Any]:
        """Refresh OIDC tokens using the refresh token.

        ``client`` must match the OAuth client the refresh token was issued
        for; an Air+ refresh token can only be refreshed by the Air+ client.

        Raises CloudAuthError only when the refresh token is permanently
        rejected (invalid_grant / invalid_token / HTTP 401). Any other
        failure (5xx, malformed response, network) raises
        CloudConnectionError so the caller retries instead of forcing
        the user into reauth.
        """
        session = await self._get_session()
        cfg = _oauth_client(client)
        data = {
            "client_id": cfg.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        try:
            async with session.post(OIDC_TOKEN_ENDPOINT, data=data) as resp:
                status = resp.status
                try:
                    result = await resp.json(content_type=None)
                except (json.JSONDecodeError, ValueError) as err:
                    raise CloudConnectionError(
                        f"Token endpoint returned non-JSON (HTTP {status})"
                    ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CloudConnectionError(f"Token endpoint unreachable: {err}") from err

        if "access_token" in result:
            return result

        error_code = result.get("error", "")
        error_msg = result.get("error_description", error_code or f"HTTP {status}")
        if status == 401 or error_code in ("invalid_grant", "invalid_token"):
            raise CloudAuthError(f"Token refresh rejected: {error_msg}")
        raise CloudConnectionError(f"Token refresh failed (HTTP {status}): {error_msg}")

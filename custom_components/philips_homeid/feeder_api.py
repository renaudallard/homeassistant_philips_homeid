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
"""HomeID pet REST backend for the PAW5320 feeder.

The HomeID app keeps its feeders on the pet backend below, which accepts the
same HomeID OIDC access token this integration already holds. Everything here
is plain aiohttp: the feeder needs no extra requirement and no other account.

This version only reads: the transport refuses every method but GET, so
nothing in the integration can change the feeder's schedule or settings.
Only fixed paths on the fixed host are requested, URLs found in responses are
never followed and redirects are refused. Error messages carry no response
body, URL, identifier or token.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp
from homeassistant.core import HomeAssistant

from .cloud_auth import CloudAuthError, CloudConnectionError
from .const import (
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_OAUTH_CLIENT,
    DOMAIN,
    OAUTH_CLIENT_HOMEID,
)

PET_API_URL = "https://petseries.production.nbx.iot.versuni.com"
SUPPORTED_MODELS = frozenset({"PAW5320"})
DATA_FEEDER_AUTH = f"{DOMAIN}_feeder_auth"

_TIMEOUT = aiohttp.ClientTimeout(total=20)
# Stop using an access token a minute before the cloud says it expires.
_ACCESS_MARGIN = 60.0
_DEFAULT_LIFETIME = 300.0
_MAX_LIFETIME = 3600.0
# After the token endpoint could not be reached, wait before spending the
# refresh token again; HA setup retries and polls reuse this back-off.
_REFRESH_RETRY_AFTER = 60.0
# A 401 on a token younger than this is not an expiry; refreshing again would
# only rotate the single-use refresh token without fixing anything.
_FORCED_REFRESH_MIN_AGE = 300.0


class FeederError(Exception):
    """A feeder request failed or its response could not be verified."""


class FeederHTTPError(FeederError):
    """Unexpected HTTP status. Only the numeric status is retained."""

    def __init__(self, status: int) -> None:
        super().__init__("HomeID pet request was not confirmed")
        self.status = status


class FeederAuthRejected(FeederHTTPError):
    """The pet backend refused the access token (401/403)."""


@dataclass(frozen=True)
class FeederDevice:
    """A feeder confirmed in the HomeID pet registry.

    ``vendor_id`` is the appliance's externalDeviceId in the HomeID appliance
    list (the Tuya device ID). ``philips_id`` is the pet backend's own device
    ID and ``external_id`` the registry's optional externalId. The three are
    distinct fields and are never substituted for one another.
    """

    vendor_id: str
    philips_id: str
    home_id: str
    model: str
    name: str
    external_id: str | None
    time_zone: str | None


def identifier(value: object) -> str:
    """Accept a non-empty string ID that is safe as one quoted path segment."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in (".", "..")
        or len(value) > 200
    ):
        raise FeederError("Invalid identifier")
    return value


def list_items(data: Any) -> list[dict[str, Any]]:
    """Unwrap the pet backend's ``{"item": [...]}`` list envelope."""
    if isinstance(data, dict):
        data = data.get("item")
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise FeederError("Unsupported HomeID list response")
    return data


def _check_path(path: str) -> None:
    if not path.startswith("/api/homes") or any(c in path for c in "?#\\"):
        raise FeederError("Unsupported HomeID request")


async def pet_request(
    session: aiohttp.ClientSession,
    access_token: str,
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
) -> Any:
    """Send one read. Anything but GET is refused before it is sent."""
    if method != "GET":
        raise FeederError("Unsupported HomeID request")
    _check_path(path)
    try:
        async with session.get(
            f"{PET_API_URL}{path}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_TIMEOUT,
            allow_redirects=False,
        ) as response:
            status = response.status
            if status in (401, 403):
                raise FeederAuthRejected(status)
            if status != 200:
                raise FeederHTTPError(status)
            return await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        # Not chained: aiohttp errors carry the request URL, with home IDs.
        raise FeederError("HomeID pet request was not confirmed") from None


async def find_feeder(
    get: Callable[[str], Awaitable[Any]], vendor_id: str
) -> FeederDevice | None:
    """Find the selected appliance in the pet registry. Read only.

    Only the registry's exact vendorId match counts; a name or ID prefix never
    does. ``None`` means this appliance is not a supported feeder.
    """
    identifier(vendor_id)
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for home in list_items(await get("/api/homes")):
        home_id = home.get("id")
        if isinstance(home_id, int) and not isinstance(home_id, bool):
            home_id = str(home_id)
        if not isinstance(home_id, str) or not home_id:
            continue
        path = f"/api/homes/{quote(identifier(home_id), safe='')}/devices"
        for device in list_items(await get(path)):
            if device.get("vendorId") == vendor_id:
                matches.append(({**home, "id": home_id}, device))
    if not matches:
        return None
    if len(matches) > 1:
        raise FeederError("HomeID feeder identity is ambiguous")
    home, device = matches[0]
    ctn = device.get("productCtn")
    model = ctn.upper().split("/")[0] if isinstance(ctn, str) else ""
    if model not in SUPPORTED_MODELS:
        return None
    external = device.get("externalId")
    zone = home.get("timeZone")
    name = device.get("name")
    return FeederDevice(
        vendor_id=vendor_id,
        philips_id=identifier(device.get("id")),
        home_id=home["id"],
        model=model,
        name=name[:80] if isinstance(name, str) and name.strip() else model,
        external_id=None if external is None else identifier(external),
        time_zone=zone if isinstance(zone, str) and zone else None,
    )


async def discover_feeder(
    session: aiohttp.ClientSession, access_token: str, vendor_id: str
) -> FeederDevice | None:
    """Config flow lookup with the login's own access token; no refresh."""

    async def get(path: str) -> Any:
        return await pet_request(session, access_token, "GET", path)

    return await find_feeder(get, vendor_id)


def _lifetime(tokens: dict[str, Any]) -> float:
    value = tokens.get("expires_in")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        return _DEFAULT_LIFETIME
    return min(float(value), _MAX_LIFETIME)


class FeederTokenOwner:
    """The only code that spends a feeder entry's single-use refresh token.

    HomeID retires a refresh token when it is used, so two holders of one
    token end in a reauth. One owner exists per config entry, kept in
    ``hass.data`` rather than on the coordinator: HA builds a new coordinator
    on every setup retry and reload, and those must share this lock, the
    cached access token, an in-flight refresh and the back-off. The refresh
    token itself is always read from, and rotated into, the config entry, so
    a reauth is picked up and nothing else keeps a stale copy.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[str] | None = None
        self._access = ""
        self._issued_for = ""
        self._expires = 0.0
        self._issued_at = -math.inf
        self._retry_after = 0.0
        self._retry_for = ""
        self._rejected = ""

    @classmethod
    def for_entry(cls, hass: HomeAssistant, entry_id: str) -> FeederTokenOwner:
        owners: dict[str, FeederTokenOwner] = hass.data.setdefault(DATA_FEEDER_AUTH, {})
        owner = owners.get(entry_id)
        if owner is None:
            owner = owners[entry_id] = cls(hass, entry_id)
        return owner

    @staticmethod
    def discard(hass: HomeAssistant, entry_id: str) -> None:
        hass.data.get(DATA_FEEDER_AUTH, {}).pop(entry_id, None)

    def _refresh_token(self) -> str:
        entry = self._hass.config_entries.async_get_entry(self._entry_id)
        token = entry.data.get(CONF_CLOUD_REFRESH_TOKEN) if entry else None
        if not isinstance(token, str) or not token:
            raise CloudAuthError("No HomeID refresh token")
        return token

    async def access_token(self) -> str:
        """Return a cached access token, or refresh once for every caller."""
        async with self._lock:
            task = self._task
            if task is None or task.done():
                refresh = self._refresh_token()
                now = time.monotonic()
                if self._access and self._issued_for == refresh and now < self._expires:
                    return self._access
                if refresh == self._rejected:
                    raise CloudAuthError("HomeID refresh token was rejected")
                # The back-off belongs to the token that failed; a reauth's
                # new token is tried at once.
                if refresh == self._retry_for and now < self._retry_after:
                    raise CloudConnectionError("HomeID token refresh is backing off")
                task = asyncio.get_running_loop().create_task(self._refresh(refresh))
                task.add_done_callback(_consume_exception)
                self._task = task
            # Shielded: a cancelled caller must not abandon a refresh whose
            # response rotates the token. The next caller awaits the same task
            # instead of spending the token a second time.
            return await asyncio.shield(task)

    async def _refresh(self, refresh: str) -> str:
        from .cloud_api import PhilipsCloudAPI

        entry = self._hass.config_entries.async_get_entry(self._entry_id)
        client = (
            entry.data.get(CONF_OAUTH_CLIENT, OAUTH_CLIENT_HOMEID)
            if entry
            else OAUTH_CLIENT_HOMEID
        )
        cloud = PhilipsCloudAPI()
        try:
            tokens = await cloud.refresh_tokens(refresh, client)
        # Re-raised with fixed messages and no chain: the originals carry
        # transport errors and the server's error_description.
        except CloudConnectionError:
            self._retry_after = time.monotonic() + _REFRESH_RETRY_AFTER
            self._retry_for = refresh
            raise CloudConnectionError("HomeID token refresh failed") from None
        except CloudAuthError:
            self._rejected = refresh
            raise CloudAuthError("HomeID refresh token was rejected") from None
        finally:
            await cloud.close()

        rotated = tokens.get("refresh_token")
        if not isinstance(rotated, str) or not rotated:
            rotated = refresh
        entry = self._hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.data.get(CONF_CLOUD_REFRESH_TOKEN) != refresh:
            # Removed, or a reauth stored a newer login meanwhile. That login
            # wins: never overwrite it with this older chain's rotation.
            raise CloudConnectionError("HomeID login changed during refresh")
        if rotated != refresh:
            # Persist before anything can use the new access token.
            self._hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_CLOUD_REFRESH_TOKEN: rotated}
            )
        access = tokens.get("access_token")
        if not isinstance(access, str) or not access:
            raise CloudConnectionError("HomeID returned no access token")
        now = time.monotonic()
        self._access = access
        self._issued_for = rotated
        self._issued_at = now
        self._expires = now + max(0.0, _lifetime(tokens) - _ACCESS_MARGIN)
        return access

    def invalidate(self, access: str) -> bool:
        """Forget an access token the pet backend refused.

        Returns whether one more refresh is reasonable. A token that was just
        issued is not expired, so refusing it again needs no new rotation.
        """
        if access != self._access:
            return True  # already replaced by a newer token
        if time.monotonic() - self._issued_at < _FORCED_REFRESH_MIN_AGE:
            return False
        self._access = ""
        self._expires = 0.0
        return True


def _consume_exception(task: asyncio.Task[str]) -> None:
    if not task.cancelled():
        task.exception()


class FeederClient:
    """Authenticated pet backend requests for one feeder entry."""

    def __init__(self, session: aiohttp.ClientSession, owner: FeederTokenOwner):
        self._session = session
        self._owner = owner

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        """A read retries once if the pet backend refused an older token."""
        if method != "GET":
            # Refused before any token work: a write must not even spend
            # the single-use refresh token.
            raise FeederError("Unsupported HomeID request")
        token = await self._owner.access_token()
        try:
            return await pet_request(self._session, token, method, path, params=params)
        except FeederAuthRejected:
            if not self._owner.invalidate(token):
                raise
        token = await self._owner.access_token()
        return await pet_request(self._session, token, method, path, params=params)

    async def get(self, path: str) -> Any:
        return await self.request("GET", path)

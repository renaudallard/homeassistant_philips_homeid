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
"""Feed now for HomeID feeders, through the app's Tuya session.

The HomeID app clamps a quick feed to 1 to 20 portions and publishes it as
one data point: "201" on a feeder with camera such as the PAW5320. A feed is
sent once per press and never again: a reply that cannot be read may still
come from a feed that reached the feeder. A session Tuya no longer accepts is
dropped, so the next press logs in afresh.
"""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .feeder_api import FeederTokenOwner
from .tuya_api import SESSION_LOST, TuyaSession, TuyaSessionLost

# Feed data point per model, from SingleContainerDispenseSerializer.
FEED_DPS = {"PAW5320": "201"}
MIN_PORTIONS = 1
MAX_PORTIONS = 20
# TuyaLoginCredentialsProviderImpl's dial code when the app has no space.
_DEFAULT_DIAL_CODE = "31"


class FeederFeed:
    """Feed now for one feeder entry. One feed or login at a time."""

    def __init__(
        self, hass: HomeAssistant, owner: FeederTokenOwner, device_id: str, model: str
    ) -> None:
        self._hass = hass
        self._owner = owner
        self._device_id = device_id
        self._dp = FEED_DPS[model]
        self._session = TuyaSession(async_get_clientsession(hass))
        self._lock = asyncio.Lock()
        self._dial_code = ""
        self.portions = MIN_PORTIONS

    @property
    def logged_in(self) -> bool:
        return self._session.logged_in

    async def feed(self, portions: int) -> None:
        if not MIN_PORTIONS <= portions <= MAX_PORTIONS:
            raise ValueError("portions out of range")
        dps = {self._dp: portions}
        async with self._lock:
            if not self._session.logged_in:
                await self._login()
            try:
                await self._session.publish(self._device_id, dps)
            except TuyaSessionLost as err:
                # Tuya said the session is gone, so drop it. An unreadable
                # reply also lands here and Tuya may have fed: drop the
                # session then only if a harmless read fails too, since
                # petsseries reports a daily login cap.
                if err.code == SESSION_LOST or not await self._session.alive():
                    self._session.forget()
                raise

    async def _login(self) -> None:
        dial_code = self._dial_code or await self._lookup_dial_code()
        await self._session.login(await self._owner.id_token(), dial_code)

    async def _lookup_dial_code(self) -> str:
        """The dial code of HA's country, from the spaces the app chooses from.

        Kept once the tenant answered; the app's own fallback stands in until
        then, or for a country HomeID has no space for.
        """
        from .cloud_api import PhilipsCloudAPI

        country = self._hass.config.country
        if not country:
            return _DEFAULT_DIAL_CODE
        cloud = PhilipsCloudAPI()
        try:
            tenant = await cloud.homeid_discovery()
        finally:
            await cloud.close()
        spaces = tenant.get("spaces")
        if not isinstance(spaces, list) or not spaces:
            return _DEFAULT_DIAL_CODE
        self._dial_code = _DEFAULT_DIAL_CODE
        for space in spaces:
            if isinstance(space, dict) and space.get("countryCode") == country:
                dial = space.get("dialCode")
                if isinstance(dial, str) and dial.isdigit() and len(dial) <= 4:
                    self._dial_code = dial
                    break
        return self._dial_code

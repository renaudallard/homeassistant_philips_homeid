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
"""The app's Tuya session for HomeID feeders: feed now and data points.

The HomeID app clamps a quick feed to 1 to 20 portions and publishes it as
one data point: "201" on a feeder with camera such as the PAW5320. Data
points are sent once per change and never again: a reply that cannot be read
may still come from a change that reached the feeder. A session Tuya no
longer accepts is dropped, so the next request logs in afresh.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .feeder_api import FeederTokenOwner
from .tuya_api import SESSION_LOST, TuyaError, TuyaSession, TuyaSessionLost

# Feed data point per model, from SingleContainerDispenseSerializer.
FEED_DPS = {"PAW5320": "201"}
MIN_PORTIONS = 1
MAX_PORTIONS = 20
# TuyaLoginCredentialsProviderImpl's dial code when the app has no space.
_DEFAULT_DIAL_CODE = "31"
# Settings data points of feeders with camera, from TuyaDpsBridgeImpl.
MOTION_DP = "106"
CONTRAST_DP = "108"
VOLUME_DP = "231"
# MotionSensitivity and NightVisionMode dpValue; high contrast is AUTO.
MOTION_LEVELS = {"0": "low", "1": "medium", "2": "high"}
CONTRAST_ON = {"0": True, "1": False}
# WithCameraAndEssentialsVolumeConverter: the app shows 1 to 10 and sends
# ten times that, kept within 10 to 100.
MIN_VOLUME = 1
MAX_VOLUME = 10


@dataclass(frozen=True)
class DpSettings:
    """The settings data points as the app reads them; None when unknown."""

    motion: str | None
    high_contrast: bool | None
    volume: int | None


def _dp_text(value: Any) -> str | None:
    # The app compares the value's toString(), so a number matches too.
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _dp_int(value: Any) -> int | None:
    # As the app's Number.intValue(): truncated, then cut to 32 bits.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        value = math.trunc(value)
    return (value + (1 << 31)) % (1 << 32) - (1 << 31)


def read_settings(dps: dict[str, Any]) -> DpSettings:
    text = _dp_text(dps.get(MOTION_DP))
    contrast = _dp_text(dps.get(CONTRAST_DP))
    raw = _dp_int(dps.get(VOLUME_DP))
    volume = None
    if raw is not None and raw > 0:
        # Kotlin's roundToInt rounds half up.
        volume = min(max(math.floor(raw / 10 + 0.5), MIN_VOLUME), MAX_VOLUME)
    return DpSettings(
        motion=MOTION_LEVELS.get(text) if text is not None else None,
        high_contrast=CONTRAST_ON.get(contrast) if contrast is not None else None,
        volume=volume,
    )


def volume_dp(volume: int) -> int:
    return min(max(volume * 10, 10), 100)


# petsseries reports a daily login cap: a poll logs in at most this often, so
# a login Tuya refuses is not tried again on every poll. A press still logs in.
_POLL_LOGIN_EVERY = 900.0
DATA_FEEDER_FEED = f"{DOMAIN}_feeder_feed"


class FeederFeed:
    """The Tuya session of one feeder entry. One request or login at a time.

    Kept in ``hass.data`` like the entry's token owner, since HA builds a new
    coordinator on every reload: a reload neither logs in again nor ends the
    poll's login wait. A reauth or a removal discards it.
    """

    def __init__(
        self, hass: HomeAssistant, owner: FeederTokenOwner, device_id: str, model: str
    ) -> None:
        self._hass = hass
        self._owner = owner
        self._device_id = device_id
        self._dp = FEED_DPS[model]
        self._session = TuyaSession(async_get_clientsession(hass))
        self._lock = asyncio.Lock()
        # The dial code looked up, and the HA country it is for.
        self._dial_code = ""
        self._dial_country = ""
        self._poll_login_at = 0.0
        self.portions = MIN_PORTIONS

    @classmethod
    def for_entry(
        cls,
        hass: HomeAssistant,
        entry_id: str,
        owner: FeederTokenOwner,
        device_id: str,
        model: str,
    ) -> FeederFeed:
        feeds: dict[str, FeederFeed] = hass.data.setdefault(DATA_FEEDER_FEED, {})
        feed = feeds.get(entry_id)
        if feed is None:
            feed = feeds[entry_id] = cls(hass, owner, device_id, model)
        return feed

    @staticmethod
    def discard(hass: HomeAssistant, entry_id: str) -> None:
        hass.data.get(DATA_FEEDER_FEED, {}).pop(entry_id, None)

    @property
    def logged_in(self) -> bool:
        return self._session.logged_in

    async def feed(self, portions: int) -> None:
        if not MIN_PORTIONS <= portions <= MAX_PORTIONS:
            raise ValueError("portions out of range")
        await self.publish({self._dp: portions})

    async def publish(self, dps: dict[str, int | str]) -> None:
        async with self._lock:
            if not self._session.logged_in:
                await self._login()
            try:
                await self._session.publish(self._device_id, dps)
            except TuyaSessionLost as err:
                await self._lost(err)
                raise

    async def read(self, home: int) -> dict[str, Any]:
        """The feeder's data points, for the poll."""
        async with self._lock:
            if not self._session.logged_in:
                now = time.monotonic()
                if now < self._poll_login_at:
                    raise TuyaError("Tuya login postponed")
                self._poll_login_at = now + _POLL_LOGIN_EVERY
                await self._login()
            try:
                return await self._session.device_dps(home, self._device_id)
            except TuyaSessionLost as err:
                await self._lost(err)
                raise

    async def _lost(self, err: TuyaSessionLost) -> None:
        # Tuya said the session is gone, so drop it. An unreadable reply also
        # lands here and a publish may have reached the feeder: drop the
        # session then only if a harmless read fails too, since petsseries
        # reports a daily login cap.
        if err.code in SESSION_LOST or not await self._session.alive():
            self._session.forget()

    async def _login(self) -> None:
        dial_code = await self._lookup_dial_code()
        await self._session.login(await self._owner.id_token(), dial_code)

    async def _lookup_dial_code(self) -> str:
        """The dial code of HA's country, from the spaces the app chooses from.

        Kept for that country once the tenant answered, since the session
        outlives reloads that may follow a change of HA's country; the app's
        own fallback stands in until then, or for a country HomeID has no
        space for.
        """
        from .cloud_api import PhilipsCloudAPI

        country = self._hass.config.country
        if not country:
            return _DEFAULT_DIAL_CODE
        if self._dial_code and self._dial_country == country:
            return self._dial_code
        cloud = PhilipsCloudAPI()
        try:
            tenant = await cloud.homeid_discovery()
        finally:
            await cloud.close()
        spaces = tenant.get("spaces")
        if not isinstance(spaces, list) or not spaces:
            return _DEFAULT_DIAL_CODE
        self._dial_code = _DEFAULT_DIAL_CODE
        self._dial_country = country
        for space in spaces:
            if isinstance(space, dict) and space.get("countryCode") == country:
                dial = space.get("dialCode")
                if isinstance(dial, str) and dial.isdigit() and len(dial) <= 4:
                    self._dial_code = dial
                    break
        return self._dial_code

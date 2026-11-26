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
"""PAW5320 feeder entries: coordinator, entities and setup.

Settings are read only. Meals are planned, changed and removed through the
pet REST backend as the app does (feeder_schedule), and feed now is sent
through Tuya by feeder_feed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .cloud_auth import CloudAuthError, CloudConnectionError
from .const import CONF_CPP_ID, CONF_DEVICE_ID, CONF_MODEL, DOMAIN
from .feeder_api import (
    FeederClient,
    FeederDevice,
    FeederError,
    FeederHTTPError,
    FeederTokenOwner,
    find_feeder,
)
from .feeder_feed import FEED_DPS, MAX_PORTIONS, MIN_PORTIONS, FeederFeed
from .feeder_meals import (
    DesiccantTimer,
    FeedingEvent,
    Meal,
    MealManager,
    MealsError,
)
from .tuya_api import TuyaError

_LOGGER = logging.getLogger(__name__)

FEEDER_PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]
UPDATE_INTERVAL = timedelta(minutes=2)
LOOKBACK = timedelta(days=7)
# The registry rarely changes; re-resolve IDs and the home time zone this often.
_IDENTITY_TTL = 900.0
FEEDER_ERRORS = (FeederError, CloudConnectionError)
_T = TypeVar("_T")


@dataclass(frozen=True)
class FeederData:
    """One poll. Each optional part has its own availability."""

    meals: list[Meal] = field(default_factory=list)
    meals_ok: bool = False
    history: list[FeedingEvent] = field(default_factory=list)
    history_ok: bool = False
    status: dict[str, datetime] = field(default_factory=dict)
    status_ok: bool = False
    desiccant: DesiccantTimer | None = None
    time_zone: str | None = None
    time_zone_from_home: bool = False


class FeederCoordinator(DataUpdateCoordinator[FeederData]):
    """Read schedules, history, events and settings for one feeder."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="HomeID feeder",
            config_entry=entry,
            update_interval=UPDATE_INTERVAL,
        )
        owner = FeederTokenOwner.for_entry(hass, entry.entry_id)
        self.client = FeederClient(async_get_clientsession(hass), owner)
        self.vendor_id: str = entry.data[CONF_DEVICE_ID]
        model = entry.data.get(CONF_MODEL)
        self.feed: FeederFeed | None = (
            FeederFeed(hass, owner, self.vendor_id, model)
            if model in FEED_DPS
            else None
        )
        self._manager: MealManager | None = None
        self._device: FeederDevice | None = None
        self._manager_until = 0.0
        self._logged: dict[str, str] = {}
        # Counts the meal reads after a change and keeps the last, which a
        # poll running meanwhile takes as newer than its own.
        self._meal_reads = 0
        self._read_back: tuple[list[Meal], bool] = ([], False)
        # Held by each change from its write to its read back.
        self.changes = asyncio.Lock()

    async def meal_manager(self, *, force_refresh: bool = False) -> MealManager:
        """Resolve the selected appliance to its home and pet backend IDs."""
        if (
            self._manager is not None
            and not force_refresh
            and time.monotonic() < self._manager_until
        ):
            return self._manager
        device = await find_feeder(self.client.get, self.vendor_id)
        if device is None:
            raise FeederError("HomeID feeder was not found")
        # A new manager drops the events it keeps for ended days, so the
        # same one stays while the feeder resolves the same.
        if self._manager is None or device != self._device:
            self._manager = MealManager.for_device(
                self.client.request, device, self.client.write
            )
            self._device = device
        self._manager_until = time.monotonic() + _IDENTITY_TTL
        return self._manager

    async def _async_update_data(self) -> FeederData:
        try:
            manager = await self.meal_manager()
        except CloudConnectionError as err:
            raise UpdateFailed("HomeID cloud unavailable") from err
        except CloudAuthError as err:
            raise ConfigEntryAuthFailed("HomeID login expired") from err
        except FeederError as err:
            self._log("registry", err)
            raise UpdateFailed("HomeID feeder registry unavailable") from err
        self._logged.pop("registry", None)
        now = dt_util.utcnow()
        reads = self._meal_reads
        meals = await self._optional("schedule", manager.list_meals)
        history = await self._optional(
            "history", lambda: manager.history(now - LOOKBACK, now)
        )
        status = await self._optional(
            "status", lambda: manager.latest_status(now - LOOKBACK, now)
        )
        desiccant = await self._optional("desiccant", manager.desiccant)
        meals_ok = meals is not None
        if reads != self._meal_reads:
            # A schedule change was read while this poll ran: its meals are
            # newer than the ones read above.
            meals, meals_ok = self._read_back
        return FeederData(
            meals=meals or [],
            meals_ok=meals_ok,
            history=history or [],
            history_ok=history is not None,
            status=status or {},
            status_ok=status is not None,
            desiccant=desiccant,
            # The app shows meal times in the phone's zone, which onboarding
            # also stores on the home; HA's zone stands in when HomeID sends
            # none.
            time_zone=manager.time_zone or self.hass.config.time_zone,
            time_zone_from_home=bool(manager.time_zone),
        )

    async def refresh_meals(self) -> None:
        """Read the meals again after a schedule change.

        Never raises: an expired login is left to the next poll, which
        starts the reauth. After a failed poll nothing is stored, since
        storing would make the entities of the other parts available again
        on old data.
        """
        try:
            manager = await self.meal_manager()
            meals = await manager.list_meals()
        except FEEDER_ERRORS as err:
            self._log("schedule", err)
            self.forget_meals()
            return
        except CloudAuthError:
            return
        self._logged.pop("schedule", None)
        self._keep_read_back(meals, True)

    def forget_meals(self) -> None:
        """Leave the meals unknown until the next poll.

        For a change whose outcome is not known: the meals held may predate
        it, and an edit built on them could undo it.
        """
        self._keep_read_back(self.data.meals if self.data else [], False)

    def _keep_read_back(self, meals: list[Meal], meals_ok: bool) -> None:
        # Kept and counted also after a failed poll, for the poll after it,
        # which may have read the meals before the change.
        self._meal_reads += 1
        self._read_back = (meals, meals_ok)
        if self.data is not None and self.last_update_success:
            self.async_set_updated_data(
                replace(self.data, meals=meals, meals_ok=meals_ok)
            )

    async def _optional(
        self, stage: str, call: Callable[[], Awaitable[_T]]
    ) -> _T | None:
        """One part failing leaves the others, and the entry, available."""
        try:
            result = await call()
        # CloudConnectionError subclasses CloudAuthError: a transient refresh
        # failure must stay a failed part, not become a reauth.
        except FEEDER_ERRORS as err:
            self._log(stage, err)
            return None
        except CloudAuthError as err:
            raise ConfigEntryAuthFailed("HomeID login expired") from err
        self._logged.pop(stage, None)
        return result

    def _log(self, stage: str, error: Exception) -> None:
        """Log a fixed reason code once per change; never cloud data."""
        reasons = {
            "Invalid identifier": "identifier",
            "Unsupported HomeID list response": "list_shape",
            "Unsupported desiccant timer response": "desiccant_shape",
            "Invalid feeding event text": "event_text",
            "Invalid feeding event amount": "event_amount",
            "Conflicting feeding event identity": "event_duplicate",
            "An explicit home IANA time zone is required": "home_time_zone",
            "HomeID feeder was not found": "not_found",
            "HomeID feeder identity is ambiguous": "ambiguous",
        }
        reason = "connection"
        if isinstance(error, FeederHTTPError) and 100 <= error.status <= 599:
            reason = f"http_{error.status}"
        elif isinstance(error, MealsError):
            reason = reasons.get(str(error), "schema")
        elif isinstance(error, FeederError):
            reason = reasons.get(str(error), "response")
        if self._logged.get(stage) != reason:
            _LOGGER.warning("HomeID feeder %s unavailable (%s)", stage, reason)
            self._logged[stage] = reason


async def async_setup_feeder_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a feeder entry."""
    coordinator = FeederCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, FEEDER_PLATFORMS)
    return True


async def async_unload_feeder_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a feeder entry; its token owner stays for the next setup."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, FEEDER_PLATFORMS
    )
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator is not None:
            await coordinator.async_shutdown()
    return unload_ok


def feeder_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Allowlisted state only: no IDs, tokens, names or cloud payloads."""
    coordinator: FeederCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    data = coordinator.data if coordinator else None
    return {
        "backend": "homeid_pet_rest",
        "model": entry.data.get(CONF_MODEL),
        "loaded": coordinator is not None,
        "last_update_success": coordinator.last_update_success if coordinator else None,
        "schedule_available": data.meals_ok if data else None,
        "meal_count": len(data.meals) if data else None,
        "history_available": data.history_ok if data else None,
        "history_count": len(data.history) if data else None,
        "status_available": data.status_ok if data else None,
        "status_types": sorted(data.status) if data else None,
        "desiccant_available": data.desiccant is not None if data else None,
        "home_time_zone_known": data.time_zone_from_home if data else None,
        "feed_supported": bool(coordinator and coordinator.feed),
        "tuya_logged_in": bool(
            coordinator and coordinator.feed and coordinator.feed.logged_in
        ),
    }


class FeederEntity(CoordinatorEntity[FeederCoordinator]):
    """Feeder entities share one device, keyed like other cloud entries."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FeederCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        identity = entry.data[CONF_CPP_ID]
        self._attr_translation_key = key
        self._attr_unique_id = f"{identity}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity)},
            manufacturer="Philips",
            model=entry.data[CONF_MODEL],
            name=entry.title,
        )


@dataclass(frozen=True)
class _TimestampSpec:
    key: str
    available: Callable[[FeederData], bool]
    value: Callable[[FeederData], datetime | date | None]
    icon: str
    diagnostic: bool = False
    device_class: SensorDeviceClass = SensorDeviceClass.TIMESTAMP


def _last_feeding(data: FeederData) -> datetime | None:
    return max((item.time for item in data.history), default=None)


def _desiccant_replace(data: FeederData) -> date | None:
    return data.desiccant.replace_at if data.desiccant else None


_TIMESTAMPS = (
    _TimestampSpec(
        "feeder_last_feeding",
        lambda data: data.history_ok,
        _last_feeding,
        "mdi:food-drumstick",
    ),
    _TimestampSpec(
        "feeder_last_food_low",
        lambda data: data.status_ok,
        lambda data: data.status.get("food_level_low"),
        "mdi:bowl-mix-outline",
    ),
    _TimestampSpec(
        "feeder_last_outlet_stuck",
        lambda data: data.status_ok,
        lambda data: data.status.get("food_outlet_stuck"),
        "mdi:alert-circle-outline",
    ),
    _TimestampSpec(
        "feeder_desiccant_replacement",
        lambda data: data.desiccant is not None,
        _desiccant_replace,
        "mdi:water-off-outline",
        diagnostic=True,
        device_class=SensorDeviceClass.DATE,
    ),
)


class FeederTimestampSensor(FeederEntity, SensorEntity):
    """The latest reported time; unknown when none was reported in the window."""

    def __init__(self, coordinator: FeederCoordinator, spec: _TimestampSpec) -> None:
        super().__init__(coordinator, spec.key)
        self._spec = spec
        self._attr_device_class = spec.device_class
        self._attr_icon = spec.icon
        if spec.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and self._spec.available(data)

    @property
    def native_value(self) -> datetime | date | None:
        data = self.coordinator.data
        return self._spec.value(data) if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if data is None or not self.available:
            return None
        if self._spec.key == "feeder_desiccant_replacement":
            applied = data.desiccant.applied_at if data.desiccant else None
            return {"last_replaced": applied.isoformat() if applied else None}
        attributes: dict[str, Any] = {"lookback_days": LOOKBACK.days}
        if self._spec.key == "feeder_last_feeding":
            # mealAmount's unit is not established, so it is not called grams.
            attributes["recent_feedings"] = [
                {
                    "time": item.time.isoformat(),
                    "name": item.name,
                    "amount": item.amount,
                    "source": item.source,
                }
                for item in sorted(
                    data.history, key=lambda item: item.time, reverse=True
                )[:20]
            ]
        return attributes


class FeederConnectivitySensor(FeederEntity, BinarySensorEntity):
    """Whichever of device_online/device_offline HomeID reported last."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator, "feeder_connectivity")

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and data.status_ok

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        online = data.status.get("device_online")
        offline = data.status.get("device_offline")
        if online is None and offline is None:
            return None  # nothing reported in the window: unknown, not offline
        if offline is None:
            return True
        if online is None:
            return False
        return None if online == offline else online > offline

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if data is None or not self.available:
            return None
        return {
            "last_online": _iso(data.status.get("device_online")),
            "last_offline": _iso(data.status.get("device_offline")),
            "lookback_days": LOOKBACK.days,
        }


class FeederFeedButton(FeederEntity, ButtonEntity):
    """Dispense the portions set on the portions number, as the app's feed now."""

    _attr_icon = "mdi:food-drumstick"

    def __init__(self, coordinator: FeederCoordinator, feed: FeederFeed) -> None:
        super().__init__(coordinator, "feeder_feed")
        self._feed = feed

    async def async_press(self) -> None:
        try:
            await self._feed.feed(self._feed.portions)
        except TuyaError as err:
            _LOGGER.warning("HomeID feed now failed (%s)", err.code or "no code")
            raise HomeAssistantError("Feed now was not confirmed by Tuya") from err
        # CloudConnectionError subclasses CloudAuthError, so it comes first.
        except CloudConnectionError as err:
            raise HomeAssistantError("HomeID cloud unavailable") from err
        except CloudAuthError as err:
            raise HomeAssistantError("HomeID login expired") from err
        await self.coordinator.async_request_refresh()


class FeederPortionsNumber(FeederEntity, RestoreNumber):
    """Portions for the feed now button. Kept in HA, never sent on its own."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:counter"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = MIN_PORTIONS
    _attr_native_max_value = MAX_PORTIONS
    _attr_native_step = 1

    def __init__(self, coordinator: FeederCoordinator, feed: FeederFeed) -> None:
        super().__init__(coordinator, "feeder_portions")
        self._feed = feed

    @property
    def native_value(self) -> float:
        return self._feed.portions

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        value = last.native_value if last else None
        if value is not None and MIN_PORTIONS <= value <= MAX_PORTIONS:
            self._feed.portions = int(value)

    async def async_set_native_value(self, value: float) -> None:
        if not (value.is_integer() and MIN_PORTIONS <= value <= MAX_PORTIONS):
            raise HomeAssistantError(
                f"Choose {MIN_PORTIONS} to {MAX_PORTIONS} whole portions"
            )
        self._feed.portions = int(value)
        self.async_write_ha_state()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def async_setup_feeder_sensors(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(FeederTimestampSensor(coordinator, s) for s in _TIMESTAMPS)


async def async_setup_feeder_binary_sensors(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FeederConnectivitySensor(coordinator)])


async def async_setup_feeder_buttons(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.feed is not None:
        async_add_entities([FeederFeedButton(coordinator, coordinator.feed)])


async def async_setup_feeder_numbers(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.feed is not None:
        async_add_entities([FeederPortionsNumber(coordinator, coordinator.feed)])

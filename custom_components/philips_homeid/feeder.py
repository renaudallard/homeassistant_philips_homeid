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

Meals (feeder_schedule), the camera switch and the desiccant reset
(feeder_settings) are changed through the pet REST backend as the app does.
Feed now and the settings data points go through Tuya by feeder_feed.
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
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
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
from .feeder_feed import (
    FEED_DPS,
    MAX_PORTIONS,
    MIN_PORTIONS,
    DpSettings,
    FeederFeed,
    read_settings,
)
from .feeder_meals import (
    DesiccantTimer,
    FeedingEvent,
    Meal,
    MealManager,
    MealsError,
    StatusEvent,
    latest_status,
)
from .tuya_api import TuyaError

_LOGGER = logging.getLogger(__name__)

FEEDER_PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]
UPDATE_INTERVAL = timedelta(minutes=2)
LOOKBACK = timedelta(days=7)
# Supported models the app gives a camera switch; its list also has the
# PAW5321 and PAW7310.
CAMERA_MODELS = frozenset({"PAW5320"})
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
    status_events: list[StatusEvent] = field(default_factory=list)
    status_ok: bool = False
    desiccant: DesiccantTimer | None = None
    camera: bool | None = None
    camera_ok: bool = False
    settings: DpSettings | None = None
    time_zone: str | None = None
    time_zone_from_home: bool = False

    @property
    def status(self) -> dict[str, datetime]:
        """Latest time of each status event type."""
        return latest_status(self.status_events)


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
            FeederFeed.for_entry(hass, entry.entry_id, owner, self.vendor_id, model)
            if model in FEED_DPS
            else None
        )
        self.has_camera = model in CAMERA_MODELS
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
        # Settings Tuya confirmed while the running poll reads, by field.
        self._dp_changes: dict[str, Any] = {}

    @property
    def has_settings(self) -> bool:
        """Whether the settings data points apply, through the feed session."""
        return self.has_camera and self.feed is not None

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
        self._dp_changes = {}
        meals = await self._optional("schedule", manager.list_meals)
        history = await self._optional(
            "history", lambda: manager.history(now - LOOKBACK, now)
        )
        status = await self._optional(
            "status", lambda: manager.status_events(now - LOOKBACK, now)
        )
        desiccant = await self._optional("desiccant", manager.desiccant)

        async def read_camera() -> tuple[bool | None]:
            # Boxed, since None is a value here: HomeID holds none.
            return (await manager.camera(),)

        camera: tuple[bool | None] | None = None
        if self.has_camera:
            camera = await self._optional("camera", read_camera)
        settings: DpSettings | None = None
        if self.has_settings:
            settings = await self._optional("settings", self._read_settings)
        meals_ok = meals is not None
        if reads != self._meal_reads:
            # A schedule change was read while this poll ran: its meals are
            # newer than the ones read above.
            meals, meals_ok = self._read_back
        if self._dp_changes and settings is not None:
            # A setting changed while this poll ran is newer than its read;
            # the others are as read.
            settings = replace(settings, **self._dp_changes)
        return FeederData(
            meals=meals or [],
            meals_ok=meals_ok,
            history=history or [],
            history_ok=history is not None,
            status_events=status or [],
            status_ok=status is not None,
            desiccant=desiccant,
            camera=camera[0] if camera else None,
            camera_ok=camera is not None,
            settings=settings,
            time_zone=self.home_zone(manager),
            time_zone_from_home=bool(manager.time_zone),
        )

    def home_zone(self, manager: MealManager) -> str | None:
        """The zone of the home's meal times and days.

        The app uses the phone's zone, which onboarding also stores on the
        home; HA's zone stands in when HomeID sends none.
        """
        return manager.time_zone or self.hass.config.time_zone

    async def _read_settings(self) -> DpSettings:
        # The poll resolved the device first; the feed is there by has_settings.
        feed, device = self.feed, self._device
        if feed is None or device is None or device.tuya_home is None:
            raise FeederError("HomeID names no Tuya home")
        return read_settings(await feed.read(device.tuya_home))

    def settings_written(self, **changes: Any) -> None:
        """Show a change Tuya confirmed, as the app does, until the next poll.

        The cloud keeps the value the feeder reports back, so an immediate
        read could still give the old one.
        """
        data = self.data
        if data is None or data.settings is None or not self.last_update_success:
            return
        self._dp_changes.update(changes)
        self.async_set_updated_data(
            replace(data, settings=replace(data.settings, **changes))
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
            # The reauth is left to the next poll; the meals held may
            # predate the change all the same.
            self.forget_meals()
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
        except (*FEEDER_ERRORS, TuyaError) as err:
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
            "Unsupported camera setting response": "camera_shape",
            "Invalid feeding event text": "event_text",
            "Invalid feeding event amount": "event_amount",
            "Conflicting event identity": "event_duplicate",
            "An explicit home IANA time zone is required": "home_time_zone",
            "HomeID feeder was not found": "not_found",
            "HomeID feeder identity is ambiguous": "ambiguous",
            "HomeID names no Tuya home": "no_tuya_home",
            "Tuya login postponed": "tuya_login_wait",
            "Tuya home does not list the feeder": "tuya_not_listed",
            "Tuya reply was not readable": "tuya_reply",
        }
        reason = "connection"
        if isinstance(error, TuyaError):
            code = f"tuya_{error.code.lower()}" if error.code else "tuya"
            reason = reasons.get(str(error), code)
        elif isinstance(error, FeederHTTPError) and 100 <= error.status <= 599:
            reason = f"http_{error.status}"
        elif isinstance(error, MealsError):
            reason = reasons.get(str(error), "schema")
        elif isinstance(error, FeederError):
            reason = reasons.get(str(error), "response")
        if reason == "tuya_login_wait" and stage in self._logged:
            # The failed login that set the wait was logged already.
            return
        if self._logged.get(stage) != reason:
            _LOGGER.warning("HomeID feeder %s unavailable (%s)", stage, reason)
            self._logged[stage] = reason


async def pet_call(call: Awaitable[_T]) -> _T:
    """One pet REST call made for an action, its failures as HA errors."""
    try:
        return await call
    except FEEDER_ERRORS as err:  # before CloudAuthError, its base class
        raise HomeAssistantError("HomeID cloud unavailable") from err
    except CloudAuthError as err:
        raise HomeAssistantError("HomeID login expired") from err


async def apply_change(
    coordinator: FeederCoordinator,
    change: Callable[[MealManager], Awaitable[None]],
    refresh: Callable[[], Awaitable[None]],
    unconfirmed: str,
    conflict: str | None = None,
    cancelled: Callable[[], None] | None = None,
) -> None:
    """Send one change, then read again whatever happened.

    A write that was not confirmed may still have been applied, so the
    read follows it too. ``conflict`` is the message for a 409, and
    ``cancelled`` runs when the action is cancelled before it ends. Changes
    go one at a time, so each starts from what the one before it read back.
    """
    async with coordinator.changes:
        manager = await pet_call(coordinator.meal_manager())
        try:
            await _send(manager, change, refresh, unconfirmed, conflict)
        except asyncio.CancelledError:
            # Cancelled by a script stop or a restarted automation: the write
            # may have been applied and its read back is missing.
            if cancelled is not None:
                cancelled()
            raise


async def _send(
    manager: MealManager,
    change: Callable[[MealManager], Awaitable[None]],
    refresh: Callable[[], Awaitable[None]],
    unconfirmed: str,
    conflict: str | None,
) -> None:
    try:
        await change(manager)
    except MealsError as err:
        # Raised before anything is sent; its messages are fixed text.
        raise ServiceValidationError(str(err)) from err
    except FeederHTTPError as err:
        await refresh()
        if conflict is not None and err.status == 409:
            raise HomeAssistantError(conflict) from err
        raise HomeAssistantError(unconfirmed) from err
    except FEEDER_ERRORS as err:
        await refresh()
        raise HomeAssistantError(unconfirmed) from err
    except CloudAuthError as err:
        raise HomeAssistantError("HomeID login expired") from err
    await refresh()


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
        # As the switch: HomeID read and holds a camera setting.
        "camera_available": (
            data.camera_ok and data.camera is not None if data else None
        ),
        "settings_available": data.settings is not None if data else None,
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


async def tuya_change(change: Awaitable[None], what: str, unconfirmed: str) -> None:
    """Send one Tuya change. It is never sent again, even unconfirmed."""
    try:
        await change
    except TuyaError as err:
        _LOGGER.warning("HomeID %s failed (%s)", what, err.code or "no code")
        raise HomeAssistantError(unconfirmed) from err
    # CloudConnectionError subclasses CloudAuthError, so it comes first.
    except CloudConnectionError as err:
        raise HomeAssistantError("HomeID cloud unavailable") from err
    except CloudAuthError as err:
        raise HomeAssistantError("HomeID login expired") from err


class FeederFeedButton(FeederEntity, ButtonEntity):
    """Dispense the portions set on the portions number, as the app's feed now."""

    _attr_icon = "mdi:food-drumstick"

    def __init__(self, coordinator: FeederCoordinator, feed: FeederFeed) -> None:
        super().__init__(coordinator, "feeder_feed")
        self._feed = feed

    async def async_press(self) -> None:
        await tuya_change(
            self._feed.feed(self._feed.portions),
            "feed now",
            "Feed now was not confirmed by Tuya",
        )
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

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
"""PAW5320 feeder entries: coordinator, sensors and setup.

Read only: nothing here changes the feeder's schedule or settings, and the
transport refuses every method but GET.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
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
    FeederError,
    FeederHTTPError,
    FeederTokenOwner,
    find_feeder,
)
from .feeder_meals import (
    DesiccantTimer,
    EventOwnershipError,
    FeedingEvent,
    Meal,
    MealManager,
    MealsError,
)

_LOGGER = logging.getLogger(__name__)

FEEDER_PLATFORMS = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.SENSOR]
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
        self.client = FeederClient(
            async_get_clientsession(hass),
            FeederTokenOwner.for_entry(hass, entry.entry_id),
        )
        self.vendor_id: str = entry.data[CONF_DEVICE_ID]
        self._manager: MealManager | None = None
        self._manager_until = 0.0
        self._logged: dict[str, str] = {}

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
        self._manager = MealManager.for_device(self.client.request, device)
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
        meals = await self._optional("schedule", manager.list_meals)
        history = await self._optional(
            "history", lambda: manager.history(now - LOOKBACK, now)
        )
        status = await self._optional(
            "status", lambda: manager.latest_status(now - LOOKBACK, now)
        )
        desiccant = await self._optional("desiccant", manager.desiccant)
        return FeederData(
            meals=meals or [],
            meals_ok=meals is not None,
            history=history or [],
            history_ok=history is not None,
            status=status or {},
            status_ok=status is not None,
            desiccant=desiccant,
            time_zone=manager.time_zone,
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
            "Invalid feeding event timestamp": "timestamp",
            "Invalid feeding event text": "event_text",
            "Invalid feeding event amount": "event_amount",
            "Feeding event ownership could not be verified": "event_ownership",
            "Conflicting feeding event identity": "event_duplicate",
            "An explicit home IANA time zone is required": "home_time_zone",
            "HomeID feeder was not found": "not_found",
            "HomeID feeder identity is ambiguous": "ambiguous",
        }
        reason = "connection"
        if isinstance(error, EventOwnershipError) and all(
            type(flag) is bool for flag in error.flags
        ):
            reason = "event_ownership_" + "".join(
                "1" if flag else "0" for flag in error.flags
            )
        elif isinstance(error, FeederHTTPError) and 100 <= error.status <= 599:
            reason = f"http_{error.status}"
        elif isinstance(error, MealsError):
            reason = reasons.get(str(error), "schema")
        elif isinstance(error, FeederError):
            reason = reasons.get(str(error), "response")
        if self._logged.get(stage) != reason:
            _LOGGER.warning("HomeID feeder %s unavailable (%s)", stage, reason)
            self._logged[stage] = reason


async def async_setup_feeder_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a feeder entry. It only reads."""
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
        "home_time_zone_known": bool(data.time_zone) if data else None,
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

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
"""Data update coordinator for Philips HomeID."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACTIVE_SCAN_INTERVAL,
    CONF_ACTIVE_SCAN_INTERVAL,
    CONF_AUTOCOOK_CATALOG_FETCHED,
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_PLATFORM_REST_URL,
    CONF_RECIPE_CACHE,
    CONF_RECIPE_LANGUAGE,
    CONF_SCAN_INTERVAL,
    CONF_TENANT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FUSION_HEARTBEAT_INTERVAL,
)
from .local_api import (
    AIRFRYER_STATUS_COOKING,
    AIRFRYER_STATUS_IDLE,
    AIRFRYER_STATUS_MAINTAIN,
    AIRFRYER_STATUS_PARASETTING,
    AIRFRYER_STATUS_PAUSED,
    AIRFRYER_STATUS_PRECOOK,
    AIRFRYER_STATUS_SETTING,
    AIRFRYER_STATUS_STANDBY,
    AIRFRYER_STATUS_USER_ACTION,
    LocalDeviceInfo,
    LocalDeviceState,
    PhilipsLocalAPI,
)
from .mqtt_api import PhilipsMQTTClient

_LOGGER = logging.getLogger(__name__)


class PhilipsHomeIDCoordinator(DataUpdateCoordinator[LocalDeviceState | None]):
    """Coordinator for Philips HomeID devices using local API."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: PhilipsLocalAPI,
        device_info: LocalDeviceInfo,
        entry: ConfigEntry,
        mqtt_client: PhilipsMQTTClient | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self._is_fusion = mqtt_client is not None
        if self._is_fusion:
            scan_interval = FUSION_HEARTBEAT_INTERVAL
        else:
            scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{device_info.cpp_id or device_info.ip_address}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        self.mqtt_client = mqtt_client
        self.device_info = device_info
        self._state: LocalDeviceState | None = None
        self._last_update_time: float = 0.0  # Timestamp of last successful poll
        self._seen_properties: set[str] = set()  # Track all properties ever seen
        self._new_properties_callbacks: list[
            Callable[[list[tuple[str, str | None]]], None]
        ] = []  # Callbacks for new properties
        self._preheat_enabled: bool = False  # Preheat flag for next cooking start
        self._keep_warm_time: int = 3600  # Keep warm duration in seconds (default 1h)
        self._keep_warm_temp: int = 65  # Keep warm temperature in Celsius
        self._rita_brew_profile_id: int = 0  # Rita espresso: profile to brew (0-7)
        self._rita_brew_recipe_id: int = 0  # Rita espresso: recipe id to brew
        self._autocook_selected_uuid: str = ""  # Venus airfryer: UUID to send next
        self._consecutive_failures: int = 0  # Track consecutive poll failures
        self._max_failures: int = 3  # Failures before marking device offline
        # Event signaled when first MQTT data with properties arrives.
        # Used to delay entity setup until NCP port data is available.
        self._initial_data_event: asyncio.Event = asyncio.Event()
        self._ncp_data_timestamp: float = 0.0  # monotonic time of last NCP data
        # Recipe name cache: recipe_id -> name, persisted in config entry.
        # Invalidate if HA language changed since cache was built.
        cached_lang = entry.data.get(CONF_RECIPE_LANGUAGE, "")
        if cached_lang and cached_lang != hass.config.language:
            self._recipe_cache: dict[str, str] = {}
        else:
            self._recipe_cache = dict(entry.data.get(CONF_RECIPE_CACHE, {}))
        self._pending_recipe_fetch: str | None = None
        self._failed_recipe_ids: set[str] = set()  # IDs that failed cloud lookup
        # Lock to prevent simultaneous token refreshes (recipe fetch vs MQTT reconnect)
        self._token_lock: asyncio.Lock = asyncio.Lock()
        self._catalog_fetch_running: bool = False
        # Snapshot of entry.options used by the update listener to skip
        # reloads triggered by data-only entry updates (recipe cache, etc.).
        self.previous_options: dict[str, Any] = dict(entry.options)

    def _is_airfryer_active(self, state: LocalDeviceState) -> bool:
        """Check if airfryer is actively cooking."""
        airfryer = state.properties.get("airfryer")
        if not airfryer or not isinstance(airfryer, dict):
            return False
        status = airfryer.get("status", "")
        return status in (
            AIRFRYER_STATUS_COOKING,
            AIRFRYER_STATUS_PAUSED,
            AIRFRYER_STATUS_SETTING,
            AIRFRYER_STATUS_PRECOOK,
            AIRFRYER_STATUS_PARASETTING,
            AIRFRYER_STATUS_MAINTAIN,
            AIRFRYER_STATUS_USER_ACTION,
        )

    def _update_polling_interval(self, state: LocalDeviceState | None) -> None:
        """Adjust polling interval based on device state."""
        if self._is_fusion:
            return  # FUSION uses MQTT push; heartbeat interval is fixed
        options = self.config_entry.options
        if state and self._is_airfryer_active(state):
            interval = options.get(CONF_ACTIVE_SCAN_INTERVAL, ACTIVE_SCAN_INTERVAL)
        else:
            interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        new_interval = timedelta(seconds=interval)

        if self.update_interval != new_interval:
            _LOGGER.debug(
                "Changing polling interval from %s to %s",
                self.update_interval,
                new_interval,
            )
            self.update_interval = new_interval

    def update_from_mqtt(self, state: LocalDeviceState) -> None:
        """Receive a push state update from MQTT.

        Called from the MQTT client's message callback via
        loop.call_soon_threadsafe.
        """
        new_properties = self._check_for_new_properties(state)
        self._state = state
        self._last_update_time = time.monotonic()
        self._consecutive_failures = 0
        self._update_polling_interval(state)
        if new_properties:
            self._notify_new_properties(new_properties)
        # Signal that initial MQTT data is available for entity setup.
        # Only signal when port data (nested dicts) is present, not just
        # shadow metadata like productState.
        if any(isinstance(v, dict) for v in state.properties.values()):
            self._ncp_data_timestamp = time.monotonic()
            if not self._initial_data_event.is_set():
                self._initial_data_event.set()
        self._inject_recipe_name()
        self.async_set_updated_data(state)

    async def _async_update_data(self) -> LocalDeviceState | None:
        """Fetch data from device."""
        if self._is_fusion and self.mqtt_client:
            return await self._async_update_data_fusion()
        return await self._async_update_data_local()

    async def _async_update_data_fusion(self) -> LocalDeviceState | None:
        """Heartbeat poll for FUSION devices via MQTT."""
        assert self.mqtt_client is not None
        try:
            # Proactively refresh token before the 1-hour expiry
            _LOGGER.debug(
                "FUSION heartbeat: connected=%s, connect_time=%.0f",
                self.mqtt_client.connected,
                self.mqtt_client._connect_time,
            )
            if self.mqtt_client.needs_token_refresh():
                await self._proactive_mqtt_refresh()
            if self.mqtt_client.connected:
                await self.hass.async_add_executor_job(self.mqtt_client.request_state)
                await self.hass.async_add_executor_job(
                    self.mqtt_client.refresh_port_data
                )
            else:
                _LOGGER.warning("MQTT not connected for heartbeat")
        except Exception as err:
            _LOGGER.debug("MQTT heartbeat error: %s", err)
        # Return cached state (real updates come via MQTT push)
        return self._state

    async def _async_update_data_local(self) -> LocalDeviceState | None:
        """Fetch data from device via local API."""
        try:
            _LOGGER.debug("Polling device at %s", self.device_info.ip_address)

            # Get full state from device
            state = await self.api.get_full_state(self.device_info)

            if state:
                # Success: reset failure counter
                self._consecutive_failures = 0

                # Check for new properties before updating state
                new_properties = self._check_for_new_properties(state)

                self._state = state
                self._last_update_time = time.monotonic()
                _LOGGER.debug(
                    "Device %s: power=%s, properties=%s",
                    self.device_info.ip_address,
                    state.power_on,
                    list(state.properties.keys()),
                )
                # Adjust polling interval based on cooking state
                self._update_polling_interval(state)

                # Notify about new properties (after state is updated)
                if new_properties:
                    self._notify_new_properties(new_properties)

                self._inject_recipe_name()
                return state

            # No response: track consecutive failures. Drop the polling
            # interval back to idle so we don't keep hammering the device at
            # the active rate after cooking is interrupted by network loss.
            self._consecutive_failures += 1
            self._update_polling_interval(None)
            if self._consecutive_failures >= self._max_failures:
                raise UpdateFailed(
                    f"No response from device at {self.device_info.ip_address} "
                    f"after {self._consecutive_failures} attempts"
                )
            _LOGGER.warning(
                "No response from device at %s (attempt %d/%d)",
                self.device_info.ip_address,
                self._consecutive_failures,
                self._max_failures,
            )
            return self._state

        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.exception("Error fetching data from device")
            raise UpdateFailed(f"Error communicating with device: {err}") from err

    async def _mqtt_command(self, port: str, props: dict[str, Any]) -> bool:
        """Send a command via MQTT for FUSION devices."""
        if not self.mqtt_client:
            return False
        await self.hass.async_add_executor_job(
            self.mqtt_client.send_port_command, port, "setPort", props
        )
        return True

    @property
    def _fusion_setting_status(self) -> str:
        """Return the pre-cooking status for this FUSION device type."""
        if self.mqtt_client and self.mqtt_client.is_venus:
            return AIRFRYER_STATUS_PRECOOK
        return AIRFRYER_STATUS_SETTING

    async def async_set_power(self, power_on: bool) -> bool:
        """Set device power state."""
        # Deferred import avoids a circular dependency on sensor_descriptions.
        from .sensor_descriptions import get_device_type

        device_type = get_device_type(self.device_info.model_name or "")
        is_airfryer = device_type in ("airfryer", "airfryer_dual", "multicooker")

        # FUSION airfryers: use NCP control port, not shadow update
        if self._is_fusion and is_airfryer:
            if not power_on:
                return await self._mqtt_command(
                    "control", {"status": AIRFRYER_STATUS_STANDBY}
                )
            return True  # Power on is a no-op; use Start button
        # FUSION non-airfryers (Rita espresso, air purifier): shadow update
        if self._is_fusion and self.mqtt_client:
            await self.hass.async_add_executor_job(self.mqtt_client.set_power, power_on)
            return True
        # Local airfryers: stop command
        if is_airfryer and self._state and "airfryer" in self._state.properties:
            if not power_on:
                return await self.async_airfryer_stop()
            return True
        result = await self.api.set_power(self.device_info, power_on)
        if result:
            if self._state:
                self._state.power_on = power_on
            await self.async_request_refresh()
        return result

    async def async_set_mode(self, mode: str) -> bool:
        """Set device mode."""
        if self._is_fusion:
            return await self._mqtt_command("status", {"mode": mode})
        result = await self.api.set_mode(self.device_info, mode)
        if result:
            if self._state:
                self._state.properties["mode"] = mode
            await self.async_request_refresh()
        return result

    async def async_set_fan_speed(self, speed: str) -> bool:
        """Set fan speed."""
        if self._is_fusion:
            return await self._mqtt_command("status", {"om": speed})
        result = await self.api.set_fan_speed(self.device_info, speed)
        if result:
            if self._state:
                self._state.properties["om"] = speed
            await self.async_request_refresh()
        return result

    async def async_set_child_lock(self, locked: bool) -> bool:
        """Set child lock state."""
        if self._is_fusion:
            return await self._mqtt_command("status", {"cl": locked})
        result = await self.api.set_child_lock(self.device_info, locked)
        if result:
            if self._state:
                self._state.properties["cl"] = locked
            await self.async_request_refresh()
        return result

    # Airfryer-specific methods
    async def async_airfryer_start(self) -> bool:
        """Start airfryer cooking.

        APK sends only status=cooking (SpectreCookingStartConverter).
        Settings (temp/time/preset) are configured separately via
        set_settings before the user presses start.
        """
        if self._is_fusion:
            await self._ensure_fusion_control_port()
            return await self._mqtt_command(
                "control", {"status": AIRFRYER_STATUS_COOKING}
            )
        # Pass current temp/time from device state for Venus 3-step flow
        temp = None
        time_seconds = None
        if self._state:
            airfryer = self._state.properties.get("airfryer")
            if airfryer and isinstance(airfryer, dict):
                temp = airfryer.get("temp")
                time_seconds = airfryer.get("time")
        result = await self.api.airfryer_start_cooking(
            self.device_info,
            preheat=self._preheat_enabled,
            temp=temp,
            time_seconds=time_seconds,
        )
        if result:
            await self.async_request_refresh()
        return result

    async def async_airfryer_pause(self) -> bool:
        """Pause airfryer cooking."""
        if self._is_fusion:
            return await self._mqtt_command("control", {"status": "pause"})
        result = await self.api.airfryer_pause(self.device_info)
        if result:
            await self.async_request_refresh()
        return result

    async def async_airfryer_stop(self) -> bool:
        """Stop airfryer and return to standby."""
        if self._is_fusion:
            return await self._mqtt_command("control", {"status": "standby"})
        result = await self.api.airfryer_stop(self.device_info)
        if result:
            await self.async_request_refresh()
        return result

    @property
    def autocook_selected_uuid(self) -> str:
        """Return the currently selected built-in AutoCook UUID."""
        return self._autocook_selected_uuid

    def set_autocook_selected_uuid(self, uuid: str) -> None:
        """Remember which built-in AutoCook UUID the user selected."""
        self._autocook_selected_uuid = uuid

    def autocook_program_options(self) -> dict[str, str]:
        """Return cached {uuid: name} pairs for built-in AutoCook programs."""
        return {rid: name for rid, name in self._recipe_cache.items() if rid.isdigit()}

    async def async_autocook_send(self) -> bool:
        """Send the currently selected AutoCook UUID to the device."""
        uuid = self._autocook_selected_uuid
        if not uuid or self._is_fusion:
            return False
        result = await self.api.set_autocook_program(self.device_info, uuid)
        if result:
            await self.async_request_refresh()
        return result

    async def async_airfryer_set_settings(
        self,
        temp: int | None = None,
        time_seconds: int | None = None,
        temp_unit_fahrenheit: bool = False,
        preset: int | None = None,
        airspeed: int | None = None,
        probe_temp: int | None = None,
    ) -> bool:
        """Set airfryer cooking settings (APK SpectreCookingSettingsSetConverter).

        Sends settings with status=setting. If device is in standby,
        wakes it to idle first (APK SpectreCookingIdleConverter).
        """
        if self._is_fusion:
            props: dict[str, Any] = {}
            if temp is not None:
                props["temp"] = temp
            if time_seconds is not None:
                props["time"] = time_seconds
            if preset is not None:
                props["preset"] = preset
            if airspeed is not None:
                props["airspeed"] = airspeed
            if probe_temp is not None:
                props["probe_temp"] = probe_temp
            if temp_unit_fahrenheit:
                props["temp_unit"] = False  # SPECTRE: True=C, False=F
            if props:
                await self._ensure_fusion_control_port()
                if self._get_airfryer_status() == AIRFRYER_STATUS_STANDBY:
                    await self._mqtt_command(
                        "control", {"status": AIRFRYER_STATUS_IDLE}
                    )
                    await self._wait_for_status(AIRFRYER_STATUS_IDLE, timeout=10)
                props["status"] = self._fusion_setting_status
                return await self._mqtt_command("control", props)
            return True
        result = await self.api.airfryer_set_settings(
            self.device_info,
            temp,
            time_seconds,
            temp_unit_fahrenheit,
            preset,
            airspeed,
            probe_temp,
        )
        if result:
            await self.async_request_refresh()
        return result

    async def async_airfryer_keep_warm(self) -> bool:
        """Start keep warm mode with configured time and temperature."""
        if self._is_fusion:
            await self._ensure_fusion_control_port()
            # Wake from standby if needed
            if self._get_airfryer_status() == AIRFRYER_STATUS_STANDBY:
                await self._mqtt_command("control", {"status": AIRFRYER_STATUS_IDLE})
                await self._wait_for_status(AIRFRYER_STATUS_IDLE, timeout=10)
            # Two-step flow: configure keep warm, then start
            await self._mqtt_command(
                "control",
                {
                    "status": self._fusion_setting_status,
                    "preset": 8,
                    "time": self._keep_warm_time,
                    "temp": self._keep_warm_temp,
                },
            )
            await self._wait_for_status(self._fusion_setting_status, timeout=10)
            return await self._mqtt_command(
                "control", {"status": AIRFRYER_STATUS_COOKING}
            )
        result = await self.api.airfryer_keep_warm(
            self.device_info,
            time_seconds=self._keep_warm_time,
            temp=self._keep_warm_temp,
        )
        if result:
            await self.async_request_refresh()
        return result

    # Rita espresso machine control commands (APK RitaControlCommand)
    RITA_CMD_REMOTE_BREW = 1
    RITA_CMD_REMOTE_BREW_CUSTOM = 2
    RITA_CMD_SKIP_STEP = 3
    RITA_CMD_ABORT_BREW = 4
    RITA_CMD_RESUME_BREW = 5
    RITA_CMD_CONFIG_BARISTA_ASSISTANT = 14

    # RitaDrinkId values (APK RitaDrinkKt) used as Recipe_id for REMOTE_BREW
    # of built-in drinks (BrewRitaRegularDrinkUseCase).
    RITA_DRINK_HOT_WATER = 21

    def _rita_session_id(self) -> int:
        """Generate a new Rita session owner id (APK RitaSessionIdGenerator)."""
        return secrets.randbelow(2**31 - 10) + 10

    def _rita_active_session_id(self) -> int:
        """Return the active session id for abort/resume/skip.

        The APK's RitaCancelBrewingConverter and RitaContinueBrewingConverter
        pass the current SessionId as SesOwnId. Using a fresh random id gets
        MACHINE_BUSY (51) because the command does not match the active
        session. Read the latest SesOwnId the machine reported via the
        Status port, and fall back to a random id if none is known yet.
        """
        if self._state:
            airfryer = self._state.properties.get("airfryer")
            if isinstance(airfryer, dict):
                value = airfryer.get("SesOwnId")
                try:
                    sid = int(value) if value is not None else 0
                except (ValueError, TypeError):
                    sid = 0
                if sid > 0:
                    return sid
        return self._rita_session_id()

    async def _rita_control(self, props: dict[str, Any]) -> bool:
        """Send a Rita control port command."""
        if not self._is_fusion or not self.mqtt_client:
            return False
        return await self._mqtt_command("control", props)

    async def async_rita_abort_brew(self) -> bool:
        """Abort the current brew (APK ABORT_BREW)."""
        return await self._rita_control(
            {
                "CtrlCmd": self.RITA_CMD_ABORT_BREW,
                "SesOwnId": self._rita_active_session_id(),
            }
        )

    async def async_rita_resume_brew(self) -> bool:
        """Resume a suspended brew (APK RESUME_BREW)."""
        return await self._rita_control(
            {
                "CtrlCmd": self.RITA_CMD_RESUME_BREW,
                "SesOwnId": self._rita_active_session_id(),
            }
        )

    async def async_rita_skip_step(self) -> bool:
        """Skip the current brewing step (APK SKIP_STEP)."""
        return await self._rita_control(
            {
                "CtrlCmd": self.RITA_CMD_SKIP_STEP,
                "SesOwnId": self._rita_active_session_id(),
            }
        )

    async def async_rita_brew_builtin(self, profile_id: int, drink_id: int) -> bool:
        """Brew a built-in drink (APK BrewRitaRegularDrinkUseCase, REMOTE_BREW)."""
        return await self._rita_control(
            {
                "CtrlCmd": self.RITA_CMD_REMOTE_BREW,
                "SesOwnId": self._rita_session_id(),
                "Profile_id": profile_id,
                "Recipe_id": drink_id,
            }
        )

    async def async_rita_brew(self, profile_id: int, slot: int = 0) -> bool:
        """Brew the recipe saved in a profile's slot if present, else fall back.

        If the slot holds a user-saved recipe (base64 RitaBrewCommand in
        Recipes_p1/p2), brew it via REMOTE_BREW_CUSTOM so the machine
        applies the saved customization. Otherwise treat ``slot`` as a
        built-in drink id and route through REMOTE_BREW.
        """
        blob = self._rita_recipe_blob(slot)
        if blob:
            return await self._rita_control(
                {
                    "CtrlCmd": self.RITA_CMD_REMOTE_BREW_CUSTOM,
                    "SesOwnId": self._rita_session_id(),
                    "Profile_id": profile_id,
                    "RcpBinData": blob,
                }
            )
        return await self.async_rita_brew_builtin(profile_id, slot)

    def _rita_recipe_blob(self, slot: int) -> str | None:
        """Return the base64 RitaBrewCommand blob for a recipe slot, if any.

        Slots 0-39 are stored as ``rcp0``..``rcp39`` in the Recipes_p1 port
        (APK RitaRecipesP1PortProperties). Slots 40-79 map to ``rcp40``..
        ``rcp79`` in Recipes_p2. Empty slots report an empty string.
        """
        if not self._state or slot < 0 or slot > 79:
            return None
        port_key = "Recipes_p1" if slot < 40 else "Recipes_p2"
        port = self._state.properties.get(port_key)
        if not isinstance(port, dict):
            return None
        value = port.get(f"rcp{slot}")
        if isinstance(value, str) and value:
            return value
        return None

    async def async_rita_brew_hot_water(self) -> bool:
        """Brew hot water using the built-in drink id (APK REMOTE_BREW).

        The APK brews built-in drinks via BrewRitaRegularDrinkUseCase,
        which sends REMOTE_BREW with Recipe_id = drink id. Hot water is
        drink id 21 per RitaDrinkKt. The profile must be non-empty. Using
        the built-in path directly avoids treating slot 21 as a saved
        recipe if that slot happens to be populated.
        """
        return await self.async_rita_brew_builtin(
            self._rita_brew_profile_id, self.RITA_DRINK_HOT_WATER
        )

    def _rita_current_roast_level(self) -> int:
        """Return the roast level the machine last reported, or 0."""
        if self._state:
            airfryer = self._state.properties.get("airfryer")
            if isinstance(airfryer, dict):
                value = airfryer.get("RoastLevel")
                try:
                    return int(value) if value is not None else 0
                except (ValueError, TypeError):
                    return 0
        return 0

    def _rita_current_bean_type(self) -> int:
        """Return the bean type the machine last reported, or 0."""
        if self._state:
            airfryer = self._state.properties.get("airfryer")
            if isinstance(airfryer, dict):
                value = airfryer.get("BeanType")
                try:
                    return int(value) if value is not None else 0
                except (ValueError, TypeError):
                    return 0
        return 0

    async def async_rita_set_roast_and_bean(
        self, roast_level: int, bean_type: int
    ) -> bool:
        """Persist both roast level and bean type on the machine.

        APK RitaSetRoastLevelAndBeanTypeConverter sends both fields with
        CtrlCmd=CONFIG_BARISTA_ASSISTANT and a fresh session id. Sending
        the properties without the command wrapper does not stick, the
        machine reverts within a few seconds.
        """
        return await self._rita_control(
            {
                "CtrlCmd": self.RITA_CMD_CONFIG_BARISTA_ASSISTANT,
                "SesOwnId": self._rita_session_id(),
                "RoastLevel": roast_level,
                "BeanType": bean_type,
            }
        )

    async def async_rita_set_roast_level(self, level: int) -> bool:
        """Set the bean roast level (0=light, 1=medium, 2=dark)."""
        return await self.async_rita_set_roast_and_bean(
            level, self._rita_current_bean_type()
        )

    async def async_rita_set_bean_type(self, bean_type: int) -> bool:
        """Set the bean type (0=arabica, 1=mix, 2=other)."""
        return await self.async_rita_set_roast_and_bean(
            self._rita_current_roast_level(), bean_type
        )

    @property
    def rita_brew_profile_id(self) -> int:
        """Return the profile id selected for the next manual brew."""
        return self._rita_brew_profile_id

    def set_rita_brew_profile_id(self, value: int) -> None:
        """Update the profile id selected for the next manual brew."""
        self._rita_brew_profile_id = value

    @property
    def rita_brew_recipe_id(self) -> int:
        """Return the recipe id selected for the next manual brew."""
        return self._rita_brew_recipe_id

    def set_rita_brew_recipe_id(self, value: int) -> None:
        """Update the recipe id selected for the next manual brew."""
        self._rita_brew_recipe_id = value

    @property
    def keep_warm_time(self) -> int:
        """Return keep warm time in seconds."""
        return self._keep_warm_time

    def set_keep_warm_time(self, seconds: int) -> None:
        """Set keep warm time in seconds."""
        self._keep_warm_time = seconds

    @property
    def keep_warm_temp(self) -> int:
        """Return keep warm temperature."""
        return self._keep_warm_temp

    def set_keep_warm_temp(self, temp: int) -> None:
        """Set keep warm temperature."""
        self._keep_warm_temp = temp

    async def async_airfryer_update_settings(
        self,
        temp: int | None = None,
        time_seconds: int | None = None,
        temp_unit_fahrenheit: bool = False,
    ) -> bool:
        """Update airfryer settings without changing cooking state."""
        if self._is_fusion:
            props: dict[str, Any] = {}
            if temp is not None:
                props["temp"] = temp
            if time_seconds is not None:
                props["time"] = time_seconds
            if props:
                # Pre-cooking: include setting status so device accepts values.
                # Mid-cooking: send without status (APK behavior).
                if not self.is_airfryer_cooking():
                    props["status"] = self._fusion_setting_status
                return await self._mqtt_command("control", props)
            return True
        cooking = self.is_airfryer_cooking()
        result = await self.api.airfryer_update_settings(
            self.device_info, temp, time_seconds, temp_unit_fahrenheit, cooking
        )
        if result:
            await self.async_request_refresh()
        return result

    async def async_set_port_property(self, port: str, key: str, value: Any) -> bool:
        """Set a property on a specific device port."""
        if self._is_fusion:
            return await self._mqtt_command(port, {key: value})
        result = await self.api.set_status_property(self.device_info, key, value)
        if result:
            await self.async_request_refresh()
        return result

    async def async_set_status_property(self, key: str, value: Any) -> bool:
        """Set a property on the device status port."""
        if self._is_fusion:
            return await self._mqtt_command("status", {key: value})
        result = await self.api.set_status_property(self.device_info, key, value)
        if result:
            await self.async_request_refresh()
        return result

    # --- Recipe cache ---

    async def _proactive_mqtt_refresh(self) -> None:
        """Refresh MQTT credentials and reconnect before token expiry."""
        assert self.mqtt_client is not None
        from .cloud_api import PhilipsCloudAPI

        self.mqtt_client._reconnecting = True
        self.mqtt_client._refreshing = True
        try:
            async with self._token_lock:
                refresh_token = self.config_entry.data.get(CONF_CLOUD_REFRESH_TOKEN, "")
                if not refresh_token:
                    return
                cloud_api = PhilipsCloudAPI()
                try:
                    tokens = await cloud_api.refresh_tokens(refresh_token)
                    new_refresh = tokens.get("refresh_token", refresh_token)
                    if new_refresh != refresh_token:
                        new_data = {
                            **self.config_entry.data,
                            CONF_CLOUD_REFRESH_TOKEN: new_refresh,
                        }
                        self.hass.config_entries.async_update_entry(
                            self.config_entry, data=new_data
                        )
                    access_token = tokens.get("access_token", "")
                    sig = await cloud_api.get_mqtt_signature(
                        access_token,
                        self.config_entry.data.get(CONF_PLATFORM_REST_URL, ""),
                        self.config_entry.data.get(CONF_TENANT, ""),
                    )
                    signature = sig.get("signature", "")
                finally:
                    await cloud_api.close()
            # Blocking connect in executor (not holding the lock)
            await self.hass.async_add_executor_job(
                self.mqtt_client._do_reconnect, access_token, signature
            )
            _LOGGER.info("Proactive MQTT reconnect successful")
        except Exception:
            _LOGGER.warning(
                "Proactive MQTT reconnect failed, will retry on next heartbeat"
            )
        finally:
            self.mqtt_client._reconnecting = False
            self.mqtt_client._refreshing = False

    def _inject_recipe_name(self) -> None:
        """Inject cached recipe name into airfryer state."""
        if not self._state:
            return
        airfryer = self._state.properties.get("airfryer")
        if not airfryer or not isinstance(airfryer, dict):
            return
        stored_lang = self.config_entry.data.get(CONF_RECIPE_LANGUAGE, "")
        catalog_fresh = (
            self.config_entry.data.get(CONF_AUTOCOOK_CATALOG_FETCHED)
            and stored_lang == self.hass.config.language
        )
        if (
            not catalog_fresh
            and not self._catalog_fetch_running
            and self.config_entry.data.get(CONF_CLOUD_REFRESH_TOKEN)
        ):
            self._catalog_fetch_running = True
            self.hass.async_create_task(self._populate_autocook_catalog())
        recipe_id = str(airfryer.get("recipe_id", ""))
        if not recipe_id or recipe_id == "0":
            recipe = self._state.properties.get("recipe")
            if isinstance(recipe, dict):
                recipe_id = str(recipe.get("recipe_id", ""))
        if not recipe_id or recipe_id == "0":
            return
        # Local preset IDs (PRESET-*) are not cloud recipes
        if recipe_id.startswith("PRESET-"):
            return
        cached = self._recipe_cache.get(recipe_id)
        if cached:
            airfryer["recipeName"] = cached
        elif (
            self.config_entry.data.get(CONF_CLOUD_REFRESH_TOKEN)
            and self._pending_recipe_fetch != recipe_id
            and recipe_id not in self._failed_recipe_ids
        ):
            self._pending_recipe_fetch = recipe_id
            self.hass.async_create_task(self._fetch_and_inject_recipe(recipe_id))

    async def _get_access_token(self) -> str | None:
        """Get a fresh access token, coordinated with MQTT credential refresh."""
        from .cloud_api import PhilipsCloudAPI

        async with self._token_lock:
            refresh_token = self.config_entry.data.get(CONF_CLOUD_REFRESH_TOKEN, "")
            if not refresh_token:
                return None
            cloud_api = PhilipsCloudAPI()
            try:
                tokens = await cloud_api.refresh_tokens(refresh_token)
                new_refresh = tokens.get("refresh_token", refresh_token)
                if new_refresh != refresh_token:
                    new_data = {
                        **self.config_entry.data,
                        CONF_CLOUD_REFRESH_TOKEN: new_refresh,
                    }
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
                return tokens.get("access_token", "")
            finally:
                await cloud_api.close()

    async def _fetch_and_inject_recipe(self, recipe_id: str) -> None:
        """Fetch a recipe name from the cloud and inject into state."""
        from .cloud_api import PhilipsCloudAPI

        try:
            access_token = await self._get_access_token()
            if not access_token:
                return
            cloud_api = PhilipsCloudAPI()
            try:
                name = await cloud_api.get_recipe_name(
                    access_token, recipe_id, self.hass.config.language
                )
                if not name and recipe_id.isdigit():
                    name = await cloud_api.get_autocook_program_name(
                        access_token, recipe_id, self.hass.config.language
                    )
            finally:
                await cloud_api.close()
            if name:
                self._recipe_cache[recipe_id] = name
                new_data = {
                    **self.config_entry.data,
                    CONF_RECIPE_CACHE: dict(self._recipe_cache),
                    CONF_RECIPE_LANGUAGE: self.hass.config.language,
                }
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                if self._state:
                    airfryer = self._state.properties.get("airfryer")
                    if airfryer and isinstance(airfryer, dict):
                        airfryer["recipeName"] = name
                    self.async_set_updated_data(self._state)
            else:
                self._failed_recipe_ids.add(recipe_id)
        except Exception:
            _LOGGER.warning("Failed to fetch recipe name for %s", recipe_id)
            self._failed_recipe_ids.add(recipe_id)
        finally:
            self._pending_recipe_fetch = None

    async def _populate_autocook_catalog(self) -> None:
        """Merge the full AutoCook catalog into the recipe name cache."""
        from .cloud_api import PhilipsCloudAPI

        try:
            access_token = await self._get_access_token()
            if not access_token:
                return
            cloud_api = PhilipsCloudAPI()
            try:
                programs = await cloud_api.get_autocook_programs(
                    access_token, self.hass.config.language
                )
            finally:
                await cloud_api.close()
            if not programs:
                return
            added = 0
            for rid, name in programs.items():
                if rid not in self._recipe_cache:
                    self._recipe_cache[rid] = name
                    added += 1
            new_data = {
                **self.config_entry.data,
                CONF_RECIPE_CACHE: dict(self._recipe_cache),
                CONF_RECIPE_LANGUAGE: self.hass.config.language,
                CONF_AUTOCOOK_CATALOG_FETCHED: True,
            }
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            _LOGGER.info("AutoCook catalog cached: %d new names", added)
            if added and self._state:
                self._inject_recipe_name()
                self.async_set_updated_data(self._state)
        except Exception:
            _LOGGER.warning("AutoCook catalog fetch failed", exc_info=True)
        finally:
            self._catalog_fetch_running = False

    async def async_refresh_recipe_cache(self) -> bool:
        """Clear cache and re-fetch current recipe from the cloud API."""
        refresh_token = self.config_entry.data.get(CONF_CLOUD_REFRESH_TOKEN, "")
        if not refresh_token:
            self.config_entry.async_start_reauth(self.hass)
            return False
        self._recipe_cache.clear()
        self._failed_recipe_ids.clear()
        new_data = {
            **self.config_entry.data,
            CONF_RECIPE_CACHE: {},
            CONF_AUTOCOOK_CATALOG_FETCHED: False,
        }
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
        self._catalog_fetch_running = True
        await self._populate_autocook_catalog()
        # Re-fetch current recipe if active
        if self._state:
            airfryer = self._state.properties.get("airfryer")
            if airfryer and isinstance(airfryer, dict):
                recipe_id = str(airfryer.get("recipe_id", ""))
                if recipe_id and recipe_id != "0":
                    self._pending_recipe_fetch = None
                    await self._fetch_and_inject_recipe(recipe_id)
        return True

    @property
    def preheat_enabled(self) -> bool:
        """Return preheat setting."""
        return self._preheat_enabled

    def set_preheat_enabled(self, enabled: bool) -> None:
        """Set preheat flag for next cooking start."""
        self._preheat_enabled = enabled

    @property
    def device_state(self) -> LocalDeviceState | None:
        """Get current device state."""
        return self._state

    @property
    def device_id(self) -> str:
        """Return unique device identifier."""
        return self.device_info.cpp_id or self.device_info.ip_address

    @property
    def available(self) -> bool:
        """Return True if device is available."""
        if self._state is None:
            return False
        return True

    @property
    def last_update_time(self) -> float:
        """Return timestamp of last successful poll."""
        return self._last_update_time

    @property
    def consecutive_failures(self) -> int:
        """Return number of consecutive poll failures."""
        return self._consecutive_failures

    async def _wait_for_status(self, target: str, timeout: int = 10) -> bool:
        """Wait for the airfryer to reach a target status."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if self._get_airfryer_status() == target:
                return True
            await asyncio.sleep(0.3)
        _LOGGER.warning("Timeout waiting for airfryer status %s", target)
        return False

    async def _ensure_fusion_control_port(self, timeout: int = 10) -> bool:
        """Ensure a FUSION airfryer has advertised its cooking control port.

        Venus 2 devices (HD9880) hide venusaf_c while in standby. Sending
        shadow powerOn=true makes the device advertise its control port.
        """
        if not self._is_fusion or not self.mqtt_client:
            return True
        if self.mqtt_client.has_cooking_control_port():
            return True
        _LOGGER.debug("No cooking control port advertised, waking airfryer")
        await self.hass.async_add_executor_job(self.mqtt_client.wake_airfryer)
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if self.mqtt_client.has_cooking_control_port():
                return True
            await asyncio.sleep(0.3)
        _LOGGER.warning("Timeout waiting for cooking control port after wake")
        return False

    def _get_airfryer_status(self) -> str:
        """Return the current airfryer status string."""
        if not self._state:
            return ""
        airfryer = self._state.properties.get("airfryer")
        if not airfryer or not isinstance(airfryer, dict):
            return ""
        return airfryer.get("status", "")

    def is_airfryer_cooking(self) -> bool:
        """Check if airfryer is actively cooking (not paused)."""
        return self._get_airfryer_status() == AIRFRYER_STATUS_COOKING

    def _is_airfryer_paused(self) -> bool:
        """Check if airfryer is paused."""
        return self._get_airfryer_status() == AIRFRYER_STATUS_PAUSED

    def has_property(
        self,
        property_key: str | None,
        nested_key: str | None = None,
    ) -> bool:
        """Check if a property exists in current device state.

        Used during entity setup to filter which entities to create.
        """
        if not self._state or not property_key:
            return False

        if nested_key:
            nested = self._state.properties.get(nested_key)
            if nested and isinstance(nested, dict):
                return property_key in nested
            return False
        return property_key in self._state.properties

    def get_property_key(self, property_key: str, nested_key: str | None) -> str:
        """Generate a unique key for tracking properties."""
        if nested_key:
            return f"{nested_key}.{property_key}"
        return property_key

    def is_property_seen(
        self, property_key: str, nested_key: str | None = None
    ) -> bool:
        """Check if a property has ever been seen."""
        key = self.get_property_key(property_key, nested_key)
        return key in self._seen_properties

    def mark_property_seen(
        self, property_key: str, nested_key: str | None = None
    ) -> None:
        """Mark a property as seen (entity created for it)."""
        key = self.get_property_key(property_key, nested_key)
        self._seen_properties.add(key)

    def register_new_property_callback(
        self, callback: Callable[[list[tuple[str, str | None]]], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when new properties are discovered.

        Returns a function to unregister the callback.
        """
        self._new_properties_callbacks.append(callback)

        def unregister() -> None:
            if callback in self._new_properties_callbacks:
                self._new_properties_callbacks.remove(callback)

        return unregister

    def _check_for_new_properties(
        self, state: LocalDeviceState
    ) -> list[tuple[str, str | None]]:
        """Check for new properties that haven't been seen before.

        Returns list of (property_key, nested_key) tuples for new properties.
        """
        new_properties: list[tuple[str, str | None]] = []

        # Check top-level properties
        for key in state.properties:
            value = state.properties[key]
            if isinstance(value, dict):
                # Check nested properties (e.g., airfryer.status)
                for nested_prop in value:
                    full_key = f"{key}.{nested_prop}"
                    if full_key not in self._seen_properties:
                        new_properties.append((nested_prop, key))
            elif key not in self._seen_properties:
                new_properties.append((key, None))

        return new_properties

    def _notify_new_properties(
        self, new_properties: list[tuple[str, str | None]]
    ) -> None:
        """Notify callbacks about new properties."""
        if not new_properties or not self._new_properties_callbacks:
            # Mark unclaimed properties as seen to avoid re-scanning
            for prop_key, nested_key in new_properties:
                self.mark_property_seen(prop_key, nested_key)
            return

        _LOGGER.debug("New properties discovered: %s", new_properties)
        for callback in self._new_properties_callbacks:
            try:
                callback(new_properties)
            except Exception:
                _LOGGER.exception("Error in new property callback")

        # Mark any properties not claimed by callbacks as seen
        for prop_key, nested_key in new_properties:
            if not self.is_property_seen(prop_key, nested_key):
                self.mark_property_seen(prop_key, nested_key)

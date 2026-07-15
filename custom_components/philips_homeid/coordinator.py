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
from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    ACTIVE_SCAN_INTERVAL,
    CLOUD_FETCH_RETRY_DELAY,
    CONF_ACTIVE_SCAN_INTERVAL,
    CONF_APPLIANCE_ID,
    CONF_AUTOCOOK_CATALOG_FETCHED,
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_CPP_ID,
    CONF_DEVICE_ID,
    CONF_MY_PRESETS,
    CONF_MY_PRESETS_FETCHED,
    CONF_MY_PRESETS_LANGUAGE,
    CONF_PLATFORM_REST_URL,
    CONF_RECIPE_CACHE,
    CONF_RECIPE_LANGUAGE,
    CONF_SCAN_INTERVAL,
    CONF_TENANT,
    CONF_THING_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FUSION_HEARTBEAT_INTERVAL,
    KEEP_WARM_DEFAULT_TEMP_C,
    RITA_BUILTIN_DRINKS,
    RITA_BUILTIN_DRINK_OFFSET,
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
    PORT_VENUSAF,
    LocalDeviceInfo,
    LocalDeviceState,
    PhilipsLocalAPI,
)
from .mqtt_api import PhilipsMQTTClient
from .rita_protobuf import decode_profile_id

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
        # Baseline the cook countdown extrapolates from, and the cur_time it
        # was taken at. Kept apart from _last_update_time because a message
        # can refresh the state without carrying a new cur_time.
        self._countdown_baseline: float = 0.0
        self._countdown_value: Any = None
        self._seen_properties: set[str] = set()  # Track all properties ever seen
        self._new_properties_callbacks: list[
            Callable[[list[tuple[str, str | None]]], None]
        ] = []  # Callbacks for new properties
        self._preheat_enabled: bool = False  # Preheat flag for next cooking start
        self._keep_warm_time: int = 3600  # Keep warm duration in seconds (default 1h)
        # None until the user picks one; the default depends on the unit the
        # appliance reads, which is not known yet. See keep_warm_temp.
        self._keep_warm_temp: int | None = None
        self._rita_brew_profile_id: int = 0  # Rita espresso: profile to brew (0-7)
        self._rita_brew_recipe_id: int = 0  # Rita espresso: saved recipe slot to brew
        self._rita_brew_drink_id: int = 0  # Rita espresso: built-in drink id to brew
        self._rita_drink_catalog: dict[int, str] = {}  # live per-model drink list
        self._rita_drinks_fetched: bool = False  # one-shot fetch guard (per session)
        self._rita_drinks_fetch_running: bool = False  # in-flight guard
        self._rita_drinks_retry_after: float = 0.0  # monotonic gate after a failure
        self._catalog_retry_after: float = 0.0  # same, for the AutoCook catalog
        self._my_presets_retry_after: float = 0.0  # same, for My Presets
        self._autocook_selected_uuid: str = ""  # Venus airfryer: UUID to send next
        self._consecutive_failures: int = 0  # Track consecutive poll failures
        self._max_failures: int = 3  # Failures before marking device offline
        # Monotonic time of the latest NCP port-data arrival; setup waits on
        # this to defer entity creation until real device state is in hand.
        # Recipe name cache: recipe_id -> name, persisted in config entry.
        # Invalidate if HA language changed since cache was built.
        cached_lang = entry.data.get(CONF_RECIPE_LANGUAGE, "")
        if cached_lang and cached_lang != hass.config.language:
            self._recipe_cache: dict[str, str] = {}
        else:
            self._recipe_cache = dict(entry.data.get(CONF_RECIPE_CACHE, {}))
        self._pending_recipe_fetch: str | None = None
        self._failed_recipe_ids: set[str] = set()  # IDs that failed cloud lookup
        # Custom "My Presets" from the cloud account. Invalidate if the HA
        # language changed since they were fetched (names are localized).
        presets_lang = entry.data.get(CONF_MY_PRESETS_LANGUAGE, "")
        if presets_lang and presets_lang != hass.config.language:
            self._my_presets: list[dict[str, Any]] = []
        else:
            self._my_presets = list(entry.data.get(CONF_MY_PRESETS, []))
        self._my_presets_fetch_running = False
        # Debounce task for persisting recipe-cache updates to entry data
        self._recipe_cache_persist_task: asyncio.Task[None] | None = None
        # Background task for local-espresso wake+brew. The heat-up can take
        # over a minute on a cold machine, so we run it off the button press
        # rather than blocking the entity update path.
        self._espresso_brew_task: asyncio.Task[None] | None = None
        # Every background task this coordinator starts, so unload can stop
        # them all. They write back to the config entry and serialise on
        # _token_lock, which is per coordinator, so one that outlives its
        # coordinator would race the reloaded one over the refresh token.
        self._background_tasks: set[asyncio.Task[None]] = set()
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
        self._update_countdown_baseline(state)
        self._state = state
        self._last_update_time = time.monotonic()
        self._consecutive_failures = 0
        self._update_polling_interval(state)
        if new_properties:
            self._notify_new_properties(new_properties)
        self._inject_recipe_name()
        self._maybe_fetch_rita_drinks()
        self._maybe_refresh_mqtt_token()
        self.async_set_updated_data(state)

    def _update_countdown_baseline(self, state: LocalDeviceState) -> None:
        """Re-baseline the cook countdown only when the device moves it.

        The countdown sensor reads cur_time and subtracts the time since this
        baseline. Pushes are deltas and most carry no cur_time, so taking a
        new baseline on every message would restart the subtraction from a
        stale value and the countdown would jump back up.
        """
        airfryer = state.properties.get("airfryer")
        cur_time = airfryer.get("cur_time") if isinstance(airfryer, dict) else None
        if cur_time != self._countdown_value:
            self._countdown_value = cur_time
            self._countdown_baseline = time.monotonic()

    def _maybe_refresh_mqtt_token(self) -> None:
        """Refresh the MQTT token from the push path if it is near expiry.

        async_set_updated_data() restarts the heartbeat timer, so a device
        that pushes more often than FUSION_HEARTBEAT_INTERVAL starves
        _async_update_data_fusion() and the refresh it carries. Checking here
        too means a chatty device stays covered; a quiet one still gets the
        heartbeat. Without this the token reaches the one hour mark and the
        broker drops the link.
        """
        if self.mqtt_client and self.mqtt_client.needs_token_refresh():
            self._create_tracked_task(self._proactive_mqtt_refresh())

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

                self._update_countdown_baseline(state)
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

            # No response: track consecutive failures. Only drop the
            # polling interval back to idle once we have hit the failure
            # threshold, so a single lost packet mid-cook doesn't widen
            # the cur_time/status gap from 10s to 60s for the next poll.
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_failures:
                self._update_polling_interval(None)
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
        is_airfryer = device_type in ("airfryer", "multicooker")

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
        # Local EP/SM espresso machines: command port power enum (2=on, 1=off)
        if device_type == "espresso" and not self._is_fusion:
            # Powering off cancels a pending brew so its 90s ready-wait
            # doesn't keep polling against a machine the user just told
            # to go to standby.
            if not power_on:
                brew_task = self._espresso_brew_task
                if brew_task is not None and not brew_task.done():
                    _LOGGER.info(
                        "Cancelling pending espresso brew because of power-off"
                    )
                    brew_task.cancel()
            result = await self.api.set_espresso_power(self.device_info, power_on)
            if result:
                if self._state:
                    self._state.power_on = power_on
                await self.async_request_refresh()
            return result
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
        recipe_id: str | None = None,
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
            if recipe_id is not None:
                # My Presets use the SPECTRE recipe control port, whose
                # required shape is time, temp, temp_unit, status, step_id
                # and recipe_id, with no preset (APK
                # SpectreUserPresetSettingsSetConverter ->
                # SpectreRecipeControlPortProperties). The device answers
                # NCP port_error when preset is present or temp_unit or
                # step_id are missing. temp_unit is standard on this port
                # (True=Fahrenheit, False=Celsius), APK-verified and
                # confirmed on a real HD9280 in issue #27, so send the
                # preset's own unit verbatim. An earlier inversion here
                # rested on a wrong "SPECTRE is inverted" assumption and
                # flipped Celsius presets to Fahrenheit.
                props["temp_unit"] = temp_unit_fahrenheit
                props["step_id"] = ""
                props["recipe_id"] = recipe_id
            else:
                if preset is not None:
                    props["preset"] = preset
                if airspeed is not None:
                    props["airspeed"] = airspeed
                if probe_temp is not None:
                    props["probe_temp"] = probe_temp
                # Echo the unit the appliance currently shows; omitting it
                # makes the device reset to Fahrenheit (issue #27).
                raw_unit = self._current_raw_temp_unit()
                if raw_unit is not None:
                    props["temp_unit"] = raw_unit
            if props:
                await self._ensure_fusion_control_port()
                if self._get_airfryer_status() == AIRFRYER_STATUS_STANDBY:
                    await self._mqtt_command(
                        "control", {"status": AIRFRYER_STATUS_IDLE}
                    )
                    await self._wait_for_status(AIRFRYER_STATUS_IDLE, timeout=10)
                props["status"] = self._fusion_setting_status
                # My Presets go to the dedicated SPECTRE recipe control port
                # (recipe_c); the regular Control port has no recipe_id/step_id
                # fields and rejects them with NCP port_error (APK
                # FusionSpectreCookingCommandGeneratorBridge routes
                # setUserPreset to SpectreRecipeControlPort).
                port = "recipe_control" if recipe_id is not None else "control"
                return await self._mqtt_command(port, props)
            return True
        # My Presets carry their own unit; everything else preserves the
        # appliance's current one (issue #27).
        raw_unit = None if recipe_id is not None else self._current_raw_temp_unit()
        result = await self.api.airfryer_set_settings(
            self.device_info,
            temp,
            time_seconds,
            temp_unit_fahrenheit,
            preset,
            airspeed,
            probe_temp,
            recipe_id,
            raw_temp_unit=raw_unit,
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
                    "temp": self.keep_warm_temp,
                },
            )
            await self._wait_for_status(self._fusion_setting_status, timeout=10)
            return await self._mqtt_command(
                "control", {"status": AIRFRYER_STATUS_COOKING}
            )
        result = await self.api.airfryer_keep_warm(
            self.device_info,
            time_seconds=self._keep_warm_time,
            temp=self.keep_warm_temp,
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

    # Built-in recipe IDs and PrimDose (water volume in ml) for the local
    # EP/SM espresso command/BasicRecipe port. Captured from the HomeID app
    # against a real EP2520.
    ESPRESSO_RECIPE_ID_ESPRESSO = 2
    ESPRESSO_PRIM_DOSE_ESPRESSO = 40
    ESPRESSO_RECIPE_ID_COFFEE = 6
    ESPRESSO_PRIM_DOSE_COFFEE = 120

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

    def _rita_profile_wire_id(self, slot: int) -> int | None:
        """Resolve a Profiles-port slot to the profile's real profileId.

        The brew command carries the profile's own profileId (tag 1 of the
        RitaProfileData blob in ``Profiles.profile{slot}``), not the storage
        slot index the dropdown uses. An empty slot, a missing blob or a
        profileId of 0 all mean "no usable profile"; a warning is logged and
        None is returned so the caller can skip a brew the machine would
        reject with INVALID_PROFILE_ID (106).
        """
        wire_id: int | None = None
        if self._state and 0 <= slot <= 7:
            profiles = self._state.properties.get("Profiles")
            if isinstance(profiles, dict):
                blob = profiles.get(f"profile{slot}")
                if isinstance(blob, str) and blob:
                    wire_id = decode_profile_id(blob)
        if wire_id is None:
            _LOGGER.warning(
                "Cannot brew: profile slot %s has no usable saved profile", slot
            )
        return wire_id

    async def async_rita_brew_builtin(self, profile_slot: int, drink_id: int) -> bool:
        """Brew a built-in drink (APK BrewRitaRegularDrinkUseCase, REMOTE_BREW)."""
        wire_id = self._rita_profile_wire_id(profile_slot)
        if wire_id is None:
            return False
        return await self._rita_control(
            {
                "CtrlCmd": self.RITA_CMD_REMOTE_BREW,
                "SesOwnId": self._rita_session_id(),
                "Profile_id": wire_id,
                "Recipe_id": drink_id,
            }
        )

    async def async_rita_brew(self, profile_slot: int, selection: int = 0) -> bool:
        """Brew the recipe or built-in drink chosen in the recipe dropdown.

        ``selection`` is the recipe-select value. Values at or above
        RITA_BUILTIN_DRINK_OFFSET name a built-in drink (offset removed) and
        brew via REMOTE_BREW. A lower value is a saved-recipe slot: if it holds
        a user recipe (base64 RitaBrewCommand in Recipes_p1/p2) it brews via
        REMOTE_BREW_CUSTOM so the machine applies the saved customization,
        otherwise it falls back to a built-in-drink brew by that id.

        ``profile_slot`` is the Profiles-port dropdown slot; it is resolved to
        the profile's real profileId before the command is sent.
        """
        if selection < 0:
            _LOGGER.warning("Cannot brew: no saved recipe selected")
            return False
        if selection >= RITA_BUILTIN_DRINK_OFFSET:
            return await self.async_rita_brew_builtin(
                profile_slot, selection - RITA_BUILTIN_DRINK_OFFSET
            )
        blob = self._rita_recipe_blob(selection)
        if blob:
            wire_id = self._rita_profile_wire_id(profile_slot)
            if wire_id is None:
                return False
            return await self._rita_control(
                {
                    "CtrlCmd": self.RITA_CMD_REMOTE_BREW_CUSTOM,
                    "SesOwnId": self._rita_session_id(),
                    "Profile_id": wire_id,
                    "RcpBinData": blob,
                }
            )
        return await self.async_rita_brew_builtin(profile_slot, selection)

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

    # Local EP/SM espresso machine brew (command/BasicRecipe port).
    # Distinct from the Rita (FUSION/cloud) brew above. Built-in drinks
    # confirmed on EP2520: espresso = recipe 2 (40 ml), coffee = recipe 6
    # (120 ml). RecipeBookIds come from configuration.recipelist.
    async def _ensure_espresso_ready(self, timeout: float = 90.0) -> bool:
        """Power the espresso machine on and wait until ready (mainstate 2)."""
        status = await self.api.get_espresso_status(self.device_info)
        mainstate = (status or {}).get("mainstate", 0)
        if mainstate == 2:
            return True
        if mainstate in (3, 5):  # already brewing / needs attention
            _LOGGER.warning(
                "Espresso machine not ready to brew (mainstate=%s)", mainstate
            )
            return False
        if mainstate in (0, 1):  # off / standby -> power on
            await self.api.set_espresso_power(self.device_info, True)
        # Heating from cold can take a while; poll for the ready state until
        # the monotonic deadline so this is unaffected by wall-clock changes.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            status = await self.api.get_espresso_status(self.device_info)
            mainstate = (status or {}).get("mainstate", 0)
            if mainstate == 2:
                return True
            if mainstate == 5:
                _LOGGER.warning("Espresso machine needs attention (mainstate=5)")
                return False
        _LOGGER.warning("Timed out waiting for espresso machine to be ready")
        return False

    async def async_espresso_brew(
        self,
        recipe_id: int,
        prim_dose: int,
        *,
        gr_dose: int = 2,
        sec_dose: int = 0,
        temperature: int = 2,
        nr_of_brews: int = 0,
    ) -> bool:
        """Kick off a brew on a local EP/SM espresso machine.

        Returns immediately. The actual wake-up + ready-wait + brew runs as
        a background task because a cold machine can take well over a minute
        to reach mainstate 2; awaiting that from a button press handler
        would freeze the entity for that whole time.
        """
        task = self._espresso_brew_task
        if task is not None and not task.done():
            _LOGGER.warning("Espresso brew already in progress; ignoring new request")
            return False
        self._espresso_brew_task = self._create_tracked_task(
            self._do_espresso_brew(
                recipe_id,
                prim_dose,
                gr_dose=gr_dose,
                sec_dose=sec_dose,
                temperature=temperature,
                nr_of_brews=nr_of_brews,
            )
        )
        return True

    async def _do_espresso_brew(
        self,
        recipe_id: int,
        prim_dose: int,
        *,
        gr_dose: int,
        sec_dose: int,
        temperature: int,
        nr_of_brews: int,
    ) -> None:
        """Background body for async_espresso_brew."""
        if not await self._ensure_espresso_ready():
            return
        result = await self.api.espresso_brew(
            self.device_info,
            recipe_id,
            prim_dose,
            gr_dose=gr_dose,
            sec_dose=sec_dose,
            temperature=temperature,
            nr_of_brews=nr_of_brews,
        )
        if result:
            await self.async_request_refresh()

    async def async_espresso_brew_espresso(self) -> bool:
        """Brew a built-in espresso."""
        return await self.async_espresso_brew(
            self.ESPRESSO_RECIPE_ID_ESPRESSO,
            self.ESPRESSO_PRIM_DOSE_ESPRESSO,
        )

    async def async_espresso_brew_coffee(self) -> bool:
        """Brew a built-in coffee."""
        return await self.async_espresso_brew(
            self.ESPRESSO_RECIPE_ID_COFFEE,
            self.ESPRESSO_PRIM_DOSE_COFFEE,
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
    def rita_brew_drink_id(self) -> int:
        """Return the built-in drink id selected for the next manual brew."""
        return self._rita_brew_drink_id

    def set_rita_brew_drink_id(self, value: int) -> None:
        """Update the built-in drink id selected for the next manual brew."""
        self._rita_brew_drink_id = value

    def rita_builtin_drinks(self) -> dict[int, str]:
        """Return the machine's live drink catalog, or the built-in fallback.

        The per-model list is fetched from the cloud once per session; until it
        arrives (or if the fetch fails) the hardcoded RITA_BUILTIN_DRINKS is
        used so the dropdown always has sensible options.
        """
        return self._rita_drink_catalog or dict(RITA_BUILTIN_DRINKS)

    @staticmethod
    def _parse_rita_capabilities(
        items: list[dict[str, Any]], ctn: str
    ) -> dict[int, str]:
        """Build a {drinkID: drinkName} map from a capabilities response.

        Keeps only drinks whose ctnNumbers include this machine's CTN, with a
        valid integer id and a non-empty name, and drops hot water (id 21,
        which has its own button). Malformed entries are skipped.
        """
        catalog: dict[int, str] = {}
        if not isinstance(items, list):
            return catalog
        for item in items:
            if not isinstance(item, dict):
                continue
            drink_id = item.get("drinkID")
            name = item.get("drinkName")
            ctns = item.get("ctnNumbers")
            if (
                not isinstance(drink_id, int)
                or isinstance(drink_id, bool)
                or drink_id == PhilipsHomeIDCoordinator.RITA_DRINK_HOT_WATER
                or not isinstance(name, str)
                or not name.strip()
                or not isinstance(ctns, list)
                or ctn not in ctns
            ):
                continue
            catalog[drink_id] = name.strip()
        return catalog

    @property
    def keep_warm_time(self) -> int:
        """Return keep warm time in seconds."""
        return self._keep_warm_time

    def set_keep_warm_time(self, seconds: int) -> None:
        """Set keep warm time in seconds."""
        self._keep_warm_time = seconds

    @property
    def keep_warm_temp(self) -> int:
        """Return keep warm temperature, in the appliance's unit.

        Until the user picks a value there is nothing to echo, so the default
        is the 65C the appliances keep warm at, expressed in whichever unit
        this one reads. Naming it in Celsius on a Fahrenheit appliance both
        put the entity below its own minimum and sent 65F, which is no heat
        at all.
        """
        if self._keep_warm_temp is not None:
            return self._keep_warm_temp
        return self._temp_in_device_unit(KEEP_WARM_DEFAULT_TEMP_C)

    def _temp_in_device_unit(self, celsius: int) -> int:
        """Express a Celsius temperature in the unit the appliance reads."""
        if not self._current_raw_temp_unit():
            return celsius
        return round(
            TemperatureConverter.convert(
                celsius, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT
            )
        )

    def set_keep_warm_temp(self, temp: int) -> None:
        """Set keep warm temperature, in the appliance's unit."""
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
                # Echo the current unit so a temp or time change never
                # resets it (issue #27).
                raw_unit = self._current_raw_temp_unit()
                if raw_unit is not None:
                    props["temp_unit"] = raw_unit
                # Pre-cooking: include setting status so device accepts values.
                # Mid-cooking: send without status (APK behavior).
                if not self.is_airfryer_cooking():
                    props["status"] = self._fusion_setting_status
                return await self._mqtt_command("control", props)
            return True
        cooking = self.is_airfryer_cooking()
        result = await self.api.airfryer_update_settings(
            self.device_info,
            temp,
            time_seconds,
            temp_unit_fahrenheit,
            cooking,
            raw_temp_unit=self._current_raw_temp_unit(),
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

    async def async_set_control_property(self, key: str, value: Any) -> bool:
        """Set a property on the device control port.

        MUJI air purifiers accept property writes (mode, beep volume, air
        quality threshold, sensor monitor) on their Control NCP port, not the
        read-only Status port (APK AirControlPort). Non-FUSION devices fall
        back to the local status-property call.
        """
        if self._is_fusion:
            return await self._mqtt_command("control", {key: value})
        result = await self.api.set_status_property(self.device_info, key, value)
        if result:
            await self.async_request_refresh()
        return result

    # --- Recipe cache ---

    async def _proactive_mqtt_refresh(self) -> None:
        """Refresh MQTT credentials and reconnect before token expiry."""
        assert self.mqtt_client is not None
        from .cloud_api import CloudAuthError, CloudConnectionError, PhilipsCloudAPI

        # The heartbeat and the push path both reach here, and the reactive
        # backoff loop claims the same ownership: only one reconnect may own
        # the client. claim_reconnect settles that across threads, which
        # matters because on_disconnect races this from the paho thread.
        if not self.mqtt_client.claim_reconnect():
            return
        self.mqtt_client._refreshing = True
        token_rejected = False
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
        except CloudConnectionError as err:
            # Checked before CloudAuthError, which it subclasses.
            _LOGGER.warning(
                "Proactive MQTT reconnect failed (%s), will retry on next heartbeat",
                err,
            )
        except CloudAuthError as err:
            # The account rejected the refresh token, so no amount of retrying
            # brings this device back. Ask the user to log in again.
            _LOGGER.error("Proactive MQTT reconnect: token rejected (%s)", err)
            token_rejected = True
            self.config_entry.async_start_reauth(self.hass)
        except Exception as err:
            _LOGGER.warning(
                "Proactive MQTT reconnect failed (%s), will retry on next heartbeat",
                err,
            )
        finally:
            self.mqtt_client.release_reconnect()
            self.mqtt_client._refreshing = False
        # A failed reconnect leaves no live client to emit on_disconnect, so
        # nothing else restarts the connection and needs_token_refresh() stays
        # False while disconnected. Kick the reactive backoff loop so the device
        # recovers instead of staying dead until a reload. A rejected token is
        # the exception: the loop would only rediscover the same rejection.
        if not token_rejected and not self.mqtt_client.connected:
            self.mqtt_client.start_reconnect()

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
            and time.monotonic() >= self._catalog_retry_after
            and self.config_entry.data.get(CONF_CLOUD_REFRESH_TOKEN)
        ):
            self._catalog_fetch_running = True
            self._create_tracked_task(self._populate_autocook_catalog())
        presets_fresh = (
            self.config_entry.data.get(CONF_MY_PRESETS_FETCHED)
            and self.config_entry.data.get(CONF_MY_PRESETS_LANGUAGE, "")
            == self.hass.config.language
        )
        if (
            not presets_fresh
            and not self._my_presets_fetch_running
            and time.monotonic() >= self._my_presets_retry_after
            and self.config_entry.data.get(CONF_CLOUD_REFRESH_TOKEN)
        ):
            self._my_presets_fetch_running = True
            self._create_tracked_task(self._populate_my_presets())
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
            self._create_tracked_task(self._fetch_and_inject_recipe(recipe_id))

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

    def _schedule_recipe_cache_persist(self) -> None:
        """Debounce recipe-cache persistence to one write per ~5s window.

        Without this, fetching recipe names one at a time produced one
        disk write to the config entry per recipe, with the full cache
        dict copied each time and the update listener fanned out
        through HA core every time.
        """
        task = self._recipe_cache_persist_task
        if task is not None and not task.done():
            return

        async def _persist() -> None:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return
            new_data = {
                **self.config_entry.data,
                CONF_RECIPE_CACHE: dict(self._recipe_cache),
                CONF_RECIPE_LANGUAGE: self.hass.config.language,
            }
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )

        self._recipe_cache_persist_task = self._create_tracked_task(_persist())

    async def _fetch_and_inject_recipe(self, recipe_id: str) -> None:
        """Fetch a recipe name from the cloud and inject into state."""
        from .cloud_api import CloudConnectionError, PhilipsCloudAPI

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
                self._schedule_recipe_cache_persist()
                if self._state:
                    airfryer = self._state.properties.get("airfryer")
                    if airfryer and isinstance(airfryer, dict):
                        airfryer["recipeName"] = name
                        # recipeName is injected into the state that was
                        # already scanned, and every later state is a fresh
                        # object that never carries it, so the scan can never
                        # see it. Announce it once so the sensor appears
                        # without waiting for a restart to warm the cache.
                        if not self.is_property_seen("recipeName", "airfryer"):
                            self._notify_new_properties([("recipeName", "airfryer")])
                    self.async_set_updated_data(self._state)
            else:
                self._failed_recipe_ids.add(recipe_id)
        except CloudConnectionError as err:
            # The cloud is having a bad moment. Leave the id out of the failed
            # set so a later poll retries instead of never naming it again.
            _LOGGER.debug("Recipe name fetch for %s deferred: %s", recipe_id, err)
        except Exception:
            _LOGGER.warning("Failed to fetch recipe name for %s", recipe_id)
            self._failed_recipe_ids.add(recipe_id)
        finally:
            self._pending_recipe_fetch = None

    async def _populate_autocook_catalog(self) -> None:
        """Merge the full AutoCook catalog into the recipe name cache.

        An empty catalog is a real answer and is recorded as fetched, so an
        account without AutoCook stops asking on every poll.
        """
        from .cloud_api import CloudConnectionError, PhilipsCloudAPI

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
        except CloudConnectionError as err:
            # The catalog flag stays unset so this retries, and the trigger
            # runs on every push, so hold off before asking again.
            self._catalog_retry_after = time.monotonic() + CLOUD_FETCH_RETRY_DELAY
            _LOGGER.debug("AutoCook catalog fetch deferred: %s", err)
        except Exception:
            self._catalog_retry_after = time.monotonic() + CLOUD_FETCH_RETRY_DELAY
            _LOGGER.warning("AutoCook catalog fetch failed", exc_info=True)
        finally:
            self._catalog_fetch_running = False

    def my_preset_options(self) -> dict[str, dict[str, Any]]:
        """Return the user's custom presets keyed by display name."""
        return {p["name"]: p for p in self._my_presets if p.get("name")}

    async def async_apply_my_preset(self, name: str) -> bool:
        """Apply a custom preset (APK SpectreUserPresetSetConverter).

        A custom preset is sent as a manual cook (preset 0) carrying the
        preset's shortId in recipe_id plus its default temperature and
        time. As with the built-in cooking method, the device enters the
        setting state and the user presses Start to begin cooking.
        """
        preset = self.my_preset_options().get(name)
        if not preset:
            return False
        return await self.async_airfryer_set_settings(
            preset=0,
            temp=preset.get("temp"),
            time_seconds=preset.get("time"),
            temp_unit_fahrenheit=bool(preset.get("fahrenheit")),
            recipe_id=preset.get("short_id"),
        )

    async def _resolve_appliance_id(
        self, cloud_api: Any, access_token: str
    ) -> str | None:
        """Find this device's cloud appliance id, cached in the entry.

        The id is the last path segment of the appliance self link (APK
        UiDevice.applianceId = StringUtils.b(applianceSelfLink)). The right
        appliance is matched by MAC / external id / serial.
        """
        cached = self.config_entry.data.get(CONF_APPLIANCE_ID, "")
        if cached:
            return str(cached)

        items = await cloud_api.get_appliances_via_homeid(
            {"access_token": access_token}
        )
        targets = {
            v.lower()
            for v in (
                self.device_info.cpp_id,
                self.config_entry.data.get(CONF_DEVICE_ID, ""),
                self.device_info.serial_number,
            )
            if v
        }
        for item in items:
            keys = {
                str(item.get(k, "")).lower()
                for k in ("macAddress", "externalDeviceId", "serialNumber")
            }
            if not targets & keys:
                continue
            href = (
                ((item.get("_links") or {}).get("self") or {}).get("href")
                or item.get("id")
                or ""
            )
            if not href:
                continue
            appliance_id = str(href).rsplit("/", 1)[-1].split("?")[0]
            new_data = {**self.config_entry.data, CONF_APPLIANCE_ID: appliance_id}
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            return appliance_id
        return None

    async def _populate_my_presets(self) -> None:
        """Fetch and cache the user's custom presets from the cloud.

        An empty list is a real answer and is recorded as fetched; only a
        retryable failure leaves the guard unset to try again later.
        """
        from .cloud_api import CloudConnectionError, PhilipsCloudAPI

        try:
            access_token = await self._get_access_token()
            if not access_token:
                return
            cloud_api = PhilipsCloudAPI()
            try:
                appliance_id = await self._resolve_appliance_id(cloud_api, access_token)
                presets: list[dict[str, Any]] = []
                if appliance_id:
                    presets = await cloud_api.get_my_presets(
                        access_token, appliance_id, self.hass.config.language
                    )
                else:
                    _LOGGER.debug("My Presets: no matching cloud appliance found")
            finally:
                await cloud_api.close()
            self._my_presets = presets
            new_data = {
                **self.config_entry.data,
                CONF_MY_PRESETS: presets,
                CONF_MY_PRESETS_LANGUAGE: self.hass.config.language,
                CONF_MY_PRESETS_FETCHED: True,
            }
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            _LOGGER.info("My Presets cached: %d preset(s)", len(presets))
            if presets:
                # Re-run platform setup so the select appears on first fetch.
                self._notify_new_properties([("my_preset", "airfryer")])
                if self._state:
                    self.async_set_updated_data(self._state)
        except CloudConnectionError as err:
            self._my_presets_retry_after = time.monotonic() + CLOUD_FETCH_RETRY_DELAY
            _LOGGER.debug("My Presets fetch deferred: %s", err)
        except Exception:
            self._my_presets_retry_after = time.monotonic() + CLOUD_FETCH_RETRY_DELAY
            _LOGGER.warning("My Presets fetch failed", exc_info=True)
        finally:
            self._my_presets_fetch_running = False

    def _maybe_fetch_rita_drinks(self) -> None:
        """Kick a one-shot fetch of the per-model Rita drink catalog."""
        if (
            not self._is_fusion
            or self._rita_drinks_fetched
            or self._rita_drinks_fetch_running
            or not self._state
            or time.monotonic() < self._rita_drinks_retry_after
        ):
            return
        props = self._state.properties
        # Rita machines expose a Profiles port; the capabilities call also
        # needs the host firmware version (a shadow field), the device id and
        # cloud creds. The device id is checked here as well as in the fetch,
        # which returns on it without latching the one-shot guard: without it
        # every push spawned a task that did nothing but clear the flag again.
        if (
            "Profiles" not in props
            or not props.get("hostFirmwareVersion")
            or not self.config_entry.data.get(CONF_DEVICE_ID)
            or not self.config_entry.data.get(CONF_CLOUD_REFRESH_TOKEN)
        ):
            return
        self._rita_drinks_fetch_running = True
        self._create_tracked_task(self._populate_rita_drinks())

    async def _populate_rita_drinks(self) -> None:
        """Fetch the machine's supported drink catalog from the cloud once."""
        from .cloud_api import CloudConnectionError, PhilipsCloudAPI

        try:
            device_id = self.config_entry.data.get(CONF_DEVICE_ID, "")
            firmware = ""
            if self._state:
                firmware = str(self._state.properties.get("hostFirmwareVersion", ""))
            if not device_id or not firmware:
                return
            access_token = await self._get_access_token()
            if not access_token:
                return
            cloud_api = PhilipsCloudAPI()
            try:
                # The IoT device list may key a device by id, thingName or MAC;
                # externalDeviceId (CONF_DEVICE_ID) is not guaranteed to equal
                # dev["id"], so match on any of the identifiers we hold (as
                # get_thing_name does) to find this machine's CTN.
                thing_name = self.config_entry.data.get(CONF_THING_NAME, "")
                mac = self.config_entry.data.get(CONF_CPP_ID, "")
                ctn = ""
                for dev in await cloud_api.get_devices(access_token):
                    if not dev.get("ctn"):
                        continue
                    if (
                        (device_id and dev.get("id") == device_id)
                        or (thing_name and dev.get("thingName") == thing_name)
                        or (mac and dev.get("macAddress") == mac)
                    ):
                        ctn = str(dev["ctn"])
                        break
                if not ctn:
                    # The cloud answered and this device has no CTN. That is a
                    # stable fact, not a bad moment, so stop asking.
                    self._rita_drinks_fetched = True
                    _LOGGER.debug("Rita drinks: no CTN for device %s", device_id)
                    return
                items = await cloud_api.get_rita_capabilities(
                    access_token, device_id, ctn, firmware
                )
                # The catalog call came back, so the attempt was real and
                # final: an empty list means this machine has no cloud drinks
                # and the built-in list stands.
                self._rita_drinks_fetched = True
            finally:
                await cloud_api.close()
            catalog = self._parse_rita_capabilities(items, ctn)
            if catalog:
                self._rita_drink_catalog = catalog
                _LOGGER.info(
                    "Rita drink catalog: %d drink(s) for %s", len(catalog), ctn
                )
                if self._state:
                    self.async_set_updated_data(self._state)
        except CloudConnectionError as err:
            # Leave the guard unset so a later push tries again rather than
            # falling back to the built-in drinks for the whole session. This
            # runs on every push, so hold off a while first.
            self._rita_drinks_retry_after = time.monotonic() + CLOUD_FETCH_RETRY_DELAY
            _LOGGER.debug("Rita drink catalog fetch deferred: %s", err)
        except Exception:
            self._rita_drinks_retry_after = time.monotonic() + CLOUD_FETCH_RETRY_DELAY
            _LOGGER.warning("Rita drink catalog fetch failed", exc_info=True)
        finally:
            self._rita_drinks_fetch_running = False

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
            CONF_MY_PRESETS_FETCHED: False,
        }
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
        self._catalog_fetch_running = True
        await self._populate_autocook_catalog()
        self._my_presets_fetch_running = True
        await self._populate_my_presets()
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
        if self._is_fusion and self.mqtt_client:
            # The FUSION heartbeat swallows its errors and returns the cached
            # state, so last_update_success can never report a dead link. The
            # MQTT connection is the real signal. It stays True across a
            # proactive token refresh, so entities do not flap during one.
            return self.mqtt_client.connected
        return True

    @property
    def countdown_baseline(self) -> float:
        """Monotonic time the device last reported a new cook countdown."""
        return self._countdown_baseline

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

    def _current_raw_temp_unit(self) -> bool | None:
        """Return the device's current raw temp_unit, or None if unknown.

        Cook and setting commands echo this value back unchanged so the
        appliance never resets its temperature unit (issue #27). The raw
        value is sent verbatim, so this stays correct regardless of a
        model's inverted or standard temp_unit semantics.
        """
        if not self._state:
            return None
        airfryer = self._state.properties.get("airfryer")
        if not airfryer or not isinstance(airfryer, dict):
            return None
        value = airfryer.get("temp_unit")
        return bool(value) if value is not None else None

    def _create_tracked_task(
        self, coro: Coroutine[Any, Any, None]
    ) -> asyncio.Task[None]:
        """Start a background task that unload can cancel."""
        task = self.hass.async_create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def cancel_background_tasks(self) -> None:
        """Cancel every background task still in flight.

        Called from unload so nothing writes to a half-unloaded entry, and so
        a task from the old coordinator cannot refresh the cloud token
        alongside the one that replaces it.
        """
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()

    @property
    def ncp_ports_replied(self) -> bool:
        """Return whether a FUSION device has replied about all of its ports."""
        if self.mqtt_client is None:
            return False
        return self.mqtt_client.ports_replied

    @property
    def ncp_ports_complete(self) -> bool:
        """Return whether a FUSION device has reported all of its ports."""
        if self.mqtt_client is None:
            return False
        return self.mqtt_client.ports_complete

    def airfryer_style_port(self) -> str | None:
        """Return the port whose conventions this appliance follows.

        The local API learns the architecture from the port that answered.
        FUSION never probes ports, so airfryer_port stays None there and the
        model decides instead. Failing that, the transport's view of the
        advertised NCP ports tells a Venus from a SPECTRE, but not a Hermes or
        a Nutrimax: those carry the Venus extras too, so they have to be
        recognised by model or not at all.
        """
        port = self.device_info.airfryer_port
        if isinstance(port, str):
            return port
        model_port = PhilipsLocalAPI._port_for_model(self.device_info)
        if model_port:
            return model_port
        if self.mqtt_client is not None and self.mqtt_client.is_venus:
            return PORT_VENUSAF
        return None

    def airfryer_temperature_unit(self) -> str:
        """Return the unit the appliance currently reports temperatures in.

        The device sends temperatures in the unit named by temp_unit and
        nothing converts them on the way through, so an entity has to name
        that unit rather than assume Celsius. temp_unit is standard polarity
        (True is Fahrenheit) on every model handled here, which is what the
        temp_unit_fahrenheit diagnostic already reports. An unknown unit falls
        back to Celsius, matching the appliances' own default.
        """
        if self._current_raw_temp_unit():
            return str(UnitOfTemperature.FAHRENHEIT)
        return str(UnitOfTemperature.CELSIUS)

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
        # Snapshot the callback list so a callback that registers or
        # unregisters another (e.g. an entity being removed) does not
        # mutate the iterator we're stepping through.
        for callback in list(self._new_properties_callbacks):
            try:
                callback(new_properties)
            except Exception:
                _LOGGER.exception("Error in new property callback")

        # Mark any properties not claimed by callbacks as seen
        for prop_key, nested_key in new_properties:
            if not self.is_property_seen(prop_key, nested_key):
                self.mark_property_seen(prop_key, nested_key)

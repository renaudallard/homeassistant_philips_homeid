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
"""The Philips HomeID integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import (
    CONF_AIRFRYER_PORT,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_CPP_ID,
    CONF_ENCRYPTION_KEY,
    CONF_IS_FUSION,
    CONF_MODEL,
    CONF_MQTT_HOST,
    CONF_PLATFORM_REST_URL,
    CONF_TENANT,
    CONF_THING_NAME,
    CONF_USE_HTTPS,
    DOMAIN,
    FUSION_MQTT_HOST,
    FUSION_PLATFORM_REST_URL,
    FUSION_TENANT,
)
from .coordinator import PhilipsHomeIDCoordinator
from .local_api import LocalDeviceInfo, PhilipsLocalAPI
from .mqtt_api import FusionDeviceInfo, PhilipsMQTTClient

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.FAN,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


def _extract_jwt_sub(token: str) -> str | None:
    """Extract the 'sub' claim from a JWT access token without validation."""
    import base64
    import json as _json

    try:
        # JWT is header.payload.signature; decode the payload (2nd part)
        payload_b64 = token.split(".")[1]
        # Add padding
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("sub")
    except Exception:
        return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Philips HomeID from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    if entry.data.get(CONF_IS_FUSION):
        return await _async_setup_fusion_entry(hass, entry)
    return await _async_setup_local_entry(hass, entry)


async def _async_setup_fusion_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a FUSION device via MQTT cloud relay."""
    from .cloud_api import CloudAuthError, PhilipsCloudAPI

    thing_name = entry.data.get(CONF_THING_NAME, "")
    tenant = entry.data.get(CONF_TENANT, FUSION_TENANT)
    mqtt_host = entry.data.get(CONF_MQTT_HOST, FUSION_MQTT_HOST)
    platform_rest_url = entry.data.get(CONF_PLATFORM_REST_URL, FUSION_PLATFORM_REST_URL)
    refresh_token = entry.data.get(CONF_CLOUD_REFRESH_TOKEN, "")
    model = entry.data.get(CONF_MODEL, "")
    cpp_id = entry.data.get(CONF_CPP_ID, "")

    if not thing_name or not refresh_token:
        _LOGGER.error("FUSION device missing thing_name or refresh_token")
        return False

    # Refresh Gigya CDC OIDC tokens and get MQTT credentials.
    # APK uses Gigya CDC tokens for DaConnect MQTT (confirmed 2026-03-27).
    cloud_api = PhilipsCloudAPI()
    try:
        tokens = await cloud_api.refresh_tokens(refresh_token)
        access_token = tokens["access_token"]
        new_refresh = tokens.get("refresh_token", refresh_token)
        if new_refresh != refresh_token:
            new_data = {**entry.data, CONF_CLOUD_REFRESH_TOKEN: new_refresh}
            hass.config_entries.async_update_entry(entry, data=new_data)

        # APK gets MQTT userId from POST /user/self/get-id with the id_token.
        # The Custom Authorizer IoT policy expects this as the client ID prefix.
        id_token = tokens.get("id_token", "")
        user_id = None
        if id_token:
            user_id = await cloud_api.get_mqtt_user_id(
                access_token, id_token, platform_rest_url, tenant
            )
        if not user_id:
            user_id = _extract_jwt_sub(access_token)
            _LOGGER.warning("get-id failed, falling back to JWT sub claim")
        _LOGGER.debug("MQTT user_id resolved: %s", bool(user_id))

        sig_data = await cloud_api.get_mqtt_signature(
            access_token, platform_rest_url, tenant
        )
        _LOGGER.info("MQTT signature response keys: %s", list(sig_data.keys()))
    except CloudAuthError as err:
        raise ConfigEntryAuthFailed(
            f"FUSION auth failed: {err}. Re-authenticate via config flow."
        ) from err
    except Exception as err:
        raise ConfigEntryNotReady(f"FUSION setup error: {err}") from err
    finally:
        await cloud_api.close()

    # Create FUSION device info
    fusion_device = FusionDeviceInfo(
        thing_name=thing_name,
        device_id=cpp_id or thing_name,
        tenant=tenant,
        mqtt_host=mqtt_host,
        platform_rest_url=platform_rest_url,
        model_name=model,
        friendly_name=entry.title,
        user_id=user_id or "",
    )

    # Create device info for entity compatibility
    device_info = LocalDeviceInfo(
        ip_address="",
        cpp_id=cpp_id or thing_name,
        model_name=model,
        friendly_name=entry.title,
    )

    # Credential refresh callable for MQTT reconnection
    def _refresh_mqtt_credentials() -> tuple[str, str]:
        """Synchronous credential refresh for MQTT reconnection thread."""
        import asyncio as _asyncio

        async def _do_refresh() -> tuple[str, str]:
            # Use coordinator's token lock to prevent race with recipe fetch
            async with coordinator._token_lock:
                api = PhilipsCloudAPI()
                try:
                    rt = entry.data.get(CONF_CLOUD_REFRESH_TOKEN, "")
                    toks = await api.refresh_tokens(rt)
                    new_rt = toks.get("refresh_token", rt)
                    if new_rt != rt:
                        new_data = {
                            **entry.data,
                            CONF_CLOUD_REFRESH_TOKEN: new_rt,
                        }
                        hass.config_entries.async_update_entry(entry, data=new_data)
                    at = toks["access_token"]
                    sig = await api.get_mqtt_signature(at, platform_rest_url, tenant)
                    return at, sig.get("signature", "")
                finally:
                    await api.close()

        future = _asyncio.run_coroutine_threadsafe(_do_refresh(), hass.loop)
        return future.result(timeout=30)

    # Create and connect MQTT client
    mqtt_client = PhilipsMQTTClient(
        device=fusion_device,
        loop=hass.loop,
        credential_refresh=_refresh_mqtt_credentials,
    )

    # Create coordinator in MQTT mode (before connect so callback is ready)
    api = PhilipsLocalAPI()  # unused but required by coordinator signature
    coordinator = PhilipsHomeIDCoordinator(
        hass, api, device_info, entry, mqtt_client=mqtt_client
    )

    # Wire MQTT push updates to coordinator BEFORE connect
    # to avoid missing the initial shadow response
    mqtt_client.set_state_callback(coordinator.update_from_mqtt)

    # APK always uses the OIDC access_token for MQTT, not a token from
    # the signature response. SignatureResponse only has "signature" field.
    try:
        await hass.async_add_executor_job(
            mqtt_client.connect,
            access_token,
            sig_data.get("signature", ""),
        )
    except Exception as err:
        raise ConfigEntryNotReady(f"MQTT connection failed: {err}") from err

    # Initial data fetch via shadow/get
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        mqtt_client.disconnect()
        raise

    # Wait for NCP port data before entity setup. The first refresh
    # sends getAllPorts + getPort requests; responses arrive async.
    # Without this, entities are created before airfryer properties
    # are available, resulting in 0 sensors/controls.
    # Wait until NCP data arrives AFTER the refresh started.
    import time as _time

    refresh_time = _time.monotonic()
    deadline = asyncio.get_event_loop().time() + 15
    while asyncio.get_event_loop().time() < deadline:
        if coordinator._ncp_data_timestamp > refresh_time:
            break
        await asyncio.sleep(0.2)
    else:
        _LOGGER.warning(
            "Timeout waiting for NCP port data from %s, proceeding with setup",
            model,
        )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    _cleanup_stale_entities(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _PREVIOUS_OPTIONS[entry.entry_id] = dict(entry.options)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_setup_local_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a local device via HTTPS API."""
    host = entry.data.get(CONF_HOST)
    cpp_id = entry.data.get(CONF_CPP_ID, "")
    model = entry.data.get(CONF_MODEL, "")
    client_id = entry.data.get(CONF_CLIENT_ID)
    client_secret = entry.data.get(CONF_CLIENT_SECRET)
    use_https = entry.data.get(CONF_USE_HTTPS, True)
    encryption_key = entry.data.get(CONF_ENCRYPTION_KEY)
    airfryer_port = entry.data.get(CONF_AIRFRYER_PORT)

    if not host:
        _LOGGER.error("No host configured for device")
        return False

    # Create device info
    device_info = LocalDeviceInfo(
        ip_address=host,
        cpp_id=cpp_id,
        model_name=model,
        use_https=use_https,
        client_id=client_id,
        client_secret=client_secret,
        encryption_key=encryption_key,
        airfryer_port=airfryer_port,
    )

    # Create local API client
    api = PhilipsLocalAPI()

    # Probe device to verify connectivity
    try:
        probed = await api.probe_device(host)
        if probed:
            if probed.cpp_id:
                device_info.cpp_id = probed.cpp_id
            if probed.model_name:
                device_info.model_name = probed.model_name
            if probed.friendly_name:
                device_info.friendly_name = probed.friendly_name
            # Keep the configured use_https; the probe tries both protocols
            # and the HTTP probe may win for devices that have a UPnP HTTP
            # endpoint alongside their HTTPS API.
            _LOGGER.info(
                "Connected to device %s at %s (model: %s)",
                device_info.friendly_name or device_info.cpp_id,
                host,
                device_info.model_name,
            )
        else:
            _LOGGER.warning(
                "Could not probe device at %s, will try polling anyway", host
            )
    except Exception as err:
        _LOGGER.error("Failed to connect to device at %s: %s", host, err)
        await api.close()
        raise ConfigEntryNotReady(f"Could not connect to device at {host}") from err

    # For HTTP devices: fetch encryption key if not already stored
    if not device_info.use_https and not device_info.encryption_key:
        _LOGGER.info("HTTP device without encryption key, attempting key exchange")
        key = await api.exchange_encryption_key(device_info)
        if key:
            new_data = {**entry.data, CONF_ENCRYPTION_KEY: key}
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.info("Encryption key stored for %s", host)

    # Create coordinator
    coordinator = PhilipsHomeIDCoordinator(hass, api, device_info, entry)

    # Fetch initial data
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await api.close()
        raise

    # Persist discovered airfryer port so it survives reloads
    if (
        isinstance(device_info.airfryer_port, str)
        and entry.data.get(CONF_AIRFRYER_PORT) != device_info.airfryer_port
    ):
        new_data = {**entry.data, CONF_AIRFRYER_PORT: device_info.airfryer_port}
        hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.info(
            "Persisted airfryer port '%s' for %s",
            device_info.airfryer_port,
            host,
        )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    _cleanup_stale_entities(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _PREVIOUS_OPTIONS[entry.entry_id] = dict(entry.options)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


_PREVIOUS_OPTIONS: dict[str, dict] = {}


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the integration.

    HA fires update_listener on both data and options changes.
    Only reload when options actually changed, not on data-only
    updates (recipe cache, token refresh).
    """
    prev = _PREVIOUS_OPTIONS.get(entry.entry_id, {})
    current = dict(entry.options)
    if prev == current:
        return  # Data-only change, no reload needed
    _PREVIOUS_OPTIONS[entry.entry_id] = current
    await hass.config_entries.async_reload(entry.entry_id)


def _cleanup_stale_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: PhilipsHomeIDCoordinator,
) -> None:
    """Remove entities whose properties are no longer reported by the device.

    Only runs cleanup when we have a complete picture of the device state.
    If the airfryer port is expected but data is missing (e.g. first poll
    before auth is established), skip cleanup to avoid removing entities
    that will reappear on the next poll.
    """
    # Don't clean up if the device has a known airfryer port but no airfryer
    # data yet - the first poll may have missed it.
    state = coordinator.device_state
    device = coordinator.device_info
    if (
        state
        and isinstance(device.airfryer_port, str)
        and "airfryer" not in state.properties
    ):
        _LOGGER.debug(
            "Skipping stale entity cleanup: airfryer port '%s' known but no data yet",
            device.airfryer_port,
        )
        return

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)

    for entity_entry in entries:
        unique_id = entity_entry.unique_id
        # unique_id format: {device_id}_{key}
        # Skip entities without a property mapping (e.g., fan, preheat switch)
        parts = unique_id.split("_", 1)
        if len(parts) != 2:
            continue

        platform = entity_entry.domain
        entity_key = parts[1]

        # Check if the property this entity represents still exists
        should_remove = False

        if platform == "sensor":
            from .sensor import SENSORS

            for sensor_desc in SENSORS:
                if sensor_desc.key == entity_key:
                    if sensor_desc.property_key and not coordinator.has_property(
                        sensor_desc.property_key, sensor_desc.nested_key
                    ):
                        should_remove = True
                    break

        elif platform == "binary_sensor":
            from .binary_sensor import BINARY_SENSORS

            for bs_desc in BINARY_SENSORS:
                if bs_desc.key == entity_key:
                    if bs_desc.property_key and not coordinator.has_property(
                        bs_desc.property_key, bs_desc.nested_key
                    ):
                        should_remove = True
                    break

        elif platform == "number":
            from .number import AIRFRYER_NUMBERS, ESPRESSO_NUMBERS, MUJI_NUMBERS

            for num_desc in (*AIRFRYER_NUMBERS, *MUJI_NUMBERS, *ESPRESSO_NUMBERS):
                if num_desc.key == entity_key:
                    if num_desc.property_key and not coordinator.has_property(
                        num_desc.property_key, num_desc.nested_key
                    ):
                        should_remove = True
                    break

        if should_remove:
            _LOGGER.info(
                "Removing stale entity %s (%s)",
                entity_entry.entity_id,
                entity_key,
            )
            registry.async_remove(entity_entry.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Tear down MQTT client and HTTP session regardless of whether the
    platform unload succeeded so we don't leak the paho thread or aiohttp
    session if HA reports an entity still in use.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
    if coordinator is not None:
        if coordinator.mqtt_client:
            await hass.async_add_executor_job(coordinator.mqtt_client.disconnect)
        await coordinator.api.close()

    _PREVIOUS_OPTIONS.pop(entry.entry_id, None)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)

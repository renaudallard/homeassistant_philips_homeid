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
"""Local API client for Philips HomeID devices."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

# Re-export models and constants so existing imports from local_api still work
from .local_models import (  # noqa: F401
    AIRFRYER_STATUS_COOKING,
    AIRFRYER_STATUS_FINISH,
    AIRFRYER_STATUS_IDLE,
    AIRFRYER_STATUS_MAINMENU,
    AIRFRYER_STATUS_MAINTAIN,
    AIRFRYER_STATUS_PAIRING,
    AIRFRYER_STATUS_PARASETTING,
    AIRFRYER_STATUS_PAUSED,
    AIRFRYER_STATUS_POWERSAVE,
    AIRFRYER_STATUS_PRECOOK,
    AIRFRYER_STATUS_SETTING,
    AIRFRYER_STATUS_STANDBY,
    AIRFRYER_STATUS_USER_ACTION,
    DEFAULT_PRODUCT_ID,
    DEFAULT_PROTOCOL_VERSION,
    LocalDeviceInfo,
    LocalDeviceState,
    PhilipsCondorAuth,
    PhilipsCrypto,
    PORT_AIR,
    PORT_AIRFRYER,
    PORT_AUTOCOOK,
    PORT_CONTROL,
    PORT_DEVCURRSTATE,
    PORT_DEVICE,
    PORT_FIRMWARE,
    PORT_FLTSTS,
    PORT_HERMESAC,
    PORT_NUTRIMAX,
    PORT_RECIPE,
    PORT_SECURITY,
    PORT_STATUS,
    PORT_VENUSAF,
    PORT_VENUS1AF,
    VENUS_STYLE_PORTS,
    _MODEL_PORT_MAP,
    parse_ssdp_device,
    parse_zeroconf_device,
)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

_LOGGER = logging.getLogger(__name__)


class PhilipsLocalAPI:
    """Local API client for Philips HomeID devices."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        """Initialize the local API client."""
        self._session = session
        self._own_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None:
            # Disable certificate verification (devices use self-signed certs)
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def close(self) -> None:
        """Close the session if we own it."""
        if self._own_session and self._session:
            await self._session.close()
            self._session = None

    @staticmethod
    def _scheme(device: LocalDeviceInfo) -> str:
        """Return URL scheme for a device."""
        return "https" if device.use_https else "http"

    @staticmethod
    def _airfryer_port(device: LocalDeviceInfo) -> str:
        """Return the airfryer port name for a device."""
        if isinstance(device.airfryer_port, str):
            return device.airfryer_port
        return PORT_AIRFRYER

    def _build_url(
        self,
        device: LocalDeviceInfo,
        port_name: str,
        product_id: int | None = None,
    ) -> str:
        """Build URL for device endpoint."""
        pid = product_id if product_id is not None else device.product_id
        return (
            f"{self._scheme(device)}://{device.ip_address}/di/v{device.protocol_version}"
            f"/products/{pid}/{port_name}"
        )

    def _prepare_body(
        self, device: LocalDeviceInfo, data: dict[str, Any] | None
    ) -> tuple[str | None, bool]:
        """Prepare request body, encrypting if needed.

        Returns: (body_string, is_encrypted)
        """
        if data is None:
            return None, False

        json_str = json.dumps(data)

        if device.encryption_key:
            encrypted = PhilipsCrypto.encrypt(json_str, device.encryption_key)
            if encrypted:
                return encrypted, True
            _LOGGER.warning("Encryption failed, sending as plain JSON")

        return json_str, False

    async def _request(
        self,
        device: LocalDeviceInfo,
        port_name: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        _retry: bool = True,
        product_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Make a request to the device."""
        session = await self._get_session()
        url = self._build_url(device, port_name, product_id)

        headers = {
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        }

        # Add authentication if available
        if device.credentials:
            headers["Authorization"] = device.credentials
            _LOGGER.debug("Using cached credentials")

        # Prepare body (encrypt if device has encryption_key)
        body, _ = self._prepare_body(device, data)

        try:
            _LOGGER.debug("Request: %s %s", method, url)

            if method == "GET":
                async with session.get(
                    url, headers=headers, timeout=REQUEST_TIMEOUT
                ) as resp:
                    result, should_retry = await self._handle_response(device, resp)
                    if should_retry and _retry:
                        return await self._request(
                            device,
                            port_name,
                            method,
                            data,
                            _retry=False,
                            product_id=product_id,
                        )
                    return result
            elif method == "PUT":
                async with session.put(
                    url, headers=headers, data=body, timeout=REQUEST_TIMEOUT
                ) as resp:
                    result, should_retry = await self._handle_response(device, resp)
                    if should_retry and _retry:
                        return await self._request(
                            device,
                            port_name,
                            method,
                            data,
                            _retry=False,
                            product_id=product_id,
                        )
                    return result
            elif method == "POST":
                async with session.post(
                    url, headers=headers, data=body, timeout=REQUEST_TIMEOUT
                ) as resp:
                    result, should_retry = await self._handle_response(device, resp)
                    if should_retry and _retry:
                        return await self._request(
                            device,
                            port_name,
                            method,
                            data,
                            _retry=False,
                            product_id=product_id,
                        )
                    return result

            else:
                _LOGGER.error("Unsupported HTTP method: %s for %s", method, url)
                return None

        except aiohttp.ClientError as err:
            _LOGGER.error("Request failed for %s: %s", url, err)
            return None
        except Exception as err:
            _LOGGER.error("Unexpected error for %s: %s", url, err)
            return None

    async def _handle_response(
        self, device: LocalDeviceInfo, resp: aiohttp.ClientResponse
    ) -> tuple[dict[str, Any] | None, bool]:
        """Handle API response, including authentication challenges.

        For devices with an encryption_key, the response body is
        base64-encoded AES/CBC/PKCS7 ciphertext that must be decrypted.

        Returns: (result, should_retry)
        """
        if resp.status == 200:
            text = await resp.text()

            # Decrypt if device uses AES encryption
            if device.encryption_key:
                decrypted = PhilipsCrypto.decrypt(text, device.encryption_key)
                if decrypted is None:
                    _LOGGER.warning("Failed to decrypt response, trying as plain JSON")
                else:
                    text = decrypted

            try:
                return json.loads(text), False
            except (json.JSONDecodeError, ValueError):
                _LOGGER.debug("Non-JSON response: %s", text[:200])
                return {"raw": text}, False

        elif resp.status == 401:
            # Handle authentication challenge
            challenge = resp.headers.get("WWW-Authenticate")
            _LOGGER.debug(
                "Got 401 challenge: %s", challenge[:100] if challenge else "None"
            )
            if challenge and device.client_id and device.client_secret:
                credentials = PhilipsCondorAuth.create_credentials(
                    challenge, device.client_id, device.client_secret
                )
                if credentials:
                    device.credentials = credentials
                    _LOGGER.info("Created new credentials from challenge, will retry")
                    return None, True  # Retry with new credentials
            else:
                _LOGGER.warning(
                    "Unauthorized - no credentials available (client_id=%s, client_secret=%s)",
                    bool(device.client_id),
                    bool(device.client_secret),
                )
            return None, False

        elif resp.status == 429:
            _LOGGER.warning("Device busy (429)")
            return None, False

        else:
            text = await resp.text()
            _LOGGER.warning("Request failed (%s): %s", resp.status, text[:200])
            return None, False

    async def get_status(self, device: LocalDeviceInfo) -> dict[str, Any] | None:
        """Get device status."""
        return await self._request(device, PORT_STATUS)

    async def get_air_quality(self, device: LocalDeviceInfo) -> dict[str, Any] | None:
        """Get air quality data (for air purifiers)."""
        return await self._request(device, PORT_AIR)

    async def get_filter_status(self, device: LocalDeviceInfo) -> dict[str, Any] | None:
        """Get filter status."""
        return await self._request(device, PORT_FLTSTS)

    async def get_device_info(self, device: LocalDeviceInfo) -> dict[str, Any] | None:
        """Get device information."""
        return await self._request(device, PORT_DEVICE)

    async def exchange_encryption_key(self, device: LocalDeviceInfo) -> str | None:
        """Fetch AES encryption key from device /security endpoint.

        The key exchange uses product_id=0 and requires authentication.
        Returns the hex-encoded encryption key, or None on failure.
        """
        result = await self._request(device, PORT_SECURITY, product_id=0)
        if result:
            # The response may be the key directly as a string,
            # or a JSON object containing the key
            if isinstance(result, dict):
                key = result.get("raw", result.get("key", ""))
                if isinstance(key, str):
                    key = key.strip()
            else:
                key = str(result).strip()

            if key:
                device.encryption_key = key
                _LOGGER.info(
                    "Obtained encryption key for %s (%d chars)",
                    device.ip_address,
                    len(key),
                )
                return key
            _LOGGER.warning("Empty encryption key from %s", device.ip_address)
        else:
            _LOGGER.warning("Failed to fetch encryption key from %s", device.ip_address)
        return None

    async def get_firmware_info(self, device: LocalDeviceInfo) -> dict[str, Any] | None:
        """Get firmware version info from device.

        The firmware port uses product_id=0.
        Returns dict with 'version' (installed) and optionally 'upgrade' (available).
        """
        return await self._request(device, PORT_FIRMWARE, product_id=0)

    async def set_power(self, device: LocalDeviceInfo, power_on: bool) -> bool:
        """Set device power state."""
        data = {"pwr": "1" if power_on else "0"}
        result = await self._request(device, PORT_STATUS, method="PUT", data=data)
        return result is not None

    async def set_mode(self, device: LocalDeviceInfo, mode: str) -> bool:
        """Set device mode (e.g., 'A' for auto, 'M' for manual, 'S' for sleep)."""
        data = {"mode": mode}
        result = await self._request(device, PORT_STATUS, method="PUT", data=data)
        return result is not None

    async def set_fan_speed(self, device: LocalDeviceInfo, speed: str) -> bool:
        """Set fan speed (e.g., '1', '2', '3', 't' for turbo, 's' for silent)."""
        data = {"om": speed}
        result = await self._request(device, PORT_STATUS, method="PUT", data=data)
        return result is not None

    async def set_child_lock(self, device: LocalDeviceInfo, locked: bool) -> bool:
        """Set child lock state."""
        data = {"cl": locked}
        result = await self._request(device, PORT_STATUS, method="PUT", data=data)
        return result is not None

    # Airfryer-specific methods
    # Mapping between Venus and SPECTRE JSON property names.
    # Venus (HD9875/HD9876/HD9880) uses different keys than
    # SPECTRE (HD9280/HD9285/HD9255). Format: {venus_key: spectre_key}
    _VENUS_KEY_MAP = {
        "disp_time": "cur_time",
        "total_time": "time",
        "method": "preset",
        "current_temp": "cur_temp",
        # Venus abbreviated boolean property names
        # (from APK VenusStatusPortProperties @SerializedName)
        "probe_unpl": "probe_unplugged",
        "probe_rqrd": "probe_required",
        "drw_opn": "drawer_open",
        "prev_stat": "prev_status",
    }
    _SPECTRE_KEY_MAP = {v: k for k, v in _VENUS_KEY_MAP.items()}

    @staticmethod
    def _normalize_venus_response(data: dict[str, Any]) -> dict[str, Any]:
        """Normalize Venus response keys to SPECTRE format for reading."""
        for venus_key, spectre_key in PhilipsLocalAPI._VENUS_KEY_MAP.items():
            if venus_key in data and spectre_key not in data:
                data[spectre_key] = data[venus_key]
        return data

    @staticmethod
    def _normalize_venus_command(data: dict[str, Any]) -> dict[str, Any]:
        """Normalize SPECTRE command keys to Venus format for writing."""
        for spectre_key, venus_key in PhilipsLocalAPI._SPECTRE_KEY_MAP.items():
            if spectre_key in data:
                data[venus_key] = data.pop(spectre_key)
        return data

    @staticmethod
    def _port_for_model(device: LocalDeviceInfo) -> str | None:
        """Return the airfryer port for a known model, or None.

        Checks model_name, model_number, and friendly_name since different
        discovery methods populate different fields.
        """
        candidates = " ".join(
            filter(
                None,
                [device.model_name, device.model_number, device.friendly_name],
            )
        ).upper()
        for prefix, port in _MODEL_PORT_MAP.items():
            if prefix in candidates:
                return port
        return None

    async def get_airfryer_status(
        self, device: LocalDeviceInfo
    ) -> dict[str, Any] | None:
        """Get airfryer status.

        Uses cached port, or model-based lookup, or probes all known ports.
        Normalizes Venus responses to match SPECTRE property names.
        """
        if isinstance(device.airfryer_port, str):
            ports_to_try = [device.airfryer_port]
        else:
            # Try model-based lookup first to avoid probing
            model_port = self._port_for_model(device)
            if model_port:
                ports_to_try = [model_port]
            else:
                ports_to_try = [
                    PORT_AIRFRYER,
                    PORT_VENUSAF,
                    PORT_VENUS1AF,
                    PORT_NUTRIMAX,
                    PORT_HERMESAC,
                ]

        for port in ports_to_try:
            result = await self._request(device, port)
            if result is not None:
                if not isinstance(device.airfryer_port, str):
                    _LOGGER.info(
                        "Device %s responds on airfryer port '%s'",
                        device.ip_address,
                        port,
                    )
                device.airfryer_port = port
                if port in VENUS_STYLE_PORTS:
                    result = self._normalize_venus_response(result)
                return result

        return None

    async def get_device_current_state(
        self, device: LocalDeviceInfo
    ) -> dict[str, Any] | None:
        """Get device current state (Venus devices only).

        Returns voltage, current_temp, current_temp_probe.
        """
        result = await self._request(device, PORT_DEVCURRSTATE)
        if result is not None:
            result = self._normalize_venus_response(result)
        return result

    async def get_autocook_program(
        self, device: LocalDeviceInfo
    ) -> dict[str, Any] | None:
        """Get auto cook program (Venus devices only)."""
        result = await self._request(device, PORT_AUTOCOOK)
        _LOGGER.debug("Autocook program: %s", result)
        return result

    async def get_recipe_status(self, device: LocalDeviceInfo) -> dict[str, Any] | None:
        """Get recipe status (Venus devices only)."""
        result = await self._request(device, PORT_RECIPE)
        _LOGGER.debug("Recipe status: %s", result)
        return result

    async def airfryer_start_cooking(
        self,
        device: LocalDeviceInfo,
        preheat: bool = False,
        temp: int | None = None,
        time_seconds: int | None = None,
    ) -> bool:
        """Start cooking on the airfryer.

        Venus uses a 3-step flow: precook → settings → cooking.
        SPECTRE uses a single cooking command.
        """
        port = self._airfryer_port(device)

        if port in VENUS_STYLE_PORTS:
            # Venus 3-step start: precook → settings → cooking
            precook: dict[str, Any] = {
                "status": AIRFRYER_STATUS_PRECOOK,
                "probe_required": False,
                "method": 0,
                "temp_unit": False,
            }
            await self._request(device, port, method="PUT", data=precook)

            if temp is not None or time_seconds is not None:
                settings: dict[str, Any] = {}
                if temp is not None:
                    settings["temp"] = temp
                if time_seconds is not None:
                    settings["total_time"] = time_seconds
                await self._request(device, port, method="PUT", data=settings)

            start: dict[str, Any] = {"status": AIRFRYER_STATUS_COOKING}
            if preheat:
                start["preheat"] = True
            result = await self._request(device, port, method="PUT", data=start)
            return result is not None

        # SPECTRE: single-step start
        data: dict[str, Any] = {"status": AIRFRYER_STATUS_COOKING}
        if preheat:
            data["preheat"] = True
        result = await self._request(device, port, method="PUT", data=data)
        return result is not None

    async def airfryer_pause(self, device: LocalDeviceInfo) -> bool:
        """Pause the airfryer."""
        port = self._airfryer_port(device)
        data = {"status": AIRFRYER_STATUS_PAUSED}
        result = await self._request(device, port, method="PUT", data=data)
        return result is not None

    async def set_autocook_program(self, device: LocalDeviceInfo, uuid: str) -> bool:
        """Select a built-in Auto-Cook program by UUID (Venus local HTTP).

        APK ConnectKitAutoCookBridge.b() sends only the UUID; the device
        fills in the rest of the program parameters locally.
        """
        if not uuid:
            return False
        result = await self._request(
            device, PORT_AUTOCOOK, method="PUT", data={"UUID": uuid}
        )
        return result is not None

    async def airfryer_stop(self, device: LocalDeviceInfo) -> bool:
        """Stop the airfryer and return to standby.

        Venus uses pause → mainmenu. SPECTRE uses standby.
        """
        port = self._airfryer_port(device)
        if port in VENUS_STYLE_PORTS:
            await self._request(
                device, port, method="PUT", data={"status": AIRFRYER_STATUS_PAUSED}
            )
            result = await self._request(
                device, port, method="PUT", data={"status": "mainmenu"}
            )
            return result is not None
        data = {"status": AIRFRYER_STATUS_STANDBY}
        result = await self._request(device, port, method="PUT", data=data)
        return result is not None

    async def airfryer_set_settings(
        self,
        device: LocalDeviceInfo,
        temp: int | None = None,
        time_seconds: int | None = None,
        temp_unit_fahrenheit: bool = False,
        preset: int | None = None,
        airspeed: int | None = None,
        probe_temp: int | None = None,
    ) -> bool:
        """Set airfryer cooking settings.

        Args:
            temp: Target temperature
            time_seconds: Total cooking time in seconds
            temp_unit_fahrenheit: True for Fahrenheit, False for Celsius
            preset: Preset program number
            airspeed: Air speed (1=LOW, 2=HIGH, Venus only)
            probe_temp: Target probe temperature (Venus only)
        """
        port = self._airfryer_port(device)
        data: dict[str, Any] = {"status": AIRFRYER_STATUS_SETTING}
        if temp is not None:
            data["temp"] = temp
        if time_seconds is not None:
            data["time"] = time_seconds
        if airspeed is not None:
            data["airspeed"] = airspeed
        if probe_temp is not None:
            data["temp_probe"] = probe_temp
            data["probe_required"] = True
        if preset is not None:
            data["preset"] = preset
        if port in VENUS_STYLE_PORTS:
            # Venus: temp_unit True=Fahrenheit, False=Celsius (standard)
            data["temp_unit"] = temp_unit_fahrenheit
            data = self._normalize_venus_command(data)
        else:
            # SPECTRE: temp_unit True=Celsius, False=Fahrenheit (inverted)
            data["temp_unit"] = not temp_unit_fahrenheit
        result = await self._request(device, port, method="PUT", data=data)
        return result is not None

    async def airfryer_keep_warm(
        self,
        device: LocalDeviceInfo,
        time_seconds: int = 3600,
        temp: int = 65,
    ) -> bool:
        """Start keep warm mode on the airfryer.

        Args:
            time_seconds: Keep warm duration in seconds (default 1 hour)
            temp: Keep warm temperature in Celsius (default 65, SPECTRE only)
        """
        port = self._airfryer_port(device)
        if port in VENUS_STYLE_PORTS:
            # Keep warm method ID varies by device architecture
            if port == PORT_NUTRIMAX:
                keep_warm_method = 9
            elif port == PORT_HERMESAC:
                keep_warm_method = 50
            else:
                keep_warm_method = 2  # Venus airfryers
            data: dict[str, Any] = {
                "total_time": time_seconds,
                "method": keep_warm_method,
                "status": AIRFRYER_STATUS_MAINTAIN,
            }
        else:
            # SPECTRE: preset=8 (KEEP_WARM), status="cooking"
            data = {
                "time": time_seconds,
                "temp": temp,
                "preset": 8,
                "status": AIRFRYER_STATUS_COOKING,
            }
        result = await self._request(device, port, method="PUT", data=data)
        return result is not None

    async def airfryer_update_settings(
        self,
        device: LocalDeviceInfo,
        temp: int | None = None,
        time_seconds: int | None = None,
        temp_unit_fahrenheit: bool = False,
        is_cooking: bool = False,
    ) -> bool:
        """Update airfryer settings without changing cooking state.

        For SPECTRE: sends only temp/time/temp_unit (no status field).
        For Venus while cooking: pause → set values → resume.
        For Venus while not cooking: send values directly.
        """
        port = self._airfryer_port(device)
        data: dict[str, Any] = {}
        if temp is not None:
            data["temp"] = temp
        if time_seconds is not None:
            data["time"] = time_seconds

        if port in VENUS_STYLE_PORTS:
            data["temp_unit"] = temp_unit_fahrenheit
            data = self._normalize_venus_command(data)
            if is_cooking:
                # Venus: pause → set → resume (always try to resume)
                await self._request(
                    device,
                    port,
                    method="PUT",
                    data={"status": AIRFRYER_STATUS_PAUSED},
                )
                try:
                    await self._request(device, port, method="PUT", data=data)
                finally:
                    result = await self._request(
                        device,
                        port,
                        method="PUT",
                        data={"status": AIRFRYER_STATUS_COOKING},
                    )
                return result is not None
        else:
            data["temp_unit"] = not temp_unit_fahrenheit

        result = await self._request(device, port, method="PUT", data=data)
        return result is not None

    async def set_status_property(
        self,
        device: LocalDeviceInfo,
        key: str,
        value: Any,
    ) -> bool:
        """Set a single property on the device status port."""
        data = {key: value}
        result = await self._request(device, PORT_STATUS, method="PUT", data=data)
        return result is not None

    async def _probe_request(
        self,
        device: LocalDeviceInfo,
        port_name: str,
        product_id: int | None = None,
    ) -> tuple[dict[str, Any] | None, int | None]:
        """Make a probe request, returning (data, status_code).

        Unlike _request, this returns the HTTP status code so the caller
        can distinguish 401 (device exists, needs auth) from connection errors.
        """
        session = await self._get_session()
        url = self._build_url(device, port_name, product_id)
        headers = {"Content-Type": "application/json"}

        try:
            async with session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    try:
                        return await resp.json(), resp.status
                    except Exception:
                        return None, resp.status
                return None, resp.status
        except aiohttp.ClientError as err:
            _LOGGER.debug("Probe connection failed for %s: %s", url, err)
            return None, None
        except Exception as err:
            _LOGGER.debug("Probe unexpected error for %s: %s", url, err)
            return None, None

    async def _probe_with_protocol(
        self, device: LocalDeviceInfo
    ) -> LocalDeviceInfo | None:
        """Try probing a device with its current protocol setting.

        Any HTTP response (even 401/501) means the device is reachable.
        Returns the device if found, None otherwise.
        """
        ip_address = device.ip_address
        protocol = "HTTPS" if device.use_https else "HTTP"

        # Try device info endpoint with product_id 1 and 0
        for product_id in (1, 0):
            info, status = await self._probe_request(
                device, PORT_DEVICE, product_id=product_id
            )
            if info:
                _LOGGER.info(
                    "Probed device at %s via %s (product %d): %s",
                    ip_address,
                    protocol,
                    product_id,
                    info,
                )
                device.cpp_id = info.get("DeviceId", info.get("cppId", ""))
                device.model_name = info.get("modelid", info.get("ModelName", ""))
                device.model_number = info.get("type", info.get("ModelNumber", ""))
                device.friendly_name = info.get("name", info.get("FriendlyName", ""))
                return device
            if status is not None:
                # Any HTTP response means the device is reachable
                _LOGGER.info(
                    "Device at %s responded with %s via %s on product %d",
                    ip_address,
                    status,
                    protocol,
                    product_id,
                )
                return device

        # Try status endpoint as fallback
        status_data, status = await self._probe_request(device, PORT_STATUS)
        if status_data:
            _LOGGER.info(
                "Got status from %s via %s: %s", ip_address, protocol, status_data
            )
            return device
        if status is not None:
            _LOGGER.info(
                "Device at %s responded with %s via %s on status",
                ip_address,
                status,
                protocol,
            )
            return device

        return None

    async def probe_device(self, ip_address: str) -> LocalDeviceInfo | None:
        """Probe a device at the given IP to get its information.

        Tries HTTPS (port 443) and HTTP (port 80) concurrently.
        Tries multiple product IDs and endpoints. A 401 response is treated
        as "device found but needs authentication" rather than a failure.
        """
        https_device = LocalDeviceInfo(
            ip_address=ip_address,
            cpp_id="",
            use_https=True,
        )
        http_device = LocalDeviceInfo(
            ip_address=ip_address,
            cpp_id="",
            use_https=False,
        )

        https_task = asyncio.ensure_future(self._probe_with_protocol(https_device))
        http_task = asyncio.ensure_future(self._probe_with_protocol(http_device))

        done, pending = await asyncio.wait(
            [https_task, http_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Check completed tasks for a result
        for task in done:
            result = task.result()
            if result:
                for p in pending:
                    p.cancel()
                return result

        # First completed task returned None, wait for the other
        if pending:
            remaining = await asyncio.gather(*pending, return_exceptions=True)
            for item in remaining:
                if isinstance(item, LocalDeviceInfo):
                    return item

        return None

    async def get_full_state(self, device: LocalDeviceInfo) -> LocalDeviceState | None:
        """Get the full state of a device."""
        state = LocalDeviceState(device_info=device)
        got_data = False

        # Try airfryer endpoint (skip if device is known to be non-airfryer)
        # Skip probing if we have no auth material at all (no client_id/secret),
        # since unauthenticated requests hang on some devices.
        airfryer = None
        has_auth = device.credentials or (device.client_id and device.client_secret)
        if device.airfryer_port is not False and has_auth:
            airfryer = await self.get_airfryer_status(device)
            if airfryer:
                got_data = True
                state.properties["airfryer"] = airfryer
                af_status = airfryer.get("status", "")
                state.power_on = af_status in (
                    AIRFRYER_STATUS_COOKING,
                    AIRFRYER_STATUS_PAUSED,
                    AIRFRYER_STATUS_SETTING,
                    AIRFRYER_STATUS_PRECOOK,
                    AIRFRYER_STATUS_PARASETTING,
                    AIRFRYER_STATUS_MAINTAIN,
                    AIRFRYER_STATUS_USER_ACTION,
                    AIRFRYER_STATUS_IDLE,
                    AIRFRYER_STATUS_FINISH,
                )
                _LOGGER.debug("Airfryer status: %s", airfryer)

                # Fetch Venus-specific endpoints
                if device.airfryer_port in VENUS_STYLE_PORTS:
                    dev_state = await self.get_device_current_state(device)
                    if dev_state:
                        for key, value in dev_state.items():
                            if key not in airfryer:
                                airfryer[key] = value
                    autocook = await self.get_autocook_program(device)
                    if autocook:
                        state.properties["autocook"] = autocook
                    recipe = await self.get_recipe_status(device)
                    if recipe:
                        state.properties["recipe"] = recipe
            elif device.airfryer_port is None and has_auth:
                # No port responded after successful auth; not an airfryer.
                # Only cache this if we have auth material (credentials or
                # client_id/secret), otherwise timeouts could be from
                # pre-auth state.
                device.airfryer_port = False

        # Get status/air/filter for non-airfryer devices (air purifiers).
        # Airfryers don't have these endpoints (return 501), so skip to
        # avoid noisy warnings every poll cycle.
        if not airfryer:
            status = await self.get_status(device)
            if status:
                got_data = True
                state.power_on = status.get("pwr") == "1"
                state.properties.update(status)

            air = await self.get_air_quality(device)
            if air:
                got_data = True
                state.properties.update(air)

            filters = await self.get_filter_status(device)
            if filters:
                got_data = True
                state.properties.update(filters)

        # Espresso machines (EP/SM models) expose their state on the
        # "machinestatus" and "configuration" ports. These are queried for
        # non-airfryer devices; non-espresso devices simply return 422 and are
        # ignored. The responses populate the ESPRESSO_SENSORS descriptions
        # (nested_key "machinestatus"/"configuration"). Confirmed on EP2520.
        if not airfryer:
            for espresso_port in ("machinestatus", "configuration"):
                espresso_data = await self._request(device, espresso_port)
                if espresso_data:
                    got_data = True
                    state.properties[espresso_port] = espresso_data
                    if espresso_port == "machinestatus":
                        state.power_on = (
                            state.power_on
                            or espresso_data.get("mainstate", 0) != 0
                        )

        # Get firmware version info
        firmware = await self.get_firmware_info(device)
        if firmware:
            got_data = True
            state.properties["firmware"] = firmware

        if not got_data:
            return None

        return state

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

import base64
import hashlib
import json
import logging
import secrets

from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

import aiohttp

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

_LOGGER = logging.getLogger(__name__)

# Default ports/endpoints for air purifiers
DEFAULT_PRODUCT_ID = 1
DEFAULT_PROTOCOL_VERSION = 1

# Known port names for different device features
PORT_STATUS = "status"
PORT_CONTROL = "control"
PORT_AIR = "air"
PORT_FLTSTS = "fltsts"  # Filter status
PORT_DEVICE = "device"
PORT_SECURITY = "security"
PORT_FIRMWARE = "firmware"

# Airfryer-specific ports per device architecture
PORT_AIRFRYER = "airfryer"  # SPECTRE (HD9280, HD9285, HD9255)
PORT_VENUSAF = "venusaf"  # VENUS 2 (HD9880)
PORT_VENUS1AF = "venus1af"  # VENUS 1 (HD9875, HD9876)
# VENUS additional endpoints
PORT_AUTOCOOK = "autocookprogram"  # VENUS auto cook program
PORT_RECIPE = "recipe"  # VENUS recipe status
# VENUS device current state (voltage, internal temp)
PORT_DEVCURRSTATE = "devcurrstate"

# Airfryer status values
AIRFRYER_STATUS_STANDBY = "standby"
AIRFRYER_STATUS_IDLE = "idle"
AIRFRYER_STATUS_SETTING = "setting"
AIRFRYER_STATUS_COOKING = "cooking"
AIRFRYER_STATUS_PAUSED = "pause"
AIRFRYER_STATUS_FINISH = "finish"
AIRFRYER_STATUS_PAIRING = "pairing"
# Venus-specific status values
AIRFRYER_STATUS_PRECOOK = "precook"
AIRFRYER_STATUS_PARASETTING = "parasetting"


@dataclass
class LocalDeviceInfo:
    """Information about a locally discovered device."""

    ip_address: str
    cpp_id: str  # Cloud Platform ID - unique device identifier
    friendly_name: str = ""
    model_name: str = ""
    model_number: str = ""
    serial_number: str = ""
    boot_id: str = ""
    protocol_version: int = 1
    product_id: int = 1
    # Connection protocol
    use_https: bool = True  # False for devices that use HTTP (e.g., HD9285)
    # Authentication credentials (obtained during pairing)
    client_id: str | None = None
    client_secret: str | None = None
    credentials: str | None = None  # Cached auth header value
    # AES encryption key (hex string) for HTTP devices - fetched from /security
    encryption_key: str | None = None
    # Discovered airfryer port name (cached after first successful request)
    # None = not yet probed, False = not an airfryer, str = port name
    airfryer_port: str | bool | None = None


@dataclass
class LocalDeviceState:
    """State of a locally controlled device."""

    device_info: LocalDeviceInfo
    power_on: bool = False
    connection_state: str = "connected"
    properties: dict[str, Any] = field(default_factory=dict)


class PhilipsCondorAuth:
    """Implements the PhilipsCondor authentication scheme."""

    SCHEME = "PhilipsCondor"
    # Alternative scheme names used by different firmware versions
    SCHEME_VARIANTS = ["PhilipsCondor", "PHILIPS-Condor", "Philips-Condor"]
    # Accept challenge sizes between 8 and 64 bytes (different firmware versions)
    MIN_CHALLENGE_SIZE = 8
    MAX_CHALLENGE_SIZE = 64

    @staticmethod
    def create_credentials(
        challenge_b64: str, client_id: str, client_secret: str
    ) -> str | None:
        """Create authentication credentials from challenge.

        The PhilipsCondor scheme works as follows:
        1. Server sends challenge in WWW-Authenticate header
        2. Client decodes challenge, client_id, and client_secret from base64
        3. Client creates SHA256 hash of (challenge + client_id + client_secret)
        4. Client sends back: "<scheme> " + base64(client_id + hash)

        Important: The response scheme must match what the device sent.
        """
        try:
            # Extract and remember the scheme from the challenge
            challenge_clean = challenge_b64.strip()
            response_scheme = PhilipsCondorAuth.SCHEME  # Default
            for variant in PhilipsCondorAuth.SCHEME_VARIANTS:
                if challenge_clean.lower().startswith(variant.lower()):
                    # Use the exact scheme the device sent (preserve case and format)
                    response_scheme = challenge_clean[: len(variant)]
                    challenge_clean = challenge_clean[len(variant) :].strip()
                    break

            _LOGGER.debug("Using scheme: %s", response_scheme)
            _LOGGER.debug("Challenge (cleaned): %s", challenge_clean)

            challenge = base64.b64decode(challenge_clean)
            if not (
                PhilipsCondorAuth.MIN_CHALLENGE_SIZE
                <= len(challenge)
                <= PhilipsCondorAuth.MAX_CHALLENGE_SIZE
            ):
                _LOGGER.warning(
                    "Invalid challenge size: %d (expected %d-%d)",
                    len(challenge),
                    PhilipsCondorAuth.MIN_CHALLENGE_SIZE,
                    PhilipsCondorAuth.MAX_CHALLENGE_SIZE,
                )
                return None

            _LOGGER.debug(
                "Challenge size: %d bytes, hex: %s", len(challenge), challenge.hex()
            )

            client_id_bytes = base64.b64decode(client_id)
            client_secret_bytes = base64.b64decode(client_secret)

            _LOGGER.debug("Client ID size: %d bytes", len(client_id_bytes))
            _LOGGER.debug("Client secret size: %d bytes", len(client_secret_bytes))

            # Create hash: SHA256(challenge + client_id + client_secret)
            data = challenge + client_id_bytes + client_secret_bytes
            hash_result = hashlib.sha256(data).digest()

            _LOGGER.debug("Hash result (hex): %s", hash_result.hex())

            # Create response: base64(client_id + hash)
            response_bytes = client_id_bytes + hash_result
            response_b64 = base64.b64encode(response_bytes).decode("utf-8")

            _LOGGER.debug("Response: %s %s", response_scheme, response_b64)

            return f"{response_scheme} {response_b64}"
        except Exception as err:
            _LOGGER.error("Failed to create credentials: %s", err)
            return None


class PhilipsCrypto:
    """AES/CBC/PKCS7 encryption for HTTP devices.

    From APK analysis: devices with https=0 encrypt response payloads
    using AES-128-CBC with PKCS7 padding and a hardcoded zero IV.
    The encryption key is a hex string fetched from the /security endpoint.
    Responses are base64-encoded ciphertext.
    """

    # 16 bytes of zeros as IV (hardcoded in APK)
    _ZERO_IV = b"\x00" * 16

    @staticmethod
    def _hex_to_key(hex_key: str) -> bytes:
        """Convert hex string encryption key to 16-byte AES key."""
        key_bytes = bytes.fromhex(hex_key)
        # APK handles leading zero: if 17 bytes, strip first
        if len(key_bytes) == 17 and key_bytes[0] == 0:
            key_bytes = key_bytes[1:]
        if len(key_bytes) != 16:
            raise ValueError(
                f"Invalid AES key length: {len(key_bytes)} bytes (expected 16)"
            )
        return key_bytes

    @staticmethod
    def decrypt(data_b64: str, hex_key: str) -> str | None:
        """Decrypt a base64-encoded AES/CBC/PKCS7 payload.

        Args:
            data_b64: Base64-encoded ciphertext from device response
            hex_key: Encryption key as hex string

        Returns:
            Decrypted JSON string, or None on failure.
        """
        try:
            key = PhilipsCrypto._hex_to_key(hex_key)
            ciphertext = base64.b64decode(data_b64.strip())

            cipher = Cipher(algorithms.AES(key), modes.CBC(PhilipsCrypto._ZERO_IV))
            decryptor = cipher.decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()

            # Remove PKCS7 padding
            unpadder = PKCS7(128).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()

            return plaintext.decode("utf-8")
        except Exception as err:
            _LOGGER.error("AES decryption failed: %s", err)
            return None

    @staticmethod
    def encrypt(data: str, hex_key: str) -> str | None:
        """Encrypt a JSON string with AES/CBC/PKCS7.

        Args:
            data: Plain JSON string to encrypt
            hex_key: Encryption key as hex string

        Returns:
            Base64-encoded ciphertext, or None on failure.
        """
        try:
            key = PhilipsCrypto._hex_to_key(hex_key)
            plaintext = data.encode("utf-8")

            # Add PKCS7 padding
            padder = PKCS7(128).padder()
            padded = padder.update(plaintext) + padder.finalize()

            cipher = Cipher(algorithms.AES(key), modes.CBC(PhilipsCrypto._ZERO_IV))
            encryptor = cipher.encryptor()
            ciphertext = encryptor.update(padded) + encryptor.finalize()

            return base64.b64encode(ciphertext).decode("utf-8")
        except Exception as err:
            _LOGGER.error("AES encryption failed: %s", err)
            return None


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

    def _build_url(self, device: LocalDeviceInfo, port_name: str) -> str:
        """Build URL for device endpoint."""
        return (
            f"{self._scheme(device)}://{device.ip_address}/di/v{device.protocol_version}"
            f"/products/{device.product_id}/{port_name}"
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
    ) -> dict[str, Any] | None:
        """Make a request to the device."""
        session = await self._get_session()
        url = self._build_url(device, port_name)

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
                            device, port_name, method, data, _retry=False
                        )
                    return result
            elif method == "PUT":
                async with session.put(
                    url, headers=headers, data=body, timeout=REQUEST_TIMEOUT
                ) as resp:
                    result, should_retry = await self._handle_response(device, resp)
                    if should_retry and _retry:
                        return await self._request(
                            device, port_name, method, data, _retry=False
                        )
                    return result
            elif method == "POST":
                async with session.post(
                    url, headers=headers, data=body, timeout=REQUEST_TIMEOUT
                ) as resp:
                    result, should_retry = await self._handle_response(device, resp)
                    if should_retry and _retry:
                        return await self._request(
                            device, port_name, method, data, _retry=False
                        )
                    return result

        except aiohttp.ClientError as err:
            _LOGGER.error("Request failed for %s: %s", url, err)
            return None
        except Exception as err:
            _LOGGER.error("Unexpected error for %s: %s", url, err)
            return None

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
        saved_product_id = device.product_id
        device.product_id = 0
        try:
            result = await self._request(device, PORT_SECURITY)
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
                _LOGGER.warning(
                    "Failed to fetch encryption key from %s", device.ip_address
                )
        finally:
            device.product_id = saved_product_id
        return None

    async def get_firmware_info(self, device: LocalDeviceInfo) -> dict[str, Any] | None:
        """Get firmware version info from device.

        The firmware port uses product_id=0, so we temporarily switch.
        Returns dict with 'version' (installed) and optionally 'upgrade' (available).
        """
        saved_product_id = device.product_id
        device.product_id = 0
        try:
            return await self._request(device, PORT_FIRMWARE)
        finally:
            device.product_id = saved_product_id

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

    async def get_airfryer_status(
        self, device: LocalDeviceInfo
    ) -> dict[str, Any] | None:
        """Get airfryer status.

        Tries the cached port first, then probes all known airfryer ports
        (SPECTRE, VENUS 2, VENUS 1) until one responds. Normalizes Venus
        responses to match SPECTRE property names.
        """
        if isinstance(device.airfryer_port, str):
            ports_to_try = [device.airfryer_port]
        else:
            ports_to_try = [PORT_AIRFRYER, PORT_VENUSAF, PORT_VENUS1AF]

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
                if port in (PORT_VENUSAF, PORT_VENUS1AF):
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
        return await self._request(device, PORT_AUTOCOOK)

    async def get_recipe_status(self, device: LocalDeviceInfo) -> dict[str, Any] | None:
        """Get recipe status (Venus devices only)."""
        return await self._request(device, PORT_RECIPE)

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

        if port in (PORT_VENUSAF, PORT_VENUS1AF):
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

    async def airfryer_stop(self, device: LocalDeviceInfo) -> bool:
        """Stop the airfryer and return to standby.

        Venus uses pause → mainmenu. SPECTRE uses standby.
        """
        port = self._airfryer_port(device)
        if port in (PORT_VENUSAF, PORT_VENUS1AF):
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
            airspeed: Air speed (0=LOW, 1=HIGH, Venus only)
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
        if port in (PORT_VENUSAF, PORT_VENUS1AF):
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
        if port in (PORT_VENUSAF, PORT_VENUS1AF):
            # Venus: method=2 (KEEP_WARM), status="maintain"
            data: dict[str, Any] = {
                "total_time": time_seconds,
                "method": 2,
                "status": "maintain",
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

        if port in (PORT_VENUSAF, PORT_VENUS1AF):
            data["temp_unit"] = temp_unit_fahrenheit
            data = self._normalize_venus_command(data)
            if is_cooking:
                # Venus: pause → set → resume
                await self._request(
                    device,
                    port,
                    method="PUT",
                    data={"status": AIRFRYER_STATUS_PAUSED},
                )
                await self._request(device, port, method="PUT", data=data)
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

    async def send_wifi_credentials(
        self,
        device: LocalDeviceInfo,
        ssid: str,
        password: str,
    ) -> bool:
        """Send Wi-Fi credentials to device during setup.

        This is used when the device is in AP mode after factory reset.
        The device will then connect to the specified Wi-Fi network.
        """
        session = await self._get_session()

        # Wi-Fi port is on product 0
        url = f"{self._scheme(device)}://{device.ip_address}/di/v{device.protocol_version}/products/0/wifi"

        headers = {"Content-Type": "application/json"}
        data = {"ssid": ssid, "password": password}

        try:
            _LOGGER.info("Sending Wi-Fi credentials to %s", url)
            async with session.put(
                url, headers=headers, json=data, timeout=REQUEST_TIMEOUT
            ) as resp:
                text = await resp.text()
                _LOGGER.info(
                    "Wi-Fi credentials response (%s): %s", resp.status, text[:200]
                )

                if resp.status == 200:
                    _LOGGER.info("Wi-Fi credentials sent successfully")
                    return True
                else:
                    _LOGGER.error("Failed to send Wi-Fi credentials: %s", resp.status)
                    return False
        except Exception as err:
            _LOGGER.exception("Error sending Wi-Fi credentials: %s", err)
            return False

    async def try_clear_pairing(self, device: LocalDeviceInfo) -> tuple[bool, str]:
        """Try to clear existing pairing on the device using DELETE.

        This may not work on all devices but is worth trying before factory reset.
        """
        session = await self._get_session()
        url = f"{self._scheme(device)}://{device.ip_address}/auth/v{device.protocol_version}/"

        try:
            _LOGGER.info("Attempting to clear pairing via DELETE %s", url)
            async with session.delete(url, timeout=REQUEST_TIMEOUT) as resp:
                await resp.text()
                _LOGGER.debug("DELETE response: %s", resp.status)

                if resp.status == 200:
                    return True, "Pairing cleared successfully"
                elif resp.status == 405:
                    return False, "DELETE not supported on this device"
                else:
                    return False, f"Failed to clear pairing: {resp.status}"
        except Exception as err:
            return False, f"Error: {err}"

    async def pair_device(self, device: LocalDeviceInfo) -> tuple[bool, str]:
        """Pair with a device to obtain credentials.

        The pairing flow is two-step:
        1. First request: PUT /auth/v1/ with {"id": "<client_id>"}
           Response: {"authenticated": false, "seed": "..."}
        2. Second request: PUT /auth/v1/ with {"id": "<client_id>", "secret": "<computed_secret>"}
           Response: {"authenticated": true, "secret": "..."}

        The secret is computed as: base64(SHA256(base64decode(seed) + base64decode(client_id)))

        Returns (success, message) tuple.
        """
        session = await self._get_session()

        # Generate a new client_id if not already set
        if not device.client_id:
            # Generate 128-bit (16 byte) random key, base64 encoded
            random_bytes = secrets.token_bytes(16)
            device.client_id = base64.b64encode(random_bytes).decode("utf-8")

        headers = {
            "Content-Type": "application/json",
        }

        # Use PUT to /auth/v{version}/ - this is the correct endpoint based on APK analysis
        url = f"{self._scheme(device)}://{device.ip_address}/auth/v{device.protocol_version}/"

        try:
            # Step 1: Initial request to get seed
            data = {"id": device.client_id}
            _LOGGER.debug("Pairing step 1: PUT %s", url)

            async with session.put(
                url, headers=headers, json=data, timeout=REQUEST_TIMEOUT
            ) as resp:
                text = await resp.text()
                _LOGGER.debug("Step 1 response: %s", resp.status)

                if resp.status != 200:
                    return (
                        False,
                        f"Step 1 failed with status {resp.status}: {text[:200]}",
                    )

                try:
                    result = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    return False, f"Invalid JSON response: {text[:200]}"

                authenticated = result.get("authenticated", False)
                seed = result.get("seed")
                secret = result.get("secret")

                # Check if already authenticated (device was already paired)
                if authenticated and secret:
                    device.client_secret = secret
                    device.credentials = None
                    _LOGGER.info("Pairing successful (already authenticated)")
                    return True, "Pairing successful"

                if authenticated and not secret:
                    return False, "Device authenticated but didn't provide secret"

                if not seed:
                    return False, (
                        "Device response was unclear. "
                        "Try factory reset or check your device manual."
                    )

                # The device returned authenticated=false with a seed
                # This means the device is NOT in pairing mode - it's already
                # paired with another client and is challenging us to prove
                # we have the existing credentials.
                #
                # We'll try the challenge-response anyway, but this likely
                # means the device needs to be factory reset.
                _LOGGER.info(
                    "Device returned a seed challenge - it may already be paired. "
                    "Attempting challenge-response anyway..."
                )

            # Step 2: Compute evidence from seed and send back
            # Based on PHILIPS-Condor auth scheme:
            # evidence = base64(client_id_bytes + SHA256(seed_bytes + client_id_bytes))
            try:
                # Decode seed and client_id from base64
                seed_bytes = base64.b64decode(seed)
                client_id_bytes = base64.b64decode(device.client_id)

                # Compute hash: SHA256(seed + client_id)
                hash_input = seed_bytes + client_id_bytes
                hash_result = hashlib.sha256(hash_input).digest()

                # Evidence format: base64(client_id_bytes + hash)
                evidence_bytes = client_id_bytes + hash_result
                computed_evidence = base64.b64encode(evidence_bytes).decode("utf-8")

                _LOGGER.info("Computed evidence, sending step 2...")
            except Exception as err:
                return False, f"Failed to compute evidence: {err}"

            # Step 2: Send back with computed evidence
            # Try both "secret" and "key" field names
            data = {"id": device.client_id, "key": computed_evidence}
            _LOGGER.info("Pairing step 2: PUT %s with key", url)

            async with session.put(
                url, headers=headers, json=data, timeout=REQUEST_TIMEOUT
            ) as resp:
                text = await resp.text()
                _LOGGER.debug("Step 2 response: %s", resp.status)

                if resp.status != 200:
                    # If step 2 failed, the device is likely already paired
                    # with another client and needs factory reset
                    return False, (
                        f"Device rejected pairing (status {resp.status}). "
                        "The device is likely already paired with the HomeID app. "
                        "To pair with Home Assistant, you need to factory reset the device. "
                        "For HD9280 Airfryer: unplug, hold power button while plugging back in."
                    )

                try:
                    result = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    return False, f"Invalid JSON response: {text[:200]}"

                authenticated = result.get("authenticated", False)
                final_secret = result.get("secret")
                new_seed = result.get("seed")

                if authenticated and final_secret:
                    device.client_secret = final_secret
                    device.credentials = None
                    _LOGGER.info("Pairing successful for device %s", device.ip_address)
                    return True, "Pairing successful"
                elif authenticated:
                    # Device says authenticated but no secret - try using our evidence
                    # This shouldn't normally happen but handle it anyway
                    device.client_secret = computed_evidence
                    device.credentials = None
                    _LOGGER.info(
                        "Pairing successful (using computed evidence as secret)"
                    )
                    return True, "Pairing successful"
                elif new_seed:
                    # Device returned another seed - our evidence was wrong
                    # This means the device is already paired with different credentials
                    return False, (
                        "Device rejected our credentials and issued a new challenge. "
                        "The device is already paired with the HomeID app. "
                        "To pair with Home Assistant, factory reset the device first."
                    )
                else:
                    return False, f"Step 2 authentication failed: {result}"

        except aiohttp.ClientError as err:
            return False, f"Connection error: {err}"
        except Exception as err:
            _LOGGER.exception("Unexpected error during pairing")
            return False, f"Unexpected error: {err}"

    async def _probe_request(
        self, device: LocalDeviceInfo, port_name: str
    ) -> tuple[dict[str, Any] | None, int | None]:
        """Make a probe request, returning (data, status_code).

        Unlike _request, this returns the HTTP status code so the caller
        can distinguish 401 (device exists, needs auth) from connection errors.
        """
        session = await self._get_session()
        url = self._build_url(device, port_name)
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
            device.product_id = product_id
            info, status = await self._probe_request(device, PORT_DEVICE)
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
                device.product_id = DEFAULT_PRODUCT_ID
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
                device.product_id = DEFAULT_PRODUCT_ID
                return device

        # Try status endpoint as fallback
        device.product_id = DEFAULT_PRODUCT_ID
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

        Tries HTTPS first (port 443), then falls back to HTTP (port 80).
        Tries multiple product IDs and endpoints. A 401 response is treated
        as "device found but needs authentication" rather than a failure.
        """
        device = LocalDeviceInfo(
            ip_address=ip_address,
            cpp_id="",
            use_https=True,
        )

        # Try HTTPS first
        result = await self._probe_with_protocol(device)
        if result:
            return result

        # Fall back to HTTP
        _LOGGER.info("HTTPS probe failed for %s, trying HTTP", ip_address)
        device.use_https = False
        return await self._probe_with_protocol(device)

    async def get_full_state(self, device: LocalDeviceInfo) -> LocalDeviceState | None:
        """Get the full state of a device."""
        state = LocalDeviceState(device_info=device)
        got_data = False

        # Try airfryer endpoint (skip if device is known to be non-airfryer)
        airfryer = None
        if device.airfryer_port is not False:
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
                )
                _LOGGER.debug("Airfryer status: %s", airfryer)

                # Fetch Venus-specific endpoints
                if device.airfryer_port in (PORT_VENUSAF, PORT_VENUS1AF):
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
            elif device.airfryer_port is None:
                # No port responded; remember this device is not an airfryer
                device.airfryer_port = False

        # Get status (for air purifiers and other devices)
        status = await self.get_status(device)
        if status:
            got_data = True
            if not airfryer:
                state.power_on = status.get("pwr") == "1"
            state.properties.update(status)

        # Get air quality (for air purifiers) - merge into top level
        air = await self.get_air_quality(device)
        if air:
            got_data = True
            state.properties.update(air)

        # Get filter status - merge into top level
        filters = await self.get_filter_status(device)
        if filters:
            got_data = True
            state.properties.update(filters)

        # Get firmware version info
        firmware = await self.get_firmware_info(device)
        if firmware:
            got_data = True
            state.properties["firmware"] = firmware

        if not got_data:
            return None

        return state


def parse_ssdp_device(discovery_info: dict[str, Any]) -> LocalDeviceInfo | None:
    """Parse SSDP discovery info into LocalDeviceInfo."""
    try:
        # SSDP provides location URL and UDN
        location = discovery_info.get("location", "")
        udn = discovery_info.get("udn", "")

        # Extract IP from location URL (e.g., http://192.168.1.100:80/description.xml)
        if "://" in location:
            host_part = location.split("://")[1].split("/")[0]
            ip_address = host_part.split(":")[0]
        else:
            return None

        # Use cppId if available (MAC-style ID), otherwise extract from UDN
        cpp_id = discovery_info.get("cppId", "")
        if not cpp_id:
            cpp_id = udn.replace("uuid:", "") if udn.startswith("uuid:") else udn

        model_name = discovery_info.get("modelName", "")
        model_number = discovery_info.get("modelNumber", "")
        friendly_name = discovery_info.get("friendlyName", "")

        # Use "modelName modelNumber" as name if friendlyName is generic placeholder
        if not friendly_name or friendly_name in (
            "Reference Product",
            "Philips Device",
        ):
            friendly_name = f"{model_name} {model_number}".strip() if model_name else ""

        device = LocalDeviceInfo(
            ip_address=ip_address,
            cpp_id=cpp_id,
            friendly_name=friendly_name,
            model_name=model_name,
            model_number=model_number,
            serial_number=discovery_info.get("serialNumber", ""),
        )

        _LOGGER.info("Parsed SSDP device: %s at %s", device.friendly_name, ip_address)
        return device

    except Exception as err:
        _LOGGER.error("Failed to parse SSDP discovery: %s", err)
        return None


def _parse_model_from_mdns_name(name: str) -> str:
    """Extract model number from mDNS name like PHILIPS_HD9285_2_21D740."""
    parts = name.split("_")
    if len(parts) >= 2 and parts[0].upper() == "PHILIPS":
        return parts[1]  # e.g., "HD9285"
    return ""


def parse_zeroconf_device(discovery_info: dict[str, Any]) -> LocalDeviceInfo | None:
    """Parse Zeroconf/mDNS discovery info into LocalDeviceInfo.

    Supports two discovery formats:
    1. _philipscondor._tcp.local. - properties: fn, mn, mr, id, bi
    2. _http._tcp.local. - name like PHILIPS_HD9285_2_21D740, uses HTTP
    """
    try:
        host = discovery_info.get("host", "")
        name = discovery_info.get("name", "")
        properties = discovery_info.get("properties", {})
        service_type = discovery_info.get("type", "")

        # Detect HTTP-based devices (e.g., HD9285)
        is_http_device = "_http._tcp" in service_type or "_http._tcp" in name

        # Properties from _philipscondor devices use short keys
        cpp_id = properties.get("id", "")
        friendly_name = properties.get("fn", "")
        model_name = properties.get("mn", "")
        model_number = properties.get("mr", "")
        boot_id = properties.get("bi", "")

        # For _http._tcp devices, extract model from mDNS name
        if is_http_device and not model_name:
            model_name = _parse_model_from_mdns_name(name.split("._")[0])

        # Use "modelName modelNumber" as name if friendlyName is generic placeholder
        if not friendly_name or friendly_name in (
            "Reference Product",
            "Philips Device",
        ):
            friendly_name = f"{model_name} {model_number}".strip() if model_name else ""

        # Fallback: extract from mDNS name if still no friendly name
        if not friendly_name:
            # Strip service type suffix to get the device name part
            for suffix in ("._philipscondor", "._http"):
                if suffix in name:
                    friendly_name = name.split(suffix)[0]
                    break
            else:
                friendly_name = name

        device = LocalDeviceInfo(
            ip_address=host,
            cpp_id=cpp_id or name,  # Use name as fallback ID
            friendly_name=friendly_name,
            model_name=model_name,
            model_number=model_number,
            boot_id=boot_id,
            use_https=not is_http_device,
        )

        _LOGGER.info(
            "Parsed Zeroconf device: %s at %s (model: %s, https: %s)",
            friendly_name,
            host,
            model_name,
            device.use_https,
        )
        return device

    except Exception as err:
        _LOGGER.error("Failed to parse Zeroconf discovery: %s", err)
        return None

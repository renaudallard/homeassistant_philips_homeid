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
"""Data models, constants, auth, and crypto for Philips local API."""

from __future__ import annotations

import base64
import hashlib
import logging

from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

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
# Multicooker ports
PORT_NUTRIMAX = "nutrimax"  # Nutrimax multicooker (NX0960)
PORT_HERMESAC = "hermesac"  # Hermes appliance (NX0950)

# Model-to-port mapping: avoids probing all ports and overwhelming
# the device's limited web server.
_MODEL_PORT_MAP: dict[str, str] = {
    "HD9200": PORT_AIRFRYER,
    "HD9255": PORT_AIRFRYER,
    "HD9280": PORT_AIRFRYER,
    "HD9285": PORT_AIRFRYER,
    "HD9875": PORT_VENUS1AF,
    "HD9876": PORT_VENUS1AF,
    "HD9880": PORT_VENUSAF,
    "NX0950": PORT_HERMESAC,
    "NX0960": PORT_NUTRIMAX,
}

# Ports that use Venus-style key naming (need normalization)
VENUS_STYLE_PORTS = frozenset(
    {PORT_VENUSAF, PORT_VENUS1AF, PORT_NUTRIMAX, PORT_HERMESAC}
)
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
# Venus/Nutrimax/Hermes status values
AIRFRYER_STATUS_PRECOOK = "precook"
AIRFRYER_STATUS_PARASETTING = "parasetting"
AIRFRYER_STATUS_MAINTAIN = "maintain"
AIRFRYER_STATUS_USER_ACTION = "user_action"
AIRFRYER_STATUS_POWERSAVE = "powersave"
AIRFRYER_STATUS_MAINMENU = "mainmenu"


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
        """
        try:
            challenge_clean = challenge_b64.strip()
            response_scheme = PhilipsCondorAuth.SCHEME
            for variant in PhilipsCondorAuth.SCHEME_VARIANTS:
                if challenge_clean.lower().startswith(variant.lower()):
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

            data = challenge + client_id_bytes + client_secret_bytes
            hash_result = hashlib.sha256(data).digest()

            _LOGGER.debug("Hash result (hex): %s", hash_result.hex())

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
    """

    _ZERO_IV = b"\x00" * 16

    @staticmethod
    def _hex_to_key(hex_key: str) -> bytes:
        """Convert hex string encryption key to 16-byte AES key."""
        key_bytes = bytes.fromhex(hex_key)
        if len(key_bytes) == 17 and key_bytes[0] == 0:
            key_bytes = key_bytes[1:]
        if len(key_bytes) != 16:
            raise ValueError(
                f"Invalid AES key length: {len(key_bytes)} bytes (expected 16)"
            )
        return key_bytes

    @staticmethod
    def decrypt(data_b64: str, hex_key: str) -> str | None:
        """Decrypt a base64-encoded AES/CBC/PKCS7 payload."""
        try:
            key = PhilipsCrypto._hex_to_key(hex_key)
            ciphertext = base64.b64decode(data_b64.strip())

            cipher = Cipher(algorithms.AES(key), modes.CBC(PhilipsCrypto._ZERO_IV))
            decryptor = cipher.decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()

            unpadder = PKCS7(128).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()

            return plaintext.decode("utf-8")
        except Exception as err:
            _LOGGER.error("AES decryption failed: %s", err)
            return None

    @staticmethod
    def encrypt(data: str, hex_key: str) -> str | None:
        """Encrypt a JSON string with AES/CBC/PKCS7."""
        try:
            key = PhilipsCrypto._hex_to_key(hex_key)
            plaintext = data.encode("utf-8")

            padder = PKCS7(128).padder()
            padded = padder.update(plaintext) + padder.finalize()

            cipher = Cipher(algorithms.AES(key), modes.CBC(PhilipsCrypto._ZERO_IV))
            encryptor = cipher.encryptor()
            ciphertext = encryptor.update(padded) + encryptor.finalize()

            return base64.b64encode(ciphertext).decode("utf-8")
        except Exception as err:
            _LOGGER.error("AES encryption failed: %s", err)
            return None


def parse_ssdp_device(discovery_info: dict[str, Any]) -> LocalDeviceInfo | None:
    """Parse SSDP discovery info into LocalDeviceInfo."""
    try:
        location = discovery_info.get("location", "")
        udn = discovery_info.get("udn", "")

        if "://" in location:
            host_part = location.split("://")[1].split("/")[0]
            ip_address = host_part.split(":")[0]
        else:
            return None

        cpp_id = discovery_info.get("cppId", "")
        if not cpp_id:
            cpp_id = udn.replace("uuid:", "") if udn.startswith("uuid:") else udn

        model_name = discovery_info.get("modelName", "")
        model_number = discovery_info.get("modelNumber", "")
        friendly_name = discovery_info.get("friendlyName", "")

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

        _LOGGER.info(
            "Parsed SSDP device: %s at %s",
            device.friendly_name,
            ip_address,
        )
        return device

    except Exception as err:
        _LOGGER.error("Failed to parse SSDP discovery: %s", err)
        return None


def _parse_model_from_mdns_name(name: str) -> str:
    """Extract model number from mDNS name like PHILIPS_HD9285_2_21D740."""
    parts = name.split("_")
    if len(parts) >= 2 and parts[0].upper() == "PHILIPS":
        return parts[1]
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

        is_http_device = "_http._tcp" in service_type or "_http._tcp" in name

        cpp_id = properties.get("id", "")
        friendly_name = properties.get("fn", "")
        model_name = properties.get("mn", "")
        model_number = properties.get("mr", "")
        boot_id = properties.get("bi", "")

        if is_http_device and not model_name:
            model_name = _parse_model_from_mdns_name(name.split("._")[0])

        if not friendly_name or friendly_name in (
            "Reference Product",
            "Philips Device",
        ):
            friendly_name = f"{model_name} {model_number}".strip() if model_name else ""

        if not friendly_name:
            for suffix in ("._philipscondor", "._http"):
                if suffix in name:
                    friendly_name = name.split(suffix)[0]
                    break
            else:
                friendly_name = name

        device = LocalDeviceInfo(
            ip_address=host,
            cpp_id=cpp_id or name,
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

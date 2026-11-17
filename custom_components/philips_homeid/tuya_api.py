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
"""Tuya mobile API client for feed now on HomeID feeders.

The HomeID app dispenses through the Tuya (ThingClips) SDK it bundles: it
logs in with thirdLogin and the HomeID id token, then publishes one data
point. This module signs those requests the way the SDK does, on hashlib,
hmac and cryptography, so feed now needs no extra requirement.

It logs in, checks a session and publishes data points, nothing else. It only
talks to Tuya's regional API hosts and never follows redirects. Errors carry
a fixed message and at most Tuya's error code, never a token, session,
identifier or reply body; the debug log names only a reply's keys.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
import zlib
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_LOGGER = logging.getLogger(__name__)

# Identity of the HomeID 8.23.0 app in Tuya's cloud, the same in every copy
# of the app. APP_ID and APP_SECRET are THING_SMART_APPKEY and
# THING_SMART_SECRET in its AndroidManifest. APP_KEY and the signing
# certificate hash are not in clear text in the APK; they come from the
# petsseries project and were proven by a live login.
APP_ID = "d997esphw5fevawrcyud"
APP_SECRET = "fcxp5hwguv5vpa4wveu8tujnhky8nhde"
APP_KEY = "dgsgxpwew54gdpcm5cuqfrvjpakxgvmn"
CERT_SHA256 = "D2:D6:95:A1:1D:1B:84:F9:25:A9:45:6E:27:F4:45:E9:FD:87:C3:74:63:AA:8A:34:32:A6:6A:23:3B:0F:D5:0F"
PACKAGE = "com.philips.ka.oneka.app"

LOGIN_URL = "https://a1.tuyaeu.com/api.json"
# Tuya's regional mobile API hosts, the only ones a login may move us to.
_API_HOSTS = frozenset(
    {
        "a1.tuyaeu.com",
        "a1.tuyaus.com",
        "a1.tuyacn.com",
        "a1.tuyain.com",
        "a1-sg.iotbing.com",
    }
)
# Request parameters that take part in the signature, as in the Tuya SDK.
_SIGNED = frozenset(
    {
        "a",
        "appVersion",
        "chKey",
        "clientId",
        "deviceId",
        "et",
        "h5",
        "h5Token",
        "lang",
        "lat",
        "lon",
        "n4h5",
        "os",
        "postData",
        "requestId",
        "sid",
        "sp",
        "time",
        "ttid",
        "v",
    }
)
# Codes the Tuya SDK reads as a session gone, to log in again.
SESSION_LOST = frozenset({"USER_SESSION_LOSS", "USER_SESSION_INVALID"})
_TIMEOUT = aiohttp.ClientTimeout(total=20)
_MAX_REPLY = 1 << 20
# Tuya error codes are upper case words; ids and tokens are not, and
# carry digits even when upper case.
_CODE = re.compile(r"[A-Z][A-Z_]{0,63}")


class TuyaError(Exception):
    """A Tuya request failed. ``code`` is Tuya's error code, if it sent one."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code if _CODE.fullmatch(code) else ""


class TuyaSessionLost(TuyaError):
    """Tuya no longer knows the session; a new login is needed."""


class Signer:
    """Request signing and keys, derived from one app's identity."""

    def __init__(
        self, app_id: str, app_secret: str, app_key: str, cert_sha256: str, package: str
    ) -> None:
        cert_msg = f"{package}_{cert_sha256}"
        material = f"{cert_msg}_{app_key}_{app_secret}"
        self.app_id = app_id
        self._material = material
        self._sign_key = hashlib.sha256(material.encode()).digest()
        mac = hmac.new(app_id.encode(), cert_msg.encode(), hashlib.sha256)
        self.channel_key = mac.hexdigest()[8:16]

    def sign(self, params: dict[str, str]) -> str:
        return hmac.new(
            self._sign_key, canonical(params).encode(), hashlib.sha256
        ).hexdigest()

    def request_key(self, request_id: str, ecode: str) -> bytes:
        """The AES key of one request, bound to the session's ecode if any."""
        msg = f"{self._material}_{ecode}" if ecode else self._material
        mac = hmac.new(request_id.encode(), msg.encode(), hashlib.sha256)
        return mac.hexdigest()[:16].encode()


HOMEID = Signer(APP_ID, APP_SECRET, APP_KEY, CERT_SHA256, PACKAGE)


def canonical(params: dict[str, str]) -> str:
    """The signed string: signed, non-empty parameters in key order."""
    parts = []
    for key in sorted(params):
        value = params[key]
        if key not in _SIGNED or not value:
            continue
        if key == "postData":
            d = hashlib.md5(value.encode()).hexdigest()
            value = d[8:16] + d[0:8] + d[24:32] + d[16:24]
        parts.append(f"{key}={value}")
    return "||".join(parts)


def seal(key: bytes, payload: dict[str, Any]) -> str:
    nonce = os.urandom(12)
    body = json.dumps(payload, separators=(",", ":")).encode()
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, body, None)).decode()


def unseal(key: bytes, value: str) -> Any:
    """Open a sealed reply. InvalidTag if this key did not seal it.

    Errors here and below are raised after their except block, not inside it
    with ``from None``: that would still keep the original on
    ``__context__``, and a JSON error holds the whole reply.
    """
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) < 28:
        raise TuyaError("Tuya reply was not readable")
    plain = AESGCM(key).decrypt(raw[:12], raw[12:], None)
    if plain[:2] == b"\x1f\x8b":
        plain = _gunzip(plain)
    return _json(plain)


def _gunzip(data: bytes) -> bytes:
    """Inflate one gzip member to at most _MAX_REPLY bytes."""
    inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        plain = inflate.decompress(data, _MAX_REPLY)
    except zlib.error:
        plain = b""
    if not plain or inflate.unconsumed_tail or not inflate.eof:
        raise TuyaError("Tuya reply was not readable")
    return plain


def _json(data: bytes) -> Any:
    try:
        return json.loads(data)
    # Deep nesting raises RecursionError, not ValueError.
    except (ValueError, RecursionError):
        pass
    raise TuyaError("Tuya reply was not readable")


def _refusal(code: Any) -> TuyaError:
    code = code if isinstance(code, str) else ""
    if code in SESSION_LOST:
        return TuyaSessionLost("Tuya session expired", code)
    return TuyaError("Tuya refused the request", code)


def _payload(data: Any) -> dict[str, Any]:
    """Unwrap nested results, refusing at the first level that failed.

    Tuya marks every reply with ``success``: a reply that does not say
    ``true`` is not taken as done.
    """
    if not isinstance(data, dict):
        raise TuyaError("Tuya reply was not readable")
    done = data.get("success") is True
    while True:
        if data.get("success") is False or data.get("errorCode"):
            raise _refusal(data.get("errorCode"))
        inner = data.get("result")
        if not isinstance(inner, dict):
            break
        data = inner
    if not done:
        raise TuyaError("Tuya request was not confirmed")
    return data


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _api_url(value: Any) -> str:
    """The mobile API URL a login names, if it is one of Tuya's hosts."""
    if not isinstance(value, str):
        raise TuyaError("Tuya login named no API host")
    try:
        parts = urlsplit(value)
        unexpected = (
            parts.scheme != "https"
            or parts.hostname not in _API_HOSTS
            or parts.port is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.username
        )
    except ValueError:
        # Its message can quote the URL.
        unexpected = True
    if unexpected:
        raise TuyaError("Tuya login named an unexpected API host")
    return f"https://{parts.hostname}/api.json"


class TuyaSession:
    """One Tuya mobile session of the HomeID app for one config entry."""

    def __init__(self, http: aiohttp.ClientSession, signer: Signer = HOMEID) -> None:
        self._http = http
        self._signer = signer
        self._url = LOGIN_URL
        self._sid = ""
        self._ecode = ""
        self._uid = ""

    @property
    def logged_in(self) -> bool:
        return bool(self._sid)

    def forget(self) -> None:
        self._url = LOGIN_URL
        self._sid = self._ecode = self._uid = ""

    async def login(self, id_token: str, dial_code: str) -> None:
        """thirdLogin with the HomeID id token, as TuyaAuthClientImpl does."""
        self.forget()
        data = await self._call(
            "thing.m.user.third.login",
            {
                "countryCode": dial_code,
                "accessToken": id_token,
                "type": "jwt",
                "extraInfo": '{"platform":"PhilipsDA"}',
                "options": '{"group": 1}',
            },
        )
        sid, ecode, uid = (_text(data.get(k)) for k in ("sid", "ecode", "uid"))
        if not (sid and ecode and uid):
            raise TuyaError("Tuya login returned no session")
        domain = data.get("domain")
        url = domain.get("mobileApiUrl") if isinstance(domain, dict) else None
        api = _api_url(url) if url is not None else LOGIN_URL
        self._url, self._sid, self._ecode, self._uid = api, sid, ecode, uid

    async def alive(self) -> bool:
        """Whether Tuya still accepts this session, by one harmless read."""
        if not self.logged_in:
            return False
        try:
            await self._call("m.life.home.space.list", {})
        except TuyaSessionLost:
            return False
        return True

    async def publish(self, device_id: str, dps: dict[str, int]) -> None:
        """Send data points to one device through Tuya's cloud."""
        if not self.logged_in:
            raise TuyaSessionLost("Tuya session expired")
        await self._call(
            "thing.m.device.dp.publish",
            {
                "devId": device_id,
                "gwId": device_id,
                "dps": json.dumps(dps, separators=(",", ":")),
            },
        )

    async def _call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        key = self._signer.request_key(request_id, self._ecode)
        params = {
            "a": action,
            "v": "1.0",
            "clientId": self._signer.app_id,
            "os": "Android",
            "appVersion": "2.2.1",
            "channel": "sdk",
            "osSystem": "14",
            "sdkVersion": "6.7.0",
            "deviceCoreVersion": "6.7.0",
            "platform": "Pixel 7",
            "timeZoneId": "UTC",
            "cp": "gzip",
            "nd": "1",
            "lang": "en",
            "ttid": "android",
            "et": "3",
            "bizDM": "ipc",
            "chKey": self._signer.channel_key,
            "deviceId": self._uid,
            "time": str(int(time.time())),
            "requestId": request_id,
            "postData": seal(key, payload),
        }
        if self._sid:
            params["sid"] = self._sid
        params["sign"] = self._signer.sign(params)
        try:
            body = await self._post(params)
        except (aiohttp.ClientError, TimeoutError):
            # Not chained: the request carries the session and the id token.
            body = None
        if body is None:
            raise TuyaError("Tuya request was not confirmed")
        envelope = _json(body)
        if not isinstance(envelope, dict):
            raise TuyaError("Tuya reply was not readable")
        sealed = envelope.get("result")
        if not isinstance(sealed, str):
            raise _refusal(envelope.get("errorCode") or envelope.get("code"))
        reply = self._open(request_id, key, sealed)
        if isinstance(reply, dict):
            _LOGGER.debug("Tuya %s reply keys: %s", action, sorted(reply))
        return _payload(reply)

    async def _post(self, params: dict[str, str]) -> bytes:
        # Inflated here, not by aiohttp: older aiohttp releases inflate each
        # received chunk whole, a few hundred MiB for a small gzip body.
        async with self._http.post(
            self._url,
            data=params,
            headers={"Accept-Encoding": "gzip"},
            timeout=_TIMEOUT,
            allow_redirects=False,
            auto_decompress=False,
        ) as response:
            if response.status != 200:
                raise TuyaError("Tuya request was not confirmed")
            # An empty header means none, as aiohttp reads it.
            encoding = response.headers.get("Content-Encoding", "")
            encoding = encoding.strip().lower() or "identity"
            if encoding not in ("identity", "gzip"):
                raise TuyaError("Tuya reply was not readable")
            body = bytearray()
            async for chunk in response.content.iter_chunked(1 << 16):
                body += chunk
                if len(body) > _MAX_REPLY:
                    raise TuyaError("Tuya request was not confirmed")
        if encoding == "gzip":
            return _gunzip(bytes(body))
        return bytes(body)

    def _open(self, request_id: str, key: bytes, sealed: str) -> Any:
        try:
            return unseal(key, sealed)
        except InvalidTag:
            pass
        if not self._ecode:
            raise TuyaError("Tuya reply was not readable")
        # Tuya answers a session it no longer knows with the session-less key,
        # or with one that opens with neither.
        try:
            return unseal(self._signer.request_key(request_id, ""), sealed)
        except InvalidTag:
            pass
        raise TuyaSessionLost("Tuya session expired")

"""Tuya mobile signing, sealing and session handling for feed now."""

import gzip
import json
import logging
import zlib
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.philips_homeid import tuya_api
from custom_components.philips_homeid.tuya_api import (
    LOGIN_URL,
    Signer,
    TuyaError,
    TuyaSession,
    TuyaSessionLost,
    canonical,
    seal,
    unseal,
)

# Dummy identity. The expected values were computed with tuya-mobile's own
# PurePythonTuyaSigner and canonical_string, an independent implementation.
DUMMY = Signer(
    "abcd1234efgh",
    "s3cr3tvalue",
    "kkkkey",
    ":".join(("A1", "B2", "C3") * 10 + ("D4", "E5")),
    "com.example.app",
)
PARAMS = {
    "a": "thing.m.device.dp.publish",
    "v": "1.0",
    "clientId": "abcd1234efgh",
    "time": "1700000000",
    "requestId": "rid-1",
    "postData": "c2VhbGVk",
    "sid": "",
    "channel": "sdk",
    "deviceId": "uid-1",
}


def test_signing_matches_tuya_mobile():
    assert canonical(PARAMS) == (
        "a=thing.m.device.dp.publish||clientId=abcd1234efgh||deviceId=uid-1"
        "||postData=3ac4add6056b2a6e728109347270bbd6||requestId=rid-1"
        "||time=1700000000||v=1.0"
    )
    assert DUMMY.sign(PARAMS) == (
        "195bdd9035f62cb37897923f090afd5e7ffc6e44a1f9be19e8999b1bcfe54108"
    )
    assert DUMMY.request_key("rid-1", "") == b"e7ba661f7228ab3f"
    assert DUMMY.request_key("rid-1", "ec-1") == b"b6f4960788050100"
    assert DUMMY.channel_key == "bbf7feb7"


def test_shipped_identity_has_the_expected_form():
    assert tuya_api.PACKAGE == "com.philips.ka.oneka.app"
    assert len(tuya_api.CERT_SHA256.split(":")) == 32
    for value in (tuya_api.APP_ID, tuya_api.APP_SECRET, tuya_api.APP_KEY):
        assert value.isalnum() and "@" not in value


def test_seal_round_trip_and_gzip_reply():
    key = DUMMY.request_key("r", "")
    assert unseal(key, seal(key, {"x": 1})) == {"x": 1}
    nonce = b"\0" * 12
    body = gzip.compress(b'{"y":2}')
    sealed = tuya_api.base64.b64encode(
        nonce + tuya_api.AESGCM(key).encrypt(nonce, body, None)
    ).decode()
    assert unseal(key, sealed) == {"y": 2}


def test_oversized_gzip_reply_is_refused():
    key = DUMMY.request_key("r", "")
    nonce = b"\0" * 12
    body = gzip.compress(b" " * (tuya_api._MAX_REPLY + 1))
    sealed = tuya_api.base64.b64encode(
        nonce + tuya_api.AESGCM(key).encrypt(nonce, body, None)
    ).decode()
    with pytest.raises(TuyaError):
        unseal(key, sealed)


@pytest.mark.parametrize("bad", ["", "not base64!", "c2hvcnQ="])
def test_malformed_sealed_value_is_an_error(bad):
    with pytest.raises(TuyaError):
        unseal(DUMMY.request_key("r", ""), bad)


@pytest.mark.parametrize(
    "url",
    ["https://a1.tuyaeu.com", "https://a1.tuyaus.com/", "https://a1-sg.iotbing.com"],
)
def test_login_may_move_to_a_regional_host(url):
    host = url.split("/")[2]
    assert tuya_api._api_url(url) == f"https://{host}/api.json"


@pytest.mark.parametrize(
    "url",
    [
        "http://a1.tuyaeu.com",
        "https://a1.tuyaeu.com.evil.example",
        "https://evil.example",
        "https://a1.tuyaeu.com:8443",
        "https://a1.tuyaeu.com/other",
        "https://a1.tuyaeu.com/?x=1",
        "https://user@a1.tuyaeu.com",
        "https://a1.tuyaeu.com:x0x0/",
        "https://a1.tuyaeu.com:99999/",
        "https://[x0x0/",
        42,
    ],
)
def test_login_never_moves_elsewhere(url):
    with pytest.raises(TuyaError) as caught:
        tuya_api._api_url(url)
    assert "x0x0" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


class FakeTuya:
    """Answers each POST like Tuya: sealed with the request's own key."""

    def __init__(self, *replies, ecode=""):
        self.replies = list(replies)
        self.ecode = ecode
        self.calls = []
        self.chunks_read = 0
        self.http = MagicMock()
        self.http.post.side_effect = self._post

    def _post(self, url, *, data, headers, timeout, allow_redirects, auto_decompress):
        assert allow_redirects is False and auto_decompress is False
        assert headers == {"Accept-Encoding": "gzip"}
        self.calls.append((url, dict(data)))
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(data)
        if not isinstance(reply, tuple):
            reply = (200, reply)
        status, body, *encoding = reply
        if isinstance(body, dict) and "_sealed" in body:
            key = DUMMY.request_key(data["requestId"], body.get("_ecode", ""))
            body = {"result": seal(key, body["_sealed"]), "t": 1}
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        response = MagicMock(status=status, headers={})
        if encoding:
            response.headers["Content-Encoding"] = encoding[0]
            gzipped = encoding[0].strip().lower() == "gzip"
            if gzipped and not isinstance(body, bytes):
                raw = gzip.compress(raw)
        response.content.iter_chunked = lambda size: self._chunks(raw, size)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        return context

    async def _chunks(self, raw, size):
        for start in range(0, len(raw), size):
            self.chunks_read += 1
            yield raw[start : start + size]

    def payload(self, index, ecode=""):
        data = self.calls[index][1]
        return unseal(DUMMY.request_key(data["requestId"], ecode), data["postData"])


LOGIN_OK = {
    "_sealed": {
        "result": {
            "sid": "sid-1",
            "ecode": "ec-1",
            "uid": "uid-1",
            "domain": {"mobileApiUrl": "https://a1.tuyaus.com"},
        },
        "success": True,
    }
}


async def logged_in(*replies):
    fake = FakeTuya(LOGIN_OK, *replies)
    session = TuyaSession(fake.http, DUMMY)
    await session.login("id-token", "32")
    return fake, session


@pytest.mark.asyncio
async def test_login_sends_the_app_payload_and_keeps_the_session():
    fake, session = await logged_in()
    url, data = fake.calls[0]
    assert url == LOGIN_URL
    assert data["a"] == "thing.m.user.third.login"
    assert "sid" not in data and data["deviceId"] == ""
    assert data["sign"] == DUMMY.sign({k: v for k, v in data.items() if k != "sign"})
    assert fake.payload(0) == {
        "countryCode": "32",
        "accessToken": "id-token",
        "type": "jwt",
        "extraInfo": '{"platform":"PhilipsDA"}',
        "options": '{"group": 1}',
    }
    assert session.logged_in


@pytest.mark.asyncio
async def test_publish_uses_the_session_host_and_compact_dps():
    fake, session = await logged_in(
        {"_sealed": {"result": True, "success": True}, "_ecode": "ec-1"}
    )
    await session.publish("dev-1", {"201": 3})
    url, data = fake.calls[1]
    assert url == "https://a1.tuyaus.com/api.json"
    assert data["a"] == "thing.m.device.dp.publish"
    assert data["sid"] == "sid-1" and data["deviceId"] == "uid-1"
    assert fake.payload(1, "ec-1") == {
        "devId": "dev-1",
        "gwId": "dev-1",
        "dps": '{"201":3}',
    }


@pytest.mark.asyncio
async def test_publish_without_a_session_sends_nothing():
    fake = FakeTuya()
    with pytest.raises(TuyaSessionLost):
        await TuyaSession(fake.http, DUMMY).publish("dev-1", {"201": 1})
    fake.http.post.assert_not_called()


@pytest.mark.parametrize(
    "result",
    [
        {"sid": "s", "ecode": "e"},
        {"sid": "s", "ecode": "", "uid": "u"},
        {"sid": 1, "ecode": "e", "uid": "u"},
    ],
)
@pytest.mark.asyncio
async def test_login_without_a_full_session_is_an_error(result):
    fake = FakeTuya({"_sealed": {"result": result, "success": True}})
    session = TuyaSession(fake.http, DUMMY)
    with pytest.raises(TuyaError):
        await session.login("id-token", "32")
    assert not session.logged_in


@pytest.mark.asyncio
async def test_login_to_an_unknown_host_is_refused():
    fake = FakeTuya(
        {
            "_sealed": {
                "result": {
                    "sid": "s",
                    "ecode": "e",
                    "uid": "u",
                    "domain": {"mobileApiUrl": "https://evil.example"},
                },
                "success": True,
            }
        }
    )
    session = TuyaSession(fake.http, DUMMY)
    with pytest.raises(TuyaError):
        await session.login("id-token", "32")
    assert not session.logged_in


@pytest.mark.parametrize(
    ("reply", "error", "code"),
    [
        (
            {
                "_sealed": {"success": False, "errorCode": "PERMISSION_DENIED"},
                "_ecode": "ec-1",
            },
            TuyaError,
            "PERMISSION_DENIED",
        ),
        (
            {
                "_sealed": {"result": {"errorCode": "USER_SESSION_LOSS"}},
                "_ecode": "ec-1",
            },
            TuyaSessionLost,
            "USER_SESSION_LOSS",
        ),
        # A session Tuya forgot: answered with the session-less key.
        (
            {"_sealed": {"success": False, "errorCode": "USER_SESSION_LOSS"}},
            TuyaSessionLost,
            "USER_SESSION_LOSS",
        ),
        # The Tuya SDK reads USER_SESSION_INVALID the same way.
        (
            {"_sealed": {"success": False, "errorCode": "USER_SESSION_INVALID"}},
            TuyaSessionLost,
            "USER_SESSION_INVALID",
        ),
        # Or sealed with a key we cannot derive.
        ({"_sealed": {"success": True}, "_ecode": "other"}, TuyaSessionLost, ""),
        (
            {"errorCode": "SING_VALIDATE_FALED", "success": False},
            TuyaError,
            "SING_VALIDATE_FALED",
        ),
        ({"errorCode": "<script>", "success": False}, TuyaError, ""),
        ((500, {"result": "x"}), TuyaError, ""),
        ([1, 2], TuyaError, ""),
    ],
)
@pytest.mark.asyncio
async def test_refusals_are_typed_and_keep_only_a_safe_code(reply, error, code):
    _, session = await logged_in(reply)
    with pytest.raises(error) as caught:
        await session.publish("dev-1", {"201": 1})
    assert type(caught.value) is error
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_oversized_reply_is_refused_while_reading():
    size = 1 << 16
    fake, session = await logged_in(b" " * (tuya_api._MAX_REPLY * 4))
    before = fake.chunks_read
    with pytest.raises(TuyaError):
        await session.publish("dev-1", {"201": 1})
    assert fake.chunks_read - before == tuya_api._MAX_REPLY // size + 1


@pytest.mark.parametrize("encoding", ["gzip", " GZIP ", "identity"])
@pytest.mark.asyncio
async def test_reply_may_come_gzip_encoded(encoding):
    _, session = await logged_in(
        (
            200,
            {"_sealed": {"result": True, "success": True}, "_ecode": "ec-1"},
            encoding,
        )
    )
    await session.publish("dev-1", {"201": 1})


@pytest.mark.parametrize("encoding", ["br", "deflate", "gzip, br"])
@pytest.mark.asyncio
async def test_reply_in_another_encoding_is_refused_before_reading(encoding):
    fake, session = await logged_in((200, {"result": "x"}, encoding))
    before = fake.chunks_read
    with pytest.raises(TuyaError):
        await session.publish("dev-1", {"201": 1})
    assert fake.chunks_read == before


@pytest.mark.asyncio
async def test_gzip_encoded_reply_inflates_to_at_most_the_cap():
    deflate = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    block = b" " * (1 << 20)
    bomb = b"".join(deflate.compress(block) for _ in range(64)) + deflate.flush()
    assert len(bomb) < tuya_api._MAX_REPLY
    _, session = await logged_in((200, bomb, "gzip"))
    with pytest.raises(TuyaError):
        await session.publish("dev-1", {"201": 1})


@pytest.mark.asyncio
async def test_failed_status_is_refused_before_reading():
    fake, session = await logged_in((502, {"result": "x"}))
    before = fake.chunks_read
    with pytest.raises(TuyaError):
        await session.publish("dev-1", {"201": 1})
    assert fake.chunks_read == before


@pytest.mark.parametrize(
    "code",
    [
        "bf12ab34cd56ef78gh90",
        "1e2d3c4b-0000-4000-8000-000000000000",
        "eu1712345678901abcde",
        "Bearer.abc",
        "A" * 65,
        "AB12CD34EF56AB78CD90",
    ],
)
def test_error_code_that_looks_like_an_id_is_dropped(code):
    assert TuyaError("x", code).code == ""


@pytest.mark.parametrize(
    "reply",
    [{}, {"result": True}, {"success": "true"}, {"success": 1}, {"status": "ok"}],
)
@pytest.mark.asyncio
async def test_publish_needs_tuya_to_say_success(reply):
    _, session = await logged_in({"_sealed": reply, "_ecode": "ec-1"})
    with pytest.raises(TuyaError) as caught:
        await session.publish("dev-1", {"201": 1})
    assert type(caught.value) is TuyaError
    assert caught.value.code == ""


@pytest.mark.asyncio
async def test_login_needs_tuya_to_say_success():
    fake = FakeTuya({"_sealed": {"result": LOGIN_OK["_sealed"]["result"]}})
    session = TuyaSession(fake.http, DUMMY)
    with pytest.raises(TuyaError):
        await session.login("id-token", "32")
    assert not session.logged_in


@pytest.mark.asyncio
async def test_debug_log_names_only_the_reply_keys(caplog):
    caplog.set_level(logging.DEBUG, logger=tuya_api.__name__)
    _, session = await logged_in(
        {"_sealed": {"result": True, "success": True, "t": 1}, "_ecode": "ec-1"}
    )
    await session.publish("dev-1", {"201": 1})
    lines = [r.getMessage() for r in caplog.records]
    assert "Tuya thing.m.device.dp.publish reply keys: ['result', 'success', 't']" in (
        lines
    )
    for secret in ("sid-1", "ec-1", "uid-1", "id-token", "tuyaus"):
        assert not any(secret in line for line in lines)


@pytest.mark.asyncio
async def test_alive_tells_a_lost_session_from_other_failures():
    _, session = await logged_in(
        {"_sealed": {"result": [], "success": True}, "_ecode": "ec-1"},
        {"_sealed": {"success": False, "errorCode": "USER_SESSION_LOSS"}},
        (502, {}),
    )
    assert await session.alive() is True
    assert await session.alive() is False
    with pytest.raises(TuyaError):
        await session.alive()
    assert await TuyaSession(MagicMock(), DUMMY).alive() is False


@pytest.mark.asyncio
async def test_transport_errors_do_not_chain_the_request():
    http = MagicMock()
    http.post.side_effect = aiohttp.ClientConnectionError("https://x?sid=secret")
    with pytest.raises(TuyaError) as caught:
        await TuyaSession(http, DUMMY).login("id-token", "32")
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "secret" not in str(caught.value)


def _sealed_raw(key, body):
    nonce = b"\0" * 12
    return tuya_api.base64.b64encode(
        nonce + tuya_api.AESGCM(key).encrypt(nonce, body, None)
    ).decode()


@pytest.mark.parametrize(
    "reply",
    [
        b'{"result": "x", "sid": "secret"',
        lambda data: {
            "result": _sealed_raw(
                DUMMY.request_key(data["requestId"], "ec-1"), b'{"sid": "secret"'
            )
        },
        lambda data: {
            "result": _sealed_raw(
                DUMMY.request_key(data["requestId"], "ec-1"), b"\x1f\x8bsecret"
            )
        },
        {"result": "not base64!"},
        {"_sealed": {"success": True}, "_ecode": "other"},
    ],
)
@pytest.mark.asyncio
async def test_unreadable_replies_keep_no_original_error(reply):
    _, session = await logged_in(reply)
    with pytest.raises(TuyaError) as caught:
        await session.publish("dev-1", {"201": 1})
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

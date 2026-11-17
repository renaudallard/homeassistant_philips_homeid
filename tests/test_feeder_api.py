"""HomeID pet REST transport, discovery and refresh-token ownership."""

import asyncio
import base64
import gzip
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from multidict import CIMultiDict

from custom_components.philips_homeid import feeder_api
from custom_components.philips_homeid.cloud_auth import (
    CloudAuthError,
    CloudConnectionError,
)
from custom_components.philips_homeid.const import CONF_CLOUD_REFRESH_TOKEN
from custom_components.philips_homeid.feeder_api import (
    DATA_FEEDER_AUTH,
    PET_API_URL,
    FeederAuthRejected,
    FeederClient,
    FeederError,
    FeederHTTPError,
    FeederTokenOwner,
    find_feeder,
    pet_request,
)

CLOUD = "custom_components.philips_homeid.cloud_api.PhilipsCloudAPI"


def fake_hass(refresh="refresh-1"):
    entry = SimpleNamespace(entry_id="entry", data={CONF_CLOUD_REFRESH_TOKEN: refresh})
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_get_entry.side_effect = lambda entry_id: (
        entry if entry_id == "entry" else None
    )

    def update(target, data):
        target.data = data

    hass.config_entries.async_update_entry.side_effect = update
    return hass, entry


def cloud_returning(*results):
    cloud = MagicMock()
    cloud.refresh_tokens = AsyncMock(side_effect=list(results))
    cloud.close = AsyncMock()
    return cloud


def tokens(access, refresh, expires_in=3600):
    return {"access_token": access, "refresh_token": refresh, "expires_in": expires_in}


def fake_session(*statuses, body=None, raw=None, headers=None, charset=None):
    """A session answering each GET with ``raw`` bytes, or ``body`` as JSON."""
    if raw is None:
        raw = json.dumps({"item": []} if body is None else body).encode()
    session = MagicMock()
    session.chunks_read = 0

    async def chunks(size):
        for start in range(0, len(raw), size):
            session.chunks_read += 1
            yield raw[start : start + size]

    contexts = []
    for status in statuses:
        response = MagicMock(status=status, headers=headers or {}, charset=charset)
        response.content.iter_chunked = chunks
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        contexts.append(context)
    session.get.side_effect = contexts
    return session


def authorizations(session):
    return [
        call.kwargs["headers"]["Authorization"] for call in session.get.call_args_list
    ]


# --- transport -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 307, 308, 401, 403, 500])
async def test_real_aiohttp_never_follows_redirects(status):
    method = "GET"
    calls = []

    async def transport(request, handler):
        calls.append((request.method, str(request.url)))
        response = MagicMock(status=status, method=method, url=request.url)
        response.headers = CIMultiDict({"Location": "/api/homes/again"})
        response._raw_cookie_headers = ()
        response.connection = None
        response.json = AsyncMock(return_value={})
        response.wait_for_close = AsyncMock()
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        return response

    async with aiohttp.ClientSession(middlewares=(transport,)) as session:
        with (
            patch.object(
                session.connector,
                "connect",
                AsyncMock(side_effect=AssertionError("No network")),
            ),
            pytest.raises(FeederHTTPError) as err,
        ):
            await pet_request(session, "access", method, "/api/homes/h/meals")
    assert err.value.status == status
    assert calls == [(method, f"{PET_API_URL}/api/homes/h/meals")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/api/homes"),
        ("POST", "/api/homes/h/meals"),
        ("PATCH", "/api/homes/h/meals/m"),
        ("DELETE", "/api/homes/h/meals/m"),
        ("GET", "/api/consumer"),
        ("GET", "https://evil.example/api/homes"),
        ("GET", "/api/homes?x=1"),
        ("GET", "/api/homes#x"),
    ],
)
async def test_only_fixed_paths_and_methods_are_requested(method, path):
    session = fake_session(200)
    with pytest.raises(FeederError):
        await pet_request(session, "access", method, path)
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_http_error_keeps_only_numeric_status():
    session = fake_session(500)
    with pytest.raises(FeederHTTPError) as err:
        await pet_request(session, "secret-token", "GET", "/api/homes")
    assert err.value.status == 500
    assert "secret" not in str(err.value) and "http" not in str(err.value).lower()


@pytest.mark.asyncio
async def test_network_failure_is_a_feeder_error():
    session = MagicMock()
    session.get.side_effect = aiohttp.ClientConnectionError("down")
    with pytest.raises(FeederError):
        await pet_request(session, "a", "GET", "/api/homes")


@pytest.mark.asyncio
async def test_deeply_nested_reply_is_a_feeder_error():
    session = fake_session(200, raw=b"[" * 200000)
    with pytest.raises(FeederError):
        await pet_request(session, "a", "GET", "/api/homes")


@pytest.mark.asyncio
async def test_read_is_not_left_to_aiohttp_to_inflate():
    session = fake_session(200)
    await pet_request(session, "a", "GET", "/api/homes")
    kwargs = session.get.call_args.kwargs
    assert kwargs["auto_decompress"] is False
    assert kwargs["headers"]["Accept-Encoding"] == "gzip"


@pytest.mark.parametrize("encoding", ["gzip", " GZIP ", "identity", ""])
@pytest.mark.asyncio
async def test_reply_may_come_gzip_encoded(encoding):
    raw = b'{"item": [{"id": "h"}]}'
    if "gzip" in encoding.lower():
        raw = gzip.compress(raw)
    session = fake_session(200, raw=raw, headers={"Content-Encoding": encoding})
    data = await pet_request(session, "a", "GET", "/api/homes")
    assert data == {"item": [{"id": "h"}]}


@pytest.mark.parametrize("encoding", ["br", "deflate", "gzip, br"])
@pytest.mark.asyncio
async def test_reply_in_another_encoding_is_refused_before_reading(encoding):
    session = fake_session(200, headers={"Content-Encoding": encoding})
    with pytest.raises(FeederError):
        await pet_request(session, "a", "GET", "/api/homes")
    assert session.chunks_read == 0


@pytest.mark.asyncio
async def test_oversized_reply_is_refused_while_reading():
    # Valid JSON, so only the size limit can refuse it.
    raw = b'"' + b"a" * (feeder_api._MAX_REPLY * 2) + b'"'
    session = fake_session(200, raw=raw)
    with pytest.raises(FeederError):
        await pet_request(session, "a", "GET", "/api/homes")
    assert session.chunks_read == feeder_api._MAX_REPLY // (1 << 16) + 1


@pytest.mark.asyncio
async def test_gzip_encoded_reply_inflates_to_at_most_the_limit():
    # Valid JSON again: a few KiB sent, twice the limit once inflated.
    raw = gzip.compress(b'"' + b"a" * (feeder_api._MAX_REPLY * 2) + b'"')
    assert len(raw) < 1 << 16
    session = fake_session(200, raw=raw, headers={"Content-Encoding": "gzip"})
    with pytest.raises(FeederError):
        await pet_request(session, "a", "GET", "/api/homes")


@pytest.mark.parametrize("raw", [b"", b" \n"])
@pytest.mark.parametrize("encoding", ["identity", "gzip"])
@pytest.mark.asyncio
async def test_empty_reply_is_none_as_before(raw, encoding):
    if encoding == "gzip" and raw:
        raw = gzip.compress(raw)
    session = fake_session(200, raw=raw, headers={"Content-Encoding": encoding})
    assert await pet_request(session, "a", "GET", "/api/homes") is None


@pytest.mark.asyncio
async def test_gzip_member_of_nothing_is_none():
    raw = gzip.compress(b"")
    session = fake_session(200, raw=raw, headers={"Content-Encoding": "gzip"})
    assert await pet_request(session, "a", "GET", "/api/homes") is None


@pytest.mark.parametrize(
    ("charset", "raw", "expected"),
    [
        (None, '{"name": "Zo\u00eb"}'.encode(), {"name": "Zo\u00eb"}),
        ("iso-8859-1", '{"name": "Zo\u00eb"}'.encode("latin-1"), {"name": "Zo\u00eb"}),
    ],
)
@pytest.mark.asyncio
async def test_reply_is_decoded_in_the_declared_charset(charset, raw, expected):
    session = fake_session(200, raw=raw, charset=charset)
    assert await pet_request(session, "a", "GET", "/api/homes") == expected


@pytest.mark.parametrize(
    ("charset", "raw"), [("no-such-charset", b"{}"), (None, b'{"name": "Zo\xeb"}')]
)
@pytest.mark.asyncio
async def test_undecodable_reply_is_a_feeder_error(charset, raw):
    session = fake_session(200, raw=raw, charset=charset)
    with pytest.raises(FeederError):
        await pet_request(session, "a", "GET", "/api/homes")


# --- discovery -------------------------------------------------------------


def registry(devices, homes=None):
    homes = homes or [{"id": "home", "timeZone": "Europe/Riga"}]

    async def get(path):
        if path == "/api/homes":
            return {"item": homes}
        return {"item": devices}

    return get


DEVICE = {
    "id": "philips-1",
    "vendorId": "tuya-1",
    "productCtn": "PAW5320/01",
    "name": "Kitchen",
}


@pytest.mark.asyncio
async def test_exact_vendor_id_and_supported_model_identify_a_feeder():
    feeder = await find_feeder(registry([DEVICE]), "tuya-1")
    assert feeder is not None
    assert (feeder.vendor_id, feeder.philips_id, feeder.home_id) == (
        "tuya-1",
        "philips-1",
        "home",
    )
    assert (feeder.model, feeder.name, feeder.time_zone) == (
        "PAW5320",
        "Kitchen",
        "Europe/Riga",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "device",
    [
        {**DEVICE, "vendorId": "tuya-10"},
        {**DEVICE, "vendorId": None, "id": "tuya-1"},
        {**DEVICE, "vendorId": None, "externalId": "tuya-1"},
        {**DEVICE, "vendorId": None, "name": "tuya-1"},
        {**DEVICE, "productCtn": "PAW3300/01"},
        {**DEVICE, "productCtn": None},
    ],
)
async def test_names_prefixes_other_ids_and_models_never_match(device):
    assert await find_feeder(registry([device]), "tuya-1") is None


@pytest.mark.asyncio
async def test_duplicate_registry_match_is_ambiguous_not_guessed():
    get = registry([DEVICE], homes=[{"id": "a"}, {"id": "b"}])
    with pytest.raises(FeederError):
        await find_feeder(get, "tuya-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"item": None}, [], {"item": [1]}, "x"])
async def test_bad_registry_envelope_is_an_error(bad):
    async def get(path):
        return bad if path == "/api/homes" else {"item": []}

    if bad == []:
        assert await find_feeder(get, "tuya-1") is None
    else:
        with pytest.raises(FeederError):
            await find_feeder(get, "tuya-1")


@pytest.mark.asyncio
async def test_unreadable_home_does_not_hide_a_feeder_in_another():
    async def get(path):
        if path == "/api/homes":
            return {"item": [{"id": "shared"}, {"id": "own"}]}
        if path == "/api/homes/shared/devices":
            raise FeederAuthRejected(403)
        return {"item": [DEVICE]}

    feeder = await find_feeder(get, "tuya-1")
    assert feeder is not None and feeder.home_id == "own"


@pytest.mark.asyncio
async def test_unreadable_home_without_a_match_is_an_error_not_a_miss():
    async def get(path):
        if path == "/api/homes":
            return {"item": [{"id": "shared"}, {"id": "own"}]}
        if path == "/api/homes/shared/devices":
            raise FeederHTTPError(500)
        return {"item": []}

    with pytest.raises(FeederHTTPError):
        await find_feeder(get, "tuya-1")


@pytest.mark.asyncio
async def test_integer_home_id_is_quoted_path_segment():
    paths = []

    async def get(path):
        paths.append(path)
        return {"item": [{"id": 7}]} if path == "/api/homes" else {"item": [DEVICE]}

    feeder = await find_feeder(get, "tuya-1")
    assert feeder is not None and feeder.home_id == "7"
    assert paths == ["/api/homes", "/api/homes/7/devices"]


# --- refresh-token ownership -------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_requests_spend_the_refresh_token_once():
    hass, entry = fake_hass()
    cloud = cloud_returning(tokens("access-1", "refresh-2"))
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with patch(CLOUD, return_value=cloud):
        results = await asyncio.gather(*(owner.access_token() for _ in range(5)))
    assert results == ["access-1"] * 5
    cloud.refresh_tokens.assert_awaited_once_with("refresh-1", "homeid")
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "refresh-2"


@pytest.mark.asyncio
async def test_rotation_is_persisted_and_cached_until_expiry():
    hass, entry = fake_hass()
    cloud = cloud_returning(tokens("access-1", "refresh-2"), tokens("a2", "r3"))
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with patch(CLOUD, return_value=cloud):
        assert await owner.access_token() == "access-1"
        assert await owner.access_token() == "access-1"
        assert cloud.refresh_tokens.await_count == 1
        owner._expires = time.monotonic() - 1
        assert await owner.access_token() == "a2"
    # The second refresh spent the rotated token, never the original one.
    assert cloud.refresh_tokens.await_args_list[1].args[0] == "refresh-2"
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "r3"


@pytest.mark.asyncio
async def test_owner_survives_new_coordinators_and_is_discarded_on_removal():
    hass, _ = fake_hass()
    first = FeederTokenOwner.for_entry(hass, "entry")
    assert FeederTokenOwner.for_entry(hass, "entry") is first
    FeederTokenOwner.discard(hass, "entry")
    assert "entry" not in hass.data[DATA_FEEDER_AUTH]
    assert FeederTokenOwner.for_entry(hass, "entry") is not first


@pytest.mark.asyncio
async def test_unreachable_token_endpoint_backs_off_across_setup_retries():
    hass, entry = fake_hass()
    cloud = cloud_returning(CloudConnectionError("down"))
    with patch(CLOUD, return_value=cloud):
        with pytest.raises(CloudConnectionError):
            await FeederTokenOwner.for_entry(hass, "entry").access_token()
        # A setup retry builds a new coordinator; it reuses the same owner.
        with pytest.raises(CloudConnectionError):
            await FeederTokenOwner.for_entry(hass, "entry").access_token()
    cloud.refresh_tokens.assert_awaited_once()
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "refresh-1"


@pytest.mark.asyncio
async def test_back_off_does_not_delay_a_reauth_token():
    hass, entry = fake_hass()
    cloud = cloud_returning(CloudConnectionError("down"), tokens("a", "r"))
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with patch(CLOUD, return_value=cloud):
        with pytest.raises(CloudConnectionError):
            await owner.access_token()
        entry.data = {CONF_CLOUD_REFRESH_TOKEN: "reauth-token"}
        assert await owner.access_token() == "a"
    assert cloud.refresh_tokens.await_args_list[1].args[0] == "reauth-token"


@pytest.mark.asyncio
async def test_rejected_refresh_token_is_not_spent_again_until_reauth():
    hass, entry = fake_hass()
    cloud = cloud_returning(CloudAuthError("invalid_grant"), tokens("a", "r"))
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with patch(CLOUD, return_value=cloud):
        for _ in range(2):
            with pytest.raises(CloudAuthError):
                await owner.access_token()
        assert cloud.refresh_tokens.await_count == 1
        entry.data = {CONF_CLOUD_REFRESH_TOKEN: "reauth-token"}
        assert await owner.access_token() == "a"
    assert cloud.refresh_tokens.await_args_list[1].args[0] == "reauth-token"


@pytest.mark.asyncio
async def test_reauth_during_refresh_is_never_overwritten():
    hass, entry = fake_hass()
    gate = asyncio.Event()

    async def slow_refresh(token, client):
        await gate.wait()
        return tokens("old-chain-access", "old-chain-refresh")

    cloud = MagicMock(refresh_tokens=slow_refresh, close=AsyncMock())
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with patch(CLOUD, return_value=cloud):
        pending = asyncio.ensure_future(owner.access_token())
        await asyncio.sleep(0)
        entry.data = {CONF_CLOUD_REFRESH_TOKEN: "reauth-token"}
        gate.set()
        with pytest.raises(CloudConnectionError):
            await pending
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "reauth-token"
    hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_cause_a_second_refresh():
    hass, entry = fake_hass()
    gate = asyncio.Event()
    calls = []

    async def slow_refresh(token, client):
        calls.append(token)
        await gate.wait()
        return tokens("access-1", "refresh-2")

    cloud = MagicMock(refresh_tokens=slow_refresh, close=AsyncMock())
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with patch(CLOUD, return_value=cloud):
        first = asyncio.ensure_future(owner.access_token())
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.ensure_future(owner.access_token())
        await asyncio.sleep(0)
        gate.set()
        assert await second == "access-1"
    assert calls == ["refresh-1"]
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "refresh-2"


@pytest.mark.asyncio
async def test_missing_access_token_or_entry_is_not_success():
    hass, entry = fake_hass()
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with (
        patch(CLOUD, return_value=cloud_returning({"access_token": ""})),
        pytest.raises(CloudConnectionError),
    ):
        await owner.access_token()
    entry.data = {}
    with pytest.raises(CloudAuthError):
        await owner.access_token()


# --- client retry policy -----------------------------------------------------


def primed_owner(age):
    hass, entry = fake_hass()
    owner = FeederTokenOwner.for_entry(hass, "entry")
    now = time.monotonic()
    owner._access, owner._issued_for = "old-access", "refresh-1"
    owner._issued_at, owner._expires = now - age, now + 600
    return owner, entry


@pytest.mark.asyncio
async def test_read_401_on_an_older_token_refreshes_once_then_retries():
    owner, entry = primed_owner(age=1000)
    session = fake_session(401, 200)
    with patch(CLOUD, return_value=cloud_returning(tokens("new", "refresh-2"))):
        assert await FeederClient(session, owner).get("/api/homes") == {"item": []}
    assert authorizations(session) == ["Bearer old-access", "Bearer new"]
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "refresh-2"


@pytest.mark.asyncio
async def test_read_401_on_a_fresh_token_does_not_rotate_again():
    owner, _ = primed_owner(age=5)
    session = fake_session(401)
    cloud = cloud_returning(tokens("new", "refresh-2"))
    with patch(CLOUD, return_value=cloud), pytest.raises(FeederAuthRejected):
        await FeederClient(session, owner).get("/api/homes")
    cloud.refresh_tokens.assert_not_called()


@pytest.mark.asyncio
async def test_second_read_401_is_reported_not_looped():
    owner, _ = primed_owner(age=1000)
    session = fake_session(401, 401)
    cloud = cloud_returning(tokens("new", "refresh-2"))
    with patch(CLOUD, return_value=cloud), pytest.raises(FeederAuthRejected):
        await FeederClient(session, owner).get("/api/homes")
    assert session.get.call_count == 2
    cloud.refresh_tokens.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
async def test_client_refuses_every_write_before_sending(method):
    owner, _ = primed_owner(age=1000)
    session = fake_session(200)
    with pytest.raises(FeederError):
        await FeederClient(session, owner).request(method, "/api/homes/h/meals")
    session.get.assert_not_called()
    session.request.assert_not_called()
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_transport_errors_do_not_chain_urls_or_ids():
    import traceback

    home = "synthetic-home"  # not on the traceback line below
    path = f"/api/homes/{home}/devices"
    session = MagicMock()
    session.get.side_effect = aiohttp.ClientConnectionError(PET_API_URL + path)
    with pytest.raises(FeederError) as err:
        await pet_request(session, "a", "GET", path)
    text = "".join(traceback.format_exception(err.value))
    assert home not in text and "versuni" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        CloudConnectionError("Token endpoint unreachable: https://x/secret-detail"),
        CloudAuthError("Token refresh rejected: secret-detail"),
    ],
)
async def test_refresh_errors_do_not_chain_server_details(failure):
    import traceback

    hass, _ = fake_hass()
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with (
        patch(CLOUD, return_value=cloud_returning(failure)),
        pytest.raises(type(failure)) as err,
    ):
        await owner.access_token()
    assert "secret-detail" not in "".join(traceback.format_exception(err.value))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
async def test_refused_write_never_spends_the_refresh_token(method):
    hass, entry = fake_hass()
    owner = FeederTokenOwner.for_entry(hass, "entry")  # cold: no access token
    session = fake_session(200)
    cloud = cloud_returning(tokens("access-1", "refresh-2"))
    with patch(CLOUD, return_value=cloud), pytest.raises(FeederError):
        await FeederClient(session, owner).request(method, "/api/homes/h/meals")
    cloud.refresh_tokens.assert_not_called()
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "refresh-1"
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_cold_read_refreshes_once_then_reads():
    hass, entry = fake_hass()
    owner = FeederTokenOwner.for_entry(hass, "entry")
    session = fake_session(200)
    cloud = cloud_returning(tokens("access-1", "refresh-2"))
    with patch(CLOUD, return_value=cloud):
        assert await FeederClient(session, owner).get("/api/homes") == {"item": []}
    cloud.refresh_tokens.assert_awaited_once()
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "refresh-2"
    assert authorizations(session) == ["Bearer access-1"]


# --- id token for the Tuya login ---------------------------------------------


def jwt(exp):
    def part(data):
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'exp': exp})}.sig"


def with_id(access, refresh, id_token):
    return {**tokens(access, refresh), "id_token": id_token}


@pytest.mark.asyncio
async def test_id_token_comes_from_the_same_refresh_and_is_cached():
    hass, entry = fake_hass()
    idt = jwt(time.time() + 3600)
    cloud = cloud_returning(with_id("access-1", "refresh-2", idt))
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with patch(CLOUD, return_value=cloud):
        assert await owner.access_token() == "access-1"
        assert await owner.id_token() == idt
        assert await owner.id_token() == idt
    cloud.refresh_tokens.assert_awaited_once()
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "refresh-2"


@pytest.mark.asyncio
async def test_expired_id_token_refreshes_once_for_all_callers():
    hass, entry = fake_hass()
    old, new = jwt(time.time() + 30), jwt(time.time() + 3600)
    cloud = cloud_returning(with_id("a1", "r2", old), with_id("a2", "r3", new))
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with patch(CLOUD, return_value=cloud):
        assert await owner.access_token() == "a1"
        # Inside the margin: the access token stays cached, the id token not.
        results = await asyncio.gather(*(owner.id_token() for _ in range(3)))
        assert results == [new] * 3
        assert await owner.access_token() == "a2"
    assert cloud.refresh_tokens.await_count == 2
    assert cloud.refresh_tokens.await_args_list[1].args[0] == "r2"
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "r3"


@pytest.mark.asyncio
async def test_refresh_without_id_token_is_not_success_for_tuya():
    hass, _ = fake_hass()
    cloud = cloud_returning(tokens("access-1", "refresh-2"))
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with patch(CLOUD, return_value=cloud):
        with pytest.raises(CloudConnectionError):
            await owner.id_token()
        assert await owner.access_token() == "access-1"
    cloud.refresh_tokens.assert_awaited_once()


@pytest.mark.parametrize("bad", ["opaque", "a.!!!.c", jwt("soon"), jwt(True)])
@pytest.mark.asyncio
async def test_unreadable_id_token_is_handed_out_once_not_cached(bad):
    hass, _ = fake_hass()
    cloud = cloud_returning(with_id("a1", "r2", bad), with_id("a2", "r3", "y"))
    owner = FeederTokenOwner.for_entry(hass, "entry")
    with patch(CLOUD, return_value=cloud):
        assert await owner.id_token() == bad
        assert await owner.id_token() == "y"
    assert cloud.refresh_tokens.await_count == 2

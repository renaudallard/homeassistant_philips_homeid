"""Feed now: login, dial code, and never feeding twice for one press."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.philips_homeid.feeder_feed import FeederFeed
from custom_components.philips_homeid.tuya_api import (
    TuyaError,
    TuyaSession,
    TuyaSessionLost,
)

from .test_tuya_api import DUMMY, LOGIN_OK, FakeTuya

CLOUD = "custom_components.philips_homeid.cloud_api.PhilipsCloudAPI"
TENANT = {
    "spaces": [
        {"countryCode": "NL", "dialCode": "31"},
        {"countryCode": "BE", "dialCode": "32"},
    ]
}


class FakeSession:
    def __init__(self, *publish_results, alive=False):
        self.logged_in = False
        self.logins = []
        self.published = []
        self.results = list(publish_results)
        self.alive_result = alive
        self.alive_checks = 0

    async def login(self, id_token, dial_code):
        await asyncio.sleep(0)
        self.logins.append((id_token, dial_code))
        self.logged_in = True

    def forget(self):
        self.logged_in = False

    async def alive(self):
        self.alive_checks += 1
        return self.alive_result

    async def publish(self, device_id, dps):
        self.published.append((device_id, dps))
        result = self.results.pop(0) if self.results else None
        if isinstance(result, Exception):
            raise result


def make_feed(session, country="BE", tenant=TENANT):
    hass = MagicMock()
    hass.config.country = country
    owner = MagicMock()
    owner.id_token = AsyncMock(return_value="id-token")
    cloud = MagicMock()
    cloud.homeid_discovery = AsyncMock(return_value=tenant)
    cloud.close = AsyncMock()
    with (
        patch(
            "custom_components.philips_homeid.feeder_feed.TuyaSession",
            return_value=session,
        ),
        patch("custom_components.philips_homeid.feeder_feed.async_get_clientsession"),
    ):
        feed = FeederFeed(hass, owner, "tuya-1", "PAW5320")
    return feed, owner, cloud


@pytest.mark.parametrize("portions", [0, 21, -1])
@pytest.mark.asyncio
async def test_out_of_range_portions_send_nothing(portions):
    session = FakeSession()
    feed, owner, _ = make_feed(session)
    with pytest.raises(ValueError):
        await feed.feed(portions)
    owner.id_token.assert_not_called()
    assert session.published == []


@pytest.mark.asyncio
async def test_first_feed_logs_in_with_the_country_dial_code_then_publishes():
    session = FakeSession()
    feed, _, cloud = make_feed(session)
    with patch(CLOUD, return_value=cloud):
        await feed.feed(3)
        await feed.feed(20)
    assert session.logins == [("id-token", "32")]
    assert session.published == [("tuya-1", {"201": 3}), ("tuya-1", {"201": 20})]
    cloud.homeid_discovery.assert_awaited_once()
    cloud.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_presses_share_one_login():
    session = FakeSession()
    feed, _, cloud = make_feed(session)
    with patch(CLOUD, return_value=cloud):
        await asyncio.gather(feed.feed(1), feed.feed(2))
    assert len(session.logins) == 1
    assert len(session.published) == 2


@pytest.mark.asyncio
async def test_dead_session_is_dropped_and_the_feed_not_sent_again():
    session = FakeSession(None, TuyaSessionLost("x"), alive=False)
    feed, owner, cloud = make_feed(session)
    with patch(CLOUD, return_value=cloud):
        await feed.feed(1)
        with pytest.raises(TuyaSessionLost):
            await feed.feed(2)
        assert session.alive_checks == 1
        assert not session.logged_in
        assert len(session.logins) == 1
        # The next press logs in again.
        await feed.feed(3)
    assert len(session.logins) == 2
    assert session.published == [
        ("tuya-1", {"201": 1}),
        ("tuya-1", {"201": 2}),
        ("tuya-1", {"201": 3}),
    ]
    assert owner.id_token.await_count == 2
    # The dial code is looked up once.
    cloud.homeid_discovery.assert_awaited_once()


@pytest.mark.asyncio
async def test_unreadable_replies_never_publish_twice_for_one_press():
    unreadable = {"_sealed": {"result": True, "success": True}, "_ecode": "other"}
    ok = {"_sealed": {"result": True, "success": True}, "_ecode": "ec-1"}
    # Publish, then the press whose publish and session check cannot be read.
    fake = FakeTuya(LOGIN_OK, ok, unreadable, unreadable, LOGIN_OK, ok)
    feed, _, cloud = make_feed(TuyaSession(fake.http, DUMMY))
    with patch(CLOUD, return_value=cloud):
        await feed.feed(1)
        with pytest.raises(TuyaSessionLost):
            await feed.feed(2)
        assert [data["a"] for _, data in fake.calls[2:]] == [
            "thing.m.device.dp.publish",
            "m.life.home.space.list",
        ]
        assert not feed.logged_in
        await feed.feed(3)
    actions = [data["a"] for _, data in fake.calls]
    assert actions.count("thing.m.device.dp.publish") == 3
    assert actions.count("thing.m.user.third.login") == 2


@pytest.mark.parametrize("code", ["USER_SESSION_LOSS", "USER_SESSION_INVALID"])
@pytest.mark.asyncio
async def test_explicit_session_loss_drops_the_session_without_a_check(code):
    session = FakeSession(None, TuyaSessionLost("x", code))
    feed, _, cloud = make_feed(session)
    with patch(CLOUD, return_value=cloud):
        await feed.feed(1)
        with pytest.raises(TuyaSessionLost):
            await feed.feed(2)
    assert session.alive_checks == 0
    assert not session.logged_in


@pytest.mark.asyncio
async def test_explicit_session_loss_logs_in_again_on_the_next_press():
    # No session check is made, so one that fails cannot keep the session.
    lost = {"_sealed": {"success": False, "errorCode": "USER_SESSION_LOSS"}}
    ok = {"_sealed": {"result": True, "success": True}, "_ecode": "ec-1"}
    fake = FakeTuya(LOGIN_OK, lost, LOGIN_OK, ok)
    feed, _, cloud = make_feed(TuyaSession(fake.http, DUMMY))
    with patch(CLOUD, return_value=cloud):
        with pytest.raises(TuyaSessionLost):
            await feed.feed(1)
        assert not feed.logged_in
        await feed.feed(2)
    actions = [data["a"] for _, data in fake.calls]
    assert actions == [
        "thing.m.user.third.login",
        "thing.m.device.dp.publish",
        "thing.m.user.third.login",
        "thing.m.device.dp.publish",
    ]


@pytest.mark.asyncio
async def test_unreadable_reply_on_a_live_session_is_never_sent_again():
    session = FakeSession(None, TuyaSessionLost("x"), alive=True)
    feed, _, cloud = make_feed(session)
    with patch(CLOUD, return_value=cloud):
        await feed.feed(1)
        with pytest.raises(TuyaSessionLost):
            await feed.feed(2)
    assert len(session.logins) == 1
    assert session.logged_in
    assert session.published == [("tuya-1", {"201": 1}), ("tuya-1", {"201": 2})]


@pytest.mark.asyncio
async def test_other_refusals_are_not_retried():
    session = FakeSession(None, TuyaError("x", "PERMISSION_DENIED"))
    feed, _, cloud = make_feed(session)
    with patch(CLOUD, return_value=cloud):
        await feed.feed(1)
        with pytest.raises(TuyaError):
            await feed.feed(1)
    assert session.alive_checks == 0
    assert len(session.published) == 2


@pytest.mark.asyncio
async def test_session_lost_right_after_a_login_is_not_looped():
    session = FakeSession(TuyaSessionLost("x"))
    feed, _, cloud = make_feed(session)
    with patch(CLOUD, return_value=cloud), pytest.raises(TuyaSessionLost):
        await feed.feed(1)
    assert len(session.logins) == 1
    assert len(session.published) == 1


@pytest.mark.asyncio
async def test_no_country_uses_the_app_fallback_without_a_lookup():
    session = FakeSession()
    feed, _, cloud = make_feed(session, country=None)
    with patch(CLOUD, return_value=cloud):
        await feed.feed(1)
    assert session.logins == [("id-token", "31")]
    cloud.homeid_discovery.assert_not_called()


@pytest.mark.parametrize(
    "tenant",
    [{"spaces": [{"countryCode": "XX", "dialCode": "99"}]}, TENANT],
)
@pytest.mark.asyncio
async def test_unknown_country_falls_back_and_is_kept(tenant):
    session = FakeSession()
    feed, _, cloud = make_feed(session, country="US", tenant=tenant)
    with patch(CLOUD, return_value=cloud):
        await feed.feed(1)
        session.forget()
        await feed.feed(1)
    assert [dial for _, dial in session.logins] == ["31", "31"]
    cloud.homeid_discovery.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_new_ha_country_is_looked_up_again():
    session = FakeSession()
    feed, _, cloud = make_feed(session, country="NL")
    with patch(CLOUD, return_value=cloud):
        await feed.feed(1)
        session.forget()
        feed._hass.config.country = "BE"
        await feed.feed(1)
    assert [dial for _, dial in session.logins] == ["31", "32"]
    assert cloud.homeid_discovery.await_count == 2


@pytest.mark.parametrize(
    "tenant",
    [{}, {"spaces": []}, {"spaces": "x"}],
)
@pytest.mark.asyncio
async def test_unanswered_tenant_falls_back_and_is_asked_again(tenant):
    session = FakeSession()
    feed, _, cloud = make_feed(session, tenant=tenant)
    with patch(CLOUD, return_value=cloud):
        await feed.feed(1)
        session.forget()
        await feed.feed(1)
    assert [dial for _, dial in session.logins] == ["31", "31"]
    assert cloud.homeid_discovery.await_count == 2


@pytest.mark.parametrize("dial", [32, "", "3x", "12345"])
@pytest.mark.asyncio
async def test_malformed_dial_code_is_not_sent(dial):
    session = FakeSession()
    tenant = {"spaces": [{"countryCode": "BE", "dialCode": dial}]}
    feed, _, cloud = make_feed(session, tenant=tenant)
    with patch(CLOUD, return_value=cloud):
        await feed.feed(1)
    assert session.logins == [("id-token", "31")]


def test_an_entry_keeps_its_session_until_discarded():
    hass = MagicMock()
    hass.data = {}
    owner = MagicMock()
    with patch("custom_components.philips_homeid.feeder_feed.async_get_clientsession"):
        feed = FeederFeed.for_entry(hass, "entry", owner, "tuya-1", "PAW5320")
        # A reload builds a new coordinator, which gets the same session.
        assert FeederFeed.for_entry(hass, "entry", owner, "tuya-1", "PAW5320") is feed
        assert (
            FeederFeed.for_entry(hass, "other", owner, "tuya-2", "PAW5320") is not feed
        )
        FeederFeed.discard(hass, "entry")
        assert (
            FeederFeed.for_entry(hass, "entry", owner, "tuya-1", "PAW5320") is not feed
        )
    FeederFeed.discard(hass, "missing")

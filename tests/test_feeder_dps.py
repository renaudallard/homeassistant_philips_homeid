"""Feeder settings data points: motion sensitivity, high contrast and volume."""

import asyncio
import json
import logging
import math
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.philips_homeid import feeder_settings
from custom_components.philips_homeid import number as number_platform
from custom_components.philips_homeid import select as select_platform
from custom_components.philips_homeid.cloud_auth import (
    CloudAuthError,
    CloudConnectionError,
)
from custom_components.philips_homeid.const import (
    CONF_CPP_ID,
    CONF_DEVICE_ID,
    CONF_IS_FEEDER,
    CONF_MODEL,
    DOMAIN,
)
from custom_components.philips_homeid.feeder import (
    FeederCoordinator,
    FeederData,
    feeder_diagnostics,
)
from custom_components.philips_homeid.feeder_api import (
    FeederDevice,
    FeederHTTPError,
    find_feeder,
)
from custom_components.philips_homeid.feeder_feed import (
    DpSettings,
    read_settings,
    volume_dp,
)
from custom_components.philips_homeid.feeder_settings import (
    FeederCameraSwitch,
    FeederHighContrastSwitch,
    FeederMotionSelect,
    FeederVolumeNumber,
    async_setup_settings_numbers,
    async_setup_settings_selects,
    async_setup_settings_switches,
)
from custom_components.philips_homeid.tuya_api import (
    TuyaError,
    TuyaSession,
    TuyaSessionLost,
)

from .test_feeder import manager as poll_manager
from .test_feeder_api import DEVICE, registry
from .test_feeder_feed import FakeSession, make_feed
from .test_tuya_api import DUMMY, logged_in

SETTINGS = DpSettings(motion="medium", high_contrast=True, volume=5)


# --- Tuya device list --------------------------------------------------------


def device_list(*rows):
    return {"_sealed": {"result": list(rows), "success": True}, "_ecode": "ec-1"}


@pytest.mark.asyncio
async def test_device_dps_asks_the_home_list_and_keeps_only_this_device(caplog):
    caplog.set_level(logging.DEBUG)
    fake, session = await logged_in(
        device_list(
            {
                "devId": "other",
                "localKey": "secret-other",
                "dataPointInfo": {"dps": {"106": "2"}},
            },
            {
                "devId": "dev-1",
                "localKey": "secret-1",
                "dataPointInfo": {"dps": {"106": "1"}, "dpsTime": {"106": 1}},
            },
        )
    )
    assert await session.device_dps(42, "dev-1") == {"106": "1"}
    url, data = fake.calls[1]
    assert url == "https://a1.tuyaus.com/api.json"
    assert (data["a"], data["v"], data["sid"]) == (
        "m.life.my.group.device.list",
        "2.2",
        "sid-1",
    )
    assert fake.payload(1, "ec-1") == {"gid": 42}
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_other_requests_keep_version_one():
    fake, session = await logged_in(
        {"_sealed": {"result": True, "success": True}, "_ecode": "ec-1"}
    )
    await session.publish("dev-1", {"108": "0"})
    assert fake.calls[0][1]["v"] == fake.calls[1][1]["v"] == "1.0"
    assert fake.payload(1, "ec-1")["dps"] == '{"108":"0"}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (
            device_list({"devId": "other", "dataPointInfo": {"dps": {}}}),
            "Tuya home does not list",
        ),
        (device_list(), "Tuya home does not list"),
        (device_list("dev-1", {"devId": 1}), "Tuya home does not list"),
        # The SDK's bean keeps a flat dps only under a dataPointInfo.
        (device_list({"devId": "dev-1", "dps": {"106": "1"}}), "not readable"),
        (device_list({"devId": "dev-1", "dataPointInfo": None}), "not readable"),
        (device_list({"devId": "dev-1", "dataPointInfo": {}}), "not readable"),
        (
            device_list({"devId": "dev-1", "dataPointInfo": {"dps": ["106"]}}),
            "not readable",
        ),
        (device_list({"devId": "dev-1"}), "not readable"),
        (
            {
                "_sealed": {"result": {"devId": "dev-1"}, "success": True},
                "_ecode": "ec-1",
            },
            "not readable",
        ),
    ],
)
async def test_device_dps_refuses_what_it_cannot_read(reply, message):
    _, session = await logged_in(reply)
    with pytest.raises(TuyaError, match=message):
        await session.device_dps(42, "dev-1")


KEYED = {"devId": "keyed", "localKey": "secret-key"}


def keyed_reply(**reply):
    return {"_sealed": {"result": [KEYED], **reply}, "_ecode": "ec-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "kind", "message"),
    [
        (
            device_list(KEYED, {"devId": "other", "dataPointInfo": {"dps": {}}}),
            TuyaError,
            "does not list",
        ),
        (
            device_list(KEYED, {"devId": "dev-1", "dataPointInfo": None}),
            TuyaError,
            "not readable",
        ),
        (keyed_reply(), TuyaError, "not confirmed"),
        (keyed_reply(success=False), TuyaError, "refused"),
        (
            keyed_reply(success=True, errorCode="PERMISSION_DENIED"),
            TuyaError,
            "refused",
        ),
        (keyed_reply(errorCode="USER_SESSION_LOSS"), TuyaSessionLost, "expired"),
    ],
)
async def test_a_device_dps_error_keeps_no_local_key(reply, kind, message):
    _, session = await logged_in(reply)
    with pytest.raises(kind, match=message) as raised:
        await session.device_dps(42, "dev-1")
    assert raised.value.__context__ is None
    tb = raised.value.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        if frame.f_code.co_filename.endswith("tuya_api.py"):
            held = dict(frame.f_locals)
            if "self" in held:
                held["self"] = vars(held["self"])
            assert "secret" not in repr(held)
        tb = tb.tb_next


@pytest.mark.asyncio
async def test_device_dps_needs_tuya_to_say_success():
    _, session = await logged_in(
        {
            "_sealed": {"result": [{"devId": "dev-1", "dataPointInfo": {"dps": {}}}]},
            "_ecode": "ec-1",
        }
    )
    with pytest.raises(TuyaError, match="not confirmed"):
        await session.device_dps(42, "dev-1")


@pytest.mark.asyncio
async def test_device_dps_without_a_session_sends_nothing():
    http = MagicMock()
    with pytest.raises(TuyaSessionLost):
        await TuyaSession(http, DUMMY).device_dps(42, "dev-1")
    http.post.assert_not_called()


# --- Tuya home of the feeder -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("vendor_ids", "home"),
    [
        ({"tuya": "123456"}, 123456),
        ({"tuya": "+7"}, 7),
        ({"tuya": "-7"}, -7),
        ({"tuya": str((1 << 63) - 1)}, (1 << 63) - 1),
        ({"tuya": str(1 << 63)}, None),
        ({"tuya": "12.0"}, None),
        ({"tuya": "1e3"}, None),
        ({"tuya": " 12"}, None),
        ({"tuya": "+"}, None),
        ({"tuya": ""}, None),
        ({"tuya": "١٢"}, None),
        ({"tuya": 12}, 12),
        ({"tuya": 1 << 63}, None),
        ({"tuya": 12.0}, None),
        ({"tuya": True}, None),
        ({"tuya": None}, None),
        ({}, None),
        (None, None),
        ("123", None),
    ],
)
async def test_tuya_home_is_read_as_the_app_reads_it(vendor_ids, home):
    homes = [{"id": "home", "timeZone": "Europe/Riga", "vendorIds": vendor_ids}]
    feeder = await find_feeder(registry([DEVICE], homes), "tuya-1")
    assert feeder is not None
    assert feeder.tuya_home == home


# --- data point values -------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "level"),
    [
        ("0", "low"),
        ("1", "medium"),
        ("2", "high"),
        (2, "high"),
        ("3", None),
        ("", None),
        ("01", None),
        (1.0, None),
        (True, None),
        (None, None),
        ({"1": 1}, None),
    ],
)
def test_motion_sensitivity_values(value, level):
    assert read_settings({"106": value}).motion == level


@pytest.mark.parametrize(
    ("value", "on"),
    [("0", True), ("1", False), (0, True), (1, False), ("2", None), (False, None)],
)
def test_high_contrast_is_on_in_the_apps_auto_mode(value, on):
    assert read_settings({"108": value}).high_contrast is on


@pytest.mark.parametrize(
    ("value", "volume"),
    [
        (0, None),
        (-10, None),
        (1, 1),
        (4, 1),
        (5, 1),
        (14, 1),
        (15, 2),
        (25, 3),
        (55, 6),
        (94, 9),
        (95, 10),
        (100, 10),
        (250, 10),
        (55.9, 6),
        (54.9, 5),
        (0.9, None),
        ((1 << 32) + 50, 5),
        ((1 << 31) + 50, None),
        (math.inf, None),
        (math.nan, None),
        ("50", None),
        (True, None),
        (None, None),
    ],
)
def test_volume_is_the_apps_one_to_ten(value, volume):
    assert read_settings({"231": value}).volume == volume


def test_missing_data_points_are_unknown():
    assert read_settings({}) == DpSettings(None, None, None)


@pytest.mark.parametrize(("volume", "dp"), [(1, 10), (6, 60), (10, 100)])
def test_volume_is_sent_as_ten_times_the_level(volume, dp):
    assert volume_dp(volume) == dp


# --- session sharing ---------------------------------------------------------


class ReadSession(FakeSession):
    def __init__(self, *reads, publish=(), alive=False):
        super().__init__(*publish, alive=alive)
        self.reads = list(reads)
        self.read_calls = []

    async def device_dps(self, home, device_id):
        self.read_calls.append((home, device_id))
        result = self.reads.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def clock(feed_module_time, now):
    feed_module_time.monotonic.return_value = now


@pytest.mark.asyncio
async def test_read_logs_in_once_and_the_feed_reuses_the_session():
    session = ReadSession({"106": "1"}, {"106": "2"})
    feed, _, cloud = make_feed(session)
    with patch(
        "custom_components.philips_homeid.cloud_api.PhilipsCloudAPI", return_value=cloud
    ):
        assert await feed.read(42) == {"106": "1"}
        assert await feed.read(42) == {"106": "2"}
        await feed.publish({"231": 50})
        await feed.feed(2)
    assert len(session.logins) == 1
    assert session.read_calls == [(42, "tuya-1"), (42, "tuya-1")]
    assert session.published == [("tuya-1", {"231": 50}), ("tuya-1", {"201": 2})]


@pytest.mark.asyncio
async def test_a_publish_check_runs_under_the_lock_and_can_stop_it():
    session = ReadSession()
    feed, _, cloud = make_feed(session)
    steps = []

    async def refuse():
        steps.append("check")
        raise ServiceValidationError("camera off")

    with patch(
        "custom_components.philips_homeid.cloud_api.PhilipsCloudAPI", return_value=cloud
    ):
        async with feed._lock:
            publish = asyncio.ensure_future(feed.publish({"106": "0"}, refuse))
            await asyncio.sleep(0)
            steps.append("poll read")
        with pytest.raises(ServiceValidationError):
            await publish
    assert steps == ["poll read", "check"]
    # Refused before a login, which Tuya caps per day.
    assert session.published == [] and session.logins == []


@pytest.mark.asyncio
async def test_a_poll_logs_in_at_most_once_per_quarter_hour():
    session = ReadSession({"106": "1"})
    feed, owner, _ = make_feed(session, country=None)
    owner.id_token.side_effect = [CloudConnectionError("down"), "id-token", "id-token"]
    with patch("custom_components.philips_homeid.feeder_feed.time") as now:
        clock(now, 1000.0)
        with pytest.raises(CloudConnectionError):
            await feed.read(42)
        clock(now, 1899.0)
        with pytest.raises(TuyaError, match="postponed"):
            await feed.read(42)
        assert owner.id_token.await_count == 1
        # A press is never held back.
        await feed.feed(1)
        assert len(session.logins) == 1
        session.forget()
        with pytest.raises(TuyaError, match="postponed"):
            await feed.read(42)
        clock(now, 1900.0)
        assert await feed.read(42) == {"106": "1"}
    assert len(session.logins) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "alive", "kept"),
    [
        (TuyaSessionLost("x", "USER_SESSION_LOSS"), True, False),
        (TuyaSessionLost("x"), False, False),
        (TuyaSessionLost("x"), True, True),
    ],
)
async def test_a_lost_session_on_read_is_dropped_as_for_a_feed(error, alive, kept):
    session = ReadSession(error, alive=alive)
    feed, _, _ = make_feed(session, country=None)
    with pytest.raises(TuyaSessionLost):
        await feed.read(42)
    assert session.logged_in is kept


# --- coordinator -------------------------------------------------------------


def settings_coordinator(read, tuya_home=42):
    coord = FeederCoordinator.__new__(FeederCoordinator)
    coord.hass = MagicMock()
    coord.hass.config.time_zone = "Europe/Brussels"
    coord._logged = {}
    coord._meal_reads = 0
    coord._dp_changes = {}
    coord.has_camera = True
    coord.feed = MagicMock()
    coord.feed.read = AsyncMock(side_effect=read)
    coord._device = FeederDevice(
        "tuya-1", "philips-1", "home", "PAW5320", "Cat", None, tuya_home
    )
    coord.meal_manager = AsyncMock(return_value=poll_manager())
    return coord


@pytest.mark.asyncio
async def test_poll_reads_the_settings_from_the_feeders_tuya_home():
    coord = settings_coordinator([{"106": "2", "108": "1", "231": 70}])
    data = await coord._async_update_data()
    coord.feed.read.assert_awaited_once_with(42)
    assert data.settings == DpSettings("high", False, 7)
    assert data.meals_ok and data.camera is True


@pytest.mark.asyncio
async def test_poll_without_settings_reads_no_tuya():
    coord = settings_coordinator([{}])
    coord.feed = None
    data = await coord._async_update_data()
    assert data.settings is None
    coord.has_camera = False
    coord.feed = MagicMock(read=AsyncMock())
    data = await coord._async_update_data()
    assert data.settings is None
    coord.feed.read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("read", "tuya_home", "reason"),
    [
        ([{}], None, "no_tuya_home"),
        (
            TuyaError("Tuya refused the request", "PERMISSION_DENIED"),
            42,
            "tuya_permission_denied",
        ),
        (TuyaError("Tuya request was not confirmed"), 42, "tuya"),
        (TuyaError("Tuya reply was not readable"), 42, "tuya_reply"),
        (TuyaError("Tuya login postponed"), 42, "tuya_login_wait"),
        (TuyaError("Tuya home does not list the feeder"), 42, "tuya_not_listed"),
        (CloudConnectionError("down"), 42, "connection"),
    ],
)
async def test_failed_settings_read_leaves_the_rest_and_logs_a_code(
    read, tuya_home, reason, caplog
):
    coord = settings_coordinator(read, tuya_home)
    data = await coord._async_update_data()
    assert data.settings is None
    assert data.meals_ok and data.camera_ok
    assert f"settings unavailable ({reason})" in caplog.text
    assert coord._logged["settings"] == reason


@pytest.mark.asyncio
async def test_the_login_wait_does_not_hide_the_login_failure(caplog):
    refused = TuyaError("Tuya refused the request", "USER_SESSION_LIMIT")
    wait = TuyaError("Tuya login postponed")
    coord = settings_coordinator([wait, refused, wait, wait, refused])
    for _ in range(5):
        await coord._async_update_data()
    lines = [r.message for r in caplog.records if "settings unavailable" in r.message]
    assert lines == [
        "HomeID feeder settings unavailable (tuya_login_wait)",
        "HomeID feeder settings unavailable (tuya_user_session_limit)",
    ]
    assert coord._logged["settings"] == "tuya_user_session_limit"


@pytest.mark.asyncio
async def test_expired_login_on_the_settings_read_starts_reauth():
    from homeassistant.exceptions import ConfigEntryAuthFailed

    coord = settings_coordinator(CloudAuthError("expired"))
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_a_poll_never_puts_back_settings_older_than_a_change():
    coord = settings_coordinator(None)
    coord.data = FeederData(settings=SETTINGS)
    coord.last_update_success = True
    coord.async_set_updated_data = lambda data: setattr(coord, "data", data)

    async def read(home):
        # A change confirmed while this poll reads.
        coord.settings_written(volume=9)
        return {"106": "0", "108": "0", "231": 50}

    coord.feed.read = AsyncMock(side_effect=read)
    data = await coord._async_update_data()
    # The change on top of this read, which is newer than SETTINGS.
    assert data.settings == DpSettings("low", True, 9)
    coord.data = data
    coord.feed.read = AsyncMock(return_value={"106": "0", "108": "0", "231": 50})
    data = await coord._async_update_data()
    assert data.settings == DpSettings("low", True, 5)


@pytest.mark.asyncio
async def test_a_failed_read_during_a_change_leaves_the_settings_unknown():
    coord = settings_coordinator(None)
    coord.data = FeederData(settings=SETTINGS)
    coord.last_update_success = True
    coord.async_set_updated_data = lambda data: setattr(coord, "data", data)

    async def read(home):
        coord.settings_written(volume=9)
        raise TuyaError("Tuya reply was not readable")

    coord.feed.read = AsyncMock(side_effect=read)
    data = await coord._async_update_data()
    assert data.settings is None


@pytest.mark.parametrize(
    ("data", "success"),
    [(None, True), (FeederData(), True), (FeederData(settings=SETTINGS), False)],
)
def test_a_written_setting_is_not_stored_without_good_data(data, success):
    coord = settings_coordinator(None)
    coord.data = data
    coord.last_update_success = success
    coord.async_set_updated_data = MagicMock()
    coord.settings_written(volume=9)
    coord.async_set_updated_data.assert_not_called()
    assert coord._dp_changes == {}


def test_diagnostics_say_only_whether_the_settings_were_read():
    hass = MagicMock()
    entry = MagicMock(entry_id="entry", data={CONF_MODEL: "PAW5320"})
    coord = MagicMock(data=FeederData(settings=SETTINGS))
    hass.data = {DOMAIN: {"entry": coord}}
    assert feeder_diagnostics(hass, entry)["settings_available"] is True
    coord.data = FeederData()
    assert feeder_diagnostics(hass, entry)["settings_available"] is False


# --- entities ----------------------------------------------------------------


def entity_coordinator(data, publish=None):
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = True
    coord.has_camera = True
    coord.has_settings = True
    coord.config_entry.data = {
        CONF_CPP_ID: "aa:bb",
        CONF_DEVICE_ID: "tuya-1",
        CONF_MODEL: "PAW5320",
    }
    coord.config_entry.title = "Cat"
    coord.tuya = asyncio.Lock()
    coord.sent = []

    async def send(dps, check=None):
        # As FeederFeed.publish: the check runs under the Tuya lock.
        async with coord.tuya:
            if check is not None:
                await check()
            if publish is not None:
                raise publish
            coord.sent.append(dps)

    coord.feed.publish = AsyncMock(side_effect=send)
    coord.settings_written = MagicMock()
    coord.changes = asyncio.Lock()
    # HomeID's camera setting as read now, which the last poll may predate.
    coord.camera = MagicMock(camera=AsyncMock(return_value=True))
    coord.meal_manager = AsyncMock(return_value=coord.camera)
    return coord


ON = FeederData(camera=True, camera_ok=True, settings=SETTINGS)


@pytest.mark.parametrize(
    ("data", "camera_rows", "volume"),
    [
        (ON, True, True),
        (FeederData(camera=False, camera_ok=True, settings=SETTINGS), False, True),
        (FeederData(camera=None, camera_ok=False, settings=SETTINGS), False, True),
        (FeederData(camera=True, camera_ok=True), False, False),
        (
            FeederData(
                camera=True, camera_ok=True, settings=DpSettings(None, None, None)
            ),
            False,
            False,
        ),
        (None, False, False),
    ],
)
def test_availability_follows_the_value_and_the_camera(data, camera_rows, volume):
    coord = entity_coordinator(data)
    assert FeederMotionSelect(coord).available is camera_rows
    assert FeederHighContrastSwitch(coord).available is camera_rows
    assert FeederVolumeNumber(coord).available is volume


def test_entities_show_the_settings():
    coord = entity_coordinator(ON)
    select = FeederMotionSelect(coord)
    assert select.options == ["low", "medium", "high"]
    assert select.current_option == "medium"
    assert FeederHighContrastSwitch(coord).is_on is True
    number = FeederVolumeNumber(coord)
    assert number.native_value == 5
    assert (number.native_min_value, number.native_max_value) == (1, 10)
    assert number.native_step == 1
    assert {
        select.unique_id,
        FeederHighContrastSwitch(coord).unique_id,
        number.unique_id,
    } == {
        "aa:bb_feeder_motion_sensitivity",
        "aa:bb_feeder_high_contrast",
        "aa:bb_feeder_volume",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("write", "dps", "changes"),
    [
        (
            lambda c: FeederMotionSelect(c).async_select_option("low"),
            {"106": "0"},
            {"motion": "low"},
        ),
        (
            lambda c: FeederMotionSelect(c).async_select_option("high"),
            {"106": "2"},
            {"motion": "high"},
        ),
        (
            lambda c: FeederHighContrastSwitch(c).async_turn_on(),
            {"108": "0"},
            {"high_contrast": True},
        ),
        (
            lambda c: FeederHighContrastSwitch(c).async_turn_off(),
            {"108": "1"},
            {"high_contrast": False},
        ),
        (
            lambda c: FeederVolumeNumber(c).async_set_native_value(1.0),
            {"231": 10},
            {"volume": 1},
        ),
        (
            lambda c: FeederVolumeNumber(c).async_set_native_value(7.0),
            {"231": 70},
            {"volume": 7},
        ),
        (
            lambda c: FeederVolumeNumber(c).async_set_native_value(10.0),
            {"231": 100},
            {"volume": 10},
        ),
    ],
)
async def test_a_change_is_sent_once_and_shown(write, dps, changes):
    coord = entity_coordinator(ON)
    await write(coord)
    assert coord.sent == [dps]
    coord.settings_written.assert_called_once_with(**changes)
    # Only the settings the app ties to the camera read it again.
    assert coord.camera.camera.await_count == int("231" not in dps)


CAMERA_WRITES = [
    lambda c: FeederMotionSelect(c).async_select_option("low"),
    lambda c: FeederHighContrastSwitch(c).async_turn_on(),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("write", CAMERA_WRITES)
@pytest.mark.parametrize("camera", [False, None])
async def test_a_camera_setting_is_not_sent_once_the_camera_is_off(write, camera):
    coord = entity_coordinator(ON)
    coord.camera.camera.return_value = camera
    with pytest.raises(ServiceValidationError, match="camera on first"):
        await write(coord)
    assert coord.sent == []
    coord.settings_written.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("write", CAMERA_WRITES)
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (FeederHTTPError(503), "cloud unavailable"),
        (CloudConnectionError("down"), "cloud unavailable"),
        (CloudAuthError("expired"), "login expired"),
    ],
)
async def test_an_unread_camera_sends_nothing(write, error, message):
    coord = entity_coordinator(ON)
    coord.camera.camera.side_effect = error
    with pytest.raises(HomeAssistantError, match=message):
        await write(coord)
    assert coord.sent == []


@pytest.mark.asyncio
async def test_a_camera_setting_waits_for_a_camera_change_and_its_read():
    coord = entity_coordinator(ON)
    steps = []

    async def camera():
        steps.append("read")
        return False

    coord.camera.camera.side_effect = camera
    async with coord.changes:
        write = asyncio.ensure_future(
            FeederMotionSelect(coord).async_select_option("low")
        )
        await asyncio.sleep(0)
        steps.append("camera off")
    with pytest.raises(ServiceValidationError):
        await write
    assert steps == ["camera off", "read"]
    assert coord.sent == []


@pytest.mark.asyncio
async def test_a_camera_setting_waits_for_a_poll_holding_the_tuya_lock():
    coord = entity_coordinator(ON)
    async with coord.tuya:
        write = asyncio.ensure_future(
            FeederMotionSelect(coord).async_select_option("low")
        )
        for _ in range(3):
            await asyncio.sleep(0)
        # The poll holding the lock read the camera off, as HomeID now has it.
        coord.camera.camera.return_value = False
    with pytest.raises(ServiceValidationError, match="camera on first"):
        await write
    assert coord.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            TuyaError("Tuya refused the request", "DEVICE_OFFLINE"),
            "Tuya did not confirm",
        ),
        (TuyaSessionLost("Tuya session expired"), "Tuya did not confirm"),
        (CloudConnectionError("down"), "cloud unavailable"),
        (CloudAuthError("expired"), "login expired"),
    ],
)
async def test_an_unconfirmed_change_is_an_error_and_not_shown(error, message, caplog):
    coord = entity_coordinator(ON, publish=error)
    with pytest.raises(HomeAssistantError, match=message):
        await FeederVolumeNumber(coord).async_set_native_value(3.0)
    coord.feed.publish.assert_awaited_once()
    coord.settings_written.assert_not_called()
    if isinstance(error, TuyaError):
        assert f"HomeID setting failed ({error.code or 'no code'})" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "write",
    [
        lambda c: FeederMotionSelect(c).async_select_option("auto"),
        lambda c: FeederVolumeNumber(c).async_set_native_value(0.0),
        lambda c: FeederVolumeNumber(c).async_set_native_value(11.0),
        lambda c: FeederVolumeNumber(c).async_set_native_value(5.5),
        lambda c: FeederVolumeNumber(c).async_set_native_value(math.nan),
    ],
)
async def test_invalid_values_send_nothing(write):
    coord = entity_coordinator(ON)
    with pytest.raises(ServiceValidationError):
        await write(coord)
    coord.feed.publish.assert_not_awaited()
    coord.settings_written.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_settings", [True, False])
async def test_setup_adds_the_settings_only_where_they_apply(has_settings):
    coord = entity_coordinator(None)
    coord.has_settings = has_settings
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coord}}
    entry = MagicMock(entry_id="entry")
    added = []
    await async_setup_settings_switches(hass, entry, added.extend)
    await async_setup_settings_selects(hass, entry, added.extend)
    await async_setup_settings_numbers(hass, entry, added.extend)
    assert [type(e) for e in added] == (
        [
            FeederCameraSwitch,
            FeederHighContrastSwitch,
            FeederMotionSelect,
            FeederVolumeNumber,
        ]
        if has_settings
        else [FeederCameraSwitch]
    )


@pytest.mark.asyncio
async def test_feeder_entries_get_the_select_and_the_sound_number():
    coord = entity_coordinator(None)
    coord.feed.portions = 1
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coord}}
    entry = MagicMock(entry_id="entry", data={CONF_IS_FEEDER: True})
    added = []
    await select_platform.async_setup_entry(hass, entry, added.extend)
    await number_platform.async_setup_entry(hass, entry, added.extend)
    assert [e.unique_id for e in added] == [
        "aa:bb_feeder_motion_sensitivity",
        "aa:bb_feeder_portions",
        "aa:bb_feeder_volume",
    ]


@pytest.mark.parametrize(
    ("model", "has_settings"), [("PAW5320", True), ("PAW3300", False), (None, False)]
)
def test_only_feeders_with_camera_and_feed_have_the_settings(model, has_settings):
    entry = MagicMock(entry_id="entry")
    entry.data = {CONF_DEVICE_ID: "tuya-1", CONF_MODEL: model}
    with (
        patch("custom_components.philips_homeid.feeder.async_get_clientsession"),
        patch("custom_components.philips_homeid.feeder_feed.async_get_clientsession"),
        patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ),
    ):
        coord = FeederCoordinator(MagicMock(data={}), entry)
    assert coord.has_settings is has_settings


@pytest.mark.asyncio
async def test_a_reload_keeps_the_session_and_the_poll_login_wait():
    hass = MagicMock(data={})
    hass.config.country = None  # no dial code lookup
    entry = MagicMock(entry_id="entry")
    entry.data = {CONF_DEVICE_ID: "tuya-1", CONF_MODEL: "PAW5320"}
    session = ReadSession({"106": "1"})
    with (
        patch("custom_components.philips_homeid.feeder.async_get_clientsession"),
        patch("custom_components.philips_homeid.feeder_feed.async_get_clientsession"),
        patch(
            "custom_components.philips_homeid.feeder_feed.TuyaSession",
            return_value=session,
        ),
        patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
            return_value=None,
        ),
        patch("custom_components.philips_homeid.feeder_feed.time") as now,
    ):
        first = FeederCoordinator(hass, entry)
        first.feed._owner.id_token = AsyncMock(side_effect=CloudConnectionError("x"))
        clock(now, 1000.0)
        with pytest.raises(CloudConnectionError):
            await first.feed.read(42)
        reloaded = FeederCoordinator(hass, entry)
        assert reloaded.feed is first.feed
        with pytest.raises(TuyaError, match="postponed"):
            await reloaded.feed.read(42)
    assert first.feed._owner.id_token.await_count == 1


def test_every_setting_has_a_name_in_every_language():
    base = Path(feeder_settings.__file__).parent
    for path in [
        base / "strings.json",
        *sorted((base / "translations").glob("*.json")),
    ]:
        entity = json.loads(path.read_text(encoding="utf-8"))["entity"]
        select = entity["select"]["feeder_motion_sensitivity"]
        assert select["name"], path
        assert set(select["state"]) == {"low", "medium", "high"}, path
        assert all(select["state"].values()), path
        assert entity["switch"]["feeder_high_contrast"]["name"], path
        assert entity["number"]["feeder_volume"]["name"], path

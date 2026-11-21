"""Feeder settings on the pet REST backend: camera switch and desiccant reset."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import MethodType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.philips_homeid import feeder_settings
from custom_components.philips_homeid.cloud_auth import (
    CloudAuthError,
    CloudConnectionError,
)
from custom_components.philips_homeid.const import (
    CONF_CPP_ID,
    CONF_DEVICE_ID,
    CONF_MODEL,
    DOMAIN,
)
from custom_components.philips_homeid.feeder import FeederCoordinator, FeederData
from custom_components.philips_homeid.feeder_api import FeederError, FeederHTTPError
from custom_components.philips_homeid.feeder_meals import MealManager, MealsError
from custom_components.philips_homeid.feeder_settings import (
    FeederCameraSwitch,
    FeederDesiccantButton,
    async_setup_settings_buttons,
    async_setup_settings_switches,
)

ROOT = "/api/homes/home-1"


def camera_row(device="philips-1", value=True, **changes):
    return {
        "id": device,
        "name": "Cat",
        "settings": {"device_active": {"category": "camera", "value": value}},
        **changes,
    }


def manager(reply=None, write=None):
    request = AsyncMock(return_value=reply)
    return MealManager(request, "home-1", "philips-1", None, write), request


# --- camera read -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, False])
async def test_camera_is_this_devices_device_active(value):
    rows = [camera_row("other", not value), camera_row(value=value)]
    m, request = manager({"item": rows})
    assert await m.camera() is value
    request.assert_awaited_once_with("GET", f"{ROOT}/modes/home/devices")


@pytest.mark.asyncio
async def test_camera_takes_the_first_row_of_this_device_like_the_app():
    m, _ = manager({"item": [camera_row(value=False), camera_row(value=True)]})
    assert await m.camera() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [camera_row("other")]])
async def test_no_row_for_this_device_is_unknown(rows):
    m, _ = manager({"item": rows})
    assert await m.camera() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        {"device_active": {"category": "camera", "value": None}},
        {"device_active": {"category": "camera"}},
        {"device_active": None},
        {},
        {"other": {"value": True}},
    ],
)
async def test_a_row_without_a_value_reads_off_as_in_the_app(settings):
    m, _ = manager({"item": [camera_row(settings=settings)]})
    assert await m.camera() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        camera_row(settings={"device_active": {"category": 3, "value": True}}),
        camera_row(settings={"device_active": {"value": True}, "x": None}),
        camera_row(name=None),
        camera_row(name=5),
    ],
)
async def test_what_the_apps_reader_takes_is_taken(row):
    m, _ = manager({"item": [row]})
    assert await m.camera() is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        None,
        [],
        {"device_active": True},
        {"device_active": {"value": "true"}},
        {"device_active": {"value": 1}},
        {"device_active": {"category": True, "value": True}},
        {"device_active": {"category": {}, "value": True}},
        {"device_active": {"value": True}, "other": {"value": "x"}},
    ],
)
async def test_camera_rows_must_have_the_apps_shape(settings):
    m, _ = manager({"item": [camera_row(settings=settings)]})
    with pytest.raises(MealsError, match="Unsupported camera setting response"):
        await m.camera()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        camera_row(None),
        camera_row(True),
        camera_row({}),
        camera_row(name=True),
        camera_row(name={}),
        {"id": "philips-1", "name": "Cat"},
    ],
)
async def test_rows_the_apps_reader_refuses_fail_the_read(row):
    m, _ = manager({"item": [camera_row(), row]})
    with pytest.raises(MealsError, match="Unsupported camera setting response"):
        await m.camera()


@pytest.mark.asyncio
async def test_a_malformed_row_of_another_device_fails_the_read_as_in_the_app():
    rows = [camera_row("other", settings={"device_active": 1}), camera_row()]
    m, _ = manager({"item": rows})
    with pytest.raises(MealsError):
        await m.camera()


@pytest.mark.asyncio
async def test_odd_ids_of_other_devices_are_read_like_the_app():
    m, _ = manager({"item": [camera_row(""), camera_row(7), camera_row(value=False)]})
    assert await m.camera() is False


@pytest.mark.asyncio
async def test_a_numeric_id_is_compared_as_text_like_the_app():
    m = MealManager(AsyncMock(return_value={"item": [camera_row(7)]}), "h", "7", None)
    assert await m.camera() is True


# --- writes ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_camera_switch_sends_the_apps_patch(enabled):
    write = AsyncMock()
    m, _ = manager(write=write)
    await m.set_camera(enabled)
    write.assert_awaited_once_with(
        "PATCH",
        f"{ROOT}/modes/home/devices/philips-1",
        {"settings": {"device_active": {"value": enabled}}},
    )


@pytest.mark.asyncio
async def test_desiccant_reset_sends_the_apps_post():
    write = AsyncMock()
    m, _ = manager(write=write)
    await m.reset_desiccant(date(2026, 11, 21))
    write.assert_awaited_once_with(
        "POST",
        f"{ROOT}/devices/philips-1/desiccant-timer",
        {"lastReplacedDate": "2026-11-21"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda m: m.set_camera(1),
        lambda m: m.set_camera("true"),
        lambda m: m.reset_desiccant("2026-11-21"),
        lambda m: m.reset_desiccant(datetime(2026, 11, 21, tzinfo=UTC)),
    ],
)
async def test_bad_values_are_refused_before_anything_is_sent(call):
    write = AsyncMock()
    m, _ = manager(write=write)
    with pytest.raises(MealsError):
        await call(m)
    write.assert_not_called()


@pytest.mark.asyncio
async def test_settings_need_a_writer():
    m, _ = manager()
    with pytest.raises(MealsError, match="changes are not available"):
        await m.set_camera(True)


# --- coordinator -------------------------------------------------------------


def poll_manager(camera):
    m = MagicMock()
    m.time_zone = None
    m.list_meals = AsyncMock(return_value=[])
    m.history = AsyncMock(return_value=[])
    m.status_events = AsyncMock(return_value=[])
    m.desiccant = AsyncMock(return_value=None)
    m.camera = AsyncMock(**camera)
    return m


def poll_coordinator(m, has_camera=True):
    coord = FeederCoordinator.__new__(FeederCoordinator)
    coord.hass = MagicMock()
    coord.hass.config.time_zone = "Europe/Brussels"
    coord._logged = {}
    coord._meal_reads = 0
    coord._dp_writes = 0
    coord.feed = None
    coord.has_camera = has_camera
    coord.meal_manager = AsyncMock(return_value=m)
    return coord


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("camera", "expected"),
    [
        ({"return_value": True}, (True, True)),
        ({"return_value": False}, (False, True)),
        ({"return_value": None}, (None, True)),
        ({"side_effect": FeederHTTPError(503)}, (None, False)),
        (
            {"side_effect": MealsError("Unsupported camera setting response")},
            (None, False),
        ),
    ],
)
async def test_poll_reads_the_camera_as_its_own_part(camera, expected):
    m = poll_manager(camera)
    data = await poll_coordinator(m)._async_update_data()
    assert (data.camera, data.camera_ok) == expected
    assert data.meals_ok and data.history_ok and data.status_ok


@pytest.mark.asyncio
async def test_camera_shape_failures_log_a_fixed_code(caplog):
    m = poll_manager({"side_effect": MealsError("Unsupported camera setting response")})
    await poll_coordinator(m)._async_update_data()
    assert "camera unavailable (camera_shape)" in caplog.text


@pytest.mark.asyncio
async def test_feeders_without_a_camera_never_read_it():
    m = poll_manager({"return_value": True})
    data = await poll_coordinator(m, has_camera=False)._async_update_data()
    m.camera.assert_not_called()
    assert not data.camera_ok


@pytest.mark.parametrize(("model", "camera"), [("PAW5320", True), ("PAW3300", False)])
def test_camera_models_follow_the_apps_list(model, camera):
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
        coord = FeederCoordinator(MagicMock(), entry)
    assert coord.has_camera is camera


# --- entities ----------------------------------------------------------------


def entity_coordinator(data, change=None):
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = True
    coord.has_camera = True
    coord.has_settings = False
    coord.config_entry.data = {
        CONF_CPP_ID: "aa:bb",
        CONF_DEVICE_ID: "tuya-1",
        CONF_MODEL: "PAW5320",
    }
    coord.config_entry.title = "Cat"
    m = MagicMock()
    m.set_camera = AsyncMock(side_effect=change)
    m.reset_desiccant = AsyncMock(side_effect=change)
    m.time_zone = None
    coord.meal_manager = AsyncMock(return_value=m)
    coord.async_request_refresh = AsyncMock()
    coord.hass.config.time_zone = "Europe/Brussels"
    coord.home_zone = MethodType(FeederCoordinator.home_zone, coord)
    return coord, m


@pytest.mark.parametrize(
    ("data", "available", "on"),
    [
        (FeederData(camera=True, camera_ok=True), True, True),
        (FeederData(camera=False, camera_ok=True), True, False),
        (FeederData(camera=None, camera_ok=True), False, None),
        (FeederData(camera=True, camera_ok=False), False, True),
        (None, False, None),
    ],
)
def test_camera_switch_state(data, available, on):
    coord, _ = entity_coordinator(data)
    switch = FeederCameraSwitch(coord)
    assert switch.available is available
    assert switch.is_on is on
    assert switch.unique_id == "aa:bb_feeder_camera"


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "enabled"), [("on", True), ("off", False)])
async def test_camera_switch_writes_then_reads_again(method, enabled):
    coord, m = entity_coordinator(FeederData(camera=not enabled, camera_ok=True))
    await getattr(FeederCameraSwitch(coord), f"async_turn_{method}")()
    m.set_camera.assert_awaited_once_with(enabled)
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        FeederHTTPError(500),
        FeederHTTPError(409),
        FeederError("x"),
        CloudConnectionError("x"),
    ],
)
async def test_unconfirmed_camera_change_is_reported_and_read_again(error):
    coord, _ = entity_coordinator(FeederData(camera=True, camera_ok=True), error)
    with pytest.raises(HomeAssistantError, match="did not confirm the camera"):
        await FeederCameraSwitch(coord).async_turn_off()
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_camera_change_with_an_expired_login_is_not_read_again():
    coord, _ = entity_coordinator(None, CloudAuthError("x"))
    with pytest.raises(HomeAssistantError, match="login expired"):
        await FeederCameraSwitch(coord).async_turn_on()
    coord.async_request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_unresolved_feeder_sends_nothing():
    coord, m = entity_coordinator(None)
    coord.meal_manager.side_effect = FeederError("HomeID feeder was not found")
    with pytest.raises(HomeAssistantError, match="cloud unavailable"):
        await FeederDesiccantButton(coord).async_press()
    m.reset_desiccant.assert_not_called()
    coord.async_request_refresh.assert_not_called()


# 23:30 UTC on 20 November is already 21 November in Tokyo.
INSTANT = datetime(2026, 11, 20, 23, 30, tzinfo=UTC)
CLOCK = "custom_components.philips_homeid.feeder_meals._utcnow"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("zone", "day"),
    [
        ("Asia/Tokyo", date(2026, 11, 21)),
        ("Europe/Brussels", date(2026, 11, 21)),
        ("America/New_York", date(2026, 11, 20)),
    ],
)
async def test_desiccant_reset_sends_today_in_the_homes_zone(zone, day):
    # A poll before the home moved still holds the old zone.
    coord, m = entity_coordinator(FeederData(time_zone="Pacific/Kiritimati"))
    m.time_zone = zone
    with patch(CLOCK, return_value=INSTANT):
        await FeederDesiccantButton(coord).async_press()
    m.reset_desiccant.assert_awaited_once_with(day)
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "zone",
    [None, "", "Not/AZone", "/etc/localtime", "../etc/passwd", "localtime", "UTC\x00"],
)
async def test_a_zone_that_is_not_an_iana_name_sends_nothing(zone):
    coord, m = entity_coordinator(FeederData(time_zone="Europe/Brussels"))
    m.time_zone = zone
    coord.hass.config.time_zone = zone
    with (
        patch(CLOCK, return_value=INSTANT),
        pytest.raises(ServiceValidationError, match="IANA time zone"),
    ):
        await FeederDesiccantButton(coord).async_press()
    m.reset_desiccant.assert_not_called()
    coord.async_request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_desiccant_reset_falls_back_to_the_ha_zone():
    coord, m = entity_coordinator(FeederData())
    coord.hass.config.time_zone = "Asia/Tokyo"
    with patch(CLOCK, return_value=INSTANT):
        await FeederDesiccantButton(coord).async_press()
    m.reset_desiccant.assert_awaited_once_with(date(2026, 11, 21))


@pytest.mark.asyncio
async def test_unconfirmed_desiccant_reset_is_reported_and_read_again():
    data = FeederData(time_zone="Europe/Brussels")
    coord, _ = entity_coordinator(data, FeederHTTPError(404))
    with pytest.raises(HomeAssistantError, match="did not confirm the desiccant"):
        await FeederDesiccantButton(coord).async_press()
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_refused_value_is_a_validation_error():
    coord, _ = entity_coordinator(FeederData(), MealsError("Enabled must be a boolean"))
    with pytest.raises(ServiceValidationError):
        await FeederCameraSwitch(coord).async_turn_on()
    coord.async_request_refresh.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_camera", [True, False])
async def test_setup_adds_the_camera_switch_only_with_a_camera(has_camera):
    coord, _ = entity_coordinator(None)
    coord.has_camera = has_camera
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coord}}
    entry = MagicMock(entry_id="entry")
    added = []
    await async_setup_settings_switches(hass, entry, added.extend)
    await async_setup_settings_buttons(hass, entry, added.extend)
    assert [type(e) for e in added] == (
        [FeederCameraSwitch, FeederDesiccantButton]
        if has_camera
        else [FeederDesiccantButton]
    )


def test_every_settings_entity_has_a_name_in_every_language():
    base = Path(feeder_settings.__file__).parent
    for path in [
        base / "strings.json",
        *sorted((base / "translations").glob("*.json")),
    ]:
        entity = json.loads(path.read_text(encoding="utf-8"))["entity"]
        assert entity["switch"]["feeder_camera"]["name"], path
        assert entity["button"]["feeder_desiccant_reset"]["name"], path

"""PAW5320 feeder entry: setup, polling, entities, config and options flows."""

import logging
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.calendar import trigger as calendar_trigger
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.philips_homeid.calendar import (
    FeederHistoryCalendar,
    FeederScheduleCalendar,
)
from custom_components.philips_homeid.cloud_auth import (
    CloudAuthError,
    CloudConnectionError,
)
from custom_components.philips_homeid.config_flow import PhilipsHomeIDConfigFlow
from custom_components.philips_homeid.const import (
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_CPP_ID,
    CONF_DEVICE_ID,
    CONF_IS_FEEDER,
    CONF_IS_FUSION,
    CONF_MODEL,
    DOMAIN,
)
from custom_components.philips_homeid.feeder import (
    _TIMESTAMPS,
    FEEDER_PLATFORMS,
    FeederConnectivitySensor,
    FeederCoordinator,
    FeederData,
    FeederFeedButton,
    FeederPortionsNumber,
    FeederTimestampSensor,
    async_setup_feeder_buttons,
    async_setup_feeder_numbers,
)
from custom_components.philips_homeid.feeder_api import (
    FeederAuthRejected,
    FeederDevice,
    FeederError,
    FeederHTTPError,
)
from custom_components.philips_homeid.feeder_feed import FeederFeed
from custom_components.philips_homeid.feeder_meals import (
    DesiccantTimer,
    FeedingEvent,
    Meal,
    MealsError,
)
from custom_components.philips_homeid.tuya_api import TuyaError

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
TODAY = date(2026, 11, 14)
FEEDER = FeederDevice("tuya-1", "philips-1", "home", "PAW5320", "Cat", "Europe/Riga")


def manager():
    m = MagicMock()
    m.time_zone = "Europe/Riga"
    m.list_meals = AsyncMock(return_value=[Meal("m", "Lunch", 2, "10:00", (1,), True)])
    m.history = AsyncMock(return_value=[FeedingEvent("e", NOW, "Lunch", 2, "schedule")])
    m.latest_status = AsyncMock(return_value={"device_online": NOW})
    m.desiccant = AsyncMock(return_value=DesiccantTimer(TODAY, None))
    return m


def bare_coordinator(m):
    coord = FeederCoordinator.__new__(FeederCoordinator)
    coord.hass = MagicMock()
    coord.hass.config.time_zone = "Europe/Brussels"
    coord._logged = {}
    coord.meal_manager = AsyncMock(return_value=m)
    return coord


# --- polling ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_reads_every_part():
    m = manager()
    data = await bare_coordinator(m)._async_update_data()
    assert data.meals_ok and data.history_ok and data.status_ok
    assert data.desiccant == DesiccantTimer(TODAY, None)
    assert data.time_zone == "Europe/Riga" and data.time_zone_from_home
    start, end = m.history.await_args.args
    assert end - start == timedelta(days=7)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "part", ["list_meals", "history", "latest_status", "desiccant"]
)
async def test_one_failing_part_leaves_the_rest_available(part):
    m = manager()
    getattr(m, part).side_effect = FeederHTTPError(503)
    data = await bare_coordinator(m)._async_update_data()
    flags = {
        "list_meals": data.meals_ok,
        "history": data.history_ok,
        "latest_status": data.status_ok,
        "desiccant": data.desiccant is not None,
    }
    assert [name for name, ok in flags.items() if not ok] == [part]


@pytest.mark.asyncio
async def test_home_without_time_zone_uses_the_ha_time_zone():
    m = manager()
    m.time_zone = None
    data = await bare_coordinator(m)._async_update_data()
    assert data.time_zone == "Europe/Brussels" and not data.time_zone_from_home


@pytest.mark.asyncio
async def test_failed_history_is_unavailable_not_empty():
    m = manager()
    m.history.side_effect = MealsError("Invalid feeding event amount")
    data = await bare_coordinator(m)._async_update_data()
    assert not data.history_ok and data.history == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (CloudAuthError("dead"), ConfigEntryAuthFailed),
        (CloudConnectionError("down"), UpdateFailed),
        (FeederError("HomeID feeder was not found"), UpdateFailed),
        (FeederAuthRejected(401), UpdateFailed),
    ],
)
async def test_registry_failures(error, expected):
    coord = bare_coordinator(manager())
    coord.meal_manager.side_effect = error
    with pytest.raises(expected):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_expired_login_in_a_part_starts_reauth():
    m = manager()
    m.latest_status.side_effect = CloudAuthError("dead")
    with pytest.raises(ConfigEntryAuthFailed):
        await bare_coordinator(m)._async_update_data()
    m.desiccant.assert_not_called()


@pytest.mark.asyncio
async def test_warnings_are_fixed_codes_once_per_change(caplog):
    m = manager()
    m.history.side_effect = MealsError("Invalid feeding event amount")
    m.latest_status.side_effect = FeederHTTPError(500)
    coord = bare_coordinator(m)
    with caplog.at_level(logging.WARNING):
        await coord._async_update_data()
        await coord._async_update_data()
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "HomeID feeder history unavailable (event_amount)",
        "HomeID feeder status unavailable (http_500)",
    ]
    m.history.side_effect = MealsError("Conflicting feeding event identity")
    with caplog.at_level(logging.WARNING):
        caplog.clear()
        await coord._async_update_data()
    assert caplog.records[0].getMessage().endswith("(event_duplicate)")


@pytest.mark.asyncio
async def test_identity_is_cached_then_resolved_again():
    hass = MagicMock()
    hass.data = {}
    entry = MagicMock(entry_id="entry", data={CONF_DEVICE_ID: "tuya-1"})
    with patch(
        "custom_components.philips_homeid.feeder.async_get_clientsession",
        return_value=MagicMock(),
    ):
        coord = FeederCoordinator(hass, entry)
    find = AsyncMock(return_value=FEEDER)
    with patch("custom_components.philips_homeid.feeder.find_feeder", find):
        first = await coord.meal_manager()
        assert await coord.meal_manager() is first
        assert await coord.meal_manager(force_refresh=True) is not first
        find.return_value = None
        with pytest.raises(FeederError):
            await coord.meal_manager(force_refresh=True)
    assert find.await_args.args[1] == "tuya-1"


# --- entities --------------------------------------------------------------


def entity_coordinator(data):
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = True
    coord.config_entry.data = {
        CONF_CPP_ID: "aa:bb",
        CONF_DEVICE_ID: "tuya-1",
        CONF_MODEL: "PAW5320",
    }
    coord.config_entry.title = "Cat"
    return coord


def spec(key):
    return next(s for s in _TIMESTAMPS if s.key == key)


def test_timestamp_sensors_report_latest_or_unknown():
    data = FeederData(
        history=[
            FeedingEvent("a", NOW - timedelta(hours=2), "x", None, ""),
            FeedingEvent("b", NOW, "y", 3, "manual"),
        ],
        history_ok=True,
        status={"food_level_low": NOW},
        status_ok=True,
        desiccant=DesiccantTimer(None, TODAY),
    )
    coord = entity_coordinator(data)
    values = {s.key: FeederTimestampSensor(coord, s).native_value for s in _TIMESTAMPS}
    assert values == {
        "feeder_last_feeding": NOW,
        "feeder_last_food_low": NOW,
        "feeder_last_outlet_stuck": None,
        "feeder_desiccant_replacement": None,
    }
    last = FeederTimestampSensor(coord, spec("feeder_last_feeding"))
    assert last.unique_id == "aa:bb_feeder_last_feeding"
    recent = last.extra_state_attributes["recent_feedings"]
    assert [item["time"] for item in recent] == [
        NOW.isoformat(),
        (NOW - timedelta(hours=2)).isoformat(),
    ]
    assert "grams" not in str(recent)
    desiccant = FeederTimestampSensor(coord, spec("feeder_desiccant_replacement"))
    assert desiccant.device_class == "date"
    assert desiccant.extra_state_attributes == {"last_replaced": TODAY.isoformat()}


def test_failed_reads_make_entities_unavailable():
    coord = entity_coordinator(FeederData())
    for s in _TIMESTAMPS:
        assert not FeederTimestampSensor(coord, s).available
    assert not FeederConnectivitySensor(coord).available


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({}, None),
        ({"device_online": NOW}, True),
        ({"device_offline": NOW}, False),
        ({"device_online": NOW, "device_offline": NOW - timedelta(1)}, True),
        ({"device_online": NOW - timedelta(1), "device_offline": NOW}, False),
        ({"device_online": NOW, "device_offline": NOW}, None),
    ],
)
def test_connectivity_follows_the_latest_report(status, expected):
    sensor = FeederConnectivitySensor(
        entity_coordinator(FeederData(status=status, status_ok=True))
    )
    assert sensor.available and sensor.is_on is expected


# --- setup, unload, diagnostics ---------------------------------------------


@pytest.mark.asyncio
async def test_setup_and_unload_use_only_feeder_platforms():
    from custom_components.philips_homeid import async_setup_entry, async_unload_entry

    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    entry = MagicMock(entry_id="entry", data={CONF_IS_FEEDER: True})
    coord = MagicMock()
    coord.async_config_entry_first_refresh = AsyncMock()
    coord.async_shutdown = AsyncMock()
    with patch(
        "custom_components.philips_homeid.feeder.FeederCoordinator", return_value=coord
    ):
        assert await async_setup_entry(hass, entry)
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, FEEDER_PLATFORMS
    )
    assert {str(p) for p in FEEDER_PLATFORMS} == {
        "binary_sensor",
        "button",
        "calendar",
        "number",
        "sensor",
    }
    assert await async_unload_entry(hass, entry)
    coord.async_shutdown.assert_awaited_once()
    assert "entry" not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_failed_first_read_propagates_and_registers_nothing():
    from custom_components.philips_homeid import async_setup_entry

    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = MagicMock(entry_id="entry", data={CONF_IS_FEEDER: True})
    coord = MagicMock()
    coord.async_config_entry_first_refresh = AsyncMock(
        side_effect=ConfigEntryAuthFailed
    )
    with (
        patch(
            "custom_components.philips_homeid.feeder.FeederCoordinator",
            return_value=coord,
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, entry)
    assert "entry" not in hass.data[DOMAIN]
    hass.config_entries.async_forward_entry_setups.assert_not_called()


@pytest.mark.asyncio
async def test_removal_discards_the_token_owner():
    from custom_components.philips_homeid import async_remove_entry
    from custom_components.philips_homeid.feeder_api import (
        DATA_FEEDER_AUTH,
        FeederTokenOwner,
    )

    hass = MagicMock()
    hass.data = {}
    FeederTokenOwner.for_entry(hass, "entry")
    await async_remove_entry(
        hass, MagicMock(entry_id="entry", data={CONF_IS_FEEDER: True})
    )
    assert hass.data[DATA_FEEDER_AUTH] == {}


@pytest.mark.asyncio
async def test_diagnostics_are_an_allowlist():
    from custom_components.philips_homeid.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    coord = entity_coordinator(
        FeederData(
            meals=[Meal("secret-meal", "Secret", 1, "08:00", (1,), True)],
            meals_ok=True,
            history=[FeedingEvent("secret-event", NOW, "Secret", 1, "secret")],
            history_ok=True,
            time_zone="Europe/Riga",
        )
    )
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coord}}
    entry = MagicMock(entry_id="entry", title="Secret name")
    entry.data = {
        CONF_IS_FEEDER: True,
        CONF_MODEL: "PAW5320",
        CONF_DEVICE_ID: "secret-device",
        CONF_CPP_ID: "secret-mac",
        CONF_CLOUD_REFRESH_TOKEN: "secret-token",
    }
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert "secret" not in str(result).lower()
    assert "europe" not in str(result).lower()
    assert result["meal_count"] == 1 and result["history_count"] == 1


# --- config flow -----------------------------------------------------------


def config_flow():
    flow = PhilipsHomeIDConfigFlow()
    flow.hass = MagicMock()
    flow._cloud_tokens = {"access_token": "access", "refresh_token": "refresh"}
    flow._cloud_api = MagicMock()
    flow._cloud_api.find_device = AsyncMock(return_value=None)
    flow._cloud_api.close = AsyncMock()
    flow._set_unique_id_or_abort = AsyncMock()
    flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)
    return flow


APPLIANCE = {"externalDeviceId": "tuya-1", "macAddress": "aa:bb", "name": "x"}
DISCOVER = "custom_components.philips_homeid.feeder_api.discover_feeder"


@pytest.mark.asyncio
async def test_fusion_miss_creates_a_verified_feeder_entry():
    flow = config_flow()
    discover = AsyncMock(return_value=FEEDER)
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession"),
        patch(DISCOVER, discover),
    ):
        errors: dict[str, str] = {}
        result = await flow._create_entry_from_homeid(APPLIANCE, errors)
    assert not errors
    assert result["title"] == "Cat"
    assert result["data"] == {
        CONF_IS_FEEDER: True,
        CONF_DEVICE_ID: "tuya-1",
        CONF_CPP_ID: "aa:bb",
        CONF_MODEL: "PAW5320",
        CONF_CLOUD_REFRESH_TOKEN: "refresh",
        "oauth_client": "homeid",
    }
    assert CONF_IS_FUSION not in result["data"]
    assert discover.await_args.args[1:] == ("access", "tuya-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (None, "cloud_credentials_not_found"),
        # Nothing says a device is a feeder until the registry answers, so a
        # failed lookup is reported like any unresolved device.
        (FeederError("x"), "cloud_credentials_not_found"),
        (FeederAuthRejected(401), "cloud_credentials_not_found"),
    ],
)
async def test_non_feeders_and_lookup_failures_create_no_entry(outcome, error):
    flow = config_flow()
    discover = AsyncMock(
        side_effect=outcome if isinstance(outcome, Exception) else None,
        return_value=outcome,
    )
    with (
        patch("homeassistant.helpers.aiohttp_client.async_get_clientsession"),
        patch(DISCOVER, discover),
    ):
        errors: dict[str, str] = {}
        assert await flow._create_entry_from_homeid(APPLIANCE, errors) is None
    assert errors == {"base": error}
    flow.async_create_entry.assert_not_called()


@pytest.mark.asyncio
async def test_iot_device_found_never_queries_the_pet_registry():
    flow = config_flow()
    flow._cloud_api.find_device.return_value = {"thingName": "thing", "ctn": "HD"}
    discover = AsyncMock()
    with patch(DISCOVER, discover):
        result = await flow._create_entry_from_homeid(APPLIANCE, {})
    discover.assert_not_called()
    assert result["data"][CONF_IS_FUSION] is True


# --- calendars -------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_calendar_splits_ranges_and_bounds_them():
    m = manager()
    m.history = AsyncMock(return_value=[])
    coord = entity_coordinator(FeederData(history_ok=True))
    coord.meal_manager = AsyncMock(return_value=m)
    calendar = FeederHistoryCalendar(coord)
    start = datetime(2026, 11, 1, tzinfo=UTC)
    end = start + timedelta(days=42)
    assert await calendar.async_get_events(MagicMock(), start, end) == []
    assert [call.args for call in m.history.await_args_list] == [
        (start, start + timedelta(days=31)),
        (start + timedelta(days=31), end),
    ]
    with pytest.raises(HomeAssistantError):
        await calendar.async_get_events(MagicMock(), start, start + timedelta(days=94))
    assert m.history.await_count == 2


def test_schedule_calendar_shows_planned_meals_only_when_available():
    data = FeederData(
        meals=[Meal("m", "Lunch", 2, "10:00Z", (1, 2, 3, 4, 5, 6, 7), True)],
        meals_ok=True,
    )
    calendar = FeederScheduleCalendar(entity_coordinator(data))
    assert calendar.available
    events = calendar._events(NOW, NOW + timedelta(days=1))
    assert [e.start for e in events] == [datetime(2026, 11, 15, 10, tzinfo=UTC)]
    assert "not confirmation" in events[0].description
    assert not FeederScheduleCalendar(entity_coordinator(FeederData())).available


# --- review R2: a transient failure is not an expired login -----------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "part", ["list_meals", "history", "latest_status", "desiccant"]
)
async def test_transient_refresh_failure_in_a_part_is_not_reauth(part):
    m = manager()
    getattr(m, part).side_effect = CloudConnectionError("down")
    data = await bare_coordinator(m)._async_update_data()
    flags = {
        "list_meals": data.meals_ok,
        "history": data.history_ok,
        "latest_status": data.status_ok,
        "desiccant": data.desiccant is not None,
    }
    assert [name for name, ok in flags.items() if not ok] == [part]


@pytest.mark.asyncio
async def test_history_calendar_transient_failure_is_not_login_expired():
    coord = entity_coordinator(FeederData(history_ok=True))
    coord.meal_manager = AsyncMock(side_effect=CloudConnectionError("down"))
    with pytest.raises(HomeAssistantError, match="feeding history"):
        await FeederHistoryCalendar(coord).async_get_events(
            MagicMock(), NOW, NOW + timedelta(days=1)
        )


def test_feeder_entries_offer_no_options_dialog():
    from custom_components.philips_homeid.config_flow import PhilipsHomeIDConfigFlow

    feeder = MagicMock(data={CONF_IS_FEEDER: True})
    other = MagicMock(data={})
    assert PhilipsHomeIDConfigFlow.async_supports_options_flow(feeder) is False
    assert PhilipsHomeIDConfigFlow.async_supports_options_flow(other) is True


@pytest.mark.asyncio
async def test_schedule_end_trigger_fires_across_a_scan_window_boundary():
    """HA scans calendars in 15 minute windows and queues an end trigger only
    from the window holding the end, so a meal that started in the previous
    window must still be returned."""
    data = FeederData(
        meals=[Meal("m", "Lunch", 2, "08:15Z", (1, 2, 3, 4, 5, 6, 7), True)],
        meals_ok=True,
    )
    calendar = FeederScheduleCalendar(entity_coordinator(data))
    with patch.object(calendar_trigger, "get_entity", return_value=calendar):
        fetch = calendar_trigger.queued_event_fetcher(
            calendar_trigger.event_fetcher(MagicMock(), {"calendar.x"}),
            "end",
            timedelta(0),
        )
        start = datetime(2026, 11, 16, 8, 0, 20, tzinfo=UTC)
        first = calendar_trigger.Timespan(
            start, start + calendar_trigger.UPDATE_INTERVAL
        )
        second = first.next_upcoming(first.end, calendar_trigger.UPDATE_INTERVAL)
        assert await fetch(first) == []
        queued = await fetch(second)
    assert [item.trigger_time for item in queued] == [
        datetime(2026, 11, 16, 8, 16, tzinfo=UTC)
    ]


# --- feed now ----------------------------------------------------------------


def feed_entities():
    coord = entity_coordinator(None)
    coord.async_request_refresh = AsyncMock()
    feed = MagicMock(spec=FeederFeed)
    feed.portions = 1
    feed.feed = AsyncMock()
    return coord, feed


@pytest.mark.asyncio
async def test_feed_button_sends_the_set_portions_then_refreshes():
    coord, feed = feed_entities()
    feed.portions = 4
    await FeederFeedButton(coord, feed).async_press()
    feed.feed.assert_awaited_once_with(4)
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (TuyaError("x", "PERMISSION_DENIED"), "not confirmed by Tuya"),
        (CloudConnectionError("x"), "cloud unavailable"),
        (CloudAuthError("x"), "login expired"),
    ],
)
@pytest.mark.asyncio
async def test_feed_button_failures_are_reported_not_refreshed(error, message):
    coord, feed = feed_entities()
    feed.feed.side_effect = error
    with pytest.raises(HomeAssistantError, match=message):
        await FeederFeedButton(coord, feed).async_press()
    coord.async_request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_portions_number_sets_and_validates():
    coord, feed = feed_entities()
    number = FeederPortionsNumber(coord, feed)
    number.async_write_ha_state = MagicMock()
    await number.async_set_native_value(7.0)
    assert feed.portions == 7 and number.native_value == 7
    for bad in (0.0, 21.0, 2.5):
        with pytest.raises(HomeAssistantError):
            await number.async_set_native_value(bad)
    assert feed.portions == 7
    feed.feed.assert_not_called()


@pytest.mark.parametrize(("stored", "expected"), [(5.0, 5), (40.0, 1), (None, 1)])
@pytest.mark.asyncio
async def test_portions_number_restores_only_a_valid_value(stored, expected):
    coord, feed = feed_entities()
    number = FeederPortionsNumber(coord, feed)
    last = None if stored is None else MagicMock(native_value=stored)
    with (
        patch.object(
            number, "async_get_last_number_data", AsyncMock(return_value=last)
        ),
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ),
    ):
        await number.async_added_to_hass()
    assert feed.portions == expected


@pytest.mark.parametrize("supported", [True, False])
@pytest.mark.asyncio
async def test_feed_entities_exist_only_for_feed_capable_models(supported):
    coord, feed = feed_entities()
    coord.feed = feed if supported else None
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coord}}
    entry = MagicMock(entry_id="entry")
    added = []
    await async_setup_feeder_buttons(hass, entry, added.extend)
    await async_setup_feeder_numbers(hass, entry, added.extend)
    assert [type(e) for e in added] == (
        [FeederFeedButton, FeederPortionsNumber] if supported else []
    )


def test_coordinator_offers_feed_only_for_known_models():
    for model, supported in (("PAW5320", True), ("PAW3300", False), (None, False)):
        hass = MagicMock()
        entry = MagicMock(entry_id="entry")
        entry.data = {CONF_DEVICE_ID: "tuya-1", CONF_MODEL: model}
        with (
            patch("custom_components.philips_homeid.feeder.async_get_clientsession"),
            patch(
                "custom_components.philips_homeid.feeder_feed.async_get_clientsession"
            ),
            patch(
                "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
                return_value=None,
            ),
        ):
            coord = FeederCoordinator(hass, entry)
        assert (coord.feed is not None) is supported

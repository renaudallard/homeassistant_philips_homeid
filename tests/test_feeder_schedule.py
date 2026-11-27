"""Feeder meal changes: transport, request bodies, actions and meal switches."""

import asyncio
import json
import traceback
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.philips_homeid import feeder_schedule
from custom_components.philips_homeid.cloud_auth import (
    CloudAuthError,
    CloudConnectionError,
)
from custom_components.philips_homeid.const import CONF_CPP_ID, CONF_MODEL, DOMAIN
from custom_components.philips_homeid.feeder import FeederCoordinator, FeederData
from custom_components.philips_homeid.feeder_api import (
    PET_API_URL,
    FeederAuthRejected,
    FeederClient,
    FeederError,
    FeederHTTPError,
    pet_write,
)
from custom_components.philips_homeid.feeder_meals import Meal, MealManager, MealsError
from custom_components.philips_homeid.feeder_schedule import (
    ADD_MEAL_SCHEMA,
    EDIT_MEAL_SCHEMA,
    FeederMealSwitch,
    _stored_meal,
    async_setup_feeder_switches,
)

BREAKFAST = Meal("m-1", "Breakfast", 2, "07:30", (1, 3, 5), True)
DINNER = Meal("m-2", "Dinner", 3, "19:00", (6, 7), False)


def write_session(*statuses, error=None):
    """A session answering each write with the next status, or raising."""
    session = MagicMock()
    contexts = []
    for status in statuses:
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=MagicMock(status=status))
        context.__aexit__ = AsyncMock(return_value=False)
        contexts.append(context)
    session.request.side_effect = error or contexts
    return session


# --- transport -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201, 204])
async def test_write_is_confirmed_by_any_2xx(status):
    session = write_session(status)
    body = {"enabled": False}
    await pet_write(session, "access", "PATCH", "/api/homes/h/meals/m", body)
    call = session.request.call_args
    assert call.args == ("PATCH", f"{PET_API_URL}/api/homes/h/meals/m")
    assert call.kwargs["json"] == body
    assert call.kwargs["headers"] == {"Authorization": "Bearer access"}
    assert call.kwargs["allow_redirects"] is False
    session.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, FeederAuthRejected),
        (403, FeederAuthRejected),
        (409, FeederHTTPError),
        (307, FeederHTTPError),
        (500, FeederHTTPError),
    ],
)
async def test_write_failures_keep_only_the_status(status, error):
    session = write_session(status)
    with pytest.raises(error) as err:
        await pet_write(session, "secret", "DELETE", "/api/homes/h/meals/m")
    assert err.value.status == status
    assert "secret" not in str(err.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/homes/h/meals"),
        ("PUT", "/api/homes/h/meals/m"),
        ("POST", "/api/consumer"),
        ("POST", "https://evil.example/api/homes"),
        ("POST", "/api/homes/h/meals?x=1"),
    ],
)
async def test_only_meal_write_methods_on_fixed_paths_are_sent(method, path):
    session = write_session(200)
    with pytest.raises(FeederError):
        await pet_write(session, "access", method, path)
    session.request.assert_not_called()


@pytest.mark.asyncio
async def test_write_transport_errors_do_not_chain_urls_or_ids():
    home = "synthetic-home"
    path = f"/api/homes/{home}/meals"
    session = write_session(error=aiohttp.ClientConnectionError(PET_API_URL + path))
    with pytest.raises(FeederError) as err:
        await pet_write(session, "a", "POST", path, {"name": "x"})
    text = "".join(traceback.format_exception(err.value))
    assert home not in text and "versuni" not in text


@pytest.mark.asyncio
async def test_real_aiohttp_sends_the_json_body_and_never_follows_a_redirect():
    calls = []

    async def transport(request, handler):
        calls.append((request.method, str(request.url), request.body))
        response = MagicMock(status=307, method=request.method, url=request.url)
        response.headers = {"Location": "/api/homes/again"}
        response._raw_cookie_headers = ()
        response.connection = None
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
            pytest.raises(FeederHTTPError),
        ):
            await pet_write(session, "a", "POST", "/api/homes/h/meals", {"amount": 2})
    assert len(calls) == 1
    method, url, body = calls[0]
    assert (method, url) == ("POST", f"{PET_API_URL}/api/homes/h/meals")
    assert json.loads(body._value) == {"amount": 2}


def token_owner(*tokens, renew=True):
    owner = MagicMock()
    owner.access_token = AsyncMock(side_effect=list(tokens))
    owner.invalidate.return_value = renew
    return owner


@pytest.mark.asyncio
async def test_client_write_is_sent_again_only_after_a_refused_token():
    session = write_session(401, 204)
    owner = token_owner("old", "new")
    await FeederClient(session, owner).write("DELETE", "/api/homes/h/meals/m")
    sent = [call.kwargs["headers"] for call in session.request.call_args_list]
    assert sent == [{"Authorization": "Bearer old"}, {"Authorization": "Bearer new"}]
    owner.invalidate.assert_called_once_with("old")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    [
        write_session(500),
        write_session(409),
        write_session(error=TimeoutError()),
    ],
)
async def test_client_write_is_never_repeated_after_other_failures(session):
    owner = token_owner("access")
    with pytest.raises(FeederError):
        await FeederClient(session, owner).write("POST", "/api/homes/h/meals", {})
    assert session.request.call_count == 1


@pytest.mark.asyncio
async def test_client_refuses_a_read_method_as_a_write_before_any_token():
    owner = token_owner("access")
    session = write_session(200)
    with pytest.raises(FeederError):
        await FeederClient(session, owner).write("GET", "/api/homes/h/meals")
    owner.access_token.assert_not_called()
    session.request.assert_not_called()


# --- request bodies --------------------------------------------------------


def meal_manager():
    write = AsyncMock()
    manager = MealManager(AsyncMock(), "home 1", "philips-1", "Europe/Riga", write)
    return manager, write


@pytest.mark.asyncio
async def test_new_and_edited_meals_send_the_apps_body():
    manager, write = meal_manager()
    await manager.add_meal("Breakfast", "07:30", 2, [5, 1, 3])
    await manager.edit_meal("m/1", "Dinner", "19:00", 3, (7, 6))
    body = {
        "name": "Breakfast",
        "time": "07:30",
        "amount": 2,
        "deviceId": "philips-1",
        "repeatDays": [1, 3, 5],
    }
    assert write.await_args_list[0].args == ("POST", "/api/homes/home%201/meals", body)
    edited = {
        "name": "Dinner",
        "time": "19:00",
        "amount": 3,
        "deviceId": "philips-1",
        "repeatDays": [6, 7],
    }
    assert write.await_args_list[1].args == (
        "PATCH",
        "/api/homes/home%201/meals/m%2F1",
        edited,
    )


@pytest.mark.asyncio
async def test_toggle_and_delete_send_the_apps_requests():
    manager, write = meal_manager()
    await manager.set_meal_enabled("m-1", False)
    await manager.delete_meal("m-1")
    assert [call.args for call in write.await_args_list] == [
        ("PATCH", "/api/homes/home%201/meals/m-1", {"enabled": False}),
        ("DELETE", "/api/homes/home%201/meals/m-1", None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "feed_time", "portions", "days"),
    [
        ("", "07:30", 2, [1]),
        (" ", "07:30", 2, [1]),
        ("x" * 81, "07:30", 2, [1]),
        ("Meal", "07:30Z", 2, [1]),
        ("Meal", "7:30", 2, [1]),
        ("Meal", "24:00", 2, [1]),
        ("Meal", "07:30", 0, [1]),
        ("Meal", "07:30", 21, [1]),
        ("Meal", "07:30", True, [1]),
        ("Meal", "07:30", 2, []),
        ("Meal", "07:30", 2, [1, 1]),
        ("Meal", "07:30", 2, [0]),
        ("Meal", "07:30", 2, [8]),
    ],
)
async def test_invalid_meals_are_refused_before_sending(
    name, feed_time, portions, days
):
    manager, write = meal_manager()
    with pytest.raises(MealsError):
        await manager.add_meal(name, feed_time, portions, days)
    with pytest.raises(MealsError):
        await manager.edit_meal("m-1", name, feed_time, portions, days)
    write.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("meal_id", ["", "..", "x" * 201, None])
async def test_bad_meal_ids_are_refused_before_sending(meal_id):
    manager, write = meal_manager()
    with pytest.raises(FeederError):
        await manager.delete_meal(meal_id)
    with pytest.raises(FeederError):
        await manager.set_meal_enabled(meal_id, True)
    write.assert_not_called()


@pytest.mark.asyncio
async def test_a_manager_without_a_writer_changes_nothing():
    manager = MealManager(AsyncMock(), "home", "philips-1", None)
    with pytest.raises(MealsError):
        await manager.delete_meal("m-1")


# --- coordinator and actions ----------------------------------------------


def coordinator(meals=(BREAKFAST, DINNER), meals_ok=True):
    coord = FeederCoordinator.__new__(FeederCoordinator)
    coord.hass = MagicMock()
    coord.config_entry = MagicMock(
        entry_id="entry", data={CONF_CPP_ID: "cpp", CONF_MODEL: "PAW5320"}
    )
    coord._logged = {}
    coord._meal_reads = 0
    coord._read_back = ([], False)
    coord.changes = asyncio.Lock()
    coord.has_camera = False
    coord.last_update_success = True
    coord.data = FeederData(meals=list(meals), meals_ok=meals_ok)
    coord.manager = MagicMock()
    coord.manager.list_meals = AsyncMock(return_value=[BREAKFAST])
    for name in ("add_meal", "edit_meal", "set_meal_enabled", "delete_meal"):
        setattr(coord.manager, name, AsyncMock())
    coord.meal_manager = AsyncMock(return_value=coord.manager)
    coord.async_set_updated_data = MagicMock()
    return coord


@pytest.mark.asyncio
async def test_meals_are_read_again_after_a_change():
    coord = coordinator()
    await coord.refresh_meals()
    data = coord.async_set_updated_data.call_args.args[0]
    assert data.meals == [BREAKFAST] and data.meals_ok


@pytest.mark.asyncio
async def test_a_failed_read_after_a_change_marks_the_schedule_unavailable():
    coord = coordinator()
    coord.manager.list_meals.side_effect = CloudConnectionError("down")
    await coord.refresh_meals()
    data = coord.async_set_updated_data.call_args.args[0]
    assert data.meals == [BREAKFAST, DINNER] and not data.meals_ok
    assert coord._meal_reads == 1  # a poll running now keeps this


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_nothing_is_stored_after_a_failed_poll(fails):
    coord = coordinator()
    coord.last_update_success = False
    if fails:
        coord.manager.list_meals.side_effect = FeederHTTPError(503)
    await coord.refresh_meals()
    coord.async_set_updated_data.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "meals", "meals_ok"),
    [
        ("read", [DINNER], True),
        ("failed", [BREAKFAST, DINNER], False),
        (None, [BREAKFAST], True),
    ],
)
async def test_a_poll_never_puts_back_meals_older_than_a_change(
    change, meals, meals_ok
):
    coord = coordinator()
    coord.hass.config.time_zone = "Europe/Riga"
    coord.async_set_updated_data = MagicMock(
        side_effect=lambda data: setattr(coord, "data", data)
    )
    manager = coord.manager
    manager.time_zone = "Europe/Riga"
    manager.history = AsyncMock(return_value=[])
    manager.status_events = AsyncMock(return_value=[])
    manager.desiccant = AsyncMock(return_value=None)

    async def stale_history(start, end):
        # The schedule changes while this poll is still reading.
        if change == "read":
            manager.list_meals.return_value = [DINNER]
            await coord.refresh_meals()
        elif change == "failed":
            # The read back fails: the meals read above predate the change.
            manager.list_meals.side_effect = FeederHTTPError(503)
            await coord.refresh_meals()
        return []

    manager.list_meals.return_value = [BREAKFAST]
    manager.history.side_effect = stale_history
    data = await coord._async_update_data()
    assert (data.meals, data.meals_ok) == (meals, meals_ok)


@pytest.mark.asyncio
@pytest.mark.parametrize("read", [True, False])
async def test_the_poll_after_a_failed_one_takes_a_change_read_meanwhile(read):
    snack = Meal("m-3", "Snack", 1, "12:00", (1,), True)
    coord = coordinator(meals=[BREAKFAST])
    coord.hass.config.time_zone = "Europe/Riga"
    coord.async_set_updated_data = MagicMock(
        side_effect=lambda data: setattr(coord, "data", data)
    )
    coord.last_update_success = False
    manager = coord.manager
    manager.time_zone = "Europe/Riga"
    manager.status_events = AsyncMock(return_value=[])
    manager.desiccant = AsyncMock(return_value=None)

    async def add_meal(name, feed_time, portions, days):
        manager.list_meals.return_value = [BREAKFAST, snack]
        if not read:
            manager.list_meals.side_effect = FeederHTTPError(503)

    async def history(start, end):
        # The meal is added after this poll read the meals.
        await feeder_schedule._change(
            coord, lambda m: m.add_meal("Snack", "12:00", 1, [1])
        )
        return []

    manager.add_meal.side_effect = add_meal
    manager.history = AsyncMock(side_effect=history)
    data = await coord._async_update_data()
    # Not stored during the failure, but kept for this poll.
    coord.async_set_updated_data.assert_not_called()
    if read:
        assert (data.meals, data.meals_ok) == ([BREAKFAST, snack], True)
    else:
        assert not data.meals_ok


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["manager", "meals"])
async def test_an_expired_login_after_a_change_leaves_the_meals_unknown(where):
    coord = coordinator()
    coord.async_set_updated_data.side_effect = lambda data: setattr(coord, "data", data)
    if where == "manager":
        coord.meal_manager.side_effect = CloudAuthError("expired")
    else:
        coord.manager.list_meals.side_effect = CloudAuthError("expired")
    await coord.refresh_meals()  # never raises: the poll starts the reauth
    assert coord.data.meals == [BREAKFAST, DINNER] and not coord.data.meals_ok
    assert coord._meal_reads == 1  # a poll running now keeps this
    with pytest.raises(ServiceValidationError, match="could not be read"):
        _stored_meal(coord, DINNER.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kind", "message", "refreshed"),
    [
        (FeederHTTPError(409), HomeAssistantError, "already planned", True),
        (FeederHTTPError(500), HomeAssistantError, "did not confirm", True),
        (FeederError("x"), HomeAssistantError, "did not confirm", True),
        (CloudConnectionError("x"), HomeAssistantError, "did not confirm", True),
        (CloudAuthError("x"), HomeAssistantError, "login expired", False),
        (MealsError("Meal time must be HH:MM"), ServiceValidationError, "HH:MM", False),
    ],
)
async def test_change_failures(error, kind, message, refreshed):
    coord = coordinator()
    coord.refresh_meals = AsyncMock()
    coord.manager.delete_meal.side_effect = error
    with pytest.raises(kind, match=message):
        await feeder_schedule._change(coord, lambda manager: manager.delete_meal("m-1"))
    assert coord.refresh_meals.await_count == int(refreshed)


@pytest.mark.asyncio
async def test_a_confirmed_change_reads_the_meals_again():
    coord = coordinator()
    coord.refresh_meals = AsyncMock()
    await feeder_schedule._change(coord, lambda manager: manager.delete_meal("m-1"))
    coord.refresh_meals.assert_awaited_once()


def registry_entry(entity_id, unique_id, entry_id="entry", platform=DOMAIN):
    return SimpleNamespace(
        entity_id=entity_id,
        unique_id=unique_id,
        config_entry_id=entry_id,
        platform=platform,
        domain=entity_id.split(".")[0],
    )


def action_hass(coord, entries=()):
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coord, "other": object()}}
    registry = MagicMock()
    registry.async_get.side_effect = {e.entity_id: e for e in entries}.get
    return hass, registry


def call(hass, **data):
    return SimpleNamespace(hass=hass, data=data)


@pytest.mark.asyncio
async def test_add_meal_action_sends_whole_minutes_and_iso_days():
    coord = coordinator()
    coord.refresh_meals = AsyncMock()
    hass, _ = action_hass(coord)
    device = SimpleNamespace(identifiers={(DOMAIN, "cpp")})
    devices = MagicMock()
    devices.async_get.return_value = device
    data = ADD_MEAL_SCHEMA(
        {
            "device": "dev",
            "name": "Breakfast",
            "time": "07:30:00",
            "portions": "2",
            "days": ["fri", "mon", "fri"],
        }
    )
    with patch.object(feeder_schedule.dr, "async_get", return_value=devices):
        await feeder_schedule._async_add_meal(call(hass, **data))
    coord.manager.add_meal.assert_awaited_once_with("Breakfast", "07:30", 2, [1, 5])


@pytest.mark.asyncio
async def test_add_meal_refuses_seconds_and_unknown_devices():
    coord = coordinator()
    hass, _ = action_hass(coord)
    devices = MagicMock()
    devices.async_get.return_value = SimpleNamespace(identifiers={(DOMAIN, "cpp")})
    data = {"device": "d", "name": "M", "time": time(7, 30, 5), "portions": 1}
    with (
        patch.object(feeder_schedule.dr, "async_get", return_value=devices),
        pytest.raises(ServiceValidationError),
    ):
        await feeder_schedule._async_add_meal(call(hass, **data, days=["mon"]))
    devices.async_get.return_value = SimpleNamespace(identifiers={(DOMAIN, "x")})
    data["time"] = time(7, 30)
    with (
        patch.object(feeder_schedule.dr, "async_get", return_value=devices),
        pytest.raises(ServiceValidationError),
    ):
        await feeder_schedule._async_add_meal(call(hass, **data, days=["mon"]))
    coord.manager.add_meal.assert_not_called()


@pytest.mark.asyncio
async def test_edit_meal_keeps_what_was_left_out():
    coord = coordinator()
    coord.refresh_meals = AsyncMock()
    switch = registry_entry("switch.cat_breakfast", "cpp_meal_m-1")
    hass, registry = action_hass(coord, [switch])
    data = EDIT_MEAL_SCHEMA({"meal": "switch.cat_breakfast", "portions": 4})
    with patch.object(feeder_schedule.er, "async_get", return_value=registry):
        await feeder_schedule._async_edit_meal(call(hass, **data))
    coord.manager.edit_meal.assert_awaited_once_with(
        "m-1", "Breakfast", "07:30", 4, [1, 3, 5]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "given", "message"),
    [
        (Meal("m-1", "Once", 2, "07:30", (), True), {"portions": 3}, "no day"),
        (Meal("m-1", "Utc", 2, "07:30Z", (1,), True), {"portions": 3}, "UTC"),
    ],
)
async def test_edit_asks_for_what_the_app_would_never_send_back(stored, given, message):
    coord = coordinator(meals=[stored])
    switch = registry_entry("switch.cat_meal", "cpp_meal_m-1")
    hass, registry = action_hass(coord, [switch])
    data = EDIT_MEAL_SCHEMA({"meal": "switch.cat_meal", **given})
    with (
        patch.object(feeder_schedule.er, "async_get", return_value=registry),
        pytest.raises(ServiceValidationError, match=message),
    ):
        await feeder_schedule._async_edit_meal(call(hass, **data))
    coord.manager.edit_meal.assert_not_called()


@pytest.mark.asyncio
async def test_edit_of_such_a_meal_goes_out_once_both_are_given():
    coord = coordinator(meals=[Meal("m-1", "Utc", 2, "07:30Z", (), True)])
    coord.refresh_meals = AsyncMock()
    switch = registry_entry("switch.cat_meal", "cpp_meal_m-1")
    hass, registry = action_hass(coord, [switch])
    data = EDIT_MEAL_SCHEMA(
        {"meal": "switch.cat_meal", "time": "08:00", "days": ["sun"]}
    )
    with patch.object(feeder_schedule.er, "async_get", return_value=registry):
        await feeder_schedule._async_edit_meal(call(hass, **data))
    coord.manager.edit_meal.assert_awaited_once_with("m-1", "Utc", "08:00", 2, [7])


@pytest.mark.asyncio
async def test_concurrent_edits_each_start_from_the_last_read_back():
    coord = coordinator(meals=[BREAKFAST])
    coord.async_set_updated_data = MagicMock(
        side_effect=lambda data: setattr(coord, "data", data)
    )
    cloud = {"meal": BREAKFAST}

    async def edit_meal(meal_id, name, feed_time, portions, days):
        await asyncio.sleep(0)
        enabled = cloud["meal"].enabled
        cloud["meal"] = Meal(meal_id, name, portions, feed_time, tuple(days), enabled)

    async def list_meals():
        await asyncio.sleep(0)
        return [cloud["meal"]]

    coord.manager.edit_meal.side_effect = edit_meal
    coord.manager.list_meals.side_effect = list_meals
    switch = registry_entry("switch.cat_breakfast", "cpp_meal_m-1")
    hass, registry = action_hass(coord, [switch])
    portions = EDIT_MEAL_SCHEMA({"meal": "switch.cat_breakfast", "portions": 4})
    moved = EDIT_MEAL_SCHEMA({"meal": "switch.cat_breakfast", "time": "08:15"})
    with patch.object(feeder_schedule.er, "async_get", return_value=registry):
        await asyncio.gather(
            feeder_schedule._async_edit_meal(call(hass, **portions)),
            feeder_schedule._async_edit_meal(call(hass, **moved)),
        )
    assert cloud["meal"] == Meal("m-1", "Breakfast", 4, "08:15", (1, 3, 5), True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", ["write", "read"])
async def test_an_edit_behind_a_cancelled_one_never_undoes_it(cancelled):
    coord = coordinator(meals=[BREAKFAST])
    coord.async_set_updated_data = MagicMock(
        side_effect=lambda data: setattr(coord, "data", data)
    )
    cloud = {"meal": BREAKFAST}
    held = asyncio.Event()
    hang = {cancelled}

    async def edit_meal(meal_id, name, feed_time, portions, days):
        enabled = cloud["meal"].enabled
        cloud["meal"] = Meal(meal_id, name, portions, feed_time, tuple(days), enabled)
        if "write" in hang:
            # Applied by HomeID, but the answer never comes.
            hang.clear()
            await held.wait()

    async def list_meals():
        if "read" in hang:
            hang.clear()
            await held.wait()
        return [cloud["meal"]]

    coord.manager.edit_meal.side_effect = edit_meal
    coord.manager.list_meals.side_effect = list_meals
    switch = registry_entry("switch.cat_breakfast", "cpp_meal_m-1")
    hass, registry = action_hass(coord, [switch])
    moved = EDIT_MEAL_SCHEMA({"meal": switch.entity_id, "time": "08:15"})
    portions = EDIT_MEAL_SCHEMA({"meal": switch.entity_id, "portions": 6})
    with patch.object(feeder_schedule.er, "async_get", return_value=registry):
        first = asyncio.ensure_future(
            feeder_schedule._async_edit_meal(call(hass, **moved))
        )
        for _ in range(3):
            await asyncio.sleep(0)
        second = asyncio.ensure_future(
            feeder_schedule._async_edit_meal(call(hass, **portions))
        )
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(ServiceValidationError, match="after the next poll"):
            await second
    assert cloud["meal"].time == "08:15"
    assert coord.manager.edit_meal.await_count == 1
    assert not coord.data.meals_ok
    assert coord._meal_reads == 1  # a poll running now keeps this
    assert not coord.changes.locked()


@pytest.mark.asyncio
async def test_a_change_waits_for_the_read_back_of_the_one_before():
    coord = coordinator()
    coord.async_set_updated_data = MagicMock(
        side_effect=lambda data: setattr(coord, "data", data)
    )
    steps = []

    async def delete_meal(meal_id):
        steps.append(meal_id)
        await asyncio.sleep(0)

    async def list_meals():
        await asyncio.sleep(0)
        steps.append("read")
        return []

    coord.manager.delete_meal.side_effect = delete_meal
    coord.manager.list_meals.side_effect = list_meals
    await asyncio.gather(
        feeder_schedule._change(coord, lambda manager: manager.delete_meal("m-1")),
        feeder_schedule._change(coord, lambda manager: manager.delete_meal("m-2")),
    )
    assert steps == ["m-1", "read", "m-2", "read"]


@pytest.mark.asyncio
async def test_an_edit_of_a_meal_deleted_meanwhile_sends_nothing():
    coord = coordinator()
    switch = registry_entry("switch.cat_breakfast", "cpp_meal_m-1")
    hass, registry = action_hass(coord, [switch])
    data = EDIT_MEAL_SCHEMA({"meal": "switch.cat_breakfast", "portions": 4})
    with patch.object(feeder_schedule.er, "async_get", return_value=registry):
        async with coord.changes:
            edit = asyncio.ensure_future(
                feeder_schedule._async_edit_meal(call(hass, **data))
            )
            await asyncio.sleep(0)
            coord.data = FeederData(meals=[DINNER], meals_ok=True)
        with pytest.raises(ServiceValidationError, match="Choose a meal switch"):
            await edit
    coord.manager.edit_meal.assert_not_called()


@pytest.mark.parametrize(
    ("given", "portions"), [(2, 2), (2.0, 2), ("3", 3), ("4.0", 4), (20, 20)]
)
def test_whole_portions_are_taken(given, portions):
    data = EDIT_MEAL_SCHEMA({"meal": "switch.x", "portions": given})
    assert data["portions"] == portions and type(data["portions"]) is int


@pytest.mark.asyncio
async def test_delete_meal_removes_the_switchs_meal():
    coord = coordinator()
    coord.refresh_meals = AsyncMock()
    switch = registry_entry("switch.cat_dinner", "cpp_meal_m-2")
    hass, registry = action_hass(coord, [switch])
    with patch.object(feeder_schedule.er, "async_get", return_value=registry):
        await feeder_schedule._async_delete_meal(call(hass, meal="switch.cat_dinner"))
    coord.manager.delete_meal.assert_awaited_once_with("m-2")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [
        None,
        registry_entry("switch.lock", "cpp_child_lock"),
        registry_entry("switch.cat_gone", "cpp_meal_m-9"),
        registry_entry("switch.cat_breakfast", "cpp_meal_m-1", entry_id="other"),
        registry_entry("switch.cat_breakfast", "cpp_meal_m-1", platform="other"),
        registry_entry("sensor.cat_breakfast", "cpp_meal_m-1"),
    ],
)
async def test_meal_actions_refuse_anything_but_a_known_meal_switch(entry):
    coord = coordinator()
    hass, registry = action_hass(coord, [entry] if entry else [])
    target = entry.entity_id if entry else "switch.none"
    with (
        patch.object(feeder_schedule.er, "async_get", return_value=registry),
        pytest.raises(ServiceValidationError),
    ):
        await feeder_schedule._async_delete_meal(call(hass, meal=target))
    coord.manager.delete_meal.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(("meals_ok", "success"), [(False, True), (True, False)])
async def test_meal_actions_refuse_while_the_schedule_is_unavailable(meals_ok, success):
    coord = coordinator(meals_ok=meals_ok)
    coord.last_update_success = success
    switch = registry_entry("switch.cat_breakfast", "cpp_meal_m-1")
    hass, registry = action_hass(coord, [switch])
    with (
        patch.object(feeder_schedule.er, "async_get", return_value=registry),
        pytest.raises(ServiceValidationError, match="after the next poll"),
    ):
        await feeder_schedule._async_delete_meal(call(hass, meal=switch.entity_id))
    edit = EDIT_MEAL_SCHEMA({"meal": switch.entity_id, "portions": 4})
    with (
        patch.object(feeder_schedule.er, "async_get", return_value=registry),
        pytest.raises(ServiceValidationError, match="after the next poll"),
    ):
        await feeder_schedule._async_edit_meal(call(hass, **edit))
    coord.manager.delete_meal.assert_not_called()
    coord.manager.edit_meal.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["edit", "delete"])
async def test_a_change_waiting_behind_one_whose_read_back_was_not_stored_stops(
    action,
):
    coord = coordinator(meals=[BREAKFAST])
    switch = registry_entry("switch.cat_breakfast", "cpp_meal_m-1")
    hass, registry = action_hass(coord, [switch])
    if action == "edit":
        data = EDIT_MEAL_SCHEMA({"meal": "switch.cat_breakfast", "portions": 4})
        handler, sent = feeder_schedule._async_edit_meal, coord.manager.edit_meal
    else:
        data = {"meal": "switch.cat_breakfast"}
        handler, sent = feeder_schedule._async_delete_meal, coord.manager.delete_meal
    with patch.object(feeder_schedule.er, "async_get", return_value=registry):
        async with coord.changes:
            change = asyncio.ensure_future(handler(call(hass, **data)))
            await asyncio.sleep(0)
            # A poll failed meanwhile, so the change before was not read back.
            coord.last_update_success = False
        with pytest.raises(ServiceValidationError, match="after the next poll"):
            await change
    sent.assert_not_called()


@pytest.mark.parametrize(
    "data",
    [
        {"portions": 0},
        {"portions": 21},
        {"portions": 2.7},
        {"portions": "2.5"},
        {"portions": "two"},
        {"portions": True},
        {"portions": None},
        {"days": []},
        {"days": ["monday"]},
        {"time": "25:00"},
    ],
)
def test_action_schemas_refuse_bad_input(data):
    base = {
        "device": "d",
        "name": "M",
        "time": "07:30",
        "portions": 1,
        "days": ["mon"],
    }
    with pytest.raises(feeder_schedule.vol.Invalid):
        ADD_MEAL_SCHEMA({**base, **data})
    with pytest.raises(feeder_schedule.vol.Invalid):
        EDIT_MEAL_SCHEMA({"meal": "switch.x", **data})


def test_meal_actions_are_admin_only():
    hass = MagicMock()
    with patch.object(feeder_schedule, "async_register_admin_service") as register:
        feeder_schedule.async_setup_schedule_services(hass)
    assert [c.args[2] for c in register.call_args_list] == [
        "add_feeder_meal",
        "edit_feeder_meal",
        "delete_feeder_meal",
    ]
    assert all(c.args[:2] == (hass, DOMAIN) for c in register.call_args_list)


def test_services_yaml_and_strings_name_the_same_fields():
    from pathlib import Path

    from homeassistant.util.yaml import load_yaml

    base = Path(feeder_schedule.__file__).parent
    services = load_yaml(base / "services.yaml")
    files = [base / "strings.json", *sorted((base / "translations").glob("*.json"))]
    for path in files:
        strings = json.loads(path.read_text(encoding="utf-8"))["services"]
        assert {name: sorted(spec["fields"]) for name, spec in services.items()} == {
            name: sorted(spec["fields"]) for name, spec in strings.items()
        }, path
        days = json.loads(path.read_text(encoding="utf-8"))["selector"]["weekday"]
        assert list(days["options"]) == [
            "mon",
            "tue",
            "wed",
            "thu",
            "fri",
            "sat",
            "sun",
        ]


# --- meal switches ---------------------------------------------------------


def test_meal_switch_follows_its_meal():
    coord = coordinator()
    switch = FeederMealSwitch(coord, "m-2")
    assert switch.unique_id == "cpp_meal_m-2"
    assert switch.name == "Dinner"
    assert switch.is_on is False
    assert switch.extra_state_attributes == {
        "time": "19:00",
        "portions": 3,
        "days": ["sat", "sun"],
    }
    coord.data = FeederData(meals=[BREAKFAST], meals_ok=True)
    assert switch.is_on is None and not switch.available


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "enabled"), [("on", True), ("off", False)])
async def test_meal_switch_enables_and_disables_its_meal(method, enabled):
    coord = coordinator()
    coord.refresh_meals = AsyncMock()
    switch = FeederMealSwitch(coord, "m-1")
    await getattr(switch, f"async_turn_{method}")()
    coord.manager.set_meal_enabled.assert_awaited_once_with("m-1", enabled)
    coord.refresh_meals.assert_awaited_once()


@pytest.mark.asyncio
async def test_meal_switches_are_added_and_removed_with_the_meals():
    coord = coordinator()
    listeners = []
    coord.async_add_listener = MagicMock(side_effect=lambda f: listeners.append(f))
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coord}}
    entry = coord.config_entry
    registry = MagicMock()
    stored = [
        registry_entry("switch.cat_breakfast", "cpp_meal_m-1"),
        registry_entry("switch.cat_old", "cpp_meal_m-0"),
        registry_entry("switch.other", "cpp_child_lock"),
        registry_entry("sensor.cat_meal", "cpp_meal_m-7"),
    ]
    added = []
    add = MagicMock(side_effect=lambda items: added.append([e._meal_id for e in items]))
    registry.async_remove.side_effect = lambda entity_id: stored.remove(
        next(e for e in stored if e.entity_id == entity_id)
    )
    with (
        patch.object(feeder_schedule.er, "async_get", return_value=registry),
        patch.object(
            feeder_schedule.er,
            "async_entries_for_config_entry",
            side_effect=lambda reg, entry_id: list(stored),
        ),
    ):
        await async_setup_feeder_switches(hass, entry, add)
        assert added == [["m-1", "m-2"]]
        removed = [c.args[0] for c in registry.async_remove.call_args_list]
        assert removed == ["switch.cat_old"]

        coord.data = FeederData(meals=[], meals_ok=False)
        listeners[0]()
        assert registry.async_remove.call_count == 1  # nothing read, nothing gone

        coord.data = FeederData(meals=[DINNER], meals_ok=True)
        listeners[0]()
        removed = [c.args[0] for c in registry.async_remove.call_args_list]
        assert removed == ["switch.cat_old", "switch.cat_breakfast"]
        assert added == [["m-1", "m-2"]]

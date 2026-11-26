"""PAW5320 event entities and the status events they read."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.philips_homeid import event as event_platform
from custom_components.philips_homeid.const import (
    CONF_CPP_ID,
    CONF_DEVICE_ID,
    CONF_IS_FEEDER,
    CONF_MODEL,
    DOMAIN,
)
from custom_components.philips_homeid.event import _EVENTS, FeederEvent
from custom_components.philips_homeid.feeder import LOOKBACK, FeederData
from custom_components.philips_homeid.feeder_api import FeederDevice
from custom_components.philips_homeid.feeder_meals import (
    FeedingEvent,
    MealManager,
    MealsError,
    StatusEvent,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
START = NOW - timedelta(days=7)
COMPONENT = Path(__file__).parent.parent / "custom_components" / "philips_homeid"


def row(event_id, kind, time, **changes):
    return {
        "id": event_id,
        "type": kind,
        "time": time.isoformat(),
        "deviceId": "internal",
        **changes,
    }


def manager(rows):
    request = AsyncMock(return_value={"item": rows})
    device = FeederDevice("tuya-vendor", "internal", "home", "PAW5320", "Cat", None)
    return MealManager.for_device(request, device)


# --- status events -----------------------------------------------------------


@pytest.mark.asyncio
async def test_status_events_keep_ids_oldest_first():
    rows = [
        row("b", "food_outlet_stuck", NOW - timedelta(hours=1)),
        row("a", "food_level_low", NOW - timedelta(hours=2)),
        row("c", "meal_dispensed", NOW - timedelta(hours=3)),
        row("d", "food_level_low", NOW - timedelta(hours=4), deviceId="other"),
    ]
    events = await manager(rows).status_events(START, NOW)
    assert events == [
        StatusEvent("a", "food_level_low", NOW - timedelta(hours=2)),
        StatusEvent("b", "food_outlet_stuck", NOW - timedelta(hours=1)),
    ]


@pytest.mark.asyncio
async def test_a_status_event_read_twice_is_kept_once():
    rows = [row("a", "food_level_low", NOW - timedelta(hours=2))] * 2
    assert len(await manager(rows).status_events(START, NOW)) == 1


@pytest.mark.asyncio
async def test_one_id_for_two_status_events_is_an_error():
    rows = [
        row("a", "food_level_low", NOW - timedelta(hours=2)),
        row("a", "food_outlet_stuck", NOW - timedelta(hours=2)),
    ]
    with pytest.raises(MealsError, match="Conflicting event identity"):
        await manager(rows).status_events(START, NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("event_id", [None, "", 5, ".."])
async def test_a_status_event_without_a_usable_id_still_counts(event_id):
    rows = [
        row(event_id, "food_level_low", NOW - timedelta(hours=1)),
        row(event_id, "food_level_low", NOW - timedelta(hours=1)),
        row("a", "food_outlet_stuck", NOW - timedelta(hours=2)),
    ]
    # One UTC day, so one request.
    events = await manager(rows).status_events(NOW - timedelta(hours=3), NOW)
    assert events == [
        StatusEvent("a", "food_outlet_stuck", NOW - timedelta(hours=2)),
        StatusEvent(None, "food_level_low", NOW - timedelta(hours=1)),
        StatusEvent(None, "food_level_low", NOW - timedelta(hours=1)),
    ]
    assert FeederData(status_events=events, status_ok=True).status == {
        "food_level_low": NOW - timedelta(hours=1),
        "food_outlet_stuck": NOW - timedelta(hours=2),
    }


def test_status_is_the_latest_time_of_each_type():
    data = FeederData(
        status_events=[
            StatusEvent("b", "food_level_low", NOW - timedelta(hours=1)),
            StatusEvent("a", "food_level_low", NOW - timedelta(hours=2)),
            StatusEvent("c", "device_online", NOW - timedelta(hours=3)),
        ]
    )
    assert data.status == {
        "food_level_low": NOW - timedelta(hours=1),
        "device_online": NOW - timedelta(hours=3),
    }


# --- event entities ----------------------------------------------------------


def coordinator(data):
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
    return next(item for item in _EVENTS if item.key == key)


def feeding(event_id, time, amount=1):
    return FeedingEvent(event_id, time, "Lunch", amount, "device")


class Fired:
    """The states HA would record: a write that changes nothing is dropped."""

    def __init__(self, entity):
        self.states = []

        def write():
            if entity.available:
                state = (entity.state, dict(entity.state_attributes))
            else:
                state = ("unavailable", {})
            if not self.states or self.states[-1] != state:
                self.states.append(state)

        entity.async_write_ha_state = write

    def changes(self):
        """(previous state, event attributes) of each write a state trigger
        with not_from unavailable, as in the README, would see."""
        previous = [None, *(state for state, _ in self.states)]
        return [
            (before, attrs)
            for before, (state, attrs) in zip(previous, self.states, strict=False)
            if attrs.get("event_type") and state != before != "unavailable"
        ]

    def events(self):
        return [attrs for _, attrs in self.changes()]

    def types(self):
        return [attrs["event_type"] for attrs in self.events()]


@pytest.fixture(autouse=True)
def later():
    """Calls scheduled with async_call_later, run by drain()."""
    calls = []

    def schedule(hass, delay, action):
        call = MagicMock(delay=delay, action=action)
        calls.append(call)
        return call.cancel

    with patch("custom_components.philips_homeid.event.async_call_later", schedule):
        yield calls


def drain(calls):
    while calls:
        call = calls.pop(0)
        if not call.cancel.called:
            call.action(NOW)


async def added(key, data):
    coord = coordinator(data)
    entity = FeederEvent(coord, spec(key))
    fired = Fired(entity)
    with patch(
        "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
        AsyncMock(),
    ):
        await entity.async_added_to_hass()
    return coord, entity, fired


def update(coord, entity, data):
    coord.data = data
    with patch(
        "custom_components.philips_homeid.event.dt_util.utcnow", return_value=NOW
    ):
        entity._handle_coordinator_update()


@pytest.mark.asyncio
async def test_events_present_at_the_first_read_do_not_fire():
    data = FeederData(history=[feeding("a", NOW)], history_ok=True)
    coord, entity, fired = await added("feeder_feeding", data)
    update(coord, entity, data)
    assert entity.state is None
    assert fired.types() == []


@pytest.mark.asyncio
async def test_a_new_feeding_fires_once_with_its_details():
    old = feeding("a", NOW - timedelta(hours=1))
    data = FeederData(history=[old], history_ok=True)
    coord, entity, fired = await added("feeder_feeding", data)
    new = feeding("b", NOW, amount=2)
    update(coord, entity, replace(data, history=[old, new]))
    update(coord, entity, replace(data, history=[old, new]))
    assert fired.events() == [
        {
            "event_type": "meal_dispensed",
            "time": NOW.isoformat(),
            "name": "Lunch",
            "amount": 2,
            "source": "device",
        }
    ]


@pytest.mark.asyncio
async def test_several_new_events_fire_oldest_first_and_apart(later):
    data = FeederData(status_ok=True)
    coord, entity, fired = await added("feeder_food_alert", data)
    events = [
        StatusEvent("late", "food_outlet_stuck", NOW),
        StatusEvent("early", "food_level_low", NOW - timedelta(minutes=5)),
        StatusEvent("other", "device_offline", NOW - timedelta(minutes=1)),
    ]
    update(coord, entity, replace(data, status_events=events))
    assert fired.types() == ["food_level_low"]
    assert [call.delay for call in later] == [0.01]
    drain(later)
    assert fired.types() == ["food_level_low", "food_outlet_stuck"]
    assert [attrs["time"] for attrs in fired.events()] == [
        (NOW - timedelta(minutes=5)).isoformat(),
        NOW.isoformat(),
    ]


@pytest.mark.asyncio
async def test_events_missed_while_the_read_failed_fire_after_it():
    data = FeederData(history=[feeding("a", NOW - timedelta(hours=2))], history_ok=True)
    coord, entity, fired = await added("feeder_feeding", data)
    update(coord, entity, FeederData())
    assert not entity.available
    recent = [*data.history, feeding("b", NOW - timedelta(hours=1))]
    update(coord, entity, replace(data, history=recent))
    assert entity.available
    assert [attrs["time"] for attrs in fired.events()] == [
        (NOW - timedelta(hours=1)).isoformat()
    ]
    # A trigger that leaves out the change from unavailable still sees it.
    assert [before for before, _ in fired.changes()] == [None]


@pytest.mark.asyncio
async def test_events_after_a_failed_poll_change_the_state_from_a_time(later):
    old = feeding("a", NOW - timedelta(hours=2))
    data = FeederData(history=[old], history_ok=True)
    coord, entity, fired = await added("feeder_feeding", data)
    update(coord, entity, replace(data, history=[old, feeding("b", NOW)]))
    coord.last_update_success = False
    update(coord, entity, coord.data)
    coord.last_update_success = True
    update(coord, entity, replace(data, history=[old, feeding("c", NOW)]))
    drain(later)
    assert [state for state, _ in fired.states].count("unavailable") == 1
    _, (before, attrs) = fired.changes()
    assert attrs["time"] == NOW.isoformat()
    assert before not in ("unavailable", None)


@pytest.mark.asyncio
async def test_events_still_to_fire_wait_out_a_failed_poll(later):
    old = feeding("a", NOW - timedelta(hours=2))
    data = FeederData(history=[old], history_ok=True)
    coord, entity, fired = await added("feeder_feeding", data)
    new = [feeding(i, NOW - timedelta(minutes=m)) for i, m in (("b", 3), ("c", 2))]
    batch = replace(data, history=[old, *new, feeding("d", NOW)])
    update(coord, entity, batch)
    coord.last_update_success = False
    update(coord, entity, coord.data)
    drain(later)
    assert len(fired.events()) == 1
    coord.last_update_success = True
    update(coord, entity, batch)
    drain(later)
    assert [attrs["time"] for attrs in fired.events()] == [
        (NOW - timedelta(minutes=3)).isoformat(),
        (NOW - timedelta(minutes=2)).isoformat(),
        NOW.isoformat(),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("after", "wait"), [(0.004, 0.006), (0.02, None)])
async def test_a_read_fires_its_first_event_apart_from_the_last_one(later, after, wait):
    clock = [100.0]
    old = feeding("a", NOW - timedelta(hours=2))
    data = FeederData(history=[old], history_ok=True)
    with patch.object(
        event_platform, "time", SimpleNamespace(monotonic=lambda: clock[0])
    ):
        coord, entity, fired = await added("feeder_feeding", data)
        update(coord, entity, replace(data, history=[old, feeding("b", NOW)]))
        clock[0] += after
        update(
            coord,
            entity,
            replace(data, history=[old, feeding("b", NOW), feeding("c", NOW)]),
        )
        if wait is None:
            assert len(fired.events()) == 2 and not later
        else:
            assert len(fired.events()) == 1
            assert [call.delay for call in later] == [pytest.approx(wait)]
            drain(later)
            assert len(fired.events()) == 2


@pytest.mark.asyncio
async def test_removal_cancels_the_events_still_to_fire(later):
    data = FeederData(status_ok=True)
    coord, entity, fired = await added("feeder_food_alert", data)
    events = [
        StatusEvent("a", "food_level_low", NOW - timedelta(minutes=1)),
        StatusEvent("b", "food_outlet_stuck", NOW),
    ]
    update(coord, entity, replace(data, status_events=events))
    await entity.async_will_remove_from_hass()
    assert later[0].cancel.called
    drain(later)
    assert fired.types() == ["food_level_low"]


@pytest.mark.asyncio
async def test_events_of_a_later_read_wait_for_those_still_to_fire(later):
    data = FeederData(status_ok=True)
    coord, entity, fired = await added("feeder_food_alert", data)
    first = [
        StatusEvent("a", "food_level_low", NOW - timedelta(minutes=2)),
        StatusEvent("b", "food_outlet_stuck", NOW - timedelta(minutes=1)),
    ]
    update(coord, entity, replace(data, status_events=first))
    update(
        coord,
        entity,
        replace(data, status_events=[*first, StatusEvent("c", "food_level_low", NOW)]),
    )
    assert len(later) == 1
    drain(later)
    assert fired.types() == ["food_level_low", "food_outlet_stuck", "food_level_low"]


@pytest.mark.asyncio
async def test_first_successful_read_after_setup_only_primes():
    coord, entity, fired = await added("feeder_feeding", FeederData())
    data = FeederData(history=[feeding("a", NOW)], history_ok=True)
    update(coord, entity, data)
    update(coord, entity, data)
    assert fired.types() == []


@pytest.mark.asyncio
async def test_an_event_missing_from_one_read_does_not_fire_again():
    event = feeding("a", NOW - timedelta(hours=1))
    data = FeederData(history=[event], history_ok=True)
    coord, entity, fired = await added("feeder_feeding", data)
    update(coord, entity, replace(data, history=[]))
    update(coord, entity, data)
    assert fired.types() == []


@pytest.mark.asyncio
async def test_seen_events_are_forgotten_once_out_of_the_window():
    inside = feeding("in", NOW - LOOKBACK + timedelta(minutes=1))
    outside = feeding("out", NOW - LOOKBACK - timedelta(minutes=1))
    data = FeederData(history=[inside, outside], history_ok=True)
    coord, entity, _ = await added("feeder_feeding", data)
    update(coord, entity, replace(data, history=[]))
    assert set(entity._seen) == {"in"}


@pytest.mark.asyncio
async def test_food_alerts_ignore_other_status_types():
    data = FeederData(status_ok=True)
    coord, entity, fired = await added("feeder_food_alert", data)
    events = [
        StatusEvent("x", kind, NOW) for kind in ("device_online", "device_offline")
    ]
    update(coord, entity, replace(data, status_events=events))
    assert fired.types() == []


@pytest.mark.asyncio
async def test_food_alerts_without_an_id_never_fire():
    data = FeederData(status_ok=True)
    coord, entity, fired = await added("feeder_food_alert", data)
    events = [StatusEvent(None, "food_level_low", NOW)]
    update(coord, entity, replace(data, status_events=events))
    assert fired.types() == [] and entity._seen == {}
    events.append(StatusEvent("a", "food_outlet_stuck", NOW))
    update(coord, entity, replace(data, status_events=events))
    assert fired.types() == ["food_outlet_stuck"]


def test_entities_have_stable_ids_and_known_types():
    entities = {
        item.key: FeederEvent(coordinator(FeederData()), item) for item in _EVENTS
    }
    assert {key: entity.unique_id for key, entity in entities.items()} == {
        "feeder_feeding": "aa:bb_feeder_feeding",
        "feeder_food_alert": "aa:bb_feeder_food_alert",
    }
    assert entities["feeder_feeding"].event_types == ["meal_dispensed"]
    assert entities["feeder_food_alert"].event_types == [
        "food_level_low",
        "food_outlet_stuck",
    ]


def test_every_event_type_has_a_label_in_every_language():
    files = [
        COMPONENT / "strings.json",
        *sorted((COMPONENT / "translations").glob("*.json")),
    ]
    assert len(files) == 13
    for path in files:
        events = json.loads(path.read_text(encoding="utf-8"))["entity"]["event"]
        for item in _EVENTS:
            labels = events[item.key]["state_attributes"]["event_type"]["state"]
            assert set(labels) == set(item.types), path
            assert all(labels.values()) and events[item.key]["name"], path


@pytest.mark.asyncio
@pytest.mark.parametrize("feeder", [True, False])
async def test_only_feeder_entries_get_events(feeder):
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coordinator(FeederData())}}
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.data = {CONF_IS_FEEDER: True} if feeder else {}
    add = MagicMock()
    await event_platform.async_setup_entry(hass, entry, add)
    if feeder:
        assert [entity.unique_id for entity in add.call_args.args[0]] == [
            "aa:bb_feeder_feeding",
            "aa:bb_feeder_food_alert",
        ]
    else:
        add.assert_not_called()

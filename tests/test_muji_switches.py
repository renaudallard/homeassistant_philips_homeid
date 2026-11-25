"""Tests for the MUJI air purifier switches and when they are created."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.philips_homeid.const import DOMAIN
from custom_components.philips_homeid.switch import (
    PhilipsHomeIDMujiChildLockSwitch,
    PhilipsHomeIDMujiDisplayLightSwitch,
    PhilipsHomeIDSensorMonitorSwitch,
    async_setup_entry,
)


def _bare(cls, properties):
    sw = cls.__new__(cls)
    coord = MagicMock()
    coord.async_set_control_property = AsyncMock()
    coord.async_set_control_properties = AsyncMock()
    state = MagicMock()
    state.properties = properties
    coord.device_state = state
    sw.coordinator = coord
    return sw, coord


@pytest.mark.parametrize(
    ("properties", "expected"),
    [({"D03103": 1}, True), ({"D03103": 0}, False), ({}, None)],
)
def test_child_lock_state(properties, expected):
    sw, _ = _bare(PhilipsHomeIDMujiChildLockSwitch, properties)
    assert sw.is_on is expected


@pytest.mark.asyncio
async def test_child_lock_writes_d03103():
    sw, coord = _bare(PhilipsHomeIDMujiChildLockSwitch, {})
    await sw.async_turn_on()
    await sw.async_turn_off()
    assert [c.args for c in coord.async_set_control_property.await_args_list] == [
        ("D03103", 1),
        ("D03103", 0),
    ]


def _setup(model, known):
    coord = MagicMock()
    coord.device_id = "dev"
    coord.device_info.model_name = model
    coord.has_property.side_effect = lambda key, nested=None: key in known
    callbacks = []
    coord.register_new_property_callback.side_effect = lambda cb: (
        callbacks.append(cb) or MagicMock()
    )
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coord}}
    entry = MagicMock(entry_id="entry", data={})
    return hass, entry, callbacks


def _types(added):
    return [type(e) for batch in added for e in batch]


@pytest.mark.asyncio
async def test_muji_switches_are_created_once_their_codes_arrive():
    hass, entry, callbacks = _setup("AC0720/10", {"D03134"})
    known = hass.data[DOMAIN]["entry"].has_property
    added = []
    await async_setup_entry(hass, entry, lambda ents, *a, **k: added.append(ents))
    assert _types(added) == [PhilipsHomeIDSensorMonitorSwitch]
    assert len(callbacks) == 1

    known.side_effect = lambda key, nested=None: key in {"D03134", "D03103"}
    callbacks[0]([])
    callbacks[0]([])
    assert _types(added) == [
        PhilipsHomeIDSensorMonitorSwitch,
        PhilipsHomeIDMujiChildLockSwitch,
    ]


@pytest.mark.asyncio
async def test_sensor_monitor_switch_can_arrive_late():
    hass, entry, callbacks = _setup("AC0651/10", set())
    known = hass.data[DOMAIN]["entry"].has_property
    added = []
    await async_setup_entry(hass, entry, lambda ents, *a, **k: added.append(ents))
    assert added == []
    known.side_effect = lambda key, nested=None: key in {"D03134", "D03103"}
    callbacks[0]([])
    callbacks[0]([])
    assert _types(added) == [PhilipsHomeIDSensorMonitorSwitch]


@pytest.mark.asyncio
async def test_no_muji_callback_once_everything_exists():
    hass, entry, callbacks = _setup("AC1715/70", {"D03134", "D03103", "D03105"})
    added = []
    await async_setup_entry(hass, entry, lambda ents, *a, **k: added.append(ents))
    assert _types(added) == [
        PhilipsHomeIDSensorMonitorSwitch,
        PhilipsHomeIDMujiDisplayLightSwitch,
    ]
    assert callbacks == []


@pytest.mark.asyncio
async def test_display_light_only_on_ac17():
    hass, entry, _ = _setup("AC0720/10", {"D03134", "D03105"})
    added = []
    await async_setup_entry(hass, entry, lambda ents, *a, **k: added.append(ents))
    assert PhilipsHomeIDMujiDisplayLightSwitch not in _types(added)


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        ({"D03105": 100}, True),
        ({"D03105": 1}, True),
        ({"D03105": 0}, False),
        ({}, None),
    ],
)
def test_display_light_state(properties, expected):
    sw, _ = _bare(PhilipsHomeIDMujiDisplayLightSwitch, properties)
    assert sw.is_on is expected


@pytest.mark.asyncio
async def test_display_light_writes_both_codes_together():
    sw, coord = _bare(PhilipsHomeIDMujiDisplayLightSwitch, {})
    await sw.async_turn_on()
    await sw.async_turn_off()
    assert [c.args for c in coord.async_set_control_properties.await_args_list] == [
        ({"D03104": 100, "D03105": 100},),
        ({"D03104": 0, "D03105": 0},),
    ]

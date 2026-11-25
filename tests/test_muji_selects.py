"""Tests for the MUJI purifier selects from the Air+ app.

The values follow the Air+ app: the lamp sheet writes D03135 and D03137 in one
Control write (off is D03135 alone), brightness is D03104.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.philips_homeid.const import DOMAIN
from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.select import (
    PhilipsHomeIDMujiBrightnessSelect,
    PhilipsHomeIDMujiLampSelect,
    PhilipsHomeIDMujiModeSelect,
    PhilipsHomeIDMujiPreferredIndexSelect,
    PhilipsHomeIDMujiTimerSelect,
    async_setup_entry,
)
from custom_components.philips_homeid.sensor_descriptions import AIR_PURIFIER_SENSORS


def _bare(cls, properties):
    sel = cls.__new__(cls)
    coord = MagicMock()
    coord.async_set_control_property = AsyncMock()
    coord.async_set_control_properties = AsyncMock()
    state = MagicMock()
    state.properties = properties
    coord.device_state = state
    sel.coordinator = coord
    return sel, coord


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        ({"D03135": 0}, "off"),
        ({"D03135": 0, "D03137": 3}, "off"),
        ({"D03135": 2, "D03137": 1}, "warm"),
        ({"D03135": 2, "D03137": 2}, "dawn"),
        ({"D03135": 2, "D03137": 3}, "calm"),
        ({"D03135": 2, "D03137": 4}, "breath"),
        ({"D03135": 2, "D03137": 9}, None),
        ({"D03135": 2}, None),
        ({"D03135": 1, "D03137": 1}, None),
        ({"D03135": "x"}, None),
        ({}, None),
    ],
)
def test_lamp_current_option(properties, expected):
    sel, _ = _bare(PhilipsHomeIDMujiLampSelect, properties)
    assert sel.current_option == expected


@pytest.mark.asyncio
async def test_lamp_off_writes_mode_only():
    sel, coord = _bare(PhilipsHomeIDMujiLampSelect, {})
    await sel.async_select_option("off")
    coord.async_set_control_properties.assert_awaited_once_with({"D03135": 0})


@pytest.mark.asyncio
async def test_lamp_effect_writes_mode_and_effect_together():
    sel, coord = _bare(PhilipsHomeIDMujiLampSelect, {})
    await sel.async_select_option("calm")
    coord.async_set_control_properties.assert_awaited_once_with(
        {"D03135": 2, "D03137": 3}
    )


@pytest.mark.asyncio
async def test_lamp_ignores_an_unknown_option():
    sel, coord = _bare(PhilipsHomeIDMujiLampSelect, {})
    await sel.async_select_option("bogus")
    coord.async_set_control_properties.assert_not_awaited()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "off"), (115, "low"), (123, "bright"), (101, "automatic"), (50, None)],
)
def test_brightness_current_option(value, expected):
    sel, _ = _bare(PhilipsHomeIDMujiBrightnessSelect, {"D03104": value})
    assert sel.current_option == expected


@pytest.mark.asyncio
async def test_brightness_writes_d03104():
    sel, coord = _bare(PhilipsHomeIDMujiBrightnessSelect, {})
    await sel.async_select_option("bright")
    coord.async_set_control_property.assert_awaited_once_with("D03104", 123)
    coord.async_set_control_property.reset_mock()
    await sel.async_select_option("bogus")
    coord.async_set_control_property.assert_not_awaited()


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
    entry = MagicMock(entry_id="entry")
    added = []
    return hass, entry, coord, known, callbacks, added


def _types(added):
    return {type(e) for batch in added for e in batch}


@pytest.mark.asyncio
async def test_setup_creates_each_select_once_its_codes_arrive():
    hass, entry, _, known, callbacks, added = _setup("AC0721/10", {"D0310C"})
    await async_setup_entry(hass, entry, lambda ents, *a, **k: added.append(ents))
    assert _types(added) == {PhilipsHomeIDMujiModeSelect}
    assert len(callbacks) == 1

    # The effect alone is not enough: the lamp select needs both codes.
    known.add("D03137")
    callbacks[0]([])
    assert _types(added) == {PhilipsHomeIDMujiModeSelect}

    known.update({"D03135", "D03104", "D03110"})
    callbacks[0]([])
    callbacks[0]([])
    assert _types(added) == {
        PhilipsHomeIDMujiModeSelect,
        PhilipsHomeIDMujiLampSelect,
        PhilipsHomeIDMujiBrightnessSelect,
        PhilipsHomeIDMujiTimerSelect,
    }
    assert sum(len(batch) for batch in added) == 4


@pytest.mark.asyncio
async def test_setup_skips_lamp_selects_on_other_models():
    hass, entry, _, _, callbacks, added = _setup(
        "AC1715/70", {"D0310C", "D03135", "D03137", "D03104", "D03110", "D0312A"}
    )
    await async_setup_entry(hass, entry, lambda ents, *a, **k: added.append(ents))
    assert _types(added) == {
        PhilipsHomeIDMujiModeSelect,
        PhilipsHomeIDMujiPreferredIndexSelect,
    }
    assert callbacks == []


class _Coordinator:
    """Runs the real multi-property Control write and records what it sends."""

    async_set_control_properties = PhilipsHomeIDCoordinator.async_set_control_properties

    def __init__(self, fusion):
        self._is_fusion = fusion
        self.sent = []

    async def _mqtt_command(self, port, props):
        self.sent.append((port, props))
        return True


@pytest.mark.asyncio
async def test_control_properties_go_out_in_one_write():
    coord = _Coordinator(fusion=True)
    assert await coord.async_set_control_properties({"D03135": 2, "D03137": 1})
    assert coord.sent == [("control", {"D03135": 2, "D03137": 1})]


@pytest.mark.asyncio
async def test_control_properties_need_fusion():
    coord = _Coordinator(fusion=False)
    assert not await coord.async_set_control_properties({"D03135": 0})
    assert coord.sent == []


@pytest.mark.asyncio
async def test_setup_mode_select_arrives_late_on_other_models():
    hass, entry, _, known, callbacks, added = _setup("AC0650/10", set())
    await async_setup_entry(hass, entry, lambda ents, *a, **k: added.append(ents))
    assert added == []
    assert len(callbacks) == 1
    known.update({"D0310C", "D03135", "D03137", "D03104"})
    callbacks[0]([])
    callbacks[0]([])
    assert _types(added) == {PhilipsHomeIDMujiModeSelect}
    assert sum(len(batch) for batch in added) == 1


@pytest.mark.asyncio
async def test_setup_skips_purifiers_without_a_mode_map():
    hass, entry, _, _, callbacks, added = _setup(
        "AC2939/10", {"D0310C", "D03135", "D03137", "D03104"}
    )
    await async_setup_entry(hass, entry, lambda ents, *a, **k: added.append(ents))
    assert added == []
    assert callbacks == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "off"), (1, "30_min"), (2, "1_h"), (3, "2_h"), (13, "12_h"), (14, None)],
)
def test_timer_current_option(value, expected):
    sel, _ = _bare(PhilipsHomeIDMujiTimerSelect, {"D03110": value})
    assert sel.current_option == expected


@pytest.mark.asyncio
async def test_timer_writes_the_app_values():
    sel, coord = _bare(PhilipsHomeIDMujiTimerSelect, {})
    for option in ("off", "30_min", "1_h", "12_h", "bogus"):
        await sel.async_select_option(option)
    assert [c.args for c in coord.async_set_control_property.await_args_list] == [
        ("D03110", 0),
        ("D03110", 1),
        ("D03110", 2),
        ("D03110", 13),
    ]


def test_timer_remaining_sensor_reads_minutes():
    (desc,) = [d for d in AIR_PURIFIER_SENSORS if d.key == "muji_timer_remaining"]
    assert desc.property_key == "D03211"
    assert desc.native_unit_of_measurement == "min"


@pytest.mark.asyncio
async def test_preferred_index_only_on_ac17():
    hass, entry, _, _, _, added = _setup("AC0720/10", {"D0310C", "D0312A"})
    await async_setup_entry(hass, entry, lambda ents, *a, **k: added.append(ents))
    assert PhilipsHomeIDMujiPreferredIndexSelect not in _types(added)


@pytest.mark.asyncio
async def test_preferred_index_reads_and_writes_d0312a():
    sel, coord = _bare(PhilipsHomeIDMujiPreferredIndexSelect, {"D0312A": 1})
    assert sel.current_option == "pm25"
    await sel.async_select_option("indoor_air_index")
    coord.async_set_control_property.assert_awaited_once_with("D0312A", 0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, "no_error"),
        (0x8001, "motor_error"),
        (0xC001, "pre_filter_clean"),
        (0xC002, "filter_replace"),
        (0xC004, "filter_lock"),
        (7, "unknown (7)"),
    ],
)
def test_error_code_sensor_decodes_d03240(raw, expected):
    (desc,) = [d for d in AIR_PURIFIER_SENSORS if d.key == "muji_error_code"]
    assert desc.property_key == "D03240"
    assert desc.value_fn(raw) == expected

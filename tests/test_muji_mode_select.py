"""Tests for the MUJI air-purifier operation-mode select.

It mirrors the fan's preset modes: reads the current mode from D0310C and, on
selection, powers the device on first (the NCP ignores a mode write while off)
and writes the mode to the Control port.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.philips_homeid.select import PhilipsHomeIDMujiModeSelect


def _select(properties, power_on=True):
    sel = PhilipsHomeIDMujiModeSelect.__new__(PhilipsHomeIDMujiModeSelect)
    coord = MagicMock()
    coord.async_set_power = AsyncMock()
    coord.async_set_control_property = AsyncMock()
    state = MagicMock()
    state.properties = properties
    state.power_on = power_on
    coord.device_state = state
    sel.coordinator = coord
    sel._mode_map = {"auto": 0, "medium": 1, "sleep": 17, "turbo": 18}
    sel._mode_reverse = {v: k for k, v in sel._mode_map.items()}
    return sel, coord


def test_current_option_maps_d0310c():
    sel, _ = _select({"D0310C": 17})
    assert sel.current_option == "sleep"


def test_current_option_is_none_when_absent():
    sel, _ = _select({})
    assert sel.current_option is None


@pytest.mark.asyncio
async def test_select_writes_mode_and_powers_on_when_off():
    sel, coord = _select({}, power_on=False)
    await sel.async_select_option("turbo")
    coord.async_set_power.assert_awaited_once_with(True)
    coord.async_set_control_property.assert_awaited_once_with("D0310C", 18)


@pytest.mark.asyncio
async def test_select_does_not_power_on_when_already_on():
    sel, coord = _select({"D0310C": 0}, power_on=True)
    await sel.async_select_option("auto")
    coord.async_set_power.assert_not_awaited()
    coord.async_set_control_property.assert_awaited_once_with("D0310C", 0)


@pytest.mark.asyncio
async def test_select_ignores_an_unknown_option():
    sel, coord = _select({})
    await sel.async_select_option("bogus")
    coord.async_set_control_property.assert_not_awaited()

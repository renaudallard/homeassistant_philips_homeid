"""Tests for the property set the FUSION control port is handed."""

from types import SimpleNamespace

import pytest

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.local_models import PORT_AIRFRYER, PORT_VENUSAF


class _Stub:
    """Runs the real FUSION send path and records what it would publish."""

    async_airfryer_set_settings = PhilipsHomeIDCoordinator.async_airfryer_set_settings
    _current_raw_temp_unit = PhilipsHomeIDCoordinator._current_raw_temp_unit
    _get_airfryer_status = PhilipsHomeIDCoordinator._get_airfryer_status

    def __init__(self, port, airfryer=None):
        self._is_fusion = True
        self._port = port
        self._state = SimpleNamespace(
            properties={"airfryer": airfryer or {"status": "idle", "temp_unit": False}}
        )
        self.sent = []

    def airfryer_style_port(self):
        return self._port

    @property
    def _fusion_setting_status(self):
        return "precook" if self._port == PORT_VENUSAF else "setting"

    async def _ensure_fusion_control_port(self):
        return True

    async def _mqtt_command(self, port, props):
        self.sent.append((port, props))
        return True


@pytest.mark.asyncio
async def test_probe_temperature_is_sent_as_temp_probe():
    """probe_temp is not a field any appliance has.

    Every port spells it temp_probe: the Venus control and status ports, and
    the local HTTP port the non-FUSION path already writes.
    """
    stub = _Stub(PORT_VENUSAF)

    await stub.async_airfryer_set_settings(probe_temp=70)

    _, props = stub.sent[-1]
    assert props["temp_probe"] == 70
    assert "probe_temp" not in props


@pytest.mark.asyncio
async def test_a_probe_temperature_asks_for_the_probe():
    """Matches the local path, which sets probe_required with the target."""
    stub = _Stub(PORT_VENUSAF)

    await stub.async_airfryer_set_settings(probe_temp=70)

    _, props = stub.sent[-1]
    assert props["probe_required"] is True


@pytest.mark.asyncio
async def test_settings_without_a_probe_do_not_ask_for_one():
    stub = _Stub(PORT_AIRFRYER)

    await stub.async_airfryer_set_settings(temp=180)

    _, props = stub.sent[-1]
    assert "probe_required" not in props
    assert "temp_probe" not in props

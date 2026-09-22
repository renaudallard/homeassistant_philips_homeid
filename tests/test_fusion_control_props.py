"""Tests for the property set the FUSION control port is handed."""

from types import SimpleNamespace

import pytest

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.local_models import PORT_AIRFRYER, PORT_VENUSAF


class _Stub:
    """Runs the real FUSION send path and records what it would publish."""

    async_airfryer_set_settings = PhilipsHomeIDCoordinator.async_airfryer_set_settings
    async_airfryer_update_settings = (
        PhilipsHomeIDCoordinator.async_airfryer_update_settings
    )
    async_airfryer_keep_warm = PhilipsHomeIDCoordinator.async_airfryer_keep_warm
    keep_warm_temp = PhilipsHomeIDCoordinator.keep_warm_temp
    _temp_in_device_unit = PhilipsHomeIDCoordinator._temp_in_device_unit
    _fusion_control_has_temp_unit = (
        PhilipsHomeIDCoordinator._fusion_control_has_temp_unit
    )
    _current_raw_temp_unit = PhilipsHomeIDCoordinator._current_raw_temp_unit
    _get_airfryer_status = PhilipsHomeIDCoordinator._get_airfryer_status
    is_airfryer_cooking = PhilipsHomeIDCoordinator.is_airfryer_cooking

    def __init__(self, port, airfryer=None):
        self._is_fusion = True
        self._port = port
        self._keep_warm_temp = None
        self._keep_warm_time = 3600
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

    async def _wait_for_status(self, target, timeout=10):
        return True

    async def _mqtt_command(self, port, props):
        self.sent.append((port, props))
        return True

    def props_for(self, key):
        """Return the published property set that carries a given key."""
        for _, props in self.sent:
            if key in props:
                return props
        return {}


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


@pytest.mark.asyncio
async def test_spectre_settings_still_echo_the_unit():
    """The SPECTRE control port has temp_unit and issue #27 needs it echoed."""
    stub = _Stub(PORT_AIRFRYER, {"status": "idle", "temp_unit": True})

    await stub.async_airfryer_set_settings(temp=180)

    _, props = stub.sent[-1]
    assert props["temp_unit"] is True


@pytest.mark.asyncio
async def test_venus_settings_do_not_carry_a_unit():
    """The Venus control port has no temp_unit field.

    It is reported on the status port, which is where the echoed value comes
    from, so the value is available even though the port cannot take it.
    """
    stub = _Stub(PORT_VENUSAF, {"status": "idle", "temp_unit": True})

    await stub.async_airfryer_set_settings(temp=180)

    _, props = stub.sent[-1]
    assert "temp_unit" not in props


@pytest.mark.asyncio
async def test_venus_settings_update_does_not_carry_a_unit():
    stub = _Stub(PORT_VENUSAF, {"status": "idle", "temp_unit": True})

    await stub.async_airfryer_update_settings(temp=180)

    _, props = stub.sent[-1]
    assert "temp_unit" not in props
    assert props["temp"] == 180


@pytest.mark.asyncio
async def test_spectre_settings_update_still_echoes_the_unit():
    stub = _Stub(PORT_AIRFRYER, {"status": "idle", "temp_unit": True})

    await stub.async_airfryer_update_settings(temp=180)

    _, props = stub.sent[-1]
    assert props["temp_unit"] is True


@pytest.mark.asyncio
async def test_venus_keep_warm_does_not_carry_a_unit():
    stub = _Stub(PORT_VENUSAF, {"status": "idle", "temp_unit": True})

    await stub.async_airfryer_keep_warm()

    assert "temp_unit" not in stub.props_for("time")


@pytest.mark.asyncio
async def test_spectre_keep_warm_still_echoes_the_unit():
    stub = _Stub(PORT_AIRFRYER, {"status": "idle", "temp_unit": True})

    await stub.async_airfryer_keep_warm()

    assert stub.props_for("time")["temp_unit"] is True


@pytest.mark.asyncio
async def test_venus_keep_warm_sends_only_what_the_app_sends():
    """The appliance picks its own keep warm temperature.

    APK VenusCookingKeepWarmSettingsConverter builds the command from the
    duration, the method and the status alone, which is what the local path
    already does.
    """
    stub = _Stub(PORT_VENUSAF, {"status": "idle", "temp_unit": True})

    await stub.async_airfryer_keep_warm()

    props = stub.props_for("time")
    assert set(props) == {"status", "preset", "time"}


@pytest.mark.asyncio
async def test_spectre_keep_warm_still_names_a_temperature():
    stub = _Stub(PORT_AIRFRYER, {"status": "idle", "temp_unit": False})

    await stub.async_airfryer_keep_warm()

    props = stub.props_for("time")
    assert props["temp"] == 65
    assert props["preset"] == 8

"""Tests for the precook a FUSION Venus is sent before a cook."""

from types import SimpleNamespace

import pytest

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.local_models import PORT_AIRFRYER, PORT_VENUS1AF


class _Stub:
    """Runs the real FUSION settings paths and records what they publish."""

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

    def __init__(self, port, status="mainmenu"):
        self._is_fusion = True
        self._port = port
        self._keep_warm_temp = None
        self._keep_warm_time = 3600
        self._state = SimpleNamespace(
            properties={"airfryer": {"status": status, "temp_unit": False}}
        )
        self.sent = []

    def airfryer_style_port(self):
        return self._port

    @property
    def _fusion_setting_status(self):
        return "setting" if self._port == PORT_AIRFRYER else "precook"

    async def _ensure_fusion_control_port(self):
        return True

    async def _wait_for_status(self, target, timeout=10):
        return True

    async def _mqtt_command(self, port, props):
        self.sent.append(props)
        return True


@pytest.mark.asyncio
async def test_a_venus_precook_says_no_probe_is_required():
    """An HD9875 answers {"status": "precook", "method": 0} with NCP ok and
    stays in mainmenu, so the cook that follows never starts. With
    probe_rqrd false, as the local start sends it, it goes to precook.
    """
    stub = _Stub(PORT_VENUS1AF)

    await stub.async_airfryer_set_settings(preset=0)

    assert stub.sent[-1] == {"status": "precook", "preset": 0, "probe_required": False}


@pytest.mark.asyncio
async def test_a_venus_precook_with_a_probe_still_asks_for_it():
    stub = _Stub(PORT_VENUS1AF)

    await stub.async_airfryer_set_settings(probe_temp=70)

    assert stub.sent[-1]["probe_required"] is True


@pytest.mark.asyncio
async def test_a_spectre_setting_carries_no_probe_flag():
    stub = _Stub(PORT_AIRFRYER, status="idle")

    await stub.async_airfryer_set_settings(preset=1)

    assert "probe_required" not in stub.sent[-1]


@pytest.mark.asyncio
async def test_a_venus_takes_temperature_and_time_alone_before_a_cook():
    """The local path sends a Venus its values without a status. On an
    HD9875 precook, then {"temp": 200}, then {"total_time": 960}, then
    cooking starts the cook at those settings.
    """
    stub = _Stub(PORT_VENUS1AF, status="precook")

    await stub.async_airfryer_update_settings(temp=200)
    await stub.async_airfryer_update_settings(time_seconds=960)

    assert stub.sent == [{"temp": 200}, {"time": 960}]


@pytest.mark.asyncio
async def test_a_spectre_still_sends_its_values_with_the_setting_status():
    stub = _Stub(PORT_AIRFRYER, status="idle")

    await stub.async_airfryer_update_settings(temp=200)

    assert stub.sent[-1]["status"] == "setting"

"""Tests for changing a FUSION airfryer's settings while it cooks."""

from types import SimpleNamespace

import pytest

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.local_models import PORT_AIRFRYER, PORT_VENUS1AF


class _Stub:
    """Runs the real FUSION update path on a cooking appliance."""

    async_airfryer_update_settings = (
        PhilipsHomeIDCoordinator.async_airfryer_update_settings
    )
    _fusion_venus_update_mid_cook = (
        PhilipsHomeIDCoordinator._fusion_venus_update_mid_cook
    )
    _fusion_control_has_temp_unit = (
        PhilipsHomeIDCoordinator._fusion_control_has_temp_unit
    )
    _current_raw_temp_unit = PhilipsHomeIDCoordinator._current_raw_temp_unit
    _get_airfryer_status = PhilipsHomeIDCoordinator._get_airfryer_status
    is_airfryer_cooking = PhilipsHomeIDCoordinator.is_airfryer_cooking

    def __init__(self, port, refuse=()):
        self._is_fusion = True
        self._port = port
        self._refuse = refuse
        self._state = SimpleNamespace(
            properties={"airfryer": {"status": "cooking", "temp_unit": False}}
        )
        self.sent = []

    def airfryer_style_port(self):
        return self._port

    @property
    def _fusion_setting_status(self):
        return "setting" if self._port == PORT_AIRFRYER else "precook"

    async def _mqtt_command(self, port, props):
        self.sent.append(props)
        return not any(key in props for key in self._refuse)


@pytest.mark.asyncio
async def test_a_venus_is_paused_set_and_resumed_mid_cook():
    """On an HD9875 {"temp": 180} sent mid-cook is accepted and shows on the
    status port, but the cook carries on at its old temperature. The local
    path pauses, sets and resumes, and pause then cooking resumes the cook
    where it was over FUSION too.
    """
    stub = _Stub(PORT_VENUS1AF)

    assert await stub.async_airfryer_update_settings(temp=180) is True

    assert stub.sent == [{"status": "pause"}, {"temp": 180}, {"status": "cooking"}]


@pytest.mark.asyncio
async def test_a_refused_mid_cook_change_still_resumes_and_reports_it():
    stub = _Stub(PORT_VENUS1AF, refuse=("temp",))

    assert await stub.async_airfryer_update_settings(temp=180) is False

    assert stub.sent[-1] == {"status": "cooking"}


@pytest.mark.asyncio
async def test_a_spectre_still_takes_values_alone_mid_cook():
    stub = _Stub(PORT_AIRFRYER)

    await stub.async_airfryer_update_settings(temp=180)

    assert stub.sent == [{"temp": 180, "temp_unit": False}]

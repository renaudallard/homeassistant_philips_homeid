"""Tests for when FUSION keep warm presses start."""

from types import SimpleNamespace

import pytest

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.local_models import PORT_AIRFRYER, PORT_VENUS1AF


class _Stub:
    """Runs the real FUSION keep warm against an appliance that may refuse."""

    async_airfryer_keep_warm = PhilipsHomeIDCoordinator.async_airfryer_keep_warm
    keep_warm_temp = PhilipsHomeIDCoordinator.keep_warm_temp
    _temp_in_device_unit = PhilipsHomeIDCoordinator._temp_in_device_unit
    _fusion_control_has_temp_unit = (
        PhilipsHomeIDCoordinator._fusion_control_has_temp_unit
    )
    _current_raw_temp_unit = PhilipsHomeIDCoordinator._current_raw_temp_unit
    _get_airfryer_status = PhilipsHomeIDCoordinator._get_airfryer_status

    def __init__(self, port, refuse_settings):
        self._is_fusion = True
        self._port = port
        self._refuse_settings = refuse_settings
        self._keep_warm_temp = None
        self._keep_warm_time = 3600
        self._state = SimpleNamespace(
            properties={"airfryer": {"status": "mainmenu", "temp_unit": False}}
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
        return not (self._refuse_settings and "time" in props)


@pytest.mark.asyncio
@pytest.mark.parametrize("port", [PORT_VENUS1AF, PORT_AIRFRYER])
async def test_refused_keep_warm_settings_do_not_start_a_cook(port):
    """Start after refused settings runs the appliance's previous program.

    On an HD9875 a refused settings write followed by start cooked at the
    180 degrees and one minute it still held, not at what was asked for.
    """
    stub = _Stub(port, refuse_settings=True)

    assert await stub.async_airfryer_keep_warm() is False
    assert all(props.get("status") != "cooking" for props in stub.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("port", [PORT_VENUS1AF, PORT_AIRFRYER])
async def test_accepted_keep_warm_settings_start(port):
    stub = _Stub(port, refuse_settings=False)

    assert await stub.async_airfryer_keep_warm() is True
    assert stub.sent[-1] == {"status": "cooking"}

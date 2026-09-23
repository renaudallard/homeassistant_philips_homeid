"""Tests for waking a FUSION airfryer out of standby before a settings write."""

from types import SimpleNamespace

import pytest

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.local_models import (
    PORT_AIRFRYER,
    PORT_VENUS1AF,
    PORT_VENUSAF,
)


class _Stub:
    """Runs the real FUSION settings paths from standby and records the wake."""

    async_airfryer_set_settings = PhilipsHomeIDCoordinator.async_airfryer_set_settings
    async_airfryer_keep_warm = PhilipsHomeIDCoordinator.async_airfryer_keep_warm
    keep_warm_temp = PhilipsHomeIDCoordinator.keep_warm_temp
    _fusion_wake_status = PhilipsHomeIDCoordinator._fusion_wake_status
    _temp_in_device_unit = PhilipsHomeIDCoordinator._temp_in_device_unit
    _fusion_control_has_temp_unit = (
        PhilipsHomeIDCoordinator._fusion_control_has_temp_unit
    )
    _current_raw_temp_unit = PhilipsHomeIDCoordinator._current_raw_temp_unit
    _get_airfryer_status = PhilipsHomeIDCoordinator._get_airfryer_status

    def __init__(self, port):
        self._is_fusion = True
        self._port = port
        self._keep_warm_temp = None
        self._keep_warm_time = 3600
        self._state = SimpleNamespace(
            properties={"airfryer": {"status": "standby", "temp_unit": False}}
        )
        self.sent = []
        self.waited_for = []

    def airfryer_style_port(self):
        return self._port

    @property
    def _fusion_setting_status(self):
        return "setting" if self._port == PORT_AIRFRYER else "precook"

    async def _ensure_fusion_control_port(self):
        return True

    async def _wait_for_status(self, target, timeout=10):
        self.waited_for.append(target)
        return True

    async def _mqtt_command(self, port, props):
        self.sent.append((port, props))
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("port", [PORT_VENUSAF, PORT_VENUS1AF])
async def test_a_venus_is_woken_to_mainmenu(port):
    """A Venus has no idle; it goes from standby to mainmenu.

    Asking it for idle and waiting on idle ran out the full wait on an
    HD9875 ("Timeout waiting for airfryer status idle") before every
    settings write sent from standby.
    """
    stub = _Stub(port)

    await stub.async_airfryer_set_settings(temp=200)

    assert stub.sent[0] == ("control", {"status": "mainmenu"})
    assert stub.waited_for[0] == "mainmenu"


@pytest.mark.asyncio
async def test_a_spectre_is_still_woken_to_idle():
    stub = _Stub(PORT_AIRFRYER)

    await stub.async_airfryer_set_settings(temp=200)

    assert stub.sent[0] == ("control", {"status": "idle"})
    assert stub.waited_for[0] == "idle"


@pytest.mark.asyncio
async def test_keep_warm_wakes_a_venus_to_mainmenu_too():
    stub = _Stub(PORT_VENUS1AF)

    await stub.async_airfryer_keep_warm()

    assert stub.sent[0] == ("control", {"status": "mainmenu"})
    assert stub.waited_for[0] == "mainmenu"


@pytest.mark.asyncio
async def test_an_awake_appliance_is_not_woken():
    stub = _Stub(PORT_VENUS1AF)
    stub._state.properties["airfryer"]["status"] = "mainmenu"

    await stub.async_airfryer_set_settings(temp=200)

    assert all(props.get("status") != "mainmenu" for _, props in stub.sent)

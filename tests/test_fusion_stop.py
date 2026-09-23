"""Tests for stopping a FUSION airfryer."""

import pytest

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.local_models import (
    PORT_AIRFRYER,
    PORT_VENUS1AF,
    PORT_VENUSAF,
)


class _Stub:
    """Runs the real FUSION stop and records what it would publish."""

    async_airfryer_stop = PhilipsHomeIDCoordinator.async_airfryer_stop

    def __init__(self, port, refuse=(), status="cooking"):
        self._is_fusion = True
        self._port = port
        self._refuse = refuse
        self._status = status
        self.sent = []

    def airfryer_style_port(self):
        return self._port

    def _get_airfryer_status(self):
        return self._status

    async def _mqtt_command(self, port, props):
        self.sent.append((port, props))
        return props.get("status") not in self._refuse


@pytest.mark.asyncio
@pytest.mark.parametrize("port", [PORT_VENUSAF, PORT_VENUS1AF])
async def test_a_venus_stops_through_pause_to_mainmenu(port):
    """A bare standby is acknowledged by an HD9875 mid-cook and ignored.

    The local path already stops a Venus with pause then mainmenu, and that
    sequence does end the cook on the same appliance over FUSION.
    """
    stub = _Stub(port)

    assert await stub.async_airfryer_stop() is True

    assert stub.sent == [
        ("control", {"status": "pause"}),
        ("control", {"status": "mainmenu"}),
    ]


@pytest.mark.asyncio
async def test_a_venus_stop_reports_the_mainmenu_verdict():
    """A refused pause does not fail the stop, as on the local path.

    Stop is offered outside a cook too, where a pause has nothing to act on.
    """
    stub = _Stub(PORT_VENUS1AF, refuse=("pause",))

    assert await stub.async_airfryer_stop() is True
    assert stub.sent[-1] == ("control", {"status": "mainmenu"})


@pytest.mark.asyncio
async def test_a_spectre_still_stops_to_standby():
    stub = _Stub(PORT_AIRFRYER)

    assert await stub.async_airfryer_stop() is True

    assert stub.sent == [("control", {"status": "standby"})]


@pytest.mark.asyncio
async def test_stopping_a_venus_in_standby_sends_nothing():
    """mainmenu is what wakes a Venus from standby, so a stop would turn it on."""
    stub = _Stub(PORT_VENUS1AF, status="standby")

    assert await stub.async_airfryer_stop() is True
    assert stub.sent == []

"""Tests for starting a FUSION airfryer."""

import pytest

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator


class _Stub:
    """Runs the real FUSION start and records what it would publish."""

    async_airfryer_start = PhilipsHomeIDCoordinator.async_airfryer_start

    def __init__(self, preheat):
        self._is_fusion = True
        self._preheat_enabled = preheat
        self.sent = []

    async def _ensure_fusion_control_port(self):
        return True

    async def _mqtt_command(self, port, props):
        self.sent.append((port, props))
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("preheat", [False, True])
async def test_start_sends_only_the_cooking_status(preheat):
    """The preheat switch does not reach the FUSION start.

    An HD9875 refused {"status": "cooking", "preheat": true} with NCP
    port_error, so passing the switch through stopped the cook starting.
    """
    stub = _Stub(preheat)

    await stub.async_airfryer_start()

    assert stub.sent == [("control", {"status": "cooking"})]

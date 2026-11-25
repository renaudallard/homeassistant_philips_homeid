"""Tests for the MUJI air purifier filter timer resets."""

import json
from unittest.mock import MagicMock

import pytest

from custom_components.philips_homeid.button import AIR_PURIFIER_BUTTONS
from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.mqtt_api import (
    FusionDeviceInfo,
    PhilipsMQTTClient,
)


class _Stub:
    """Runs the real reset path and records what it would publish."""

    async_reset_filter = PhilipsHomeIDCoordinator.async_reset_filter

    def __init__(self, fusion=True, properties=None):
        self._is_fusion = fusion
        self._state = None
        if properties is not None:
            self._state = MagicMock()
            self._state.properties = properties
        self.sent = []

    async def _mqtt_command(self, port, props):
        self.sent.append((port, props))
        return True


async def _press_all(stub):
    for description in AIR_PURIFIER_BUTTONS:
        assert await description.press_fn(stub)
    return stub.sent


@pytest.mark.asyncio
async def test_buttons_write_the_reported_lifetimes():
    """Like the Air+ app, a reset writes the lifetime the purifier reports.

    These are the AC0720 values from an owner's diagnostics (discussion #55).
    """
    stub = _Stub(properties={"D05207": 720, "D05408": 14400, "D0540E": 14016})

    assert await _press_all(stub) == [
        ("filtWr", {"D0520D": 720}),
        ("filtWr", {"D0540E": 14400}),
    ]


@pytest.mark.parametrize(
    "properties",
    [None, {}, {"D05207": 0, "D05408": 0}, {"D05207": "x", "D05408": True}],
)
@pytest.mark.asyncio
async def test_buttons_fall_back_until_the_lifetime_is_known(properties):
    """The clean timer gets the Air+ app's 360 h placeholder.

    The app has no placeholder for the replace timer (it would write 0), so
    that one keeps the HomeID app's 4800 h.
    """
    stub = _Stub(properties=properties)

    assert await _press_all(stub) == [
        ("filtWr", {"D0520D": 360}),
        ("filtWr", {"D0540E": 4800}),
    ]


@pytest.mark.asyncio
async def test_no_reset_without_fusion():
    """Only FUSION purifiers have a filtWr port."""
    stub = _Stub(fusion=False, properties={"D05207": 720})

    assert not await stub.async_reset_filter("D0520D", "D05207", 720)
    assert stub.sent == []


class _FakePaho:
    def __init__(self):
        self.payloads = []

    def publish(self, topic, payload, qos):
        self.payloads.append(json.loads(payload))


def test_filtwr_goes_out_unchanged():
    """No port or property rename applies to the filter write port."""
    client = PhilipsMQTTClient(
        FusionDeviceInfo(
            thing_name="da-test",
            device_id="test",
            tenant="da",
            mqtt_host="host",
            platform_rest_url="url",
            model_name="AC1715/70",
        )
    )
    paho = _FakePaho()
    client._client = paho
    client._connected = True
    client._discovered_ports = ["Status", "filtRd", "Config"]
    client._discovered_write_ports = ["Control", "filtWr"]

    client.send_port_command("filtWr", "setPort", {"D0540E": 4800})

    assert paho.payloads[0]["data"] == {
        "portName": "filtWr",
        "properties": {"D0540E": 4800},
    }

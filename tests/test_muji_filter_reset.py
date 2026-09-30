"""Tests for the MUJI air purifier filter timer resets."""

import json

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

    def __init__(self, fusion=True):
        self._is_fusion = fusion
        self.sent = []

    async def _mqtt_command(self, port, props):
        self.sent.append((port, props))
        return True


@pytest.mark.asyncio
async def test_buttons_write_what_the_app_writes():
    """The app sends the full lifetime to filtWr, one timer per write."""
    stub = _Stub()

    for description in AIR_PURIFIER_BUTTONS:
        assert await description.press_fn(stub)

    assert stub.sent == [
        ("filtWr", {"D0520D": 720}),
        ("filtWr", {"D0540E": 4800}),
    ]


@pytest.mark.asyncio
async def test_no_reset_without_fusion():
    """Only FUSION purifiers have a filtWr port."""
    stub = _Stub(fusion=False)

    assert not await stub.async_reset_filter("D0520D", 720)
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

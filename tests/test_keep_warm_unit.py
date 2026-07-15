"""Tests that keep warm preserves the appliance's temperature unit.

Omitting temp_unit next to a temp makes the device reset to Fahrenheit
(issue #27). Keep warm was the only command left still doing it, so a
Celsius appliance flipped to Fahrenheit and read a 65C setpoint as 65F,
which is about 18C: keep warm with no heat.
"""

import pytest

from custom_components.philips_homeid.local_api import PhilipsLocalAPI
from custom_components.philips_homeid.local_models import (
    PORT_AIRFRYER,
    PORT_VENUSAF,
    LocalDeviceInfo,
)


def _api():
    api = PhilipsLocalAPI()
    sent = []

    async def _request(device, port, method="GET", data=None, **kwargs):
        sent.append(data)
        return {"ok": True}

    api._request = _request
    api._sent = sent
    return api


def _device(port):
    device = LocalDeviceInfo(ip_address="192.0.2.10", cpp_id="aabb")
    device.airfryer_port = port
    return device


@pytest.mark.asyncio
async def test_spectre_keep_warm_echoes_celsius():
    api = _api()
    await api.airfryer_keep_warm(_device(PORT_AIRFRYER), temp=65, raw_temp_unit=False)
    assert api._sent[0]["temp"] == 65
    assert api._sent[0]["temp_unit"] is False


@pytest.mark.asyncio
async def test_spectre_keep_warm_echoes_fahrenheit():
    api = _api()
    await api.airfryer_keep_warm(_device(PORT_AIRFRYER), temp=149, raw_temp_unit=True)
    assert api._sent[0]["temp"] == 149
    assert api._sent[0]["temp_unit"] is True


@pytest.mark.asyncio
async def test_spectre_keep_warm_omits_an_unknown_unit():
    """Guessing could flip the unit the other way, so say nothing."""
    api = _api()
    await api.airfryer_keep_warm(_device(PORT_AIRFRYER), temp=65, raw_temp_unit=None)
    assert "temp_unit" not in api._sent[0]


@pytest.mark.asyncio
async def test_venus_keep_warm_carries_no_unit():
    """Venus keep warm sends no temperature, and its control port has no
    writable temp_unit field.
    """
    api = _api()
    await api.airfryer_keep_warm(_device(PORT_VENUSAF), temp=65, raw_temp_unit=False)
    assert "temp_unit" not in api._sent[0]
    assert "temp" not in api._sent[0]
    assert api._sent[0]["method"] == 2


def _fusion_coordinator(temp_unit):
    """A FUSION coordinator with just enough state to read the temp unit."""
    from unittest.mock import MagicMock

    from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
    from custom_components.philips_homeid.local_models import LocalDeviceState

    coordinator = PhilipsHomeIDCoordinator.__new__(PhilipsHomeIDCoordinator)
    coordinator._is_fusion = True
    coordinator._keep_warm_temp = None
    coordinator._keep_warm_time = 3600
    coordinator._state = LocalDeviceState(
        device_info=LocalDeviceInfo(ip_address="", cpp_id="aabb"),
        properties={"airfryer": {"temp_unit": temp_unit, "status": "idle"}},
    )
    sent = []

    async def _mqtt_command(port, props):
        sent.append(props)
        return True

    async def _noop(*a, **kw):
        return None

    coordinator._mqtt_command = _mqtt_command
    coordinator._ensure_fusion_control_port = _noop
    coordinator._wait_for_status = _noop
    coordinator.async_request_refresh = _noop
    coordinator.api = MagicMock()
    # _fusion_setting_status derives from the transport, so supply one rather
    # than override the property.
    client = MagicMock()
    client.is_venus = False
    coordinator.mqtt_client = client
    coordinator._sent = sent
    return coordinator


@pytest.mark.asyncio
async def test_fusion_keep_warm_on_a_celsius_appliance():
    """The case that bit: 65 with no unit, device flips to F, reads 65F."""
    coordinator = _fusion_coordinator(False)
    await coordinator.async_airfryer_keep_warm()
    setting = coordinator._sent[0]
    assert setting["temp"] == 65
    assert setting["temp_unit"] is False


@pytest.mark.asyncio
async def test_fusion_keep_warm_on_a_fahrenheit_appliance():
    coordinator = _fusion_coordinator(True)
    await coordinator.async_airfryer_keep_warm()
    setting = coordinator._sent[0]
    assert setting["temp"] == 149  # 65C expressed in the unit the device reads
    assert setting["temp_unit"] is True


@pytest.mark.asyncio
async def test_fusion_keep_warm_omits_an_unknown_unit():
    coordinator = _fusion_coordinator(None)
    await coordinator.async_airfryer_keep_warm()
    assert "temp_unit" not in coordinator._sent[0]
    assert coordinator._sent[0]["temp"] == 65

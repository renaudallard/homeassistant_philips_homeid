"""Tests for the model stored on a FUSION entry made from a cloud login.

The model decides the device type, which decides every entity. The HomeID
appliance name is whatever the user called it and can be blank, so on an
HD9876 the entry was stored with the MAC as its model and got no airfryer
entities (issue #46). The IoT registry record carries the CTN.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.philips_homeid.config_flow import PhilipsHomeIDConfigFlow
from custom_components.philips_homeid.const import CONF_MODEL, CONF_THING_NAME
from custom_components.philips_homeid.sensor_descriptions import get_device_type

MAC = "e4:bc:96:00:00:01"
APPLIANCE = {"name": "", "macAddress": MAC, "externalDeviceId": "ext-1"}


def _flow(iot_device):
    flow = PhilipsHomeIDConfigFlow()
    api = MagicMock()
    api.find_device = AsyncMock(return_value=iot_device)
    api.close = AsyncMock()
    flow._cloud_api = api
    flow._cloud_tokens = {"access_token": "at", "refresh_token": "rt"}
    flow._set_unique_id_or_abort = AsyncMock()
    flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)
    return flow


@pytest.mark.asyncio
async def test_registry_ctn_is_the_model_when_the_name_is_blank():
    flow = _flow({"thingName": "da-1", "ctn": "HD9876"})

    result = await flow._create_fusion_entry(dict(APPLIANCE), {})

    assert result["data"][CONF_MODEL] == "HD9876"
    assert result["data"][CONF_THING_NAME] == "da-1"
    assert get_device_type(result["data"][CONF_MODEL]) == "airfryer"
    assert result["title"] == f"{MAC} (Cloud)"


@pytest.mark.asyncio
async def test_name_is_the_model_when_the_registry_has_no_ctn():
    flow = _flow({"thingName": "da-1"})
    appliance = {**APPLIANCE, "name": "Airfryer 5000 Series XXL"}

    result = await flow._create_fusion_entry(appliance, {})

    assert result["data"][CONF_MODEL] == "Airfryer 5000 Series XXL"


@pytest.mark.asyncio
async def test_discovered_model_wins_over_the_registry_ctn():
    flow = _flow({"thingName": "da-1", "ctn": "HD9876"})
    flow._discovered_device = SimpleNamespace(
        model_name="HD9285",
        model_number="HD9285/93",
        cpp_id=MAC,
        ip_address="192.0.2.10",
    )

    result = await flow._create_fusion_entry(dict(APPLIANCE), {})

    assert result["data"][CONF_MODEL] == "HD9285 HD9285/93"


@pytest.mark.asyncio
async def test_no_registry_record_still_fails():
    flow = _flow(None)
    errors: dict[str, str] = {}

    result = await flow._create_fusion_entry(dict(APPLIANCE), errors)

    assert result is None
    assert errors == {"base": "cloud_credentials_not_found"}

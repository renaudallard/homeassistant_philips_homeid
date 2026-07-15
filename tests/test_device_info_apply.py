"""Tests for copying /device metadata onto the device object."""

from custom_components.philips_homeid.local_api import PhilipsLocalAPI
from custom_components.philips_homeid.local_models import LocalDeviceInfo

_apply = PhilipsLocalAPI._apply_device_info


def _device():
    return LocalDeviceInfo(ip_address="192.0.2.10", cpp_id="")


def test_spectre_style_keys():
    d = _device()
    _apply(
        d,
        {"DeviceId": "abc", "modelid": "HD9280", "type": "HD9280/90", "name": "Fryer"},
    )
    assert (d.cpp_id, d.model_name, d.model_number, d.friendly_name) == (
        "abc",
        "HD9280",
        "HD9280/90",
        "Fryer",
    )


def test_alternate_key_spellings():
    d = _device()
    _apply(
        d,
        {
            "cppId": "xyz",
            "ModelName": "AC0650",
            "ModelNumber": "AC0650/11",
            "FriendlyName": "Air",
        },
    )
    assert (d.cpp_id, d.model_name, d.model_number, d.friendly_name) == (
        "xyz",
        "AC0650",
        "AC0650/11",
        "Air",
    )


def test_absent_fields_do_not_blank_what_discovery_found():
    d = _device()
    d.cpp_id = "from-discovery"
    d.model_name = "HD9280"
    _apply(d, {"name": "Kitchen"})
    assert d.cpp_id == "from-discovery"
    assert d.model_name == "HD9280"
    assert d.friendly_name == "Kitchen"


def test_empty_values_are_ignored():
    d = _device()
    d.model_name = "HD9280"
    _apply(d, {"modelid": "", "ModelName": ""})
    assert d.model_name == "HD9280"

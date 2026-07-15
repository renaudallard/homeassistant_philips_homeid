"""Tests for reporting airfryer temperatures in the appliance's own unit."""

from types import SimpleNamespace

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator


class _Stub:
    """Minimal stand-in exercising the real unit logic against a state dict."""

    _current_raw_temp_unit = PhilipsHomeIDCoordinator._current_raw_temp_unit
    airfryer_temperature_unit = PhilipsHomeIDCoordinator.airfryer_temperature_unit

    def __init__(self, properties=None):
        self._state = (
            SimpleNamespace(properties=properties) if properties is not None else None
        )


def test_true_is_fahrenheit():
    assert _Stub({"airfryer": {"temp_unit": True}}).airfryer_temperature_unit() == "°F"


def test_false_is_celsius():
    assert _Stub({"airfryer": {"temp_unit": False}}).airfryer_temperature_unit() == "°C"


def test_unknown_unit_falls_back_to_celsius():
    # No temp_unit field, no airfryer port, and no state at all.
    assert _Stub({"airfryer": {}}).airfryer_temperature_unit() == "°C"
    assert _Stub({}).airfryer_temperature_unit() == "°C"
    assert _Stub().airfryer_temperature_unit() == "°C"


def test_non_dict_airfryer_port_is_tolerated():
    assert _Stub({"airfryer": "unexpected"}).airfryer_temperature_unit() == "°C"

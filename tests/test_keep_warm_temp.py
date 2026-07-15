"""Tests for the keep warm temperature default and its unit."""

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.local_models import (
    LocalDeviceInfo,
    LocalDeviceState,
)

# The bounds the number entity declares, written in Celsius and converted the
# same way the entity converts them.
MIN_C, MAX_C = 40, 100
MIN_F, MAX_F = 104, 212


def _coordinator(temp_unit):
    """Build a coordinator with just enough state to read the temp unit."""
    coordinator = PhilipsHomeIDCoordinator.__new__(PhilipsHomeIDCoordinator)
    coordinator._keep_warm_temp = None
    coordinator._state = LocalDeviceState(
        device_info=LocalDeviceInfo(ip_address="192.0.2.10", cpp_id="aabb"),
        properties={"airfryer": {"temp_unit": temp_unit}},
    )
    return coordinator


def test_celsius_appliance_keeps_the_celsius_default():
    assert _coordinator(False).keep_warm_temp == 65


def test_fahrenheit_appliance_reports_the_same_temperature_in_fahrenheit():
    """65C is 149F, not 65F.

    Naming 65 on a Fahrenheit appliance sat below the entity's own 104F
    minimum, and pressing Keep Warm without touching the slider sent 65F,
    which is about 18C: keep warm with no heat.
    """
    assert _coordinator(True).keep_warm_temp == 149


def test_default_sits_inside_the_declared_bounds():
    """Whatever the unit, the default must be a value the entity allows."""
    assert MIN_C <= _coordinator(False).keep_warm_temp <= MAX_C
    assert MIN_F <= _coordinator(True).keep_warm_temp <= MAX_F


def test_unknown_unit_falls_back_to_celsius():
    """Matches airfryer_temperature_unit's own fallback."""
    coordinator = _coordinator(None)
    assert coordinator.keep_warm_temp == 65


def test_a_user_set_value_is_returned_verbatim():
    """The number entity writes in the appliance's unit, so echo it back."""
    coordinator = _coordinator(True)
    coordinator.set_keep_warm_temp(180)
    assert coordinator.keep_warm_temp == 180

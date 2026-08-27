"""Tests for the FUSION shadow OTA state sensor.

The shadow reports the OTA download state as an integer, and 0 is a real
state (NO_DOWNLOAD) rather than "no data". A falsy-zero bug here would hide
the one value the sensor spends most of its life showing.
"""

from homeassistant.const import EntityCategory

from custom_components.philips_homeid.sensor_descriptions import SENSORS, _ota_state


def _ota_description():
    """Return the ota_state sensor description."""
    return next(d for d in SENSORS if d.key == "ota_state")


def test_ota_zero_is_no_download():
    """Value 0 is a state, not a missing reading."""
    assert _ota_state(0) == "no_download"


def test_ota_states_match_the_apk_ordinals():
    """Each ordinal decodes to its APK OsState name."""
    assert _ota_state(1) == "ncp_download_in_progress"
    assert _ota_state(2) == "host_download_in_progress"
    assert _ota_state(3) == "host_download_paused"
    assert _ota_state(4) == "ncp_validating"
    assert _ota_state(5) == "host_validating"


def test_unknown_ota_value_is_labelled():
    """A value the APK enum does not define is shown, not swallowed."""
    assert _ota_state(7) == "unknown (7)"


def test_missing_ota_is_none():
    """A device that reports no OTA state yields no reading."""
    assert _ota_state(None) is None


def test_description_is_a_shadow_top_level_diagnostic():
    """The description reads the top-level shadow key as a diagnostic."""
    desc = _ota_description()
    assert desc.translation_key == "ota_state"
    assert desc.property_key == "ota"
    assert desc.nested_key is None
    assert desc.entity_category is EntityCategory.DIAGNOSTIC
    assert desc.device_class is None
    assert desc.state_class is None


def test_description_is_not_restricted_by_device_type():
    """Presence of the shadow key is the gate, not the model family.

    Naming device types here would drop the Air+ purifiers, which are FUSION
    devices and receive a shadow like any other.
    """
    assert _ota_description().device_types is None

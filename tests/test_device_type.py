"""Tests for device type detection."""

from custom_components.philips_homeid.sensor import get_device_type


def test_air_purifier_models():
    """AC* models should be detected as air_purifier."""
    assert get_device_type("AC0650") == "air_purifier"
    assert get_device_type("AC0651") == "air_purifier"
    assert get_device_type("AC1234") == "air_purifier"


def test_spectre_airfryer_models():
    """HD9* SPECTRE models should be detected as airfryer."""
    assert get_device_type("HD9200") == "airfryer"
    assert get_device_type("HD9255") == "airfryer"
    assert get_device_type("HD9280") == "airfryer"
    assert get_device_type("HD9285") == "airfryer"


def test_venus_airfryer_models():
    """HD9* Venus models should be detected as airfryer."""
    assert get_device_type("HD9875") == "airfryer"
    assert get_device_type("HD9876") == "airfryer"
    assert get_device_type("HD9880") == "airfryer"


def test_case_insensitive():
    """Detection should be case insensitive."""
    assert get_device_type("hd9280") == "airfryer"
    assert get_device_type("ac0650") == "air_purifier"
    assert get_device_type("HD9280") == "airfryer"


def test_multicooker_models():
    """NX* models should be detected as multicooker."""
    assert get_device_type("NX0960") == "multicooker"
    assert get_device_type("NX0950") == "multicooker"


def test_unknown_models():
    """Unknown models should return unknown."""
    assert get_device_type("EP2520") == "unknown"
    assert get_device_type("") == "unknown"
    assert get_device_type("random") == "unknown"


def test_none_model():
    """None model should return unknown."""
    assert get_device_type(None) == "unknown"


def test_airfryer_keyword():
    """Models containing 'airfryer' should be detected."""
    assert get_device_type("Airfryer XXL") == "airfryer"

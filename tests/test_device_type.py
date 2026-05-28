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


def test_venus_codename_models():
    """Venus codename models should be detected as airfryer."""
    assert get_device_type("Venus2") == "airfryer"
    assert get_device_type("Venus1") == "airfryer"
    assert get_device_type("venus") == "airfryer"


def test_spectre_codename_models():
    """Spectre codename models should be detected as airfryer."""
    assert get_device_type("Spectre") == "airfryer"
    assert get_device_type("SPECTRE") == "airfryer"


def test_case_insensitive():
    """Detection should be case insensitive."""
    assert get_device_type("hd9280") == "airfryer"
    assert get_device_type("ac0650") == "air_purifier"
    assert get_device_type("HD9280") == "airfryer"


def test_multicooker_models():
    """NX* models should be detected as multicooker."""
    assert get_device_type("NX0960") == "multicooker"
    assert get_device_type("NX0950") == "multicooker"


def test_multicooker_codename_models():
    """Multicooker codename models should be detected as multicooker."""
    assert get_device_type("Nutrimax") == "multicooker"
    assert get_device_type("Hermes") == "multicooker"
    assert get_device_type("HERMES") == "multicooker"


def test_espresso_models():
    """EP/SM models and espresso codenames should be detected as espresso."""
    assert get_device_type("EP2520") == "espresso"
    assert get_device_type("EP3546") == "espresso"
    assert get_device_type("EP8757") == "espresso"
    assert get_device_type("SM5400") == "espresso"
    assert get_device_type("Flash_Entry_P EP2520") == "espresso"
    assert get_device_type("flash_entry_p ep2520") == "espresso"
    assert get_device_type("espresso machine") == "espresso"
    assert get_device_type("Coffee Maker") == "espresso"


def test_unknown_models():
    """Unknown models should return unknown."""
    assert get_device_type("") == "unknown"
    assert get_device_type("random") == "unknown"
    assert get_device_type("XY1234") == "unknown"


def test_none_model():
    """None model should return unknown."""
    assert get_device_type(None) == "unknown"


def test_airfryer_keyword():
    """Models containing 'airfryer' should be detected."""
    assert get_device_type("Airfryer XXL") == "airfryer"

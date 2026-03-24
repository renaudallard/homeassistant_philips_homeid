"""Tests for Venus/SPECTRE normalization mappings."""

from custom_components.philips_homeid.local_api import PhilipsLocalAPI


def test_venus_response_normalization():
    """Venus response keys should be mapped to SPECTRE equivalents."""
    data = {
        "disp_time": 300,
        "total_time": 600,
        "method": 1,
        "current_temp": 180,
        "status": "cooking",
        "temp": 200,
    }
    result = PhilipsLocalAPI._normalize_venus_response(data)

    # SPECTRE aliases added
    assert result["cur_time"] == 300
    assert result["time"] == 600
    assert result["preset"] == 1
    assert result["cur_temp"] == 180

    # Original Venus keys preserved
    assert result["disp_time"] == 300
    assert result["total_time"] == 600
    assert result["method"] == 1
    assert result["current_temp"] == 180

    # Unchanged keys pass through
    assert result["status"] == "cooking"
    assert result["temp"] == 200


def test_venus_response_no_overwrite():
    """Normalization should not overwrite existing SPECTRE keys."""
    data = {
        "disp_time": 300,
        "cur_time": 999,  # Already has SPECTRE key
    }
    result = PhilipsLocalAPI._normalize_venus_response(data)
    assert result["cur_time"] == 999  # Not overwritten


def test_venus_command_normalization():
    """SPECTRE command keys should be mapped to Venus equivalents."""
    data = {
        "time": 600,
        "preset": 1,
        "cur_temp": 180,
        "temp": 200,
        "status": "setting",
    }
    result = PhilipsLocalAPI._normalize_venus_command(data)

    # SPECTRE keys replaced with Venus keys
    assert "time" not in result
    assert result["total_time"] == 600
    assert "preset" not in result
    assert result["method"] == 1
    assert "cur_temp" not in result
    assert result["current_temp"] == 180

    # Unchanged keys pass through
    assert result["temp"] == 200
    assert result["status"] == "setting"


def test_venus_key_map_consistency():
    """Forward and reverse maps should be consistent."""
    for venus_key, spectre_key in PhilipsLocalAPI._VENUS_KEY_MAP.items():
        assert PhilipsLocalAPI._SPECTRE_KEY_MAP[spectre_key] == venus_key

"""Tests for the seconds-to-time conversions used by the sensors."""

from custom_components.philips_homeid.sensor_descriptions import (
    _seconds_to_hours,
    _seconds_to_minutes,
)


def test_hours_from_seconds():
    assert _seconds_to_hours(7200) == 2
    assert _seconds_to_hours(3599) == 0


def test_hours_from_a_numeric_string():
    """Every other conversion in the file copes with this; runtime raised."""
    assert _seconds_to_hours("7200") == 2


def test_hours_from_an_unreadable_value():
    assert _seconds_to_hours("later") is None
    assert _seconds_to_hours(None) is None
    assert _seconds_to_hours([]) is None


def test_minutes_are_unchanged():
    assert _seconds_to_minutes(120) == 2
    assert _seconds_to_minutes("120") == 2
    assert _seconds_to_minutes("soon") is None
    assert _seconds_to_minutes(None) is None

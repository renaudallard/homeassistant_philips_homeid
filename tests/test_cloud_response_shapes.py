"""Tests for parsing cloud responses whose shape is not what we expect.

The callers treat an exception from these as a permanent miss: a recipe id
that raises is added to a failed set and never asked for again for the rest
of the session. A 200 carrying an odd shape has to parse as "nothing found".
"""

import pytest

from custom_components.philips_homeid.cloud_api import PhilipsCloudAPI

_food_item = PhilipsCloudAPI._extract_autocook_food_item
_my_presets = PhilipsCloudAPI._parse_my_presets


@pytest.mark.parametrize("data", [None, [], "text", 42, {"_embedded": None}])
def test_food_item_survives_an_unexpected_shape(data):
    assert _food_item(data) is None


def test_food_item_survives_a_non_dict_member():
    assert _food_item({"_embedded": {"item": ["not-an-object"]}}) is None


def test_food_item_still_reads_a_normal_response():
    data = {"_embedded": {"item": [{"foodItem": "chicken"}]}}
    assert _food_item(data) == "chicken"


def test_food_item_still_reads_a_single_object():
    assert (
        _food_item({"_embedded": {"autocookProgram": {"foodItem": "fish"}}}) == "fish"
    )


@pytest.mark.parametrize("data", [None, [], "text", 42, {"_embedded": None}])
def test_my_presets_survives_an_unexpected_shape(data):
    assert _my_presets(data) == []


def test_my_presets_skips_a_preset_with_unreadable_numbers():
    data = {
        "_embedded": {
            "item": [
                {
                    "shortId": "a1",
                    "name": "Bad",
                    "temperature": {"default": "hot", "unit": "C"},
                },
                {
                    "shortId": "b2",
                    "name": "Good",
                    "temperature": {"default": 180, "unit": "C"},
                    "time": {"default": 600},
                },
            ]
        }
    }
    presets = _my_presets(data)
    assert [p["name"] for p in presets] == ["Good"]


def test_my_presets_tolerates_a_non_dict_temperature():
    data = {
        "_embedded": {"item": [{"shortId": "a1", "name": "X", "temperature": "hot"}]}
    }
    assert _my_presets(data) == [
        {"name": "X", "short_id": "a1", "temp": None, "time": None, "fahrenheit": False}
    ]


def test_my_presets_still_reads_a_normal_response():
    data = {
        "_embedded": {
            "item": [
                {
                    "shortId": "a1",
                    "name": "Wings",
                    "temperature": {"default": 400, "unit": "FAHRENHEIT"},
                    "time": {"default": 900},
                }
            ]
        }
    }
    assert _my_presets(data) == [
        {
            "name": "Wings",
            "short_id": "a1",
            "temp": 400,
            "time": 900,
            "fahrenheit": True,
        }
    ]

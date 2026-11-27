"""Tests for parsing cloud responses whose shape is not what we expect.

The callers treat an exception from these as a permanent miss: a recipe id
that raises is added to a failed set and never asked for again for the rest
of the session. A 200 carrying an odd shape has to parse as "nothing found".
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.philips_homeid.cloud_api import (
    CloudConnectionError,
    PhilipsCloudAPI,
)

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


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["[]", "null", '"text"', "42", "{"])
async def test_discovery_survives_an_unexpected_shape(body):
    resp = MagicMock(status=200)
    resp.text = AsyncMock(return_value=body)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=resp)
    context.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = context
    api = PhilipsCloudAPI()
    api._get_session = AsyncMock(return_value=session)
    assert await api.homeid_discovery() == {}


def json_session(*replies):
    """A session answering each GET with the next JSON reply, status 200."""
    session = MagicMock()
    contexts = []
    for reply in replies:
        resp = MagicMock(status=200)
        resp.json = AsyncMock(return_value=reply)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=resp)
        context.__aexit__ = AsyncMock(return_value=False)
        contexts.append(context)
    session.get.side_effect = contexts
    return session


SPACE = {"spaces": [{"backendBaseUrl": "https://a"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replies",
    [
        ([],),
        (None,),
        ("text",),
        ({"spaces": "x"},),
        ({"spaces": [1]},),
        ({"spaces": [None]},),
        ({"spaces": [{"backendBaseUrl": 5}]},),
        (SPACE, ["root"]),
        (SPACE, None),
        (SPACE, {"_links": "x"}),
    ],
)
async def test_root_links_of_an_unexpected_shape_are_retried(replies):
    # Like a reply that is not JSON, not an answer that there are no links:
    # the callers would keep that one.
    api = PhilipsCloudAPI()
    api._get_session = AsyncMock(return_value=json_session(*replies))
    with pytest.raises(CloudConnectionError, match="not readable"):
        await api._get_root_links("token")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replies",
    [
        ({},),
        ({"spaces": None},),
        ({"spaces": [{}, {"backendBaseUrl": ""}]},),
        (SPACE, {}),
        (SPACE, {"_links": []}),
    ],
)
async def test_root_links_of_a_tenant_without_links_are_empty(replies):
    api = PhilipsCloudAPI()
    api._get_session = AsyncMock(return_value=json_session(*replies))
    assert await api._get_root_links("token") == {}


@pytest.mark.asyncio
async def test_root_links_still_read_a_normal_reply():
    links = {"self": {"href": "https://a/"}}
    replies = (
        {"spaces": ["x", {"backendBaseUrl": "https://a"}]},
        {"_links": links},
    )
    api = PhilipsCloudAPI()
    api._get_session = AsyncMock(return_value=json_session(*replies))
    assert await api._get_root_links("token") == links

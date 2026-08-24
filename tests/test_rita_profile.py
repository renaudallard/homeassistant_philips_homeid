"""Tests for Rita espresso machine profile/recipe protobuf decoders."""

import base64

from custom_components.philips_homeid.rita_protobuf import (
    decode_profile_id,
    decode_profile_recipe_ids,
    decode_recipe_id,
)


def _blob(*data: int) -> str:
    """Base64-encode raw protobuf bytes the way the FUSION port carries them."""
    return base64.b64encode(bytes(data)).decode()


# A profile blob laid out like the APK's RitaProfileData.ProfileData:
# tag 1 = profileId, tag 2 = profileIdOrder, tag 3 = colorId,
# tag 4 = recipeIdOrderList (packed varints).
_PROFILE_SLOT2 = _blob(
    0x08,
    0x05,  # profileId = 5
    0x10,
    0x02,  # profileIdOrder = 2 (the display slot, NOT the id)
    0x18,
    0x01,  # colorId = 1
    0x22,
    0x02,
    0x0A,
    0x14,  # recipeIdOrderList = [10, 20]
)


def test_profile_id_is_tag1_not_slot():
    """The wire profileId comes from tag 1, independent of the display order."""
    assert decode_profile_id(_PROFILE_SLOT2) == 5


def test_profile_id_single_byte_varint():
    """A small profileId decodes from a single-byte varint."""
    assert decode_profile_id(_blob(0x08, 0x2A)) == 42


def test_profile_id_multi_byte_varint():
    """A profileId above 127 spans multiple varint bytes."""
    assert decode_profile_id(_blob(0x08, 0xAC, 0x02)) == 300


def test_profile_id_zero_is_empty_sentinel():
    """profileId 0 is the empty/invalid sentinel and maps to None."""
    assert decode_profile_id(_blob(0x08, 0x00)) is None


def test_profile_id_missing_tag_returns_none():
    """A blob without tag 1 (only a recipe list) has no profileId."""
    assert decode_profile_id(_blob(0x22, 0x03, 0x01, 0x02, 0x03)) is None


def test_profile_id_empty_blob_returns_none():
    """An empty slot reports an empty string, which has no profileId."""
    assert decode_profile_id("") is None


def test_profile_id_bad_base64_returns_none():
    """Undecodable base64 is swallowed, not raised."""
    assert decode_profile_id("abc") is None


def test_profile_id_truncated_varint_returns_none():
    """A truncated varint is swallowed, not raised."""
    assert decode_profile_id(_blob(0x08, 0x80)) is None


def test_profile_recipe_ids_from_tag4():
    """The recipe id order list is read from tag 4, in the machine's order."""
    assert decode_profile_recipe_ids(_PROFILE_SLOT2) == [10, 20]


def test_recipe_id_from_tag1():
    """A saved RitaBrewCommand recipe blob exposes its recipeId at tag 1."""
    assert decode_recipe_id(_blob(0x08, 0x07)) == 7


def test_recipe_id_bad_base64_returns_none():
    """Undecodable recipe blobs decode to None rather than raising."""
    assert decode_recipe_id("abc") is None

"""Tests for rejecting truncated Rita protobuf blobs."""

import base64

from custom_components.philips_homeid.rita_protobuf import (
    decode_profile_recipe_ids,
    iter_fields,
)


def _b64(raw):
    return base64.b64encode(raw).decode()


def test_truncated_length_delimited_field_is_rejected():
    """Slicing clamps, so a short blob used to look like a whole one.

    The recipe dropdown would then silently show a partial slot list rather
    than falling back to empty.
    """
    # tag 4, wire type 2, declared length 10, only 2 bytes present
    raw = bytes([4 << 3 | 2, 10, 0x05, 0x07])
    try:
        list(iter_fields(raw))
    except ValueError as err:
        assert "truncated" in str(err)
    else:
        raise AssertionError("expected a ValueError")


def test_a_truncated_profile_decodes_as_empty():
    raw = bytes([4 << 3 | 2, 10, 0x05, 0x07])
    assert decode_profile_recipe_ids(_b64(raw)) == set()


def test_truncated_fixed_width_fields_are_rejected():
    for wire_type, present in ((1, 3), (5, 2)):
        raw = bytes([1 << 3 | wire_type]) + bytes(present)
        try:
            list(iter_fields(raw))
        except ValueError as err:
            assert "truncated" in str(err)
        else:
            raise AssertionError(f"expected a ValueError for wire type {wire_type}")


def test_a_whole_profile_still_decodes():
    body = bytes([0x05, 0x07])
    raw = bytes([4 << 3 | 2, len(body)]) + body
    assert decode_profile_recipe_ids(_b64(raw)) == {5, 7}


def test_whole_fixed_width_fields_are_skipped():
    raw = bytes([1 << 3 | 5]) + bytes(4) + bytes([2 << 3 | 0, 9])
    assert list(iter_fields(raw)) == [(2, 0, 9)]

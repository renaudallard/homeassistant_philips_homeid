# Copyright (c) 2025, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
"""Protobuf decoders for Philips Rita espresso machine port blobs.

The Rita FUSION ports carry a handful of base64-encoded protobuf messages
(profile and recipe data). These helpers pull out the few fields the
integration needs without pulling in a protobuf runtime. They are shared by
the coordinator (which builds brew commands) and the select platform (which
filters dropdowns), and live in their own leaf module so the coordinator can
reuse them without a circular import back into ``select``.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator


def decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode one protobuf varint; return (value, new_offset)."""
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift >= 64:
            raise ValueError("varint overflow")
    raise ValueError("truncated varint")


def iter_fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield (field_number, wire_type, value) for top-level protobuf fields."""
    offset = 0
    while offset < len(data):
        tag, offset = decode_varint(data, offset)
        field_number = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:  # varint
            value, offset = decode_varint(data, offset)
            yield field_number, wire_type, value
        elif wire_type == 2:  # length-delimited
            length, offset = decode_varint(data, offset)
            # Slicing clamps, so a field claiming more bytes than are left
            # would hand back a short value and end the loop as though the
            # message were whole. The callers turn that into a recipe list
            # missing entries rather than an empty one they can fall back on.
            if offset + length > len(data):
                raise ValueError("truncated length-delimited field")
            raw = data[offset : offset + length]
            offset += length
            yield field_number, wire_type, raw
        elif wire_type == 1:  # 64-bit fixed
            if offset + 8 > len(data):
                raise ValueError("truncated 64-bit field")
            offset += 8
        elif wire_type == 5:  # 32-bit fixed
            if offset + 4 > len(data):
                raise ValueError("truncated 32-bit field")
            offset += 4
        else:
            raise ValueError(f"unsupported wire type {wire_type}")


def decode_profile_recipe_ids(blob_b64: str) -> set[int]:
    """Return the recipeIds from a Rita profile's recipeIdOrderList (tag 4)."""
    try:
        data = base64.b64decode(blob_b64)
    except (ValueError, TypeError):
        return set()
    result: set[int] = set()
    try:
        for field_number, wire_type, value in iter_fields(data):
            if field_number != 4 or wire_type != 2 or not isinstance(value, bytes):
                continue
            offset = 0
            while offset < len(value):
                recipe_id, offset = decode_varint(value, offset)
                if recipe_id:
                    result.add(recipe_id)
    except ValueError:
        return set()
    return result


def decode_recipe_id(blob_b64: str) -> int | None:
    """Return the recipeId (tag 1) from a RitaBrewCommand saved recipe blob."""
    try:
        data = base64.b64decode(blob_b64)
    except (ValueError, TypeError):
        return None
    try:
        for field_number, wire_type, value in iter_fields(data):
            if field_number == 1 and wire_type == 0 and isinstance(value, int):
                return value
    except ValueError:
        return None
    return None


def decode_profile_id(blob_b64: str) -> int | None:
    """Return the profileId (tag 1) from a Rita profile blob (RitaProfileData).

    The machine's brew command carries this profileId, not the storage slot
    index of the profile. profileId 0 is the app's empty/invalid sentinel
    (profiles reporting 0 are skipped), so it is reported as None.
    """
    try:
        data = base64.b64decode(blob_b64)
    except (ValueError, TypeError):
        return None
    try:
        for field_number, wire_type, value in iter_fields(data):
            if field_number == 1 and wire_type == 0 and isinstance(value, int):
                return value or None
    except ValueError:
        return None
    return None
